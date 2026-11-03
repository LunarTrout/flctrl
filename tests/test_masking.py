"""Матрица маскировки: кто кого забивает и в каких полосах.

Проверяем на синтетике: низкий тон должен забивать высокий внизу и не забивать
вверху; обратное — наоборот. Иначе метрика бесполезна.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402

FS = 48000


def noise(name: str, low_hz: float | None = None, high_hz: float | None = None,
          seconds: float = 6.0, amp: float = 0.3) -> str:
    """Шум, обрезанный по краям: у него есть энергия во всей своей полосе."""
    rng = np.random.default_rng(7)
    n = int(FS * seconds)
    spec = np.fft.rfft(rng.normal(0, 1, n))
    f = np.fft.rfftfreq(n, 1 / FS)
    if low_hz:
        spec[f < low_hz] = 0
    if high_hz:
        spec[f > high_hz] = 0
    x = np.fft.irfft(spec, n)
    x = amp * x / (np.max(np.abs(x)) + 1e-9)
    path = os.path.join(tempfile.gettempdir(), "mask_%s.wav" % name)
    sf.write(path, x.astype("float32"), FS)
    return path


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    # тихий широкополосный + громкий низ: внизу второй должен забивать первого,
    # вверху второго нет, поэтому маски там нет по определению
    full = noise("full", amp=0.01)
    low = noise("low", high_hz=300.0, amp=0.3)
    f = api.measure({"metric": "masking", "stems": {"low": low, "full": full}, "a": 0, "b": 5})
    pairs = f["axes"]["pair"]
    bands = f["axes"]["band"]
    check("обе пары на месте", set(pairs) == {"low>full", "full>low"}, str(pairs))
    check("полосы подписаны", len(bands) == len(f["values"][0]), str(len(bands)))

    lo_masks = f["values"][pairs.index("low>full")]
    hi_masks = f["values"][pairs.index("full>low")]
    check("громкий низ забивает тихий микс", max(lo_masks[:6]) > 10, str(lo_masks[:6]))
    check("тихий не забивает громкий", max(hi_masks) <= 0.1, str(max(hi_masks)))
    check("где цели нет — маски нет", max(lo_masks[-6:]) <= 0.1, str(lo_masks[-6:]))

    try:
        api.measure({"metric": "masking", "stems": {"only": low}, "a": 0, "b": 2})
        check("одного источника мало", False, "приняло")
    except api.ApiError as e:
        check("одного источника мало", e.code == "bad_spec", e.code)
    check("разница ограничена 60 дБ", max(max(r) for r in f["values"]) <= 60.0, "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
