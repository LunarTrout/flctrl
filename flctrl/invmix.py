"""invmix.py — обратная задача сведения: подобрать гейны ролей под цель.

Постановка. У нас есть НАСТОЯЩИЕ стемы (дорожки проекта), а у референса —
только стерео-микс. Разделять референс нейросетью нельзя: ошибки разделения
больше, чем сам эффект. Поэтому цель берётся на уровне микса (профиль семи
областей, резкость, crest, стерео), а неизвестные — гейны наших ролей.

Дальше это обычная оптимизация: сумма стемов с гейнами → те же метрики →
расстояние до цели. Уравнений больше, чем неизвестных, разделение не нужно,
и ответ проверяем: сумма должна дать вектор, близкий к целевому.

    python -m flctrl.invmix --stems drums=a.wav --stems bass=b.wav \
        --target target.json --cap 6
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

import perceptual as P

REGIONS = P.REGIONS
AXES_REGION = ("sub", "bass", "lowmid", "mid", "upmid", "pres", "air")
WEIGHTS = {"region": 1.0, "sharp": 0.8, "crest": 0.8, "stereo": 0.6}


def load(path: str, max_seconds: float | None = None):
    import soundfile as sf

    x, fs = sf.read(path, dtype="float64", always_2d=True)
    if max_seconds:
        x = x[: int(max_seconds * fs)]
    return x, fs


def align(*arrays):
    n = min(len(a) for a in arrays)
    ch = max(a.shape[1] for a in arrays)
    out = []
    for a in arrays:
        a = a[:n]
        if a.shape[1] < ch:
            a = np.repeat(a, ch, axis=1) if a.shape[1] == 1 else np.pad(
                a, ((0, 0), (0, ch - a.shape[1])))
        out.append(a)
    return out


def mix(stems: dict[str, np.ndarray], gains_db: dict[str, float]) -> np.ndarray:
    keys = list(stems)
    parts = align(*[stems[k] for k in keys])
    total = np.zeros_like(parts[0])
    for k, part in zip(keys, parts):
        total += part * (10 ** (gains_db.get(k, 0.0) / 20.0))
    return total


def metrics(x: np.ndarray, fs: int) -> dict:
    """Метрики микса без обращения к файлам и без FL."""
    lv, _t = P.band_levels(x, fs)
    lv = P.calibrate(lv)
    N, _phon, spec, sharp = P.zwicker_series(lv)
    regions = {}
    for name, lo, hi in REGIONS:
        m = (P.BARK_Z >= lo) & (P.BARK_Z < hi)
        regions[name] = float(spec[:, m].sum(axis=1).mean())
    tot = sum(regions.values()) or 1e-9
    regions_pct = {k: 100.0 * v / tot for k, v in regions.items()}

    mono = x.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    crest = 20 * np.log10(peak / rms)

    corr = 1.0
    width = 0.0
    if x.shape[1] >= 2:
        left, right = x[:, 0], x[:, 1]
        if np.std(left) > 1e-9 and np.std(right) > 1e-9:
            corr = float(np.corrcoef(left, right)[0, 1])
        mid = (left + right) / 2
        side = (left - right) / 2
        width = float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid ** 2)) + 1e-12))
    return {"regions_pct": regions_pct, "sharp": float(np.mean(sharp)),
            "crest": float(crest), "corr": corr, "width": width,
            "n_mean": float(np.mean(N)),
            "bands_db": [float(v) for v in np.median(lv, axis=0)]}


def distance(m: dict, target: dict) -> float:
    """Взвешенное расстояние до цели (в единицах допуска).

    Семисеми областями задача недоопределена: разные балансы дают похожие
    доли. Поэтому основной вес — у полного 28-полосного профиля (допуск 1.5 дБ),
    а области, резкость, crest и стерео идут как дополнительные约束.
    """
    parts, wsum = 0.0, 0.0
    tb = target.get("bands_db")
    mb = m.get("bands_db")
    if tb and mb and len(tb) == len(mb):
        for a, b in zip(mb, tb):
            if b <= -80:            # полоса без сигнала — не оцениваем
                continue
            parts += 2.0 * ((a - b) / 1.5) ** 2
            wsum += 2.0
    for reg in AXES_REGION:
        tgt = (target.get("regions_pct") or {}).get(reg)
        if tgt is None:
            continue
        d = m["regions_pct"][reg] - tgt
        parts += WEIGHTS["region"] * (d / 2.5) ** 2
        wsum += WEIGHTS["region"]
    for ax, tol in (("sharp", 0.06), ("crest", 1.5)):
        if target.get(ax) is None:
            continue
        d = m[ax] - target[ax]
        parts += WEIGHTS[ax] * (d / tol) ** 2
        wsum += WEIGHTS[ax]
    if target.get("corr") is not None:
        d = m["corr"] - target["corr"]
        parts += WEIGHTS["stereo"] * (d / 0.05) ** 2
        wsum += WEIGHTS["stereo"]
    return float((parts / wsum) ** 0.5) if wsum else float("nan")


def solve(stems: dict[str, np.ndarray], fs: int, target: dict,
          cap_db: float = 6.0, step_db: float = 0.5, rounds: int = 14,
          fixed: dict[str, float] | None = None, verbose: bool = False) -> dict:
    """Координатный спуск по гейнам ролей с капом.

    Почему не градиент: сигнал нелинейный (громкость, маскировка), а
    вычисление метрик дороже, чем перебор нескольких десятков вариантов.
    Координатный спуск здесь устойчивее и не требует производных.
    """
    gains = {k: float((fixed or {}).get(k, 0.0)) for k in stems}
    free = [k for k in stems if k not in (fixed or {})]
    m0 = metrics(mix(stems, gains), fs)
    best = distance(m0, target)
    history = [{"gains": dict(gains), "distance": best}]

    for _ in range(rounds):
        improved = False
        for k in free:
            for delta in (step_db, -step_db):
                cand = dict(gains)
                cand[k] = float(np.clip(cand[k] + delta, -cap_db, cap_db))
                if cand[k] == gains[k]:
                    continue
                d = distance(metrics(mix(stems, cand), fs), target)
                if d < best - 1e-6:
                    gains, best = cand, d
                    improved = True
                    if verbose:
                        print("  %-8s %+0.1f дБ → %.3f" % (k, cand[k], best), flush=True)
                    break
        history.append({"gains": dict(gains), "distance": best})
        if not improved:
            break
    return {"gains": gains, "distance": best, "history": history,
            "before": history[0]["distance"], "metrics": metrics(mix(stems, gains), fs)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Обратная задача: гейны под цель микса")
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--target", required=True, help="JSON с целевыми метриками")
    ap.add_argument("--cap", type=float, default=6.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    stems = {}
    fs = None
    for item in a.stems:
        name, _, path = item.partition("=")
        x, fs = load(path, a.seconds)
        stems[name] = x
    if not stems:
        print("нужны --stems name=path")
        return 2

    with open(a.target, encoding="utf-8") as f:
        target = json.load(f)

    res = solve(stems, fs, target, cap_db=a.cap, verbose=True)
    print("\nитог: %.2f → %.2f" % (res["before"], res["distance"]))
    for k, v in res["gains"].items():
        print("  %-8s %+5.1f дБ" % (k, v))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("записано:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
