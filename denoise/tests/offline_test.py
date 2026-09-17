# -*- coding: utf-8 -*-
"""
offline_test.py —— 离线客观测试（不需要麦克风、不需要 Voicemeeter）

用"已知的干净合成语音"当参考，混入"实测的真实风扇噪声"，再过降噪链。
因为干净参考已知，可以算出真实的信噪比提升与语音损伤。

指标口径（很重要，口径错了结论就会错）：
  * 噪声抑制：分"整段"和"稳态段（第 3 秒起）"两个口径。
    因为噪声估计需要 ~2 s 收敛，整段平均会严重低估真实性能。
  * 语音损伤：用 1/6 倍频程带宽谱的 LSD。
    窄带陷波（±18 Hz）在逐频点 LSD 上会显示成巨大损伤，但在听觉上几乎无害；
    按 1/6 倍频程合成后，才反映真正可感知的频谱包络改变。
  * ΔSNR：混音过链前后，用"语音段/停顿段"能量比估算。
"""

import os
import sys
import time

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

from dsp import Denoiser, process_array  # noqa: E402

SR = 48000
# 噪声样本已归类到 mic-denoise\samples\（旧位置保留兼容）
NOISE_WAV = os.path.join(ROOT, "samples", "mic-noise-test.wav")
if not os.path.exists(NOISE_WAV):
    NOISE_WAV = os.path.join(os.path.dirname(ROOT), "mic-noise-test.wav")
BANDS = ((20, 120), (120, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000))
CONVERGE_SEC = 3.0      # 噪声估计收敛时间（滑动窗口 2 s + 起始静音）


def dbfs(x):
    r = float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-30))
    return 20.0 * np.log10(max(r, 1e-12))


def make_speech(dur=8.0, sr=SR, f0=118.0):
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0t = f0 * (1.0 + 0.10 * np.sin(2 * np.pi * 0.35 * t) + 0.03 * np.sin(2 * np.pi * 1.7 * t))
    phase = 2 * np.pi * np.cumsum(f0t) / sr
    formants = [(700, 1.0, 90), (1220, 0.5, 110), (2600, 0.22, 160), (3400, 0.10, 200)]
    sig = np.zeros(n)
    for h in range(1, 60):
        fh = h * f0
        if fh > sr * 0.45:
            break
        amp = sum(g / (1.0 + ((fh - fc) / bw) ** 2) for fc, g, bw in formants)
        sig += amp * np.sin(h * phase)
    sig /= max(1e-9, np.max(np.abs(sig)))
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 3.6 * t - 1.2)) ** 1.5
    gate = np.ones(n)
    r = int(0.02 * sr)
    for a, b in ((2.2, 3.0), (5.0, 5.8)):
        i0, i1 = int(a * sr), int(b * sr)
        gate[i0:i1] = 0.0
        if i0 - r >= 0:
            gate[i0 - r:i0] = np.linspace(1.0, 0.0, r)
        if i1 + r <= n:
            gate[i1:i1 + r] = np.linspace(0.0, 1.0, r)
    sig = sig * env * gate
    sig /= max(1e-9, np.sqrt(np.mean(sig ** 2)))
    return sig, gate


def prominence(x, sr=SR, targets=(363.3, 421.9)):
    n = min(len(x), sr * 6)
    X = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    p = 20 * np.log10(np.maximum(X, 1e-12))
    out = {}
    for f0 in targets:
        k = int(np.argmin(np.abs(freqs - f0)))
        lo, hi = max(0, k - 120), min(len(p), k + 121)
        base = np.median(np.concatenate((p[lo:max(lo + 1, k - 6)], p[k + 7:hi])))
        out[f0] = float(p[k] - base)
    return out


def band_edges(sr=SR, f_lo=100.0, f_hi=8000.0, per_oct=6):
    n_band = int(np.round(per_oct * np.log2(f_hi / f_lo)))
    return f_lo * 2 ** (np.arange(n_band + 1) / per_oct)


def band_spectrum_db(x, sr=SR, n_fft=1024, hop=512, edges=None):
    """1/6 倍频程带宽谱（dB），返回 (帧数, 频带数)。"""
    edges = band_edges(sr) if edges is None else edges
    w = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    n = len(x)
    rows = []
    for i in range(0, n - n_fft, hop):
        P = np.abs(np.fft.rfft(x[i:i + n_fft] * w)) ** 2
        row = []
        for a, b in zip(edges[:-1], edges[1:]):
            m = (freqs >= a) & (freqs < b)
            row.append(10.0 * np.log10(max(float(P[m].mean()) if m.any() else 1e-20, 1e-20)))
        rows.append(row)
    return np.array(rows)


def lsd_bands(ref, test, sr=SR):
    """1/6 倍频程带的谱失真（dB），只统计参考明确有声的帧。"""
    A = band_spectrum_db(ref, sr)
    B = band_spectrum_db(test, sr)
    n = min(len(A), len(B))
    A, B = A[:n], B[:n]
    if n == 0:
        return float("nan")
    energy = A.max(axis=1)
    keep = energy > (energy.max() - 20.0)
    if not np.any(keep):
        return float("nan")
    return float(np.sqrt(np.mean((A[keep] - B[keep]) ** 2)))


def frame_split_db(x, gate, sr=SR, win_ms=40.0):
    w = int(win_ms / 1000.0 * sr)
    a, b = [], []
    for i in range(0, len(x) - w, w):
        if np.all(gate[i:i + w] > 0.9):
            a.append(float(np.mean(x[i:i + w] ** 2)))
        elif np.all(gate[i:i + w] < 0.1):
            b.append(float(np.mean(x[i:i + w] ** 2)))
    if not a or not b:
        return float("nan")
    return 10.0 * np.log10((np.mean(a) + 1e-20) / (np.mean(b) + 1e-20))


def band_levels(x, sr=SR):
    n = min(len(x), sr * 6)
    X = np.abs(np.fft.rfft(x[:n] * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    return [10.0 * np.log10(max(float(X[(f >= a) & (f < b)].sum()), 1e-30)) for a, b in BANDS]


def suite(cfg, noise, speech, gate, snr_target=20.0):
    den = Denoiser(cfg)
    on = process_array(den, noise)
    den = Denoiser(cfg)
    osp = process_array(den, speech)
    nscale = 10 ** ((dbfs(speech) - snr_target - dbfs(noise)) / 20.0)
    mix = speech + noise * nscale
    den = Denoiser(cfg)
    om = process_array(den, mix)
    t0 = time.perf_counter()
    den = Denoiser(cfg)
    process_array(den, np.tile(noise, 3)[:SR * 10])
    rtf = (time.perf_counter() - t0) / 10.0
    ref = Denoiser(cfg)
    k = int(CONVERGE_SEC * SR)
    sup_full = dbfs(on) - dbfs(noise)
    sup_steady = dbfs(on[k:]) - dbfs(noise[k:]) if len(on) > k else float("nan")
    return dict(sup_full=sup_full, sup_steady=sup_steady,
                lsd=lsd_bands(speech, osp),
                dsnr=frame_split_db(om, gate) - frame_split_db(mix, gate),
                rtf=rtf, lat_ms=1000.0 * ref.latency_samples / SR,
                bin_hz=SR / float(ref.n))


def main():
    if not os.path.exists(NOISE_WAV):
        print("找不到噪声样本:", NOISE_WAV)
        return 1
    noise_raw, nsr = sf.read(NOISE_WAV, always_2d=True)
    noise = noise_raw.mean(axis=1)
    if nsr != SR:
        idx = np.arange(0, len(noise), float(nsr) / SR)
        noise = np.interp(idx, np.arange(len(noise)), noise)
    speech, gate = make_speech(dur=8.0)
    L = min(len(speech), len(noise))
    speech, noise = speech[:L], noise[:L]
    noise = noise / max(1e-12, np.sqrt(np.mean(noise ** 2))) * 10 ** (-56.7 / 20.0)
    speech = speech / max(1e-12, np.sqrt(np.mean(speech ** 2))) * 10 ** (-26.0 / 20.0)
    k = int(CONVERGE_SEC * SR)

    print("=" * 96)
    print("离线客观测试：干净合成语音 + 实测真实风扇噪声（噪声 RMS = -56.7 dBFS，语音 RMS = -26.0 dBFS）")
    print("=" * 96)

    den = Denoiser()
    on = process_array(den, noise)
    print("1) 噪声抑制          整段 %+.2f dB   稳态段(≥3s) %+.2f dB   输出 %.2f dBFS"
          % (dbfs(on) - dbfs(noise), dbfs(on[k:]) - dbfs(noise[k:]), dbfs(on)))

    den = Denoiser()
    osp = process_array(den, speech)
    print("2) 语音损伤          1/6 倍频程带 LSD = %5.2f dB（越小越好）  电平变化 %+.2f dB"
          % (lsd_bands(speech, osp), dbfs(osp) - dbfs(speech)))

    print("3) 混音信噪比提升")
    print("     %-10s %12s %12s %10s" % ("目标SNR", "SNR_in", "SNR_out", "ΔSNR"))
    for target in (30.0, 20.0, 10.0):
        nscale = 10 ** ((dbfs(speech) - target - dbfs(noise)) / 20.0)
        mix = speech + noise * nscale
        den = Denoiser()
        out = process_array(den, mix)
        si, so = frame_split_db(mix, gate), frame_split_db(out, gate)
        print("     %-10s %12.2f %12.2f %10.2f" % ("%g dB" % target, si, so, so - si))

    pi, po = prominence(noise), prominence(on)
    print("4) 谐波突出度        " + "    ".join(
        "%.1f Hz: %5.1f → %5.1f dB" % (x, pi[x], po[x]) for x in sorted(pi)))

    bi, bo = band_levels(noise), band_levels(on)
    print("5) 频段绝对电平（噪声，dBFS）")
    for (a, b), vi, vo in zip(BANDS, bi, bo):
        print("     %-11s %9.2f → %9.2f   %+7.2f dB" % ("%d-%d Hz" % (a, b), vi, vo, vo - vi))

    r0 = suite({}, noise, speech, gate)
    print("6) 实时性能          RTF = %.4f（单线程，含噪声估计）   算法延迟 %.0f ms（不含声卡缓冲）"
          % (r0["rtf"], r0["lat_ms"]))

    peaks = np.concatenate((on, osp))
    print("7) 安全自检          NaN/Inf=%s   峰值 %.1f dBFS   最大样本跳变 %.4f"
          % ("有!" if not np.all(np.isfinite(peaks)) else "无",
             20 * np.log10(max(float(np.max(np.abs(peaks))), 1e-12)),
             float(np.max(np.abs(np.diff(osp))))))

    print("8) 分辨率对比（陷波精度 vs 延迟）")
    print("     %-8s %10s %10s %14s %12s %12s %9s"
          % ("档位", "频点间隔", "算法延迟", "噪声抑制(稳态)", "语音损伤", "ΔSNR(20dB)", "RTF"))
    for name, cfg in (("低延迟", dict(n_fft=2048, hop=512)),
                      ("默认★", dict(n_fft=4096, hop=1024)),
                      ("高质量", dict(n_fft=8192, hop=2048))):
        r = suite(cfg, noise, speech, gate)
        print("     %-8s %8.1f Hz %8.0f ms %11.2f dB %9.2f dB %9.2f dB %9.4f"
              % (name, r["bin_hz"], r["lat_ms"], r["sup_steady"], r["lsd"], r["dsnr"], r["rtf"]))

    print("9) 参数扫描")
    print("     %-9s %-9s %-9s %14s %11s %11s" %
          ("噪声偏差", "过减因子", "门限dB", "噪声抑制(稳态)", "语音损伤", "ΔSNR(20dB)"))
    for bias in (1.5, 2.5, 4.0):
        for gth in (4.0, 6.0):
            r = suite(dict(noise_bias=bias, over_sub=2.5, gate_thresh_db=gth, gate_range_db=6.0),
                      noise, speech, gate)
            print("     %-9.1f %-9.1f %-9.1f %11.2f dB %8.2f dB %8.2f dB"
                  % (bias, 2.5, gth, r["sup_steady"], r["lsd"], r["dsnr"]))
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
