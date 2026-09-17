# -*- coding: utf-8 -*-
"""
dsp.py —— 麦克风降噪 DSP 链（纯 numpy，无第三方依赖）

【为什么这样设计】
依据对 mic-noise-test.wav 的实测：
  * 噪声能量 85% 落在 250–1000 Hz，语音频段(300–3400 Hz)占 93.6%
    → 宽带底噪与语音完全重叠，"低切/高通"无效（实测只降 0.1 dB）
  * 存在清晰机械谐波族：52.7 / 158.2 / 210.9 / 263.7 / 363.3 / 421.9 /
    703.1 / 878.9 / 2068.4 / 2173.8 Hz
    → 频域陷波"精确挖掉"，避免通用 AI 降噪为了压窄峰而削薄整段人声
  * 稳态性极好（帧间起伏 4.1 dB）→ 自适应噪声估计能学得很准

【结构】每个 hop 样本处理一次，全向量化，无逐样本 Python 循环：
  输入块(hop) → STFT(N, Hann, 75% 重叠)
    1) 静态掩蔽：低频切除 + 谐波陷波
    2) 自适应噪声估计 N[k]：VAD 门控的连续最小值跟踪
    3) 维纳式过减谱减（功率域，量纲 N[k]/P[k]）+ 帧间/频间增益平滑
    4) 帧级软噪声门（说话间隙静音，连续过渡不突变）
  → ISTFT 重叠相加

【分辨率与延迟】频点间隔 = sr/N；陷波精度直接由它决定。
  N=2048 → 23.4 Hz 频点、32 ms 延迟：谐波刚好落在频点之间，陷波只能到 ~-9 dB
  N=4096 → 11.7 Hz 频点、64 ms 延迟：多数谐波正好落在频点上，陷波可到 -18 dB ★默认
"""

import numpy as np

# 实测得到的谐波表：(中心频率 Hz, 陷波半带宽 Hz, 陷波深度 dB)
DEFAULT_HARMONICS = [
    (52.7,   22.0, -24.0),
    (158.2,  14.0,  -6.0),
    (210.9,  14.0,  -6.0),
    (263.7,  16.0,  -8.0),
    (363.3,  18.0, -20.0),
    (421.9,  18.0, -22.0),
    (703.1,  30.0, -14.0),
    (878.9,  30.0, -14.0),
    (2068.4, 60.0, -12.0),
    (2173.8, 60.0, -10.0),
]

DEFAULT_CFG = dict(
    sr=48000,
    n_fft=4096,
    hop=1024,
    hp_freq=75.0,           # 低频切除起点
    hp_db=-30.0,            # 低频切除深度
    notch_sharp=4.0,        # 陷波剖面陡度（指数，越大越接近方波陷波）
    learn_sec=1.5,          # 学习期：只做静态掩蔽，让噪声估计收敛
    vad_db=6.0,             # 帧能量高于噪声底这么多 dB 就判定为"有语音"，冻结噪声估计
    over_sub=2.5,           # 过减因子（离线扫描：2.5 与 3.0 差异很小，取保守值）
    gain_floor_db=-20.0,    # 谱减增益下限
    gain_smooth=0.55,       # 帧间增益平滑
    alpha_dec=0.7,          # 判决用功率谱的时间平滑系数（必须平滑，否则谱减失效）
    min_window_sec=2.0,     # 噪声估计的滑动最小值窗口（自愈：静音段滑出后自动回到真实底噪）
    noise_bias=2.5,         # 滑动最小值天然低于真实均值，需要偏差补偿（Martin 算法同款思路）
    noise_alpha=0.9,        # （保留）噪声功率谱平滑系数
    noise_rise=1.002,       # 噪声估计每帧最大上升系数（≈1.45x/秒）
    gate_thresh_db=6.0,     # 噪声门阈值（帧信噪比）：离线实测 4→6 dB 可再压 5 dB 且不伤语音
    gate_range_db=6.0,      # 阈值以下多少 dB 内线性过渡到全关
    gate_floor_db=-16.0,    # 门全关时的增益
    gate_attack_ms=6.0,
    gate_release_ms=180.0,
    output_gain_db=0.0,
    harmonics=DEFAULT_HARMONICS,
)


def _smooth3(v):
    """3 点滑动平均（边缘补齐），用于频域平滑。"""
    p = np.pad(v, 1, mode="edge")
    return (p[:-2] + p[1:-1] + p[2:]) / 3.0


class Denoiser:
    """实时降噪处理器：输入 hop 长的块，输出同样长度的块。"""

    def __init__(self, cfg=None):
        c = dict(DEFAULT_CFG)
        if cfg:
            c.update(cfg)
        self.cfg = c
        self.sr = int(c["sr"])
        self.n = int(c["n_fft"])
        self.hop = int(c["hop"])
        if self.hop > self.n:
            raise ValueError("hop 不能大于 n_fft")
        self.win = np.hanning(self.n).astype(np.float64)
        self.freqs = np.fft.rfftfreq(self.n, 1.0 / self.sr)
        self.nbins = len(self.freqs)
        self.ola_norm = self._calc_ola_norm()
        self.static_mask = self._build_static_mask()
        self.learn_frames = max(1, int(self.cfg["learn_sec"] * self.sr / self.hop))
        hop_t = self.hop / self.sr
        self.a_att = float(np.exp(-hop_t / max(1e-4, self.cfg["gate_attack_ms"] / 1000.0)))
        self.a_rel = float(np.exp(-hop_t / max(1e-4, self.cfg["gate_release_ms"] / 1000.0)))
        self.reset()

    # ---------- 初始化辅助 ----------
    def _calc_ola_norm(self):
        """同窗分析+合成的重叠相加归一化系数（Hann 75% 重叠时 = 1.5）。"""
        k = 6
        total = np.zeros(self.n * (k + 2))
        for i in range(k + 2):
            s = i * self.hop
            total[s:s + self.n] += self.win ** 2
        return float(np.mean(total[self.n:self.n + self.hop]))

    def _build_static_mask(self):
        """静态频域掩蔽：低频切除 + 实测谐波陷波。"""
        m = np.ones(self.nbins)
        m[self.freqs < self.cfg["hp_freq"]] = 10.0 ** (self.cfg["hp_db"] / 20.0)
        bin_hz = self.sr / float(self.n)
        sharp = float(self.cfg["notch_sharp"])
        for f0, bw, depth in self.cfg["harmonics"]:
            if f0 >= self.sr / 2.0:
                continue
            d = 10.0 ** (depth / 20.0)
            half = max(bw / 2.0, bin_hz)      # 至少一个频点宽，否则挖不动
            x = np.clip(np.abs(self.freqs - f0) / half, 0.0, 1.0)
            prof = d + (1.0 - d) * (x ** sharp)
            m *= prof
        return m

    def reset(self):
        self.in_buf = np.zeros(self.n)
        self.acc = np.zeros(self.n)
        self.P_dec = None
        self.min_buf = None
        self.min_idx = 0
        self.min_win = max(1, int(self.cfg["min_window_sec"] * self.sr / self.hop))
        self.Nk = None
        self.En = 1e-20
        self.G_prev = np.ones(self.nbins)
        self.g_gate = 1.0
        self.frames = 0
        self.last_frame_snr_db = float("nan")
        self.trace = []

    @property
    def latency_samples(self):
        return self.n - self.hop

    # ---------- 主处理 ----------
    def process(self, block):
        x = np.asarray(block, dtype=np.float64).reshape(-1)
        if x.size != self.hop:
            raise ValueError("期望 %d 个样本，收到 %d" % (self.hop, x.size))

        self.in_buf = np.concatenate((self.in_buf[self.hop:], x))
        X = np.fft.rfft(self.in_buf * self.win)
        mag = np.abs(X)
        P = mag * mag                       # 单帧功率谱（方差极大，不能直接用于判决）
        ad = self.cfg["alpha_dec"]
        self.P_dec = P.copy() if self.P_dec is None else ad * self.P_dec + (1.0 - ad) * P
        Pd = self.P_dec                     # 时间平滑后的功率谱（判决用）
        E = float(Pd.sum())

        # --- 1) 自适应噪声估计（滑动最小值 / minimum statistics）---
        # 关键：不能用"运行最小值"——它一旦被开头的静音钉低就永远回不来，
        # 且会让帧信噪比虚高、把噪声估计自锁死。滑动窗口能自愈。
        if self.min_buf is None:
            self.min_buf = np.tile(Pd, (self.min_win, 1))
            self.Nk = Pd.copy() * self.cfg["noise_bias"]
            self.min_idx = 0
        else:
            self.min_buf[self.min_idx] = Pd
            self.min_idx = (self.min_idx + 1) % self.min_win
            self.Nk = np.min(self.min_buf, axis=0) * self.cfg["noise_bias"]
        self.En = float(self.Nk.sum())
        snr_frame = 10.0 * np.log10((E + 1e-20) / (self.En + 1e-20))

        Nk = _smooth3(self.Nk) if self.nbins >= 3 else self.Nk

        # --- 2) 维纳式过减谱减（功率域：N[k]/P_dec[k]，两者都是平滑量，方差才可比）---
        floor = 10.0 ** (self.cfg["gain_floor_db"] / 20.0)
        ratio_k = Nk / (Pd + 1e-20)
        G_pre = np.clip(1.0 - self.cfg["over_sub"] * ratio_k, floor, 1.0)
        G = G_pre
        self.G_prev = self.cfg["gain_smooth"] * self.G_prev + (1.0 - self.cfg["gain_smooth"]) * G
        G = _smooth3(self.G_prev) if self.nbins >= 3 else self.G_prev

        # --- 3) 帧级软噪声门（连续过渡，避免突变斩音）---
        self.last_frame_snr_db = snr_frame
        if self.frames < self.learn_frames:
            target = 1.0
        else:
            t = (snr_frame - (self.cfg["gate_thresh_db"] - self.cfg["gate_range_db"])) / \
                max(1e-6, self.cfg["gate_range_db"])
            t = float(np.clip(t, 0.0, 1.0))
            target = 10.0 ** ((self.cfg["gate_floor_db"] * (1.0 - t)) / 20.0)
        coef = self.a_att if target < self.g_gate else self.a_rel
        self.g_gate = coef * self.g_gate + (1.0 - coef) * target

        if self.frames < self.learn_frames:
            G = np.ones(self.nbins)         # 学习期内不做谱减

        # --- 4) 合成 ---
        G = G * self.static_mask * self.g_gate
        Y = np.fft.irfft(X * G, n=self.n) * self.win
        self.acc[:self.n] += Y
        out = self.acc[:self.hop] / self.ola_norm
        self.acc = np.concatenate((self.acc[self.hop:], np.zeros(self.hop)))

        self.frames += 1
        if self.cfg["output_gain_db"]:
            out = out * (10.0 ** (self.cfg["output_gain_db"] / 20.0))
        if self.cfg.get("trace"):
            self.trace.append(dict(frame=self.frames - 1, snr_db=snr_frame,
                                   e_db=10 * np.log10(E + 1e-30),
                                   en_db=10 * np.log10(self.En + 1e-30),
                                   ratio_mean=float(np.mean(ratio_k)),
                                   ratio_med=float(np.median(ratio_k)),
                                   g_pre_mean=float(np.mean(G_pre)),
                                   g_post_mean=float(np.mean(G)),
                                   gate=self.g_gate, nk_sum=float(self.Nk.sum())))
        return out


def process_array(den, x):
    """把一整段信号按 hop 切片过链（离线测试用），并补偿算法延迟。"""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    hop = den.hop
    nblk = max(1, int(np.ceil(len(x) / hop)))
    pad = np.zeros(nblk * hop)
    pad[:len(x)] = x
    out = np.empty_like(pad)
    for i in range(nblk):
        out[i * hop:(i + 1) * hop] = den.process(pad[i * hop:(i + 1) * hop])
    # 链输出 out[i] 对应输入 input[i-d]，前移 d 个样本对齐
    d = den.latency_samples
    if d:
        out = np.concatenate((out[d:], np.zeros(d)))
    return out[:len(x)]
