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

    sp = res.get("speech") or {}
    if sp.get("sii") is not None:
        actions.append("разборчивость вокала SII %.2f — %s; хуже всего %d Гц" % (
            sp["sii"], sp.get("grade") or "", sp.get("worst_band") or 0))

    wl = res.get("wall") or {}
    if wl.get("mine"):
        w = wl["mine"]
        c = wl.get("compare") or {}
        actions.append("стена: толщина %.2f, провалов %d%s" % (
            w["thickness"], w["hole_count"],
            (" (%s)" % c["holes"]["verdict"]) if c.get("holes") else ""))
        for h in w["holes"][:3]:
            actions.append("провал на %.0f с: полоса %d Гц упала на %.1f дБ — "
                           "проверить, что там играет" % (h["t"], h["band"], h["drop_db"]))

    tr = res.get("transients") or {}
    if tr.get("compare", {}).get("ok"):
        c = tr["compare"]
        for v in c.get("verdict", [])[:3]:
            actions.append("транзиенты — %s" % v)
        actions.append("плотность ударов %.1f/с против %.1f/с у референсов" % (
            c.get("onsets_s") or 0, c.get("ref_onsets_s") or 0))

    dec = res.get("decisions") or {}
    for e in (dec.get("edits") or [])[:6]:
        actions.append("%s, «%s», %.0f–%.0f с: %+.1f дБ (наш %s, цель %s)%s" % (
            e["stem"], e["region_label"], e["span"][0], e["span"][1], e["do_db"],
            ("%+.1f дБ" % e["snr_db"]) if e.get("snr_db") is not None else
            ("A=%.2f" % (e.get("audib") or 0)),
            ("%+.1f дБ" % e["goal_db"]) if e.get("goal_db") is not None else "-",
            " — упёрлось в кап" if e.get("capped") else ""))

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
          ducked: str | None = None, vocal: str | None = None,
          masker: str | None = None,
          sii_baseline: list[float] | None = None,
          targets_path: str | None = None) -> dict:
    ctx = {"mine": mine, "refs": refs, "a": a, "b": b, "stems": stems,
           "impulse": impulse, "sine": sine, "ducked": ducked,
           "vocal": vocal, "masker": masker, "sii_baseline": sii_baseline,
           "targets_path": targets_path}
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

    wl = res.get("wall")
    if wl and wl.get("mine"):
        w = wl["mine"]
        c = wl.get("compare") or {}
        lines.append("")
        lines.append("## Стена и провалы")
        lines.append("")
        lines.append("Толщина **%.2f**%s, провалов **%d**%s." % (
            w["thickness"],
            (" (%s)" % c["thickness"]["verdict"]) if c.get("thickness") else "",
            w["hole_count"],
            (" (%s)" % c["holes"]["verdict"]) if c.get("holes") else ""))
        if w["holes"]:
            lines.append("")
            lines.append("| время, с | полоса, Гц | провал, дБ |")
            lines.append("|---|---|---|")
            for h in w["holes"]:
                lines.append("| %.0f | %d | %.1f |" % (h["t"], h["band"], h["drop_db"]))
        else:
            lines.append("Провалов по полосам нет: каждая полоса держится "
                         "в пределах 8 дБ от своей медианы.")

    tr = res.get("transients")
    if tr and tr.get("compare", {}).get("ok"):
        c = tr["compare"]
        lines.append("")
        lines.append("## Транзиенты: атака и удар/хвост")
        lines.append("")
        lines.append("Плотность ударов **%.1f/с** (референсы %.1f/с)." % (
            c.get("onsets_s") or 0, c.get("ref_onsets_s") or 0))
        lines.append("")
        lines.append("| полоса | наш удар/хвост, дБ | референсы, дБ | разница | атака, мс |")
        lines.append("|---|---|---|---|---|")
        for r in c["rows"]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                r["band"],
                "-" if r["mine"] is None else "%.2f" % r["mine"],
                "-" if r["ref"] is None else "%.2f" % r["ref"],
                "-" if r["delta"] is None else "%+.2f" % r["delta"],
                "-" if not r.get("attack_ms") else "%.1f" % r["attack_ms"]))
        if c.get("verdict"):
            lines.append("")
            for v in c["verdict"]:
                lines.append("- %s" % v)
        else:
            lines.append("")
            lines.append("Атака в коридоре референсов по всем полосам.")

    sp = res.get("speech")
    if sp:
        lines.append("")
        lines.append("## Разборчивость вокала (SII, ANSI S3.5)")
        lines.append("")
        lines.append("SII **%.3f** — %s. Хуже всего полоса %d Гц." % (
            sp["sii"], sp.get("grade", ""), sp.get("worst_band", 0)))
        lines.append("")
        lines.append("| Гц | речь | маскер | маскирует | слышимость | важность |")
        lines.append("|---|---|---|---|---|---|")
        for b in sp["bands"]:
            lines.append("| %d | %.1f | %.1f | %.1f | %.2f | %.4f |" % (
                b["f"], b["speech"], b["masker"], b["masked"],
                b["audibility"], b["importance"]))

    dec = res.get("decisions")
    if dec and dec.get("edits"):
        lines.append("")
        lines.append("## Правки по секциям (из измерений)")
        lines.append("")
        lines.append("Цели: %s." % ("из корпуса" if dec.get("targets_used")
                                  else "учебные, «наравне с остальным»"))
        lines.append("")
        lines.append("| инструмент | полоса | участок, с | наш, дБ | цель, дБ | правка |")
        lines.append("|---|---|---|---|---|---|")
        for e in dec["edits"]:
            lines.append("| %s | %s | %.0f–%.0f | %s | %s | %+.1f дБ%s |" % (
                e["stem"], e["region_label"], e["span"][0], e["span"][1],
                ("%+.1f" % e["snr_db"]) if e.get("snr_db") is not None else "—",
                ("%+.1f" % e["goal_db"]) if e.get("goal_db") is not None else "—",
                e["do_db"], " (кап)" if e.get("capped") else ""))
        if dec.get("skipped"):
            lines.append("")
            for s in dec["skipped"]:
                lines.append("- пропущено: %s — %s" % (s["stem"], s["why"]))

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
    ap.add_argument("--vocal", default=None)
    ap.add_argument("--masker", default=None)
    ap.add_argument("--sii-baseline", default=None,
                    help="JSON с базовой линией SII референсов (sii_baseline.json)")
    ap.add_argument("--targets", default=None,
                    help="JSON целей по полосам (snr_baseline.json)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    stems = {}
    for item in a.stems:
        name, _, path = item.partition("=")
        stems[name] = path

    baseline = None
    if a.sii_baseline and os.path.exists(a.sii_baseline):
        import json as _json
        data = _json.load(open(a.sii_baseline, encoding="utf-8"))
        baseline = [r["sii"] for r in data.get("refs", [])] or None

    rep = build(a.mine, a.refs or None, a.a, a.b, stems or None,
                a.impulse, a.sine, a.ducked, a.vocal, a.masker, baseline,
                a.targets)
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
