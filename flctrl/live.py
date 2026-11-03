"""live.py — звук дорожек микшера в память, без записи на диск.

FL при Disk Recording отдаёт PCM каждой дорожки в WriteFile. Мы перехватываем
этот вызов: копируем байты себе и обнуляем длину — на диск уходит только
76-байтный заголовок, звук остаётся в памяти.

Хук живёт в отдельном долгоживущем процессе (`tools-local/hook_daemon.py`):
один attach на сессию, выгрузки после каждого снимка нет (иначе FL иногда
падает — поток записи оказывается внутри хука в момент выгрузки).

    from flctrl import live
    res = live.capture(tracks=[1, 12], seconds=2.0, start=8.0)
    res["tracks"]["1"]["rms"]; res["frames"]["1"]["values"]   # лента время x полоса

Нужен сам звук — `live.dump(...)` (WAV/MP3), по явному запросу.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time

BRIDGE = ("127.0.0.1", 9787)
DAEMON = ("127.0.0.1", 9788)
RECORDED = os.path.join(os.environ.get("USERPROFILE", "C:/"),
                        "Documents", "Image-Line", "FL Studio", "Audio", "Recorded")
DAEMON_SCRIPT = os.environ.get(
    "FLCTRL_HOOK_DAEMON",
    r"C:/Users/epicc/mcp/flstudio-mcp/tools-local/hook_daemon.py")

SCRIPT = r"""
const files = {};
const calls = [];
let active = false;
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
        args[2] = ptr(0);            // на диск не пишем
        this.mute = true;
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


# --------------------------------------------------------------------- мост FL

def _bridge(expr: str, timeout: float = 8.0):
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
        return (json.loads(buf.decode("utf-8", "replace")).get("data") or {}).get("result")
    except ValueError:
        return None


_call = _bridge          # старое имя, чтобы не ломать вызывающих


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fl_pid() -> int | None:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                         capture_output=True).stdout.decode("cp866", "replace")
    parts = out.split('","')
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def available() -> dict:
    try:
        return {"bridge": _bridge("1+1") == 2}
    except OSError as exc:
        return {"bridge": False, "error": str(exc)[:120]}


# ------------------------------------------------------------------ демон хука

def _daemon_send(payload: dict, timeout: float = 180.0) -> dict:
    s = socket.create_connection(DAEMON, timeout=5.0)
    try:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        s.sendall(struct.pack("<I", len(raw)) + raw)
        head = s.recv(4)
        if len(head) < 4:
            raise ConnectionError("демон закрыл соединение")
        size = struct.unpack("<I", head)[0]
        buf = b""
        s.settimeout(timeout)
        while len(buf) < size:
            chunk = s.recv(min(1 << 20, size - len(buf)))
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8", "replace"))
    finally:
        s.close()


def daemon_status() -> dict:
    try:
        res = _daemon_send({"cmd": "ping"}, timeout=3.0)
        return {"running": bool(res.get("ok")), "pid": res.get("pid")}
    except (OSError, ValueError) as exc:
        return {"running": False, "error": str(exc)[:120]}


def ensure_daemon(timeout: float = 30.0) -> dict:
    """Поднять демон хука; перецепить, если FL перезапустилась."""
    pid = fl_pid()
    if not pid:
        return {"ok": False, "error": "FL не запущена"}
    st = daemon_status()
    if st.get("running"):
        if st.get("pid") == pid:
            return {"ok": True, "pid": pid, "started": False}
        try:
            _daemon_send({"cmd": "quit"}, timeout=10.0)      # смотрит в мёртвую FL
        except OSError:
            pass
    if not os.path.isfile(DAEMON_SCRIPT):
        return {"ok": False, "error": "нет файла демона: %s" % DAEMON_SCRIPT}
    subprocess.Popen([sys.executable, DAEMON_SCRIPT],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = daemon_status()
        if st.get("running") and st.get("pid") == pid:
            return {"ok": True, "pid": pid, "started": True}
        time.sleep(0.5)
    return {"ok": False, "error": "демон не поднялся за %.0f с" % timeout}


def stop_daemon() -> dict:
    try:
        return _daemon_send({"cmd": "quit"}, timeout=15.0)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:120]}


# ---------------------------------------------------------------------- снимок

def capture(tracks=None, seconds: float = 2.0, start: float | None = None,
            fade: bool = True, want_audio: bool = False) -> dict:
    """Снять звук дорожек: метрики + лента (время x 28 полос) на каждую.

    want_audio=True — оставить сэмплы в ``_samples`` (нужно для dump(),
    через API наружу не отдаётся).
    """
    import numpy as np

    if not fl_pid():
        return {"error": "FL не запущена", "tracks": {}, "frames": {}}
    d = ensure_daemon()
    if not d.get("ok"):
        return {"error": d.get("error", "демон недоступен"), "tracks": {}, "frames": {}}

    if not tracks:
        n = int(_num(_bridge("mixer.getTrackCount()")))
        tracks = list(range(1, n))
    tracks = [int(t) for t in tracks]

    was_mode = _bridge("transport.getLoopMode()")
    armed_raw = _bridge("[i for i in range(mixer.getTrackCount()) if mixer.isTrackArmed(i)]")
    try:
        armed_before = set(json.loads(armed_raw)) if isinstance(armed_raw, str) else set()
    except ValueError:
        armed_before = set()

    _daemon_send({"cmd": "start"}, timeout=10.0)
    error = None
    stats: dict = {}
    payload: dict = {}
    try:
        if str(was_mode).strip() not in ("1", "1.0"):
            _bridge("transport.setLoopMode()")       # song mode: в pattern играет паттерн
        for t in tracks:
            if not int(_num(_bridge("mixer.isTrackArmed(%d)" % t))):
                _bridge("mixer.armTrack(%d)" % t)
        if not int(_num(_bridge("transport.isRecording()"))):
            _bridge("transport.record()")
        if start is not None:
            _bridge("transport.setSongPos(%r)" % float(start))
        _bridge("transport.start()")
        time.sleep(seconds)
        _bridge("transport.stop()")
        if int(_num(_bridge("transport.isRecording()"))):
            _bridge("transport.record()")
    except OSError as exc:
        error = "мост FL: %s" % str(exc)[:120]
    finally:
        for t in tracks:
            try:
                if t not in armed_before and int(_num(_bridge("mixer.isTrackArmed(%d)" % t))):
                    _bridge("mixer.armTrack(%d)" % t)
            except OSError:
                pass
        try:
            if str(was_mode).strip() not in ("1", "1.0"):
                _bridge("transport.setLoopMode()")
        except OSError:
            pass
        try:
            payload = _daemon_send({"cmd": "stop", "settle": 0.3})
            stats = payload.get("stats") or {}
        except (OSError, ValueError) as exc:
            error = (error + "; " if error else "") + "демон: %s" % str(exc)[:120]

    by_handle: dict[int, bytearray] = {}
    for r in payload.get("calls") or []:
        if r.get("len", -1) < 4096:
            continue
        by_handle.setdefault(int(r["h"]), bytearray()).extend(bytes(r["data"]))
    files = payload.get("files") or {}

    names = {}
    for t in tracks:
        try:
            names[t] = str(_bridge("mixer.getTrackName(%d)" % t)).strip("'\"")
        except OSError:
            names[t] = "track_%d" % t

    out: dict[int, dict] = {}
    frames: dict[str, dict] = {}
    samples: dict[int, object] = {}
    for h, buf in by_handle.items():
        base = os.path.basename(files.get(h) or files.get(str(h)) or "")
        track = next((t for t, nm in names.items() if nm and base.endswith(nm + ".wav")), None)
        audio = np.frombuffer(bytes(buf), dtype="<f4")
        if audio.size % 2:
            audio = audio[:-1]
        stereo = audio.reshape(-1, 2)
        mono = stereo.mean(axis=1).copy()
        if fade and mono.size > 64:
            k = min(1024, mono.size // 8)
            mono[:k] *= np.linspace(0.0, 1.0, k)
            mono[-k:] *= np.linspace(1.0, 0.0, k)
        peak = float(np.abs(mono).max()) if mono.size else 0.0
        rms = float(np.sqrt((mono ** 2).mean())) if mono.size else 0.0
        key = track if track is not None else -1
        item = {"file": base, "name": names.get(track, ""),
                "seconds": round(len(mono) / 44100, 2),
                "rms": round(rms, 5), "peak": round(peak, 4),
                "silent": peak < 1e-5}
        try:
            frames[str(key)] = make_frame(item, stereo)
        except Exception as exc:  # noqa: BLE001
            item["frame_error"] = str(exc)[:120]
        if want_audio:
            samples[key] = stereo.copy()
        out[key] = item

    res = {"tracks": {str(k): v for k, v in out.items()}, "frames": frames,
           "chunks": stats.get("n"), "bytes": stats.get("bytes"),
           "seconds": seconds, "start": start, "armed_before": sorted(armed_before),
           "cleaned": clean()}
    if want_audio:
        res["_samples"] = samples
    if error:
        res["error"] = error
    return res


def strip(stereo, fs: int = 44100, hop_s: float = 0.1, nfft: int = 4096) -> dict:
    """Лента: по отрезкам времени — 28 третьоктав и RMS."""
    import numpy as np
    try:
        import incr
    except ImportError:                      # как пакет flctrl
        from . import incr

    x = np.asarray(stereo, dtype=float)
    if x.ndim == 2:
        x = x.mean(axis=1)
    fc = np.asarray(incr.FC, dtype=float)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    lo, hi = fc * 10 ** (-1 / 20), fc * 10 ** (1 / 20)
    M = np.zeros((len(fc), len(freqs)))
    for i in range(len(fc)):
        m = (freqs >= lo[i]) & (freqs < hi[i])
        if not m.any():
            m[np.argmin(np.abs(freqs - fc[i]))] = True
        M[i] = m
    win = np.hanning(nfft)
    hop = max(1, int(round(hop_s * fs)))
    times, bands, rms_db, peaks = [], [], [], []
    for start in range(0, max(0, len(x) - hop // 2), hop):
        seg = x[start:start + nfft]
        if len(seg) < nfft:
            seg = np.pad(seg, (0, nfft - len(seg)))
        spec = np.abs(np.fft.rfft(seg * win)) ** 2
        spec *= 2.0 / (np.sum(win ** 2) * nfft)     # синус амплитуды 1 даёт 0 дБ
        power = M @ spec
        bands.append([round(float(10 * np.log10(p + 1e-20)), 1) for p in power])
        r = float(np.sqrt(np.mean(seg ** 2)))
        rms_db.append(round(20 * np.log10(r + 1e-12), 1))
        peaks.append(round(float(np.abs(seg).max()), 4))
        times.append(round(start / fs, 3))
    return {"hop_s": hop_s, "times": times, "fc": [float(v) for v in fc],
            "bands": bands, "rms_db": rms_db, "peaks": peaks}


def make_frame(item: dict, stereo) -> dict:
    """Кадр ленты для дорожки: оси время x полоса, значения в дБ."""
    try:
        import frames
    except ImportError:
        from . import frames
    st = strip(stereo)
    return frames.make_frame("bands", {"time": st["times"], "band": st["fc"]},
                             st["bands"], coverage={"measured": [item.get("name") or "?"]})


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


def dump(tracks=None, seconds: float = 5.0, start: float | None = None,
         outdir: str | None = None, fmt: str = "wav") -> dict:
    """Явно выгрузить звук дорожек в файлы (когда аудио реально нужно)."""
    import soundfile as sf

    outdir = outdir or os.path.join(os.environ.get("TEMP", "."), "fl_tracks")
    os.makedirs(outdir, exist_ok=True)
    res = capture(tracks=tracks, seconds=seconds, start=start, fade=False, want_audio=True)
    if res.get("error"):
        return res
    written = []
    for track, stereo in (res.pop("_samples", {}) or {}).items():
        item = res["tracks"].get(str(track), {})
        name = (item.get("name") or "track_%s" % track).replace("/", "_").replace("\\", "_")
        path = os.path.join(outdir, "%02d_%s.%s" % (max(track, 0), name, fmt))
        if fmt == "wav":
            sf.write(path, stereo, 44100, subtype="FLOAT")
        else:
            tmp = path + ".wav"
            sf.write(tmp, stereo, 44100, subtype="FLOAT")
            ff = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "tools",
                              "ffmpeg-9.0.1-win32-x64", "bin", "ffmpeg.exe")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", tmp,
                            "-codec:a", "libmp3lame", "-b:a", "256k", path], check=False)
            os.remove(tmp)
        written.append(path)
    res["files"] = written
    return res


save = dump            # прежнее имя
