"""Живая вставка плагина на чистом проекте: без перезапуска FL.

Проверяет то, ради чего всё затевалось: плагин появляется в слоте и исчезает
из него прямо в работающей FL. Тест откажется работать, если открыт не
тестовый проект — чужую сессию он не тронет.
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
PLUGIN = os.environ.get("FLCTRL_TEST_PLUGIN", "FabFilter Pro-Q 4")
PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print("ок      %-34s %s" % (name, str(detail)[:60]))
    except Exception as e:
        FAIL.append((name, str(e)[:140]))
        print("ПАДЕНИЕ %-34s %s" % (name, str(e)[:90]))


def guard() -> None:
    """Не трогаем чужой проект: работаем только если открыт тестовый."""
    title = render_job.window_title()
    if TEST_NAME.lower() not in title.lower():
        raise SystemExit("открыт не тестовый проект (%r) — тест остановлен" % title)


def main() -> int:
    guard()
    print("проект:", render_job.window_title())

    def empty_slot():
        try:
            fl.create.live_delete(TRACK, SLOT)
        except Exception:
            pass
        time.sleep(1.2)
        name = fl.create.live_slots(TRACK)
        assert not name or not any(s.get("busy") for s in name if s.get("slot") == SLOT), \
            "слот не пуст перед тестом: %r" % name
        return "слот свободен"
    check("подготовка: слот пуст", empty_slot)

    def insert():
        res = fl.create.live_insert(TRACK, SLOT, PLUGIN)
        time.sleep(1.5)
        slots = fl.create.live_slots(TRACK)
        busy = [s for s in slots if s.get("slot") == SLOT and s.get("busy")]
        assert busy, "после вставки слот пуст: %r" % slots
        return "занят: %s" % busy[0].get("name", "?")
    check("живая вставка", insert)

    def alive():
        got = fl.state.plugins(TRACK)
        name = (got or {}).get(SLOT) or str(got)
        assert PLUGIN.split()[0].lower() in str(name).lower(), "имя не совпало: %r" % name
        return name
    check("плагин виден через API", alive)

    def delete():
        fl.create.live_delete(TRACK, SLOT)
        time.sleep(1.5)
        slots = fl.create.live_slots(TRACK)
        busy = [s for s in slots if s.get("slot") == SLOT and s.get("busy")]
        assert not busy, "слот всё ещё занят: %r" % busy
        return "слот освобождён"
    check("живое удаление", delete)

    print("\nитог: %d прошло, %d упало" % (len(PASS), len(FAIL)))
    for name, err in FAIL:
        print("  -", name, ":", err)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
