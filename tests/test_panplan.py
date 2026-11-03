"""Проверки плана панорамы на синтетике.

План — это не «красиво придумали», а следствие измерений. Поэтому проверяем
свойства, которые обязаны держаться на любом материале:
1. низ не двигается (уходит фаза и разваливается низ);
2. широкий/плотный материал отходит, разрежённый — подбирается;
3. шаг между точками ограничен, позиции в пределах;
4. точки идут по времени;
5. проверка плана ловит намеренно испорченный план.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import panplan  # noqa: E402

FS = 24000


def guitar(path: str, wide_sections=(0, 2)) -> str:
    """Гитара слева: средняя секция уже, крайние шире."""
    sec = 8.0
    n = int(3 * sec * FS)
    t = np.arange(n) / FS
    rng = np.random.default_rng(5)
    body = 0.2 * np.sin(2 * np.pi * 220 * t) + 0.05 * rng.standard_normal(n)
    left = body.copy()
    right = np.zeros(n)
    for i in range(3):
        sl = slice(int(i * sec * FS), int((i + 1) * sec * FS))
        if i in wide_sections:
            right[sl] = 0.5 * body[sl] + 0.35 * rng.standard_normal(sl.stop - sl.start)
        else:
            right[sl] = 0.15 * body[sl] + 0.05 * rng.standard_normal(sl.stop - sl.start)
    sf.write(path, np.stack([left, right], axis=1), FS)
    return path


def bass(path: str) -> str:
    n = int(24 * FS)
    t = np.arange(n) / FS
    sos = butter(4, 120, btype="lowpass", fs=FS, output="sos")
    x = sosfilt(sos, np.sin(2 * np.pi * 55 * t))
    sf.write(path, np.stack([x, x], axis=1), FS)
    return path


def pad(path: str) -> str:
    """Ровная стерео-подложка: ширина по секциям не меняется вообще."""
    rng = np.random.default_rng(11)
    n = int(24 * FS)
    left = 0.05 * rng.standard_normal(n)
    right = 0.05 * rng.standard_normal(n)
    sf.write(path, np.stack([left, right], axis=1), FS)
    return path


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flpan_")
    g = guitar(os.path.join(tmp, "gtr.wav"))
    b = bass(os.path.join(tmp, "bass.wav"))
    pd = pad(os.path.join(tmp, "pad.wav"))
    stems = {"gtr": g, "bass": b, "pad": pd}

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    p = panplan.plan(stems, section_s=8.0, max_step=0.15)
    res = panplan.check(p)

    check("план прошёл проверки", res["ok"], str(res["problems"])[:80])
    check("низ статичен", p["stems"]["bass"]["static"] is True,
          "ход %s" % p["stems"]["bass"].get("moved"))
    check("ровная подложка дышит вместе с треком",
          p["stems"]["pad"]["moved"] > 0.02,
          "ход %s" % p["stems"]["pad"].get("moved"))
    check("гитара двигается", p["stems"]["gtr"]["moved"] > 0.02,
          "ход %.3f" % p["stems"]["gtr"].get("moved"))
    pts = p["stems"]["gtr"]["points"]
    check("точек по числу секций", len(pts) == 3, str(len(pts)))
    check("точки по времени",
          [q["t"] for q in pts] == sorted(q["t"] for q in pts), "")
    check("шаг не превышен",
          all(abs(q["pan"] - r["pan"]) <= 0.15 + 1e-9 for r, q in zip(pts, pts[1:])), "")
    check("позиции в пределах", all(abs(q["pan"]) <= p["max_pan"] + 1e-9 for q in pts), "")

    # направление: узкая секция должна стоять ближе к центру, чем широкие
    narrow, wide0 = abs(pts[1]["pan"]), abs(pts[0]["pan"])
    check("узкая секция ближе к центру, чем широкая", narrow < wide0 - 0.05,
          "узкая %.2f против широкой %.2f | %s" % (
              narrow, wide0, " ".join("%.1f:%.2f" % (q["t"], q["pan"]) for q in pts)))

    # намеренно испорченный план должен быть пойман
    bad = {"stems": {"x": {"points": [{"t": 8, "pan": 0.0}, {"t": 0, "pan": 0.5},
                                     {"t": 16, "pan": -0.9}], "static": False}},
           "max_pan": 0.7, "max_step": 0.15}
    res_bad = panplan.check(bad)
    check("проверка ловит скачок и порядок", not res_bad["ok"] and len(res_bad["problems"]) >= 2,
          str(res_bad["problems"])[:70])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
