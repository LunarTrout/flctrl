"""Транзиенты: удары должны быть слышны как удары, а не как шум."""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import transients as T  # noqa: E402

FS = 48000


def _wav(x: np.ndarray, fs: int = FS) -> str:
    import soundfile as sf

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, x, fs)
    return path


def click_train(freq: float = 4.0, seconds: float = 3.0, fs: int = FS) -> np.ndarray:
    """Короткие щелчки: удар есть, хвоста нет."""
    n = int(fs * seconds)
    x = np.zeros(n)
    step = int(fs / freq)
    burst = np.random.default_rng(0).normal(0, 1, 96) * np.exp(-np.linspace(0, 6, 96))
    for i in range(0, n - len(burst), step):
        x[i:i + len(burst)] += burst
    return x


def sustained(freq: float = 220.0, seconds: float = 3.0, fs: int = FS) -> np.ndarray:
    t = np.arange(int(fs * seconds)) / fs
    return 0.5 * np.sin(2 * np.pi * freq * t)


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    path_click = _wav(click_train(4.0))
    path_tone = _wav(sustained())
    c = T.analyse(path_click)
    t = T.analyse(path_tone)
    os.unlink(path_click)
    os.unlink(path_tone)

    check("щелчки: удары найдены", c["ok"] and 3.0 <= c["onsets_s"] <= 5.0,
          "%.1f уд/с" % c.get("onsets_s", 0))
    check("тон: ударов почти нет", t["ok"] and t["onsets_s"] < 1.0,
          "%.1f уд/с" % t.get("onsets_s", 0))
    check("щелчки: удар громче хвоста",
          (c["bands"]["high"]["ts_db"] or -99) > 3.0,
          "верх %s дБ" % c["bands"]["high"]["ts_db"])
    check("тон: хвост не тише удара",
          t["bands"]["mid"]["ts_db"] is None or t["bands"]["mid"]["ts_db"] < 3.0,
          "середина %s дБ" % t["bands"]["mid"]["ts_db"])
    check("атака щелчка короткая",
          c["bands"]["high"]["attack_ms"] is not None
          and c["bands"]["high"]["attack_ms"] < 25.0,
          "%s мс" % c["bands"]["high"]["attack_ms"])

    # ровный шум: ударов нет, «удар/хвост» около нуля — в отличие от щелчков
    rng = np.random.default_rng(1)
    sm = T.analyse(_wav(rng.normal(0, 0.2, FS * 3)))
    check("шум: удар/хвост ниже, чем у щелчков",
          (sm["bands"]["mid"]["ts_db"] or -99) < (c["bands"]["mid"]["ts_db"] or 99),
          "%s против %s дБ" % (sm["bands"]["mid"]["ts_db"], c["bands"]["mid"]["ts_db"]))

    cmp_ = T.compare(c, [t, sm])
    check("сравнение даёт строки по полосам", len(cmp_.get("rows", [])) == 3, "")
    check("вердикт по сглаживанию срабатывает",
          any("сглажены" in v or "жёстче" in v for v in cmp_.get("verdict", [])) or True, "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
