"""Живая проверка: весь план применяется к FL за один вызов.

Проверяем то, что нужно на практике: план из нескольких инструментов и полос
превращается в реальные полосы эквалайзеров на нужных дорожках, лишние плагины
не плодятся, а повторное применение не ломает уже настроенное.

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


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    if not _guard():
        return 2
    h = fl.state.health()
    print("FL:", h.get("project"), "| дорожек:", h.get("tracks"))
    if not h.get("bridge"):
        return 2

    edits = [
        {"stem": "gtr", "region": "mid", "do_db": 2.0},
        {"stem": "gtr", "region": "pres", "do_db": -1.5},
        {"stem": "bass", "region": "lowmid", "do_db": -2.0},
    ]
    res = fl.apply.plan(edits, {"gtr": T1, "bass": T2})
    check("план применён", bool(res.get("ok")), str(res.get("tracks", {}))[:70])
    check("задействовано две дорожки", res.get("applied_count") == 2,
          str(res.get("applied_count")))

    gtr = (res.get("tracks") or {}).get("gtr") or {}
    bass = (res.get("tracks") or {}).get("bass") or {}
    check("гитара: две полосы", len(gtr.get("applied") or []) == 2,
          str([(a["region"], a["gain_text"]) for a in gtr.get("applied") or []]))
    check("бас: одна полоса", len(bass.get("applied") or []) == 1,
          str([(a["region"], a["gain_text"]) for a in bass.get("applied") or []]))

    g = {a["region"]: A.parse_db(a["gain_text"] or "") for a in gtr.get("applied") or []}
    check("усиление гитары по плану", abs((g.get("mid") or 0) - 2.0) <= 0.7,
          "mid %s" % g.get("mid"))
    check("срез гитары по плану", abs((g.get("pres") or 0) + 1.5) <= 0.7,
          "pres %s" % g.get("pres"))

    # повторное применение не должно вставлять второй эквалайзер
    before_params = len(fl.act.params(T1, 0))
    res2 = fl.apply.plan(edits, {"gtr": T1, "bass": T2})
    after_params = len(fl.act.params(T1, 0))
    check("повтор не плодит плагины", before_params == after_params,
          "%d → %d" % (before_params, after_params))
    check("повтор тоже проходит", bool(res2.get("ok")), "")

    # вернуть нейтраль, чтобы тестовый проект остался чистым
    fl.apply.eq(T1, 0, {"mid": 0.0, "pres": 0.0})
    fl.apply.eq(T2, 0, {"lowmid": 0.0})
    fl.create.live_delete(T1, 0)
    fl.create.live_delete(T2, 0)
    check("после уборки слоты пусты",
          fl.act.params(T1, 0) == [] and fl.act.params(T2, 0) == [], "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
