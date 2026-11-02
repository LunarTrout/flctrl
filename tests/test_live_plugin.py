"""Живой цикл на чистом проекте: вставка, проверка, удаление — без перезапуска.

Работает только если открыт тестовый проект; чужую сессию не трогает.
Прицепляется к уже запущенной FL (attach), ничего не переоткрывает.
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
TRACK, SLOT = 1, 0
# По имени FL ищет только в Plugins\Fruity; для VST задай путь к .fst.
PLUGIN = os.environ.get("FLCTRL_TEST_PLUGIN", "Fruity Limiter")
FST = os.environ.get("FLCTRL_TEST_FST", "")
EMPTY = 0xFFFFFFFF
PASS, FAIL = [], []


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    return True


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print("ок      %-32s %s" % (name, str(detail)[:70]))
    except Exception as e:
        FAIL.append((name, str(e)[:140]))
        print("ПАДЕНИЕ %-32s %s" % (name, str(e)[:90]))


def guard() -> None:
    """Не трогаем чужой проект: работаем только если открыт тестовый."""
    title = render_job.window_title()
    if TEST_NAME.lower() not in title.lower():
        raise SystemExit("открыт не тестовый проект (%r) — тест остановлен" % title)


def slot(track: int = TRACK, num: int = SLOT) -> dict:
    """Состояние слота из памяти движка (карта приходит словарём 'дорожка:слот')."""
    res = fl.create.live_slots(track)
    data = res.get("result") if isinstance(res, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("карта слотов не пришла: %r" % (res,))
    return data.get("%d:%d" % (track, num)) or {}


def busy(s: dict) -> bool:
    return int(s.get("state", EMPTY)) != EMPTY


def main() -> int:
    guard()
    print("проект:", render_job.window_title())
    try:
        print("attach:", fl.create.live_attach())
    except Exception as e:
        raise SystemExit("attach не удался: %s" % str(e)[:200])

    def empty_slot():
        try:
            fl.create.live_delete(TRACK, SLOT)
        except Exception:
            pass
        time.sleep(1.0)
        s = slot()
        _assert(not busy(s), "слот занят перед тестом: %r" % s)
        return "свободен"
    check("подготовка: слот пуст", empty_slot)

    def insert():
        fl.create.live_insert(TRACK, SLOT, PLUGIN)
        time.sleep(1.5)
        s = slot()
        _assert(busy(s), "слот пуст после вставки")
        return "state %s, параметров %s" % (s.get("state"), s.get("params"))
    check("живая вставка", insert)

    def delete():
        fl.create.live_delete(TRACK, SLOT)
        time.sleep(1.5)
        s = slot()
        _assert(not busy(s), "слот всё ещё занят: %r" % s)
        return "state %s" % s.get("state")
    check("живое удаление", delete)

    if FST:
        def vst():
            fl.create.live_insert(TRACK, SLOT, FST)
            time.sleep(2.0)
            s = slot()
            _assert(busy(s), "VST не встал в слот")
            return "обёртка %s" % str(s.get("path", ""))[-34:]
        check("VST через .fst", vst)

        def vst_out():
            fl.create.live_delete(TRACK, SLOT)
            time.sleep(1.5)
            _assert(not busy(slot()), "VST остался в слоте")
            return "слот свободен"
        check("удаление VST", vst_out)

    print("\nитог: %d прошло, %d упало" % (len(PASS), len(FAIL)))
    for name, err in FAIL:
        print("  -", name, ":", err)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
