"""stems_take.py — записать ВСЕ дорожки микшера на N секунд и сохранить файлы.

Диск-рекординг FL пишет по файлу на каждую взведённую дорожку. Файлы переносятся в
_stems_take, шаг записи откатывается, AutoCreateClip возвращается, проект НЕ сохраняется.

    python stems_take.py --seconds 10 --pos 0.30
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

BASE = r"C:\Users\epicc\mcp\flstudio-lib"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "flctrl"))

import live  # noqa: E402  # pyright: ignore[reportMissingImports]

REC = os.path.expanduser(r"~\Documents\Image-Line\FL Studio\Audio\Recorded")
OUT = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_stems_take"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--pos", type=float, default=0.30)
    a = ap.parse_args()

    n = int(float(live._bridge("mixer.getTrackCount()", timeout=4)))
    tracks = list(range(1, n))
    print("дорог:", n, "| пишем", len(tracks), "| секунд", a.seconds, flush=True)

    before = set(os.listdir(REC)) if os.path.isdir(REC) else set()
    clip = live.clip_flag(0)
    hint = live._undo_hint()

    armed_before = [t for t in tracks if int(float(live._bridge("mixer.isTrackArmed(%d)" % t, timeout=3) or 0))]
    for t in tracks:
        if t not in armed_before:
            live._bridge("mixer.armTrack(%d)" % t, timeout=3)
    print("взведено: было", len(armed_before), "стало", flush=True)
    live._bridge("transport.setSongPos(%r)" % a.pos, timeout=4)
    live._bridge("transport.record()", timeout=4)
    live._bridge("transport.start()", timeout=4)
    t0 = time.time()
    time.sleep(a.seconds)
    live._bridge("transport.stop()", timeout=5)
    if int(float(live._bridge("transport.isRecording()", timeout=4) or 0)):
        live._bridge("transport.record()", timeout=4)
    for t in tracks:
        if t not in armed_before:
            live._bridge("mixer.armTrack(%d)" % t, timeout=3)
    print("запись %.1f с, ждём файлы" % (time.time() - t0), flush=True)
    time.sleep(2.0)

    new = sorted(set(os.listdir(REC)) - before)
    os.makedirs(OUT, exist_ok=True)
    kept = 0
    total = 0
    for f in new:
        src = os.path.join(REC, f)
        try:
            size = os.path.getsize(src)
            shutil.move(src, os.path.join(OUT, f))
            kept += 1
            total += size
        except OSError:
            pass
    print("файлов: %d, всего %.1f МБ" % (kept, total / 1048576), flush=True)

    if hint is not None and live._undo_hint() != hint:
        live._bridge("general.undoUpDown(-1)", timeout=4)
        print("шаг записи откачен", flush=True)
    live.clip_flag(int(clip.get("before") or 1))
    print("клип-флаг:", live.clip_flag().get("value"), "| файлы:", OUT, flush=True)
    return 0 if kept else 3


if __name__ == "__main__":
    raise SystemExit(main())
