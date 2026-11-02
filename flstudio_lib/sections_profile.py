"""sections_profile.py -- профиль всего трека по секциям, без выбора одного окна.

Что делает:
  1. считает покадровую громкость N по всему файлу;
  2. находит границы секций по смене энергии и тембра (perceptual.auto_sections);
  3. замеряет каждую секцию полным профилем;
  4. печатает таблицу: строка = секция, плюс итоговая строка по треку.

  ../.venv313/Scripts/python.exe sections_profile.py mix.wav [ref.mp3 ...]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402
import perceptual as P    # noqa: E402
import numpy as np        # noqa: E402

COLS = [
    ("sharp", "резк", 2),
    ("N", "соны", 0),
    ("lufs", "LUFS", 1),
    ("crest", "crest", 1),
    ("ldr", "LDR", 1),
    ("corr", "corr", 2),
    ("sm", "s/m", 1),
    ("mid", "mid%", 0),
    ("upm", "upm%", 0),
    ("pres", "pres%", 0),
    ("air", "air%", 0),
]


def row_of(m: dict) -> list:
    st = m.get("stereo") or {}
    rp = m.get("regions_pct") or {}
    return [m["sharp_mean"], m["N_mean"], m["lufs"], m["crest_db"], m["ldr_db"],
            st.get("corr", float("nan")), st.get("side_mid_db", float("nan")),
            rp.get("mid", float("nan")), rp.get("upmid", float("nan")),
            rp.get("pres", float("nan")), rp.get("air", float("nan"))]


def profile_sections(path: str, min_len: float = 8.0) -> list:
    x, fs = L.load_section(path, None)
    lv, t = P.band_levels(x, fs)
    lv = P.calibrate(lv)
    N, _ph, _sp, _sh = P.zwicker_series(lv)
    secs = P.auto_sections(lv, t, min_len=min_len, n=6)
    out = []
    for (a, b) in secs:
        if b - a < 1.0:
            continue
        m = L.measure(path, section=(a, b))
        out.append((a, b, m))
    return out


def print_table(name: str, rows: list) -> None:
    head = "%-24s" % name + "".join("%8s" % c[1] for c in COLS)
    print(head)
    print("-" * len(head))
    for (a, b, m) in rows:
        line = "%6.0f–%-6.0f %-11s" % (a, b, "")
        for (key, _lbl, nd), v in zip(COLS, row_of(m)):
            line += "%8s" % ("%.*f" % (nd, v) if v == v else "—")
        print(line)


def main() -> int:
    files = sys.argv[1:]
    if not files:
        print("укажи файл(ы)")
        return 2
    for f in files:
        rows = profile_sections(f)
        print()
        print_table(os.path.basename(f)[:22], rows)
        tot = L.measure(f, section=None)
        line = "%-24s" % "ВЕСЬ ТРЕК"
        for (key, _lbl, nd), v in zip(COLS, row_of(tot)):
            line += "%8s" % ("%.*f" % (nd, v) if v == v else "—")
        print("-" * len(line))
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
