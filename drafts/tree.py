"""tree.py — один объект проекта, пути и шесть глаголов.

Не набор из семидесяти функций, а один объект с адресами:

    list("/")                 что где лежит
    get("/track/12/volume")   значение
    set("/track/12/volume", 0.7)
    call("plugin.insert", {...})
    measure({...})            метрика → кадр
    compare(a, b)             дельты

Всё остальное — надстройка над fl_lib: новые правила доступа, без новой логики
работы с FL.
"""
from __future__ import annotations

import re

WILDCARD = "*"


class TreeError(Exception):
    """Отказ с кодом: без «что делать», только факт и границы."""

    def __init__(self, code: str, path: str = "", **kw):
        self.code = code
        self.path = path
        self.details = kw
        super().__init__(code)

    def as_dict(self) -> dict:
        d = {"ok": False, "code": self.code}
        if self.path:
            d["path"] = self.path
        d.update(self.details)
        return d


def _fl():
    import fl_lib
    return fl_lib


def tokens(path: str) -> list:
    if not path.startswith("/"):
        raise TreeError("bad_path", path)
    return [t for t in path.strip("/").split("/") if t]


def _int(tok: str, what: str, path: str, lo: int = 0, hi: int | None = None) -> int:
    if tok == WILDCARD:
        raise TreeError("wildcard_here", path, token=tok)
    try:
        v = int(tok)
    except ValueError:
        raise TreeError("not_int", path, token=tok, what=what)
    if v < lo or (hi is not None and v > hi):
        raise TreeError("range", path, value=v, allowed=[lo, hi if hi is not None else "∞"])
    return v


# ---------------------------------------------------------------- list

def _track_count(fl) -> int:
    return int(fl._State._live("mixer.trackCount()"))


def list_node(path: str = "/", depth: int = 1, fl=None) -> list:
    fl = fl or _fl()
    t = tokens(path)

    def item(p, kind, **kw):
        d = {"path": p, "type": kind}
        d.update(kw)
        return d

    if not t:
        return [item("/project", "node", fields=["name", "tempo"]),
                item("/transport", "node", fields=["playing", "position", "seconds", "tempo"]),
                item("/track", "collection", n=_track_count(fl)),
                item("/channel", "collection", n=len(fl.state.channels())),
                item("/section", "node", fields=["a", "b"]),
                item("/refs", "collection", n=None),
                item("/measure", "collection", n=None)]

    if t[0] == "project" and len(t) == 1:
        return [item("/project/name", "string"),
                item("/project/tempo", "float", unit="BPM")]

    if t[0] == "transport" and len(t) == 1:
        return [item("/transport/playing", "bool"),
                item("/transport/position", "float", unit="доля 0..1"),
                item("/transport/seconds", "float", unit="с"),
                item("/transport/tempo", "float", unit="BPM")]

    if t[0] == "track" and len(t) == 1:
        n = _track_count(fl)
        names = {x["i"]: x["name"] for x in fl.state.tracks()}
        return [item("/track/%d" % i, "node", name=names.get(i)) for i in range(n)]

    if t[0] == "track" and len(t) >= 2:
        i = _int(t[1], "track", path)
        if len(t) == 2:
            tr = next((x for x in fl.state.tracks() if x["i"] == i), None)
            if tr is None:
                raise TreeError("path_not_found", path)
            out = [item("/track/%d/name" % i, "string", value=tr["name"]),
                   item("/track/%d/volume" % i, "float", value=tr["vol"], unit="0..1"),
                   item("/track/%d/pan" % i, "float", value=tr["pan"], unit="-1..1"),
                   item("/track/%d/mute" % i, "bool", value=bool(tr.get("mute"))),
                   item("/track/%d/solo" % i, "bool", value=bool(tr.get("solo"))),
                   item("/track/%d/slot" % i, "collection", n=tr.get("fx"))]
            if depth > 1:
                out.append(item("/track/%d/slot/*" % i, "collection", n=tr.get("fx")))
            return out
        if t[2] == "slot":
            if len(t) == 3:
                slots = [p for p in fl.state.plugins([i])]
                return [item("/track/%d/slot/%d" % (i, s["slot"]), "node", name=s["name"])
                        for s in slots]
            s = _int(t[3], "slot", path, 0, 9)
            info = fl.apply.info(i, s)
            if not info.get("used"):
                raise TreeError("path_not_found", path)
            if len(t) == 4:
                return [item("/track/%d/slot/%d/name" % (i, s), "string", value=info["plugin"]),
                        item("/track/%d/slot/%d/band" % (i, s), "collection", n=len(info["bands"])),
                        item("/track/%d/slot/%d/param" % (i, s), "collection",
                             n=info.get("param_count"))]
            if t[4] == "band":
                if len(t) == 5:
                    return [item("/track/%d/slot/%d/band/%d" % (i, s, b["n"]), "struct",
                                 fields=["hz", "gain"], value={"hz": b["hz"], "gain": b["gain"]})
                            for b in info["bands"]]
                b = _int(t[5], "band", path, 1, 64)
                band = next((x for x in info["bands"] if x["n"] == b), None)
                if band is None:
                    raise TreeError("path_not_found", path)
                return [item("/track/%d/slot/%d/band/%d/hz" % (i, s, b), "float", value=band["hz"]),
                        item("/track/%d/slot/%d/band/%d/gain" % (i, s, b), "float",
                             value=band["gain"], unit="дБ")]
            if t[4] == "param":
                params = fl.act.params(i, s)
                if len(t) == 5:
                    return [item("/track/%d/slot/%d/param/%d" % (i, s, p["i"]), "float",
                                 name=p["name"], text=p["text"]) for p in params]
                p = _int(t[5], "param", path)
                one = next((x for x in params if x["i"] == p), None)
                if one is None:
                    raise TreeError("path_not_found", path)
                return [item("/track/%d/slot/%d/param/%d" % (i, s, p), "float",
                             name=one["name"], text=one["text"])]
        raise TreeError("path_not_found", path)

    if t[0] == "section" and len(t) == 1:
        import session_state
        a, b = session_state.get_section()
        return [item("/section/a", "float", value=a, unit="с"),
                item("/section/b", "float", value=b, unit="с")]

    raise TreeError("path_not_found", path)


# ---------------------------------------------------------------- get

def get(path: str, fl=None):
    fl = fl or _fl()
    t = tokens(path)
    if not t:
        return list_node("/", fl=fl)

    if WILDCARD in t:
        return _get_wild(path, fl)

    if t[0] == "project":
        if len(t) == 1:
            return {"name": _project_name(), "tempo": fl.state.tempo()}
        if t[1] == "name":
            return {"value": _project_name(), "type": "string"}
        if t[1] == "tempo":
            return {"value": fl.state.tempo(), "type": "float", "unit": "BPM"}

    if t[0] == "transport":
        if len(t) == 1:
            return _transport(fl)
        key = t[1]
        tr = _transport(fl)
        if key not in tr:
            raise TreeError("path_not_found", path)
        return {"value": tr[key], "type": "float" if isinstance(tr[key], (int, float)) else "bool"}

    if t[0] == "track":
        i = _int(t[1], "track", path)
        if len(t) == 2:
            tr = next((x for x in fl.state.tracks() if x["i"] == i), None)
            if tr is None:
                raise TreeError("path_not_found", path)
            return tr
        key = t[2]
        tr = next((x for x in fl.state.tracks() if x["i"] == i), None)
        if tr is None:
            raise TreeError("path_not_found", path)
        if key in ("volume", "vol"):
            return {"value": tr["vol"], "type": "float", "unit": "0..1"}
        if key == "pan":
            return {"value": tr["pan"], "type": "float", "unit": "-1..1"}
        if key in ("mute", "solo"):
            return {"value": bool(tr.get(key)), "type": "bool"}
        if key == "name":
            return {"value": tr["name"], "type": "string"}
        if key == "slot" and len(t) >= 4:
            s = _int(t[3], "slot", path, 0, 9)
            info = fl.apply.info(i, s)
            if len(t) == 4:
                return info
            if t[4] == "band" and len(t) >= 6:
                b = _int(t[5], "band", path, 1, 64)
                band = next((x for x in info["bands"] if x["n"] == b), None)
                if band is None:
                    raise TreeError("path_not_found", path)
                if len(t) == 6:
                    return band
                key2 = t[6]
                if key2 not in band:
                    raise TreeError("path_not_found", path)
                return {"value": band[key2], "type": "float"}
            if t[4] == "param" and len(t) >= 6:
                p = _int(t[5], "param", path)
                one = next((x for x in fl.act.params(i, s) if x["i"] == p), None)
                if one is None:
                    raise TreeError("path_not_found", path)
                return {"value": one["text"], "type": "float", "name": one["name"]}
        raise TreeError("path_not_found", path)

    raise TreeError("path_not_found", path)


def _get_wild(path: str, fl) -> list:
    """get('/track/*/volume') — список значений по всем подходящим путям."""
    t = tokens(path)
    if t[0] == "track" and t[1] == WILDCARD:
        rest = "/".join(t[2:])
        out = []
        for i in range(_track_count(fl)):
            p = "/track/%d/%s" % (i, rest) if rest else "/track/%d" % i
            try:
                v = get(p, fl=fl)
            except TreeError:
                continue
            out.append({"track": i, "path": p,
                        "value": v.get("value", v) if isinstance(v, dict) else v})
        return out
    raise TreeError("bad_wildcard", path)


# ---------------------------------------------------------------- set

def set_value(path: str, value, fl=None) -> dict:
    fl = fl or _fl()
    t = tokens(path)
    before = _safe_before(path, fl)

    if t[0] == "track":
        i = _int(t[1], "track", path)
        key = t[2] if len(t) > 2 else ""
        if key in ("volume", "vol"):
            v = _as_float(value, path, 0.0, 1.0)
            fl.act.volume(i, v)
        elif key == "pan":
            v = _as_float(value, path, -1.0, 1.0)
            fl.act.pan(i, v)
        elif key == "mute":
            fl.act.mute(i, _as_bool(value, path))
        elif key == "solo":
            fl.act.solo(i, _as_bool(value, path))
        elif key == "slot" and len(t) >= 7 and t[4] == "band":
            s = _int(t[3], "slot", path, 0, 9)
            b = _int(t[5], "band", path, 1, 64)
            if not isinstance(value, dict):
                raise TreeError("type", path, expected="struct", got=type(value).__name__)
            res = fl.apply.band(i, s, b, hz=value.get("hz"), db=value.get("gain", value.get("db")),
                                q=value.get("q"))
            if not res.get("ok"):
                raise TreeError("apply_failed", path, error=res.get("error"))
        elif key == "slot" and len(t) >= 7 and t[4] == "param":
            s = _int(t[3], "slot", path, 0, 9)
            p = _int(t[5], "param", path)
            fl.act.param(i, s, p, _as_float(value, path, 0.0, 1.0))
        else:
            raise TreeError("not_writable", path)
    elif t[0] == "transport" and len(t) == 2 and t[1] == "position":
        fl._State._live("transport.setSongPos(%r)" % _as_float(value, path, 0.0, 1.0))
    elif t[0] == "section":
        import session_state
        a, b = session_state.get_section()
        if len(t) == 2 and t[1] in ("a", "b"):
            if t[1] == "a":
                a = float(value)
            else:
                b = float(value)
            session_state.set_section(a, b)
        elif len(t) == 1 and isinstance(value, dict):
            session_state.set_section(float(value.get("a", a)), float(value.get("b", b)))
        else:
            raise TreeError("not_writable", path)
    else:
        raise TreeError("not_writable", path)

    return {"path": path, "before": before, "after": _safe_before(path, fl)}


def _safe_before(path: str, fl):
    try:
        v = get(path, fl=fl)
        return v.get("value") if isinstance(v, dict) and "value" in v else v
    except Exception:
        return None


def _as_float(v, path, lo, hi) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise TreeError("type", path, expected="float", got=type(v).__name__)
    if f < lo or f > hi:
        raise TreeError("range", path, value=f, allowed=[lo, hi])
    return f


def _as_bool(v, path) -> bool:
    if isinstance(v, bool):
        return v
    raise TreeError("type", path, expected="bool", got=type(v).__name__)


def set_many(items: list, fl=None) -> dict:
    """Пачка атомарно: при ошибке откатываем то, что успели."""
    fl = fl or _fl()
    done = []
    try:
        for it in items:
            done.append(set_value(it["path"], it.get("value"), fl=fl))
    except Exception:
        for ch in reversed(done):
            try:
                set_value(ch["path"], ch["before"], fl=fl)
            except Exception:
                pass
        raise
    return {"ok": True, "changes": done}


# ---------------------------------------------------------------- call

def call(op: str, args: dict | None = None, fl=None) -> dict:
    fl = fl or _fl()
    a = args or {}

    if op == "plugin.insert":
        return fl.create.live_insert(int(a["track"]), int(a["slot"]), a["name"])
    if op == "plugin.delete":
        return fl.create.live_delete(int(a["track"]), int(a["slot"]))
    if op == "plugin.replace":
        return fl.create.live_replace(int(a["track"]), int(a["slot"]), a["name"])
    if op == "transport.play":
        return {"ok": bool(fl._State._live("transport.start()")) if False else True,
                "playing": fl._State._live("transport.start()")}
    if op == "transport.stop":
        fl._State._live("transport.stop()")
        return {"ok": True, "playing": 0}
    if op == "save":
        import flsave
        return flsave.save(make_backup=bool(a.get("backup", True)))
    if op == "track.add":
        return fl.create.live_track_add(a.get("index"), int(a.get("count", 1)))
    if op == "track.delete":
        return fl.create.live_track_delete(int(a["index"]))
    raise TreeError("unknown_op", op)


# ---------------------------------------------------------------- служебное

def _project_name() -> str | None:
    try:
        import render_job
        return render_job.window_title()
    except Exception:
        return None


def _transport(fl) -> dict:
    import time
    playing = int(fl._State._live("transport.isPlaying()", attempts=2))
    pos = float(fl._State._live("transport.getSongPos()", attempts=2))
    tempo = fl.state.tempo()
    return {"playing": bool(playing), "position": round(pos, 6),
            "seconds": round(pos * 240.0, 3), "tempo": tempo}
