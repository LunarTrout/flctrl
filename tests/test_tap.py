"""Живой замер дорожек: уровни и запись в WAV.

Тест живой (нужен FL), поэтому защищён: если открыт не демо-проект — пропуск.
Проверяем, что буферы дорожек читаются и что запись даёт файлы с сигналом.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402
import render_job  # noqa: E402

OUT = r"C:/Users/epicc/tools/fltest/tap_test"


def main() -> int:
    passed, failed, skipped = [], [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    title = render_job.window_title() or ""
    if "With You" not in title:
        print("пропуск: открыт не демо-проект (%s)" % (title[:40] or "нет окна"))
        return 0

    r = api.call("tap.levels", {"tracks": list(range(12)), "seconds": 0.8})
    check("уровни вернулись", bool(r.get("levels")), str(len(r.get("levels") or {})))
    check("кадров в блоке", r.get("frames") in (128, 256, 512, 1024), str(r.get("frames")))
    check("дорожек посчитано", r.get("tracks", 0) >= 12, str(r.get("tracks")))

    alive = [t for t, v in r["levels"].items() if v["rms"] > 1e-6]
    check("есть дорожки со звуком", bool(alive), str(alive[:5]))

    top = max(r["levels"].items(), key=lambda kv: kv[1]["peak"])
    check("пик правдоподобен", 0 < top[1]["peak"] <= 4.0, str(top))

    rec = api.call("tap.record", {"tracks": [0, 2], "seconds": 0.5, "outdir": OUT})
    paths = rec.get("paths") or {}
    check("файлы записаны", len(paths) >= 1, str(list(paths.values())[:2]))
    if paths:
        p = list(paths.values())[0]
        size = os.path.getsize(p)
        check("wav не пустой", size > 44 + 256, str(size))
        with open(p, "rb") as fh:
            head = fh.read(44)
        check("это wav", head[:4] == b"RIFF" and head[8:12] == b"WAVE", str(head[:4]))
        n = (size - 44) // 4
        if n > 8:
            with open(p, "rb") as fh:
                fh.seek(44)
                raw = fh.read(min(n, 4096) * 4)
            vals = struct.unpack("<%df" % (len(raw) // 4), raw)
            check("в файле есть сигнал", max(abs(v) for v in vals) > 1e-6,
                  "%.5f" % max(abs(v) for v in vals))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
