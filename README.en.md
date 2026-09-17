# Mic Fan-Noise Denoiser

> A real-time **virtual microphone** for Windows that removes *stationary* machine noise
> (laptop/cooling fans, air conditioners, PC case fans) using a noise profile
> **measured on your own machine** — instead of handing your voice to a generic model.

[中文说明 / Chinese docs](README.md)

```
Mic array ──WASAPI──▶ DSP chain ──▶ Voicemeeter virtual input ──▶ any app (game / VoIP / recording)
```

## Why another denoiser?

Generic AI denoisers (NVIDIA Broadcast, Krisp, RNNoise…) are excellent at *unknown* noise,
but for a **fixed, stationary noise source** they have two problems:

1. They are black boxes: to kill a narrow tonal peak they often thin out the whole voice,
   which sounds dull;
2. The GPU ones compete with your game for the GPU and keep the discrete GPU pinned in a
   high-power state.

This project does the opposite — **measure first, then remove surgically**:

* discrete harmonics (fan rotation rate, blade-pass tone, their multiples) are cut with
  narrow notches that leave the voice alone;
* the broadband floor is removed with an over-subtraction Wiener filter driven by a
  sliding-minimum noise estimate;
* and it runs **on the CPU, pinned to E-cores**, so it costs ≈0 FPS in CPU-bound games.

## Features

* Real-time WASAPI capture/render, ~64 ms algorithmic latency (32/64/128 ms presets)
* Measured harmonic notch bank + adaptive spectral subtraction + soft noise gate
* Adaptive noise estimation (self-healing; no manual noise profile needed)
* **Automated Voicemeeter routing setup, verified with real audio** — its eight virtual
  devices share one friendly name, so the tool plays test tones and listens back to map them
* Objective offline test suite (synthetic speech + real noise → SNR gain, distortion, RTF)
* End-to-end verification on the virtual microphone itself
* One-click manager (start / stop / status / live compare), everything rollback-able

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11 (WASAPI) |
| Python | 3.9+ (tested on 3.13) |
| Packages | `pip install -r requirements.txt` (numpy, sounddevice, soundfile) |
| Virtual audio | [Voicemeeter Potato](https://voicemeeter.com/) (free) |

## Quick start

```bat
pip install -r requirements.txt

:: 1. measure your own noise (stay quiet, keep the fans running)
python denoise\live.py --ab 10
python denoise\tools\measure_noise.py denoise\samples\ab_1_raw.wav
::    -> paste the suggested `harmonics` list into denoise\dsp.py

:: 2. start Voicemeeter, then let the tool configure + verify the routing
python denoise\tools\vm_setup.py

:: 3. run it
python denoise\live.py --run
::    or:  python manager\manager.py     ([1] start, [4] live verify, [2] stop)

:: 4. select the reported capture device (e.g. "Voicemeeter Out 6") as your microphone
```

No recording at hand? Generate a synthetic sample that mimics the reference profile:

```bat
python denoise\tools\make_noise_sample.py
```

## How it works

```
               ┌──────────────────── DSP (per 10.7 ms hop, fully vectorised) ───────────────────┐
 mic ──STFT──▶ │ 1. low-cut  +  harmonic notch bank (measured peaks)                           │
 (4096/Hann)   │ 2. noise estimate N[k]  ← sliding minimum over a 2 s window (+ bias comp.)     │
               │ 3. over-subtraction Wiener gain   G = clip(1 − β·N[k]/P[k])  + gain smoothing │
               │ 4. frame-level soft noise gate (frame SNR vs noise floor)                      │
               └──────────────────────────── ISTFT / overlap-add ──────────────────────────────┘
```

Details that matter (all found the hard way — see the comments in `denoise/dsp.py`):

* **The decision power spectrum must be time-smoothed.** A single-frame periodogram is
  exponentially distributed (±5.6 dB); using it directly makes the subtraction a no-op.
* **A running minimum is a trap.** It latches onto a quiet startup fragment and never
  recovers, which inflates the frame SNR and freezes the noise estimate in a deadlock.
  A *sliding* minimum window self-heals within ~2 s.
* **Sliding minima are biased low** — a bias factor (`noise_bias`) brings the estimate back
  to the noise mean.
* **Frequency resolution sets notch precision.** At 48 kHz, `n_fft=4096` (11.7 Hz bins)
  puts the measured harmonics on bin centres; `n_fft=2048` cannot and loses ~10 dB of
  notch depth.

## Measured results

Offline (synthetic speech + real recorded fan noise):

| Metric | Result |
| --- | --- |
| Noise suppression (steady state) | **−34.4 dB** |
| ΔSNR @ 30 / 20 / 10 dB | +10.7 / +11.1 / +10.6 dB |
| Harmonic prominence, 363 Hz | 6.4 → 1.7 dB |
| Real-time factor (single thread) | 0.008 |
| Safety | no NaN/Inf, no clipping |

End-to-end (real live capture, through Voicemeeter, measured on the virtual mic):

```
raw mic      −56.7 dBFS
virtual mic  −91.3 dBFS      ->  −34.7 dB
```

## Configuration

| Option | Default | Meaning |
|---|---|---|
| `--n-fft` | 4096 | 4096 = 64 ms latency / 11.7 Hz bins; 2048 = 32 ms; 8192 = 128 ms |
| `--bias` | 2.5 | noise-estimate bias. Higher = stronger suppression (1.5→4.0 moved steady-state suppression from −13 dB to −37 dB with no measurable speech-envelope damage) |
| `--over` | 2.5 | over-subtraction factor |
| `--gate` | 6.0 | noise-gate threshold (frame SNR, dB) |
| `--gate-floor` | −16 | gate gain when fully closed (dB) |
| `--no-gate` | — | disable the gate |

`harmonics` is a list of `(frequency_Hz, half_bandwidth_Hz, depth_dB)` and **must be
re-measured for your machine** — a fan at a different RPM produces peaks elsewhere.

## Limitations

* **Fixed-frequency notches only fit a stable fan speed.** When RPM drifts the harmonics
  move; the adaptive spectral subtraction is what keeps working.
* **No beamforming.** Vendor drivers expose only the processed stereo stream of a mic array
  (the two channels of the reference machine correlate at 0.92).
* **~2 s of convergence** at startup while the sliding-minimum estimate fills.
* **Voicemeeter must be running**; exclusive-mode (ASIO) apps cannot use a virtual mic.
* Windows-only.

## Project layout

```
.
├── denoise/                 engine
│   ├── dsp.py                 DSP chain (pure numpy)
│   ├── live.py                real-time pipeline + A/B recording
│   ├── tests/                 offline objective tests
│   ├── tools/                 measure_noise, vm_setup, vm_restore, verify_e2e, ...
│   └── config.example.json
├── manager/                 one-click start/stop/status UI
├── requirements.txt
└── LICENSE
```

## License

MIT — see [LICENSE](LICENSE).

---

支持 / Support: https://afdian.com/a/AozakiAoko
