"""sandbox_mix.py — песочница: подгоняем баланс дорожек под референсы, не трогая проект.

Берём стемы (40 с), суммируем с групповыми гейнами, считаем «ленту» и то, чего в ленте
нет — LUFS-подобную громкость, crest, ширину, корреляцию, плотность онсетов — и сравниваем
с целями из референсов. Гейны подбираются численно.

    python sandbox_mix.py --seconds 40
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import soundfile as sf

STEMS = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_stems_take"
REFS = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_refs"
OUT = r"C:\Users\epicc\Desktop\PROJECTS\vers1\_sandbox"
FC = np.array([31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 12500])

GROUPS = {
    "drums": ["DRUMS SD3", "DRUM BUS", "CYMBALS"],
    "bass": ["BASS", "Sub Bass", "Main Bass"],
    "gtr_l": ["GTR L1", "GTR L BUS", "GTR HI"],
    "gtr_r": ["GTR R1", "GTR R BUS"],
    "gtr_lead": ["GTR LEAD"],
    "vox": ["Scream vocal", "Ann vocal", "VOX BUS"],
    "keys": ["KEYS 1", "KEYS 2", "SYNTH", "BAYAN", "баян1L", "баян1R", "баян2L", "баян2R"],
    "atmo": ["ATMO 1", "ATMO 2", "AUDIO 2"],
}


def bands(x: np.ndarray, sr: int = 44100) -> np.ndarray:
    m = x.mean(axis=1)
    if m.size < 8192:
        return np.full(len(FC), -120.0)
    w = np.hanning(m.size)
    sp = np.abs(np.fft.rfft(m * w)) ** 2
    f = np.fft.rfftfreq(m.size, 1 / sr)
    out = []
    for fc in FC:
        sel = (f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))
        out.append(10 * np.log10(max(sp[sel].mean(), 1e-20)))
    return np.array(out)


def features(x: np.ndarray, sr: int = 44100) -> dict:
    m = x.mean(axis=1)
    rms = float(np.sqrt((m ** 2).mean()))
    peak = float(np.abs(m).max())
    if x.ndim > 1 and x.shape[1] > 1:
        l, r = x[:, 0], x[:, 1]
        corr = float(np.corrcoef(l, r)[0, 1]) if l.std() > 1e-9 and r.std() > 1e-9 else 0.0
        mid, side = (l + r) / 2, (l - r) / 2
        width = float(np.sqrt((side ** 2).mean()) / max(np.sqrt((mid ** 2).mean()), 1e-9))
    else:
        corr, width = 1.0, 0.0
    # плотность онсетов: пики огибающей выше 1.5 медианы
    hop = 512
    n = (m.size // hop) * hop
    envd = np.sqrt((m[:n].reshape(-1, hop) ** 2).mean(axis=1)) if n else np.zeros(1)
    thr = 1.5 * (np.median(envd) + 1e-9)
    onsets = int(((envd[1:] > thr) & (envd[:-1] <= thr)).sum())
    return {
        "rms_db": round(20 * np.log10(rms + 1e-12), 2),
        "crest_db": round(20 * np.log10(peak / max(rms, 1e-9) + 1e-12), 2),
        "width": round(width, 3), "corr": round(corr, 3),
        "onsets_per_s": round(onsets / (m.size / sr), 2),
        "centroid_hz": int((np.abs(np.fft.rfft(m * np.hanning(m.size))) *
                            np.fft.rfftfreq(m.size, 1 / sr)).sum() /
                           max(np.abs(np.fft.rfft(m * np.hanning(m.size))).sum(), 1e-9)),
    }


def load_stems(seconds: float) -> dict:
    out = {}
    for f in sorted(glob.glob(os.path.join(STEMS, "vers1_2026-11-04 02-22-34_*.wav"))):
        name = os.path.basename(f).split("02-22-34_", 1)[-1][:-4]
        try:
            x, sr = sf.read(f, always_2d=True, frames=int(seconds * 44100))
        except Exception:
            continue
        if x.shape[0] < 8192 or float(np.sqrt((x ** 2).mean())) < 1e-4:
            continue
        out[name] = x
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0)
    a = ap.parse_args()

    stems = load_stems(a.seconds)
    print("дорожек со звуком:", len(stems), flush=True)

    refs = []
    for f in sorted(glob.glob(os.path.join(REFS, "*.wav"))):
        x, sr = sf.read(f, always_2d=True)
        hop = sr * 2
        n = len(x) // hop
        rms = [float(np.sqrt((x[i * hop:(i + 1) * hop] ** 2).mean())) for i in range(max(n, 1))]
        i = int(np.argmax(rms))
        seg = x[i * hop: i * hop + int(a.seconds * sr)]
        if seg.shape[0] > 8192:
            refs.append(seg)
    tgt_bands = np.mean([bands(r) for r in refs], axis=0)
    tgt_feat = {k: float(np.mean([features(r)[k] for r in refs]))
                for k in ("rms_db", "crest_db", "width", "corr", "onsets_per_s")}
    print("референсов:", len(refs), flush=True)
    print("цель:", {k: round(v, 2) for k, v in tgt_feat.items()}, flush=True)

    # группируем дорожки
    group_of = {}
    for g, names in GROUPS.items():
        for nm in names:
            if nm in stems:
                group_of[nm] = g
    missing = [nm for nm in stems if nm not in group_of]
    for nm in missing:
        group_of[nm] = "other"

    groups = sorted(set(group_of.values()))
    n = int(a.seconds * 44100)
    stack = {g: np.zeros((n, 2), dtype=np.float32) for g in groups}
    for nm, x in stems.items():
        g = group_of[nm]
        k = min(n, x.shape[0])
        stack[g][:k] += x[:k]

    # нормируем группы на себя, чтобы гейн 0 дБ = текущий уровень
    base = {}
    for g in groups:
        base[g] = stack[g].copy()
    cur = sum(base[g] for g in groups)
    cur_bands = bands(cur)
    cur_feat = features(cur)
    print("сейчас:", {k: round(v, 2) for k, v in cur_feat.items()}, flush=True)
    print("Δ полос сейчас:", np.round(cur_bands - tgt_bands, 1).tolist(), flush=True)

    # подбор гейнов: минимизируем |полосы| + штраф за громкость/ширину
    def mix(gains):
        return sum(base[g] * (10 ** (gains[i] / 20.0)) for i, g in enumerate(groups))

    def cost(gains):
        m = mix(gains)
        d = bands(m) - tgt_bands
        f = features(m)
        pen = 0.6 * (f["rms_db"] - tgt_feat["rms_db"]) ** 2 / 10
        pen += 2.0 * (f["width"] - tgt_feat["width"]) ** 2
        pen += 2.0 * (f["corr"] - tgt_feat["corr"]) ** 2
        pen += 0.02 * (f["crest_db"] - tgt_feat["crest_db"]) ** 2
        return float(np.mean(d ** 2)) + pen

    from itertools import product
    grid = [-6, -4, -2, 0, 2, 4]
    best = None
    # координатный спуск по группам (дёшево и достаточно)
    gains = {g: 0.0 for g in groups}
    for _ in range(3):
        for g in groups:
            best_v, best_c = gains[g], cost([gains[x] for x in groups])
            for v in grid:
                trial = dict(gains)
                trial[g] = v
                c = cost([trial[x] for x in groups])
                if c < best_c:
                    best_v, best_c = v, c
            gains[g] = best_v

    order = [gains[g] for g in groups]
    m = mix(order)
    f = features(m)
    d = bands(m) - tgt_bands
    print("\nгейны по группам, дБ:", {g: round(gains[g], 1) for g in groups}, flush=True)
    print("получилось:", {k: round(v, 2) for k, v in f.items()}, flush=True)
    print("Δ полос:", np.round(d, 1).tolist(), flush=True)
    print("средняя |Δ| %.2f дБ, макс %.2f дБ" % (np.abs(d).mean(), np.abs(d).max()), flush=True)

    os.makedirs(OUT, exist_ok=True)
    sf.write(os.path.join(OUT, "sandbox_mix.wav"), m, 44100, subtype="FLOAT")
    json.dump({"gains": gains, "features": f, "delta_bands": d.tolist(),
               "target": {**{k: round(v, 2) for k, v in tgt_feat.items()},
                          "bands": tgt_bands.tolist()}},
              open(os.path.join(OUT, "sandbox_mix.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("превью:", os.path.join(OUT, "sandbox_mix.wav"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
