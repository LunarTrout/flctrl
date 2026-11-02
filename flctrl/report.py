"""report.py - один вызов: где мы хуже коридора и что с этим делать.

Собирает вместе аудит по фронтам, сравнение по одинаковым участкам и (если
даны стемы) карту ролей. На выход - markdown-отчёт и список действий,
выведенных из чисел: без «всегда шире» и «всегда резать 300 Гц».

    python -m flctrl.report --mine my.wav --refs a.wav b.wav --out report.md
"""
from __future__ import annotations

import argparse
import json
import os

import audit
import seccomp


def _fmt(v, nd=2):
    return "—" if v is None else ("%.*f" % (nd, v))


def build(mine: str, refs: list[str], a: float | None = None,
          b: float | None = None, stems: dict | None = None) -> dict:
    mine_m = audit.measure(mine, a, b)
    refs_m = [audit.measure(r, a, b) for r in refs]
    aud = audit.audit(mine_m, refs_m)
    sec = seccomp.compare(mine, refs, a, b)

    actions: list[str] = []
    for row in aud["bad"]:
        direction = "поднять" if row["mine"] < row["ref_median"] else "снизить"
        actions.append("%s: наш %.2f против %.2f у референсов — %s до коридора (%.2f…%.2f)" % (
            row["label"], row["mine"], row["ref_median"], direction,
            row["q25"], row["q75"]))
    if aud["bands_out_of_corridor"]:
        worst = sorted(aud["bands_detail"],
                       key=lambda x: abs(x["mine"] - (x["lo"] + x["hi"]) / 2), reverse=True)[:3]
        for w in worst:
            where = "выше" if w["mine"] > (w["lo"] + w["hi"]) / 2 else "ниже"
            actions.append("полоса %d: форма спектра %s коридора на %.1f дБ" % (
                w["band"], where, abs(w["mine"] - (w["lo"] + w["hi"]) / 2)))
    for info, label in ((aud.get("stereo_bands"), "ширина"), (aud.get("corr_bands"), "корреляция")):
        if info and info.get("count"):
            w = info["worst"][0]
            actions.append("%s по полосам: %d полос вне коридора, худшая — %d (наш %.2f, коридор %.2f…%.2f)" % (
                label, info["count"], w["band"], w["mine"], w["lo"], w["hi"]))

    # секции: где спектр дальше всего от ближайшего референса
    worst_sec = sorted(sec["sections"], key=lambda s: s["bands_delta_db"], reverse=True)[:3]
    for s in worst_sec:
        actions.append("секция %.0f–%.0f с: ближе всего к %s, но спектр расходится на %.1f дБ" % (
            s["t0"], s["t1"], s["ref"][:18], s["bands_delta_db"]))

    roles = None
    if stems:
        import balance
        try:
            roles = balance.project(stems)
        except Exception as exc:
            roles = {"error": str(exc)[:120]}

    return {"audit": aud, "sections": sec, "roles": roles,
            "actions": actions, "mine_path": mine, "refs": refs}


def to_markdown(res: dict) -> str:
    aud = res["audit"]
    lines = ["# Отчёт по миксу", ""]
    lines.append("Файл: `%s`" % os.path.basename(res["mine_path"]))
    lines.append("Референсов: %d" % len(res["refs"]))
    lines.append("")
    lines.append("## Фронты (коридор — межквартильный размах референсов)")
    lines.append("")
    lines.append("| фронт | наш | медиана реф. | коридор | вывод |")
    lines.append("|---|---|---|---|---|")
    for r in aud["rows"]:
        lines.append("| %s | %.2f | %.2f | %.2f…%.2f | %s |" % (
            r["label"], r["mine"], r["ref_median"], r["q25"], r["q75"],
            r["flag"] if r["flag"] != "ok" else "в коридоре"))
    if aud.get("mastering"):
        lines.append("")
        lines.append("## Мастеринг (ступень обработки, не качество)")
        lines.append("")
        for m in aud["mastering"]:
            lines.append("- %s: наш %.2f, у референсов %.2f" % (m["label"], m["mine"], m["ref_median"]))
    lines.append("")
    lines.append("## Спектр")
    lines.append("")
    lines.append("Полос вне коридора: **%d** из 28." % aud["bands_out_of_corridor"])
    for b in aud["bands_detail"]:
        lines.append("- полоса %d: наш %.1f, коридор %.1f…%.1f дБ" % (
            b["band"], b["mine"], b["lo"], b["hi"]))
    for key, label in (("stereo_bands", "Ширина по полосам"), ("corr_bands", "Корреляция по полосам")):
        info = aud.get(key) or {}
        lines.append("")
        lines.append("## %s" % label)
        lines.append("")
        if info.get("count"):
            lines.append("Вне коридора: **%d** полос." % info["count"])
            for w in info["worst"][:3]:
                lines.append("- полоса %d: наш %.2f, коридор %.2f…%.2f" % (
                    w["band"], w["mine"], w["lo"], w["hi"]))
        else:
            lines.append("В коридоре.")
    lines.append("")
    lines.append("## Секции (сопоставление по форме спектра)")
    lines.append("")
    lines.append("| наши, с | похоже на | схожесть | Δспектр, дБ | крест наш/реф |")
    lines.append("|---|---|---|---|---|")
    for s in res["sections"]["sections"]:
        lines.append("| %.0f–%.0f | %s @%.0f с | %.3f | %.2f | %.1f / %.1f |" % (
            s["t0"], s["t1"], s["ref"][:20], s["ref_t0"], s["sim"],
            s["bands_delta_db"], s["crest"][0], s["crest"][1]))
    lines.append("")
    lines.append("## Что делать (выведено из чисел)")
    lines.append("")
    for i, act in enumerate(res["actions"], 1):
        lines.append("%d. %s" % (i, act))
    if not res["actions"]:
        lines.append("Ни один фронт не хуже коридора — правок по числам нет.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Единый отчёт по миксу")
    ap.add_argument("--mine", required=True)
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    stems = {}
    for item in a.stems:
        name, _, path = item.partition("=")
        stems[name] = path

    res = build(a.mine, a.refs, a.a, a.b, stems or None)
    md = to_markdown(res)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("отчёт:", a.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
