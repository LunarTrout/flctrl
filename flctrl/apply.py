"""apply.py — применить план к живому FL через эквалайзер, без перезагрузки проекта.

План говорит «этому инструменту в этой полосе +3 дБ». Чтобы это стало звуком,
нужен эквалайзер в слоте и точная установка полосы и усиления. Шкалы плагинов
не документированы и у каждого свои, поэтому здесь они не угадываются, а
измеряются: ставим значение, читаем, что плагин показывает, и подбираем.

Так же поступает живой звукорежиссёр: крутит, смотрит на подпись, крутит ещё.

    python -m flctrl.apply --track 1 --slot 0 --gain mid=3 --gain pres=-2
"""
from __future__ import annotations

import argparse
import re

REGION_HZ = {"sub": 45.0, "bass": 90.0, "lowmid": 200.0, "mid": 700.0,
             "upmid": 2000.0, "pres": 4500.0, "air": 9000.0}


def parse_hz(text: str) -> float | None:
    t = text.strip().lower().replace(" ", "")
    m = re.match(r"^(-?\d+(?:[.,]\d+)?)(k?)(hz)?$", t)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    if m.group(2) == "k":
        v *= 1000.0
    return v


def parse_db(text: str) -> float | None:
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*db", text.strip().lower())
    return float(m.group(1).replace(",", ".")) if m else None


def _find(params: list[dict], name: str) -> dict | None:
    for p in params:
        if p["name"].strip().lower() == name.lower():
            return p
    return None


def set_param_until(get_params, set_param, index: int, target: float,
                    parse, tolerance: float, lo: float = 0.0, hi: float = 1.0,
                    iters: int = 12) -> dict:
    """Подобрать нормализованное значение так, чтобы подпись плагина дала цель.

    Монотонность предполагается: частота и усиление растут с параметром.
    Если подпись не читается — возвращаем то, что есть, и честно помечаем.
    """
    best = None
    for _ in range(iters):
        mid = (lo + hi) / 2
        set_param(index, mid)
        params = get_params()
        p = next((x for x in params if x["i"] == index), None)
        value = parse(p["text"]) if p else None
        if value is None:
            return {"index": index, "value": mid, "text": None, "ok": False}
        best = {"index": index, "value": round(mid, 4), "text": p["text"],
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


def apply_eq(fl, track: int, slot: int, gains: dict, verify: bool = True) -> dict:
    """Выставить полосы эквалайзера по плану: {region: db}.

    `fl` — клиент библиотеки (или None: тогда берётся fl_lib сам).
    """
    if fl is None:
        import fl_lib as fl
    params = fl.act.params(track, slot)
    if not params:
        return {"ok": False, "error": "в слоте нет плагина или нет параметров"}

    def get_params():
        return fl.act.params(track, slot)

    def set_param(index: int, value: float):
        fl.act.param(track, slot, index, value)

    names = {p["name"] for p in params}
    if not any(n.lower().startswith("band 1 level") for n in names):
        return {"ok": False, "error": "плагин не похож на параметрический эквалайзер",
                "names": sorted(names)[:8]}

    bands = []
    for i in range(1, 9):
        lvl = _find(params, "Band %d level" % i)
        frq = _find(params, "Band %d freq" % i)
        if lvl and frq:
            bands.append((i, lvl["i"], frq["i"]))

    applied = []
    for region, db in gains.items():
        hz = REGION_HZ.get(region)
        if hz is None or abs(db) < 0.2:
            continue
        need = len(applied)
        if need >= len(bands):
            break
        num, lvl_i, frq_i = bands[need]
        f = set_param_until(get_params, set_param, frq_i, hz, parse_hz, tolerance=hz * 0.15)
        g = set_param_until(get_params, set_param, lvl_i, db, parse_db, tolerance=0.4)
        applied.append({"region": region, "band": num, "want_hz": hz, "want_db": db,
                        "freq_text": f.get("text"), "gain_text": g.get("text"),
                        "freq_ok": bool(f.get("ok")), "gain_ok": bool(g.get("ok"))})

    ok = all(a["freq_ok"] and a["gain_ok"] for a in applied) if applied else False
    check = None
    if verify and applied:
        check = [{"band": a["band"], "text": _param_text(get_params(), a["band"], True)}
                 for a in applied]
    return {"ok": ok, "applied": applied, "verify": check}


def _param_text(params: list[dict], band: int, level: bool = True) -> str | None:
    name = ("Band %d level" if level else "Band %d freq") % band
    p = _find(params, name)
    return p["text"] if p else None


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
