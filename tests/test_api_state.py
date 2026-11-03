"""Состояние интерфейса: кадры под id, dry_run, откат.

Проверки, которым не нужен живой FL: кадры и их id, режим «только показать»,
откат через /section (это состояние сессии, а не проекта).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    api.STORE.clear()
    api.UNDO.clear()
    api.dry_run(False)

    # кадр хранится под id и достаётся по пути
    import frames as F
    f = api._store_frame(F.make_frame("rms", {"band": ["a"]}, [-10.0]))
    check("кадр получил id", f["id"].startswith("f"), f["id"])
    check("кадр достаётся по id", api.get("/measure/" + f["id"])["metric"] == "rms", "")
    check("список кадров", any(x["id"] == f["id"] for x in api.get("/measure")), "")

    try:
        api.get("/measure/f999")
        check("неизвестный кадр отвергается", False, "приняло")
    except api.ApiError as e:
        check("неизвестный кадр отвергается", e.code == "unknown_frame", e.code)

    # dry_run: ничего не меняется
    api.set_value("/section/a", 3.0)
    api.dry_run(True)
    plan = api.set_value("/section/a", 9.0)
    now = api.get("/section/a")["value"]
    check("dry_run возвращает план", plan.get("dry_run") is True, str(plan))
    check("dry_run не меняет значение", abs(now - 3.0) < 1e-6, str(now))
    api.dry_run(False)

    # откат
    api.set_value("/section/a", 7.0)
    check("значение изменилось", abs(api.get("/section/a")["value"] - 7.0) < 1e-6, "")
    res = api.undo(1)
    check("откат вернул прежнее", abs(api.get("/section/a")["value"] - 3.0) < 1e-6,
          str(res))
    check("стек откатов уменьшился", res["left"] >= 0, str(res["left"]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
