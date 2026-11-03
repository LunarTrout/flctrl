"""snrtargets.py — жанровые цели по полосам: какое отношение «роль / остальное» нормально.

Ноль децибел («быть наравне со всем») — учебная цель, а не музыкальная: в
металле гитары обязаны быть громче баса в середине, а бас — громче всех в
своей полосе. Поэтому цель берётся из корпуса: у референсов измеряется то же
отношение по каждой полосе, медиана становится целью.

Роли упрощаются до тех, что реально разделяются в референсах: drums, bass,
vocals, other. Гитары и клавиши попадают в other — ограничение разделения,
и оно записывается в файл, чтобы не забылось.

    python -m flctrl.snrtargets --refs mix1.mp3:stems1 mix2.mp3:stems2 --out docs/snr_baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

REGIONS = ("sub", "bass", "lowmid", "mid", "upmid", "pres", "air")

# какие наши роли берут цель у какой роли корпуса
ROLE_ALIAS = {"guitar": "other", "keys": "other", "other": "other",
              "drums": "drums", "bass": "bass", "vocals": "vocals"}


def measure_one(mix_path: str, stems: dict[str, str], a: float | None = None,
                b: float | None = None, section_s: float = 10.0) -> dict:
    """Отношения по полосам для одного трека: роль -> полоса -> дБ."""
    import partial

    attr = partial.attribute_mg(mix_path, stems, a, b, section_s=section_s)
    out = {}
    for name, row in attr["stems"].items():
        per_region = {r: [] for r in REGIONS}
        for sec in row.get("sections") or []:
            for region, value in (sec.get("snr") or {}).items():
                if region in per_region:
                    per_region[region].append(value)
        out[name] = {r: round(float(statistics.median(v)), 1) if v else None
                     for r, v in per_region.items()}
    return {"mix": mix_path, "roles": out}


def build(pairs: list[tuple[str, dict[str, str]]], a: float | None = None,
          b: float | None = None, section_s: float = 10.0) -> dict:
    """Собрать цели по корпусу: медиана по трекам для каждой роли и полосы."""
    rows = []
    for mix_path, stems in pairs:
        try:
            rows.append(measure_one(mix_path, stems, a, b, section_s))
        except Exception as exc:
            rows.append({"mix": mix_path, "error": str(exc)[:120]})
    model: dict = {"roles": {}, "tracks": rows, "section_s": section_s,
                   "note": "гитары и клавиши попадают в other — разделение корпуса "
                           "не различает их; цели для guitar/keys берутся у other"}
    names = sorted({n for r in rows if r.get("roles") for n in r["roles"]})
    for name in names:
        per_region = {}
        for region in REGIONS:
            vals = [r["roles"][name][region] for r in rows
                    if r.get("roles") and r["roles"].get(name, {}).get(region) is not None]
            if vals:
                per_region[region] = {"median": round(float(statistics.median(vals)), 1),
                                      "n": len(vals)}
        if per_region:
            model["roles"][name] = per_region
    return model


def for_role(model: dict, role: str) -> dict:
    """Цели для нашей роли: guitar/keys берут у other, если своих нет."""
    roles = model.get("roles") or {}
    key = role if role in roles else ROLE_ALIAS.get(role, role)
    if key not in roles:
        return {}
    return {r: v["median"] for r, v in roles[key].items()}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_pairs(items: list[str]) -> list[tuple[str, dict[str, str]]]:
    """Разбор «путь_микса:папка_стемов». Делим по расширению, а не по первому
    двоеточию: в Windows путь сам содержит «C:»."""
    pairs = []
    for item in items:
        mix = stems_dir = None
        for marker in (".mp3:", ".wav:", ".flac:", ".ogg:", ".m4a:"):
            if marker in item:
                head, _, tail = item.partition(marker)
                mix = head + marker[:-1]
                stems_dir = tail
                break
        if not mix or not stems_dir:
            continue
        stems = {}
        for name in ("drums", "bass", "vocals", "other"):
            p = os.path.join(stems_dir, name + ".wav")
            if os.path.exists(p):
                stems[name] = p
        if stems:
            pairs.append((mix, stems))
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Цели по полосам из корпуса")
    ap.add_argument("--refs", nargs="+", required=True, help="mix.mp3:папка_со_стемами")
    ap.add_argument("--a", type=float, default=0.0)
    ap.add_argument("--b", type=float, default=60.0)
    ap.add_argument("--section", type=float, default=10.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    model = build(_parse_pairs(args.refs), args.a, args.b, args.section)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=1)
    for name, regs in model["roles"].items():
        print("%-8s %s" % (name, "  ".join("%s=%+.1f" % (r, v["median"])
                                           for r, v in regs.items())))
    print("сохранено:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
