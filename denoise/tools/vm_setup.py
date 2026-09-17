# -*- coding: utf-8 -*-
"""
vm_setup.py —— 自动完成 Voicemeeter 侧配置与路由探测（全自动，无需手动点界面）

流程：
  1. Voicemeeter Remote API 登录（VBVMR_* 导出）
  2. 保存参数快照 → vm_state_before.json（可回滚）
  3. 安全静音：关闭所有 Strip 的 A1~A5（硬件监听），静音硬件输入条
     → 防止笔记本麦克风被直接播到音箱产生啸叫
  4. 依次尝试 B1 / B2 / B3：在虚拟输入条上打开该 Bus，然后
     逐个输出设备播测试音 + 同时录所有虚拟录音通道 → 用音频功能实测出配对关系
  5. 结果写入 config.json（输出设备 + 应用该选的录音设备）
"""
import ctypes
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import sounddevice as sd  # noqa: E402

DLL64 = r"C:\Program Files (x86)\VB\Voicemeeter\VoicemeeterRemote64.dll"
BEFORE = os.path.join(ROOT, "vm_state_before.json")
CFG = os.path.join(ROOT, "config.json")
LOG = os.path.join(ROOT, "vm_setup_log.txt")
SR = 48000
KEY = "Voicemeeter"
BUS_NAMES = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3"]

_lines = []


def p(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _lines.append(s)
    try:
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines))
    except Exception:
        pass


class VM:
    def __init__(self):
        self.dll = ctypes.WinDLL(DLL64)
        self.f = {n: getattr(self.dll, n, None) for n in (
            "VBVMR_Login", "VBVMR_Logout", "VBVMR_GetVoicemeeterType", "VBVMR_GetParameterFloat",
            "VBVMR_SetParameterFloat", "VBVMR_GetParameterStringA", "VBVMR_GetLevel",
            "VBVMR_IsParametersDirty")}
        self.f["VBVMR_Login"].argtypes = [ctypes.c_char_p]
        self.f["VBVMR_Login"].restype = ctypes.c_long
        self.f["VBVMR_Logout"].restype = ctypes.c_long
        self.f["VBVMR_GetParameterFloat"].argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]
        self.f["VBVMR_GetParameterFloat"].restype = ctypes.c_long
        self.f["VBVMR_SetParameterFloat"].argtypes = [ctypes.c_char_p, ctypes.c_float]
        self.f["VBVMR_SetParameterFloat"].restype = ctypes.c_long
        self.f["VBVMR_GetVoicemeeterType"].argtypes = [ctypes.POINTER(ctypes.c_long)]
        self.f["VBVMR_GetVoicemeeterType"].restype = ctypes.c_long

    def login(self, timeout=60.0):
        t0 = time.time()
        rc = -99
        while time.time() - t0 < timeout:
            rc = self.f["VBVMR_Login"](b"")
            if rc == 0:
                return 0
            time.sleep(0.5)
        return rc

    def get(self, name):
        v = ctypes.c_float()
        rc = self.f["VBVMR_GetParameterFloat"](name.encode(), ctypes.byref(v))
        return rc, float(v.value)

    def set(self, name, val):
        return self.f["VBVMR_SetParameterFloat"](name.encode(), ctypes.c_float(float(val)))

    def vmtype(self):
        t = ctypes.c_long()
        rc = self.f["VBVMR_GetVoicemeeterType"](ctypes.byref(t))
        return int(t.value) if rc == 0 else -1


def cands(kind):
    out = []
    for i, d in enumerate(sd.query_devices()):
        n = d["max_input_channels"] if kind == "in" else d["max_output_channels"]
        if n <= 0 or KEY.lower() not in d["name"].lower():
            continue
        if "WASAPI" not in sd.query_hostapis(d["hostapi"])["name"]:
            continue
        out.append(i)
    return out


def make_tone(dur=0.8):
    t = np.arange(int(SR * dur)) / SR
    y = (0.08 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
    r = int(0.015 * SR)
    y[:r] *= np.linspace(0, 1, r)
    y[-r:] *= np.linspace(1, 0, r)
    return y


def probe(out_idx, in_idxs, tone):
    """往 out_idx 播测试音，同时录 in_idxs，返回 {in_idx: 峰值}。"""
    levels = {i: 0.0 for i in in_idxs}
    opened = []

    def mk(idx):
        def cb(indata, frames, tinfo, status):
            v = float(np.max(np.abs(indata)))
            if v > levels[idx]:
                levels[idx] = v
        return cb

    for i in in_idxs:
        try:
            s = sd.InputStream(device=i, channels=1, samplerate=SR, blocksize=1024,
                               dtype="float32", callback=mk(i))
            s.start()
            opened.append(s)
        except Exception:
            pass
    try:
        sd.play(tone, SR, device=out_idx, blocking=True)
    except Exception as e:
        p("        播放失败:", e)
    time.sleep(0.45)
    for s in opened:
        s.stop(); s.close()
    return levels


def main():
    vm = VM()
    rc = vm.login(60.0)
    p("VBVMR_Login rc =", rc, "（0=成功）")
    if rc != 0:
        p("登录失败：请确认 voicemeeter_x64.exe 正在运行")
        return 1
    p("Voicemeeter 类型:", vm.vmtype(), "（1=Voicemeeter 2=Banana 3=Potato）")

    strips = [i for i in range(16) if vm.get("Strip[%d].Mute" % i)[0] == 0]
    buses = [i for i in range(16) if vm.get("Bus[%d].Mute" % i)[0] == 0]
    p("有效 Strip:", strips, " 有效 Bus:", buses, "(Bus 顺序 =", BUS_NAMES[:len(buses)], ")")

    # ---- 快照 ----
    snap = {}
    for i in strips:
        for k in ("Mute", "Gain") + tuple(BUS_NAMES[:len(buses)]):
            rc2, v = vm.get("Strip[%d].%s" % (i, k))
            if rc2 == 0:
                snap["Strip[%d].%s" % (i, k)] = v
    json.dump(snap, open(BEFORE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    p("参数快照已存 → vm_state_before.json（%d 项）" % len(snap))

    # ---- 安全：关闭所有 A 路监听 + 静音硬件输入条 ----
    n_a = 0
    for i in strips:
        for b in BUS_NAMES[:5]:
            if vm.set("Strip[%d].%s" % (i, b), 0.0) == 0:
                n_a += 1
    # 前 3 条视为虚拟输入，其余视为硬件输入（硬件输入静音，避免原始麦克风混进来）
    hw = strips[3:]
    for i in hw:
        vm.set("Strip[%d].Mute" % i, 1.0)
    p("安全静音：关闭 %d 个 A 路送；静音硬件输入条 %s" % (n_a, hw))

    outs = cands("out")
    ins = cands("in")
    p("候选输出 %d 个，候选录音 %d 个" % (len(outs), len(ins)))
    tone = make_tone()

    # ---- 逐 Bus 尝试：打开 B 路 → 实测配对 ----
    found = None
    for bi in range(5, min(8, len(buses))):          # B1,B2,B3
        vm.set("Bus[%d].Mute" % bi, 0.0)
        for i in strips[:3]:                          # 只在虚拟输入条上开
            vm.set("Strip[%d].%s" % (i, BUS_NAMES[bi]), 1.0)
        time.sleep(0.3)
        p("--- 已打开虚拟输入的 %s（Bus[%d]），开始实测 ---" % (BUS_NAMES[bi], bi))
        hit = None
        for o in outs:
            lv = probe(o, ins, tone)
            good = sorted([(k, v) for k, v in lv.items() if v > 0.002], key=lambda kv: -kv[1])
            if good:
                p("    OUT [%2d] → IN [%2d] %-38s 电平 %.5f"
                  % (o, good[0][0], sd.query_devices(good[0][0])["name"][:38], good[0][1]))
                if hit is None:
                    hit = (o, good[0][0])
            else:
                p("    OUT [%2d] → 无" % o)
        if hit:
            found = (hit[0], hit[1], BUS_NAMES[bi], bi)
            break
        # 该 Bus 没通，关掉再试下一个
        for i in strips[:3]:
            vm.set("Strip[%d].%s" % (i, BUS_NAMES[bi]), 0.0)

    # ---- 收敛：只保留找到的那一条 ----
    if found:
        o_idx, i_idx, bname, bi = found
        for i in strips[:3]:
            for b in BUS_NAMES[5:8]:
                vm.set("Strip[%d].%s" % (i, b), 1.0 if b == bname else 0.0)
        p("=== 成功 ===")
        p("    降噪输出 → [%d] %s" % (o_idx, sd.query_devices(o_idx)["name"][:50]))
        p("    应用麦克风 → [%d] %s" % (i_idx, sd.query_devices(i_idx)["name"][:50]))
        p("    Bus = %s，已在虚拟输入条上固定开启" % bname)
    else:
        p("=== 失败：三种 B 路都试过，没有任何录音通道收到信号 ===")
        p("    可能原因：Voicemeeter 引擎未启动（界面上的引擎按钮）")

    # ---- 写 config.json ----
    data = {}
    if os.path.exists(CFG):
        try:
            data = json.load(open(CFG, encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("devices", {})
    for i, d in enumerate(sd.query_devices()):
        if "麦克风阵列" in d["name"] and d["max_input_channels"] > 0 and \
                "WASAPI" in sd.query_hostapis(d["hostapi"])["name"]:
            data["devices"]["input"] = i
            break
    data["devices"]["output"] = found[0] if found else (outs[0] if outs else None)
    data["voicemeeter"] = dict(bus=found[2] if found else None,
                               bus_index=found[3] if found else None,
                               capture_device=found[1] if found else None,
                               capture_device_name=(sd.query_devices(found[1])["name"] if found else None))
    json.dump(data, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    p("已写入 config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
