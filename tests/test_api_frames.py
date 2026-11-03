"""Интерфейс: кадры из файлов и сравнение картин (офлайн-часть).

Живые пути (/track, set) проверяются отдельно на тестовом проекте, здесь —
измерение и сравнение, которым FL не нужен.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402
import frames as F  # noqa: E402

WAV = r"C:/Users/epicc/tools/fltest/fixtures/demo2.wav"


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    if not os.path.exists(WAV):
        print("нет файла", WAV)
        return 2

    f1 = api.measure({"metric": "bands", "source": WAV, "a": 10, "b": 20})
    check("кадр полос собран", f1["metric"] == "bands" and len(f1["values"][0]) == 28,
          str(len(f1["values"][0])))
    check("единица относительная", "относительно" in f1["unit"], f1["unit"])
    check("ось источника подписана", f1["axes"]["source"] == ["demo2"],
          str(f1["axes"]["source"]))

    f2 = api.measure({"metric": "bands", "source": WAV, "a": 30, "b": 40})
    same = api.compare(f1, f2)
    check("сравнение двух окон того же файла", same["stats"]["compared"] == 28,
          str(same["stats"]["compared"]))
    check("источники в отчёте", same["sources"]["a"] == "demo2", str(same["sources"]))

    check("дельты в разумных пределах", same["stats"]["max_abs"] < 25.0,
          "макс %.1f дБ" % same["stats"]["max_abs"])

    # одинаковые окна дают ноль
    zero = api.compare(f1, api.measure({"metric": "bands", "source": WAV, "a": 10, "b": 20}))
    check("одинаковые окна дают ноль", zero["stats"]["max_abs"] < 0.01,
          "%.4f" % zero["stats"]["max_abs"])

    g = api.measure({"metric": "lufs", "source": WAV, "a": 10, "b": 20})
    check("одномерный кадр (LUFS)", "source" in g["axes"] and isinstance(g["values"][0], float),
          str(g["values"]))

    try:
        api.measure({"metric": "bands", "source": "live"})
        check("живой источник честно отказывает", False, "приняло live")
    except api.ApiError as e:
        check("живой источник честно отказывает", e.code == "not_available", e.code)

    # таблица для логов
    txt = F.to_table(same, limit=3)
    check("таблица печатается", "дельта" in txt and len(txt.splitlines()) >= 3, str(len(txt.splitlines())))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
