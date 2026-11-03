"""apply.py — применить план к живому FL через эквалайзер, без перезагрузки.

План говорит «этому инструменту в этой полосе +3 дБ». Чтобы это стало звуком,
нужен эквалайзер в слоте и точная установка полосы и усиления.

Две вещи здесь намеренно НЕ угадываются:

1. Шкалы плагинов. Они не документированы и у каждого свои, поэтому значение
   подбирается по подписи: ставим — читаем, что показывает плагин — правим.
2. Что за плагин в слоте. Никаких списков имён: тип определяется по тому, что
   плагин показывает. Параметр, чья подпись читается как герцы, — частота;
   как децибелы — усиление. Пара «частота + усиление» — полоса. Плагин, у
   которого таких пар две и больше, ведёт себя как эквалайзер, и с ним можно
   работать. Компрессор или лимитер частот не показывает — значит, не трогаем.

Так это работает и с Fruity Parametric EQ, и с Pro-Q, и с TDR Nova, и с любым
другим эквалайзером, которого ещё нет в проекте.

    python -m flctrl.apply --track 1 --slot 0 --gain mid=3 --gain pres=-2
"""
from __future__ import annotations

import argparse
import re

REGION_HZ = {"sub": 45.0, "bass": 90.0, "lowmid": 200.0, "mid": 700.0,
             "upmid": 2000.0, "pres": 4500.0, "air": 9000.0}

# FL даёт по 10 слотов эффектов на дорожку — это свойство хоста, не догадка
SLOTS_PER_TRACK = 10


def parse_hz(text: str) -> float | None:
    t = (text or "").strip().lower().replace(" ", "")
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)(k?)(?:hz)?$", t)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    if m.group(2) == "k":
        v *= 1000.0
    return v


def parse_db(text: str) -> float | None:
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*db", (text or "").strip().lower())
    return float(m.group(1).replace(",", ".")) if m else None


def _looks_hz(text: str) -> bool:
    return "hz" in (text or "").lower() and parse_hz(text) is not None


def bands_from_params(params: list[dict]) -> list[dict]:
    """Полосы эквалайзера из параметров — по подписям, а не по именам.

    Возвращает [{'n','freq_i','level_i','hz','gain'}] в порядке появления.
    Пустой список означает: плагин не показывает частот и усилений, то есть
    это не эквалайзер (или его интерфейс скрыт).
    """
    freqs, levels = [], []
    for p in params:
        text = p.get("text") or ""
        if _looks_hz(text):
            freqs.append(p)
        elif parse_db(text) is not None:
            levels.append(p)
    freqs.sort(key=lambda p: p["i"])
    levels.sort(key=lambda p: p["i"])
    out = []
    for k, (f, l) in enumerate(zip(freqs, levels), start=1):
        out.append({"n": k, "freq_i": f["i"], "level_i": l["i"],
                    "hz": parse_hz(f.get("text") or ""),
                    "gain": parse_db(l.get("text") or "")})
    return out


def is_eq(params: list[dict], min_bands: int = 2) -> bool:
    """Эквалайзер ли это: минимум две полосы «частота + усиление»."""
    return len(bands_from_params(params)) >= min_bands


def plugin_at(fl, track: int, slot: int) -> str | None:
    try:
        for p in fl.state.plugins([track]) or []:
            if int(p["slot"]) == slot:
                return p.get("name")
    except Exception:
        pass
    return None


def eq_slot(fl, track: int):
    """Слот, где уже стоит эквалайзер: (номер, имя) или (None, None).

    Определяется по параметрам: у эквалайзера есть полосы «частота + усиление».
    """
    for slot in occupied_slots(fl, track):
        params = fl.act.params(track, slot)
        if params and is_eq(params):
            return slot, plugin_at(fl, track, slot)
    return None, None


def free_slot(fl, track: int, skip: set | None = None) -> int | None:
    """Первый слот без плагина или None, если свободных нет."""
    skip = skip or set()
    used = set()
    try:
        used = {int(p["slot"]) for p in (fl.state.plugins([track]) or [])}
    except Exception:
        pass
    for s in range(SLOTS_PER_TRACK):
        if s in used or s in skip:
            continue
        if not fl.act.params(track, s):
            return s
    return None


def set_param_until(get_params, set_param, index: int, target: float,
                    parse, tolerance: float, lo: float = 0.0, hi: float = 1.0,
                    iters: int = 12, read_text=None) -> dict:
    """Подобрать нормализованное значение так, чтобы подпись плагина дала цель.

    Монотонность предполагается: частота и усиление растут с параметром.
    Если подпись не читается — возвращаем то, что есть, и честно помечаем.
    """
    best = None
    for _ in range(iters):
        mid = (lo + hi) / 2
        set_param(index, mid)
        if read_text is not None:
            text = read_text(index)
        else:
            params = get_params()
            p = next((x for x in params if x["i"] == index), None)
            text = p["text"] if p else None
        value = parse(text) if text else None
        if value is None:
            return {"index": index, "value": mid, "text": None, "ok": False}
        best = {"index": index, "value": round(mid, 4), "text": text,
                "actual": value}
        if abs(value - target) <= tolerance:
            best["ok"] = True
            return best
        if value < target:
            lo = mid
        else:
            hi = mid
    if best:
        best["ok"] = abs((best.get("actual") or 0) - target) <= tolerance * 3
    return best or {"index": index, "ok": False}


def describe_slot(fl, track: int, slot: int) -> dict:
    """Что в слоте: имя плагина и найденные полосы «частота + усиление».

    Это примитив: он ничего не решает и ничего не меняет. По нему видно, можно
    ли вообще работать с этим плагином как с эквалайзером и какие у него полосы.
    """
    params = fl.act.params(track, slot)
    bands = bands_from_params(params) if params else []
    return {"track": track, "slot": slot, "plugin": plugin_at(fl, track, slot),
            "used": bool(params), "is_eq": len(bands) >= 2, "bands": bands,
            "param_count": len(params)}


def occupied_slots(fl, track: int) -> list:
    """Занятые слоты дорожки: пустые проверять незачем, это лишние запросы."""
    try:
        return sorted(int(p["slot"]) for p in (fl.state.plugins([track]) or []))
    except Exception:
        return list(range(SLOTS_PER_TRACK))


def eq_slots(fl, track: int) -> list:
    """Слоты, где стоит что-то похожее на эквалайзер (по параметрам)."""
    out = []
    for slot in occupied_slots(fl, track):
        info = describe_slot(fl, track, slot)
        if info["is_eq"]:
            out.append({"slot": slot, "plugin": info["plugin"], "bands": len(info["bands"])})
    return out


def set_band(fl, track: int, slot: int, band: int,
             hz: float | None = None, db: float | None = None,
             q: float | None = None) -> dict:
    """Поставить одну полосу: номер полосы и что именно менять.

    Примитив без семантики «низ/середина»: частоту и усиление задаёт вызывающий.
    Если параметр не задан — он не трогается.
    """
    if fl is None:
        import fl_lib as fl
    params = fl.act.params(track, slot)
    bands = bands_from_params(params)
    target = next((b for b in bands if b["n"] == band), None)
    if target is None:
        return {"ok": False, "error": "полосы %d нет (есть %s)"
                                      % (band, [b["n"] for b in bands])}

    def get_params():
        return fl.act.params(track, slot)

    def set_param(index: int, value: float):
        fl.act.param(track, slot, index, value)

    def read_text(index: int):
        try:
            return fl.act.param_text(track, slot, index)
        except Exception:
            return None

    out = {"ok": True, "band": band, "freq": None, "gain": None, "q": None}
    if hz is not None:
        f = set_param_until(get_params, set_param, target["freq_i"], float(hz),
                            parse_hz, tolerance=max(5.0, float(hz) * 0.1),
                            read_text=read_text)
        out["freq"] = f.get("text")
        out["ok"] = out["ok"] and bool(f.get("ok"))
    if db is not None:
        g = set_param_until(get_params, set_param, target["level_i"], float(db),
                            parse_db, tolerance=0.3, read_text=read_text)
        out["gain"] = g.get("text")
        out["ok"] = out["ok"] and bool(g.get("ok"))
    if q is not None:
        # Q ищем по подписи: у разных плагинов это «Q», «Band N Q», «Width»
        qp = None
        for p in get_params():
            name = (p.get("name") or "").lower()
            if p["i"] in (target["freq_i"], target["level_i"]):
                continue
            if " q" in name or name.endswith("q") or "width" in name or " bw" in name:
                if parse_db(p.get("text") or "") is None and not _looks_hz(p.get("text") or ""):
                    qp = p
                    break
        if qp:
            qr = set_param_until(get_params, set_param, qp["i"], float(q),
                                 lambda t: float(str(t).replace(",", ".")), tolerance=0.05)
            out["q"] = qr.get("text")
            out["ok"] = out["ok"] and bool(qr.get("ok"))
        else:
            out["q"] = None
            out["q_note"] = "параметра Q у плагина не нашлось"
    now = get_params()
    out["now"] = {"freq": next((p["text"] for p in now if p["i"] == target["freq_i"]), None),
                  "gain": next((p["text"] for p in now if p["i"] == target["level_i"]), None)}
    return out


def apply_eq(fl, track: int, slot: int, gains: dict, verify: bool = True) -> dict:
    """Выставить полосы эквалайзера по плану: {region: db}.

    `fl` — клиент библиотеки (или None: тогда берётся fl_lib сам).
    """
    if fl is None:
        import fl_lib as fl

    params = fl.act.params(track, slot)
    if not params:
        return {"ok": False, "track": track, "slot": slot,
                "error": "в слоте нет плагина или нет параметров"}

    def get_params():
        return fl.act.params(track, slot)

    def set_param(index: int, value: float):
        fl.act.param(track, slot, index, value)

    def read_text(index: int):
        try:
            return fl.act.param_text(track, slot, index)
        except Exception:
            return None

    bands = bands_from_params(get_params())
    if not bands:
        return {"ok": False, "track": track, "slot": slot,
                "error": "плагин «%s» не показывает частот и усилений — это не эквалайзер"
                         % (plugin_at(fl, track, slot) or "?"),
                "names": sorted({p["name"] for p in params})[:6]}

    applied = []
    used: set = set()
    for region, db in gains.items():
        hz = REGION_HZ.get(region)
        if hz is None or abs(db) < 0.2:
            continue
        free = [b for b in bands if b["n"] not in used]
        if not free:
            break
        with_hz = [b for b in free if b.get("hz") is not None]
        band = (min(with_hz, key=lambda b: abs((b["hz"] or hz) - hz) / max(hz, 1.0))
                if with_hz else free[0])
        used.add(band["n"])

        f = set_param_until(get_params, set_param, band["freq_i"], hz,
                            parse_hz, tolerance=hz * 0.15, read_text=read_text)
        g = set_param_until(get_params, set_param, band["level_i"], db,
                            parse_db, tolerance=0.4, read_text=read_text)
        applied.append({"region": region, "band": band["n"], "want_hz": hz, "want_db": db,
                        "freq_text": f.get("text"), "gain_text": g.get("text"),
                        "freq_i": band["freq_i"], "level_i": band["level_i"],
                        "freq_ok": bool(f.get("ok")), "gain_ok": bool(g.get("ok"))})

    ok = all(a["freq_ok"] and a["gain_ok"] for a in applied) if applied else False
    check = None
    if verify and applied:
        now = get_params()
        check = []
        for a in applied:
            lvl = next((p["text"] for p in now if p["i"] == a["level_i"]), None)
            frq = next((p["text"] for p in now if p["i"] == a["freq_i"]), None)
            check.append({"band": a["band"], "freq": frq, "level": lvl})
    return {"ok": ok, "applied": applied, "verify": check,
            "plugin": plugin_at(fl, track, slot)}


def apply_plan(fl, edits: list[dict], track_map: dict, slot: int | None = None,
               ensure_eq: bool = True, save: bool = True) -> dict:
    """Применить план целиком: сгруппировать правки по инструментам и вставить EQ.

    Один инструмент — одна дорожка — один эквалайзер: полосы разных правок
    складываются в усиления по областям, поэтому несколько строк плана на один
    инструмент не создают несколько плагинов.

    Слот выбирается по факту, а не по номеру: сначала ищется уже стоящий
    эквалайзер (по параметрам), затем первый свободный. Занятый чужим плагином
    слот не трогается никогда.
    """
    if fl is None:
        import fl_lib as fl

    by_stem: dict = {}
    for e in edits:
        stem = e.get("stem")
        if stem is None or stem not in track_map:
            continue
        by_stem.setdefault(stem, {}).setdefault(e["region"], []).append(e["do_db"])

    results = {}
    ok_all = True
    for stem, regions in by_stem.items():
        track = int(track_map[stem])
        gains = {r: float(sum(v) / len(v)) for r, v in regions.items()}

        target, found_name = eq_slot(fl, track)
        inserted = False
        if target is None and slot is not None:
            params = fl.act.params(track, slot)
            if params and not is_eq(params):
                results[stem] = {"ok": False,
                                 "error": "слот %d занят не эквалайзером" % slot}
                ok_all = False
                continue
            target = slot
        if target is None:
            target = free_slot(fl, track)
        if target is None:
            results[stem] = {"ok": False,
                             "error": "все %d слотов заняты" % SLOTS_PER_TRACK}
            ok_all = False
            continue
        if not fl.act.params(track, target) and ensure_eq:
            ins = fl.create.live_insert(track, target, "Fruity Parametric EQ 2")
            inserted = bool(ins.get("ok"))
        if not fl.act.params(track, target):
            results[stem] = {"ok": False,
                             "error": "слот %d пуст, вставить не удалось" % target}
            ok_all = False
            continue
        res = apply_eq(fl, track, target, gains)
        results[stem] = {"track": track, "slot": target, "inserted": inserted,
                         "plugin": res.get("plugin") or found_name,
                         "ok": bool(res.get("ok")), "applied": res.get("applied"),
                         "error": res.get("error")}
        ok_all = ok_all and bool(res.get("ok"))

    if save:
        try:
            import flsave
            flsave.save(make_backup=False)
        except Exception:
            pass
    return {"ok": ok_all, "tracks": results, "applied_count": len(results)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Применить правки к живому EQ")
    ap.add_argument("--track", type=int, required=True)
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--gain", action="append", default=[], help="полоса=дБ, напр. mid=3")
    a = ap.parse_args(argv)
    gains = {}
    for item in a.gain:
        region, _, db = item.partition("=")
        gains[region] = float(db)

    import fl_lib as fl
    res = apply_eq(fl, a.track, a.slot, gains)
    for x in res.get("applied", []):
        print("полоса %d: %s по частоте, %s по усилению (хочу %s Гц / %+.1f дБ)" % (
            x["band"], x["freq_text"], x["gain_text"], x["want_hz"], x["want_db"]))
    print("итог:", "ок" if res.get("ok") else res.get("error", "не всё встало"))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
