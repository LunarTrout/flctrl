"""live.py — звук дорожек микшера в память, без записи на диск.

FL при Disk Recording отдаёт PCM каждой дорожки в WriteFile. Мы перехватываем
этот вызов: копируем байты себе и обнуляем длину, поэтому на диск уходит только
заголовок (76 байт), а звук остаётся в памяти.

    from flctrl import live
    res = live.capture(tracks=[1, 12, 15], seconds=2.0, start=8.0)
    res["tracks"][1]["rms"], res["tracks"][1]["samples"]

Требует: FL запущена, мост 9787, frida (venv312).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

BRIDGE = ("127.0.0.1", 9787)
RECORDED = os.path.join(os.environ.get("USERPROFILE", "C:/"),
                        "Documents", "Image-Line", "FL Studio", "Audio", "Recorded")

SCRIPT = r"""
const files = {};
const calls = [];
let active = false;
let skipping = true;
let total = 0;

const pCreate = Module.getGlobalExportByName("CreateFileW");
Interceptor.attach(pCreate, {
    onEnter(args) { this.path = args[0].readUtf16String(); },
    onLeave(ret) {
        try {
            const h = ret.toInt32();
            if (h !== -1 && this.path && /Recorded/i.test(this.path) && /\.wav$/i.test(this.path)) {
                files[h] = this.path;
            }
        } catch (e) {}
    }
});

const pWrite = Module.getGlobalExportByName("WriteFile");
Interceptor.attach(pWrite, {
    onEnter(args) {
        this.h = args[0].toInt32();
        this.len = args[2].toInt32();
        this.written = args[3];
        this.mute = false;
        if (!active || !(this.h in files) || this.len < 4096) return;
        if (total + this.len <= 64000000) {
            try {
                const data = args[1].readByteArray(this.len);
                if (data) {
                    calls.push({h: this.h, len: this.len,
                                data: Array.from(new Uint8Array(data))});
                    total += this.len;
                }
            } catch (e) {}
        }
        if (skipping) { args[2] = ptr(0); this.mute = true; }
    },
    onLeave(ret) {
        if (this.mute) {
            ret.replace(1);
            try { if (!this.written.isNull()) this.written.writeU32(this.len); } catch (e) {}
        }
    }
});

rpc.exports = {
    start() { active = true; calls.length = 0; total = 0; return true; },
    stop() { active = false; return {n: calls.length, bytes: total}; },
    files() { return files; },
    dump() { return calls; }
};
"""


def _call(expr: str, timeout: float = 8.0):
    s = socket.create_connection(BRIDGE, timeout=timeout)
    s.sendall((json.dumps({"op": "call", "cmd": "exec", "params": {"expr": expr}}) + "\n").encode())
    buf = b""
    s.settimeout(timeout)
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    try:
        data = json.loads(buf.decode("utf-8", "replace"))
        return (data.get("data") or {}).get("result")
    except ValueError:
        return None


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def available() -> dict:
    try:
        return {"bridge": _call("1+1") == 2}
    except OSError as exc:
        return {"bridge": False, "error": str(exc)[:120]}


def capture(tracks=None, seconds: float = 2.0, start: float | None = None,
            fade: bool = True) -> dict:
    """Снять звук дорожек в память. Возвращает PCM и базовые метрики.

    tracks=None — все дорожки, кроме мастера.
    start — позиция в долях (по умолчанию текущая).
    """
    import frida
    import numpy as np

    if not tracks:
        n = int(_call("mixer.getTrackCount()") or 0)
        tracks = list(range(1, n))
    tracks = [int(t) for t in tracks]

    pid = int(subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
        capture_output=True).stdout.decode("cp866", "replace").split('","')[1])

    session = frida.attach(pid)
    script = session.create_script(SCRIPT)
    script.load()

    was_mode = _call("transport.getLoopMode()")
    armed_before = _call("[i for i in range(mixer.getTrackCount()) if mixer.isTrackArmed(i)]")
    armed_before = set(json.loads(armed_before)) if isinstance(armed_before, str) else set()

    script.exports_sync.start()
    try:
        if str(was_mode).strip() not in ("1", "1.0"):
            _call("transport.setLoopMode()")     # song mode: иначе играет паттерн
        for t in tracks:
            if not int(_num(_call("mixer.isTrackArmed(%d)" % t))):
                _call("mixer.armTrack(%d)" % t)
        if not int(_num(_call("transport.isRecording()"))):
            _call("transport.record()")
        if start is not None:
            _call("transport.setSongPos(%r)" % float(start))
        _call("transport.start()")
        time.sleep(seconds)
        _call("transport.stop()")
        if int(_num(_call("transport.isRecording()"))):
            _call("transport.record()")
    finally:
        for t in tracks:
            if t not in armed_before and int(_num(_call("mixer.isTrackArmed(%d)" % t))):
                _call("mixer.armTrack(%d)" % t)
        if str(was_mode).strip() not in ("1", "1.0"):
            _call("transport.setLoopMode()")
        stats = script.exports_sync.stop()
        time.sleep(2.0)                      # дать потоку записи выйти из хука
        files = script.exports_sync.files()
        rows = script.exports_sync.dump()
        script.unload()
        session.detach()

    by_handle: dict[int, bytearray] = {}
    for r in rows:
        if r.get("len", -1) < 4096:
            continue
        by_handle.setdefault(int(r["h"]), bytearray()).extend(bytes(r["data"]))

    names = {}
    for t in tracks:
        try:
            names[t] = str(_call("mixer.getTrackName(%d)" % t)).strip("'\"")
        except Exception:  # noqa: BLE001
            names[t] = "track_%d" % t

    out: dict[int, dict] = {}
    for h, buf in by_handle.items():
        path = files.get(h) or files.get(str(h)) or ""
        base = os.path.basename(path)
        track = None
        for t, nm in names.items():
            if nm and base.endswith(nm + ".wav"):
                track = t
                break
        audio = np.frombuffer(bytes(buf), dtype="<f4")
        if audio.size % 2:
            audio = audio[:-1]
        stereo = audio.reshape(-1, 2)
        mono = stereo.mean(axis=1)
        if fade and mono.size > 64:
            k = min(1024, mono.size // 8)
            ramp = np.linspace(0.0, 1.0, k)
            mono[:k] *= ramp
            mono[-k:] *= ramp[::-1]
        peak = float(np.abs(mono).max()) if mono.size else 0.0
        rms = float(np.sqrt((mono ** 2).mean())) if mono.size else 0.0
        out[track if track is not None else -1] = {
            "file": base, "name": names.get(track, ""), "seconds": round(len(mono) / 44100, 2),
            "rms": round(rms, 5), "peak": round(peak, 4),
            "silent": peak < 1e-5, "mono": mono,
        }

    return {"tracks": out, "chunks": stats.get("n"), "bytes": stats.get("bytes"),
            "seconds": seconds, "start": start, "armed_before": sorted(armed_before)}


def clean(folder: str = RECORDED) -> int:
    """Убрать пустые (76-байтных) файлы, которые FL всё же создаёт."""
    removed = 0
    if not os.path.isdir(folder):
        return 0
    for f in os.listdir(folder):
        if not f.lower().endswith(".wav"):
            continue
        p = os.path.join(folder, f)
        try:
            if os.path.getsize(p) <= 4096:
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed
