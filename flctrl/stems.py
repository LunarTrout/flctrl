"""Разбор стемов: профиль каждой дорожки против референсов и баланс барабанов.

Зачем в библиотеке: разовые скрипты в tools-local живут до первой уборки, а
эти два разбора нужны каждый раз после рендера микшера. Считаются через слой
слуха (fl.hear), поэтому работают в любом окружении.
"""
from __future__ import annotations

import glob
import os

GROUPS = (
    ("drums", ("drum", "бараб", "kick", "snare", "sd3", "overhead", "cymbal", "цимбал")),
    ("bass", ("bass", "бас")),
    ("vocals", ("vocl", "vox", "вокал", "voice", "scream", "girl", "ann", "vocal")),
    ("other", ("gtr", "гитар", "lead", "bariton", "баритон", "bayan", "баян",
               "synth", "pad", "atmo", "aux", "ref", "current")),
)


def group_of(name: str) -> str:
    low = name.lower()
    for group, keys in GROUPS:
        if any(k in low for k in keys):
            return group
    return "other"


def _ref_median(refs_dir: str, group: str, hear) -> dict | None:
    values = []
    for track in sorted(glob.glob(os.path.join(refs_dir, "*"))):
        path = os.path.join(track, "%s.wav" % group)
        if not os.path.exists(path):
            continue
        try:
            values.append(hear.measure(path)["regions_pct"])
        except Exception:
            continue
    if not values:
        return None
    keys = values[0].keys()
    return {k: round(sum(v[k] for v in values) / len(values), 1) for k in keys}


def report(stem_dir: str, refs_dir: str | None = None, prefix: str = "",
           a: float = 0.0, b: float = 30.0, hear=None) -> dict:
    """Профиль каждого стема и, если есть референсы, расхождение с ними."""
    if hear is None:
        import fl_lib
        hear = fl_lib.hear
    pattern = os.path.join(stem_dir, "%s*.wav" % prefix)
    paths = [p for p in sorted(glob.glob(pattern))
             if os.path.getsize(p) > 200_000 and "converted" not in p]
    if not paths:
        return {"error": "стемов не найдено: %s" % pattern, "rows": []}

    refs: dict[str, dict | None] = {}
    rows = []
    for path in paths:
        name = os.path.basename(path)
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
        name = os.path.splitext(name)[0]
        group = group_of(name)
        try:
            m = hear.measure(path, a, b)
        except Exception as e:
            rows.append({"file": os.path.basename(path), "group": group,
                         "error": None, "measure_error": str(e)[:120]})
            continue
        row = {"file": os.path.basename(path), "name": name, "group": group,
               "bands": {k: round(v, 1) for k, v in m["regions_pct"].items()},
               "sharp": round(m["sharp_mean"], 2), "crest": round(m["crest_db"], 1),
               "ldr": round(m["ldr_db"], 1), "rms": m.get("lufs")}
        if refs_dir:
            if group not in refs:
                refs[group] = _ref_median(refs_dir, group, hear)
            ref = refs[group]
            if ref:
                keys = [k for k in ref if k in row["bands"]]
                row["ref"] = ref
                row["error"] = round(
                    sum(abs(row["bands"][k] - ref[k]) for k in keys) / len(keys), 1)
        rows.append(row)

    rows.sort(key=lambda r: -(r.get("error") or -1))
    return {"stems": len(rows), "rows": rows,
            "refs": {k: v for k, v in refs.items() if v}}


def drums_balance(path: str, a: float = 0.0, dur: float = 30.0) -> dict:
    """Бочка/малый/тело/железо и плотность атак по одному файлу."""
    import numpy as np
    import soundfile as sf

    info = sf.info(path)
    fs = info.samplerate
    begin = min(int(a * fs), max(0, info.frames - int(dur * fs)))
    x, fs = sf.read(path, start=begin, frames=int(dur * fs), dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.shape[0] < fs // 2:
        return {"error": "слишком короткий отрезок"}

    def energy(lo: float, hi: float) -> float:
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        freqs = np.fft.rfftfreq(len(x), 1.0 / fs)
        return float(spec[(freqs >= lo) & (freqs <= hi)].sum())

    kick, snare = energy(40, 100), energy(150, 300)
    body, metal = energy(800, 2000), energy(6000, 12000)
    total = kick + snare + body + metal + 1e-20

    hop = int(0.01 * fs)
    frames = len(x) // hop
    env = np.sqrt(np.mean(x[:frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-20)
    env_db = 20 * np.log10(env)
    above = env_db > (np.median(env_db) + 6.0)
    onsets = int(np.sum(above[1:] & ~above[:-1]))
    return {
        "kick_pct": round(100 * kick / total, 1),
        "snare_pct": round(100 * snare / total, 1),
        "body_pct": round(100 * body / total, 1),
        "metal_pct": round(100 * metal / total, 1),
        "onsets_per_s": round(onsets / (frames * hop / fs), 1),
        "rms_db": round(20 * float(np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)), 1),
    }
