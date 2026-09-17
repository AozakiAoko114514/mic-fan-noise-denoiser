# -*- coding: utf-8 -*-
"""debug_trace.py —— 打开 DSP 内部探针，看噪声估计与增益到底发生了什么。"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)

from dsp import Denoiser, process_array  # noqa: E402

SR = 48000
NOISE_WAV = os.path.join(os.path.dirname(ROOT), "mic-noise-test.wav")

noise_raw, nsr = sf.read(NOISE_WAV, always_2d=True)
noise = noise_raw.mean(axis=1)
if nsr != SR:
    idx = np.arange(0, len(noise), float(nsr) / SR)
    noise = np.interp(idx, np.arange(len(noise)), noise)
noise = noise / max(1e-12, np.sqrt(np.mean(noise ** 2))) * 10 ** (-56.7 / 20.0)

cfg = dict(trace=True)
den = Denoiser(cfg)
print("n_fft=%d hop=%d learn_frames=%d 频点数=%d" % (den.n, den.hop, den.learn_frames, den.nbins))
process_array(den, noise[: SR * 5])
tr = den.trace
print("总帧数:", len(tr))
print("%6s %9s %9s %9s %11s %11s %11s %8s" %
      ("frame", "E(dB)", "En(dB)", "SNR(db)", "ratio均值", "ratio中位", "G_pre均值", "gate"))
for i in range(0, len(tr), max(1, len(tr) // 22)):
    t = tr[i]
    print("%6d %9.2f %9.2f %9.2f %11.4f %11.4f %11.4f %8.4f" %
          (t["frame"], t["e_db"], t["en_db"], t["snr_db"],
           t["ratio_mean"], t["ratio_med"], t["g_pre_mean"], t["gate"]))
t = tr[-1]
print("末帧: ratio均值=%.4f 中位=%.4f  G_pre均值=%.4f  G_post均值=%.4f" %
      (t["ratio_mean"], t["ratio_med"], t["g_pre_mean"], t["g_post_mean"]))
