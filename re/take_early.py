"""take_early.py — хук ставится через 3 секунды после старта FL, пока проект ещё грузится.

Порядок: закрыть FL -> запустить с vers1 -> sleep 3 -> attach и хуки -> ждать контроллер
-> wake -> 10 секунд потока -> WAV по объектам.

    python take_early.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

BASE = r"C:\Users\epicc\mcp\flstudio-lib"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "flctrl"))

FL = r"C:\Programs\Fl Studio 2025\FL64.exe"
FLP = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
OUT = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_rt_take"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def pid_of():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                         capture_output=True).stdout.decode("cp866", "replace")
    for line in out.splitlines():
        p = [x.strip('"') for x in line.split('","')]
        if p and p[0].lower() == "fl64.exe":
            return int(p[1])
    return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--wake", type=float, default=2.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--pos", type=float, default=0.30)
    a = ap.parse_args()
    import live
    import rt

    subprocess.run(["taskkill", "/F", "/IM", "FL64.exe"], capture_output=True)
    time.sleep(3)
    subprocess.Popen([FL, FLP], cwd=os.path.dirname(FL))
    time.sleep(3)
    pid = pid_of()
    if not pid:
        log("FL не стартовала")
        return 2
    log("FL pid", pid, "— ставлю хуки во время загрузки")
    try:
        r = rt.RT(pid=pid)
    except Exception as exc:
        log("attach не удался:", str(exc)[:90])
        return 4
    log("хуки стоят")

    t0 = time.time()
    while time.time() - t0 < 90:
        try:
            n = int(float(live._bridge("mixer.getTrackCount()", timeout=3)))
            if n >= 30:
                log("проект готов, дорог", n, "| %.0f с" % (time.time() - t0))
                break
        except Exception:
            pass
        time.sleep(4)
    else:
        log("проект не поднялся за 90 с")
        return 3

    time.sleep(2)
    w = rt.wake(track=1, seconds=a.wake)
    log("wake:", w)

    r.script.exports_sync.begin()
    live._bridge("transport.stop()", timeout=4)
    time.sleep(0.3)
    live._bridge("transport.setSongPos(%r)" % a.pos, timeout=4)
    live._bridge("transport.start()", timeout=4)
    time.sleep(a.seconds)
    info = r.script.exports_sync.end()
    live._bridge("transport.stop()", timeout=4)

    loud = [c for c in info.get("post", []) if c["rms"] > 1e-4]
    log("вызовов %d, объектов %d, с сигналом %d" % (info["calls"], len(info["post"]), len(loud)))

    import soundfile as sf
    os.makedirs(OUT, exist_ok=True)
    for c in sorted(loud, key=lambda c: -c["rms"]):
        x = r.pcm(c["key"], "post")
        if x.size < 1024:
            continue
        p = os.path.join(OUT, "obj_%s.wav" % c["key"].replace("0x", ""))
        sf.write(p, x, 44100, subtype="FLOAT")
        log("  %s rms %.4f peak %.2f кадров %d -> %s" % (
            c["key"], c["rms"], c["peak"], x.shape[0], os.path.basename(p)))
    r.close()
    log("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
