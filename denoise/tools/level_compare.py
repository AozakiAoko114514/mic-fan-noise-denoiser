# -*- coding: utf-8 -*-
"""
level_compare.py —— 实时对比：原始麦克风 vs 虚拟麦克风（可在管线运行时使用）

同时录两路：
  * 麦克风阵列（原始）
  * Voicemeeter Out N（应用看到的虚拟麦克风）
打印两者电平与压制量，用来一眼确认管线是否在工作。
"""
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

SR = 48000


def dbfs(x):
    return 20.0 * np.log10(max(float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))), 1e-12))


def main():
    sec = 6.0
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    din = cfg["devices"]["input"]
    dcap = cfg.get("voicemeeter", {}).get("capture_device")
    if dcap is None:
        print("config.json 里没有虚拟麦克风，请先跑 tests/vm_setup.py")
        return 2
    print("保持安静 %.0f 秒…（原始 %s  /  虚拟 %s）"
          % (sec, sd.query_devices(din)["name"][:30], sd.query_devices(dcap)["name"][:30]))
    a, b = [], []

    def c1(i, f, t, s):
        a.append(i.mean(axis=1).copy())

    def c2(i, f, t, s):
        b.append(i[:, 0].copy())

    with sd.InputStream(device=din, channels=2, samplerate=SR, blocksize=1024,
                        dtype="float32", callback=c1):
        with sd.InputStream(device=dcap, channels=1, samplerate=SR, blocksize=1024,
                            dtype="float32", callback=c2):
            time.sleep(sec)
    x, y = np.concatenate(a), np.concatenate(b)
    lv_in, lv_out = dbfs(x), dbfs(y)
    print("-" * 62)
    print("  原始麦克风 [%d] %-32s %8.2f dBFS" % (din, sd.query_devices(din)["name"][:32], lv_in))
    print("  虚拟麦克风 [%d] %-32s %8.2f dBFS" % (dcap, sd.query_devices(dcap)["name"][:32], lv_out))
    print("  压制量 %+.2f dB" % (lv_out - lv_in))
    print("-" * 62)
    if lv_out < -110:
        print("  ⚠ 虚拟麦克风几乎是绝对静音 → 管线很可能没在运行（或 Voicemeeter 没开）")
    elif lv_out - lv_in < -10:
        print("  ✅ 管线工作中")
    else:
        print("  ⚠ 压制不明显 → 可能处于启动收敛期（前 2~3 秒），或管线未接通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
