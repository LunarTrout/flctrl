"""report.py — один вызов: полный обход проверок и что с этим делать.

Отчёт обязан обойти ВЕСЬ реестр (checks.py). Пропущенная проверка не исчезает,
а печатается с причиной — так «забыл посмотреть» видно, а не теряется.
Действия выводятся из чисел: без «всегда шире» и «всегда резать 300 Гц».

    python -m flctrl.report --mine my.wav --refs a.wav b.wav
    python -m flctrl.report ... --stems drums=d.wav --impulse ir.wav
"""
from __future__ import annotations

import argparse
import os

import checks


def _derive_actions(res: dict) -> list[str]:
    actions: list[str] = []

    fronts = res.get("fronts") or {}
    for row in fronts.get("bad", []):
        direction = "поднять" if row["mine"] < row["ref_median"] else "снизить"
        actions.append("%s: наш %.2f против %.2f — %s до коридора (%.2f…%.2f)" % (
            row["label"], row["mine"], row["ref_median"], direction,
            row["q25"], row["q75"]))
    if fronts.get("bands_out_of_corridor"):
        worst = sorted(fronts["bands_detail"],
                       key=lambda x: abs(x["mine"] - (x["lo"] + x["hi"]) / 2),
                       reverse=True)[:3]
        for w in worst:
            where = "выше" if w["mine"] > (w["lo"] + w["hi"]) / 2 else "ниже"
            actions.append("полоса %d: форма спектра %s коридора на %.1f дБ" % (
                w["band"], where, abs(w["mine"] - (w["lo"] + w["hi"]) / 2)))
    for key, label in (("stereo_bands", "ширина"), ("corr_bands", "корреляция")):
        info = fronts.get(key) or {}
        if info.get("count"):
            w = info["worst"][0]
            actions.append("%s по полосам: %d полос вне коридора, худшая — %d "
                           "(наш %.2f, коридор %.2f…%.2f)" % (
                               label, info["count"], w["band"], w["mine"],
                               w["lo"], w["hi"]))

    sec = res.get("sections") or {}
    for s in sorted(sec.get("sections", []),
                    key=lambda s: s["bands_delta_db"], reverse=True)[:3]:
        actions.append("секция %.0f–%.0f с: ближе всего к %s, спектр расходится "
                       "на %.1f дБ" % (s["t0"], s["t1"], s["ref"][:18],
                                       s["bands_delta_db"]))

    roles = res.get("roles") or {}
    for name, v in (roles.get("roles") or {}).items():
        if v["audib"] < 0.08:
            actions.append("роль %s: слышимость %.2f при доле %.1f%% — в миксе "
                           "её почти нет" % (name, v["audib"], v["share_pct"]))

    pan_res = res.get("pan") or {}
    for tip in pan_res.get("advice", []):
        actions.append("панорама %s: %s (%s)" % (tip["stem"], tip["action"], tip["why"]))

    ef = res.get("effects_ir") or {}
    if ef.get("rt60"):
        actions.append("реверб: RT60 %.2f с, EDT %.2f с, C50 %.1f дБ — сверить "
                       "с коридором референсов" % (ef["rt60"], ef["edt"], ef["c50"]))
    th = res.get("effects_thd") or {}
    if th.get("thd_pct"):
        actions.append("нелинейность: THD %.2f%% — если выше 3%%, проверить "
                       "перегруз цепи" % th["thd_pct"])
    return actions


def build(mine: str, refs: list[str] | None = None, a: float | None = None,
          b: float | None = None, stems: dict | None = None,
          impulse: str | None = None, sine: str | None = None,
          ducked: str | None = None) -> dict:
    ctx = {"mine": mine, "refs": refs, "a": a, "b": b, "stems": stems,
           "impulse": impulse, "sine": sine, "ducked": ducked}
    cov = checks.run_all(ctx)
    res = cov["results"]
    return {"coverage": cov, "results": res, "actions": _derive_actions(res),
            "mine_path": mine, "refs": refs or [], "ctx": ctx}


def to_markdown(rep: dict) -> str:
    cov = rep["coverage"]
    res = rep["results"]
    lines = ["# Отчёт по миксу", ""]
    lines.append("Файл: `%s`" % os.path.basename(rep["mine_path"]))
    lines.append("Референсов: %d" % len(rep["refs"]))
    lines.append("")
    lines.append("## Покрытие проверок: %s" % cov["coverage"])
    lines.append("")
    lines.append("| проверка | группа | статус |")
    lines.append("|---|---|---|")
    ran = set(cov["ran"])
    covered = set(cov.get("covered") or [])
    for chk in checks.REGISTRY:
        if chk.id in ran:
            status = "выполнена"
        elif chk.id in covered:
            status = chk.note or "входит в другую проверку"
        else:
            skip = next((s for s in cov["skipped"] if s["id"] == chk.id), None)
            status = ("пропущена: %s" % ", ".join(skip["why"])) if skip else "—"
        lines.append("| %s | %s | %s |" % (chk.label, checks.GROUP_LABELS.get(chk.group, ""), status))

    fronts = res.get("fronts")
    if fronts:
        lines.append("")
        lines.append("## Фронты")
        lines.append("")
        lines.append("| фронт | наш | медиана реф. | коридор | вывод |")
        lines.append("|---|---|---|---|---|")
        for r in fronts["rows"]:
            lines.append("| %s | %.2f | %.2f | %.2f…%.2f | %s |" % (
                r["label"], r["mine"], r["ref_median"], r["q25"], r["q75"],
                r["flag"] if r["flag"] != "ok" else "в коридоре"))
        if fronts.get("mastering"):
            lines.append("")
            lines.append("Мастеринг (ступень обработки, не качество): "
                         + "; ".join("%s наш %.2f, рефы %.2f" % (
                             m["label"], m["mine"], m["ref_median"])
                             for m in fronts["mastering"]))
        lines.append("")
        lines.append("Спектр: полос вне коридора **%d** из 28." % fronts["bands_out_of_corridor"])
        for b in fronts["bands_detail"]:
            lines.append("- полоса %d: наш %.1f, коридор %.1f…%.1f дБ" % (
                b["band"], b["mine"], b["lo"], b["hi"]))
        for key, label in (("stereo_bands", "Ширина по полосам"),
                           ("corr_bands", "Корреляция по полосам")):
            info = fronts.get(key) or {}
            lines.append("")
            if info.get("count"):
                lines.append("%s: вне коридора **%d** полос." % (label, info["count"]))
                for w in info["worst"][:3]:
                    lines.append("- полоса %d: наш %.2f, коридор %.2f…%.2f" % (
                        w["band"], w["mine"], w["lo"], w["hi"]))
            else:
                lines.append("%s: в коридоре." % label)

    sec = res.get("sections")
    if sec:
        lines.append("")
        lines.append("## Секции (сопоставление по форме спектра)")
        lines.append("")
        lines.append("| наши, с | похоже на | схожесть | Δспектр, дБ | крест наш/реф |")
        lines.append("|---|---|---|---|---|")
        for s in sec["sections"]:
            lines.append("| %.0f–%.0f | %s @%.0f с | %.3f | %.2f | %.1f / %.1f |" % (
                s["t0"], s["t1"], s["ref"][:20], s["ref_t0"], s["sim"],
                s["bands_delta_db"], s["crest"][0], s["crest"][1]))

    roles = res.get("roles")
    if roles and roles.get("roles"):
        lines.append("")
        lines.append("## Роли (слышимость в миксе)")
        lines.append("")
        lines.append("| роль | A | доля | N_partial | dN/dB |")
        lines.append("|---|---|---|---|---|")
        for name, v in sorted(roles["roles"].items(), key=lambda kv: -kv[1]["share_pct"]):
            lines.append("| %s | %.3f | %.1f%% | %.1f | %.2f |" % (
                name, v["audib"], v["share_pct"], v["N_partial"], v["dN_per_db"]))

    pan_res = res.get("pan")
    if pan_res and pan_res.get("advice"):
        lines.append("")
        lines.append("## Панорама (измеренные основания)")
        lines.append("")
        for tip in pan_res["advice"]:
            lines.append("- %s: %s — %s" % (tip["stem"], tip["action"], tip["why"]))

    if cov["skipped"]:
        lines.append("")
        lines.append("## Не проверено (и почему)")
        lines.append("")
        for s in cov["skipped"]:
            lines.append("- %s: не хватает %s%s" % (
                s["label"], ", ".join(s["why"]),
                (" — " + s["note"]) if s.get("note") else ""))

    lines.append("")
    lines.append("## Что делать (выведено из чисел)")
    lines.append("")
    if rep["actions"]:
        for i, act in enumerate(rep["actions"], 1):
            lines.append("%d. %s" % (i, act))
    else:
        lines.append("Ни один измеряемый фронт не хуже коридора — правок по числам нет.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Единый отчёт по миксу")
    ap.add_argument("--mine", required=True)
    ap.add_argument("--refs", nargs="*", default=[])
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--impulse", default=None)
    ap.add_argument("--sine", default=None)
    ap.add_argument("--ducked", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    stems = {}
    for item in a.stems:
        name, _, path = item.partition("=")
        stems[name] = path

    rep = build(a.mine, a.refs or None, a.a, a.b, stems or None,
                a.impulse, a.sine, a.ducked)
    md = to_markdown(rep)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("отчёт:", a.out, "| покрытие:", rep["coverage"]["coverage"])
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
