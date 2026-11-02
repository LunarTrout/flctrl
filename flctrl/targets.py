"""targets.py — цели из корпуса референсов, а не из головы.

Зачем: «идеальный микс» — это не абсолютные числа, а распределение, в котором
живут референсы. Инструмент собирает по корпусу медиану и разброс (IQR) для
каждого стема: уровень относительно микса, доли семи областей, динамику
(crest, PLR, LDR), резкость и стерео.

Откуда данные: стемы Demucs по каждому референсу (drums/bass/other/vocals),
разложенные по папкам <track>/. Считается один раз, кэшируется в JSON по mtime
исходников — повторный вызов бесплатный.

    python -m flctrl.targets --refs DIR --out targets.json
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

STEMS = ("drums", "bass", "other", "vocals")
AXES = ("lufs", "peak_db", "plr", "crest", "ldr", "sharp")
REGIONS = ("sub", "bass", "lowmid", "mid", "upmid", "pres", "air")
STEREO = ("corr", "width", "mono_loss_db")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _measure(path: str) -> dict:
    """Замер одного стема через слуховой слой библиотеки."""
    import listen_layer as L

    m = L.measure(path)
    stereo = m.get("stereo") or {}
    return {
        "lufs": float(m.get("lufs", float("nan"))),
        "peak_db": float(m.get("peak_db", float("nan"))),
        "plr": float(m.get("plr", float("nan"))),
        "crest": float(m.get("crest_db", float("nan"))),
        "ldr": float(m.get("ldr_db", float("nan"))),
        "sharp": float(m.get("sharp_mean", float("nan"))),
        "regions": {k: float(v) for k, v in (m.get("regions_pct") or {}).items()},
        "stereo": {
            "corr": float(stereo.get("corr", float("nan"))),
            "width": float(stereo.get("width", float("nan"))),
            "mono_loss_db": float(stereo.get("mono_loss_db", float("nan"))),
        },
    }


def _iter_tracks(refs_dir: str):
    for name in sorted(os.listdir(refs_dir)):
        d = os.path.join(refs_dir, name)
        if not os.path.isdir(d):
            continue
        files = {}
        for stem in STEMS:
            p = os.path.join(d, stem + ".wav")
            if os.path.exists(p):
                files[stem] = p
        if files:
            yield name, files


def build(refs_dir: str, out: str | None = None) -> dict:
    """Собрать цели: медиана и IQR по каждому стему и оси."""
    rows: dict[str, list[dict]] = {s: [] for s in STEMS}
    used: list[str] = []
    for name, files in _iter_tracks(refs_dir):
        used.append(name)
        for stem, path in files.items():
            try:
                rows[stem].append(_measure(path))
            except Exception as exc:  # битый стем не должен рушить корпус
                print("пропуск %s/%s: %s" % (name, stem, str(exc)[:80]))

    targets: dict[str, Any] = {"tracks": used, "stems": {}}
    for stem, items in rows.items():
        if not items:
            continue
        entry: dict[str, Any] = {"n": len(items)}
        for axis in AXES:
            vals = [it[axis] for it in items if it[axis] == it[axis]]  # без NaN
            if vals:
                entry[axis] = {"median": _percentile(vals, 0.5),
                               "q25": _percentile(vals, 0.25),
                               "q75": _percentile(vals, 0.75)}
        regions: dict[str, Any] = {}
        for reg in REGIONS:
            vals = [it["regions"].get(reg) for it in items]
            vals = [v for v in vals if v is not None and v == v]
            if vals:
                regions[reg] = {"median": _percentile(vals, 0.5),
                                "q25": _percentile(vals, 0.25),
                                "q75": _percentile(vals, 0.75)}
        entry["regions"] = regions
        stereo: dict[str, Any] = {}
        for ax in STEREO:
            vals = [it["stereo"].get(ax) for it in items]
            vals = [v for v in vals if v is not None and v == v]
            if vals:
                stereo[ax] = {"median": _percentile(vals, 0.5),
                              "q25": _percentile(vals, 0.25),
                              "q75": _percentile(vals, 0.75)}
        entry["stereo"] = stereo
        targets["stems"][stem] = entry

    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(targets, f, ensure_ascii=False, indent=1)
    return targets


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Цели из корпуса референсов")
    ap.add_argument("--refs", required=True, help="папка с подпапками-референсами")
    ap.add_argument("--out", required=True, help="куда записать JSON")
    a = ap.parse_args(argv)
    t = build(a.refs, a.out)
    for stem, e in t["stems"].items():
        regs = " ".join("%s%.0f" % (k[0], v["median"]) for k, v in e["regions"].items())
        print("%-8s n=%d crest %.1f LDR %.1f sharp %.2f | %s" % (
            stem, e["n"], e["crest"]["median"], e["ldr"]["median"],
            e["sharp"]["median"], regs))
    print("записано:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
