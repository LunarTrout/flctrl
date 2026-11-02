"""Живое создание и удаление дорожки микшера — без перезапуска проекта.

Работает только на тестовом проекте; чужую сессию не трогает.
Ловушка, найденная этим тестом: удаление принимает индексы в диапазоне
1..17 (`index 18 вне диапазона 1..17`), поэтому создаём и удаляем в 16.
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "flctrl"))

import fl_lib as fl          # noqa: E402
import render_job            # noqa: E402

TEST_NAME = os.environ.get("FLCTRL_TEST_NAME", "small")
SLOT_INDEX = 16              # проверенный рабочий индекс (1..17)
PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print("ок      %-32s %s" % (name, str(detail)[:70]))
    except Exception as e:
        FAIL.append((name, str(e)[:140]))
        print("ПАДЕНИЕ %-32s %s" % (name, str(e)[:90]))


def count() -> int:
    res = fl.create.live_track_count()
    value = res.get("result") if isinstance(res, dict) else res
    return int(value)


def main() -> int:
    title = render_job.window_title()
    if TEST_NAME.lower() not in title.lower():
        raise SystemExit("открыт не тестовый проект (%r) — тест остановлен" % title)
    print("проект:", title)
    print("attach:", fl.create.live_attach())

    state = {"before": count()}
    print("дорог до опыта:", state["before"])

    def add():
        res = fl.create.live_track_add(index=SLOT_INDEX)
        time.sleep(1.5)
        state["after"] = count()
        assert state["after"] == state["before"] + 1, \
            "было %s, стало %s (ответ %r)" % (state["before"], state["after"], res)
        return "%d → %d" % (state["before"], state["after"])
    check("живое создание дорожки", add)

    def delete():
        res = fl.create.live_track_delete(index=SLOT_INDEX)
        time.sleep(1.5)
        back = count()
        assert back == state["before"], \
            "после удаления %s, ожидалось %s (ответ %r)" % (back, state["before"], res)
        return "%d → %d" % (state["after"], back)
    check("живое удаление дорожки", delete)

    print("\nитог: %d прошло, %d упало" % (len(PASS), len(FAIL)))
    for name, err in FAIL:
        print("  -", name, ":", err)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
