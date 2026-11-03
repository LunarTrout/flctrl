"""Проверки детектора провалов и толщины стены.

Смысл: провал — выпадение полосы относительно самого трека, а не тихий микс.
Проверяем на сигналах с известным ответом:
1. ровный шум — провалов нет, стена толстая;
2. вырезанная полоса в одной секции — найдена именно там и именно та;
3. провал в одной полосе не выдумывает провалы в соседних (кроме краёв);
4. тремоло-микс рыхлее ровного (толщина падает);
5. сравнение с референсами даёт вердикт.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import wall  # noqa: E402

FS = 24000
SEC = 8.0


def noise(path: str, notch_section: int | None = None, tremolo: bool = False) -> str:
    rng = np.random.default_rng(7)
    n = int(3 * SEC * FS)
    x = rng.standard_normal(n) * 0.08
    if notch_section is not None and notch_section >= 0:
        sl = slice(int(notch_section * SEC * FS), int((notch_section + 1) * SEC * FS))
        # вырезаем ровно октаву вокруг 1 кГц — это и есть «дыра» в середине
        sos = butter(6, [700, 1400], btype="bandstop", fs=FS, output="sos")
        x[sl] = sosfilt(sos, x[sl])
    if tremolo:
        t = np.arange(n) / FS
        x = x * (0.25 + 0.75 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t)))
    sf.write(path, np.stack([x, x], axis=1), FS)
    return path


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flwall_")
    flat = noise(os.path.join(tmp, "flat.wav"))
    holed = noise(os.path.join(tmp, "hole.wav"), notch_section=1)
    loose = noise(os.path.join(tmp, "loose.wav"), tremolo=True)

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    a = wall.analyze(flat, section_s=SEC)
    b = wall.analyze(holed, section_s=SEC)
    c = wall.analyze(loose, section_s=SEC)

    check("ровный микс: провалов нет", a["hole_count"] == 0, str(a["holes"][:2]))
    check("ровный микс: стена толстая", a["thickness"] > 0.9, "%.3f" % a["thickness"])

    check("вырезанная полоса найдена", b["hole_count"] > 0,
          "провалов %d" % b["hole_count"])
    hit = [h for h in b["holes"] if h["band"] == 1000]
    check("найдена именно 1000 Гц", bool(hit), str(hit[:2]))
    check("найдена в нужной секции", bool(hit) and 6.0 <= hit[0]["t"] <= 10.0,
          "t=%s" % (hit[0]["t"] if hit else None))
    bands_hit = {h["band"] for h in b["holes"]}
    check("соседние полосы не обвинены зря",
          bands_hit <= {500, 1000, 2000}, str(sorted(bands_hit)))

    check("тремоло-микс рыхлее ровного", c["thickness"] < a["thickness"] - 0.1,
          "%.3f против %.3f" % (c["thickness"], a["thickness"]))

    comp = wall.compare(b, [a, c])
    check("вердикт по референсам есть", comp["thickness"]["verdict"] in
          ("плотнее жанра", "рыхлее жанра"), comp["thickness"]["verdict"])
    check("провалы сравниваются с референсами", "verdict" in comp["holes"],
          comp["holes"]["verdict"])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
