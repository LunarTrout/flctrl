"""q.py -- единая гибкая точка доступа: состояние, слух, действие.

Три группы команд, один интерфейс:

  q state [--live | --file x.flp] [--what tracks|channels|plugins|routing|playlist]
  q hear  <file> [--at T | --section A:B] [--metrics m1,m2,...] [--stems DIR]
  q compare <file> <ref...> [--section A:B] [--metrics ...]
  q act   setvol|setpan|mute|solo|route|param|snapshot|rollback ...

Смысл: не помнить, какой скрипт для чего, а спрашивать нужное напрямую и
получать только запрошенные поля (--metrics), чтобы ответ не раздувался.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TCP = ("127.0.0.1", 9787)


# ------------------------------------------------------------------ мост FL

def _call(cmd, params=None, timeout=30):
    s = socket.create_connection(TCP, timeout=timeout)
    s.sendall((json.dumps({"op": "call", "cmd": cmd, "params": params or {}}) + "\n").encode())
    buf = b""
    s.settimeout(timeout)
    while True:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            break
    s.close()
    return json.loads(buf.decode("utf-8", "replace").strip())


def _restart_daemon(timeout: float = 60.0) -> bool:
    """Перезапустить демон моста: он теряет связь после перезапуска FL."""
    import subprocess
    import time

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process fl-studio-mcp-daemon -ErrorAction SilentlyContinue |"
             " Stop-Process -Force -ErrorAction SilentlyContinue"],
            capture_output=True, timeout=30)
    except Exception:
        pass
    try:
        import fl_lib as fl
        fl.link.ensure()
    except Exception:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _call("exec", {"expr": "mixer.trackCount()"}, timeout=5)
            return True
        except Exception:
            time.sleep(1.0)
    return False


def live(expr):
    try:
        r = _call("exec", {"expr": expr})
    except (ConnectionRefusedError, OSError):
        if not _restart_daemon():
            raise
        r = _call("exec", {"expr": expr})
    d = r.get("data") or {}
    if not d.get("ok"):
        err = str(d.get("error") or r)
        if "FLNotRunning" in err or "not responding" in err:
            if _restart_daemon():
                r = _call("exec", {"expr": expr})
                d = r.get("data") or {}
                if d.get("ok"):
                    return d.get("result")
        raise RuntimeError(err[:300])
    return d.get("result")


LIVE_EXPR = {
    "tracks": ("[{'i': i, 'name': str(mixer.getTrackName(i)), 'vol': round(mixer.getTrackVolume(i), 3),"
               " 'pan': round(mixer.getTrackPan(i), 3),"
               " 'fx': sum(1 for s in range(10) if plugins.isValid(i, s))}"
               " for i in range(mixer.trackCount())]"),
    "channels": ("[{'i': i, 'name': str(channels.getChannelName(i)),"
                 " 'vol': round(channels.getChannelVolume(i), 3),"
                 " 'pan': round(channels.getChannelPan(i), 3)}"
                 " for i in range(channels.channelCount())]"),
    "routing": "[{'track': i, 'name': str(mixer.getTrackName(i)), 'dest': mixer.getRouteToLevel(i, d)} for i in []]",
    "tempo": "general.getRecPPB()/1000.0",
    "plugins": ("[(i, s, str(plugins.getPluginName(i, s)))"
                " for i in range(mixer.trackCount()) for s in range(10)"
                " if plugins.isValid(i, s)]"),
}


def state_live(what: str):
    if what == "plugins":
        return live("__import__('json').dumps([list(x) for x in %s])" % LIVE_EXPR["plugins"])
    if what == "tempo":
        return live(LIVE_EXPR["tempo"])
    return live("__import__('json').dumps(%s)" % LIVE_EXPR[what])


def state_file(path: str, what: str):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "flpresearch"))
    import flp_events as fe
    f = fe.FLP.load(path)
    if what == "plugins":
        return fe.plugin_table(f)
    if what == "channels":
        return [{"i": i, "name": n} for i, n in enumerate(f.channel_names())]
    if what == "mixer":
        return fe.mixer_summary(f) if hasattr(fe, "mixer_summary") else {}
    return {}


# --------------------------------------------------------------------- слух

def _daemon_measure(path, sec, metrics):
    """Запрос к резидентному процессу слуха. None, если он не запущен."""
    try:
        s = socket.create_connection(("127.0.0.1", 9788), timeout=3)
        req = {"op": "measure", "path": path,
               "section": list(sec) if sec else None,
               "metrics": [m.strip() for m in metrics.split(",")] if metrics else None}
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        r = json.loads(buf.decode("utf-8", "replace"))
        return r.get("result") if r.get("ok") else None
    except Exception:
        return None


def hear(path, sec, metrics=None):
    import listen_layer as L
    from brief import flatten, measure_cached

    if os.path.basename(path).endswith(".py"):
        raise SystemExit("нужен аудиофайл")

    fast = _daemon_measure(path, sec, metrics)
    if fast is not None:
        return fast

    m = measure_cached(path, sec)
    flat = flatten(m)
    if metrics:
        want = [x.strip() for x in metrics.split(",") if x.strip()]
        flat = {k: v for k, v in flat.items() if any(w in k for w in want)}
    return flat


def compare(path, refs, sec, metrics=None):
    import listen_layer as L
    from brief import flatten
    mine = flatten(L.measure(path, section=sec))
    rs = [flatten(L.measure(r, section=sec)) for r in refs]
    cor = L.corridor(mine, rs)
    if metrics:
        want = [x.strip() for x in metrics.split(",") if x.strip()]
        cor = {k: v for k, v in cor.items() if any(w in k for w in want)}
    return cor


# ----------------------------------------------------------------- действие

ACTIONS = {
    "setvol": "mixer.setTrackVolume(%(track)d, %(value)s)",
    "setpan": "mixer.setTrackPan(%(track)d, %(value)s)",
    "mute": "mixer.muteTrack(%(track)d, %(value)s)",
    "solo": "mixer.soloTrack(%(track)d, %(value)s)",
    "param": "plugins.setParamValue(%(value)s, %(param)d, %(track)d, %(slot)d)",
    "chvol": "channels.setChannelVolume(%(index)d, %(value)s)",
    "chpan": "channels.setChannelPan(%(index)d, %(value)s)",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("state")
    s1.add_argument("--what", default="tracks")
    s1.add_argument("--file")
    s1.add_argument("--live", action="store_true")

    s2 = sub.add_parser("hear")
    s2.add_argument("file")
    s2.add_argument("--at", type=float)
    s2.add_argument("--section")
    s2.add_argument("--metrics")

    s3 = sub.add_parser("compare")
    s3.add_argument("file")
    s3.add_argument("refs", nargs="+")
    s3.add_argument("--section")
    s3.add_argument("--metrics")

    s4 = sub.add_parser("act")
    s4.add_argument("action", choices=sorted(ACTIONS))
    s4.add_argument("--track", type=int)
    s4.add_argument("--index", type=int)
    s4.add_argument("--slot", type=int)
    s4.add_argument("--param", type=int)
    s4.add_argument("--value", type=float)
    s4.add_argument("--dry", action="store_true")

    a = ap.parse_args(argv)

    if a.cmd == "state":
        if a.file:
            print(json.dumps(state_file(a.file, a.what), ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps(state_live(a.what), ensure_ascii=False, indent=2, default=str))
        return 0

    if a.cmd == "hear":
        sec = (a.at - 2, a.at + 2) if a.at is not None else (
            tuple(float(x) for x in a.section.split(":")) if a.section else None)
        print(json.dumps(hear(a.file, sec, a.metrics), ensure_ascii=False, indent=2, default=str))
        return 0

    if a.cmd == "compare":
        sec = tuple(float(x) for x in a.section.split(":")) if a.section else None
        print(json.dumps(compare(a.file, a.refs, sec, a.metrics),
                         ensure_ascii=False, indent=2, default=str))
        return 0

    expr = ACTIONS[a.action] % vars(a)
    print("expr:", expr)
    if a.dry:
        return 0
    print("было:", live("__import__('json').dumps(mixer.getTrackVolume(%d))" % (a.track or 0))
          if a.action == "setvol" else "-")
    print("результат:", live(expr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
