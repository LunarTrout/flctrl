"""Операции поверх измерений: отчёт, имитация правок, план панорамы.

Всё офлайн — на демо-файлах, без FL.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402

WAV = r"C:/Users/epicc/tools/fltest/fixtures/demo2.wav"


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    if not os.path.exists(WAV):
        print("нет демо-файла", WAV)
        return 2

    rep = api.call("mix.report", {"mine": WAV, "refs": [WAV], "a": 0, "b": 12})
    check("отчёт содержит проверки", len(rep["coverage"]["results"]) >= 3,
          str(len(rep["coverage"]["results"])))
    check("отчёт даёт действия", isinstance(rep["actions"], list), str(len(rep["actions"])))
    check("контекст не раздут", "ctx" not in rep, str(list(rep)[:5]))

    md = api.call("mix.report", {"mine": WAV, "refs": [WAV], "a": 0, "b": 12,
                                 "markdown": True})
    check("markdown собирается", len(md["markdown"]) > 200, str(len(md["markdown"])))
    check("в markdown есть заголовок", "#" in md["markdown"], "")

    plan = api.call("pan.plan", {"stems": {"guitar": WAV}, "a": 0, "b": 12,
                                 "section_s": 4.0})
    check("план панорамы построен", bool(plan["stems"]), str(list(plan["stems"])))
    ok = api.call("pan.check", {"plan": plan})
    check("план проходит проверку", bool(ok.get("ok")), str(ok)[:80])

    try:
        api.call("pan.move", {"track": 1, "points": []})
        check("пустые точки отвергаются", False, "приняло")
    except api.ApiError as e:
        check("пустые точки отвергаются", e.code == "bad_args", e.code)

    try:
        api.call("mix.report", {})
        check("без файла — ошибка", False, "приняло")
    except api.ApiError as e:
        check("без файла — ошибка", e.code == "bad_args", e.code)

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
