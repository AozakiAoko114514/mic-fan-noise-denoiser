# -*- coding: utf-8 -*-
"""
compare_ab.py —— 对 live.py --ab 录出的两段真实音频做定量对比

这是最有说服力的一环：不是仿真，是同一台机器、同一个麦克风阵列、
同一时刻录下来的"原始"与"降噪后"两段，做频谱级对比。
"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAIRS = (("原始", os.path.join(ROOT, "ab_1_raw.wav")),
         ("降噪后", os.path.join(ROOT, "ab_2_denoised.wav")))
BANDS = ((20, 120), (120, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000))
SKIP_SEC = 3.0


def dbfs(x):
    r = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-30))
    return 20.0 * np.log10(max(r, 1e-12))


def main():
    sig = {}
    sr = None
    for name, path in PAIRS:
        if not os.path.exists(path):
            print("缺少文件:", path, "—— 先运行 live.py --ab <秒数>")
            return 1
        x, sr = sf.read(path, always_2d=True)
        sig[name] = x.mean(axis=1)
    n = min(len(v) for v in sig.values())
    for k in sig:
        sig[k] = sig[k][:n]
    k_skip = int(SKIP_SEC * sr)

    print("=" * 88)
    print("真实录音 A/B 对比（%d Hz，%.1f 秒，其中前 %.0f 秒为噪声估计收敛期）"
          % (sr, n / sr, SKIP_SEC))
    print("=" * 88)
    a, b = sig["原始"], sig["降噪后"]
    print("1) 总电平")
    print("     整段    原始 %7.2f → 降噪后 %7.2f dBFS   %+.2f dB"
          % (dbfs(a), dbfs(b), dbfs(b) - dbfs(a)))
    print("     稳态段  原始 %7.2f → 降噪后 %7.2f dBFS   %+.2f dB"
          % (dbfs(a[k_skip:]), dbfs(b[k_skip:]), dbfs(b[k_skip:]) - dbfs(a[k_skip:])))

    def bands(x):
        m = min(len(x), sr * 10)
        X = np.abs(np.fft.rfft(x[:m] * np.hanning(m))) ** 2
        f = np.fft.rfftfreq(m, 1.0 / sr)
        return [10 * np.log10(max(float(X[(f >= lo) & (f < hi)].sum()), 1e-30)) for lo, hi in BANDS]

    ba, bb = bands(a[k_skip:]), bands(b[k_skip:])
    print("2) 稳态段频段电平（dBFS）")
    for (lo, hi), va, vb in zip(BANDS, ba, bb):
        print("     %-11s %8.2f → %8.2f   %+7.2f dB" % ("%d-%d Hz" % (lo, hi), va, vb, vb - va))

    def prom(x, f0_list):
        m = min(len(x), sr * 6)
        X = np.abs(np.fft.rfft(x[:m] * np.hanning(m)))
        f = np.fft.rfftfreq(m, 1.0 / sr)
        p = 20 * np.log10(np.maximum(X, 1e-12))
        out = {}
        for f0 in f0_list:
            k = int(np.argmin(np.abs(f - f0)))
            lo, hi = max(0, k - 120), min(len(p), k + 121)
            base = np.median(np.concatenate((p[lo:max(lo + 1, k - 6)], p[k + 7:hi])))
            out[f0] = float(p[k] - base)
        return out

    tgt = [52.7, 363.3, 421.9, 703.1, 878.9]
    pa, pb = prom(a, tgt), prom(b, tgt)
    print("3) 机械谐波突出度（相对局部基线 dB）")
    for f0 in tgt:
        print("     %8.1f Hz   %6.1f → %6.1f   %+6.1f dB" % (f0, pa[f0], pb[f0], pb[f0] - pa[f0]))

    print("4) 噪声底噪（稳态段能量最高的 5%% 帧之外的分位数）")
    for name in ("原始", "降噪后"):
        x = sig[name][k_skip:]
        w = int(0.05 * sr)
        e = np.array([np.mean(x[i:i + w] ** 2) for i in range(0, len(x) - w, w)])
        print("     %-6s 中位 %7.2f dBFS   p10 %7.2f   p90 %7.2f"
              % (name, 10 * np.log10(max(float(np.median(e)), 1e-20)),
                 10 * np.log10(max(float(np.percentile(e, 10)), 1e-20)),
                 10 * np.log10(max(float(np.percentile(e, 90)), 1e-20))))
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
