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

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import zlib

BRIDGE = ("127.0.0.1", 9787)
DAEMON = ("127.0.0.1", 9788)
RECORDED = os.path.join(os.environ.get("USERPROFILE", "C:/"),
                        "Documents", "Image-Line", "FL Studio", "Audio", "Recorded")
AUTOSAVE_DIR = os.path.join(os.environ.get("USERPROFILE", "C:/"), "Documents",
                            "Image-Line", "FL Studio", "Projects", "Backup")
BACKUP_DIR = os.path.join(os.environ.get("USERPROFILE", "C:/"), "Backups", "flctrl")
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


def _sha1(path: str) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshots(limit: int = 50) -> list:
    """Точки сохранения, которые мы наделали перед снимками (новые сверху)."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    rows = []
    for f in os.listdir(BACKUP_DIR):
        if not f.lower().endswith(".flp"):
            continue
        p = os.path.join(BACKUP_DIR, f)
        rows.append({"file": p, "size": os.path.getsize(p),
                     "mtime": round(os.path.getmtime(p))})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:limit]


def backup(max_age_s: float = 3600.0, keep: int = 20) -> dict:
    """Точка сохранения перед снимком: копия последнего автосейва FL.

    Одинаковые автосейвы не дублируются (сверка по sha1), старые точки
    подрезаются до `keep`.
    """
    import shutil
    if not os.path.isdir(AUTOSAVE_DIR):
        return {"ok": False, "error": "нет папки автосейвов FL"}
    files = [os.path.join(AUTOSAVE_DIR, f) for f in os.listdir(AUTOSAVE_DIR)
             if f.lower().endswith(".flp")]
    if not files:
        return {"ok": False, "error": "автосейвов нет"}
    newest = max(files, key=os.path.getmtime)
    age = time.time() - os.path.getmtime(newest)
    if age > max_age_s:
        return {"ok": False, "error": "автосейв старше %.0f мин" % (age / 60),
                "file": newest, "age_s": round(age)}
    digest = _sha1(newest)
    existing = snapshots(limit=200)
    for row in existing:
        if row["size"] == os.path.getsize(newest) and _sha1(row["file"]) == digest:
            return {"ok": True, "file": row["file"], "skipped": "тот же автосейв",
                    "age_s": round(age), "points": len(existing)}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, "%s_%s" % (stamp, os.path.basename(newest)))
    shutil.copy2(newest, dst)
    for row in snapshots(limit=1000)[keep:]:
        try:
            os.remove(row["file"])
        except OSError:
            pass
    return {"ok": True, "file": dst, "source": newest, "age_s": round(age),
            "points": len(snapshots(limit=1000))}


# ---------------------------------------------------------------------- снимок

def capture_memory(tracks=None, seconds: float = 2.0, start: float | None = None,
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

    safety = backup()
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
            payload = _daemon_send({"cmd": "stop", "settle": 0.3}, timeout=300.0)
            stats = payload.get("stats") or {}
        except (OSError, ValueError) as exc:
            error = (error + "; " if error else "") + "демон: %s" % str(exc)[:120]

    by_handle: dict[int, bytearray] = {}
    audio_map = payload.get("audio")
    if isinstance(audio_map, dict):
        for h, blob in audio_map.items():
            try:
                by_handle[int(h)] = bytearray(zlib.decompress(base64.b64decode(blob)))
            except (ValueError, zlib.error):
                continue
    else:                                   # старый формат ответа
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
           "cleaned": clean(), "backup": safety}
    if want_audio:
        res["_samples"] = samples
    if error:
        res["error"] = error
    return res


def capture(tracks=None, seconds: float = 2.0, start: float | None = None,
            fade: bool = True, want_audio: bool = False, mode: str = "files") -> dict:
    """Снимок звука дорожек. mode="files" — надёжно (FL пишет и сразу удаляем),
    mode="memory" — через хук, на диск не пишется ничего вообще."""
    fn = capture_memory if str(mode).startswith("mem") else capture_files
    return fn(tracks=tracks, seconds=seconds, start=start, fade=fade,
              want_audio=want_audio)


def capture_files(tracks=None, seconds: float = 2.0, start: float | None = None,
                  fade: bool = True, want_audio: bool = False) -> dict:
    """Надёжный путь: FL пишет дорожки на диск, читаем и сразу удаляем.

    Никаких хуков — значит FL не падает. Диск «засоряется» только на время
    снимка, после чего пустые и рабочие файлы удаляются.
    """
    import numpy as np
    import soundfile as sf

    if not fl_pid():
        return {"error": "FL не запущена", "tracks": {}, "frames": {}}
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
    before = set(os.listdir(RECORDED)) if os.path.isdir(RECORDED) else set()
    safety = backup()
    error = None
    try:
        if str(was_mode).strip() not in ("1", "1.0"):
            _bridge("transport.setLoopMode()")
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

    time.sleep(0.6)
    new = sorted(set(os.listdir(RECORDED)) - before) if os.path.isdir(RECORDED) else []
    names = {}
    for t in tracks:
        try:
            names[t] = str(_bridge("mixer.getTrackName(%d)" % t)).strip("'\"")
        except OSError:
            names[t] = "track_%d" % t

    out: dict[int, dict] = {}
    frames: dict[str, dict] = {}
    samples: dict[int, object] = {}
    for f in new:
        path = os.path.join(RECORDED, f)
        try:
            x, fs = sf.read(path, always_2d=True)
        except Exception:  # noqa: BLE001
            continue
        stereo = np.asarray(x, dtype="float64")
        if stereo.shape[1] == 1:
            stereo = np.repeat(stereo, 2, axis=1)
        base = os.path.basename(f)
        track = next((t for t, nm in names.items() if nm and base.endswith(nm + ".wav")), None)
        mono = stereo.mean(axis=1)
        peak = float(np.abs(mono).max()) if mono.size else 0.0
        rms = float(np.sqrt((mono ** 2).mean())) if mono.size else 0.0
        key = track if track is not None else -1
        item = {"file": base, "name": names.get(track, ""),
                "seconds": round(len(mono) / fs, 2),
                "rms": round(rms, 5), "peak": round(peak, 4), "silent": peak < 1e-5}
        try:
            frames[str(key)] = make_frame(item, stereo, fs=fs)
        except Exception as exc:  # noqa: BLE001
            item["frame_error"] = str(exc)[:120]
        if want_audio:
            samples[key] = stereo
        out[key] = item

    for f in new:                       # диск после снимка чистый
        try:
            os.remove(os.path.join(RECORDED, f))
        except OSError:
            pass

    res = {"tracks": {str(k): v for k, v in out.items()}, "frames": frames,
           "seconds": seconds, "start": start, "armed_before": sorted(armed_before),
           "cleaned": clean(), "backup": safety, "mode": "files"}
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


def make_frame(item: dict, stereo, fs: int = 44100) -> dict:
    """Кадр ленты для дорожки: оси время x полоса, значения в дБ."""
    try:
        import frames
    except ImportError:
        from . import frames
    st = strip(stereo, fs=fs)
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
