"""Живая проверка применения плана к FL: эквалайзер без перезагрузки проекта.

Проверяем то, ради чего всё делалось: правка из плана («середина +3 дБ»)
становится реальной полосой эквалайзера, и это видно по подписи самого
плагина, а не по нашим предположениям о его шкалах.

Нужен запущенный FL с тестовым проектом. Рабочий проект не используется.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import apply as A  # noqa: E402
import fl_lib as fl  # noqa: E402

TRACK, SLOT = 1, 0


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    h = fl.state.health()
    print("FL:", h.get("project"), "| дорожек:", h.get("tracks"))
    if not h.get("bridge"):
        print("нет живого управления:", h.get("hint"))
        return 2

    slots = fl.create.live_slots(TRACK)
    occupied = False
    try:
        occupied = bool((slots.get("result") or {}).get("%d:%d" % (TRACK, SLOT), {}).get("state", -1) == 2)
    except Exception:
        pass
    if not occupied:
        ins = fl.create.live_insert(TRACK, SLOT, "Fruity Parametric EQ 2")
        check("эквалайзер вставлен вживую", bool(ins.get("ok")),
              str(ins.get("err", ""))[:60])
    else:
        check("эквалайзер уже стоит в слоте", True, "используем существующий")

    res = fl.apply.eq(TRACK, SLOT, {"mid": 3.0})
    check("правка применилась", bool(res.get("ok")), str(res.get("error", ""))[:60])

    row = (res.get("applied") or [{}])[0]
    gdb = A.parse_db(row.get("gain_text") or "")
    fhz = A.parse_hz(row.get("freq_text") or "")
    check("усиление совпало с планом", gdb is not None and abs(gdb - 3.0) <= 0.6,
          "%s при цели +3.0" % row.get("gain_text"))
    check("частота попала в полосу", fhz is not None and 400 <= fhz <= 1100,
          "%s при цели 700 Гц" % row.get("freq_text"))

    params = fl.act.params(TRACK, SLOT)
    lvl = next((p for p in params if p["name"] == "Band 1 level"), None)
    check("чтение подтверждает значение", lvl is not None
          and abs((A.parse_db(lvl["text"]) or -99) - 3.0) <= 0.6,
          lvl["text"] if lvl else "нет параметра")

    off = fl.apply.eq(TRACK, SLOT, {"mid": 0.0})
    check("нулевая правка не трогает полосы", off.get("applied") == [],
          str(off.get("applied"))[:40])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
