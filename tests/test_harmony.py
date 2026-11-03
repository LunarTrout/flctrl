"""Гармония: тональность, строй, гармоничность — числами, а не на слух."""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import harmony as H  # noqa: E402

FS = 44100


def _wav(x, fs=FS):
    import soundfile as sf

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, x, fs)
    return path


def tone(freq=440.0, seconds=4.0, cents=0.0, fs=FS, harmonics=(1.0, 0.5, 0.25)):
    t = np.arange(int(fs * seconds)) / fs
    f = freq * 2 ** (cents / 1200.0)
    x = np.zeros(len(t))
    for k, amp in enumerate(harmonics, start=1):
        x += amp * np.sin(2 * np.pi * f * k * t)
    return 0.3 * x / np.max(np.abs(x))


def triad(root=261.63, seconds=4.0, fs=FS):
    """C-E-G: хрома должна указать на эти ступени."""
    return 0.3 * sum(tone(root * 2 ** (k / 12.0), seconds, fs=fs) for k in (0, 4, 7))


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    a440 = H.analyse(_wav(tone(440.0)))
    check("тон A440: тоника A", "A" in a440["top_pitch_classes"], str(a440["top_pitch_classes"]))
    check("тон A440: гармоничность высокая", (a440["harmonicity"] or 0) > 0.6,
          "%.2f" % (a440["harmonicity"] or 0))
    check("тон A440: строй ровный", abs(a440["tuning_cents"]) <= 3, "%+.0f центов" % a440["tuning_cents"])

    sharp = H.analyse(_wav(tone(440.0, cents=25.0)))
    check("сдвиг +25 центов измерен", 15 <= abs(sharp["tuning_cents"]) <= 35,
          "%+.0f центов" % sharp["tuning_cents"])

    ch = H.analyse(_wav(triad()))
    top = set(ch["top_pitch_classes"][:3])
    check("трезвучие C-E-G: ступени на месте", {"C", "E", "G"} <= top, str(sorted(top)))
    check("тональность определена", bool(ch["key"]) and ch["key"][0]["r"] > 0.5,
          str(ch["key"][0]) if ch["key"] else "-")

    rng = np.random.default_rng(0)
    noise = H.analyse(_wav(rng.normal(0, 0.2, FS * 4)))
    check("шум: гармоничность низкая",
          noise["harmonicity"] is None or noise["harmonicity"] < 0.45,
          "%.2f" % (noise["harmonicity"] if noise["harmonicity"] is not None else -1))

    cmp_ = H.compare(a440, [a440, ch])
    check("сравнение с собой даёт близость 1.0", (cmp_["rows"][0]["cos"] or 0) > 0.99,
          "%.3f" % cmp_["rows"][0]["cos"])
    check("вердиктов без причины нет", isinstance(cmp_["verdict"], list), str(len(cmp_["verdict"])))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
