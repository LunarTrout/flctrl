"""simulate.py — применить план к стемам и посмотреть, стало ли лучше.

Смысл: план — это гипотеза. Пока её не применили и не измерили заново, это
просто числа. Здесь правки применяются по-настоящему: каждый стем делится на
семь областей, в нужных областях меняется усиление, стемы складываются в новый
микс, и измерение повторяется.

Ограничение, которое нужно помнить: правка применяется ко всему стему, а не
только к участку, где она предложена. Это проверка направления и величины, а не
точная копия будущей автоматизации.

    python -m flctrl.simulate --mix m.wav --stems drums=d.wav --targets t.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile

import numpy as np

REGION_EDGES = {
    "sub": (20.0, 60.0), "bass": (60.0, 120.0), "lowmid": (120.0, 300.0),
    "mid": (300.0, 1200.0), "upmid": (1200.0, 3000.0),
    "pres": (3000.0, 6000.0), "air": (6000.0, 16000.0),
}


def _read(path: str):
    import soundfile as sf
    x, fs = sf.read(path, dtype="float64", always_2d=False)
    if x.ndim == 2:
        x = x.mean(axis=1)
    return x, fs


def region_gains(edits: list[dict], stem: str) -> dict:
    """Свести правки стема в усиления по областям (медиана по участкам)."""
    by_region: dict = {}
    for e in edits:
        if e["stem"] != stem:
            continue
        by_region.setdefault(e["region"], []).append(e["do_db"])
    return {r: float(statistics.median(v)) for r, v in by_region.items()}


def apply_gains(x: np.ndarray, fs: int, gains: dict, order: int = 4) -> np.ndarray:
    """Разделить на области и сложить обратно с усилением в каждой."""
    from scipy.signal import butter, sosfilt

    out = np.zeros_like(x)
    for region, (lo, hi) in REGION_EDGES.items():
        db = float(gains.get(region, 0.0))
        if abs(db) < 1e-9 or lo >= fs / 2:
            continue
        nyq = fs / 2 * 0.99
        hi_b = min(hi, nyq)
        if lo >= hi_b:
            continue
        sos = butter(order, [lo, hi_b], btype="bandpass", fs=fs, output="sos")
        band = sosfilt(sos, x)
        out += band * (10 ** (db / 20.0))
    # то, что осталось вне полос (ниже 20 Гц), сохраняем как было
    return out


def simulate(mix_path: str, stems: dict[str, str], edits: list[dict],
             outdir: str | None = None) -> dict:
    """Собрать новый микс с применёнными правками."""
    outdir = outdir or tempfile.mkdtemp(prefix="flsim_")
    os.makedirs(outdir, exist_ok=True)
    import soundfile as sf

    new_stems = {}
    length = None
    fs_ref = None
    for name, path in stems.items():
        x, fs = _read(path)
        gains = region_gains(edits, name)
        y = apply_gains(x, fs, gains) if gains else x
        out = os.path.join(outdir, "%s.wav" % name)
        sf.write(out, y, fs)
        new_stems[name] = out
        length = len(y) if length is None else min(length, len(y))
        fs_ref = fs if fs_ref is None else fs_ref

    # микс собираем из обработанных стемов
    acc = None
    for name, path in new_stems.items():
        y, _ = _read(path)
        y = y[:length]
        acc = y if acc is None else acc + y
    mix_out = os.path.join(outdir, "mix.wav")
    sf.write(mix_out, acc, fs_ref)
    return {"mix": mix_out, "stems": new_stems, "dir": outdir,
            "applied": {n: region_gains(edits, n) for n in stems}}


def compare_before_after(attr_before: dict, attr_after: dict) -> dict:
    """Насколько упал худший дефицит слышимости по ролям."""
    rows = []
    for name, before in (attr_before.get("stems") or {}).items():
        after = (attr_after.get("stems") or {}).get(name)
        if not after:
            continue
        rows.append({"stem": name,
                     "audib_before": round(before["audib"], 3),
                     "audib_after": round(after["audib"], 3),
                     "share_before": round(before["share_pct"], 1),
                     "share_after": round(after["share_pct"], 1)})
    return {"rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Применить план к стемам и перемерить")
    ap.add_argument("--mix", required=True)
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--targets", default=None)
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--section", type=float, default=8.0)
    args = ap.parse_args(argv)

    import decide
    import partial
    import roles as roles_mod
    import snrtargets

    stems = {}
    for item in args.stems:
        name, _, path = item.partition("=")
        stems[name] = path

    # честная база для сравнения: сумма стемов, а не мастер (иначе до/после —
    # разные вещи: мастер уже обработан, сумма стемов ещё нет)
    base = simulate(args.mix, stems, [])
    attr0 = partial.attribute_mg(base["mix"], base["stems"], None, None,
                                 section_s=args.section)
    roles_map = {n: roles_mod.role_of(n, p) for n, p in stems.items()}
    tg = {}
    if args.targets:
        model = snrtargets.load(args.targets)
        for name, role in roles_map.items():
            t = snrtargets.for_role(model, role)
            if t:
                tg[role] = t
    plan0 = decide.plan(attr0, targets=tg or None, roles_map=roles_map)
    print("правок в плане:", plan0["count"])

    sim = simulate(args.mix, stems, plan0["edits"])
    attr1 = partial.attribute_mg(sim["mix"], sim["stems"], None, None,
                                 section_s=args.section)
    diff = compare_before_after(attr0, attr1)

    print("\n%-8s %8s %8s %8s %8s" % ("роль", "A до", "A после", "доля до", "доля после"))
    for r in diff["rows"]:
        print("%-8s %8.3f %8.3f %8.1f %8.1f" % (
            r["stem"], r["audib_before"], r["audib_after"],
            r["share_before"], r["share_after"]))
    print("\nновый микс:", sim["mix"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
