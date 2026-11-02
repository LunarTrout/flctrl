"""Проверка реестра проверок и отчёта на синтетике (без FL).

Главное, что проверяем: отчёт обязан обойти ВЕСЬ реестр. Если входов не хватает,
проверка не исчезает, а попадает в «пропущено» с причиной. Иначе оценка тихо
теряет фронты, и «забыл посмотреть» остаётся незамеченным.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import checks   # noqa: E402
import report   # noqa: E402

FS = 22050


def make(path: str, bright: bool, seed: int, dur: float = 8.0) -> str:
    t = np.arange(int(dur * FS)) / FS
    rng = np.random.default_rng(seed)
    base = 0.15 * np.sin(2 * np.pi * 110 * t)
    noise = 0.05 * rng.standard_normal(len(t))
    if bright:
        tone = 0.05 * np.sin(2 * np.pi * 3000 * t)
    else:
        tone = 0.05 * np.sin(2 * np.pi * 300 * t)
    left = base + noise + tone
    right = base + 0.8 * noise + tone
    sf.write(path, np.stack([left, right], axis=1), FS)
    return path


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flrep_")
    mine = make(os.path.join(tmp, "mine.wav"), False, 1)
    ref1 = make(os.path.join(tmp, "ref1.wav"), True, 2)
    ref2 = make(os.path.join(tmp, "ref2.wav"), True, 3)

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    # 1. полный обход реестра: без стемов и пробников часть проверок пропущена
    rep = report.build(mine, [ref1, ref2])
    cov = rep["coverage"]
    check("реестр обойдён целиком", len(cov["ran"]) + len(cov["covered"]) + len(cov["skipped"])
          == len(checks.REGISTRY),
          "%d проверок" % len(checks.REGISTRY))
    check("пропуски названы с причиной", all(s["why"] for s in cov["skipped"]),
          ", ".join(sorted({w for s in cov["skipped"] for w in s["why"]})))
    check("фронты посчитаны", "fronts" in cov["ran"], str(cov["ran"]))
    check("секции посчитаны", "sections" in cov["ran"], "")
    check("роли пропущены без стемов", any(s["id"] == "roles" for s in cov["skipped"]), "")
    check("эффекты ждут пробников",
          all(any(s["id"] == i for s in cov["skipped"])
              for i in ("effects_ir", "effects_thd", "effects_duck")), "")

    md = report.to_markdown(rep)
    check("в отчёте есть раздел покрытия", "## Покрытие проверок" in md, "")
    check("в отчёте есть список непроверенного", "## Не проверено" in md, "")
    check("в отчёте есть действия", "## Что делать" in md, "")

    # 2. с пробниками эффектов покрытие растёт
    ir = os.path.join(tmp, "ir.wav")
    t = np.arange(int(1.0 * FS)) / FS
    sf.write(ir, (10 ** (-3 * t / 0.5) * np.random.default_rng(4).standard_normal(len(t))).astype(float), FS)
    sine = os.path.join(tmp, "sine.wav")
    tt = np.arange(int(1.0 * FS)) / FS
    sf.write(sine, np.sin(2 * np.pi * 440 * tt) + 0.01 * np.sin(2 * np.pi * 1320 * tt), FS)
    ducked = os.path.join(tmp, "duck.wav")
    mod = 0.5 * (1 + np.cos(2 * np.pi * 2 * tt))
    sf.write(ducked, np.sin(2 * np.pi * 220 * tt) * (0.15 + 0.85 * mod), FS)

    rep2 = report.build(mine, [ref1, ref2], impulse=ir, sine=sine, ducked=ducked)
    check("с пробниками покрытие полнее",
          len(rep2["coverage"]["ran"]) > len(rep["coverage"]["ran"]),
          "%d против %d" % (len(rep2["coverage"]["ran"]), len(rep["coverage"]["ran"])))
    check("реверб посчитан", "effects_ir" in rep2["coverage"]["ran"], "")
    check("THD посчитан", "effects_thd" in rep2["coverage"]["ran"], "")
    check("дакинг посчитан", "effects_duck" in rep2["coverage"]["ran"], "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
