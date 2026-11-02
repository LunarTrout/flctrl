"""Проверка модели частичной громкости на синтетике (без FL и без проекта).

Ключевое требование к тесту: микс собирается ровно из тех же стемов, что потом
подаются на вход модели. Иначе вычитание «микс минус стем» оставляет разницу
фаз и уровней, и любые числа бессмысленны — на этом уже обожглись.

Проверяем свойства, без которых модели нельзя доверять:
1. Один источник в миксе: N_partial ≈ N_solo.
2. Подъём фейдера источника увеличивает его N_partial.
3. Доли частичной громкости в сумме дают 100 %.
4. Источник в далёкой полосе почти не отнимает слышимость у баса.
5. Роли по именам определяются без ошибок.
"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import partial            # noqa: E402
import roles              # noqa: E402

FS = 44100
DUR = 6.0


def make(freq, amp, seed=None):
    t = np.arange(int(DUR * FS)) / FS
    fade = np.minimum(1.0, np.minimum(t, DUR - t) * 8)
    if seed is None:
        x = np.sin(2 * np.pi * freq * t)
    else:
        x = np.random.default_rng(seed).standard_normal(len(t))
    return amp * x * fade


def write(path, x):
    sf.write(path, x.astype(np.float64), FS)
    return path


def sum_to(path, *parts):
    n = min(len(p) for p in parts)
    return write(path, sum(p[:n] for p in parts))


def main() -> int:
    tmp = os.path.join(HERE, "_partial_tmp")
    os.makedirs(tmp, exist_ok=True)

    drums = make(0, 0.05, seed=1)       # шум
    bass = make(60.0, 0.05)             # тон
    lead = make(900.0, 0.04)            # тон выше

    p_drums = write(os.path.join(tmp, "drums.wav"), drums)
    p_bass = write(os.path.join(tmp, "bass.wav"), bass)
    p_lead = write(os.path.join(tmp, "lead.wav"), lead)

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    # 1. один источник
    solo_mix = write(os.path.join(tmp, "solo.wav"), bass)
    r = partial.attribute(solo_mix, {"bass": p_bass})
    st = r["stems"]["bass"]
    ratio = st["N_partial"] / max(st["N_solo"], 1e-9)
    check("один источник: N_partial ≈ N_solo", 0.9 <= ratio <= 1.05,
          "отношение %.3f" % ratio)

    # 2. подъём фейдера: стем и микс масштабируются одинаково
    levels = []
    for gain_db in (0.0, 3.0, 6.0):
        g = 10 ** (gain_db / 20.0)
        mix = sum_to(os.path.join(tmp, "mix_%d.wav" % int(gain_db)),
                     drums, bass * g, lead)
        stem = write(os.path.join(tmp, "bass_%d.wav" % int(gain_db)), bass * g)
        rr = partial.attribute(mix, {"bass": stem})
        levels.append(rr["stems"]["bass"]["N_partial"])
    check("подъём источника растит его N_partial",
          levels[0] < levels[1] < levels[2],
          "%.2f → %.2f → %.2f" % tuple(levels))

    # 3. доли и границы
    mix3 = sum_to(os.path.join(tmp, "mix3.wav"), drums, bass, lead)
    r3 = partial.attribute(mix3, {"drums": p_drums, "bass": p_bass, "lead": p_lead})
    shares = sum(v["share_pct"] for v in r3["stems"].values())
    check("доли частичной громкости дают 100%", abs(shares - 100.0) < 0.5,
          "сумма %.1f%%" % shares)
    check("частичные не больше сольных",
          all(v["N_partial"] <= v["N_solo"] * 1.05 for v in r3["stems"].values()),
          "")

    # 4. далёкий по частоте источник не забирает слышимость у баса
    mix2 = sum_to(os.path.join(tmp, "mix2.wav"), drums, bass)
    r_without = partial.attribute(mix2, {"bass": p_bass})["stems"]["bass"]["N_partial"]
    r_with = r3["stems"]["bass"]["N_partial"]
    loss = 1.0 - r_with / max(r_without, 1e-9)
    check("далёкий источник почти не глушит бас", abs(loss) < 0.15,
          "изменение %.1f%%" % (100 * loss))

    # 5. роли
    cases = {"GTR L1": "guitar", "GTR R BUS": "guitar", "BAYAN": "keys",
             "баян1L": "keys", "BASS": "bass", "Scream vocal": "vocals",
             "DRUMS SD3": "drums", "CYMBALS": "drums"}
    wrong = {k: (roles.role_of(k), want) for k, want in cases.items()
             if roles.role_of(k) != want}
    check("роли по именам без ошибок", not wrong, str(wrong))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    print("модель:", r3.get("model", "zwicker"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
