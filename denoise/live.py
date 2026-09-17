# -*- coding: utf-8 -*-
"""
live.py —— 实时降噪虚拟麦克风

数据流：  麦克风阵列 ──WASAPI采集──► 降噪DSP ──► Voicemeeter 虚拟输入 ──► 各应用当麦克风

为什么是 CPU 方案而不是 GPU（NVIDIA Broadcast）：
  CPU 侧处理可以绑到 E-core + 低于普通优先级，对 CS2 这种帧率敏感场景影响≈0；
  GPU 方案会占用独显并把显卡钉在 P0 状态，打游戏时是拿帧率换安静。
  实测本机 i9-14900HX 有 24 核 / 32 线程，本程序单线程 RTF≈0.008（约 125 倍实时）。

用法：
  python live.py --check                 检查设备、CPU 亲和性、优先级（不改任何东西）
  python live.py --list                  列出候选设备（带索引）
  python live.py --ab 10                 录 10 秒：原始 + 降噪 两个 wav，供试听对比
  python live.py --probe 48              向指定输出设备播 2 秒测试音，用来确认哪一路是 Voicemeeter 输入
  python live.py --run                   正式运行（麦克风阵列 → 降噪 → Voicemeeter 虚拟输入）
  python live.py --run --output 51       手动指定输出设备索引

可调参数（覆盖 dsp.py 的默认值）：
  --bias 2.5     噪声估计偏差补偿（越大压得越狠）
  --over 2.5     过减因子
  --gate 6.0     噪声门阈值(dB)
  --gate-floor -16   门全关时的增益(dB)
  --n-fft 4096   4096=默认(64ms延迟) / 2048=低延迟(32ms) / 8192=高质量(128ms)
"""

import argparse
import ctypes
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)

import sounddevice as sd  # noqa: E402

from dsp import Denoiser, DEFAULT_CFG  # noqa: E402

SR = 48000
IN_KEY = "麦克风阵列"
OUT_KEY = "Voicemeeter"
CFG_PATH = os.path.join(HERE, "config.json")

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
THREAD_PRIORITY_BELOW_NORMAL = -1


# ---------------------------------------------------------------- CPU 策略
def apply_cpu_policy(mask=None):
    """绑 E-core + 低于普通优先级：这是"不影响 CS2 帧率"的技术保证。"""
    info = {}
    n = os.cpu_count() or 1
    info["logical_cpus"] = n
    if mask is None:
        # i9-14900HX：0-15 为 8 个 P 核（含超线程），16-31 为 16 个 E 核
        mask = ((1 << (n - 16)) - 1) << 16 if n >= 32 else 0
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 必须声明 restype/argtypes：HANDLE 是 64 位伪句柄，
        # 不声明会被 ctypes 截断成 32 位，导致 err=6 ERROR_INVALID_HANDLE
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k32.SetProcessAffinityMask.restype = ctypes.c_int
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.SetPriorityClass.restype = ctypes.c_int
        h = k32.GetCurrentProcess()
        if mask:
            info["affinity_set"] = bool(k32.SetProcessAffinityMask(h, ctypes.c_size_t(mask)))
            if not info["affinity_set"]:
                info["affinity_errno"] = ctypes.get_last_error()
        else:
            info["affinity_set"] = False
        info["priority_set"] = bool(k32.SetPriorityClass(h, BELOW_NORMAL_PRIORITY_CLASS))
        if not info["priority_set"]:
            info["priority_errno"] = ctypes.get_last_error()
        info["mask"] = hex(mask)
    except Exception as e:                                    # pragma: no cover
        info["error"] = str(e)
    return info


def lower_callback_thread():
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        pass


# ---------------------------------------------------------------- 设备
def dev_info(i):
    """统一设备表示：sounddevice 的字段 + hostapi_name。"""
    d = dict(sd.query_devices(i))
    d["hostapi_name"] = sd.query_hostapis(d["hostapi"])["name"]
    d["ch"] = d["max_input_channels"] or d["max_output_channels"]
    return d


def all_devices(kind):
    out = []
    for i, d in enumerate(sd.query_devices()):
        if (d["max_input_channels"] if kind == "in" else d["max_output_channels"]) <= 0:
            continue
        out.append(dev_info(i))
    return out


def pick(kind, key, hostapi="WASAPI", prefer_sr=SR):
    cands = [d for d in all_devices(kind) if key.lower() in d["name"].lower()]
    if not cands:
        return None

    def score(d):
        s = 0
        if hostapi in d["hostapi_name"]:
            s += 100
        if abs(d["default_samplerate"] - prefer_sr) < 1:
            s += 10
        return -s

    return sorted(cands, key=score)[0]


def resolve_devices(args):
    """优先用命令行 / config.json，否则自动匹配。"""
    saved = {}
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = {}
    saved = saved.get("devices", saved)

    idx_in = args.input if args.input is not None else saved.get("input")
    idx_out = args.output if args.output is not None else saved.get("output")
    din = dev_info(idx_in) if idx_in is not None else pick("in", IN_KEY)
    dout = dev_info(idx_out) if idx_out is not None else pick("out", OUT_KEY)
    return din, dout


def save_devices(din, dout):
    try:
        data = {}
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        data.setdefault("devices", {})
        data["devices"]["input"] = din["index"] if din is not None else None
        data["devices"]["output"] = dout["index"] if dout is not None else None
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def make_cfg(args):
    cfg = {}
    if args.n_fft:
        cfg["n_fft"] = args.n_fft
        cfg["hop"] = args.n_fft // 4
    if args.bias is not None:
        cfg["noise_bias"] = args.bias
    if args.over is not None:
        cfg["over_sub"] = args.over
    if args.gate is not None:
        cfg["gate_thresh_db"] = args.gate
    if args.gate_floor is not None:
        cfg["gate_floor_db"] = args.gate_floor
    if args.no_gate:
        cfg["gate_floor_db"] = 0.0
    return cfg


# ---------------------------------------------------------------- 各模式
def cmd_check(args):
    policy = apply_cpu_policy(args.affinity)
    print("=== CPU 策略 ===")
    print("  逻辑处理器 %d   亲和性掩码 %s  设置成功=%s(err=%s)  低于普通优先级=%s(err=%s)"
          % (policy["logical_cpus"], policy.get("mask"), policy.get("affinity_set"),
             policy.get("affinity_errno"), policy.get("priority_set"),
             policy.get("priority_errno")))
    din, dout = resolve_devices(args)
    print("=== 设备 ===")
    for label, d in (("输入(麦克风)", din), ("输出(虚拟麦克风)", dout)):
        if d is None:
            print("  %-16s 未找到！" % label)
        else:
            print("  %-16s [%d] %s  | %s | %dch | %.0fHz"
                  % (label, d["index"], d["name"], d["hostapi_name"], d["ch"],
                     d["default_samplerate"]))
    den = Denoiser(make_cfg(args))
    print("=== DSP ===")
    print("  n_fft=%d  hop=%d  算法延迟 %.0f ms  频点间隔 %.1f Hz"
          % (den.n, den.hop, 1000.0 * den.latency_samples / SR, SR / float(den.n)))
    print("  低频切除 %.0f Hz   陷波 %d 个   噪声偏差 %.1f  过减 %.1f  门限 %.1f dB"
          % (den.cfg["hp_freq"], len(den.cfg["harmonics"]), den.cfg["noise_bias"],
             den.cfg["over_sub"], den.cfg["gate_thresh_db"]))
    return 0


def cmd_list(args):
    for kind in ("in", "out"):
        print("=== %s ===" % ("输入设备" if kind == "in" else "输出设备"))
        for d in all_devices(kind):
            if "WASAPI" not in d["hostapi"]:
                continue
            mark = ""
            if (kind == "in" and IN_KEY.lower() in d["name"].lower()):
                mark = "  <== 目标输入"
            if (kind == "out" and OUT_KEY.lower() in d["name"].lower()):
                mark = "  <== Voicemeeter 虚拟输入（候选）"
            print("  %3d  %-50s %dch %.0fHz%s" % (d["index"], d["name"][:50], d["ch"], d["sr"], mark))
    return 0


def cmd_probe(args):
    """向指定输出设备播测试音，用来确认哪一路才是 Voicemeeter 的输入通道。"""
    idx = args.probe
    d = sd.query_devices(idx)
    print("向 [%d] %s 播放 2 秒 440Hz 测试音…" % (idx, d["name"]))
    t = np.arange(int(SR * 2.0)) / SR
    tone = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sd.play(tone, SR, device=idx, blocking=True)
    print("完成。若 Voicemeeter 对应通道的电平表动了，就是这个设备。")
    return 0


def cmd_ab(args):
    """录原始 + 降噪两段，写 wav 供试听（不需要 Voicemeeter）。"""
    din, _ = resolve_devices(args)
    if din is None:
        print("找不到输入设备")
        return 2
    cfg = make_cfg(args)
    den = Denoiser(cfg)
    apply_cpu_policy(args.affinity)
    seconds = args.ab
    hop = den.hop
    raw_parts, out_parts = [], []
    n_blocks = int(seconds * SR / hop)
    print("开始录制 %.1f 秒（请正常说话）… 输入 [%d] %s" % (seconds, din["index"], din["name"]))

    def cb(indata, frames, tinfo, status):
        if status:
            pass
        mono = indata.mean(axis=1)
        raw_parts.append(mono.astype(np.float64).copy())
        out_parts.append(den.process(mono))

    with sd.InputStream(device=din["index"], channels=min(2, din["max_input_channels"]),
                        samplerate=SR, blocksize=hop, dtype="float32", callback=cb):
        time.sleep(seconds + 0.2)

    raw = np.concatenate(raw_parts)[:n_blocks * hop]
    out = np.concatenate(out_parts)[:n_blocks * hop]
    d = den.latency_samples
    out_aligned = out[d:]
    raw_aligned = raw[:len(out_aligned)]

    import soundfile as sf
    p_raw = os.path.join(HERE, "ab_1_raw.wav")
    p_out = os.path.join(HERE, "ab_2_denoised.wav")
    sf.write(p_raw, raw_aligned.astype(np.float32), SR)
    sf.write(p_out, out_aligned.astype(np.float32), SR)

    rms = lambda x: 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-12))
    print("已写出：")
    print("  %s   RMS %.2f dBFS" % (p_raw, rms(raw_aligned)))
    print("  %s   RMS %.2f dBFS" % (p_out, rms(out_aligned)))
    print("请依次播放对比。两段已做延迟对齐（降噪段前移 %d 样本）。" % d)
    return 0


def cmd_run(args):
    din, dout = resolve_devices(args)
    if din is None or dout is None:
        print("设备不齐：输入=%s 输出=%s" % (din, dout))
        print("先跑 --list 找到索引，再用 --input/--output 指定。")
        return 2
    cfg = make_cfg(args)
    den = Denoiser(cfg)
    policy = apply_cpu_policy(args.affinity)
    print("运行中（Ctrl+C 退出）：")
    print("  输入 [%d] %s" % (din["index"], din["name"]))
    print("  输出 [%d] %s" % (dout["index"], dout["name"]))
    print("  CPU: 掩码 %s  优先级=低于普通  |  算法延迟 %.0f ms"
          % (policy.get("mask"), 1000.0 * den.latency_samples / SR))
    # 从 config.json 读出应用该选的录音设备名（由 tests/vm_setup.py 自动测出）
    cap_desc = None
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            _cd = json.load(f).get("voicemeeter", {}).get("capture_device")
        if _cd is not None:
            cap_desc = "[%d] %s" % (_cd, sd.query_devices(_cd)["name"])
    except Exception:
        pass
    if cap_desc:
        print("  ===> 各应用/CS2 的麦克风请选: %s" % cap_desc)
    else:
        print("  各应用的麦克风请选 Voicemeeter 的录音通道（先跑 tests/vm_setup.py 可自动测出）")
    # 写 PID 文件：manager.py 靠它做幂等的启停与状态判断
    pid_path = os.path.join(HERE, "pipeline.pid")
    try:
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    hop = den.hop
    first = [True]
    stats = dict(t0=time.time(), raw=0.0, out=0.0, n=0)

    def cb(indata, outdata, frames, tinfo, status):
        if first[0]:
            lower_callback_thread()
            first[0] = False
        mono = indata.mean(axis=1)
        y = den.process(mono)
        outdata[:, 0] = y
        if outdata.shape[1] > 1:
            outdata[:, 1] = y
        stats["raw"] += float(np.mean(mono ** 2)) * frames
        stats["out"] += float(np.mean(y ** 2)) * frames
        stats["n"] += frames

    try:
        with sd.Stream(device=(din["index"], dout["index"]), channels=(2, 2),
                       samplerate=SR, blocksize=hop, dtype="float32", callback=cb):
            while True:
                time.sleep(5.0)
                n = max(1, stats["n"])
                r, o = stats["raw"] / n, stats["out"] / n
                print("  [%5.0fs] 输入 %6.1f dBFS   输出 %6.1f dBFS   压制 %+5.1f dB   门 %.2f   帧SNR %5.1f dB"
                      % (time.time() - stats["t0"],
                         10 * np.log10(max(r, 1e-20)), 10 * np.log10(max(o, 1e-20)),
                         10 * np.log10(max(o, 1e-20)) - 10 * np.log10(max(r, 1e-20)),
                         den.g_gate, den.last_frame_snr_db))
                stats.update(raw=0.0, out=0.0, n=0)
    except KeyboardInterrupt:
        print("\n已停止，麦克风回到原始状态（未修改任何系统设置）。")
    finally:
        try:
            os.remove(pid_path)
        except Exception:
            pass
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True, description="实时降噪虚拟麦克风")
    ap.add_argument("--check", action="store_true", help="检查设备/CPU策略")
    ap.add_argument("--list", action="store_true", help="列出候选设备")
    ap.add_argument("--ab", type=float, metavar="SEC", help="录原始+降噪两段 wav 供试听")
    ap.add_argument("--probe", type=int, metavar="IDX", help="向指定输出设备播测试音")
    ap.add_argument("--run", action="store_true", help="正式实时运行")
    ap.add_argument("--input", type=int, default=None, help="输入设备索引")
    ap.add_argument("--output", type=int, default=None, help="输出设备索引")
    ap.add_argument("--n-fft", type=int, default=None, help="2048/4096/8192")
    ap.add_argument("--bias", type=float, default=None, help="噪声偏差补偿")
    ap.add_argument("--over", type=float, default=None, help="过减因子")
    ap.add_argument("--gate", type=float, default=None, help="噪声门阈值 dB")
    ap.add_argument("--gate-floor", type=float, default=None, help="门全关增益 dB")
    ap.add_argument("--no-gate", action="store_true", help="关闭噪声门")
    ap.add_argument("--affinity", type=lambda s: int(s, 0), default=None, help="CPU 亲和性掩码")
    ap.add_argument("--save-devices", action="store_true", help="把当前设备选择写入 config.json")
    args = ap.parse_args()

    if args.save_devices:
        din, dout = resolve_devices(args)
        ok = save_devices(din, dout)
        print("设备选择已%s写入 config.json：输入=%s 输出=%s"
              % ("成功" if ok else "失败，未能", 
                 din["index"] if din else None, dout["index"] if dout else None))
        if not (args.check or args.run or args.ab):
            return 0

    for flag in ("check", "run"):
        if getattr(args, flag):
            return {"check": cmd_check, "run": cmd_run}[flag](args)
    if args.list:
        return cmd_list(args)
    if args.ab:
        return cmd_ab(args)
    if args.probe is not None:
        return cmd_probe(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
