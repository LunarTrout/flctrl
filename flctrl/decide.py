"""decide.py — из измеренной слышимости в конкретные правки.

Глобальное «эта роль тише» бесполезно: непонятно, что крутить и на сколько.
Здесь на вход идёт слышимость по секциям и областям (partial.attribute_mg с
section_s), а на выходе — список правок: где, что и на сколько дБ.

Перевод «не хватает слышимости» в децибелы — через отношение шансов:
    A = s/(1+s),  s — отношение сигнал/маскер,
    подъём на Δ дБ даёт s·10^(Δ/10),
    Δ = 10·lg( (target/(1-target)) / (A/(1-A)) ).
Это не «магическая формула», а прямое следствие того, как растёт слышимость,
когда источник уходит из-под маскера. Результат всегда режется капом: одна
правка не должна переворачивать баланс.

    python -m flctrl.decide --attr attr.json
"""
from __future__ import annotations

import argparse
import json

REGION_LABELS = {
    "sub": "саб", "bass": "бас", "lowmid": "низ-середина", "mid": "середина",
    "upmid": "верх-середина", "pres": "присутствие", "air": "воздух",
}


def need_db(a: float, target: float) -> float:
    """Сколько дБ нужно, чтобы поднять слышимость A до target (отношение шансов)."""
    a = min(max(float(a), 1e-4), 0.999)
    t = min(max(float(target), 1e-4), 0.999)
    return 10.0 * __import__("math").log10((t / (1 - t)) / (a / (1 - a)))


def plan(attr: dict, target: float = 0.6, cap_db: float = 3.0,
         min_share_pct: float = 3.0, min_deficit: float = 0.05,
         min_region_share: float = 0.05, max_edits: int = 12,
         target_snr_db: float = 0.0, min_deficit_db: float = 1.0) -> dict:
    """План правок: одна строка на «инструмент × полоса», с участком времени.

    Два пути:
    * есть `snr` (отношение инструмент/остальное в полосе) — считаем по нему.
      Это рабочий путь: он не насыщается нулём в плотном миксе, поэтому правки
      различимы по величине и по приоритету;
    * `snr` нет — считаем по слышимости A (отношение шансов), как раньше.

    Решение принимается по медиане в тех секциях, где инструмент в этой полосе
    реально звучит: «бас, верх-середина, 120–145 с, +2.4 дБ» вместо сотни строк.
    """
    import statistics

    groups: dict = {}
    skipped = []
    for name, row in (attr.get("stems") or {}).items():
        sections = row.get("sections")
        if not sections:
            skipped.append({"stem": name, "why": "нет разбивки по секциям"})
            continue
        share = row.get("share_pct", 0.0)
        if share < min_share_pct:
            skipped.append({"stem": name,
                            "why": "доля %.1f%% ниже порога — правка не слышна" % share})
            continue
        for sec in sections:
            if not sec.get("active"):
                continue
            for region, a in (sec.get("regions") or {}).items():
                content = (sec.get("share") or {}).get(region, 1.0)
                if content < min_region_share:
                    continue          # инструмент здесь не звучит — не наша забота
                snr = (sec.get("snr") or {}).get(region)
                if snr is not None:
                    deficit_db = target_snr_db - float(snr)
                    if abs(deficit_db) < min_deficit_db:
                        continue
                else:
                    deficit_db = None
                    if target - a < min_deficit - 1e-9:
                        continue
                groups.setdefault((name, region), []).append(
                    {"t": sec["t"], "audib": a, "content": content,
                     "snr": snr, "deficit_db": deficit_db})

    edits = []
    for (name, region), rows in groups.items():
        a_med = float(statistics.median(r["audib"] for r in rows))
        content = float(statistics.median(r["content"] for r in rows))
        times = [r["t"] for r in rows]
        dbs = [r["deficit_db"] for r in rows if r["deficit_db"] is not None]
        if dbs:
            deficit_db = float(statistics.median(dbs))
            raw = deficit_db
            basis = "snr"
            audib = None
            snr_med = round(float(statistics.median(
                [r["snr"] for r in rows if r["snr"] is not None])), 1)
        else:
            raw = need_db(a_med, target)
            deficit_db = raw
            basis = "audibility"
            audib = round(a_med, 3)
            snr_med = None
        capped = max(-cap_db, min(cap_db, raw))
        edits.append({
            "stem": name,
            "region": region,
            "region_label": REGION_LABELS.get(region, region),
            "basis": basis,
            "audib": audib,
            "snr_db": snr_med,
            "deficit": round(deficit_db, 3),
            "need_db": round(raw, 2),
            "do_db": round(capped, 2),
            "capped": abs(raw) > cap_db + 1e-9,
            "sections": times,
            "span": [min(times), max(times)],
            "n_sections": len(rows),
            "impact": round(abs(deficit_db) * content, 4),
            "why": ("«%s» на %.0f–%.0f с: %s" % (
                REGION_LABELS.get(region, region), min(times), max(times),
                ("инструмент на %+.1f дБ относительно остального микса" % snr_med
                 if basis == "snr" else
                 "слышимость %.2f при цели %.2f" % (a_med, target)))),
        })
    edits.sort(key=lambda e: -e["impact"])
    total = len(edits)
    return {"edits": edits[:max_edits], "skipped": skipped,
            "target": target, "cap_db": cap_db, "target_snr_db": target_snr_db,
            "count": min(total, max_edits), "found": total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Правки из измеренной слышимости")
    ap.add_argument("--attr", required=True, help="JSON вывода partial.attribute_mg")
    ap.add_argument("--target", type=float, default=0.6)
    ap.add_argument("--cap", type=float, default=3.0)
    a = ap.parse_args(argv)
    data = json.load(open(a.attr, encoding="utf-8"))
    res = plan(data, a.target, a.cap)
    for e in res["edits"][:20]:
        print("%-10s %5.0f–%-4.0f с  %-14s A=%.2f  %+.1f дБ%s" % (
            e["stem"], e["span"][0], e["span"][1], e["region_label"], e["audib"],
            e["do_db"], " (обрезано с %+.1f)" % e["need_db"] if e["capped"] else ""))
    for s in res["skipped"]:
        print("пропущено: %-10s %s" % (s["stem"], s["why"]))
    print("правок: %d из %d найденных" % (res["count"], res["found"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
