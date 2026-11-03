"""measure_all.py — полный замер «по всем фронтам» нашей библиотекой.

Референсы и наш микс (сумма стемов) прогоняются через flctrl.timbre.analyze;
по клеткам берутся медианы всех числовых признаков, плюс lufs/lra/onsets/mod/jitter.

    python measure_all.py --seconds 30
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
import soundfile as sf

BASE = r"C:\Users\epicc\mcp\flstudio-lib"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "flctrl"))

import timbre  # noqa: E402  # pyright: ignore[reportMissingImports]

P = r"C:\Users\epicc\Desktop\PROJECTS\vers1"
STEMS = os.path.join(P, "_stems_take")
REFS = os.path.join(P, "_refs")
OUT = os.path.join(P, "_sandbox")


def measure(src, seconds: float) -> dict:
    if isinstance(src, str):
        x, sr = sf.read(src, always_2d=True)
        if x.shape[0] > seconds * sr:
            hop = sr * 2
            n = x.shape[0] // hop
            rms = [float(np.sqrt((x[i * hop:(i + 1) * hop] ** 2).mean())) for i in range(n)]
            i = int(np.argmax(rms))
            x = x[i * hop:i * hop + int(seconds * sr)]
    else:
        x, sr = src, 44100
    if x.shape[0] < 8192:
        return {}
    t = timbre.analyze(x, fs=sr, hop_s=0.1)
    cells = t.get("cells") or []
    out: dict = {}
    keys = set()
    for c in cells:
        keys |= set(c.keys())
    for k in sorted(keys):
        vals = []
        for c in cells:
            v = c.get(k)
            if isinstance(v, (int, float)) and np.isfinite(v):
                vals.append(float(v))
        if vals and len(vals) >= max(3, len(cells) // 3):
            out[k] = round(float(np.median(vals)), 3)
    if cells and isinstance(cells[0].get("bands"), list):
        b = np.median(np.array([c["bands"] for c in cells], dtype=float), axis=0)
        out["bands"] = [round(float(v), 1) for v in b]
    for k in ("lufs", "lra", "onset_rate_hz", "onsets", "mod_rate_hz", "mod_depth", "jitter_pct"):
        if k in t:
            out[k] = t[k]
    m = x.mean(axis=1)
    out["rms_total_db"] = round(20 * np.log10(float(np.sqrt((m ** 2).mean())) + 1e-12), 1)
    out["peak_db"] = round(20 * np.log10(float(np.abs(m).max()) + 1e-12), 1)
    out["crest_all_db"] = round(out["peak_db"] - out["rms_total_db"], 1)
    return out


def load_stem_sum(seconds: float) -> np.ndarray:
    acc = None
    for f in sorted(glob.glob(os.path.join(STEMS, "vers1_2026-11-04 02-22-34_*.wav"))):
        x, sr = sf.read(f, always_2d=True, frames=int(seconds * 44100))
        if float(np.sqrt((x ** 2).mean())) < 1e-4:
            continue
        acc = x.astype(np.float64) if acc is None else acc + x
    return acc if acc is not None else np.zeros((0, 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args()

    rows = {}
    for f in sorted(glob.glob(os.path.join(REFS, "*.wav"))):
        rows[os.path.basename(f)[:22]] = measure(f, a.seconds)
    mix = load_stem_sum(a.seconds)
    if mix.size:
        rows["НАШ МИКС"] = measure(mix, a.seconds)

    names = list(rows.keys())
    keys = sorted({k for r in rows.values() for k in r if k != "bands"})
    print("%-18s" % "метрика", " ".join("%14s" % n[:14] for n in names))
    for k in keys:
        cells = []
        for n in names:
            v = rows[n].get(k)
            cells.append("%14.2f" % v if isinstance(v, (int, float)) else "%14s" % "—")
        print("%-18s" % k, " ".join(cells))

    refs = [rows[n] for n in names if not n.startswith("НАШ")]
    ours = rows.get("НАШ МИКС")
    if refs and ours:
        print("\nΔ (наш − среднее референсов):")
        for k in keys:
            if all(k in r for r in refs) and k in ours:
                print("  %-16s %+8.2f" % (k, ours[k] - float(np.mean([r[k] for r in refs]))))
        if "bands" in ours and all("bands" in r for r in refs):
            tb = np.mean([r["bands"] for r in refs], axis=0)
            d = np.asarray(ours["bands"]) - tb
            print("  полосы Δ: %s" % np.round(d, 1).tolist())
            print("  средняя |Δ| %.2f, макс %.2f" % (np.abs(d).mean(), np.abs(d).max()))

    os.makedirs(OUT, exist_ok=True)
    json.dump(rows, open(os.path.join(OUT, "measure_all.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\nсохранено:", os.path.join(OUT, "measure_all.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
