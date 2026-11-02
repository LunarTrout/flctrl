"""Проверка модели частичной громкости на синтетике (без FL и без проекта).

Три свойства, которые обязаны выполняться, иначе модель нельзя использовать
для решений:

1. Если все источники, кроме одного, молчат, то N_partial одиночного источника
   совпадает с его N_solo (нечему маскировать).
2. Подъём фейдера источника увеличивает его N_partial (монотонно).
3. Сумма частичных громкостей не превышает громкость микса и растёт, когда
   источник прибавляют.
"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import partial            # noqa: E402

FS = 22050
DUR = 8.0


def tone(freq: float, amp: float = 0.1, seconds: float = DUR, fs: int = FS):
    t = np.arange(int(seconds * fs)) / fs
    fade = np.minimum(1.0, np.minimum(t, seconds - t) * 10)
    return (amp * np.sin(2 * np.pi * freq * t) * fade).astype(np.float64)


def noise(amp: float = 0.05, seconds: float = DUR, fs: int = FS, seed: int = 0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * fs)) * amp
    fade = np.minimum(1.0, np.minimum(np.arange(len(x)) / fs,
                                      (len(x) / fs) - np.arange(len(x)) / fs) * 10)
    return (x * fade).astype(np.float64)


def write(path: str, x: np.ndarray, fs: int = FS) -> str:
    sf.write(path, np.clip(x, -1, 1), fs)
    return path


def main() -> int:
    tmp = os.path.join(HERE, "_partial_tmp")
    os.makedirs(tmp, exist_ok=True)

    drums = write(os.path.join(tmp, "drums.wav"), noise(0.08, seed=1))
    bass = write(os.path.join(tmp, "bass.wav"), tone(60.0, 0.12))
    lead = write(os.path.join(tmp, "lead.wav"), tone(900.0, 0.06))

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-40s %s" % (name, detail))

    # 1. один источник в миксе = его сольная громкость
    solo_mix = write(os.path.join(tmp, "solo_mix.wav"), tone(60.0, 0.12))
    r = partial.attribute(solo_mix, {"bass": bass})
    st = r["stems"]["bass"]
    ratio = st["N_partial"] / max(st["N_solo"], 1e-9)
    check("один источник: N_partial ≈ N_solo", 0.9 <= ratio <= 1.05,
          "отношение %.3f" % ratio)

    # 2. подъём фейдера увеличивает частичную громкость
    p_mix = None
    levels = []
    for gain_db in (0.0, 3.0, 6.0):
        g = 10 ** (gain_db / 20.0)
        mix = write(os.path.join(tmp, "mix_%d.wav" % int(gain_db)),
                    np.clip(tone(60.0, 0.12 * g) + noise(0.08, seed=1), -1, 1))
        rr = partial.attribute(mix, {"bass": bass, "drums": drums})
        levels.append(rr["stems"]["bass"]["N_partial"])
        p_mix = rr
    check("подъём баса растит его N_partial",
          levels[0] < levels[1] < levels[2],
          "%.2f → %.2f → %.2f" % tuple(levels))

    # 3. сумма частичных ≤ громкость микса и доли считаются честно
    total = sum(v["N_partial"] for v in p_mix["stems"].values())
    shares = sum(v["share_pct"] for v in p_mix["stems"].values())
    check("доли частичной громкости дают 100%", abs(shares - 100.0) < 0.5,
          "сумма %.1f%%" % shares)
    check("частичные не больше сольных", all(
        v["N_partial"] <= v["N_solo"] * 1.05 for v in p_mix["stems"].values()),
        "total %.1f" % total)

    # 4. третий источник в далёкой полосе почти не забирает слышимость у баса
    mix2 = write(os.path.join(tmp, "mix2.wav"),
                 np.clip(tone(60.0, 0.12) + noise(0.08, seed=1) + tone(900.0, 0.06), -1, 1))
    r2 = partial.attribute(mix2, {"bass": bass, "drums": drums, "lead": lead})
    loss = 1.0 - r2["stems"]["bass"]["N_partial"] / max(p_mix["stems"]["bass"]["N_partial"], 1e-9)
    check("далёкий по частоте источник не глушит бас", loss < 0.15,
          "потеря %.1f%%" % (100 * loss))

    # 5. роли: баян не должен стать басом, бас — гитарой
    import roles
    cases = {"GTR L1": "guitar", "GTR R BUS": "guitar", "BAYAN": "keys",
             "баян1L": "keys", "BASS": "bass", "Scream vocal": "vocals",
             "DRUMS SD3": "drums", "CYMBALS": "drums"}
    wrong = {k: (roles.role_of(k), want) for k, want in cases.items()
             if roles.role_of(k) != want}
    check("роли по именам без ошибок", not wrong, str(wrong))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
