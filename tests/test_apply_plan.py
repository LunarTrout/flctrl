"""Живая проверка: весь план применяется к FL за один вызов.

Главное — плагины не портятся. Тип плагина определяется по тому, что он
показывает (частоты и усиления), а не по списку имён:
  * лимитер в слоте 0 не считается эквалайзером, план берёт слот 1;
  * уже стоящий эквалайзер находится в дальнем слоте и используется повторно;
  * прямой вызов на лимитере отказывает, а не крутит чужие ручки.

Нужен запущенный FL с тестовым проектом (small.flp). Рабочий проект не трогаем.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import apply as A  # noqa: E402
import fl_lib as fl  # noqa: E402

T1, T2 = 1, 2


def _guard() -> bool:
    import render_job
    title = (render_job.window_title() or "").lower()
    if "small" not in title:
        print("открыт не тестовый проект (%r) — тест остановлен" % title[:60])
        return False
    return True


def _clean(track: int) -> None:
    for s in range(A.SLOTS_PER_TRACK):
        if fl.act.params(track, s):
            fl.create.live_delete(track, s)


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    if not _guard():
        return 2
    h = fl.state.health()
    print("FL:", (h.get("project") or "")[:50], "| дорожек:", h.get("tracks"))
    if not h.get("bridge"):
        return 2

    _clean(T1)
    _clean(T2)

    # --- 1. чужой плагин в слоте 0
    ins = fl.create.live_insert(T1, 0, "Fruity Limiter")
    check("лимитер встал в слот 0", bool(ins.get("ok")), str(ins.get("ok")))
    limiter_params = fl.act.params(T1, 0)
    limiter_before = [p["text"] for p in limiter_params][:3]
    check("лимитер не похож на эквалайзер", not A.is_eq(limiter_params),
          "полос: %d" % len(A.bands_from_params(limiter_params)))
    found_slot, _ = A.eq_slot(fl, T1)
    check("эквалайзера на дорожке нет", found_slot is None, str(found_slot))

    edits = [
        {"stem": "gtr", "region": "mid", "do_db": 2.0},
        {"stem": "gtr", "region": "pres", "do_db": -1.5},
        {"stem": "bass", "region": "lowmid", "do_db": -2.0},
    ]
    res = fl.apply.plan(edits, {"gtr": T1, "bass": T2})
    gtr = (res.get("tracks") or {}).get("gtr") or {}
    check("план применён", bool(res.get("ok")), str(res.get("error") or "")[:40])
    check("задействовано две дорожки", res.get("applied_count") == 2,
          str(res.get("applied_count")))
    check("взят свободный слот, а не занятый", gtr.get("slot") == 1, "слот %s" % gtr.get("slot"))
    check("лимитер не тронут",
          [p["text"] for p in fl.act.params(T1, 0)][:3] == limiter_before,
          str(limiter_before[:2]))

    applied = gtr.get("applied") or []
    check("гитара: две полосы", len(applied) == 2,
          str([(a["region"], a["freq_text"], a["gain_text"]) for a in applied]))
    g = {a["region"]: A.parse_db(a["gain_text"] or "") for a in applied}
    check("усиление гитары по плану", abs((g.get("mid") or 0) - 2.0) <= 0.7, "mid %s" % g.get("mid"))
    check("срез гитары по плану", abs((g.get("pres") or 0) + 1.5) <= 0.7, "pres %s" % g.get("pres"))
    freqs = {a["region"]: A.parse_hz(a["freq_text"] or "") for a in applied}
    check("частоты попали в области", abs((freqs.get("mid") or 0) - 700) < 120
          and abs((freqs.get("pres") or 0) - 4500) < 700, str(freqs))

    # --- 2. эквалайзер уже стоит далеко: второй не появляется
    _clean(T2)
    fl.create.live_insert(T2, 3, "Fruity Parametric EQ 2")
    before_slots = len(fl.state.plugins([T2]) or [])
    found_slot, name = A.eq_slot(fl, T2)
    check("эквалайзер найден по параметрам", found_slot == 3, "%s в слоте %s" % (name, found_slot))

    res2 = fl.apply.plan([{"stem": "flute", "region": "air", "do_db": 1.0}], {"flute": T2})
    flute = (res2.get("tracks") or {}).get("flute") or {}
    after_slots = len(fl.state.plugins([T2]) or [])
    check("использован уже стоящий эквалайзер", flute.get("slot") == 3, "слот %s" % flute.get("slot"))
    check("второй плагин не появился", before_slots == after_slots,
          "%d → %d" % (before_slots, after_slots))

    # --- 3. прямой вызов на чужом плагине отказывает
    bad = A.apply_eq(fl, T1, 0, {"mid": 3.0})
    check("чужой плагин не крутится",
          bad.get("ok") is False and "эквалайзер" in (bad.get("error") or ""),
          str(bad.get("error"))[:60])

    _clean(T1)
    _clean(T2)
    check("после уборки слоты пусты",
          not fl.state.plugins([T1]) and not fl.state.plugins([T2]), "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
