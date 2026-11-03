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


def _hint() -> str:
    return str(call("transport.getSongPosHint()"))


def _bar() -> int:
    """Текущий такт из подсказки FL (формат «такт:доля:тик»)."""
    import re
    try:
        m = re.search(r"(\d+)\s*:", str(_hint()))
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def total_bars() -> int:
    """Сколько всего тактов: позиция в FL задаётся долей от них.

    FL отдаёт номер такта только в подсказке и не сразу после перемотки,
    поэтому после установки позиции ждём и, если номер не пришёл, пробуем
    конец песни.
    """
    import time as _t
    here = float(call("transport.getSongPos()"))
    total = 1
    for probe in (0.5, 0.999):
        call("transport.setSongPos(%r)" % probe)
        _t.sleep(0.25)
        bar = _bar()
        if bar > 1:
            total = int(round((bar - 1) / probe)) + 1
            break
    call("transport.setSongPos(%r)" % here)
    return max(1, total)


def seek_bar(bar: int, tol: int = 1) -> dict:
    """Перевести плейхед на такт N и проверить попадание."""
    total = total_bars()
    frac = max(0.0, min(0.999, (bar - 1) / float(max(1, total - 1))))
    got = 0
    for _ in range(6):
        call("transport.setSongPos(%r)" % frac)
        got = _bar()
        if abs(got - bar) <= tol:
            break
        frac = max(0.0, min(0.999, frac + (bar - got) / float(max(1, total - 1))))
    return {"asked_bar": bar, "bar": got, "fraction": round(frac, 4), "total_bars": total}


def seek_seconds(seconds: float, tol: float = 0.0015) -> float:
    """Поставить плейхед примерно на секунду N (доля песни).

    Точное попадание в секунду невозможно: FL задаёт позицию долей от тактов,
    а при смене темпа связь «секунды ↔ такты» нелинейна. Для точной работы
    используй seek_bar — такт это родная единица FL.
    """
    length = float(call("transport.getSongLength(1)"))
    if length <= 0:
        return 0.0
    target = max(0.0, min(0.999, float(seconds) / length))
    frac = target
    for _ in range(5):
        call("transport.setSongPos(%r)" % frac)
        pos = float(call("transport.getSongPos()"))
        err = target - pos
        if abs(err) <= tol:
            break
        frac = max(0.0, min(0.999, frac + err))
    return frac


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


def capture(seconds: float, fs: int = 48000, device: str | None = None) -> np.ndarray:
    """Записать отрезок с выхода.

    device — имя устройства (например, виртуального кабеля). По умолчанию
    пишем с того, что сейчас является устройством воспроизведения.
    """
    import soundcard as sc
    if device:
        name = device
    else:
        name = str(sc.default_speaker().name)
    mic = sc.get_microphone(id=name, include_loopback=True)
    with mic.recorder(samplerate=fs, channels=2) as rec:
        data = rec.record(numframes=int(seconds * fs))
    return np.asarray(data, dtype=np.float64)


def solo_only(track: int) -> None:
    """Оставить звучащей только нужную дорожку.

    Мастер (0) не глушим никогда: если замьютить сумму, на выходе будет тишина.
    """
    call("__import__('json').dumps([mixer.muteTrack(i, (i != %d) and (i != 0)) "
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


def capture_all(seconds: float, start: float, outdir: str,
                tracks: list[int] | None = None, fs: int = 48000) -> dict:
    """Снять звук всех дорожек по очереди и мастер для сверки.

    Возвращает пути и статистику. Каждая дорожка пишется отдельным WAV,
    мастер — как есть (без соло), чтобы можно было проверить сумму.
    """
    import soundfile as sf
    import numpy as np

    os.makedirs(outdir, exist_ok=True)
    n = track_count()
    targets = tracks if tracks is not None else list(range(1, n))
    out: dict[int, dict] = {}

    for t in targets:
        unmute_all()
        solo_only(t)
        time.sleep(0.2)
        call("transport.setSongPos(%r)" % _frac(start))
        call("transport.start()")
        time.sleep(0.3)
        audio = capture(seconds, fs)
        try:
            call("transport.stop()")
        except Exception:  # noqa: BLE001
            pass
        unmute_all()
        path = os.path.join(outdir, "track_%02d.wav" % t)
        sf.write(path, audio, fs)
        mono = np.asarray(audio).mean(axis=1) if np.asarray(audio).ndim == 2 else np.asarray(audio)
        out[t] = {"path": path, "peak": round(float(np.abs(mono).max()), 4),
                  "rms": round(float(np.sqrt((mono ** 2).mean())), 5),
                  "silent": bool(np.abs(mono).max() < 1e-4)}

    unmute_all()
    call("transport.setSongPos(%r)" % _frac(start))
    call("transport.start()")
    time.sleep(0.3)
    master = capture(seconds, fs)
    try:
        call("transport.stop()")
    except Exception:  # noqa: BLE001
        pass
    master_path = os.path.join(outdir, "master.wav")
    sf.write(master_path, master, fs)
    return {"tracks": out, "master": master_path, "dir": outdir,
            "seconds": seconds, "start": start}


def verify_sum(result: dict, fs: int = 48000) -> dict:
    """Сверить сумму снятых дорожек с мастером: форма спектра и корреляция."""
    import soundfile as sf
    import numpy as np

    tracks = result.get("tracks") or {}
    used, acc = [], None
    for t, info in tracks.items():
        if info.get("silent"):
            continue
        x, _ = sf.read(info["path"], always_2d=True)
        m = x.mean(axis=1)
        n = min(len(m), int(fs * 4))
        m = m[:n]
        acc = m if acc is None else acc[:n] + m[:min(len(acc), n)]
        used.append(t)
    if acc is None:
        return {"error": "нет непустых дорожек"}
    master, _ = sf.read(result["master"], always_2d=True)
    mm = master.mean(axis=1)[:len(acc)]

    a = acc - acc.mean()
    b = mm - mm.mean()
    corr = float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    # грубая сверка спектра: 8 полос
    def bands(x):
        n = 8192
        spec = np.abs(np.fft.rfft(x[:n] * np.hanning(min(n, len(x))))) ** 2
        f = np.fft.rfftfreq(n, 1 / fs)
        edges = [20, 100, 300, 800, 2000, 5000, 10000, 16000, 24000]
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (f >= lo) & (f < hi)
            out.append(float(10 * np.log10(spec[m].sum() + 1e-12)))
        return out

    bs, bm = bands(acc), bands(mm)
    diff = [round(s - m, 1) for s, m in zip(bs, bm)]
    return {"tracks_used": len(used), "track_ids": sorted(used),
            "corr": round(corr, 3), "band_diff_db": diff,
            "sum_rms": round(float(np.sqrt((acc ** 2).mean())), 4),
            "master_rms": round(float(np.sqrt((mm ** 2).mean())), 4)}
