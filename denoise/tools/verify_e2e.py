# -*- coding: utf-8 -*-
"""
verify_e2e.py —— 端到端验证：麦克风阵列 → 降噪 → Voicemeeter → 虚拟麦克风

同时开三条流：
  A. 麦克风阵列（输入）—— 顺便作为"原始"参考
  B. Voicemeeter 虚拟输入（输出）—— 送降噪后的音频
  C. Voicemeeter Out N（输入）—— 模拟应用读取虚拟麦克风，录回来
然后对比 A 与 C 的电平，确认降噪真的作用到了"应用能看到的那一路"。
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
from dsp import Denoiser  # noqa: E402

SR = 48000
SEC = 14.0
CONVERGE = 5.0


def dbfs(x):
    r = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-30))
    return 20.0 * np.log10(max(r, 1e-12))


def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    dev = cfg["devices"]
    vm = cfg.get("voicemeeter", {})
    din, dout, dcap = dev["input"], dev["output"], vm.get("capture_device")
    print("麦克风阵列 [%d] %s" % (din, sd.query_devices(din)["name"][:46]))
    print("虚拟输入   [%d] %s" % (dout, sd.query_devices(dout)["name"][:46]))
    print("虚拟麦克风 [%s] %s" % (dcap, sd.query_devices(dcap)["name"][:46] if dcap else "(未配置)"))
    if dcap is None:
        print("config.json 里没有 capture_device，请先跑 tests/vm_setup.py")
        return 2

    den = Denoiser()
    hop = den.hop
    raw_parts, cap_parts = [], []

    def cb_mic(indata, outdata, frames, tinfo, status):
        mono = indata.mean(axis=1)
        raw_parts.append(mono.astype(np.float64).copy())
        y = den.process(mono)
        outdata[:, 0] = y
        if outdata.shape[1] > 1:
            outdata[:, 1] = y

    def cb_cap(indata, frames, tinfo, status):
        cap_parts.append(indata[:, 0].astype(np.float64).copy())

    print("运行 %.0f 秒（保持安静，让风扇噪声作为测试信号）…" % SEC)
    try:
        with sd.InputStream(device=dcap, channels=1, samplerate=SR, blocksize=hop,
                            dtype="float32", callback=cb_cap):
            with sd.Stream(device=(din, dout), channels=(2, 2), samplerate=SR,
                           blocksize=hop, dtype="float32", callback=cb_mic):
                time.sleep(SEC)
    except Exception as e:
        print("打开流失败:", e)
        return 1

    raw = np.concatenate(raw_parts)
    cap = np.concatenate(cap_parts)
    n = min(len(raw), len(cap))
    raw, cap = raw[:n], cap[:n]
    k = int(CONVERGE * SR)
    full = dbfs(cap) - dbfs(raw)
    steady = (dbfs(cap[k:]) - dbfs(raw[k:])) if n > k else float("nan")
    print("=" * 80)
    print("整段     麦克风阵列 %7.2f dBFS   →   Voicemeeter Out %7.2f dBFS   压制 %+.2f dB"
          % (dbfs(raw), dbfs(cap), full))
    print("收敛后   麦克风阵列 %7.2f dBFS   →   Voicemeeter Out %7.2f dBFS   压制 %+.2f dB"
          % (dbfs(raw[k:]), dbfs(cap[k:]), steady))
    print("=" * 80)
    # 判定用收敛后的数值：前几秒是噪声估计的收敛期，必然拉低整段平均值
    ok = (steady == steady) and steady < -15.0
    print("结论：%s" % ("✅ 端到端打通，降噪确实作用在应用能看到的那一路上"
                        if ok else "❌ 压制不足，链路可能没接通"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
