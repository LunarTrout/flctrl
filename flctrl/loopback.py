"""loopback.py — запись того, что реально звучит, с выхода системы (WASAPI).

Зачем: буферы дорожек внутри FL опросом читаются неровно (пул буферов, блоки не
по порядку), а хук в аудиоколбэке вешает RPC. Но звук, который слышно, отдаёт
аудиодрайвер — его и пишем: непрерывно, без склеек, без вмешательства в FL.

Это МАСТЕР (сумма всех дорожек), не отдельные каналы: разделить уже сведённый
выход на дорожки нельзя.

    python -m flctrl.loopback --seconds 6 --out C:/tmp/take.wav --mp3
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys


def available() -> dict:
    try:
        import soundcard as sc
    except ImportError:
        return {"ok": False, "error": "нужен пакет soundcard (uv pip install soundcard)"}
    try:
        spk = sc.default_speaker()
        return {"ok": True, "speaker": spk.name}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def record(seconds: float = 5.0, path: str | None = None, rate: int = 48000,
           channels: int = 2) -> dict:
    """Писать выход системы в WAV (float32). Возвращает путь и статистику."""
    import numpy as np
    import soundcard as sc

    spk = sc.default_speaker()
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    if mic is None:
        return {"error": "нет loopback-устройства для %s" % spk.name}

    with mic.recorder(samplerate=rate, channels=channels) as rec:
        data = rec.record(numframes=int(rate * seconds))
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[:, None]

    path = path or os.path.join(os.environ.get("TEMP", "."), "loopback.wav")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = audio.astype("<f4").tobytes()
    header = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, audio.shape[1], rate,
                                    rate * audio.shape[1] * 4, audio.shape[1] * 4, 32)
    header += b"data" + struct.pack("<I", len(raw))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(raw)

    mono = audio.mean(axis=1)
    return {"path": path, "seconds": round(len(audio) / rate, 2),
            "rate": rate, "channels": int(audio.shape[1]),
            "rms": round(float(np.sqrt((mono ** 2).mean())), 5),
            "peak": round(float(np.abs(mono).max()), 4),
            "silent": bool(np.abs(mono).max() < 1e-5),
            "speaker": spk.name}


def to_mp3(wav: str, bitrate: str = "192k") -> str | None:
    ff = os.path.expandvars(r"%LOCALAPPDATA%/hermes/tools/ffmpeg-9.0.1-win32-x64/bin/ffmpeg.exe")
    if not os.path.exists(ff):
        return None
    mp3 = os.path.splitext(wav)[0] + ".mp3"
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", wav, "-b:a", bitrate, mp3],
                       capture_output=True, text=True)
    return mp3 if os.path.exists(mp3) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "loopback.wav"))
    ap.add_argument("--mp3", action="store_true")
    a = ap.parse_args(argv)

    info = available()
    if not info.get("ok"):
        print("нельзя:", info.get("error"))
        return 2
    res = record(a.seconds, a.out)
    if res.get("error"):
        print("ошибка:", res["error"])
        return 3
    print("записано %.2f с, rms %.5f, пик %.3f -> %s"
          % (res["seconds"], res["rms"], res["peak"], res["path"]))
    if a.mp3:
        print("mp3:", to_mp3(res["path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
