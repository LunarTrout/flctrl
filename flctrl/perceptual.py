#!/usr/bin/env python
"""perceptual.py — перцептивный анализ микса: не «энергия в полосах», а слышимая громкость.

Ядро — ISO 532-1 (Zwicker): 28 третьоктавных полос -> удельная громкость N'(z)
на 240 полосах Барка (0.1 Bark) -> суммарная громкость N (соны), уровень (фоны),
резкость DIN 45692 (acum). Модель считается по кадрам, поэтому работает
в любой точке, на любом отрезке, по секциям и по всей длине.

Запуск (Windows, без сборки):
  uv run --python 3.13 --with phonometry --with soundfile --with numpy --with scipy \
      --with pyloudnorm python perceptual.py mix.wav ref1.wav ...

Примеры:
  perceptual.py full_mix.wav ref.wav                     # сводка + дельта к ref
  perceptual.py full_mix.wav --section 95:140           # только припев
  perceptual.py full_mix.wav --at 112.5                 # точка (+-2 с)
  perceptual.py full_mix.wav --grid out.npz             # покадровая матрица
  perceptual.py full_mix.wav --stems DIR                # маскировка по стемам
  perceptual.py full_mix.wav --rough                    # + ECMA-418-2 roughness (медленно)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

try:
    import soundfile as sf
except ImportError:
    sys.exit("нужен soundfile: uv run --with soundfile ...")

from phonometry import psychoacoustics as psy

SR = 48000
HOP = 0.05           # 50 мс — кадр перцептивных метрик
NFFT = 4096
# ISO 266 / IEC 61260 третьоктавные центры, 25 Гц .. 12.5 кГц (28 полос, как в ISO 532-1)
FC = np.array([25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630,
               800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000,
               10000, 12500], float)
BARK_Z = np.arange(240) * 0.1 + 0.05          # центры 240 полос N'(z)
# perceptual regions on the Bark scale (Zwicker: 1 Bark ~ 100 Hz below 500 Hz)
REGIONS = [("sub", 0, 2.0), ("bass", 2.0, 5.0), ("lowmid", 5.0, 9.0),
           ("mid", 9.0, 14.0), ("upmid", 14.0, 18.0), ("pres", 18.0, 21.0),
           ("air", 21.0, 24.0)]


# ---------------------------------------------------------------- loading
def load(path: str, max_dur: float | None = None):
    x, fs = sf.read(path, always_2d=True, dtype="float64")
    if max_dur:
        x = x[: int(max_dur * fs)]
    if fs != SR:
        import scipy.signal as ss
        g = np.gcd(int(fs), SR)
        x = ss.resample_poly(x, SR // g, int(fs) // g, axis=0)
        fs = SR
    return x, fs


def lufs(x: np.ndarray, fs: int) -> float:
    try:
        import pyloudnorm as pyln
        return float(pyln.Meter(fs).integrated_loudness(x))
    except Exception:
        return float("nan")


# ---------------------------------------------------------------- filterbank
def _band_matrix(fs: int, nfft: int):
    """Матрица (28, n_bins) долей мощности в третьоктавных полосах (IEC 61260)."""
    freqs = np.fft.rfftfreq(nfft, 1 / fs)
    edges = FC * 10 ** (-1 / 20), FC * 10 ** (1 / 20)
    M = np.zeros((len(FC), len(freqs)))
    for i, (lo, hi) in enumerate(zip(*edges)):
        m = (freqs >= lo) & (freqs < hi)
        if not m.any() and len(freqs):
            m[np.argmin(np.abs(freqs - FC[i]))] = True
        M[i, m] = 1.0
    return M, freqs


def band_levels(x: np.ndarray, fs: int, hop_s: float = HOP, nfft: int = NFFT,
                calibrated: bool = True):
    """Покадровые уровни 28 третьоктавных полос, dB SPL (Pa).

    x: (n,) или (n,2). Для стерео — энергия суммарная (мощность L+R).
    Возвращает (T,28) уровней и времена.
    """
    if x.ndim == 2:
        x = x  # keep channels, sum power
    n = x.shape[0]
    hop = max(1, int(round(hop_s * fs)))
    win = np.hanning(nfft)
    wcorr = (win.sum() / 2.0) ** 2          # синусная нормировка -> mean square
    M, _ = _band_matrix(fs, nfft)
    nfr = max(1, 1 + (n - nfft) // hop) if n > nfft else 1
    pad = np.zeros((max(0, nfft - n), x.shape[1] if x.ndim == 2 else 1))
    xp = np.concatenate([x, pad], 0) if pad.size else x
    idx = np.arange(nfft)[None, :] + hop * np.arange(nfr)[:, None]
    frames = xp[idx]                          # (T, nfft, ch)
    spec = np.fft.rfft(frames * win[:, None], axis=1)   # (T, bins, ch)
    ms = (np.abs(spec) ** 2).sum(axis=2) * 2.0 / wcorr   # (T,bins) суммарная мощность
    p2 = ms @ M.T                                        # (T,28)
    lv = 10 * np.log10(np.maximum(p2, 1e-30) / (20e-6) ** 2)
    t = (np.arange(nfr) * hop + nfft / 2) / fs
    return lv, t


def calibrate(lv: np.ndarray, target_spl: float = 90.0) -> np.ndarray:
    """Сдвиг уровней так, чтобы суммарный уровень был target_spl dB SPL.

    Смысл: все файлы предварительно приводятся к одной интегральной громкости,
    поэтому сравнение идёт при одинаковой громкости прослушивания — иначе
    компрессия громкости Zwicker'а делает цифры несравнимыми.
    """
    tot = 10 * np.log10(np.sum(10 ** (lv / 10), axis=1, keepdims=True))
    return lv + (target_spl - tot)


# ---------------------------------------------------------------- perception
def zwicker_series(lv: np.ndarray):
    """lv (T,28) dB SPL -> N (T,), phon (T,), specific (T,240), sharpness (T,)."""
    N = np.empty(len(lv)); phon = np.empty(len(lv))
    spec = np.empty((len(lv), 240)); sharp = np.empty(len(lv))
    for i, row in enumerate(lv):
        r = psy.loudness_zwicker_from_spectrum(row)
        N[i] = r.loudness; phon[i] = r.loudness_level
        spec[i] = r.specific
        sharp[i] = psy.sharpness_din_from_specific(r.specific, method="din")
    return N, phon, spec, sharp


def summarize(lv, N, phon, spec, sharp, t, x=None, fs=SR):
    """Сводка по отрезку: перцентили, полосы, стерео."""
    out = {}
    out["dur"] = float(t[-1] - t[0] + HOP) if len(t) else 0.0
    out["N_mean"] = float(np.mean(N)); out["N_p95"] = float(np.percentile(N, 95))
    out["N_p10"] = float(np.percentile(N, 10))
    out["N5"] = float(np.percentile(N, 5)); out["N10"] = float(np.percentile(N, 10))
    out["phon_mean"] = float(np.mean(phon)); out["phon_max"] = float(np.max(phon))
    out["sharp_mean"] = float(np.mean(sharp)); out["sharp_p95"] = float(np.percentile(sharp, 95))
    out["regions"] = {}
    for name, lo, hi in REGIONS:
        m = (BARK_Z >= lo) & (BARK_Z < hi)
        out["regions"][name] = float(np.mean(spec[:, m].sum(axis=1)))
    out["regions_pct"] = {k: 100 * v / max(sum(out["regions"].values()), 1e-9)
                          for k, v in out["regions"].items()}
    if x is not None and x.ndim == 2 and x.shape[1] == 2:
        L, R = x[:, 0], x[:, 1]
        mid, side = (L + R) / 2, (L - R) / 2
        e = lambda a: float(np.mean(a ** 2)) + 1e-12
        corr = float(np.corrcoef(L, R)[0, 1]) if len(L) > 1 else float("nan")
        out["stereo"] = {
            "corr": corr,
            "side_mid_db": float(10 * np.log10(e(side) / e(mid))),
            "width": float(np.sqrt(e(side) / (e(mid) + e(side)))),
            "mono_loss_db": float(10 * np.log10(e(mid) / (e(L) + e(R)) * 2 + 1e-12)),
        }
    if len(N):
        out["crest_db"] = float(10 * np.log10(max(np.max(x ** 2) if x is not None else 1e-12, 1e-12)
                                              / (np.mean(x ** 2) + 1e-12))) if x is not None else float("nan")
    return out


def masking_by_stem(mix_path: str, stem_dir: str, a: float, b: float):
    names = [n[:-4] for n in ("drums.wav", "bass.wav", "vocals.wav", "other.wav")
             if os.path.exists(os.path.join(stem_dir, n))]
    if not names:
        return None
    acc = {}
    lv_mix = None
    xm, fs = load(mix_path); s, e = int(a * fs), int(b * fs)
    lvm, tm = band_levels(xm[s:e], fs)
    tot_mix = 10 * np.log10(np.sum(10 ** (lvm / 10), axis=1))
    offset = 90.0 - float(np.mean(tot_mix))     # единая калибровка для микса и стемов
    lvm = lvm + offset
    Nm, _, specm, _ = zwicker_series(lvm)
    for nm in names:
        x, fs = load(os.path.join(stem_dir, nm + ".wav"))
        s2, e2 = int(a * fs), int(b * fs)
        lv, t = band_levels(x[s2:e2], fs)
        lv = lv + offset                        # та же точка прослушивания, не своя
        N, phon, spec, sharp = zwicker_series(lv)
        acc[nm] = spec.mean(0)
    tot = np.sum([acc[n] for n in names], axis=0) + 1e-9
    mixspec = specm.mean(0)
    rows = []
    for nm in names:
        share = acc[nm] / tot
        # где стем сам по себе громкий, а в сумме почти отсутствует
        idx = np.where((share < 0.10) & (acc[nm] > 0.5))[0]
        idx = idx[np.argsort(-acc[nm][idx])][:6]
        regions = {}
        for name, lo, hi in REGIONS:
            m = (BARK_Z >= lo) & (BARK_Z < hi)
            regions[name] = round(float(100 * acc[nm][m].sum() / max(tot[m].sum(), 1e-9)), 1)
        rows.append({
            "stem": nm,
            "N_share_pct": float(100 * acc[nm].sum() / max(tot.sum(), 1e-9)),
            "regions_pct": regions,
            "buried_hz": [int(round(600 * np.sinh(BARK_Z[i] / 6))) for i in idx],
        })
    return {"mix_specific_sum": float(mixspec.sum()), "stems_specific_sum": float(tot.sum()),
            "ratio": float(tot.sum() / max(mixspec.sum(), 1e-9)), "stems": rows}


# ---------------------------------------------------------------- sections
def auto_sections(lv: np.ndarray, t: np.ndarray, min_len=6.0, n=6):
    """Грубая карта секций по новизне перцептивного профиля (не заменяет sections.py)."""
    if len(lv) < 10:
        return []
    feat = np.diff(lv, axis=0)
    feat = np.vstack([feat[:1], feat])
    feat = np.concatenate([lv[:, :8], feat], axis=1)
    feat = (feat - feat.mean(0)) / (feat.std(0) + 1e-9)
    k = 9
    sm = np.apply_along_axis(lambda r: np.convolve(r, np.ones(k) / k, "same"), 0, feat)
    nov = np.linalg.norm(np.diff(sm, axis=0), axis=1)
    cand = [i for i in range(1, len(nov) - 1) if nov[i] > nov[i - 1] and nov[i] >= nov[i + 1]]
    cand = sorted(cand, key=lambda i: -nov[i])
    picks = []
    for i in cand:
        if all(abs(t[i] - t[j]) > min_len for j in picks):
            picks.append(i)
        if len(picks) >= n:
            break
    bounds = [0] + sorted(picks) + [len(lv) - 1]
    return [(float(t[bounds[i]]), float(t[bounds[i + 1]])) for i in range(len(bounds) - 1)]


# ---------------------------------------------------------------- cli
def fmt(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{nd}f}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--section", help="start:end в секундах")
    ap.add_argument("--at", type=float, help="точка, секунды (окно +-2 с)")
    ap.add_argument("--grid", help="сохранить покадровую матрицу .npz")
    ap.add_argument("--stems", help="каталог со стемами drums/bass/vocals/other.wav")
    ap.add_argument("--sections", action="store_true", help="карта секций по новизне перцептивного профиля")
    ap.add_argument("--rough", action="store_true", help="ECMA-418-2 roughness (медленно, ~4x реального времени)")
    ap.add_argument("--lufs", type=float, default=-14.0, help="нормировка перед анализом; 0 = выкл")
    ap.add_argument("--json", help="куда сохранить сводку")
    ap.add_argument("--max-dur", type=float, default=None)
    a = ap.parse_args(argv)

    reports, grids = [], {}
    for path in a.files:
        t0 = time.time()
        x, fs = load(path, a.max_dur)
        if a.section or a.at:
            s = a.at - 2 if a.at is not None else float(a.section.split(":")[0])
            e = a.at + 2 if a.at is not None else float(a.section.split(":")[1])
            x = x[int(s * fs):int(e * fs)]
        if a.lufs:
            cur = lufs(x, fs)
            if np.isfinite(cur):
                x = x * 10 ** ((a.lufs - cur) / 20)
        lv, t = band_levels(x, fs)
        lv = calibrate(lv)                    # все файлы слушаются на 90 dB SPL
        N, phon, spec, sharp = zwicker_series(lv)
        rep = summarize(lv, N, phon, spec, sharp, t, x, fs)
        rep["file"] = os.path.basename(path)
        rep["lufs"] = lufs(x, fs)
        rep["seconds"] = round(time.time() - t0, 2)
        if a.rough:
            try:
                r = psy.roughness_ecma(x.mean(1), fs)
                rep["roughness_asper"] = float(r.roughness)
                rep["roughness_p95"] = float(np.percentile(r.roughness_vs_time, 95))
                print(f"  roughness ECMA: {rep['roughness_asper']:.2f} asper (p95 {rep['roughness_p95']:.2f})")
            except Exception as ex:
                rep["roughness_error"] = str(ex)[:120]
                print("  roughness: не посчиталась —", rep["roughness_error"])
        reports.append(rep)
        if a.grid:
            grids[os.path.basename(path)] = {"t": t, "lv": lv, "N": N, "phon": phon,
                                             "sharp": sharp, "specific": spec.astype(np.float32)}
        print(f"\n=== {rep['file']}  ({rep['dur']:.1f} с, {rep['seconds']} с счёта)")
        print(f"  LUFS {fmt(rep['lufs'])}   N {fmt(rep['N_mean'])} сон  (p10 {fmt(rep['N_p10'])}, p95 {fmt(rep['N_p95'])})"
              f"   phon {fmt(rep['phon_mean'],1)}   резкость {fmt(rep['sharp_mean'])} acum")
        print("  слышимый баланс: " + "  ".join(f"{k} {v:.0f}%" for k, v in rep["regions_pct"].items()))
        if "stereo" in rep:
            st = rep["stereo"]
            print(f"  стерео: corr {fmt(st['corr'])}  side/mid {fmt(st['side_mid_db'])} dB  ширина {fmt(st['width'])}  потеря в моно {fmt(st['mono_loss_db'])} dB")
        if a.grid:
            continue
        secs = [(0.0, rep["dur"])]
        if a.sections:
            auto = auto_sections(lv, t)
            if auto:
                secs = auto
                print("  секции (новизия):", "  ".join(f"{s:.0f}-{e:.0f}" for s, e in secs))
                for s, e in secs:
                    m = (t >= s) & (t < e)
                    print(f"    {s:6.1f}-{e:6.1f}  N {N[m].mean():5.1f}  phon {phon[m].mean():5.1f}  "
                          f"резк {sharp[m].mean():4.2f}  " + " ".join(
                              f"{k}{100*spec[m][:, (BARK_Z>=lo)&(BARK_Z<hi)].sum(1).mean()/max(spec[m].sum(1).mean(),1e-9):3.0f}"
                              for k, lo, hi in REGIONS))
    if a.grid and grids:
        np.savez_compressed(a.grid, **{f"{k}__{n}": v for k, d in grids.items() for n, v in d.items()})
        print(f"\nматрица сохранена: {a.grid}")

    if len(reports) > 1:
        ref = reports[1]
        print("\n=== дельта к референсу (", ref["file"], ")")
        print(f"{'файл':28} {'LUFS':>7} {'N,%':>7} {'phon':>7} {'резк':>6} " +
              " ".join(f"{k:>7}" for k, _, _ in REGIONS))
        for r in reports:
            dn = 100 * (r["N_mean"] / ref["N_mean"] - 1)
            print(f"{r['file'][:28]:28} {r['lufs']:7.1f} {dn:+7.1f} {r['phon_mean']-ref['phon_mean']:+7.1f} "
                  f"{r['sharp_mean']-ref['sharp_mean']:+6.2f} " +
                  " ".join(f"{r['regions'][k]-ref['regions'][k]:+7.2f}" for k, _, _ in REGIONS))

    if a.stems and a.files:
        s, e = (0.0, reports[0]["dur"])
        if a.section:
            s, e = [float(v) for v in a.section.split(":")]
        m = masking_by_stem(a.files[0], a.stems, s, e)
        if m:
            print(f"\n=== маскировка по стемам ({s:.0f}-{e:.0f} с), удельная громкость N'(z)")
            print(f"Σ N' стемов / N' микса = {m['ratio']:.2f}  (1.0 = стемы складываются в микс)")
            print(f"  {'стем':8} {'доля N%':>8} " + " ".join(f"{k:>7}" for k, _, _ in REGIONS))
            for r in m["stems"]:
                print(f"  {r['stem']:8} {r['N_share_pct']:8.1f} " +
                      " ".join(f"{r['regions_pct'][k]:7.1f}" for k, _, _ in REGIONS))
                if r["buried_hz"]:
                    print(f"           задавлено (Гц): {r['buried_hz']}")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=1)
        print("json:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
