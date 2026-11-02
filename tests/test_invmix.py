"""Проверка обратной задачи на синтетике с известным ответом.

Собираем микс из стемов с заранее заданными гейнами, берём его метрики как
«цель» и просим решатель восстановить эти гейны, стартуя с нуля. Если он не
находит ответ там, где ответ известен, — доверять ему нельзя.

Допуск: 1.5 дБ (шаг спуска 0.5 дБ, сигнал нелинейный).
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import invmix  # noqa: E402

FS = 22050
DUR = 8.0


def sig(freq, amp, seed=None):
    t = np.arange(int(DUR * FS)) / FS
    fade = np.minimum(1.0, np.minimum(t, DUR - t) * 8)
    if seed is None:
        x = np.sin(2 * np.pi * freq * t)
    else:
        x = np.random.default_rng(seed).standard_normal(len(t))
    return (amp * x * fade).astype(np.float64)


def main() -> int:
    drums = sig(0, 0.10, seed=1)
    bass = sig(55.0, 0.10)
    guitar = sig(220.0, 0.06) + sig(1200.0, 0.02)
    vocal = sig(700.0, 0.04)

    stems = {"drums": drums[:, None], "bass": bass[:, None],
             "guitar": guitar[:, None], "vocal": vocal[:, None]}

    truth = {"drums": 0.0, "bass": -3.0, "guitar": 4.0, "vocal": 0.0}
    target = invmix.metrics(invmix.mix(stems, truth), FS)

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-40s %s" % (name, detail))

    # старт из нуля должен прийти к тем же гейнам
    res = invmix.solve(stems, FS, target, cap_db=6.0, verbose=False)
    errs = {k: res["gains"][k] - truth[k] for k in truth}
    worst = max(errs, key=lambda k: abs(errs[k]))
    check("решатель восстанавливает гейн гитары",
          abs(errs["guitar"]) <= 1.5, "%+.1f дБ" % errs["guitar"])
    check("решатель восстанавливает гейн баса",
          abs(errs["bass"]) <= 1.5, "%+.1f дБ" % errs["bass"])
    check("худшая ошибка в пределах 1.5 дБ",
          abs(errs[worst]) <= 1.5, "%s %+.1f дБ" % (worst, errs[worst]))
    check("расстояние уменьшилось", res["distance"] < res["before"],
          "%.2f → %.2f" % (res["before"], res["distance"]))
    check("итоговый микс близок к цели", res["distance"] < 1.0,
          "%.2f σ" % res["distance"])

    # кап соблюдается
    res_cap = invmix.solve(stems, FS, target, cap_db=1.0, verbose=False)
    check("кап соблюдается", all(abs(v) <= 1.0 + 1e-9 for v in res_cap["gains"].values()),
          str({k: round(v, 1) for k, v in res_cap["gains"].items()}))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    print("восстановлено:", {k: round(v, 1) for k, v in res["gains"].items()},
          "| истина:", truth)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
