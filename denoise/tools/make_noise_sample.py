# -*- coding: utf-8 -*-
"""
make_noise_sample.py -- generate a synthetic "fan noise" sample so that the offline
test suite can run on any machine, without shipping somebody else's recording.

The generated file mimics the noise profile measured on the reference machine
(a laptop cooling fan), i.e. the profile the shipped `harmonics` preset was derived from:

  * broadband floor with most energy between 250-1000 Hz
  * a mechanical harmonic series: fundamental ~52.7 Hz with a strong blade-pass
    harmonic around 363 Hz (7 blades => ~3160 RPM)
  * overall RMS -56.7 dBFS, very stationary (frame-to-frame spread ~4 dB)

Usage:
    python tools/make_noise_sample.py [out_path] [--sec 10] [--rms-db -56.7]
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SR = 48000

# (frequency Hz, prominence dB above the local broadband floor) -- measured, not invented
HARMONICS = [
    (52.7, 23.7), (158.2, 8.5), (210.9, 7.8), (263.7, 9.3), (363.3, 12.9),
    (421.9, 21.0), (703.1, 9.9), (878.9, 8.6), (2068.4, 12.0), (2173.8, 7.6),
]


def shaped_floor(n, sr, seed=12345):
    """Broadband floor shaped to put most of its energy in 250-1000 Hz."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    # broad band-pass shape (in dB), rolling off outside ~100 Hz .. 4 kHz
    shape = np.zeros_like(f)
    lo, mid, hi = 90.0, 600.0, 4000.0
    band = (f >= lo) & (f <= hi)
    shape[band] = -12.0 * np.abs(np.log2(np.maximum(f[band], 1e-6) / mid)) ** 1.4
    shape = np.clip(shape, -60.0, 0.0)
    X *= 10 ** (shape / 20.0)
    return np.fft.irfft(X, n=n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=os.path.join(ROOT, "samples", "mic-noise-test.wav"))
    ap.add_argument("--sec", type=float, default=10.0)
    ap.add_argument("--rms-db", type=float, default=-56.7)
    ap.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = ap.parse_args()

    if os.path.exists(args.out) and not args.force:
        print("refusing to overwrite existing file: %s" % args.out)
        print("(it may be your real recording - pass --force to replace it)")
        return 1

    n = int(args.sec * SR)
    t = np.arange(n) / SR
    x = shaped_floor(n, SR)

    # measure the floor's spectral density near each harmonic so the added tones land at
    # the measured prominence relative to their local neighbourhood
    Xf = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    for f0, prom in HARMONICS:
        k = int(np.argmin(np.abs(freqs - f0)))
        band = slice(max(0, k - 60), min(len(Xf), k + 61))
        local = float(np.median(Xf[band]))
        amp = local * (10 ** (prom / 20.0)) / (n / 4.0)
        x = x + amp * np.sin(2 * np.pi * f0 * t + (f0 % 1.0) * 6.283)

    x /= max(1e-12, np.sqrt(np.mean(x ** 2)))

    # slight level jitter so the frame-energy spread resembles a real recording
    env = 10 ** (0.4 * np.sin(2 * np.pi * 0.07 * t) / 20.0)
    x = x * env
    x /= max(1e-12, np.sqrt(np.mean(x ** 2)))
    x *= 10 ** (args.rms_db / 20.0)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    stereo = np.stack([x, x], axis=1).astype(np.float32)
    sf.write(args.out, stereo, SR)
    print("wrote %s  (%.1fs, %.1f dBFS RMS, %d harmonics)"
          % (args.out, args.sec, args.rms_db, len(HARMONICS)))
    print("note: synthetic. For real results record your own noise with:  live.py --ab 10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
