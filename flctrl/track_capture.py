"""track_capture.py -- слышать каждую дорожку отдельно, в реальном времени.

Как работает:
  1. мютит все дорожки, кроме нужной (или соло), через мост FL;
  2. запускает воспроизведение с указанной позиции;
  3. захватывает выход звуковой карты (loopback) N секунд;
  4. возвращает mute/соло в исходное состояние;
  5. сохраняет wav и (по флагу) сразу мерит.

  ../.venv312/Scripts/python.exe track_capture.py --track 9 --seconds 12 --start 60
  ../.venv312/Scripts/python.exe track_capture.py --all --seconds 8 --step 8
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TCP = ("127.0.0.1", 9787)
OUT = os.path.join(HERE, "_cache", "tracks")


def _frac(seconds: float) -> float:
    """Перевести секунды в долю песни (0..1) — в этом виде FL ждёт позицию."""
    try:
        length = float(call("transport.getSongLength(1)"))
    except Exception:
        length = 0.0
    if length <= 0:
        return 0.0
    return max(0.0, min(0.999, float(seconds) / length))


def call(expr: str, timeout: int = 30):
    s = socket.create_connection(TCP, timeout=timeout)
    s.sendall((json.dumps({"op": "call", "cmd": "exec", "params": {"expr": expr}}) + "\n").encode())
    buf = b""
    s.settimeout(timeout)
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    r = json.loads(buf.decode("utf-8", "replace").strip())
    d = r.get("data") or {}
    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or r)[:200])
    return d.get("result")


def track_count() -> int:
    return int(call("mixer.trackCount()"))


def names() -> list:
    n = track_count()
    return [str(call("mixer.getTrackName(%d)" % i)) for i in range(n)]


def capture(seconds: float, fs: int = 48000) -> np.ndarray:
    import soundcard as sc
    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    with mic.recorder(samplerate=fs, channels=2) as rec:
        data = rec.record(numframes=int(seconds * fs))
    return np.asarray(data, dtype=np.float64)


def solo_only(track: int) -> None:
    """Оставить звучащей только нужную дорожку."""
    call("__import__('json').dumps([mixer.muteTrack(i, i != %d) "
         "for i in range(mixer.trackCount())])" % track)


def unmute_all() -> None:
    n = track_count()
    """Снять все mute."""
    call("__import__('json').dumps([mixer.muteTrack(i, False) for i in range(mixer.trackCount())])")


def grab(track: int, seconds: float, start: float, fs: int = 48000) -> str:
    os.makedirs(OUT, exist_ok=True)
    solo_only(track)
    time.sleep(0.3)
    call("transport.setSongPos(%r)" % _frac(start))
    call("transport.start()")
    time.sleep(0.4)
    audio = capture(seconds, fs)
    try:
        call("transport.stop()")
    except Exception:
        pass
    unmute_all()
    import soundfile as sf
    path = os.path.join(OUT, "track_%02d.wav" % track)
    sf.write(path, audio, fs)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    return path if peak > 1e-4 else ""


def grab_any(seconds: float, start: float, track: int | None = None, fs: int = 48000) -> str:
    """Записать отрезок вживую: играем с позиции start и пишем выход карты.

    track=None — весь микс; иначе только указанная дорожка (остальные глушатся).
    Это быстрый путь: время ожидания равно длине отрезка, без рендера проекта.
    """
    os.makedirs(OUT, exist_ok=True)
    if track is not None:
        solo_only(track)
        time.sleep(0.2)
    call("transport.setSongPos(%r)" % _frac(start))
    call("transport.start()")
    time.sleep(0.25)
    audio = capture(seconds, fs)
    try:
        call("transport.stop()")
    except Exception:
        pass
    if track is not None:
        unmute_all()
    import soundfile as sf
    tag = "mix" if track is None else "track_%02d" % track
    path = os.path.join(OUT, "%s_%07.2f.wav" % (tag, start))
    sf.write(path, audio, fs)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--start", type=float, default=60.0)
    ap.add_argument("--step", type=float, default=8.0)
    a = ap.parse_args()

    nm = names()
    print("дорожек: %d" % len(nm))
    targets = range(len(nm)) if a.all else [a.track]
    for i in targets:
        if i is None or i < 0 or i >= len(nm):
            continue
        st = a.start + (i * a.step if a.all else 0)
        try:
            p = grab(i, a.seconds, st)
        except Exception as e:
            print("  %2d %-20s ошибка: %s" % (i, nm[i][:20], str(e)[:80]))
            continue
        print("  %2d %-20s %s" % (i, nm[i][:20], "wav: " + os.path.basename(p) if p else "тишина"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
