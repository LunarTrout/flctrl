"""timbre.py — что за звук: окрас, техника, высота, панорама.

Одна «клетка» (отрезок ~0.1 с) описывается полным набором признаков, по которым
можно судить не только «сколько энергии», но и «что это за звук»:

    громкость   rms_db, peak, crest_db, attack_s, decay_db, flux_db
    спектр      28 полос, centroid, spread, flatness, rolloff85, slope
    тон         f0, hnr_db (гармоничность), chroma[12], polyphony, inharmonicity
    шум         noisiness, zcr
    стерео      corr, width, pan
    техника     метки: palm_mute, tremolo, pinch, scream, kick, snare, hat, pad

Метки — эвристики на признаках (не обученная модель), они объяснимы: видно,
из чего сделан вывод.
"""
from __future__ import annotations

import numpy as np

try:
    import incr
except ImportError:                      # как пакет
    from . import incr

FC = np.asarray(incr.FC, dtype=float)
# границы третьоктавных полос
LO = FC * 10 ** (-1 / 20)
HI = FC * 10 ** (1 / 20)

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _bands_matrix(nfft: int, fs: int) -> np.ndarray:
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    M = np.zeros((len(FC), len(freqs)))
    for i in range(len(FC)):
        m = (freqs >= LO[i]) & (freqs < HI[i])
        if not m.any():
            m[np.argmin(np.abs(freqs - FC[i]))] = True
        M[i] = m
    return M


def _active(x: np.ndarray, n: int = 2048) -> np.ndarray:
    """Самый громкий кусок клетки: там и живёт тон, а не в хвосте затухания."""
    if x.size <= n:
        return x
    e = np.convolve(np.abs(x), np.ones(n) / n, mode="same")
    i = int(np.argmax(e))
    s = max(0, min(i - n // 2, x.size - n))
    return x[s:s + n]


def _f0_and_hnr(x: np.ndarray, fs: int, fmin: float = 40.0, fmax: float = 1200.0):
    """Основной тон и гармоничность по автокорреляции (через FFT)."""
    x = x - x.mean()
    if x.size < 256 or np.max(np.abs(x)) < 1e-6:
        return 0.0, 0.0
    n = 1 << int(np.ceil(np.log2(2 * x.size)))
    spec = np.fft.rfft(x, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[:x.size]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lo = max(1, int(fs / fmax))
    hi = min(x.size - 2, int(fs / fmin))
    if hi <= lo:
        return 0.0, 0.0
    lag = lo + int(np.argmax(ac[lo:hi]))
    r = float(ac[lag])
    # параболическая подгонка вершины
    if 0 < lag < x.size - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = (a - 2 * b + c)
        if abs(denom) > 1e-12:
            lag = lag + 0.5 * (a - c) / denom
    f0 = fs / lag if lag > 0 else 0.0
    hnr = 10 * np.log10(max(r, 1e-6) / max(1 - r, 1e-6))
    return float(f0), float(max(-20.0, min(40.0, hnr)))


def _chroma(freqs: np.ndarray, spec: np.ndarray) -> list:
    chr_ = np.zeros(12)
    m = (freqs >= 55) & (freqs <= 2000)
    if not m.any():
        return [0.0] * 12
    f = freqs[m]
    p = spec[m]
    pc = np.mod(np.round(12 * np.log2(f / 440.0) + 9).astype(int), 12)
    for i, c in enumerate(pc):
        chr_[c] += p[i]
    total = chr_.sum()
    if total <= 0:
        return [0.0] * 12
    chr_ = chr_ / total
    return [round(float(v), 3) for v in chr_]


def _peaks(spec, freqs, lo=200.0, hi=4000.0, n=3):
    """Формантоподобные пики: сглаженный спектр, локальные максимумы."""
    m = (freqs >= lo) & (freqs <= hi)
    if not m.any():
        return []
    f = freqs[m]
    p = np.log(spec[m] + 1e-20)
    k = max(3, len(p) // 60)
    smooth = np.convolve(p, np.ones(k) / k, mode="same")
    idx = [i for i in range(2, len(smooth) - 2)
           if smooth[i] > smooth[i - 1] and smooth[i] >= smooth[i + 1]]
    idx.sort(key=lambda i: smooth[i], reverse=True)
    out = []
    for i in idx[:n]:
        out.append(round(float(f[i]), 0))
    return sorted(out)


def _f0_hps(spec, freqs, fmin=45.0, fmax=1000.0):
    """Запасной путь: гармоническое произведение спектра (для коротких нот)."""
    m = (freqs >= fmin) & (freqs <= fmax)
    if not m.any():
        return 0.0
    f = freqs[m]
    p = spec[m] + 1e-20
    score = np.ones_like(p)
    for k in (1, 2, 3, 4):
        idx = np.searchsorted(freqs, f * k)
        idx = np.clip(idx, 0, len(spec) - 1)
        score *= spec[idx] + 1e-20
    return float(f[int(np.argmax(score))])


def _harmonics(spec, freqs, f0):
    """Сила гармоник 2f0…6f0 относительно f0 и доля негармонической энергии."""
    if f0 <= 20:
        return [], 0.0
    def band_power(fc, width=0.06):
        m = (freqs >= fc * (1 - width)) & (freqs <= fc * (1 + width))
        return float(spec[m].sum()) if m.any() else 0.0
    fund = band_power(f0)
    if fund <= 0:
        return [], 0.0
    levels = []
    tot_harm = fund
    for k in range(2, 7):
        p = band_power(f0 * k)
        levels.append(round(float(10 * np.log10((p + 1e-20) / (fund + 1e-20))), 1))
        tot_harm += p
    total = float(spec.sum()) + 1e-20
    inharm = float(1.0 - tot_harm / total)
    return levels, round(max(0.0, min(1.0, inharm)), 3)


def _band_decay(seg_l, seg_r, fs, nquart=4):
    """Спад энергии в low/mid/high внутри клетки, дБ/с."""
    mid = (seg_l + seg_r) / 2.0
    parts = np.array_split(mid, nquart)
    if any(len(p) < 32 for p in parts):
        return [0.0, 0.0, 0.0]
    n = min(len(p) for p in parts)
    edges = [(20, 200), (200, 2000), (2000, 16000)]
    vals = []
    for lo, hi in edges:
        e = []
        for p in parts:
            sp = np.abs(np.fft.rfft(p[:n] * np.hanning(n))) ** 2
            f = np.fft.rfftfreq(n, 1.0 / fs)
            m = (f >= lo) & (f < hi)
            e.append(float(sp[m].sum()) + 1e-20)
        db = 10 * np.log10(np.array(e))
        t = np.arange(len(db)) * (n / fs)
        slope = np.polyfit(t, db, 1)[0] if len(db) > 1 else 0.0
        vals.append(round(float(slope), 0))
    return vals


def _coherence(seg_l, seg_r, fs, edges=((20, 200), (200, 2000), (2000, 16000))):
    """Когерентность L/R по трём полосам: 1 — моно, 0 — независимо."""
    n = min(len(seg_l), len(seg_r))
    if n < 512:
        return [0.0, 0.0, 0.0]
    parts_l = np.array_split(seg_l[:n], 4)
    parts_r = np.array_split(seg_r[:n], 4)
    out = []
    f = np.fft.rfftfreq(len(parts_l[0]), 1.0 / fs)
    for lo, hi in edges:
        m = (f >= lo) & (f < hi)
        if not m.any():
            out.append(0.0)
            continue
        sxy = 0j
        sxx = 0.0
        syy = 0.0
        for pl, pr in zip(parts_l, parts_r):
            L = np.fft.rfft(pl * np.hanning(len(pl)))
            R = np.fft.rfft(pr * np.hanning(len(pr)))
            sxy += (L[m] * np.conj(R[m])).sum()
            sxx += float((np.abs(L[m]) ** 2).sum())
            syy += float((np.abs(R[m]) ** 2).sum())
        denom = sxx * syy
        out.append(round(float(abs(sxy) ** 2 / denom) if denom > 0 else 0.0, 3))
    return out


def _modulation(x, fs: int) -> dict:
    """Биения/тремоло: доминирующая частота огибающей 2–12 Гц и её глубина."""
    x = np.asarray(x, dtype=float)
    if x.ndim == 2:
        x = x.mean(axis=1)
    hop = max(1, int(0.005 * fs))
    win = max(32, int(0.02 * fs))
    env = np.array([float(np.sqrt(np.mean(x[i:i + win] ** 2)))
                    for i in range(0, max(1, len(x) - win), hop)])
    if env.size < 16 or env.max() <= 0:
        return {"rate_hz": 0.0, "depth": 0.0}
    env = env - env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(env.size)))
    freqs = np.fft.rfftfreq(env.size, hop / fs)
    m = (freqs >= 2) & (freqs <= 12)
    if not m.any():
        return {"rate_hz": 0.0, "depth": 0.0}
    i = int(np.argmax(spec[m]))
    rate = float(freqs[m][i])
    band = spec[m]
    depth = float(band[i] / (band.sum() + 1e-12))
    return {"rate_hz": round(rate, 2), "depth": round(depth, 3)}


def analyze_cell(seg_l: np.ndarray, seg_r: np.ndarray, fs: int = 44100,
                 prev_rms_db: float | None = None, M: np.ndarray | None = None,
                 nfft: int | None = None) -> dict:
    """Все признаки одного отрезка (клетки ленты)."""
    nfft = nfft or len(seg_l)
    if M is None:
        M = _bands_matrix(nfft, fs)
    win = np.hanning(nfft)
    mid = (seg_l + seg_r) / 2.0
    if len(mid) < nfft:
        pad = nfft - len(mid)
        mid = np.pad(mid, (0, pad))
        seg_l = np.pad(seg_l, (0, pad))
        seg_r = np.pad(seg_r, (0, pad))

    spec = np.abs(np.fft.rfft(mid * win)) ** 2
    spec *= 2.0 / (np.sum(win ** 2) * nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    power = M @ spec
    bands = [round(float(10 * np.log10(p + 1e-20)), 1) for p in power]

    rms = float(np.sqrt(np.mean(mid ** 2)))
    rms_db = 20 * np.log10(rms + 1e-12)
    peak = float(np.max(np.abs(mid)))
    crest_db = 20 * np.log10((peak + 1e-12) / (rms + 1e-12))

    zcr = float(np.mean(np.abs(np.diff(np.sign(mid))) > 0))

    env = np.abs(mid)
    if env.size and env.max() > 1e-6:
        ipk = int(np.argmax(env))
        thr10, thr90 = 0.1 * env[ipk], 0.9 * env[ipk]
        i10 = int(np.argmax(env >= thr10))
        i90 = int(np.argmax(env >= thr90))
        attack_s = round(float(max(0, i90 - i10)) / fs, 4)
        tail = float(np.sqrt(np.mean(mid[ipk:] ** 2))) if ipk < mid.size else 0.0
        decay_db = round(float(20 * np.log10((peak + 1e-12) / (tail + 1e-12))), 1)
    else:
        attack_s, decay_db = 0.0, 0.0

    flux_db = round(float(rms_db - prev_rms_db), 1) if prev_rms_db is not None else 0.0

    psum = float(spec.sum()) + 1e-20
    centroid = float((freqs * spec).sum() / psum)
    spread = float(np.sqrt(((freqs - centroid) ** 2 * spec).sum() / psum))
    flatness = float(np.exp(np.mean(np.log(spec + 1e-20))) / (np.mean(spec) + 1e-20))
    cum = np.cumsum(spec)
    rolloff = float(freqs[int(np.searchsorted(cum, 0.85 * cum[-1]))])

    f0, hnr = _f0_and_hnr(_active(mid), fs)
    if hnr < 0.0 or f0 <= 0:                 # автокорреляция не уверена — берём спектр
        alt = _f0_hps(spec, freqs)
        if alt > 0:
            f0 = alt
    chroma = _chroma(freqs, spec)
    harm, inharm = _harmonics(spec, freqs, f0)
    formants = _peaks(spec, freqs) if rms_db > -55 else []

    hits = 0
    energy = np.abs(mid)
    if energy.size > 128 and energy.max() > 1e-5:
        k = max(3, int(0.003 * fs))
        e1 = np.convolve(energy, np.ones(k) / k, mode="same")
        thr = 0.5 * e1.max()
        idx = [i for i in range(1, e1.size - 1)
               if e1[i] > thr and e1[i] >= e1[i - 1] and e1[i] > e1[i + 1]]
        merged = []
        for i in idx:
            if not merged or i - merged[-1] > 0.008 * fs:
                merged.append(i)
        if len(merged) >= 3:
            gaps = np.diff(merged)
            if gaps.mean() > 0 and gaps.std() / gaps.mean() < 0.5:
                rate = fs / gaps.mean()
                if 6 <= rate <= 40:
                    hits = len(merged)

    a, b = seg_l - seg_l.mean(), seg_r - seg_r.mean()
    denom = (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)
    corr = float((a * b).sum() / denom)
    side = (seg_l - seg_r) / 2.0
    width = float(np.sqrt(np.mean(side ** 2)) / (rms + 1e-12))
    el = float(np.sqrt(np.mean(seg_l ** 2)))
    er = float(np.sqrt(np.mean(seg_r ** 2)))
    pan = float((er - el) / (er + el + 1e-12))

    low = float(10 * np.log10(float(spec[(freqs >= 20) & (freqs < 200)].sum()) + 1e-20))
    midb = float(10 * np.log10(float(spec[(freqs >= 200) & (freqs < 2000)].sum()) + 1e-20))
    high = float(10 * np.log10(float(spec[(freqs >= 2000) & (freqs < 16000)].sum()) + 1e-20))

    cell = {
        "rms_db": round(rms_db, 1), "peak": round(peak, 4), "crest_db": round(crest_db, 1),
        "attack_s": attack_s, "decay_db": decay_db, "flux_db": flux_db, "zcr": round(zcr, 3),
        "bands": bands,
        "centroid_hz": round(centroid, 1), "spread_hz": round(spread, 1),
        "flatness": round(flatness, 4), "rolloff_hz": round(rolloff, 1),
        "f0_hz": round(f0, 1),
        "note": (PITCH_NAMES[int(np.argmax(chroma))] if f0 > 0 else ""),
        "hnr_db": round(hnr, 1), "chroma": chroma,
        "harm": harm, "inharm": inharm, "formants": formants,
        "decay_bands": _band_decay(seg_l, seg_r, fs),
        "coherence": _coherence(seg_l, seg_r, fs),
        "corr": round(corr, 3), "width": round(width, 3), "pan": round(pan, 3),
        "low_db": round(low, 1), "mid_db": round(midb, 1), "high_db": round(high, 1),
        "hits": hits,
    }
    return cell


def technique(stereo: np.ndarray, fs: int = 44100, cell_s: float = 0.1) -> dict:
    """Отдельный проход 10 мс: видит тремоло/дробь, которые не влезают в клетку."""
    x = np.asarray(stereo, dtype=float)
    if x.ndim == 2:
        x = x.mean(axis=1)
    hop = max(1, int(0.005 * fs))
    win = max(64, int(0.01 * fs))
    env = []
    for start in range(0, max(0, len(x) - win), hop):
        seg = x[start:start + win]
        env.append(float(np.sqrt(np.mean(seg ** 2))))
    env = np.asarray(env)
    if env.size < 8:
        return {"per_cell": [], "rate_hz": 0.0}
    d = np.diff(env, prepend=env[0])
    thr = 0.4 * (d.max() + 1e-12)
    peaks = [i for i in range(1, len(d) - 1)
             if d[i] > thr and d[i] >= d[i - 1] and d[i] > d[i + 1]]
    merged = []
    min_gap = int(0.025 / 0.005)
    for i in peaks:
        if not merged or i - merged[-1] > min_gap:
            merged.append(i)
    rate = 0.0
    if len(merged) >= 3:
        gaps = np.diff(merged) * 0.005
        if gaps.mean() > 0 and gaps.std() / gaps.mean() < 0.45:
            rate = float(1.0 / gaps.mean())
    per_cell = []
    if merged:
        times = np.array(merged) * 0.005
        idx = (times / cell_s).astype(int)
        per_cell = sorted(set(int(i) for i in idx))
    return {"per_cell": per_cell, "rate_hz": round(rate, 2),
            "fast": bool(6.0 <= rate <= 40.0)}


def analyze(stereo: np.ndarray, fs: int = 44100, hop_s: float = 0.1,
            nfft: int = 4096) -> dict:
    """Лента широких признаков: на каждый отрезок — полный набор."""
    x = np.asarray(stereo, dtype=float)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    left, right = x[:, 0], x[:, 1]
    hop = max(1, int(round(hop_s * fs)))
    M = _bands_matrix(nfft, fs)

    cells = []
    times = []
    prev = None
    for start in range(0, max(0, len(left) - hop // 2), hop):
        cell = analyze_cell(left[start:start + nfft], right[start:start + nfft], fs,
                            prev_rms_db=prev, M=M, nfft=nfft)
        prev = cell["rms_db"]
        cells.append(cell)
        times.append(round(start / fs, 3))

    # производные по ленте: темп онсетов и «тремоло»
    flux = np.array([c["flux_db"] for c in cells])
    onsets = [i for i in range(1, len(flux)) if flux[i] > 6.0 and flux[i] >= flux[i - 1]]
    rate = 0.0
    if len(onsets) >= 2 and len(times) > 1:
        span = times[onsets[-1]] - times[onsets[0]]
        rate = (len(onsets) - 1) / span if span > 0 else 0.0

    mod = _modulation(x, fs)
    f0s = [c["f0_hz"] for c in cells if c["hnr_db"] > 3 and c["f0_hz"] > 20]
    jitter = 0.0
    if len(f0s) >= 3:
        m = float(np.mean(f0s))
        jitter = float(np.std(f0s) / m * 100.0) if m > 0 else 0.0
    return {"hop_s": hop_s, "times": times, "fc": [float(v) for v in FC],
            "cells": cells, "onset_rate_hz": round(rate, 2),
            "onsets": len(onsets), "technique": technique(stereo, fs, hop_s),
            "mod_rate_hz": mod["rate_hz"], "mod_depth": mod["depth"],
            "jitter_pct": round(jitter, 2)}


CHARS = " .:-=+*#%@"


def _shade(v: float, lo: float = -60.0, hi: float = 0.0) -> str:
    k = (v - lo) / (hi - lo)
    k = 0.0 if k < 0 else (1.0 if k > 1 else k)
    return CHARS[min(len(CHARS) - 1, int(k * (len(CHARS) - 1) + 0.5))]


def _curve(vals, lo, hi, chars=" ▁▂▃▄▅▆▇█") -> str:
    out = []
    for v in vals:
        k = (v - lo) / (hi - lo + 1e-9)
        k = 0.0 if k < 0 else (1.0 if k > 1 else k)
        out.append(chars[min(len(chars) - 1, int(k * (len(chars) - 1) + 0.5))])
    return "".join(out)


def picture(tp: dict, band_rows: int = 14, width: int = 100) -> str:
    """Лента как картинка: полосы по времени + кривые громкости, тона, панорамы.

    tp — то, что отдаёт analyze()/live.tape().
    """
    cells = tp["cells"]
    if not cells:
        return "(пусто)"
    times = tp["times"]
    n = len(times)
    bands = [c["bands"] for c in cells]
    nb = len(bands[0])
    if width and n > width:                      # сжать по времени, если длинно
        k = int(np.ceil(n / width))
        bands = [[max(bands[i][j] for i in range(g, min(g + k, n))) for j in range(nb)]
                 for g in range(0, n, k)]
        times = [times[g] for g in range(0, n, k)]
        n = len(times)

    step = max(1, nb // band_rows)
    lines = []
    step_s = max(1, int(round(times[-1] / max(1, n - 1)))) if n > 1 else 1
    header = "".join(str(int(t)) if abs(t - round(t)) < 0.051 else " " for t in times)
    lines.append("время,с  " + header)
    for r in range(0, nb, step):
        grp = bands
        label = "%5.0fГц" % tp["fc"][r]
        row = "".join(
            _shade(max(bands[i][j] for j in range(r, min(r + step, nb)))) for i in range(n)
        )
        lines.append("%s |%s|" % (label, row))
    lines.append("      └" + "─" * n + "┘  шкала: ' '=-60 дБ … '@'=0 дБ")

    rms = [c["rms_db"] for c in cells]
    lines.append("RMS   " + _curve(rms, -60, -6) + "  %.1f…%.1f дБ" % (min(rms), max(rms)))
    cen = [c["centroid_hz"] for c in cells]
    lines.append("центр " + _curve([np.log10(max(v, 20)) for v in cen], np.log10(50), np.log10(12000))
                 + "  %.0f…%.0f Гц" % (min(cen), max(cen)))
    pan = [c["pan"] for c in cells]
    lines.append("пан   " + _curve(pan, -1, 1, "Lll·rR") + "  %.2f…%.2f" % (min(pan), max(pan)))
    corr = [c["corr"] for c in cells]
    lines.append("corr  " + _curve(corr, -1, 1) + "  %.2f…%.2f" % (min(corr), max(corr)))
    f0 = [c["f0_hz"] for c in cells]
    if max(f0) > 0:
        lines.append("f0    " + "".join("%-3s" % (c["note"] if c["hnr_db"] > 3 else "·")
                                        for c in cells))
    lines.append("онсеты: %d, %.1f Гц" % (tp.get("onsets", 0), tp.get("onset_rate_hz", 0.0)))
    return "\n".join(lines)
