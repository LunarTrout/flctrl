"""incr.py — инкрементальные метрики: те же числа, что у файловых, но по потоку.

Каждый анализатор имеет `feed(x)` (x — кусок моно/стерео float32) и `value()`.
Состояние маленькое: фильтры, буфер окна, накопители. Ничего не хранится, кроме
агрегатов, поэтому один и тот же код работает и на живом потоке, и на файле.

Сверка с файловыми реализациями — `tests/test_incr.py`: rms/peak до 0.01 дБ,
полосы до 0.1 дБ, LUFS до 0.1 LU.
"""
from __future__ import annotations

import numpy as np

# --- третьоктавные полосы: ровно как в perceptual.py -------------------------
FC = np.array([25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500,
               630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300,
               8000, 10000, 12500], float)
NFFT = 4096
HOP_S = 0.05


def band_matrix(fs: int, nfft: int = NFFT):
    """Матрица (28, bins) — доля мощности в третьоктавной полосе (IEC 61260)."""
    freqs = np.fft.rfftfreq(nfft, 1 / fs)
    lo, hi = FC * 10 ** (-1 / 20), FC * 10 ** (1 / 20)
    M = np.zeros((len(FC), len(freqs)))
    for i, (a, b) in enumerate(zip(lo, hi)):
        m = (freqs >= a) & (freqs < b)
        if not m.any():
            m[np.argmin(np.abs(freqs - FC[i]))] = True
        M[i, m] = 1.0
    return M


class RmsMeter:
    """rms / peak / crest за всё, что скормили. Точность — по построению."""

    def __init__(self, fs: int = 44100):
        self.fs = fs
        self.n = 0
        self.sum = 0.0
        self.sumsq = 0.0
        self.peak = 0.0

    def feed(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64).ravel()      # все каналы, как в файловой
        if x.size == 0:
            return
        self.n += x.size
        self.sum += float(x.sum())
        self.sumsq += float((x * x).sum())
        self.peak = max(self.peak, float(np.abs(x).max()))

    def value(self) -> dict:
        rms = (self.sumsq / self.n) ** 0.5 if self.n else 0.0
        peak = self.peak
        return {"rms": rms, "peak": peak,
                "rms_db": 20 * np.log10(max(rms, 1e-12)),
                "peak_db": 20 * np.log10(max(peak, 1e-12)),
                "crest_db": 20 * np.log10(max(peak, 1e-12) / max(rms, 1e-12)),
                "samples": self.n}


class BandMeter:
    """28 третьоктавных полос, шаг 50 мс, окно 4096 — как perceptual.band_levels."""

    def __init__(self, fs: int = 44100, nfft: int = NFFT, hop_s: float = HOP_S):
        self.fs = fs
        self.nfft = nfft
        self.hop = max(1, int(round(hop_s * fs)))
        self.win = np.hanning(nfft)
        self.wcorr = (self.win.sum() / 2.0) ** 2
        self.M = band_matrix(fs, nfft)
        self.buf = np.zeros((0, 1), dtype=np.float64)
        self.pos = 0
        self.rows: list[np.ndarray] = []

    def feed(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        if x.size:
            if self.buf.shape[1] != x.shape[1]:
                self.buf = np.zeros((0, x.shape[1]), dtype=np.float64)
            self.buf = np.concatenate([self.buf, x], axis=0)
        while len(self.buf) - self.pos >= self.nfft:
            frame = self.buf[self.pos:self.pos + self.nfft] * self.win[:, None]
            spec = np.fft.rfft(frame, axis=0)
            ms = (np.abs(spec) ** 2).sum(axis=1) * 2.0 / self.wcorr
            p2 = self.M @ ms
            self.rows.append(10 * np.log10(np.maximum(p2, 1e-30) / (20e-6) ** 2))
            self.pos += self.hop
        if self.pos > self.nfft:
            self.buf = self.buf[self.pos:]
            self.pos = 0

    def value(self) -> dict:
        if not self.rows:
            return {"bands": None, "frames": 0}
        avg = np.mean(np.stack(self.rows), axis=0)
        return {"bands": [round(float(v), 2) for v in avg],
                "frames": len(self.rows),
                "fc": [float(v) for v in FC]}


class StereoMeter:
    """Ширина/корреляция/side-mid — из накопителей, без хранения звука."""

    def __init__(self):
        self.n = 0
        self.eL = self.eR = self.eM = self.eS = self.eLR = 0.0

    def feed(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] < 2:
            return
        L, R = x[:, 0], x[:, 1]
        mid, side = (L + R) / 2, (L - R) / 2
        self.n += L.size
        self.eL += float((L * L).sum())
        self.eR += float((R * R).sum())
        self.eM += float((mid * mid).sum())
        self.eS += float((side * side).sum())
        self.eLR += float((L * R).sum())

    def value(self) -> dict:
        if not self.n:
            return {}
        eL, eR, eM, eS, eLR = (self.eL / self.n, self.eR / self.n, self.eM / self.n,
                               self.eS / self.n, self.eLR / self.n)
        denom = (eL * eR) ** 0.5
        return {"corr": round(float(eLR / denom), 4) if denom else 0.0,
                "side_mid_db": round(float(10 * np.log10(max(eS, 1e-15) / max(eM, 1e-15))), 2),
                "width": round(float((eS / (eM + eS)) ** 0.5), 4) if (eM + eS) else 0.0}


class LoudnessMeter:
    """LUFS (BS.1770): K-взвешивание IIR + блоки 400 мс с перекрытием 75% + гейты.

    Коэффициенты берутся у pyloudnorm, чтобы потоковое значение совпало с
    файловым (perceptual.lufs) в пределах 0.1 LU.
    """

    def __init__(self, fs: int = 44100, channels: int = 2):
        import pyloudnorm as pyln
        m = pyln.Meter(fs)
        f = m._filters
        self.shelf = (np.array(f["high_shelf"].b, float), np.array(f["high_shelf"].a, float))
        self.hp = (np.array(f["high_pass"].b, float), np.array(f["high_pass"].a, float))
        self.channels = channels
        self.zi_shelf = [np.zeros(max(len(self.shelf[0]), len(self.shelf[1])) - 1)
                         for _ in range(channels)]
        self.zi_hp = [np.zeros(max(len(self.hp[0]), len(self.hp[1])) - 1)
                      for _ in range(channels)]
        self.block = int(round(0.4 * fs))
        self.step = int(round(0.1 * fs))
        self.buf = np.zeros((0, channels), dtype=np.float64)
        self.blocks: list[float] = []

    def feed(self, x: np.ndarray) -> None:
        import scipy.signal as ss
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 1:
            x = x[:, None]
        if x.shape[1] < self.channels:
            x = np.repeat(x[:, :1], self.channels, axis=1)
        x = x[:, :self.channels]

        out = np.empty_like(x)
        for ch in range(self.channels):
            y, self.zi_shelf[ch] = ss.lfilter(*self.shelf, x[:, ch], zi=self.zi_shelf[ch])
            y, self.zi_hp[ch] = ss.lfilter(*self.hp, y, zi=self.zi_hp[ch])
            out[:, ch] = y

        self.buf = np.concatenate([self.buf, out], axis=0)
        while len(self.buf) >= self.block:
            seg = self.buf[:self.block]
            ms = (seg ** 2).mean(axis=0)                 # по каналам
            self.blocks.append(float(-0.691 + 10 * np.log10(max(ms.sum(), 1e-20))))
            self.buf = self.buf[self.step:]
        self.buf = self.buf[-self.block:]                # не копим больше окна

    def value(self) -> dict:
        if not self.blocks:
            return {"lufs": None, "blocks": 0}
        b = np.array(self.blocks)
        above_abs = b[b > -70.0]
        if not above_abs.size:
            return {"lufs": -70.0, "blocks": len(b)}
        rel = float(above_abs.mean() - 10.0)
        gated = above_abs[above_abs > rel]
        lufs = float(gated.mean()) if gated.size else float(above_abs.mean())
        return {"lufs": round(lufs, 2), "blocks": len(b),
                "gated": int(gated.size)}


class TransientMeter:
    """Удары/хвосты в потоке по тем же правилам, что transients.analyse.

    Звук не храним: держим спектральный поток (число на кадр 1024/256) и
    огибающие трёх полос в кольце 0.30 с. Индексация абсолютная (номер сэмпла),
    иначе кольцо съезжает и удары считаются не там.
    """

    FRAME = 1024
    HOP = 256
    BANDS = (("low", 20.0, 200.0), ("mid", 200.0, 2000.0), ("high", 2000.0, 16000.0))
    KEEP_S = 0.35

    def __init__(self, fs: int = 44100):
        import scipy.signal as ss
        self.fs = fs
        self.win = np.hanning(self.FRAME)
        self.buf = np.zeros(0, dtype=np.float64)
        self.buf_start = 0                 # абсолютный сэмпл первого элемента buf
        self.prev_mag = None
        self.flux: list[float] = []
        self.n_samples = 0                 # сколько всего скормлено
        ny = fs / 2.0
        self.sos, self.zi, self.env, self.env_start = {}, {}, {}, {}
        for name, lo, hi in self.BANDS:
            hi2 = min(hi, ny * 0.95)
            self.sos[name] = ss.butter(4, [max(lo, 10.0) / ny, hi2 / ny],
                                       btype="band", output="sos")
            self.zi[name] = np.zeros((self.sos[name].shape[0], 2))
            self.env[name] = np.zeros(0, dtype=np.float64)
            self.env_start[name] = 0
        self.keep = int(fs * self.KEEP_S)
        self.hits: list[dict] = []

    # ------------------------------------------------------------------ вход
    def feed(self, x: np.ndarray) -> None:
        import scipy.signal as ss
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1)
        if x.size == 0:
            return

        for name, _lo, _hi in self.BANDS:
            y, self.zi[name] = ss.sosfilt(self.sos[name], x, zi=self.zi[name])
            env = np.concatenate([self.env[name], np.abs(y)])
            if env.size > self.keep:
                drop = env.size - self.keep
                env = env[drop:]
                self.env_start[name] += drop
            self.env[name] = env

        self.buf = np.concatenate([self.buf, x])
        while self.buf_start + len(self.buf) >= self.buf_start + self.FRAME and                 len(self.buf) >= self.FRAME:
            seg = self.buf[:self.FRAME] * self.win
            mag = np.abs(np.fft.rfft(seg))
            if self.prev_mag is not None:
                step = float(np.maximum(0.0, np.log1p(10.0 * mag)
                                        - np.log1p(10.0 * self.prev_mag)).sum())
                self.flux.append(step)
                self._maybe_onset(len(self.flux) - 1)
            self.prev_mag = mag
            self.buf = self.buf[self.HOP:]
            self.buf_start += self.HOP
        self.n_samples += x.size
        for hit in self.hits:               # хвост доехал — меряем сразу
            if "bands" not in hit and not hit.get("dropped"):
                self._measure(hit)

    # -------------------------------------------------------------- детекция
    def _smooth_flux(self) -> np.ndarray:
        f = np.asarray(self.flux, dtype=np.float64)
        if f.size < 3:
            return f
        return np.convolve(f, np.ones(3) / 3.0, mode="same")

    def _maybe_onset(self, idx: int) -> None:
        # сглаживание у правого края усечено, поэтому судим по кадру на один назад
        fl = self._smooth_flux()
        j = idx - 1
        if fl.size < 16 or j < 1 or j + 1 >= fl.size:
            return
        floor = max(0.05 * float(np.percentile(fl, 99.5)), float(np.median(fl)))
        v = fl[j]
        if v < floor or v < fl[j - 1] or v < fl[j + 1]:
            return
        win = max(3, int(self.fs / self.HOP * 0.4))
        loc = fl[max(0, j - win):j + win]
        if v < 1.15 * float(np.median(loc)):
            return
        gap = max(1, int(0.04 * self.fs / self.HOP))
        if self.hits and j - self.hits[-1]["idx"] < gap:
            return
        self.hits.append({"idx": j, "sample": (j + 1) * self.HOP})
        self._measure(self.hits[-1])

    def _measure(self, hit: dict) -> None:
        pre = int(self.fs * 0.02)
        short = int(self.fs * 0.02)
        long_ = int(self.fs * 0.20)
        s = hit["sample"]
        for name, _lo, _hi in self.BANDS:
            start = s - pre
            end = s + long_
            a0 = self.env_start[name]
            a1 = a0 + len(self.env[name])
            if end > a1:
                return                      # хвост ещё не пришёл — досчитаем позже
            if start < a0:
                hit["dropped"] = True       # окно уже уехало из кольца
                return
        hit["bands"] = {}
        for name, _lo, _hi in self.BANDS:
            a0 = self.env_start[name]
            seg = self.env[name][s - pre - a0:s + long_ - a0]
            if seg.size == 0 or seg.max() <= 0:
                continue
            peak_i = int(np.argmax(seg))
            target = 0.9 * seg[peak_i]
            reach = int(np.argmax(seg[:peak_i + 1] >= target)) if peak_i >= 0 else 0
            a = float(self.env[name][s - a0:s + short - a0].mean()) + 1e-12
            b = float(self.env[name][s + short - a0:s + short + long_ - a0].mean()) + 1e-12
            hit["bands"][name] = {"attack_ms": round(1000.0 * reach / self.fs, 2),
                                  "ts_db": round(float(10 * np.log10(a / b)), 2),
                                  "ok": True}

    def value(self) -> dict:
        for hit in self.hits:                 # досчитать отложенные хвосты
            if "bands" not in hit and not hit.get("dropped"):
                self._measure(hit)
        done = [h for h in self.hits if h.get("bands")]
        bands = {}
        for name, _lo, _hi in self.BANDS:
            att = [h["bands"][name]["attack_ms"] for h in done if name in h["bands"]]
            rts = [h["bands"][name]["ts_db"] for h in done if name in h["bands"]]
            bands[name] = {"attack_ms": round(float(np.median(att)), 2) if att else None,
                           "ts_db": round(float(np.median(rts)), 2) if rts else None,
                           "n": len(rts)}
        dur = self.n_samples / self.fs
        return {"onsets": len(self.hits),
                "onsets_s": round(len(self.hits) / dur, 2) if dur else 0.0,
                "flux_frames": len(self.flux), "bands": bands}
