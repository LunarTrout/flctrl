"""Проверка измерителей эффектов на сигналах с ИЗВЕСТНЫМ ответом.

Если инструмент не восстанавливает заранее заданные RT60, THD и частоту
дакинга, ему нельзя доверять ни в отчёте, ни в решениях.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import effects  # noqa: E402

FS = 48000


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-42s %s" % (name, detail))

    # 1. импульсный отклик: экспоненциальный спад с T60 = 1.0 с
    t = np.arange(int(4 * FS)) / FS
    ir = np.zeros_like(t)
    ir[0] = 1.0
    t60 = 1.0
    decay = 10 ** (-3 * t / t60)          # −60 дБ за t60
    ir += 0.5 * decay * np.random.default_rng(0).standard_normal(len(t)) * (t > 0)
    m = effects.ir_metrics(ir, FS)
    check("RT60 близок к 1.0 с", abs(m["rt60"] - t60) < 0.25, "%.2f с" % m["rt60"])

    # 2. синус с известным THD: 1 кГц + 3-я гармоника −40 дБ (1 %)
    f0 = 1000.0
    s = np.sin(2 * np.pi * f0 * t[: FS]) + 10 ** (-40 / 20) * np.sin(2 * np.pi * 3 * f0 * t[: FS])
    th = effects.thd(s, FS, f0)
    check("THD ≈ 1 %", abs(th["thd_pct"] - 1.0) < 0.2, "%.2f %%" % th["thd_pct"])
    check("основная частота найдена", abs(th["f0"] - f0) < 5, "%.0f Гц" % th["f0"])

    # 3. дакинг: огибающая с провалами каждые 0.5 с (2 Гц)
    dur = 4.0
    tt = np.arange(int(dur * FS)) / FS
    carrier = np.sin(2 * np.pi * 220 * tt)
    mod = 0.5 * (1 + np.cos(2 * np.pi * 2.0 * tt))     # 1 → 0 → 1
    ducked = carrier * (0.15 + 0.85 * mod)
    d = effects.ducking(ducked, FS)
    check("частота дакинга ≈ 2 Гц", abs(d["rate_hz"] - 2.0) < 0.35, "%.2f Гц" % d["rate_hz"])
    check("глубина дакинга измерена", d["depth_db"] > 6.0, "%.1f дБ" % d["depth_db"])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
