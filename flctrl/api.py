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

# Кадры живут под id: сравнение принимает id, а не мегабайты таблиц.
STORE: dict = {}
_SEQ = {"frame": 0, "change": 0}
UNDO: list = []
DRY_RUN = False
_PROJECT = {"path": None, "parsed": None, "mtime": None}


def use_project(path: str | None) -> dict:
    """Указать файл проекта для точных файловых запросов (темп, плейлист, ноты).

    Живой FL и файл — разные источники: файл даёт точный темп и состав, мост —
    текущее состояние. Держим оба, но не смешиваем.
    """
    if path is None:
        _PROJECT.update({"path": None, "parsed": None, "mtime": None})
        return {"project": None}
    import timebase_file
    p = str(path).replace("\\", "/")
    mtime = os.path.getmtime(p)
    _PROJECT.update({"path": p, "parsed": timebase_file.load(p), "mtime": mtime})
    return {"project": p, "tempo": _PROJECT["parsed"].tempo,
            "clips": len(_PROJECT["parsed"].clips)}


def _project():
    """Разобранный проект: из кэша, по mtime, либо по заголовку окна FL."""
    import timebase_file
    path = _PROJECT.get("path")
    if not path:
        title = _project_name() or ""
        head = title.split(" - ")[0].strip()
        if not head.lower().endswith(".flp"):
            raise ApiError("no_project", error="не указан файл проекта (use_project)")
        path = head.replace("\\", "/")
    mtime = os.path.getmtime(path)
    if _PROJECT.get("parsed") is None or _PROJECT.get("mtime") != mtime:
        _PROJECT.update({"path": path, "parsed": timebase_file.load(path), "mtime": mtime})
    return _PROJECT["parsed"]


def _next_id(prefix: str) -> str:
    _SEQ[prefix] = _SEQ.get(prefix, 0) + 1
    return "%s%d" % (prefix[0], _SEQ[prefix])


def _store_frame(frame: dict) -> dict:
    fid = _next_id("frame")
    frame = dict(frame)
    frame["id"] = fid
    STORE[fid] = frame
    return frame


def _resolve(x):
    """Принять id кадра или сам кадр."""
    if isinstance(x, str):
        if x not in STORE:
            raise ApiError("unknown_frame", x)
        return STORE[x]
    return x


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

    if t[0] == "measure":
        if len(t) == 1:
            return [{"id": k, "metric": v.get("metric"), "unit": v.get("unit")}
                    for k, v in STORE.items()]
        fid = t[1]
        if fid not in STORE:
            raise ApiError("unknown_frame", path)
        return STORE[fid]

    if t[0] == "playlist":
        pr = _project()
        if len(t) == 1:
            return {"clips": len(pr.clips), "tracks": len(pr.tracks()), "ppq": pr.ppq}
        if t[1] == "clip":
            if len(t) == 2:
                return [{"id": i, "track": c.track, "kind": "pattern" if c.is_pattern else "audio",
                         "item": c.item, "start_s": round(pr.seconds_at(c.pos), 3),
                         "length_s": round(pr.seconds_at(c.length), 3)}
                        for i, c in enumerate(pr.clips)][:500]
            try:
                c = pr.clips[int(t[2])]
            except (ValueError, IndexError):
                raise ApiError("path_not_found", path)
            return {"id": int(t[2]), "track": c.track,
                    "kind": "pattern" if c.is_pattern else "audio",
                    "item": c.item, "clip_id": c.clip_id,
                    "start_s": round(pr.seconds_at(c.pos), 3),
                    "length_s": round(pr.seconds_at(c.length), 3),
                    "source_offset_s": round(c.start_offset, 3)}

    if t[0] == "markers":
        return _project().markers

    if t[0] == "section":
        import session_state
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
    if t[0] == "measure":
        if len(t) == 1:
            return [{"id": k, "metric": v.get("metric"), "unit": v.get("unit")}
                    for k, v in STORE.items()]
        if t[1] not in STORE:
            raise ApiError("unknown_frame", path)
        return STORE[t[1]]
    if t[0] == "playlist":
        pr = _project()
        if len(t) == 1:
            return {"clips": len(pr.clips), "tracks": len(pr.tracks()), "ppq": pr.ppq}
        if t[1] == "clip":
            if len(t) == 2:
                return [{"id": i, "track": c.track, "kind": "pattern" if c.is_pattern else "audio",
                         "item": c.item, "start_s": round(pr.seconds_at(c.pos), 3),
                         "length_s": round(pr.seconds_at(c.length), 3)}
                        for i, c in enumerate(pr.clips)][:500]
            try:
                c = pr.clips[int(t[2])]
            except (ValueError, IndexError):
                raise ApiError("path_not_found", path)
            return {"id": int(t[2]), "track": c.track,
                    "kind": "pattern" if c.is_pattern else "audio",
                    "item": c.item, "clip_id": c.clip_id,
                    "start_s": round(pr.seconds_at(c.pos), 3),
                    "length_s": round(pr.seconds_at(c.length), 3),
                    "source_offset_s": round(c.start_offset, 3)}

    if t[0] == "markers":
        return _project().markers

    if t[0] == "section":
        import session_state
        a_, b_ = session_state.get_section()
        if len(t) == 1:
            return {"a": a_, "b": b_}
        if len(t) == 2 and t[1] in ("a", "b"):
            return {"value": a_ if t[1] == "a" else b_, "type": "float", "unit": "с"}
        raise ApiError("path_not_found", path)

    if WILDCARD in t:
        return _get_wild(path, fl)

    if t[0] == "project":
        try:
            pr = _project()
        except Exception:
            pr = None
        if len(t) == 1:
            if pr:
                return {"name": os.path.basename(pr.path), "tempo": pr.tempo,
                        "time_sig": "%d/%d" % (pr.ts_num, pr.ts_den), "ppq": pr.ppq,
                        "clips": len(pr.clips), "channels": len(pr.channels),
                        "source": "file"}
            return {"name": _project_name(), "tempo": fl.state.tempo(), "source": "bridge"}
        if t[1] == "name":
            return {"value": os.path.basename(pr.path) if pr else _project_name(),
                    "type": "string"}
        if t[1] == "tempo":
            v = pr.tempo if pr else fl.state.tempo()
            return {"value": v, "type": "float", "unit": "BPM",
                    "source": "file" if pr else "bridge"}

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

def dry_run(on: bool = True) -> dict:
    """Включить/выключить режим «только показать»: set ничего не меняет."""
    global DRY_RUN
    DRY_RUN = bool(on)
    return {"dry_run": DRY_RUN}


def undo(n: int = 1) -> dict:
    """Откатить последние n изменений (в обратном порядке)."""
    done = []
    for _ in range(max(0, int(n))):
        if not UNDO:
            break
        ch = UNDO.pop()
        try:
            set_value(ch["path"], ch["before"], record=False)
            done.append(ch["path"])
        except Exception as exc:
            done.append({"path": ch["path"], "error": str(exc)[:80]})
    return {"undone": done, "left": len(UNDO)}


def set_value(path: str, value, fl=None, record: bool = True) -> dict:
    fl = fl or _fl()
    t = tokens(path)
    before = _safe_before(path, fl)
    if DRY_RUN:
        return {"path": path, "before": before, "after": value, "dry_run": True}

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
        import session_state
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

    after = _safe_before(path, fl)
    if record:
        UNDO.append({"path": path, "before": before, "after": after})
        _SEQ["change"] = _SEQ.get("change", 0) + 1
    return {"path": path, "before": before, "after": after}


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
    if op == "plan.apply":
        edits = a.get("edits") or a.get("plan") or []
        track_map = a.get("track_map") or {}
        if not edits or not track_map:
            raise ApiError("bad_args", op, need=["edits", "track_map"])
        return fl.apply.plan(edits, track_map, a.get("slot"))

    if op == "eq.set":
        if "track" not in a or "slot" not in a or not a.get("gains"):
            raise ApiError("bad_args", op, need=["track", "slot", "gains"])
        return fl.apply.eq(int(a["track"]), int(a["slot"]), a["gains"])

    if op == "inverse.solve":
        import invmix
        stems = a.get("stems") or {}
        against = a.get("against") or []
        if not stems or not against:
            raise ApiError("bad_args", op, need=["stems", "against"])
        if isinstance(against, str):
            against = [against]
        a0, b0 = a.get("a"), a.get("b")
        target = _avg_metrics([_metrics_of(p, a0, b0) for p in against])
        loaded, fs = {}, None
        for role, path in stems.items():
            # грузим целиком и режем по окну: брать «первые N секунд» нельзя —
            # у трека там может быть тишина, и задача становится вырожденной
            x, fs_i = invmix.load(path)
            if fs is None:
                fs = fs_i
            if a0 or b0:
                lo = int((a0 or 0.0) * fs)
                hi = int((b0 or (len(x) / fs)) * fs)
                x = x[lo:hi] if hi > lo else x
            elif a.get("max_seconds"):
                x = x[:int(float(a["max_seconds"]) * fs)]
            loaded[role] = x
        loaded = invmix.align(*loaded.values())
        # Калибровка: абсолютный уровень наших дорожек и референса разный, а
        # гейнами его всё равно не выправить — выравниваем средний уровень
        # цели по нашей сумме, иначе оптимизатор видит только постоянный сдвиг.
        base = invmix.metrics(invmix.mix(dict(zip(stems, loaded)),
                                         {k: 0.0 for k in stems}), fs)
        if target.get("bands_db") and base.get("bands_db"):
            shift = (sum(base["bands_db"]) / len(base["bands_db"])
                     - sum(target["bands_db"]) / len(target["bands_db"]))
            target["bands_db"] = [b + shift for b in target["bands_db"]]
            target["_level_shift_db"] = round(shift, 2)
        res = invmix.solve(dict(zip(stems, loaded)), fs, target,
                           cap_db=float(a.get("cap_db", 6.0)),
                           step_db=float(a.get("step_db", 0.5)))
        res["target_refs"] = [_label(p) for p in against]
        return res

    if op == "record.start" or op == "record.stop":
        raise ApiError("not_available", op,
                       error="запись дорожек появится с tap (см. SPEC §9)")

    if op == "mix.report":
        import report
        mine = a.get("mine")
        if not mine:
            raise ApiError("bad_args", op, need=["mine"])
        rep = report.build(mine, a.get("refs"), a.get("a"), a.get("b"),
                           stems=a.get("stems"), targets_path=a.get("targets_path"))
        if a.get("markdown"):
            return {"markdown": report.to_markdown(rep), "actions": rep["actions"]}
        # контекст с путями раздувает ответ: отдаём без него
        rep.pop("ctx", None)
        return rep

    if op == "mix.decide":
        import partial, decide
        mix, stems = a.get("mix"), a.get("stems") or {}
        if not mix or not stems:
            raise ApiError("bad_args", op, need=["mix", "stems"])
        attr = partial.attribute(mix, stems, a.get("a"), a.get("b"))
        pl = decide.plan(attr, cap_db=float(a.get("cap_db", 3.0)),
                         max_edits=int(a.get("max_edits", 12)),
                         roles_map=a.get("roles_map"),
                         targets=a.get("targets"))
        return pl

    if op == "mix.simulate":
        import simulate
        mix, stems, edits = a.get("mix"), a.get("stems") or {}, a.get("edits") or []
        if not mix or not stems or not edits:
            raise ApiError("bad_args", op, need=["mix", "stems", "edits"])
        out = simulate.simulate(mix, stems, edits, a.get("outdir"))
        return out

    if op == "pan.plan":
        import panplan
        stems = a.get("stems") or {}
        if not stems:
            raise ApiError("bad_args", op, need=["stems"])
        return panplan.plan(stems, a.get("a"), a.get("b"),
                            section_s=float(a.get("section_s", 8.0)),
                            max_step=float(a.get("max_step", 0.15)),
                            max_pan=float(a.get("max_pan", 0.7)))

    if op == "pan.check":
        import panplan
        return panplan.check(a.get("plan") or {}, a.get("section_s"))

    if op == "pan.move":
        import realtime, fl_lib
        track = a.get("track")
        points = a.get("points")
        if track is None or not points:
            raise ApiError("bad_args", op, need=["track", "points"])
        secs = float(a.get("seconds", 8.0))
        if DRY_RUN:
            return {"dry_run": True, "track": int(track), "points": points,
                    "seconds": secs}
        res = realtime.move_pan(fl_lib, int(track), [tuple(p) for p in points],
                                duration_s=secs, rate_hz=float(a.get("rate_hz", 20.0)))
        mover = res["mover"]
        # Mover работает в своём потоке: ждём его, иначе вернём пустую статистику
        th = getattr(mover, "_thread", None)
        if th is not None:
            th.join(secs + 2.0)
        return {"track": int(track), "stats": dict(mover.stats),
                "pan_after": _safe_before("/track/%d/pan" % int(track), fl_lib)}

    if op == "clip.at":
        pr = _project()
        sec = a.get("seconds")
        if sec is None and a.get("bar") is not None:
            sec = float(a["bar"]) * 4.0 * pr.beat_seconds
        if sec is None:
            raise ApiError("bad_args", op, need=["seconds или bar"])
        lines = pr.active_at(pr.ticks_at(float(sec)))
        return {"seconds": round(float(sec), 3),
                "bar": round(float(sec) / (4.0 * pr.beat_seconds), 2),
                "tempo": pr.tempo, "active": lines, "count": len(lines)}

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
    against = spec.get("against")
    if source == "live" or (isinstance(sources, list) and sources and sources[0] == "live"):
        raise ApiError("not_available", error="живой источник не подключён (tap в работе)")
    if spec.get("metric") in ("sii", "partial", "masking"):
        sources = []          # этим метрикам файл-источник не нужен
    if (not sources and spec.get("metric") not in ("sii", "partial", "masking")
            and spec.get("by") != "role"):
        if not source:
            raise ApiError("bad_spec", error="нужен source или sources")
        sources = [source]
    if isinstance(sources, str):
        sources = [sources]

    a, b = spec.get("a"), spec.get("b")
    import audit
    import perceptual as P

    if metric in ("bands", "rms", "peak", "crest", "lufs") and not spec.get("by"):
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
        return _finish(frames.make_frame("bands" if metric == "bands" else metric,
                                         axes, values,
                                         coverage={"measured": [_label(p) for p in sources]},
                                         unit=unit), metric, spec)

    regions = [int(round(f)) for f in P.FC]
    by = spec.get("by")
    if by == "section":
        if not sources:
            raise ApiError("bad_spec", error="by=section требует source")
        import soundfile as sf
        dur = sf.info(sources[0]).duration
        a0 = float(a or 0.0)
        b0 = float(b or dur)

        bounds = None
        if spec.get("sections_from") == "markers":
            try:
                pr = _project()
                marks = sorted({pr.seconds_at(m.get("pos", 0)) if isinstance(m, dict) else 0.0
                                for m in (pr.markers or [])})
                marks = [m for m in marks if a0 - 1e-6 <= m <= b0 + 1e-6]
                if len(marks) >= 2:
                    bounds = marks + [b0]
            except Exception:
                bounds = None
        if bounds:
            rows, labels = [], []
            for i in range(len(bounds) - 1):
                t, e = bounds[i], bounds[i + 1]
                m = audit.measure(sources[0], t, e)
                rows.append([round(float(v), 2) for v in m["bands_rel"]])
                labels.append("%.1f–%.1f с" % (t, e))
            return _finish(frames.make_frame("bands", {"section": labels, "band": regions},
                                             rows, unit="дБ (относительно среднего)"),
                           metric, spec)
        step = float(spec.get("section_s", 8.0))
        rows, labels = [], []
        t = a0
        while t < b0 - 1e-6:
            e = min(t + step, b0)
            m = audit.measure(sources[0], t, e)
            rows.append([round(float(v), 2) for v in m["bands_rel"]])
            labels.append("%.0f–%.0f с" % (t, e))
            t = e
        return _finish(frames.make_frame("bands", {"section": labels, "band": regions},
                                         rows, unit="дБ (относительно среднего)"),
                       metric, spec)

    if by == "role":
        stems = spec.get("stems")
        if not stems:
            stems = roles_for(sources) if sources else {}
        if not stems:
            raise ApiError("bad_spec",
                           error="by=role требует stems {роль: путь} или sources")
        roles = list(stems)
        rows = []
        for role in roles:
            m = audit.measure(stems[role], a, b)
            rows.append([round(float(v), 2) for v in m["bands_rel"]])
        return _finish(frames.make_frame("bands", {"role": roles, "band": regions},
                                         rows, unit="дБ (относительно среднего)"),
                       metric, spec)

    if metric == "transients":
        import transients
        names = ["low", "mid", "high"]
        rows = []
        for path in sources:
            a_ = transients.analyse(path, max_seconds=float(spec.get("seconds", 45.0)))
            if not a_.get("ok"):
                raise ApiError("measure_failed", error=a_.get("error"))
            rows.append([(a_["bands"].get(n) or {}).get("ts_db") for n in names])
        return _finish(frames.make_frame("transient",
                                         {"source": [_label(p) for p in sources], "band": names},
                                         rows, unit="дБ (удар/хвост)"), metric, spec)

    if metric == "harmony":
        import harmony
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        rows = []
        for path in sources:
            a_ = harmony.analyse(path, a, b)
            if not a_.get("ok"):
                raise ApiError("measure_failed", error=a_.get("error"))
            rows.append([round(float(x), 4) for x in a_["chroma"]])
        return _finish(frames.make_frame("harmony",
                                         {"source": [_label(p) for p in sources], "pitch": names},
                                         rows, unit="доли"), metric, spec)

    if metric == "wall":
        import wall
        rows, centers = [], None
        for path in sources:
            w = wall.analyze(path, a, b)
            centers = [b_["fc"] for b_ in w["bands"]]
            rows.append([round(float(x["median_db"]), 2) for x in w["bands"]])
        return _finish(frames.make_frame("wall",
                                         {"source": [_label(p) for p in sources], "band": centers},
                                         rows, unit="дБ (медиана полосы)"), metric, spec)

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

    if metric == "masking":
        import audit
        stems = spec.get("stems") or (roles_for(sources) if sources else {})
        if len(stems) < 2:
            raise ApiError("bad_spec", error="masking требует два и более источников")
        levels, regions = {}, None
        for role, path in stems.items():
            m = audit.measure(path, spec.get("a"), spec.get("b"))
            levels[role] = m["bands_db"]
            regions = regions or _band_labels(m)
        pairs, rows = [], []
        for masker in sorted(levels):
            for target in sorted(levels):
                if masker == target:
                    continue
                pairs.append("%s>%s" % (masker, target))
                # Маска имеет смысл только там, где цель вообще звучит: если в
                # полосе у цели на 40 дБ меньше её же пика, это «нечего забивать».
                peak = max(levels[target]) if levels[target] else 0.0
                rows.append([0.0 if lt < peak - 40 else round(min(60.0, max(0.0, lm - lt)), 1)
                             for lm, lt in zip(levels[masker], levels[target])])
        return _finish(frames.make_frame("masking", {"pair": pairs, "band": regions}, rows,
                                         unit="дБ (насколько маскер громче цели)"),
                       metric, spec)

    raise ApiError("unknown_metric", metric,
                   known=["bands", "rms", "peak", "crest", "lufs", "transients",
                          "harmony", "wall", "sii", "partial", "masking"])


def _band_labels(m: dict) -> list:
    """Подписи полос: частоты из метрики, иначе индексы."""
    for key in ("band_hz", "bands_hz", "centers"):
        if isinstance(m.get(key), list):
            return [int(x) for x in m[key]]
    n = len(m.get("bands_db") or [])
    return ["полоса %d" % (i + 1) for i in range(n)]


def roles_for(sources: list) -> dict:
    """Разложить файлы по ролям: имя → спектр → other.

    Нужна, чтобы «по ролям» работало без ручного словаря: роли определяются
    тем же модулем, что и в остальной библиотеке.
    """
    import roles
    out: dict = {}
    for i, path in enumerate(sources):
        role = roles.role_of(os.path.basename(str(path)), str(path))
        key = role if role not in out else "%s_%d" % (role, i)
        out[key] = path
    return out


def _finish(frame: dict, metric: str, spec: dict) -> dict:
    """Если в спеке есть against — вернуть картину вместе с коридором и вылетами."""
    against = spec.get("against")
    if not against:
        return _store_frame(frame)
    if isinstance(against, str):
        against = [against]
    refs = []
    for i, path in enumerate(against):
        f = measure({"metric": metric, "source": path,
                     "a": spec.get("a"), "b": spec.get("b"),
                     "seconds": spec.get("seconds")})
        refs.append(f)
    import frames
    cor = frames.corridor(refs)
    cmp_ = frames.compare_corridor(frame, cor)
    frame = _store_frame(frame)
    return {"ours": frame, "refs": [f["axes"]["source"][0] for f in refs],
            "corridor": {"n": cor["n"], "median": cor["median"]["values"],
                         "lo": cor["lo"]["values"], "hi": cor["hi"]["values"]},
            "compare": cmp_}


def compare(a, b, top: int | None = None) -> dict:
    """Сравнить картину с картиной или с набором (коридор по референсам).

    Принимает и кадры, и их id — id короче и не раздувает контекст.
    """
    import frames
    a = _resolve(a)
    if isinstance(b, list):
        b = [_resolve(x) for x in b]
        cor = frames.corridor(b)
        return frames.compare_corridor(a, cor, top=top)
    return frames.compare(a, _resolve(b), top=top)


def _metrics_of(path: str, a=None, b=None) -> dict:
    import invmix
    x, fs = invmix.load(path)
    import soundfile as sf
    info = sf.info(path)
    start = int((a or 0.0) * info.samplerate)
    frames = int(((b or info.duration) - (a or 0.0)) * info.samplerate)
    x = x[max(0, start):start + max(1, frames)]
    if x.ndim == 1:
        x = x[:, None]
    return invmix.metrics(x, fs)


def _avg_metrics(items: list) -> dict:
    """Средняя цель по нескольким референсам — покомпонентно, без хитростей."""
    out = {}
    bands = [m.get("bands_db") for m in items if m.get("bands_db")]
    if bands:
        n = min(len(b) for b in bands)
        out["bands_db"] = [sum(b[i] for b in bands) / len(bands) for i in range(n)]
    regs = {}
    for m in items:
        for k, v in (m.get("regions_pct") or {}).items():
            regs.setdefault(k, []).append(v)
    out["regions_pct"] = {k: sum(v) / len(v) for k, v in regs.items()}
    for key in ("sharp", "crest", "corr"):
        vals = [m[key] for m in items if m.get(key) is not None]
        if vals:
            out[key] = sum(vals) / len(vals)
    return out


def _label(path: str, index: int | None = None) -> str:
    base = os.path.basename(str(path).replace("\\", "/"))
    name = re.sub(r"\.(wav|mp3|flac|ogg|m4a)$", "", base)[:36]
    return ("%02d:%s" % (index, name)) if index is not None else name


# ------------------------------------------------------------------ справка

OPS_HELP = {
    "plugin.insert": "{track, slot, name} — вставить плагин",
    "plugin.delete": "{track, slot} — удалить плагин",
    "plugin.replace": "{track, slot, name} — заменить",
    "transport.play": "{} — играть",
    "transport.stop": "{} — стоп",
    "save": "{backup?} — сохранить проект",
    "track.add": "{index?, count?} — добавить дорожку",
    "track.delete": "{index} — удалить дорожку",
    "plan.apply": "{edits, track_map, slot?} — применить план правок",
    "eq.set": "{track, slot, gains} — полосы эквалайзера",
    "clip.at": "{seconds|bar} — что звучит в этот момент (локальное время источников)",
    "inverse.solve": "{stems, against, cap_db} — обратная задача: гейны под цели микса",
    "pan.plan": "{stems, a, b, section_s} — план панорамы по секциям",
    "pan.check": "{plan} — проверка шага и зеркальности",
    "pan.move": "{track, points:[[t,pan]], seconds, rate_hz} — вести панораму вживую",
    "mix.report": "{mine, refs, a, b, stems, markdown} — проверки и действия одним вызовом",
    "mix.decide": "{mix, stems, a, b} — правки с капом",
    "mix.simulate": "{mix, stems, edits} — микс с правками до записи",
    "use_project": "не операция: api.use_project(path) — разобрать .flp",
    "system.health": "{} — жив ли мост",
    "system.doctor": "{} — пробы API",
}

PATHS_HELP = [
    "/project", "/transport",
    "/track/{i}", "/track/{i}/volume|pan|mute|solo",
    "/track/{i}/slot/{s}", "/track/{i}/slot/{s}/band/{b}",
    "/track/{i}/slot/{s}/param/{p}",
    "/track/{i}/send/{d}", "/channel/{i}", "/playlist/clip/{id}",
    "/section", "/refs/{name}", "/measure/{id}",
]


def coverage() -> dict:
    """Что измерено, а что нет — защита от «забыл посмотреть».

    Смотрит на кадры в хранилище: по каждой метрике из реестра говорит,
    есть ли замер, сколько кадров и какие метрики вообще не трогались.
    """
    import frames
    have: dict = {}
    for fid, fr in STORE.items():
        m = fr.get("metric")
        have.setdefault(m, []).append(fid)
    rows = []
    for name, info in sorted(frames.METRICS.items()):
        got = have.get(name, [])
        rows.append({"metric": name, "unit": info["unit"],
                     "axes": list(info["axes"]), "frames": got,
                     "measured": bool(got)})
    missing = [r["metric"] for r in rows if not r["measured"]]
    return {"rows": rows, "measured": [r["metric"] for r in rows if r["measured"]],
            "missing": missing,
            "share": round(len(missing) and (len(rows) - len(missing)) / len(rows) or 0, 3)}


def help(what: str | None = None) -> dict:
    """Карта поверхности: пути, операции, метрики, глаголы.

    Один вызов вместо чтения документации: что вообще бывает и как называется.
    """
    import frames
    if what in (None, "", "all"):
        return {"verbs": ["list", "get", "set", "call", "measure", "compare"],
                "paths": PATHS_HELP,
                "ops": OPS_HELP,
                "metrics": {k: {"unit": v["unit"], "axes": list(v["axes"]),
                                "source": v["source"]} for k, v in frames.METRICS.items()},
                "measures_now": ["bands", "rms", "peak", "crest", "lufs",
                                 "transients", "harmony", "wall", "sii", "partial",
                                 "masking", "stereo", "erb"]}
    if what == "paths":
        return {"paths": PATHS_HELP}
    if what == "ops":
        return {"ops": OPS_HELP}
    if what == "metrics":
        return {"metrics": {k: v["unit"] for k, v in frames.METRICS.items()}}
    if what == "coverage":
        return coverage()
    raise ApiError("unknown_help", what, known=["paths", "ops", "metrics"])


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
