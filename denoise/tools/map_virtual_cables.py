# -*- coding: utf-8 -*-
"""
map_virtual_cables.py —— 自动测出"输出设备 → 录音设备"的对应关系

背景：Voicemeeter 的虚拟输入在 Windows 里名字都一样（扬声器 (VB-Audio Voicemeeter VAIO)），
      靠名字无法区分哪一路是 Voicemeeter Input、哪一路是 Aux。本脚本用测试音实测映射。

做法：逐个向候选输出设备播 1 kHz 测试音，同时监听所有候选录音设备，
      看哪一路收到了信号 → 得到"往哪个输出送，应用该从哪个录音通道取"。
"""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

import sounddevice as sd  # noqa: E402

SR = 48000
KEY = "Voicemeeter"


def dev(i):
    d = dict(sd.query_devices(i))
    d["hostapi_name"] = sd.query_hostapis(d["hostapi"])["name"]
    return d


def candidates(kind):
    out = []
    for i, d in enumerate(sd.query_devices()):
        n = (d["max_input_channels"] if kind == "in" else d["max_output_channels"])
        if n <= 0 or KEY.lower() not in d["name"].lower():
            continue
        info = dev(i)
        if "WASAPI" not in info["hostapi_name"]:
            continue
        out.append(info)
    return out


def main():
    outs = candidates("out")
    ins = candidates("in")
    print("候选输出 %d 个，候选输入 %d 个" % (len(outs), len(ins)))
    for d in outs:
        print("  OUT [%2d] %s" % (d["index"], d["name"][:60]))
    for d in ins:
        print("  IN  [%2d] %s" % (d["index"], d["name"][:60]))

    t = np.arange(int(SR * 0.6)) / SR
    tone = (0.08 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
    # 淡入淡出，避免爆音
    r = int(0.01 * SR)
    tone[:r] *= np.linspace(0, 1, r)
    tone[-r:] *= np.linspace(1, 0, r)

    print("\n开始逐个探测（每个 0.6 秒测试音，音量很低）…\n")
    mapping = {}
    for o in outs:
        levels = {d["index"]: 0.0 for d in ins}
        opened = []

        def mk(idx):
            def cb(indata, frames, tinfo, status):
                v = float(np.max(np.abs(indata)))
                if v > levels[idx]:
                    levels[idx] = v
            return cb

        for d in ins:
            try:
                s = sd.InputStream(device=d["index"], channels=1, samplerate=SR,
                                   blocksize=1024, dtype="float32", callback=mk(d["index"]))
                s.start()
                opened.append(s)
            except Exception as e:
                print("  （无法打开 IN [%d] %s: %s）" % (d["index"], d["name"][:30], e))
        try:
            sd.play(tone, SR, device=o["index"], blocking=True)
        except Exception as e:
            print("  OUT [%2d] 播放失败: %s" % (o["index"], e))
            for s in opened:
                s.stop(); s.close()
            continue
        time.sleep(0.4)
        for s in opened:
            s.stop(); s.close()

        hits = [(k, v) for k, v in levels.items() if v > 0.002]
        if hits:
            hits.sort(key=lambda kv: -kv[1])
            desc = "  ".join("[%d] 电平 %.4f" % (k, v) for k, v in hits)
            print("  OUT [%2d] %-42s → %s" % (o["index"], o["name"][:42], desc))
            mapping[o["index"]] = [k for k, _ in hits]
        else:
            print("  OUT [%2d] %-42s → （无任何录音通道收到信号）" % (o["index"], o["name"][:42]))

    print("\n=== 结论 ===")
    if not mapping:
        print("  全部无信号：说明虚拟通道当前不通。")
        print("  原因通常是 Voicemeeter 主程序没有运行（虚拟设备在，但没有内部路由）。")
        print("  请启动 Voicemeeter 后重新运行本脚本。")
    else:
        print("  可用映射（往左边输出送音频，应用就从右边录音通道取）：")
        for k, v in mapping.items():
            print("    OUT [%2d]  →  %s" % (k, ", ".join("IN [%d]" % x for x in v)))
        print("  建议：用第一个映射作为降噪输出，并把对应的录音通道选为 CS2 的麦克风。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
