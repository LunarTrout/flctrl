"""balance.py — жанровая модель баланса и подбор правок по слышимости.

Что хранится: распределение (медиана + IQR) по ролям — слышимость A и доля
частичной громкости. Ничего про конкретный трек.

Как применяется: проект приводится к ролям, считается его вектор слышимости,
сравнивается с моделью, и на выход идут правки в дБ с капом. Каждая правка
несёт предсказание, которое потом проверяется замером.

Почему слышимость, а не громкость: в плотном миксе роль маскируется, и её
фейдер перестаёт управлять тем, что слышно. A = N_partial/N_solo это учитывает.

    python -m flctrl.balance build --refs DIR --out bal.json
    python -m flctrl.balance check --model bal.json --track DIR
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from typing import Any

import numpy as np

ROLES = ("drums", "bass", "guitar", "keys", "vocals", "other")


# ------------------------------------------------------------------ корпус
def _sum_to_file(paths: list[str], out: str) -> str:
    import soundfile as sf

    acc = None
    fs = None
    for p in paths:
        x, sr = sf.read(p, dtype="float64", always_2d=True)
        if acc is None:
            acc, fs = x, sr
        else:
            n = min(len(acc), len(x))
            acc = acc[:n] + x[:n]
            fs = sr
    peak = float(np.max(np.abs(acc))) if acc is not None else 0.0
    if peak > 0.999 and peak > 0:
        acc = acc / peak * 0.999
    sf.write(out, acc, fs)
    return out


def project(mapping: dict[str, Any], mix_path: str | None = None,
            a: float | None = None, b: float | None = None,
            max_seconds: float | None = 30.0) -> dict:
    """Вектор слышимости проекта: роль -> A, доля, N_partial, dN/dB.

    `max_seconds`: по умолчанию берём первые 30 секунд окна — модель
    Мура-Гласберга дорогая, а для оценки баланса этого достаточно. Полная
    длина нужна только для отчётности, не для решения.
    """
    import partial

    tmp = tempfile.mkdtemp(prefix="flbalance_")
    paths: dict[str, str] = {}
    for role, value in mapping.items():
        if isinstance(value, (list, tuple)):
            paths[role] = _sum_to_file(list(value), os.path.join(tmp, role + ".wav"))
        else:
            paths[role] = value

    if max_seconds and b is not None and a is not None:
        b = min(b, a + max_seconds)
    elif max_seconds:
        b = max_seconds
        a = 0.0

    if mix_path is None:
        mix_path = os.path.join(tmp, "_mix.wav")
        _sum_to_file(list(paths.values()), mix_path)

    res = partial.attribute(mix_path, paths, a, b)
    out = {"mix": mix_path, "roles": {}}
    for role, v in res["stems"].items():
        out["roles"][role] = {
            "audib": v["audib"],
            "share_pct": v["share_pct"],
            "N_partial": v["N_partial"],
            "N_solo": v["N_solo"],
            "dN_per_db": v["dN_per_db"],
        }
    return out


def build(refs_dir: str, out: str | None = None, max_tracks: int | None = None) -> dict:
    """Собрать модель по папкам <track>/{drums,bass,other,vocals}.wav.

    Храним НЕ только медианы: каждая запись корпуса остаётся отдельной строкой.
    Так цель для нового проекта можно считать как взвешенную смесь ближайших
    референсов, а не как один усреднённый профиль.
    """
    import targets as T

    tracks = list(T._iter_tracks(refs_dir))
    if max_tracks:
        tracks = tracks[:max_tracks]
    per_role: dict[str, list[dict]] = {}
    rows: list[dict] = []
    used = []
    for name, files in tracks:
        try:
            vec = project(dict(files))
        except Exception as exc:
            print("пропуск %s: %s" % (name, str(exc)[:100]))
            continue
        used.append(name)
        rows.append({"track": name, "roles": vec["roles"]})
        for role, v in vec["roles"].items():
            per_role.setdefault(role, []).append(v)

    model: dict[str, Any] = {"tracks": used, "roles": {}, "rows": rows}
    for role, items in per_role.items():
        aud = [it["audib"] for it in items]
        shr = [it["share_pct"] for it in items]
        sens = [it["dN_per_db"] for it in items if it["dN_per_db"] > 0]
        model["roles"][role] = {
            "n": len(items),
            "audib": {"median": float(np.median(aud)),
                      "q25": float(np.percentile(aud, 25)),
                      "q75": float(np.percentile(aud, 75))},
            "share_pct": {"median": float(np.median(shr)),
                          "q25": float(np.percentile(shr, 25)),
                          "q75": float(np.percentile(shr, 75))},
            "dN_per_db": {"median": float(np.median(sens)) if sens else float("nan")},
        }
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=1)
    return model


# ------------------------------------------------------------------ решение
def _target_audib(model: dict, role: str) -> float | None:
    e = (model.get("roles") or {}).get(role)
    if not e:
        return None
    return float(e["audib"]["median"])


def _target_share(model: dict, role: str) -> float | None:
    e = (model.get("roles") or {}).get(role)
    if not e:
        return None
    return float(e["share_pct"]["median"])


def targets_for(vec: dict, model: dict, k: int = 3, temp: float = 0.12) -> dict:
    """Цель как смесь ближайших референсов, а не медиана по всем.

    Смысл: у корпуса разные школы сведения. Если наш материал по балансу ближе
    к двум-трём из них, среднее по всем шести размазывает цель и просит правки,
    которых не хочет ни один живой микс. Поэтому берём k ближайших и смешиваем
    их с весами по расстоянию (softmax), а имя каждого источника возвращаем —
    чтобы было видно, НА КОГО мы равняемся.
    """
    rows = model.get("rows") or []
    roles = model.get("roles") or {}
    if not rows:
        return {"roles": {r: {"audib": e["audib"]["median"],
                              "share_pct": e["share_pct"]["median"]}
                          for r, e in roles.items()},
                "from": [], "mode": "median"}

    def dist(row: dict) -> float:
        common = [r for r in vec.get("roles", {}) if r in (row.get("roles") or {})]
        if not common:
            return 9.9
        d = [abs(vec["roles"][r]["audib"] - row["roles"][r]["audib"]) for r in common]
        s = [abs(vec["roles"][r]["share_pct"] - row["roles"][r]["share_pct"]) / 100.0
             for r in common]
        return float(np.mean(d) + 0.5 * np.mean(s))

    scored = sorted(((dist(r), r) for r in rows), key=lambda x: x[0])[:max(1, k)]
    dmin = scored[0][0]
    weights = [float(np.exp(-(d - dmin) / max(temp, 1e-3))) for d, _ in scored]
    total = sum(weights) or 1.0
    weights = [w / total for w in weights]

    out_roles: dict[str, dict] = {}
    for role in roles:
        vals, wsum = [], 0.0
        for (d, row), w in zip(scored, weights):
            rv = (row.get("roles") or {}).get(role)
            if not rv:
                continue
            vals.append((rv["audib"], rv["share_pct"], w))
            wsum += w
        if not vals or wsum <= 0:
            continue
        out_roles[role] = {
            "audib": sum(v[0] * v[2] for v in vals) / wsum,
            "share_pct": sum(v[1] * v[2] for v in vals) / wsum,
        }
    return {"roles": out_roles,
            "from": [{"track": row["track"], "weight": round(w, 3),
                      "distance": round(d, 3)}
                     for (d, row), w in zip(scored, weights)],
            "mode": "ensemble"}


def solve(vec: dict, model: dict, cap_db: float = 6.0,
          deadband: float = 0.05, k: int = 3) -> dict:
    """Каким ролям и на сколько дБ двинуть фейдер.

    Цели берутся из ансамбля ближайших референсов (`targets_for`). Дефицит
    слышимости переводится в дБ через ИЗМЕРЕННУЮ чувствительность dN/dB этого
    проекта (не среднюю по корпусу). Доля частичной громкости — второй признак:
    если она сильно ниже цели при нормальной A, роль маскируется, и одним
    фейдером это не лечится.
    """
    tgt = targets_for(vec, model, k=k)
    moves = []
    for role, mine in (vec.get("roles") or {}).items():
        t = tgt["roles"].get(role)
        if not t:
            continue
        tgt_a, tgt_s = t["audib"], t["share_pct"]
        d_a = tgt_a - mine["audib"]
        sens = mine.get("dN_per_db") or 0.0
        pred_db = None
        if sens > 1e-6 and mine["N_partial"] > 1e-6:
            need_n = (tgt_a * mine["N_solo"]) - mine["N_partial"]
            pred_db = need_n / sens
        if abs(d_a) < deadband and abs(tgt_s - mine["share_pct"]) < 2.0:
            continue
        db = float(np.clip(pred_db, -cap_db, cap_db)) if pred_db is not None else 0.0
        masked = (tgt_s - mine["share_pct"]) > 4.0 and abs(d_a) < 0.1
        moves.append({
            "role": role,
            "db": round(db, 1),
            "audib": round(mine["audib"], 3),
            "target_audib": round(tgt_a, 3),
            "share_pct": round(mine["share_pct"], 1),
            "target_share": round(tgt_s, 1),
            "why": ("маскируется: слышимость в норме, а доля низкая — нужен EQ/панорама"
                    if masked else "дефицит слышимости"),
        })
    moves.sort(key=lambda m: abs(m["db"]), reverse=True)
    return {"moves": moves, "cap_db": cap_db, "from": tgt["from"],
            "mode": tgt["mode"]}


def check(before: dict, after: dict, model: dict) -> dict:
    """Приёмка: стало ли ближе к модели по слышимости и доле."""
    tgt = targets_for(before, model)
    rows = []
    for role, b in (before.get("roles") or {}).items():
        a = (after.get("roles") or {}).get(role)
        t = tgt["roles"].get(role)
        if not a or not t:
            continue
        tgt_a, tgt_s = t["audib"], t["share_pct"]
        rows.append({
            "role": role,
            "d_audib": round(a["audib"] - b["audib"], 3),
            "err_before": round(abs(b["audib"] - tgt_a), 3),
            "err_after": round(abs(a["audib"] - tgt_a), 3),
            "err_share_before": round(abs(b["share_pct"] - tgt_s), 1),
            "err_share_after": round(abs(a["share_pct"] - tgt_s), 1),
        })
    better = [r for r in rows if r["err_after"] is not None
              and r["err_before"] is not None and r["err_after"] < r["err_before"]]
    worse = [r for r in rows if r["err_after"] is not None
             and r["err_before"] is not None and r["err_after"] > r["err_before"]]
    verdict = "accept" if len(better) > len(worse) else ("reject" if worse else "insufficient")
    return {"verdict": verdict, "rows": rows}


# ------------------------------------------------------------------ CLI
def _mapping_from_dir(directory: str, module_names: list[str] | None = None) -> dict:
    import glob
    import roles as R

    files = sorted(glob.glob(os.path.join(directory, "*.wav")))
    mapping: dict[str, list[str]] = {}
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        role = R.role_of(name, path)
        mapping.setdefault(role, []).append(path)
    return mapping


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Жанровая модель баланса")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="собрать модель по корпусу")
    b.add_argument("--refs", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--max", type=int, default=None)

    c = sub.add_parser("check", help="сравнить папку стемов с моделью")
    c.add_argument("--model", required=True)
    c.add_argument("--dir", required=True)
    c.add_argument("--a", type=float, default=None)
    c.add_argument("--b", type=float, default=None)

    a = ap.parse_args(argv)
    if a.cmd == "build":
        m = build(a.refs, a.out, a.max)
        for role, e in m["roles"].items():
            print("%-7s n=%d A %.3f (%.3f…%.3f) доля %.1f%%" % (
                role, e["n"], e["audib"]["median"], e["audib"]["q25"],
                e["audib"]["q75"], e["share_pct"]["median"]))
        print("записано:", a.out)
        return 0

    with open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    vec = project(_mapping_from_dir(a.dir), a=a.a, b=a.b)
    print("%-7s %7s %7s %9s" % ("роль", "A", "цель", "доля"))
    for role, v in vec["roles"].items():
        print("%-7s %7.3f %7.3f %8.1f%%" % (
            role, v["audib"], _target_audib(model, role) or 0, v["share_pct"]))
    res = solve(vec, model)
    print("\nправки:")
    for m in res["moves"]:
        print("  %-7s %+5.1f дБ  (%s)" % (m["role"], m["db"], m["why"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
