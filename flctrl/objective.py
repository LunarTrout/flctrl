"""objective.py — насколько наш микс далёк от «идеального» по корпусу.

Идея: каждую ось (уровень, семь областей, динамика, стерео) сравниваем не с
абсолютом, а с распределением референсов. Расстояние нормируется на разброс
корпуса (IQR), поэтому «два пункта» в узкой оси весят больше, чем в широкой.
Итог — одно число и список худших мест: что именно тянет микс назад.

Никаких захардкоженных «правильных» чисел: все цели приходят из targets.json.

    python -m flctrl.objective --stems drums=... --stems bass=... --targets t.json
    python -m flctrl.objective --dir stems_dir --targets t.json --map other=GTR
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from typing import Any

AXES = ("crest", "ldr", "sharp")
REGIONS = ("sub", "bass", "lowmid", "mid", "upmid", "pres", "air")
STEREO = ("corr", "width", "mono_loss_db")

# Насколько ось важна для «здорового» микса. Уровень и области — главное,
# динамика и стерео — тоже, но их легко испортить линейной правкой.
WEIGHTS = {
    "level": 1.4,
    "regions": 1.0,
    "crest": 1.0,
    "ldr": 0.9,
    "sharp": 0.8,
    "stereo": 0.6,
}


def _measure(path: str) -> dict:
    import listen_layer as L

    m = L.measure(path)
    st = m.get("stereo") or {}
    return {
        "lufs": float(m.get("lufs", float("nan"))),
        "crest": float(m.get("crest_db", float("nan"))),
        "ldr": float(m.get("ldr_db", float("nan"))),
        "sharp": float(m.get("sharp_mean", float("nan"))),
        "regions": {k: float(v) for k, v in (m.get("regions_pct") or {}).items()},
        "stereo": {
            "corr": float(st.get("corr", float("nan"))),
            "width": float(st.get("width", float("nan"))),
            "mono_loss_db": float(st.get("mono_loss_db", float("nan"))),
        },
    }


def _sum_files(paths: list[str], out: str) -> str:
    """Сложить стемы в один файл (например, «other» = гитары + баян)."""
    import numpy as np
    import soundfile as sf

    acc = None
    fs = None
    for p in paths:
        x, sr = sf.read(p, dtype="float64", always_2d=True)
        if acc is None:
            acc, fs = x, sr
        else:
            if sr != fs:
                raise ValueError("разные частоты дискретизации: %s" % p)
            n = min(len(acc), len(x))
            acc = acc[:n] + x[:n]
    if acc is None:
        raise ValueError("нечего складывать")
    peak = float(np.max(np.abs(acc))) or 1.0
    if peak > 0.999:
        acc = acc / peak * 0.999
    sf.write(out, acc, fs)
    return out


def stems_from_dir(directory: str, prefix: str = "vers1_") -> dict[str, str]:
    """Разложить папку сплит-рендера по стемам-аналогам.

    Гитары и баян вместе — это «other» (как у Demucs), барабаны — DRUMS/DRUM BUS,
    бас — BASS, вокал — Scream/Ann/VOX.
    """
    files = sorted(glob.glob(os.path.join(directory, prefix + "*.wav")))
    by = {os.path.basename(f): f for f in files}

    def pick(*patterns: str) -> list[str]:
        out = []
        for name, path in by.items():
            if any(re.search(p, name, re.I) for p in patterns):
                out.append(path)
        return out

    mapping: dict[str, str] = {}
    drums = pick(r"DRUMS SD3", r"DRUM BUS")
    if drums:
        mapping["drums"] = drums[0]
    bass = pick(r"^vers1_BASS")
    if bass:
        mapping["bass"] = bass[0]
    vocals = pick(r"Scream vocal", r"Ann vocal")
    if vocals:
        mapping["vocals"] = vocals[0]
    other = pick(r"GTR L BUS", r"GTR R BUS", r"BAYAN")
    if other:
        mapping["other"] = other
    return mapping


def measure_stems(mapping: dict[str, Any], tmp_dir: str | None = None) -> dict[str, dict]:
    """Замер каждого стема; значением может быть путь или список путей."""
    out: dict[str, dict] = {}
    for stem, value in mapping.items():
        if isinstance(value, (list, tuple)):
            if len(value) == 1:
                path = value[0]
            else:
                tmp_dir = tmp_dir or os.path.join(os.path.dirname(value[0]), "_sum")
                os.makedirs(tmp_dir, exist_ok=True)
                path = _sum_files(list(value), os.path.join(tmp_dir, stem + ".wav"))
        else:
            path = value
        out[stem] = _measure(path)
    return out


def _sigma(entry: dict, axis: str) -> float:
    q25 = entry.get(axis, {}).get("q25")
    q75 = entry.get(axis, {}).get("q75")
    if q25 is None or q75 is None:
        return float("nan")
    # IQR -> сигма нормального распределения; снизу ограничиваем, чтобы
    # корпус из шести треков не давал деления на ноль.
    return max((q75 - q25) / 1.349, 0.15)


def score(ours: dict[str, dict], targets: dict, weights: dict | None = None) -> dict:
    """Сравнить наши стемы с целями. Возвращает число, разбивку и худшие места."""
    w = dict(WEIGHTS)
    if weights:
        w.update(weights)
    target_stems = targets.get("stems") or {}
    # «other» у нас громче всех: уровни сравниваем относительно среднего по
    # стемам, иначе получаем разницу калибровки, а не баланса.
    def rel(levels: dict[str, float]) -> dict[str, float]:
        vals = [v for v in levels.values() if v == v]
        base = sum(vals) / len(vals) if vals else 0.0
        return {k: v - base for k, v in levels.items() if v == v}

    our_rel = rel({s: m["lufs"] for s, m in ours.items()})
    ref_rel = rel({s: e["lufs"]["median"] for s, e in target_stems.items()
                   if e.get("lufs")})

    rows: list[dict] = []
    for stem, mine in ours.items():
        entry = target_stems.get(stem)
        if not entry:
            continue
        # уровень
        if stem in our_rel and stem in ref_rel:
            sig = max(abs(ref_rel[stem]) * 0.12, 0.8)  # допуск по уровню, дБ
            d = our_rel[stem] - ref_rel[stem]
            rows.append({"stem": stem, "axis": "level", "delta": d,
                         "z": d / sig, "unit": "dB"})
        # области
        for reg in REGIONS:
            tgt = (entry.get("regions") or {}).get(reg)
            if not tgt:
                continue
            sig = max(_sigma(entry["regions"][reg], "median") if False else
                      (entry["regions"][reg]["q75"] - entry["regions"][reg]["q25"]) / 1.349, 0.8)
            d = mine["regions"].get(reg, float("nan")) - tgt["median"]
            if d != d:
                continue
            rows.append({"stem": stem, "axis": "region:" + reg, "delta": d,
                         "z": d / sig, "unit": "п.п."})
        # динамика, резкость, стерео
        for axis in AXES:
            tgt = entry.get(axis)
            if not tgt:
                continue
            sig = _sigma(entry, axis)
            d = mine[axis] - tgt["median"]
            if d != d or sig != sig:
                continue
            rows.append({"stem": stem, "axis": axis, "delta": d,
                         "z": d / sig, "unit": ""})
        for ax in STEREO:
            tgt = (entry.get("stereo") or {}).get(ax)
            if not tgt:
                continue
            sig = (tgt["q75"] - tgt["q25"]) / 1.349
            sig = max(sig, 0.02)
            d = mine["stereo"].get(ax, float("nan")) - tgt["median"]
            if d != d:
                continue
            rows.append({"stem": stem, "axis": "stereo:" + ax, "delta": d,
                         "z": d / sig, "unit": ""})

    def axis_weight(axis: str) -> float:
        if axis == "level":
            return w["level"]
        if axis.startswith("region:"):
            return w["regions"]
        if axis.startswith("stereo:"):
            return w["stereo"]
        return w.get(axis, 0.7)

    num = 0.0
    den = 0.0
    for r in rows:
        r["weight"] = axis_weight(r["axis"])
        num += r["weight"] * r["z"] ** 2
        den += r["weight"]
    total = (num / den) ** 0.5 if den else float("nan")
    worst = sorted(rows, key=lambda r: abs(r["z"]), reverse=True)[:8]
    return {"score": total, "rows": len(rows), "worst": worst,
            "by_stem": {s: max((abs(r["z"]) for r in rows if r["stem"] == s), default=0.0)
                        for s in ours}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Расстояние до целей корпуса")
    ap.add_argument("--targets", required=True)
    ap.add_argument("--dir", help="папка сплит-рендера")
    ap.add_argument("--stems", action="append", default=[],
                    help="явная пара stem=path (можно несколько раз)")
    a = ap.parse_args(argv)

    import targets as T

    tg = T.load(a.targets)
    if a.dir:
        mapping: dict[str, Any] = stems_from_dir(a.dir)
    else:
        mapping = {}
        for item in a.stems:
            stem, _, path = item.partition("=")
            mapping[stem] = path
    if not mapping:
        print("нет стемов: укажи --dir или --stems")
        return 2

    ours = measure_stems(mapping)
    res = score(ours, tg)
    print("расстояние до корпуса: %.2f σ" % res["score"])
    for stem, z in sorted(res["by_stem"].items(), key=lambda kv: kv[1], reverse=True):
        print("  %-8s худшая ось %.2f σ" % (stem, z))
    print("худшие места:")
    for r in res["worst"]:
        print("  %-8s %-16s %+7.2f %-4s (%.2f σ)" % (
            r["stem"], r["axis"], r["delta"], r["unit"], r["z"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
