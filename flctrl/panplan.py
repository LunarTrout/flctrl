"""panplan.py — план движения панорамы по треку, выведенный из измерений.

Задача: панорама не должна быть статичной линейкой на весь трек, но и
«красивость ради красивости» недопустима — движение должно следовать музыке.
Поэтому план строится из измеренной ширины и плотности по секциям: где микс
плотнее, инструмент отходит шире, где разрежено — подбирается к центру. Низ
(бочка, бас) не двигается никогда: уходит фаза и разваливается низ.

План — это точки (время, позиция), а не готовые значения фейдера: применение
к FL идёт отдельно (автоматизация через движок), и его можно проверить.

    python -m flctrl.panplan --stems "gtrL=a.wav" --stems "gtrR=b.wav"
"""
from __future__ import annotations

import argparse
import json

import numpy as np

LOW_BAND_HZ = 200.0
LOW_DOMINANCE = 0.5


def _low_share(profile_row: dict) -> float:
    """Доля энергии ниже 200 Гц — по ней решаем, можно ли двигать инструмент."""
    bands = profile_row.get("bands") or []
    total = sum(b["energy"] for b in bands) + 1e-30
    low = sum(b["energy"] for b in bands if b["fc"] <= LOW_BAND_HZ)
    return float(low / total)


def plan(stems: dict[str, str], a: float | None = None, b: float | None = None,
         section_s: float = 8.0, max_step: float = 0.15,
         max_pan: float = 0.7) -> dict:
    """Построить план панорамы по секциям.

    Возвращает для каждого стема список точек {t, pan, why} и сводку решений.
    """
    import pan as pan_mod

    m = pan_mod.analyze(stems, a, b, section_s)
    out = {"stems": {}, "section_s": section_s, "max_step": max_step,
           "max_pan": max_pan, "notes": []}

    widths = {n: [s["width"] for s in p.get("sections", [])]
              for n, p in m["stems"].items() if p.get("sections")}
    alive = {n: v for n, v in widths.items() if len(v) >= 2}
    if not alive:
        raise ValueError("недостаточно секций для плана: нужно минимум 2")

    w_all = np.array([x for v in alive.values() for x in v])
    _ = float(w_all.mean())  # ориентир для отчёта, на цели не влияет

    # общий контур плотности по секциям: если у инструмента ширина не меняется,
    # он всё равно должен дышать вместе с треком, а не стоять колом
    max_sec = max(len(v) for v in alive.values())
    global_w = np.zeros(max_sec)
    counts = np.zeros(max_sec)
    for v in alive.values():
        for i, w in enumerate(v):
            global_w[i] += w
            counts[i] += 1
    global_w = global_w / np.maximum(counts, 1)
    g_lo, g_hi = float(global_w.min()), float(global_w.max())
    g_span = max(g_hi - g_lo, 1e-6)

    for name, p in m["stems"].items():
        if p.get("error") or not p.get("sections"):
            out["stems"][name] = {"error": p.get("error", "нет секций")}
            continue
        secs = p["sections"]
        low = _low_share(p)
        pan_now = float(np.mean([s["pan"] for s in secs]))

        if low > LOW_DOMINANCE:
            out["stems"][name] = {
                "points": [{"t": secs[0]["t"], "pan": round(pan_now, 3),
                            "why": "низ — не двигаем"}],
                "static": True, "low_share": round(low, 3)}
            out["notes"].append("%s: %.0f%% энергии ниже %.0f Гц — оставлен на месте"
                                % (name, low * 100, LOW_BAND_HZ))
            continue

        points, why_list = [], []
        w_lo, w_hi = float(np.min(alive[name])), float(np.max(alive[name]))
        own_span = w_hi - w_lo
        use_global = own_span < 0.05          # своих вариаций нет — берём общий контур
        span = max(own_span, 1e-6)
        side = 1.0 if pan_now >= 0 else -1.0
        for i, s in enumerate(secs):
            # ширина секции задаёт, насколько далеко инструмент отходит:
            # разрежено — ближе к центру, плотно — к краю своей стороны
            if use_global:
                fill = float(np.clip((global_w[min(i, len(global_w) - 1)] - g_lo) / g_span,
                                     0.0, 1.0))
            else:
                fill = float(np.clip((s["width"] - w_lo) / span, 0.0, 1.0))
            mag = min(max_pan, 0.35 + 0.35 * fill)
            target = float(np.clip(side * mag, -max_pan, max_pan))
            # шаг между соседними точками ограничен: без телепортов
            if points:
                prev = points[-1]["pan"]
                target = float(np.clip(target, prev - max_step, prev + max_step))
            why = ("плотно — шире" if fill > 0.66 else
                   "разрежено — ближе к центру" if fill < 0.33 else "середина")
            points.append({"t": s["t"], "pan": round(target, 3), "why": why})
            why_list.append(why)

        moved = max(abs(q["pan"] - points[0]["pan"]) for q in points)
        out["stems"][name] = {
            "points": points, "static": moved < 0.02,
            "low_share": round(low, 3), "moved": round(float(moved), 3)}
        out["notes"].append("%s: ход %.2f, %s" % (name, moved, "статичен" if moved < 0.02 else "движется"))

    return out


def check(plan_: dict, section_s: float | None = None) -> dict:
    """Проверки самого плана: порядок, пределы, шаг, низ на месте."""
    problems = []
    for name, row in plan_["stems"].items():
        if row.get("error"):
            problems.append("%s: %s" % (name, row["error"]))
            continue
        pts = row["points"]
        times = [p["t"] for p in pts]
        if times != sorted(times):
            problems.append("%s: точки не по времени" % name)
        lim = plan_["max_pan"]
        if any(abs(p["pan"]) > lim + 1e-9 for p in pts):
            problems.append("%s: позиция за пределом %.2f" % (name, lim))
        step = plan_["max_step"]
        for p, q in zip(pts, pts[1:]):
            if abs(q["pan"] - p["pan"]) > step + 1e-9:
                problems.append("%s: скачок %.3f больше шага %.2f"
                                % (name, abs(q["pan"] - p["pan"]), step))
        if row.get("static") and len({p["pan"] for p in pts}) > 1:
            problems.append("%s: помечен статичным, но позиции разные" % name)
        if row.get("low_share", 0) > LOW_DOMINANCE and len({p["pan"] for p in pts}) > 1:
            problems.append("%s: низ двигается" % name)
    return {"ok": not problems, "problems": problems,
            "stems": len(plan_["stems"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="План движения панорамы по секциям")
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--section", type=float, default=8.0)
    ap.add_argument("--max-step", type=float, default=0.15)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    stems = {}
    for item in a.stems:
        name, _, path = item.partition("=")
        stems[name] = path
    p = plan(stems, a.a, a.b, a.section, a.max_step)
    p["check"] = check(p)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(p, ensure_ascii=False, indent=1))
        print("план:", a.out, "| проверки:", "ок" if p["check"]["ok"] else p["check"]["problems"])
    else:
        print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0 if p["check"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
