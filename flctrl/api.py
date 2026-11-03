"""api.py — один объект проекта: пути и шесть глаголов.

    api.list("/")                       что где лежит
    api.get("/track/12/volume")         значение
    api.set("/track/12/volume", 0.7)    записать (пачка — атомарно)
    api.call("plugin.insert", {...})    действие
    api.measure({"metric": "bands", "sources": [...]})   картина → кадр
    api.compare(a, b)                   дельты по осям

Никакого порядка не навязано: `set` работает без `measure`, `compare` — без
`plan`, `measure` — без `set`. Всё, что нужно для работы с живым FL, берётся из
fl_lib; файловые измерения — из audit/frames.

Ошибки структурные: {ok:false, code, path, ...}. Без «что делать дальше».
"""
from __future__ import annotations

import os
import re

WILDCARD = "*"


class ApiError(Exception):
    def __init__(self, code: str, path: str = "", **kw):
        self.code, self.path, self.details = code, path, kw
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


# ------------------------------------------------------------------ разбор пути

def tokens(path: str) -> list:
    if not path.startswith("/"):
        raise ApiError("bad_path", path)
    return [t for t in path.strip("/").split("/") if t]


def _int(tok: str, what: str, path: str, lo: int = 0, hi: int | None = None) -> int:
    if tok == WILDCARD:
        raise ApiError("wildcard_here", path, token=tok)
    try:
        v = int(tok)
    except ValueError:
        raise ApiError("not_int", path, token=tok, what=what)
    if v < lo or (hi is not None and v > hi):
        raise ApiError("range", path, value=v,
                       allowed=[lo, hi if hi is not None else "∞"])
    return v


def _as_float(v, path: str, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError("type", path, expected="float", got=type(v).__name__)
    if f < lo or f > hi:
        raise ApiError("range", path, value=f, allowed=[lo, hi])
    return f


def _as_bool(v, path: str) -> bool:
    if isinstance(v, bool):
        return v
    raise ApiError("type", path, expected="bool", got=type(v).__name__)


# ------------------------------------------------------------------ list

def list_node(path: str = "/", depth: int = 1, fl=None) -> list:
    fl = fl or _fl()
    t = tokens(path)

    def item(p, kind, **kw):
        d = {"path": p, "type": kind}
        d.update(kw)
        return d

    n_tracks = None
    if not t:
        n_tracks = int(fl._State._live("mixer.trackCount()"))
        return [item("/project", "node", fields=["name", "tempo"]),
                item("/transport", "node",
                     fields=["playing", "position", "seconds", "tempo"]),
                item("/track", "collection", n=n_tracks),
                item("/channel", "collection", n=len(fl.state.channels())),
                item("/section", "node", fields=["a", "b"]),
                item("/refs", "collection", n=None),
                item("/measure", "collection", n=None)]

    if t[0] == "project":
        return [item("/project/name", "string"), item("/project/tempo", "float", unit="BPM")]

    if t[0] == "transport":
        return [item("/transport/" + f, "float" if f != "playing" else "bool")
                for f in ("playing", "position", "seconds", "tempo")]

    if t[0] == "track" and len(t) == 1:
        names = {x["i"]: x["name"] for x in fl.state.tracks()}
        return [item("/track/%d" % i, "node", name=names.get(i))
                for i in sorted(names)]

    if t[0] == "track":
        i = _int(t[1], "track", path)
        tr = next((x for x in fl.state.tracks() if x["i"] == i), None)
        if tr is None:
            raise ApiError("path_not_found", path)
        if len(t) == 2:
            return [item("/track/%d/volume" % i, "float", value=tr["vol"], unit="0..1"),
                    item("/track/%d/pan" % i, "float", value=tr["pan"], unit="-1..1"),
                    item("/track/%d/mute" % i, "bool", value=bool(tr.get("mute"))),
                    item("/track/%d/solo" % i, "bool", value=bool(tr.get("solo"))),
                    item("/track/%d/slot" % i, "collection", n=tr.get("fx"))]
        if t[2] == "slot":
            if len(t) == 3:
                return [item("/track/%d/slot/%d" % (i, p["slot"]), "node", name=p["name"])
                        for p in fl.state.plugins([i])]
            s = _int(t[3], "slot", path, 0, 9)
            info = fl.apply.info(i, s)
            if not info.get("used"):
                raise ApiError("path_not_found", path)
            if len(t) == 4:
                return [item("/track/%d/slot/%d/name" % (i, s), "string", value=info["plugin"]),
                        item("/track/%d/slot/%d/band" % (i, s), "collection",
                             n=len(info["bands"])),
                        item("/track/%d/slot/%d/param" % (i, s), "collection",
                             n=info.get("param_count"))]
            if t[4] == "band":
                if len(t) == 5:
                    return [item("/track/%d/slot/%d/band/%d" % (i, s, b["n"]), "struct",
                                 value={"hz": b["hz"], "gain": b["gain"]})
                            for b in info["bands"]]
                b = _int(t[5], "band", path, 1, 64)
                band = next((x for x in info["bands"] if x["n"] == b), None)
                if band is None:
                    raise ApiError("path_not_found", path)
                return [item("/track/%d/slot/%d/band/%d/hz" % (i, s, b), "float",
                             value=band["hz"], unit="Гц"),
                        item("/track/%d/slot/%d/band/%d/gain" % (i, s, b), "float",
                             value=band["gain"], unit="дБ")]
            if t[4] == "param":
                params = fl.act.params(i, s)
                if len(t) == 5:
                    return [item("/track/%d/slot/%d/param/%d" % (i, s, p["i"]), "float",
                                 name=p["name"], text=p["text"]) for p in params]
        raise ApiError("path_not_found", path)

    if t[0] == "section":
        from . import session_state
        a, b = session_state.get_section()
        return [item("/section/a", "float", value=a, unit="с"),
                item("/section/b", "float", value=b, unit="с")]

    raise ApiError("path_not_found", path)


# ------------------------------------------------------------------ get

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
        tr = _transport(fl)
        if len(t) == 1:
            return tr
        if t[1] not in tr:
            raise ApiError("path_not_found", path)
        return {"value": tr[t[1]], "type": "float" if t[1] != "playing" else "bool"}

    if t[0] == "track":
        i = _int(t[1], "track", path)
        tr = next((x for x in fl.state.tracks() if x["i"] == i), None)
        if tr is None:
            raise ApiError("path_not_found", path)
        if len(t) == 2:
            return tr
        key = t[2]
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
                    raise ApiError("path_not_found", path)
                if len(t) == 6:
                    return band
                if t[6] not in band:
                    raise ApiError("path_not_found", path)
                return {"value": band[t[6]], "type": "float"}
            if t[4] == "param" and len(t) >= 6:
                p = _int(t[5], "param", path)
                one = next((x for x in fl.act.params(i, s) if x["i"] == p), None)
                if one is None:
                    raise ApiError("path_not_found", path)
                return {"value": one["text"], "type": "float", "name": one["name"]}
        raise ApiError("path_not_found", path)

    raise ApiError("path_not_found", path)


def _get_wild(path: str, fl) -> list:
    t = tokens(path)
    if t[0] == "track" and t[1] == WILDCARD:
        rest = "/".join(t[2:])
        out = []
        for i in range(int(fl._State._live("mixer.trackCount()"))):
            p = "/track/%d/%s" % (i, rest) if rest else "/track/%d" % i
            try:
                v = get(p, fl=fl)
            except ApiError:
                continue
            out.append({"track": i, "path": p,
                        "value": v.get("value", v) if isinstance(v, dict) else v})
        return out
    raise ApiError("bad_wildcard", path)


# ------------------------------------------------------------------ set

def set_value(path: str, value, fl=None) -> dict:
    fl = fl or _fl()
    t = tokens(path)
    before = _safe_before(path, fl)

    if t[0] == "track":
        i = _int(t[1], "track", path)
        key = t[2] if len(t) > 2 else ""
        if key in ("volume", "vol"):
            fl.act.volume(i, _as_float(value, path, 0.0, 1.0))
        elif key == "pan":
            fl.act.pan(i, _as_float(value, path, -1.0, 1.0))
        elif key == "mute":
            fl.act.mute(i, _as_bool(value, path))
        elif key == "solo":
            fl.act.solo(i, _as_bool(value, path))
        elif key == "slot" and len(t) >= 7 and t[4] == "band":
            s = _int(t[3], "slot", path, 0, 9)
            b = _int(t[5], "band", path, 1, 64)
            if not isinstance(value, dict):
                raise ApiError("type", path, expected="struct", got=type(value).__name__)
            res = fl.apply.band(i, s, b, hz=value.get("hz"),
                                db=value.get("gain", value.get("db")), q=value.get("q"))
            if not res.get("ok"):
                raise ApiError("apply_failed", path, error=res.get("error"))
        elif key == "slot" and len(t) >= 7 and t[4] == "param":
            s = _int(t[3], "slot", path, 0, 9)
            p = _int(t[5], "param", path)
            fl.act.param(i, s, p, _as_float(value, path, 0.0, 1.0))
        else:
            raise ApiError("not_writable", path)
    elif t[0] == "transport" and len(t) == 2 and t[1] == "position":
        fl._State._live("transport.setSongPos(%r)" % _as_float(value, path, 0.0, 1.0))
    elif t[0] == "section":
        from . import session_state
        a, b = session_state.get_section()
        if len(t) == 2 and t[1] in ("a", "b"):
            a, b = (float(value), b) if t[1] == "a" else (a, float(value))
            session_state.set_section(a, b)
        elif len(t) == 1 and isinstance(value, dict):
            session_state.set_section(float(value.get("a", a)), float(value.get("b", b)))
        else:
            raise ApiError("not_writable", path)
    else:
        raise ApiError("not_writable", path)

    return {"path": path, "before": before, "after": _safe_before(path, fl)}


def _safe_before(path: str, fl):
    try:
        v = get(path, fl=fl)
        return v.get("value") if isinstance(v, dict) and "value" in v else v
    except Exception:
        return None


def set_many(items: list, fl=None) -> dict:
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


# ------------------------------------------------------------------ call

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
        fl._State._live("transport.start()")
        return {"ok": True, "playing": 1}
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
    if op == "system.health":
        return fl.state.health()
    if op == "system.doctor":
        return fl.state.doctor()
    raise ApiError("unknown_op", op)


# ------------------------------------------------------------------ measure

def measure(spec: dict) -> dict:
    """Картина по файлам: {metric, source|sources, a, b} → кадр.

    Сейчас живые метрики (tap) не подключены — источником может быть файл или
    список файлов. Живой источник вернёт `not_available` с причиной.
    """
    import frames

    metric = spec.get("metric")
    if not metric:
        raise ApiError("bad_spec", error="нет metric")
    sources = spec.get("sources")
    source = spec.get("source")
    if source == "live" or (isinstance(sources, list) and sources and sources[0] == "live"):
        raise ApiError("not_available", error="живой источник не подключён (tap в работе)")
    if spec.get("metric") in ("sii", "partial"):
        sources = []          # этим метрикам файл-источник не нужен
    if not sources and spec.get("metric") not in ("sii", "partial"):
        if not source:
            raise ApiError("bad_spec", error="нужен source или sources")
        sources = [source]
    if isinstance(sources, str):
        sources = [sources]

    a, b = spec.get("a"), spec.get("b")
    import audit
    import perceptual as P

    if metric in ("bands", "rms", "peak", "crest", "lufs"):
        rows = []
        regions = [int(round(f)) for f in P.FC]
        for path in sources:
            m = audit.measure(path, a, b)
            if metric == "bands":
                # сравнивать абсолютные дБ между файлами нельзя: у каждого своя
                # калибровка. Берём профиль относительно собственного среднего.
                rows.append([round(float(v), 2) for v in m["bands_rel"]])
            else:
                key = {"rms": "n_mean", "peak": "peak_db", "crest": "crest", "lufs": "lufs"}[metric]
                rows.append([round(float(m[key]), 2)])
        if metric == "bands":
            axes = {"source": [_label(p) for p in sources], "band": regions}
            values = rows
        else:
            axes = {"source": [_label(p) for p in sources]}
            values = [r[0] for r in rows]
        unit = {"bands": "дБ (относительно среднего)", "rms": "соны", "peak": "дБ",
                "crest": "дБ", "lufs": "LUFS"}[metric]
        return frames.make_frame(metric if metric != "bands" else "bands", axes, values,
                                 coverage={"measured": [_label(p) for p in sources]},
                                 unit=unit)

    if metric == "transients":
        import transients
        names = ["low", "mid", "high"]
        rows = []
        for path in sources:
            a_ = transients.analyse(path, max_seconds=float(spec.get("seconds", 45.0)))
            if not a_.get("ok"):
                raise ApiError("measure_failed", error=a_.get("error"))
            rows.append([(a_["bands"].get(n) or {}).get("ts_db") for n in names])
        return frames.make_frame("transient", {"source": [_label(p) for p in sources],
                                               "band": names}, rows,
                                 unit="дБ (удар/хвост)")

    if metric == "harmony":
        import harmony
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        rows = []
        for path in sources:
            a_ = harmony.analyse(path, a, b)
            if not a_.get("ok"):
                raise ApiError("measure_failed", error=a_.get("error"))
            rows.append([round(float(x), 4) for x in a_["chroma"]])
        return frames.make_frame("harmony", {"source": [_label(p) for p in sources],
                                             "pitch": names}, rows, unit="доли")

    if metric == "wall":
        import wall
        rows, centers = [], None
        for path in sources:
            w = wall.analyze(path, a, b)
            centers = [b_["fc"] for b_ in w["bands"]]
            rows.append([round(float(x["median_db"]), 2) for x in w["bands"]])
        return frames.make_frame("wall", {"source": [_label(p) for p in sources],
                                          "band": centers}, rows,
                                 unit="дБ (медиана полосы)")

    if metric == "sii":
        import speech
        vocal, masker = spec.get("vocal"), spec.get("masker")
        if not vocal or not masker:
            raise ApiError("bad_spec", error="sii требует vocal и masker")
        r = speech.intelligibility(vocal, masker)
        bands = r["bands"]
        return frames.make_frame("sii", {"band": [b["f"] for b in bands]},
                                 [round(float(b["audibility"]), 4) for b in bands],
                                 unit="0..1", coverage={"measured": ["vocal", "masker"]})

    if metric == "partial":
        import partial
        stems = spec.get("stems") or {}
        mix = spec.get("mix")
        if not stems or not mix:
            raise ApiError("bad_spec", error="partial требует stems и mix")
        r = partial.attribute(mix, stems, a, b)
        roles = sorted(r["roles"])
        regions = list(next(iter(r["roles"].values()))["regions"])
        values = [[round(float(r["roles"][role]["regions"][reg]), 2) for reg in regions]
                  for role in roles]
        return frames.make_frame("partial", {"source": roles, "band": regions}, values,
                                 unit="дБ (частичная громкость)")

    raise ApiError("unknown_metric", metric,
                   known=["bands", "rms", "peak", "crest", "lufs", "transients",
                          "harmony", "wall", "sii", "partial"])


def compare(a: dict, b: dict, top: int | None = None) -> dict:
    import frames
    return frames.compare(a, b, top=top)


def _label(path: str) -> str:
    base = os.path.basename(str(path).replace("\\", "/"))
    return re.sub(r"\.(wav|mp3|flac|ogg|m4a)$", "", base)[:40]


# ------------------------------------------------------------------ служебное

def _project_name() -> str | None:
    try:
        import render_job
        return render_job.window_title()
    except Exception:
        return None


def _transport(fl) -> dict:
    playing = int(fl._State._live("transport.isPlaying()", attempts=2))
    pos = float(fl._State._live("transport.getSongPos()", attempts=2))
    return {"playing": bool(playing), "position": round(pos, 6),
            "seconds": round(pos * 240.0, 3), "tempo": fl.state.tempo()}
