"""Кадры и сравнение: одна форма, дельты по осям, честные пропуски."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import frames as F  # noqa: E402


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-50s %s" % (name, detail))

    bands = ["sub", "bass", "mid"]
    a = F.make_frame("rms", {"band": bands}, [-18.0, -14.0, -12.0],
                     coverage={"measured": ["project"]})
    b = F.make_frame("rms", {"band": bands}, [-16.0, -14.4, -13.9],
                     coverage={"measured": ["refs"]})
    check("кадр собирается", a["metric"] == "rms" and a["unit"] == "дБ", a["unit"])
    check("оси сохраняются", a["axes"]["band"] == bands, str(a["axes"]))

    c = F.compare(a, b)
    check("сравнение даёт строки по числу осей", len(c["rows"]) == 3, str(len(c["rows"])))
    check("сортировка по модулю дельты", abs(c["rows"][0]["d"]) >= abs(c["rows"][-1]["d"]),
          str([r["d"] for r in c["rows"]]))
    check("статистика считает медиану", c["stats"]["median"] is not None,
          str(c["stats"]))

    # разные наборы полос — сравнение по пересечению
    a2 = F.make_frame("rms", {"band": ["sub", "mid"]}, [-18.0, -12.0])
    b2 = F.make_frame("rms", {"band": ["mid", "air"]}, [-13.0, -25.0])
    c2 = F.compare(a2, b2)
    check("сравниваются общие оси", c2["stats"]["compared"] == 1, str(c2["stats"]))
    check("разница осей видна",
          c2["coverage"]["unmatched"]["band"]["only_a"] == ["sub"]
          and c2["coverage"]["unmatched"]["band"]["only_b"] == ["air"],
          str(c2["coverage"]["unmatched"]))

    # пропуски не превращаются в ноль
    a3 = F.make_frame("rms", {"band": ["sub", "mid"]}, [-18.0, None])
    check("пропуск помечен в coverage", len(a3["coverage"]["missing"]) == 1,
          str(a3["coverage"]["missing"]))
    c3 = F.compare(a3, F.make_frame("rms", {"band": ["sub", "mid"]}, [-17.0, -11.0]))
    check("пропуск не участвует в сравнении", c3["stats"]["compared"] == 1,
          str(c3["stats"]["compared"]))

    # двумерный кадр
    m = F.make_frame("rms", {"track": [1, 2], "band": ["sub", "mid"]},
                     [[-18.0, -12.0], [-20.0, -11.0]])
    n = F.make_frame("rms", {"track": [1, 2], "band": ["sub", "mid"]},
                     [[-17.0, -12.5], [-20.0, -10.0]])
    cm = F.compare(m, n)
    check("двумерный кадр сравнивается", cm["stats"]["compared"] == 4, str(cm["stats"]))
    check("дельты по клеткам верные",
          {(r["track"], r["band"]): r["d"] for r in cm["rows"]}[(1, "sub")] == 1.0,
          str([(r["track"], r["band"], r["d"]) for r in cm["rows"]]))

    # защита от кривой формы
    try:
        F.make_frame("rms", {"track": [1], "band": ["a", "b"]}, [[1.0]])
        check("кривая форма отвергается", False, "приняло")
    except ValueError:
        check("кривая форма отвергается", True, "")

    try:
        F.compare(a, F.make_frame("peak", {"track": [1]}, [-10.0]))
        check("разные метрики не сравниваются", False, "приняло")
    except ValueError:
        check("разные метрики не сравниваются", True, "")

    check("реестр метрик знает единицы", F.metric_info("partial")["unit"] == "доля", "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
