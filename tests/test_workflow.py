"""Сквозной путь по интерфейсу — так, как им будет пользоваться модель.

Проверяем не отдельные функции, а последовательность из спеки:
help → проект → замер → сравнение → маскировка → провенанс → план → отчёт.
Если какой-то шаг описан в документах, но не работает, тест это покажет.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402

FLP = r"C:/Users/epicc/tools/fltest/fixtures/demo2.flp"
WAV = r"C:/Users/epicc/tools/fltest/fixtures/demo2.wav"


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-50s %s" % (name, detail))

    if not (os.path.exists(FLP) and os.path.exists(WAV)):
        print("нет демо-файлов")
        return 2

    # 1. карта: модель должна узнать, что вообще есть
    h = api.help()
    ops = h.get("operations") or h.get("ops") or {}
    check("help перечисляет операции", len(ops) >= 8, str(len(ops)))
    check("help знает про панораму", "pan.move" in ops or "pan.plan" in ops, "")

    # 2. проект
    api.use_project(FLP)
    pr = api.get("/project")
    check("темп точный", abs(pr["tempo"] - 126.0) < 0.01, str(pr["tempo"]))
    check("клипы посчитаны", pr["clips"] > 600, str(pr["clips"]))

    # 3. замер и кадр
    f1 = api.measure({"metric": "bands", "source": WAV, "a": 10, "b": 30})
    f2 = api.measure({"metric": "bands", "source": WAV, "a": 40, "b": 60})
    check("кадр с id", f1["id"].startswith("f"), f1["id"])
    check("полосы подписаны", len(f1["axes"]["band"]) == len(f1["values"][0]), "")

    # 4. сравнение по id
    same = api.compare(f1["id"], f1["id"])
    check("сравнение с самим собой — нули", abs(same["stats"]["max_abs"]) < 1e-9,
          str(same["stats"]["max_abs"]))
    diff = api.compare(f1["id"], f2["id"])
    check("сравнение разных участков — не нули", diff["stats"]["max_abs"] > 0.1,
          "%.2f" % diff["stats"]["max_abs"])

    # 5. маскировка
    m = api.measure({"metric": "masking", "stems": {"a": WAV, "b": WAV},
                     "a": 10, "b": 20})
    check("маскировка вернула пары", len(m["axes"]["pair"]) == 2, str(m["axes"]["pair"]))

    # 6. провенанс
    at = api.call("clip.at", {"seconds": 60.0})
    check("провенанс в момент", at["count"] > 0, str(at["count"]))

    # 7. план панорамы
    plan = api.call("pan.plan", {"stems": {"guitar": WAV}, "a": 0, "b": 20,
                                 "section_s": 4.0})
    check("план панорамы", bool(plan["stems"]), str(list(plan["stems"])))
    check("план проходит проверку", bool(api.call("pan.check", {"plan": plan}).get("ok")), "")

    # 8. отчёт
    rep = api.call("mix.report", {"mine": WAV, "refs": [WAV], "a": 0, "b": 12})
    check("отчёт с проверками", len(rep["coverage"]["results"]) >= 3,
          str(len(rep["coverage"]["results"])))
    check("отчёт с действиями", isinstance(rep["actions"], list), str(len(rep["actions"])))

    # 9. реестр покрытия
    cov = api.help("coverage")
    check("покрытие видит измеренное", "bands" in cov["measured"], str(cov["measured"][:3]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
