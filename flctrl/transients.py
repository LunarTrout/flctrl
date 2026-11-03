"""transients.py — транзиенты: атака, плотность ударов, «удар/хвост».

Зачем это в системе слуха. Спектр и громкость описывают, ЧТО звучит, но не
описывают, НАСКОЛЬКО удар читается. Для блэка это главное отличие живой
установки от каши: у референсов бочка и тарелки имеют короткую атаку и высокое
отношение «удар/хвост», у пережатого микса транзиенты сглажены.

Три числа на полосу:
  onsets_s   — сколько РАЗЛИЧИМЫХ ударов в секунду: величина сравнительная,
               а не «сколько нот сыграно»; в плотном тремоло удары сливаются
  attack_ms  — медианное время от начала удара до 90 % локального пика
  ts_ratio   — энергия первых 20 мс после удара к энергии следующих 200 мс, дБ

Полосы: низ (<200 Гц), середина (200–2000), верх (>2000).
"""
from __future__ import annotations

import numpy as np

BANDS = (("low", 20.0, 200.0), ("mid", 200.0, 2000.0), ("high", 2000.0, 16000.0))

FRAME = 1024
HOP = 256


def _load(path: str):
    import soundfile as sf

    x, fs = sf.read(path, dtype="float64", always_2d=True)
    if x.shape[1] > 1:
        x = x.mean(axis=1)
    else:
        x = x[:, 0]
    return x, int(fs)


def _stft(x: np.ndarray, n: int = FRAME, hop: int = HOP):
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    win = np.hanning(n)
    frames = 1 + (len(x) - n) // hop
    out = np.empty((frames, n // 2 + 1), dtype=np.complex128)
    for i in range(frames):
        seg = x[i * hop:i * hop + n] * win
        out[i] = np.fft.rfft(seg)
    return out


def _onsets(mag: np.ndarray, fps: float, rel: float = 0.05, min_gap_s: float = 0.04,
            local: float = 1.35):
    """Пики спектрального потока с двумя порогами: абсолютный и локальный.

    Один локальный порог ловит шум ровного тона (фаза окна гуляет от кадра к
    кадру). Поэтому удар должен быть и заметным на фоне всего файла
    (rel от 99.5-го процентиля), и выше локальной медианы вдвое.
    """
    step = np.maximum(0.0, np.diff(np.log1p(10.0 * mag), axis=0)).sum(axis=1)
    if not len(step):
        return np.array([], dtype=int), step
    k = 3
    flux = np.convolve(step, np.ones(k) / k, mode="same")
    # три порога: относительный (не ловить тишину), глобальный (не ловить ровный
    # тон, где поток равномерный) и локальный (не ловить общий подъём).
    floor = max(rel * float(np.percentile(flux, 99.5) or 0.0),
                float(np.median(flux) or 0.0))
    win = max(3, int(fps * 0.4))
    picked = []
    gap = max(1, int(min_gap_s * fps))
    for i in range(1, len(flux) - 1):
        if flux[i] < floor or flux[i] < flux[i - 1] or flux[i] < flux[i + 1]:
            continue
        loc = flux[max(0, i - win):i + win]
        if flux[i] < local * float(np.median(loc)):
            continue
        if picked and i - picked[-1] < gap:
            if flux[i] > flux[picked[-1]]:
                picked[-1] = i
            continue
        picked.append(int(i))
    return np.array(picked, dtype=int), flux


def _band_signal(x: np.ndarray, fs: int, lo: float, hi: float) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    ny = fs / 2.0
    hi = min(hi, ny * 0.95)
    sos = butter(4, [max(lo, 10.0) / ny, hi / ny], btype="band", output="sos")
    return sosfiltfilt(sos, x)


def analyse(path: str, max_seconds: float = 60.0) -> dict:
    """Транзиентная картина файла: по полосам и общая."""
    x, fs = _load(path)
    if max_seconds and len(x) > fs * max_seconds:
        x = x[:int(fs * max_seconds)]
    if not len(x) or np.max(np.abs(x)) < 1e-9:
        return {"ok": False, "error": "тишина"}

    mag = np.abs(_stft(x))
    fps = fs / HOP
    peaks, flux = _onsets(mag, fps)
    dur = len(x) / fs
    out = {"ok": True, "seconds": round(dur, 2),
           "onsets_s": round(len(peaks) / dur, 2) if dur else 0.0,
           "flux_mean": float(np.mean(flux)) if len(flux) else 0.0,
           "bands": {}}

    for name, lo, hi in BANDS:
        y = _band_signal(x, fs, lo, hi)
        env = np.abs(y)
        # сглаживание огибающей ~5 мс
        k = max(1, int(fs * 0.005))
        env = np.convolve(env, np.ones(k) / k, mode="same")
        attacks, ratios = [], []
        pre = int(fs * 0.02)
        short = int(fs * 0.02)
        long = int(fs * 0.20)
        for p in peaks:
            i = int(p * HOP)
            if i + pre + long >= len(env) or i < pre:
                continue
            seg = env[i:i + pre + long]
            if seg.max() <= 0:
                continue
            peak_i = int(np.argmax(seg))
            target = 0.9 * seg[peak_i]
            reach = np.argmax(seg[:peak_i + 1] >= target) if peak_i >= 0 else 0
            attacks.append(1000.0 * reach / fs)
            a = env[i:i + short].mean() + 1e-12
            b = env[i + short:i + short + long].mean() + 1e-12
            ratios.append(10 * np.log10(a / b))
        out["bands"][name] = {
            "attack_ms": round(float(np.median(attacks)), 2) if attacks else None,
            "ts_db": round(float(np.median(ratios)), 2) if ratios else None,
            "n": len(ratios),
        }
    return out


def compare(mine: dict, refs: list[dict]) -> dict:
    """Сравнение с референсами: где транзиенты сглажены, где наоборот жёстче."""
    if not mine.get("ok"):
        return {"ok": False, "error": mine.get("error", "нет замера")}
    rows = []
    for name, _lo, _hi in BANDS:
        m = (mine.get("bands") or {}).get(name) or {}
        vals = [(r.get("bands") or {}).get(name, {}).get("ts_db")
                for r in refs if r.get("ok")]
        vals = [v for v in vals if v is not None]
        if m.get("ts_db") is None or not vals:
            rows.append({"band": name, "mine": m.get("ts_db"), "ref": None, "delta": None})
            continue
        ref = float(np.median(vals))
        rows.append({"band": name, "mine": m["ts_db"], "ref": round(ref, 2),
                     "delta": round(m["ts_db"] - ref, 2),
                     "attack_ms": m.get("attack_ms")})
    verdict = []
    for r in rows:
        if r["delta"] is None:
            continue
        if r["delta"] < -1.5:
            verdict.append("%s: транзиенты сглажены на %.1f дБ — вернуть атаку"
                           % (r["band"], -r["delta"]))
        elif r["delta"] > 2.5:
            verdict.append("%s: удары жёстче референсов на %.1f дБ — проверить, не щёлкает ли"
                           % (r["band"], r["delta"]))
    return {"ok": True, "rows": rows, "verdict": verdict,
            "onsets_s": mine.get("onsets_s"),
            "ref_onsets_s": round(float(np.median([r["onsets_s"] for r in refs
                                                   if r.get("ok")])), 2) if refs else None}
