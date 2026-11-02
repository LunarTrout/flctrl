"""Проверка модели баланса на синтетическом корпусе (без FL и без аудио).

Что обязано выполняться, иначе решения по ней небезопасны:

1. Цель берётся из ансамбля ближайших референсов, а не из среднего по всем.
2. Веса ансамбля нормированы и отдают имена треков — видно, на кого равняемся.
3. Правка считается в дБ с капом и правильным знаком (дефицит → плюс).
4. Приёмка принимает улучшение и отклоняет ухудшение.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import balance  # noqa: E402


def row(track, drums, bass, gtr, vox):
    def role(a, s):
        return {"audib": a, "share_pct": s, "N_partial": a * 10.0,
                "N_solo": 10.0, "dN_per_db": 1.0}
    return {"track": track,
            "roles": {"drums": role(*drums), "bass": role(*bass),
                      "guitar": role(*gtr), "vocals": role(*vox)}}


MODEL = {
    "tracks": ["A", "B", "C"],
    "roles": {
        "drums": {"n": 3, "audib": {"median": 0.30, "q25": 0.2, "q75": 0.4},
                  "share_pct": {"median": 20.0, "q25": 15, "q75": 25},
                  "dN_per_db": {"median": 1.0}},
        "bass": {"n": 3, "audib": {"median": 0.20, "q25": 0.15, "q75": 0.25},
                 "share_pct": {"median": 15.0, "q25": 10, "q75": 20},
                 "dN_per_db": {"median": 1.0}},
        "guitar": {"n": 3, "audib": {"median": 0.50, "q25": 0.4, "q75": 0.6},
                   "share_pct": {"median": 55.0, "q25": 50, "q75": 60},
                   "dN_per_db": {"median": 1.0}},
        "vocals": {"n": 3, "audib": {"median": 0.10, "q25": 0.05, "q75": 0.15},
                   "share_pct": {"median": 10.0, "q25": 5, "q75": 15},
                   "dN_per_db": {"median": 1.0}},
    },
    "rows": [
        row("A", (0.30, 20), (0.20, 15), (0.50, 55), (0.10, 10)),
        row("B", (0.60, 35), (0.10, 8), (0.70, 50), (0.05, 7)),   # барабанная школа
        row("C", (0.15, 12), (0.35, 25), (0.40, 50), (0.15, 13)),  # басовая школа
    ],
}


def vec(drums, bass, gtr, vox):
    def role(a, s):
        return {"audib": a, "share_pct": s, "N_partial": a * 10.0,
                "N_solo": 10.0, "dN_per_db": 1.0}
    return {"roles": {"drums": role(*drums), "bass": role(*bass),
                      "guitar": role(*gtr), "vocals": role(*vox)}}


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-42s %s" % (name, detail))

    # 1. ближайший референс определяет цель
    near_b = vec((0.55, 33), (0.12, 9), (0.68, 50), (0.06, 7))
    t = balance.targets_for(near_b, MODEL, k=1)
    check("цель = ближайший референс", t["from"][0]["track"] == "B",
          "выбран %s" % (t["from"][0]["track"] if t["from"] else "—"))
    check("вес одного = 1.0", abs(t["from"][0]["weight"] - 1.0) < 1e-6,
          "%.3f" % t["from"][0]["weight"])
    check("цель по барабанам близка к B",
          abs(t["roles"]["drums"]["audib"] - 0.60) < 1e-6,
          "%.2f" % t["roles"]["drums"]["audib"])

    # 2. ансамбль из двух даёт промежуточную цель и нормированные веса
    mixed = vec((0.40, 25), (0.20, 15), (0.50, 52), (0.10, 10))
    t2 = balance.targets_for(mixed, MODEL, k=2)
    wsum = sum(f["weight"] for f in t2["from"])
    check("веса ансамбля нормированы", abs(wsum - 1.0) < 1e-6, "сумма %.3f" % wsum)
    check("ансамбль из двух треков", len(t2["from"]) == 2, str(len(t2["from"])))
    check("цель между крайними", 0.30 <= t2["roles"]["drums"]["audib"] <= 0.60,
          "%.2f" % t2["roles"]["drums"]["audib"])

    # 3. дефицит слышимости → положительная правка, с капом
    weak = vec((0.05, 5), (0.20, 15), (0.50, 55), (0.10, 10))
    res = balance.solve(weak, MODEL, cap_db=6.0)
    drums = next((m for m in res["moves"] if m["role"] == "drums"), None)
    check("дефицит даёт подъём", drums is not None and drums["db"] > 0,
          "db=%s" % (drums["db"] if drums else "нет"))
    check("правка ограничена капом", all(abs(m["db"]) <= 6.0 for m in res["moves"]),
          str([m["db"] for m in res["moves"]]))
    check("в ответе есть источники цели", bool(res["from"]),
          ", ".join(f["track"] for f in res["from"]))

    # 4. приёмка
    before = weak
    after_good = vec((0.28, 19), (0.20, 15), (0.50, 55), (0.10, 10))
    after_bad = vec((0.01, 1), (0.20, 15), (0.50, 55), (0.10, 10))
    v_good = balance.check(before, after_good, MODEL)
    v_bad = balance.check(before, after_bad, MODEL)
    check("улучшение принимается", v_good["verdict"] == "accept", v_good["verdict"])
    check("ухудшение отклоняется", v_bad["verdict"] in ("reject", "insufficient"),
          v_bad["verdict"])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
