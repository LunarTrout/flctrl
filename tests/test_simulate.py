"""Проверки симуляции: правка, применённая к стемам, действительно слышна.

Это замкнутая петля: план → применение по областям → повторное измерение.
Проверяем, что усиление в полосе переносится почти один к одному и что
слышимость роли растёт, а не просто «файл стал громче».
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import partial  # noqa: E402
import simulate  # noqa: E402

FS = 24000
SEC = 8.0


def band(fc: float, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sos = butter(4, [fc * 0.7, fc * 1.3], btype="bandpass", fs=FS, output="sos")
    y = sosfilt(sos, rng.standard_normal(n))
    return y / (np.sqrt((y ** 2).mean()) + 1e-12)


def w(path: str, x: np.ndarray) -> str:
    sf.write(path, np.stack([x, x], axis=1), FS)
    return path


def snr_mid(mix: str, stems: dict, stem: str) -> float:
    attr = partial.attribute_mg(mix, stems, None, None, section_s=SEC)
    secs = attr["stems"][stem].get("sections") or []
    vals = [s["snr"]["mid"] for s in secs if "mid" in (s.get("snr") or {})]
    return float(np.median(vals)) if vals else 0.0


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flsimt_")
    n = int(SEC * FS)
    t = np.arange(n) / FS
    gtr = 0.25 * band(1000, n, 1) * (0.8 + 0.2 * np.sin(2 * np.pi * 1.5 * t))
    bass = 0.03 * band(1000, n, 2)
    kick = 0.10 * band(100, n, 3)

    gb = w(os.path.join(tmp, "gtr.wav"), gtr)
    bb = w(os.path.join(tmp, "bass.wav"), bass)
    kb = w(os.path.join(tmp, "kick.wav"), kick)
    mix = w(os.path.join(tmp, "mix.wav"), gtr + bass + kick)
    stems = {"gtr": gb, "bass": bb, "kick": kb}

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    # 1. усиление в полосе переносится в отношение почти один к одному
    before = snr_mid(mix, stems, "bass")
    x, fs = simulate._read(bb)
    y = simulate.apply_gains(x, fs, {"mid": 6.0})
    bb6 = w(os.path.join(tmp, "bass6.wav"), y)
    mix6 = w(os.path.join(tmp, "mix6.wav"), gtr + y + kick)
    after = snr_mid(mix6, {"gtr": gb, "bass": bb6, "kick": kb}, "bass")
    check("усиление 6 дБ видно в измерении", 5.0 <= after - before <= 7.0,
          "%+.1f дБ" % (after - before))

    # 2. полный цикл: план -> применение -> повторный замер
    attr0 = partial.attribute_mg(mix, stems, None, None, section_s=SEC)
    roles = {"gtr": "guitar", "bass": "bass", "kick": "drums"}
    import decide
    plan = decide.plan(attr0, cap_db=6.0, min_deficit_db=1.0, min_share_pct=0.1,
                       targets={"bass": {"mid": 0.0}}, roles_map=roles)
    bass_edits = [e for e in plan["edits"] if e["stem"] == "bass"]
    check("план предложил поднять бас", bool(bass_edits),
          str([(e["region"], e["do_db"]) for e in bass_edits]))

    sim = simulate.simulate(mix, stems, plan["edits"])
    check("новый микс собран", os.path.exists(sim["mix"]), sim["mix"][-24:])

    attr1 = partial.attribute_mg(sim["mix"], sim["stems"], None, None, section_s=SEC)
    diff = {r["stem"]: r for r in simulate.compare_before_after(attr0, attr1)["rows"]}
    check("бас стал слышнее", diff["bass"]["audib_after"] > diff["bass"]["audib_before"],
          "%.3f → %.3f" % (diff["bass"]["audib_before"], diff["bass"]["audib_after"]))
    check("доля баса выросла", diff["bass"]["share_after"] > diff["bass"]["share_before"],
          "%.1f → %.1f %%" % (diff["bass"]["share_before"], diff["bass"]["share_after"]))

    # 3. повторный план видит меньший дефицит
    plan2 = decide.plan(attr1, cap_db=6.0, min_deficit_db=1.0, min_share_pct=0.1,
                        targets={"bass": {"mid": 0.0}}, roles_map=roles)
    d1 = min([e["need_db"] for e in plan["edits"] if e["stem"] == "bass" and e["region"] == "mid"] or [0])
    d2 = min([e["need_db"] for e in plan2["edits"] if e["stem"] == "bass" and e["region"] == "mid"] or [0])
    check("дефицит после правки меньше", d2 < d1 - 0.9, "%.1f → %.1f дБ" % (d1, d2))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
