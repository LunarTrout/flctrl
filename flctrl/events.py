"""events.py — быстрый слой: короткие события, которые лента 100 мс теряет.

Считает огибающую каждые ~3 мс и отдаёт не кадры, а список событий:

    {t, kind: onset|spike|clip, peak_db, dur_ms, crest_db, band, level_db}

    * onset — рост уровня на 6+ дБ после тишины/спада (удар, щипок, слог);
    * spike — резкий скачок на 15+ дБ за 20 мс (тот самый «внезапно громко»);
    * clip  — сэмплы у 0 дБ (перегруз).

События — дешёвая замена плотного потока: разрешение по времени 3 мс,
а данных в десятки раз меньше, чем кадров.
"""
from __future__ import annotations

import numpy as np

HOP_S = 0.002          # 2 мс — хватает на 32-е при 300 BPM (37 мс)
WIN_S = 0.004          # окно огибающей 4 мс


def _envelope(mono: np.ndarray, fs: int, hop_s: float = HOP_S, win_s: float = WIN_S):
    hop = max(1, int(hop_s * fs))
    win = max(hop, int(win_s * fs))
    n = max(1, (len(mono) - win) // hop + 1)
    peaks = np.zeros(n)
    rmss = np.zeros(n)
    for i in range(n):
        seg = mono[i * hop:i * hop + win]
        peaks[i] = np.max(np.abs(seg))
        rmss[i] = np.sqrt(np.mean(seg ** 2))
    times = np.arange(n) * hop / fs
    return times, peaks, rmss


def _event_spectrum(stereo: np.ndarray, fs: int, t0: float, dur: float) -> dict:
    """Короткое окно вокруг события: полосы и центроид (что это было)."""
    a = int(t0 * fs)
    b = min(len(stereo), a + max(128, int(dur * fs)))
    seg = stereo[a:b].mean(axis=1)
    if len(seg) < 128:
        return {"band": "?", "centroid_hz": 0.0, "bands_db": []}
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / fs)
    e = [float(spec[(f >= 20) & (f < 200)].sum()),
         float(spec[(f >= 200) & (f < 2000)].sum()),
         float(spec[(f >= 2000) & (f < 16000)].sum())]
    tot = sum(e) + 1e-20
    cent = float((f * spec).sum() / (spec.sum() + 1e-20))
    return {"band": ["low", "mid", "high"][int(np.argmax(e))],
            "centroid_hz": round(cent, 0),
            "bands_db": [round(10 * np.log10(v / tot + 1e-20), 1) for v in e]}


def _band_energy(stereo: np.ndarray, fs: int, t0: float, dur: float):
    a = int(t0 * fs)
    b = min(len(stereo), a + max(64, int(dur * fs)))
    seg = stereo[a:b].mean(axis=1)
    if len(seg) < 64:
        return "?"
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / fs)
    e = [float(spec[(f >= 20) & (f < 200)].sum()),
         float(spec[(f >= 200) & (f < 2000)].sum()),
         float(spec[(f >= 2000) & (f < 16000)].sum())]
    return ["low", "mid", "high"][int(np.argmax(e))]


def detect(stereo: np.ndarray, fs: int = 44100, hop_s: float = HOP_S,
           onset_db: float = 5.0, refractory_s: float = 0.012,
           spike_db: float = 15.0, spike_window_s: float = 0.02,
           floor_db: float = -70.0) -> dict:
    """Найти короткие события: удары/щипки, скачки громкости, перегруз."""
    x = np.asarray(stereo, dtype=float)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    mono = x.mean(axis=1)

    times, peaks, rmss = _envelope(mono, fs, hop_s)
    if len(times) == 0:
        return {"hop_ms": hop_s * 1000, "events": [], "count": 0}

    db = 20 * np.log10(peaks + 1e-12)
    events = []

    # --- онсеты: рост уровня после спада
    last_t = -1e9
    for i in range(1, len(db)):
        if db[i] < floor_db:
            continue
        rise = db[i] - db[i - 1]
        prev_dip = db[max(0, i - 3):i].min()
        if rise >= onset_db and db[i] > prev_dip + onset_db and (times[i] - last_t) >= refractory_s:
            last_t = float(times[i])
            events.append({"t": round(float(times[i]), 3), "kind": "onset",
                           "level_db": round(float(db[i]), 1)})

    # --- пики и длительность каждого события
    for ev in events:
        i0 = int(ev["t"] / hop_s)
        i1 = min(len(db), i0 + int(0.15 / hop_s))
        win = db[i0:i1]
        if len(win) == 0:
            continue
        pk_i = i0 + int(np.argmax(win))
        pk_db = float(db[pk_i])
        # длительность: пока не упадёт на 15 дБ ниже пика
        j = pk_i
        while j < len(db) and db[j] > pk_db - 15:
            j += 1
        dur = (j - pk_i) * hop_s
        # крест внутри события
        seg_p = peaks[i0:i1]
        seg_r = rmss[i0:i1]
        crest = 20 * np.log10((seg_p.max() + 1e-12) / (seg_r.mean() + 1e-12))
        capped = j >= len(db)
        spec = _event_spectrum(x, fs, ev["t"], 0.04)
        ev.update({"peak_db": round(pk_db, 1),
                   "dur_ms": round(min(dur, 0.15) * 1000, 0),
                   "dur_capped": bool(capped),
                   "crest_db": round(float(crest), 1),
                   "band": spec["band"],
                   "centroid_hz": spec["centroid_hz"],
                   "bands_db": spec["bands_db"]})

    # --- скачки громкости (внезапно громко)
    w = max(1, int(spike_window_s / hop_s))
    for i in range(w, len(db)):
        jump = db[i] - db[i - w]
        if jump >= spike_db and db[i] > -40:
            if any(abs(times[i] - e["t"]) < 0.05 for e in events):
                continue
            events.append({"t": round(float(times[i]), 3), "kind": "spike",
                           "level_db": round(float(db[i]), 1),
                           "jump_db": round(float(jump), 1)})

    # --- перегруз
    clip_idx = np.where(np.abs(mono) >= 0.999)[0]
    if clip_idx.size:
        events.append({"t": round(float(clip_idx[0]) / fs, 3), "kind": "clip",
                       "count": int(clip_idx.size)})

    events.sort(key=lambda e: e["t"])
    for ev in events:
        if ev["kind"] == "onset":
            idx = int(ev["t"] / hop_s)
            if idx < len(rmss):
                ev["rms_db"] = round(float(20 * np.log10(rmss[idx] + 1e-12)), 1)

    ons = [e["t"] for e in events if e["kind"] == "onset"]
    rate = 0.0
    regular = 0.0
    if len(ons) >= 3:
        gaps = np.diff(ons)
        span = ons[-1] - ons[0]
        rate = (len(ons) - 1) / span if span > 0 else 0.0
        regular = float(np.std(gaps) / (np.mean(gaps) + 1e-9))
    peaks_db = [e.get("peak_db", e.get("level_db", -120)) for e in events]
    return {"hop_ms": round(hop_s * 1000, 2), "events": events, "count": len(events),
            "max_peak_db": round(float(max(peaks_db)), 1) if peaks_db else None,
            "onsets": len(ons), "onset_rate_hz": round(rate, 2),
            "regularity": round(regular, 3),
            "min_gap_ms": round(float(np.min(np.diff(ons))) * 1000, 1) if len(ons) > 1 else None,
            "spikes": sum(1 for e in events if e["kind"] == "spike"),
            "clips": sum(1 for e in events if e["kind"] == "clip")}
