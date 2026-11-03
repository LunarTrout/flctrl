"""Повторы вызовов моста: чтения можно, записи нельзя.

Мост отвечает таймаутом, когда FL занята (рендер, открытие проекта), и это
нормальный шум, а не смерть связи. Поэтому чтение повторяется, а запись — нет:
неизвестно, дошло ли действие до движка, и повтор может сделать его дважды.

Проверяем на подставном мосту, без FL.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

# Подставной мост: считает вызовы и падает заданное число раз
calls = {"n": 0, "fail": 0, "value": 42}


def fake_live(expr):
    calls["n"] += 1
    if calls["n"] <= calls["fail"]:
        raise TimeoutError("мост не ответил")
    return calls["value"]


mod = types.ModuleType("q")
mod.live = fake_live
sys.modules["q"] = mod

import fl_lib  # noqa: E402

S = fl_lib._State


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    check("чтение распознано", S._is_read("mixer.getTrackVolume(1)"), "")
    check("запись распознана", not S._is_read("mixer.setTrackVolume(1, 0.5)"), "")
    check("вставка не считается чтением", not S._is_read("plugins.insert(1,0)"), "")
    check("список дорожек — чтение", S._is_read("str([n for n in dir(mixer)])"), "")

    calls.update(n=0, fail=2, value=7)
    v = S._live("mixer.getTrackVolume(1)", heal=False)
    check("чтение повторено и вернуло значение", v == 7, "вызовов %d" % calls["n"])
    check("понадобилось ровно 3 попытки", calls["n"] == 3, "вызовов %d" % calls["n"])

    calls.update(n=0, fail=5, value=7)
    try:
        S._live("mixer.getTrackVolume(1)", heal=False)
        ok = False
    except TimeoutError:
        ok = True
    check("после всех попыток ошибка пробрасывается", ok, "вызовов %d" % calls["n"])

    calls.update(n=0, fail=5, value=1)
    try:
        S._live("mixer.setTrackVolume(1, 0.5)", heal=False)
        ok = False
    except TimeoutError:
        ok = True
    check("запись не повторяется", ok and calls["n"] == 1, "вызовов %d" % calls["n"])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
