"""wake_keep.py — сделать проход пробуждения и ОСТАВИТЬ файл, чтобы его можно было послушать.

Запись 0.5 с со взведённой дорожкой, файл копируется в _rt_take, шаг записи откатывается,
AutoCreateClip возвращается. Проект не сохраняется.

    python wake_keep.py --track 1 --seconds 0.5 --pos 0.30
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
OUT = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_rt_take"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=0.5)
    ap.add_argument("--pos", type=float, default=0.30)
    a = ap.parse_args()

    before = set(os.listdir(REC)) if os.path.isdir(REC) else set()
    clip = live.clip_flag(0)
    hint = live._undo_hint()
    armed = int(float(live._bridge("mixer.isTrackArmed(%d)" % a.track, timeout=4) or 0))
    print("дорожка", a.track, "| взведена:", armed, "| клип-флаг:", clip.get("before"), flush=True)

    if not armed:
        live._bridge("mixer.armTrack(%d)" % a.track, timeout=4)
    if not int(float(live._bridge("transport.isRecording()", timeout=4) or 0)):
        live._bridge("transport.record()", timeout=4)
    live._bridge("transport.setSongPos(%r)" % a.pos, timeout=4)
    live._bridge("transport.start()", timeout=4)
    time.sleep(a.seconds)
    live._bridge("transport.stop()", timeout=4)
    if int(float(live._bridge("transport.isRecording()", timeout=4) or 0)):
        live._bridge("transport.record()", timeout=4)
    if not armed:
        live._bridge("mixer.armTrack(%d)" % a.track, timeout=4)
    time.sleep(0.8)

    new = sorted(set(os.listdir(REC)) - before)
    print("новых файлов:", len(new), new[:4], flush=True)

    os.makedirs(OUT, exist_ok=True)
    kept = []
    for f in new:
        src = os.path.join(REC, f)
        dst = os.path.join(OUT, "wake_" + os.path.basename(f))
        shutil.move(src, dst)
        kept.append(dst)
        print("  оставил:", dst, os.path.getsize(dst), "байт", flush=True)

    if hint is not None and live._undo_hint() != hint:
        live._bridge("general.undoUpDown(-1)", timeout=4)
        print("шаг записи откачен", flush=True)
    live.clip_flag(int(clip.get("before") or 1))
    print("клип-флаг вернул:", live.clip_flag(), flush=True)
    return 0 if kept else 3


if __name__ == "__main__":
    raise SystemExit(main())
