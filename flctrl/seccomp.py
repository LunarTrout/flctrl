"""seccomp.py — сравнение с референсами по ОДИНАКОВЫМ участкам.

Проблема случайного окна: мы сравниваем вступление с кульминацией и делаем
вывод «у нас всё не так». Здесь трек режется на секции по смене спектральной
картины, и каждая наша секция сопоставляется с наиболее похожей секцией
референса — по форме спектра, а не по времени.

    python -m flctrl.seccomp --mine my.wav --refs a.wav b.wav
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

import perceptual as P

REGIONS = P.REGIONS
REGION_NAMES = [r[0] for r in REGIONS]


def _read(path: str, a: float | None = None, b: float | None = None):
    import soundfile as sf

    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if a is not None and b is not None:
            f.seek(int(a * fs))
            x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    return x, fs


def _features(x: np.ndarray, fs: int) -> dict:
    lv, _t = P.band_levels(x, fs)
    lv_cal = P.calibrate(lv)
    N, _ph, spec, sharp = P.zwicker_series(lv_cal)
    bands = np.median(lv, axis=0)
    rel = bands - np.mean(bands)
    regions = {}
    for name, lo, hi in REGIONS:
        m = (P.BARK_Z >= lo) & (P.BARK_Z < hi)
        regions[name] = float(spec[:, m].sum(axis=1).mean())
    tot = sum(regions.values()) or 1e-9
    mono = x.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    corr, width = 1.0, 0.0
    if x.shape[1] >= 2:
        l, r = x[:, 0], x[:, 1]
        if np.std(l) > 1e-9 and np.std(r) > 1e-9:
            corr = float(np.corrcoef(l, r)[0, 1])
        mid = (l + r) / 2
        side = (l - r) / 2
        width = float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid ** 2)) + 1e-12))
    return {"bands_rel": [float(v) for v in rel],
            "regions_pct": {k: 100.0 * v / tot for k, v in regions.items()},
            "crest": 20 * np.log10(peak / rms),
            "sharp": float(np.mean(sharp)),
            "corr": corr, "width": width,
            "level": float(20 * np.log10(rms + 1e-12))}


def sections(path: str, a: float | None = None, b: float | None = None,
             min_len: float = 8.0, max_sections: int = 12) -> list[dict]:
    """Нарезать трек на секции по смене спектральной картины."""
    x, fs = _read(path, a, b)
    dur = len(x) / fs
    if dur < min_len * 2:
        return [{"t0": 0.0, "t1": dur, **_features(x, fs)}]

    # кандидаты — равные окна; затем сливаем похожие соседи
    step = max(min_len, dur / max_sections)
    raw = []
    t = 0.0
    while t < dur - min_len / 2:
        seg = x[int(t * fs):int(min(dur, t + step) * fs)]
        if len(seg) < int(0.5 * fs):
            break
        f = _features(seg, fs)
        raw.append({"t0": t, "t1": min(dur, t + step), **f})
        t += step

    merged: list[dict] = []
    for seg in raw:
        if merged:
            prev = merged[-1]
            a1 = np.array(prev["bands_rel"])
            b1 = np.array(seg["bands_rel"])
            sim = float(np.dot(a1, b1) / (np.linalg.norm(a1) * np.linalg.norm(b1) + 1e-9))
            if sim > 0.995:          # картина не менялась — это та же секция
                prev["t1"] = seg["t1"]
                continue
        merged.append(seg)
    return merged


def _sim(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))


def compare(mine_path: str, ref_paths: list[str], a=None, b=None,
            min_len: float = 8.0) -> dict:
    """Каждой нашей секции — самая похожая секция референсов и дельты."""
    mine = sections(mine_path, a, b, min_len)
    refs = []
    for p in ref_paths:
        for s in sections(p, a, b, min_len):
            refs.append({**s, "ref": os.path.basename(p)})

    rows = []
    for i, s in enumerate(mine):
        best, best_sim = None, -1.0
        for r in refs:
            sim = _sim(s["bands_rel"], r["bands_rel"])
            if sim > best_sim:
                best, best_sim = r, sim
        if not best:
            continue
        d_bands = float(np.mean(np.abs(np.array(s["bands_rel"]) -
                                       np.array(best["bands_rel"]))))
        rows.append({
            "mine": i, "t0": round(s["t0"], 1), "t1": round(s["t1"], 1),
            "ref": best["ref"], "ref_t0": round(best["t0"], 1),
            "sim": round(best_sim, 3),
            "bands_delta_db": round(d_bands, 2),
            "crest": (round(s["crest"], 1), round(best["crest"], 1)),
            "width": (round(s["width"], 2), round(best["width"], 2)),
            "sharp": (round(s["sharp"], 2), round(best["sharp"], 2)),
            "regions_delta": {k: round(s["regions_pct"][k] - best["regions_pct"][k], 1)
                              for k in REGION_NAMES},
        })
    return {"sections": rows, "mine_count": len(mine), "ref_count": len(refs)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сравнение по одинаковым участкам")
    ap.add_argument("--mine", required=True)
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    res = compare(args.mine, args.refs, args.a, args.b)
    print("секций у нас: %d, у референсов: %d" % (res["mine_count"], res["ref_count"]))
    print("%-6s %-13s %-22s %6s %8s %-13s %-13s" % (
        "секц", "наши", "похоже на", "схож", "Δспектр", "крест (наш/реф)", "ширина"))
    for r in res["sections"]:
        print("%-6d %-13s %-22s %6.3f %8.2f %-13s %-13s" % (
            r["mine"], "%.0f–%.0f с" % (r["t0"], r["t1"]),
            "%s @%.0f с" % (r["ref"][:14], r["ref_t0"]),
            r["sim"], r["bands_delta_db"],
            "%.1f / %.1f" % r["crest"], "%.2f / %.2f" % r["width"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("записано:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
