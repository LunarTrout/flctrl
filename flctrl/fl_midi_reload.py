"""fl_midi_reload.py -- перезагрузка MIDI-устройств и контроллерных скриптов в живом FL.

Аналог F10 -> MIDI settings -> "Refresh device list" (+ переподключение скрипта),
но без кликов по диалогу и без перезапуска FL.

Движковая функция
-----------------
    FLEngine_x64.dll + 0xC77830        (FL Studio 25.2.5.5319, sha256 919c9e47...)
    procedure RefreshMIDI(full: Byte); // единственный аргумент — байт в CL
    байт-сигнатура:
        55 53 48 83 EC 48 48 8B EC 48 C7 45 20 00 00 00 00 48 89 6D 28 88 4D 60

Что делает (статически прослежено):
    1. BeginWaitCursor (0x3F9B60), guard повторного входа;
    2. 0xCCCE10 — сброс глобальных флагов MIDI;
    3. 0xC75220(-1) — обход всех устройств, закрытие портов;
    4. 0xC77360 — пере-синхронизация настроек устройств (ключ 'ScriptFolder');
    5. 0xC75A10 — открытие устройств заново + переподключение скриптов
       ('New MIDI device detected', '... has been detected and connected with script: ');
    6. 0xE5FFC0 / 0xE60B90 / 0xE62CE0 — обновление списков в UI;
    7. при full=1 -> 0xAAEEB0 (перестройка списка входов), затем 0xAAEF10;
    8. EndWaitCursor (0x3F9C00).

Как вызывать
------------
    python fl_midi_reload.py                 # full=1, порт демона 9790
    python fl_midi_reload.py --full 0        # как при старте FL (без перестройки списков)
    python fl_midi_reload.py --verify        # после вызова проверить мост 9787

Из библиотеки:

    from fl_midi_reload import reload_midi_scripts
    reload_midi_scripts()          # -> {'ok': True, 'data': '...'}

Ограничение: нужен уже поднятый демон стенда (fl_live_insert.py daemon) — этот
скрипт НЕ запускает FL, только шлёт op=call в существующий процесс.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fl_live_insert import send, daemon_alive, live_state  # noqa: E402

#: FLEngine_x64.dll + RVA: procedure RefreshMIDI(full: Byte)
REFRESH_MIDI_RVA = "0xC77830"

#: обработчик кнопки "Refresh device list" (mov cl,1; call 0xC77830; ret)
RESCAN_BTN_RVA = "0xE545C0"

#: обработчик кнопки "Update MIDI scripts" — онлайн-апдейтер, требует инстанс формы
#: опций (Self+0xF50), поэтому headless не используется
UPDATE_SCRIPTS_BTN_RVA = "0xE56F50"


def reload_midi_scripts(port: int = 9790, full: int = 1, timeout: float = 180) -> dict:
    """Перезагрузить MIDI-устройства и заново подключить контроллерные скрипты.

    full=1 — как кнопка "Refresh device list" (полный проход, включая перестройку
             списков устройств);
    full=0 — как при старте FL / из хоста скриптов.

    Возвращает ответ демона: {'ok': True, 'data': '<ret>'}; исключение — если
    демона нет или вызов упал.
    """
    if not daemon_alive(port):
        raise RuntimeError("демон стенда не слушает 127.0.0.1:%d "
                           "(запусти: python fl_live_insert.py daemon)" % port)
    req = {"op": "call", "rva": REFRESH_MIDI_RVA, "args": [int(full)],
           "timeout": timeout}
    r = send(req, port=port, timeout=timeout + 30)
    if not r.get("ok"):
        raise RuntimeError("вызов не удался: %s" % str(r)[:400])
    return r


def verify_bridge(timeout: float = 30) -> dict | None:
    """Проверить, что контроллер отвечает по мосту 9787. None — если мост молчит."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return live_state()
        except Exception:
            time.sleep(2)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9790)
    ap.add_argument("--full", type=int, default=1, choices=(0, 1))
    ap.add_argument("--verify", action="store_true",
                    help="после вызова опросить мост 9787 (контроллер отвечает?)")
    ap.add_argument("--timeout", type=float, default=180)
    a = ap.parse_args(argv)

    before = verify_bridge(5) if a.verify else None
    t0 = time.time()
    r = reload_midi_scripts(a.port, a.full, a.timeout)
    print(json.dumps({"ok": True, "rva": REFRESH_MIDI_RVA, "full": a.full,
                      "secs": round(time.time() - t0, 2), "data": r.get("data")},
                     ensure_ascii=False))
    if a.verify:
        after = verify_bridge(45)
        print(json.dumps({"bridge_before": before is not None,
                          "bridge_after": after is not None,
                          "state": after}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
