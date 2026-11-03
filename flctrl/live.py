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


# ------------------------------------------------------- окно записи FL (реестр)

DSTITF = r"Software\Image-Line\FL Studio 25\DSTITF"


def popup_state() -> int | None:
    """PopupRec: 0 — FL показывает окно «Recording» при нажатии Record."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DSTITF) as key:
            return int(winreg.QueryValueEx(key, "PopupRec")[0])
    except OSError:
        return None


def suppress_popup(enable: bool = True) -> dict:
    """Убрать модальное окно «Recording» — без него автоматизация встаёт."""
    import winreg
    old = popup_state()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DSTITF, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PopupRec", 0, winreg.REG_SZ, "1" if enable else "0")
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:160], "was": old}
    return {"ok": True, "was": old, "now": popup_state()}


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


def fl_window() -> dict:
    """Главное окно FL: заголовок (в нём имя проекта) и хэндл."""
    import ctypes
    u = ctypes.windll.user32
    found = {}

    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, n, 512)
        if "FL Studio" in n.value and n.value.strip().endswith("FL Studio 2025"):
            found["hwnd"] = h
            found["title"] = n.value
            return False
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(cb), 0)
    return found


PROJECT_ROOTS = [
    os.path.join(os.environ.get("USERPROFILE", "C:/"), "Desktop", "PROJECTS"),
    os.path.join(os.environ.get("USERPROFILE", "C:/"), "Documents", "Image-Line",
                 "FL Studio", "Projects"),
    os.path.join(os.environ.get("USERPROFILE", "C:/"), "Documents"),
]


def _find_project(name: str, max_depth: int = 3) -> str | None:
    """Найти файл проекта по имени из заголовка окна."""
    for root in PROJECT_ROOTS:
        if not os.path.isdir(root):
            continue
        base_depth = os.path.normpath(root).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth > max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in
                           ("backup", "node_modules", ".git", "recorded", "_analysis")]
            if name in filenames:
                return os.path.join(dirpath, name)
    return None


def save_project(timeout: float = 15.0) -> dict:
    """Сохранить проект в FL через Ctrl+S и вернуть путь к файлу.

    Нужно, чтобы точка сохранения отражала текущее состояние, а не автосейв.
    """
    import ctypes
    win = fl_window()
    if not win.get("hwnd"):
        return {"ok": False, "error": "окно FL не найдено"}
    title = win["title"]
    name = title.split(" - ")[0].strip()
    if not name.lower().endswith(".flp"):
        return {"ok": False, "error": "в заголовке нет имени проекта: %r" % title}
    path = _find_project(name)
    before = os.path.getmtime(path) if path and os.path.isfile(path) else None

    u = ctypes.windll.user32
    u.ShowWindow(win["hwnd"], 5)
    fg = u.GetForegroundWindow()
    tid_fg = u.GetWindowThreadProcessId(fg, None)
    tid_fl = u.GetWindowThreadProcessId(win["hwnd"], None)
    if tid_fg != tid_fl:
        u.AttachThreadInput(tid_fg, tid_fl, True)
    u.SetForegroundWindow(win["hwnd"])
    u.BringWindowToTop(win["hwnd"])
    time.sleep(0.4)
    if tid_fg != tid_fl:
        u.AttachThreadInput(tid_fg, tid_fl, False)
    if u.GetForegroundWindow() != win["hwnd"]:
        return {"ok": False, "error": "FL не в фокусе — Ctrl+S не отправляю"}
    VK_CONTROL, VK_S = 0x11, 0x53
    KEYEVENTF_KEYUP = 2
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_S, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(VK_S, 0, KEYEVENTF_KEYUP, 0)
    u.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    deadline = time.time() + timeout
    while time.time() < deadline:
        path = _find_project(name) or path
        if path and os.path.isfile(path):
            if before is None or os.path.getmtime(path) > before + 0.001:
                return {"ok": True, "file": path, "mtime": round(os.path.getmtime(path))}
        time.sleep(0.3)
    if path and os.path.isfile(path):
        return {"ok": False, "error": "Ctrl+S не изменил файл", "file": path}
    return {"ok": False, "error": "файл проекта не найден"}


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


def backup(max_age_s: float = 3600.0, keep: int = 20, save: bool = True) -> dict:
    """Точка сохранения перед снимком.

    save=True — сначала сохраняем проект в FL (Ctrl+S), чтобы точка отражала
    текущее состояние. Если сохранить не вышло — берём последний автосейв FL.
    Одинаковые файлы не дублируются (sha1), старые точки подрезаются до `keep`.
    """
    import shutil

    saved = save_project() if save else {"ok": False}
    if saved.get("ok"):
        src, age, origin = saved["file"], 0, "save"
    else:
        if not os.path.isdir(AUTOSAVE_DIR):
            return {"ok": False, "error": saved.get("error") or "нет папки автосейвов FL"}
        files = [os.path.join(AUTOSAVE_DIR, f) for f in os.listdir(AUTOSAVE_DIR)
                 if f.lower().endswith(".flp")]
        if not files:
            return {"ok": False, "error": "автосейвов нет"}
        src = max(files, key=os.path.getmtime)
        age = time.time() - os.path.getmtime(src)
        origin = "autosave"
        if age > max_age_s:
            return {"ok": False, "error": "автосейв старше %.0f мин" % (age / 60),
                    "file": src, "age_s": round(age)}

    digest = _sha1(src)
    existing = snapshots(limit=200)
    for row in existing:
        if row["size"] == os.path.getsize(src) and _sha1(row["file"]) == digest:
            return {"ok": True, "file": row["file"], "skipped": "без изменений",
                    "origin": origin, "points": len(existing)}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, "%s_%s" % (stamp, os.path.basename(src)))
    shutil.copy2(src, dst)
    for row in snapshots(limit=1000)[keep:]:
        try:
            os.remove(row["file"])
        except OSError:
            pass
    return {"ok": True, "file": dst, "source": src, "origin": origin,
            "age_s": round(age), "points": len(snapshots(limit=1000))}


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
    if popup_state() == 0:
        suppress_popup(True)          # иначе FL откроет модальное окно и всё встанет
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
            _bridge("transport.setSongPos(%r)" % min(1.0, max(0.0, float(start))))
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
                "role": _role(names.get(track, "")),
                "seconds": round(len(mono) / 44100, 2),
                "rms": round(rms, 5), "peak": round(peak, 4),
                "silent": peak < 1e-5}
        try:
            frames[str(key)] = make_frame(item, stereo)
            item["tape"] = tape(stereo)
            item["transients"] = _transients(stereo, fs)
        except Exception as exc:  # noqa: BLE001
            item["frame_error"] = str(exc)[:120]
        if want_audio:
            samples[key] = stereo.copy()
        out[key] = item

    res = {"tracks": {str(k): v for k, v in out.items()}, "frames": frames,
           "chunks": stats.get("n"), "bytes": stats.get("bytes"),
           "seconds": seconds, "start": start, "armed_before": sorted(armed_before),
           "cleaned": clean(), "backup": safety}
    res = _enrich(res, out, start, seconds)
    if want_audio:
        res["_samples"] = samples
    if error:
        res["error"] = error
    return res


def _role(name: str) -> str:
    try:
        import roles
    except ImportError:
        from . import roles
    try:
        return roles.role_of(name)
    except Exception:  # noqa: BLE001
        return "other"


def _enrich(res: dict, out: dict, start, seconds: float) -> dict:
    """Добавить к снимку меж-дорожные слои: маскировка и провенанс."""
    import timbre as _t

    tiles = {}
    for key, item in out.items():
        cells = (item.get("tape") or {}).get("cells")
        if cells:
            tiles[str(key)] = {"name": item.get("name") or str(key), "cells": cells}
    try:
        res["masking"] = _t.masking(tiles)
    except Exception as exc:  # noqa: BLE001
        res["masking"] = {"error": str(exc)[:120]}

    prov = []
    try:
        import api as _api
        win = fl_window()
        name = (win.get("title") or "").split(" - ")[0].strip()
        path = _find_project(name) if name else None
        if path:
            _api.use_project(path)
            start_s = float(start or 0.0)
            t = 0.0
            while t <= seconds + 1e-9:
                r = _api.call("clip.at", {"seconds": start_s + t})
                prov.append({"t": round(t, 2), "bar": r.get("bar"),
                             "active": [a.get("channel_name") or a.get("pattern_name")
                                        or ("clip%d" % a.get("clip_id"))
                                        for a in (r.get("active") or [])]})
                t += 0.5
            res["position"] = {"start_seconds": start_s, "bpm": r.get("tempo")}
    except Exception as exc:  # noqa: BLE001
        res["provenance_error"] = str(exc)[:140]
    if prov:
        res["provenance"] = prov
    return res


def _transients(stereo, fs) -> dict:
    try:
        import timbre as _t
    except ImportError:
        from . import timbre as _t
    try:
        return _t.transients_stats(stereo, fs)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def _events(stereo, fs) -> dict:
    try:
        import events as ev
    except ImportError:
        from . import events as ev
    try:
        return ev.detect(stereo, fs)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def _picture(cells, res, fc, times) -> str:
    try:
        import timbre
    except ImportError:
        from . import timbre
    tp = {"cells": cells, "times": times, "fc": fc,
          "onsets": res.get("onsets", 0), "onset_rate_hz": res.get("onset_rate_hz", 0.0)}
    try:
        return timbre.picture(tp)
    except Exception as exc:  # noqa: BLE001
        return "не нарисовалось: %s" % str(exc)[:80]


def clip_flag(value=None):
    """Авто-создание клипов в плейлисте (HKCU\\...\\FL Studio 25\\AutoCreateClip).

    FL читает это значение **в момент записи**, поэтому его можно погасить на время
    снимка и вернуть как было. Именно из-за него записанные дорожки ложились клипами
    в плейлист и накладывались друг на друга — в одном месте трека становилось резко
    громко.

    ``value=None`` — только прочитать; ``0``/``1`` — поставить.
    """
    key = r"Software\Image-Line\FL Studio 25"
    out = {"key": key, "value": None, "before": None, "changed": False}
    try:
        import winreg
    except Exception as exc:  # не Windows
        out["error"] = str(exc)[:120]
        return out
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
            out["value"] = int(winreg.QueryValueEx(k, "AutoCreateClip")[0])
        out["before"] = out["value"]
        if value is not None and out["value"] != int(value):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "AutoCreateClip", 0, winreg.REG_SZ, str(int(value)))
            out["value"] = int(value)
            out["changed"] = True
    except FileNotFoundError:
        out["error"] = "в реестре нет AutoCreateClip"
    except Exception as exc:
        out["error"] = str(exc)[:120]
    return out


def _undo_hint() -> dict:
    """Позиция в истории undo: {'pos','count','text'}."""
    try:
        text = str(_bridge("general.getUndoLevelHint()", timeout=5)).strip().strip("'\"")
        pos, _, count = text.partition("/")
        return {"text": text, "pos": int(pos), "count": int(count)}
    except Exception as exc:
        return {"text": None, "error": str(exc)[:80]}


def capture(tracks=None, seconds: float = 2.0, start: float | None = None,
            fade: bool = True, want_audio: bool = False, mode: str = "memory",
            discard_take: bool = True) -> dict:
    """Снимок звука дорожек. mode="files" — надёжно (FL пишет и сразу удаляем),
    mode="memory" — через хук, на диск не пишется ничего вообще.

    ``start`` — доля длины проекта 0..1 (так его принимает ``transport.setSongPos``).

    Плейлист не трогается:

    1. на время снимка гасится ``AutoCreateClip`` — FL читает его в момент записи;
    2. после снимка выбрасывается шаг записи из истории (``undoUpDown``), иначе FL
       материализует клипы при следующем сохранении проекта;
    3. значение ``AutoCreateClip`` возвращается как было — своя запись пользователя
       работает по-прежнему.

    Проверено на живом проекте: снимок отдаёт звук дорожки, а сохранение после него
    не добавляет в плейлист ни одного клипа.
    """
    flag0 = clip_flag()
    hint0 = _undo_hint()
    clips = {"before": flag0.get("value"), "during": None, "after": None}
    undone = 0
    if flag0.get("value") != 0:
        clip_flag(0)
        clips["during"] = 0
    try:
        want_mem = str(mode).startswith("mem")
        fn = capture_memory if want_mem else capture_files
        res = fn(tracks=tracks, seconds=seconds, start=start, fade=fade,
                 want_audio=want_audio)
        # поток недоступен (нет демона хука) — тихо откатываемся на файлы
        if want_mem and (res.get("error") or not (res.get("tracks") or {})):
            res = capture_files(tracks=tracks, seconds=seconds, start=start, fade=fade,
                                want_audio=want_audio)
            res["mode_used"] = "files (fallback)"
        else:
            res["mode_used"] = "memory" if want_mem else "files"
    finally:
        if discard_take and hint0.get("text"):
            hint1 = _undo_hint()
            if hint1.get("text") and hint1.get("text") == hint0.get("text"):
                undone = 0                     # запись не создала шага — откатывать нечего
            elif hint1.get("text"):
                try:
                    _bridge("general.undoUpDown(-1)", timeout=10)
                    time.sleep(0.2)
                    h2 = _undo_hint()
                    # важно, что история сдвинулась после нашего undoUpDown,
                    # а не в какую сторону нумеруются позиции
                    undone = 1 if h2.get("text") and h2.get("text") != hint1.get("text") else -1
                except Exception:
                    undone = -1
        if clips["during"] == 0 and clips["before"] is not None:
            clip_flag(clips["before"])
        clips["after"] = clip_flag().get("value")

    res["clips"] = clips
    res["take_discarded"] = undone
    res["clips_clean"] = clips["after"] == clips["before"] and undone >= 0
    res["undo"] = {"before": hint0.get("text"), "after": _undo_hint().get("text")}
    return res


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
    if popup_state() == 0:
        suppress_popup(True)
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
                "role": _role(names.get(track, "")),
                "seconds": round(len(mono) / fs, 2),
                "rms": round(rms, 5), "peak": round(peak, 4), "silent": peak < 1e-5}
        try:
            frames[str(key)] = make_frame(item, stereo, fs=fs)
            item["tape"] = tape(stereo, fs=fs)
            item["transients"] = _transients(stereo, fs)
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
    res = _enrich(res, out, start, seconds)
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


def tape(stereo, fs: int = 44100, hop_s: float = 0.1, nfft: int = 4096) -> dict:
    """Широкая лента по дорожке: признаки, окрас, техника (см. timbre.py).

    Возвращает и плоские ряды (bands/rms/peak/corr/...), и полные клетки
    в ``cells`` — по ним видно не только «сколько», но и «что за звук».
    """
    try:
        import timbre
    except ImportError:
        from . import timbre
    res = timbre.analyze(stereo, fs=fs, hop_s=hop_s, nfft=nfft)
    cells = res["cells"]
    return {"hop_s": hop_s, "times": res["times"], "fc": res["fc"],
            "bands": [c["bands"] for c in cells],
            "rms_db": [c["rms_db"] for c in cells],
            "peak": [c["peak"] for c in cells],
            "crest_db": [c["crest_db"] for c in cells],
            "flux_db": [c["flux_db"] for c in cells],
            "low_db": [c["low_db"] for c in cells],
            "mid_db": [c["mid_db"] for c in cells],
            "high_db": [c["high_db"] for c in cells],
            "stereo_corr": [c["corr"] for c in cells],
            "stereo_width": [c["width"] for c in cells],
            "f0_hz": [c["f0_hz"] for c in cells],
            "notes": [c["note"] for c in cells],
            "cells": cells, "technique": res["technique"],
            "events": _events(stereo, fs),
            "picture": _picture(cells, res, fc=res["fc"], times=res["times"]),
            "onset_rate_hz": res["onset_rate_hz"], "onsets": res["onsets"]}


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
