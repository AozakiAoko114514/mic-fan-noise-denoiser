# -*- coding: utf-8 -*-
"""
measure_noise.py -- analyse a noise recording and derive a notch plan for dsp.py.

Record your noise first (stay quiet, keep the fans running):

    python live.py --ab 10                       # writes samples/ab_1_raw.wav
    python tools/measure_noise.py samples/ab_1_raw.wav

Output: level, channel correlation, stationarity, band distribution, spectral
flatness, and the strongest tonal peaks with a ready-to-paste `harmonics` list.

    python tools/measure_noise.py rec.wav --json notch_plan.json
"""
import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf
from numpy.lib.stride_tricks import sliding_window_view

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT = os.path.join(ROOT, "samples", "mic-noise-test.wav")

BANDS = [(20, 60), (60, 120), (120, 250), (250, 500), (500, 1000),
         (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000), (16000, 24000)]


def dbfs(v):
    return 20.0 * np.log10(max(float(v), 1e-12))


def main():
    ap = argparse.ArgumentParser(description="analyse fan/AC noise and suggest notches")
    ap.add_argument("wav", nargs="?", default=DEFAULT)
    ap.add_argument("--min-prom", type=float, default=7.0, help="peak prominence threshold (dB)")
    ap.add_argument("--max-peaks", type=int, default=12)
    ap.add_argument("--json", metavar="OUT", help="write the suggested harmonics to a JSON file")
    args = ap.parse_args()

    if not os.path.exists(args.wav):
        print("file not found: %s" % args.wav)
        print("record one with:  python live.py --ab 10")
        print("or generate a synthetic one:  python tools/make_noise_sample.py")
        return 1

    x, sr = sf.read(args.wav, always_2d=True)
    dur = x.shape[0] / sr
    print("WAV: %d Hz, %d ch, %.2f s  (%s)" % (sr, x.shape[1], dur, os.path.basename(args.wav)))

    for i in range(x.shape[1]):
        print("  ch%d  RMS %.1f dBFS  peak %.1f dBFS"
              % (i, dbfs(x[:, i].std()), dbfs(np.abs(x[:, i]).max())))
    if x.shape[1] >= 2:
        a, b = x[:, 0], x[:, 1]
        if a.std() > 1e-9 and b.std() > 1e-9:
            print("  L/R correlation = %.4f   (1.0 => same signal / already processed by the driver)"
                  % float(np.corrcoef(a, b)[0, 1]))

    mono = x.mean(axis=1).astype(np.float64)
    mono -= mono.mean()
    n0, n1 = int(sr * min(1.5, dur * 0.2)), int(min(len(mono), sr * (dur - 0.5)))
    seg = mono[n0:n1] if n1 > n0 else mono
    print("analysing %.1f s, RMS %.1f dBFS" % (len(seg) / sr, dbfs(seg.std())))

    N = 8192
    hop = N // 2
    win = np.hanning(N)
    frames = []
    i = 0
    while i + N <= len(seg):
        frames.append(np.abs(np.fft.rfft(seg[i:i + N] * win)))
        i += hop
    if not frames:
        frames = [np.abs(np.fft.rfft(seg * np.hanning(len(seg))))]
    P = np.array(frames)
    freqs = np.fft.rfftfreq(N, 1.0 / sr)
    avg = P.mean(axis=0)
    pdb = 20 * np.log10(np.maximum(avg, 1e-12))

    fe = 20 * np.log10(np.maximum(P.sum(axis=1), 1e-12))
    print("frame level: mean %.1f dB, spread %.1f dB  (small spread => stationary => easy to remove)"
          % (fe.mean(), fe.max() - fe.min()))

    tot = float((avg ** 2).sum())
    print("band energy distribution:")
    for lo, hi in BANDS:
        m = (freqs >= lo) & (freqs < hi)
        if not m.any():
            continue
        e = float((avg[m] ** 2).sum())
        print("  %6d-%-6d Hz : %5.1f %%   (%.1f dB rel)" % (lo, hi, 100.0 * e / tot,
                                                           10 * np.log10(max(e / tot, 1e-9))))

    efull = float((avg[(freqs >= 20) & (freqs <= 20000)] ** 2).sum())
    eh = float((avg[(freqs >= 120) & (freqs <= 20000)] ** 2).sum())
    esp = float((avg[(freqs >= 300) & (freqs <= 3400)] ** 2).sum())
    print("high-pass at 120 Hz would remove %.1f dB of total energy "
          "(i.e. %s)" % (-10 * np.log10(max(eh / efull, 1e-9)),
                         "useless - the noise sits in the speech band"
                         if esp / efull > 0.7 else "may help"))
    print("speech band 300-3400 Hz share: %.1f %% of energy" % (100.0 * esp / efull))
    ms = (freqs >= 200) & (freqs <= 8000)
    p = np.maximum(avg[ms], 1e-12)
    print("spectral flatness 200-8000 Hz = %.3f  (near 0 = tonal/hum, near 1 = broadband hiss)"
          % float(np.exp(np.log(p).mean()) / p.mean()))

    # ---- tonal peaks ----
    w = 161
    pad = np.pad(pdb, w // 2, mode="edge")
    base = np.median(sliding_window_view(pad, w), axis=1)
    prom = pdb - base
    cand = np.where(prom > args.min_prom)[0]
    cand = cand[(freqs[cand] >= 40) & (freqs[cand] <= 12000)]
    groups = []
    for k in cand:
        if groups and k - groups[-1][-1] <= 3:
            groups[-1].append(k)
        else:
            groups.append([k])
    peaks = []
    for g in groups:
        k = g[int(np.argmax(prom[g]))]
        peaks.append((float(freqs[k]), float(prom[k])))
    peaks.sort(key=lambda t: -t[1])
    peaks = peaks[:args.max_peaks]

    print("tonal peaks (prominence > %.0f dB): %d" % (args.min_prom, len(peaks)))
    for f, pr in peaks:
        print("  %8.1f Hz   prominence %5.1f dB" % (f, pr))

    if peaks:
        f0 = min(f for f, _ in peaks)
        print("lowest strong peak = %.1f Hz" % f0)
        for blades in (5, 6, 7, 9):
            print("    if this is a blade-pass tone with %d blades -> %.0f RPM" % (blades, f0 * 60.0 / blades))

    # ---- notch plan ----
    harmonics = []
    for f, pr in peaks:
        depth = -min(24.0, max(6.0, pr))
        half_bw = max(14.0, f * 0.05)
        harmonics.append([round(f, 1), round(half_bw, 1), round(depth, 1)])
    print("suggested harmonics list (frequency_Hz, half_bandwidth_Hz, depth_dB):")
    print("    harmonics=%r" % (harmonics,))
    print("paste it into denoise/dsp.py DEFAULT_CFG['harmonics'] "
          "(n_fft=4096 keeps the notches precise)")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"harmonics": harmonics,
                       "measured": {
                           "wav": os.path.abspath(args.wav),
                           "rms_dbfs": round(dbfs(seg.std()), 2),
                           "band_share_speech_300_3400": round(100.0 * esp / efull, 1),
                           "stationarity_spread_db": round(float(fe.max() - fe.min()), 2),
                       }}, f, ensure_ascii=False, indent=2)
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
