"""judge.py -- принять или отклонить правку по числам, а не по ощущению.

На вход два замера (до и после), цель и (необязательно) коридор референсов.
На выход вердикт с причинами:

    accept      — изменение значимо и ведёт к цели, побочного ущерба нет
    reject      — уводит от цели или ломает другую метрику
    insufficient— изменение меньше шума замера, судить нельзя

Правила:
  * значимость: |Δ| должно превышать шум метрики (по умолчанию свой для каждой);
  * цель: направление движения (down / up) или целевое число;
  * ущерб: если метрика вышла из коридора — вето, даже при «улучшении» цели.

Журнал решений: _cache/journal.jsonl (что сделали, что ожидали, что вышло).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "_cache", "journal.jsonl")

# шум замера: меньше этого считать изменением нельзя
NOISE = {
    "sharp_mean": 0.01,
    "sharp_p95": 0.02,
    "N_mean": 0.5,
    "phon_mean": 0.5,
    "lufs": 0.1,
    "crest_db": 0.2,
    "ldr_db": 0.5,
    "stereo.corr": 0.005,
    "stereo.side_mid_db": 0.2,
    "octave_4k_db": 0.2,
}
DEFAULT_NOISE = 0.3


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = prefix + k
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = float(v)
    return out


def judge(before: dict, after: dict, goals: dict | None = None,
          corridor: dict | None = None, tolerance: float = 0.0) -> dict:
    """Вердикт по правке.

    goals: {"sharp_mean": "down", "regions_pct.air": "up", "lufs": -12.5}
    corridor: вывод listen_layer.corridor() — проверка побочного ущерба.
    tolerance: насколько допустимо выйти из коридора (0 — нельзя).
    """
    a, b = flatten(before), flatten(after)
    goals = goals or {}
    reasons: list[str] = []
    flags: list[str] = []

    moved, too_small, wrong_way = [], [], []
    for key, target in goals.items():
        if key not in a or key not in b:
            flags.append("нет метрики %s" % key)
            continue
        d = b[key] - a[key]
        noise = NOISE.get(key.split(".")[-1], DEFAULT_NOISE)
        if abs(d) <= noise:
            too_small.append("%s: Δ%+.3f (шум %.3f)" % (key, d, noise))
            continue
        if target == "down":
            (moved if d < 0 else wrong_way).append("%s: Δ%+.3f" % (key, d))
        elif target == "up":
            (moved if d > 0 else wrong_way).append("%s: Δ%+.3f" % (key, d))
        elif isinstance(target, (int, float)):
            close = abs(b[key] - target) < abs(a[key] - target)
            (moved if close else wrong_way).append(
                "%s: %.3f → %.3f (цель %.3f)" % (key, a[key], b[key], target))
        else:
            flags.append("непонятная цель для %s: %r" % (key, target))

    damage = []
    if corridor:
        for key, c in corridor.items():
            if key not in b or key not in a:
                continue
            lo, hi = c["коридор"]

            def dist(v):
                return max(0.0, lo - v, v - hi)

            # ущерб — только если стало ДАЛЬШЕ от коридора, чем было
            if dist(b[key]) > dist(a[key]) + max(tolerance, 1e-9):
                damage.append("%s: %.3f → %.3f, дальше от коридора %.3f..%.3f"
                              % (key, a[key], b[key], lo, hi))

    if damage:
        verdict = "reject"
        reasons.append("побочный ущерб: " + "; ".join(damage))
    elif wrong_way:
        verdict = "reject"
        reasons.append("движение против цели: " + "; ".join(wrong_way))
    elif moved:
        verdict = "accept"
        reasons.append("цель достигнута: " + "; ".join(moved))
    else:
        verdict = "insufficient"
        reasons.append("изменения в пределах шума: " + ("; ".join(too_small) or "нет данных"))

    out = {"verdict": verdict, "reasons": reasons, "flags": flags,
           "moved": moved, "wrong_way": wrong_way, "too_small": too_small,
           "damage": damage}
    return out


def log(entry: dict) -> None:
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    entry = dict(entry)
    entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="json замера до")
    ap.add_argument("--after", required=True, help="json замера после")
    ap.add_argument("--goals", default="", help="sharp_mean:down,regions_pct.air:up")
    ap.add_argument("--corridor", help="json вывода corridor()")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    with open(a.before, encoding="utf-8") as f:
        before = json.load(f)
    with open(a.after, encoding="utf-8") as f:
        after = json.load(f)
    corridor = None
    if a.corridor:
        with open(a.corridor, encoding="utf-8") as f:
            corridor = json.load(f)

    goals = {}
    for item in filter(None, a.goals.split(",")):
        k, _, v = item.partition(":")
        goals[k.strip()] = v.strip() if v.strip() in ("down", "up") else float(v)

    r = judge(before, after, goals, corridor)
    print("вердикт:", r["verdict"])
    for line in r["reasons"]:
        print("  " + line)
    if r["flags"]:
        print("  замечания:", "; ".join(r["flags"]))
    log({"note": a.note, "goals": goals, "verdict": r["verdict"], "reasons": r["reasons"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
