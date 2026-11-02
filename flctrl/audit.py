"""audit.py — проверка «грамотности» микса по всем фронтам против референсов.

Вопрос, на который отвечает: наш трек не хуже референсов ХОТЯ БЫ по каждому
измеримому фронту? Громкость — только один из них.

Фронты: уровень и пик, динамика (crest, PLR, LDR, разброс по секциям),
спектральный профиль (28 полос и семь областей), провалы в спектре, резкость,
стерео (корреляция, ширина, потеря в моно, стабильность по секциям),
клиппинг, постоянная составляющая, шумовой пол, плотность атак, разборчивость
вокала (если дан стем).

Для каждого показателя считается медиана и разброс по корпусу референсов,
и выносится вердикт: ниже коридора / в коридоре / выше.

    python -m flctrl.audit --mine my.wav --refs ref1.wav ref2.wav ...
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

import perceptual as P

REGIONS = P.REGIONS


def _stereo_bands(x: np.ndarray, fs: int) -> dict:
    """Ширина и корреляция ПО ПОЛОСАМ: одна цифра на весь микс врёт.

    Считаем по кросс-спектру: для каждой полосы — доля боковой энергии и
    нормированная корреляция L/R. Так видно, где низ разъехан, а верх узок.
    """
    if x.shape[1] < 2:
        return {"width_bands": [], "corr_bands": []}
    nfft = 4096
    hop = nfft // 2
    win = np.hanning(nfft)
    left, right = x[:, 0], x[:, 1]
    frames = 1 + (len(left) - nfft) // hop if len(left) > nfft else 0
    if frames <= 0:
        return {"width_bands": [], "corr_bands": []}
    idx = np.arange(nfft)[None, :] + hop * np.arange(frames)[:, None]
    L = np.fft.rfft(left[idx] * win, axis=1)
    R = np.fft.rfft(right[idx] * win, axis=1)
    freqs = np.fft.rfftfreq(nfft, 1 / fs)

    # полосы как в perceptual (28 третьоктавных центров)
    centres = P.FC
    width_bands, corr_bands = [], []
    for fc in centres:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        m = (freqs >= lo) & (freqs < hi)
        if not m.any():
            width_bands.append(0.0)
            corr_bands.append(1.0)
            continue
        pl = np.sum(np.abs(L[:, m]) ** 2, axis=1)
        pr = np.sum(np.abs(R[:, m]) ** 2, axis=1)
        xr = np.real(np.sum(L[:, m] * np.conj(R[:, m]), axis=1))
        denom = np.sqrt(np.maximum(pl * pr, 1e-30))
        corr = float(np.mean(xr / denom)) if len(denom) else 1.0
        mid = (pl + pr + 2 * xr) / 4.0
        side = (pl + pr - 2 * xr) / 4.0
        w = float(np.sqrt(np.mean(side) / (np.mean(mid) + 1e-30)))
        width_bands.append(w)
        corr_bands.append(corr)
    return {"width_bands": width_bands, "corr_bands": corr_bands}


def _read(path: str, a: float | None = None, b: float | None = None):
    import soundfile as sf

    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if a is not None and b is not None:
            f.seek(int(a * fs))
            x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    return x, fs


def _onsets(x: np.ndarray, fs: int) -> dict:
    """Плотность атак: сколько раз огибающая заметно растёт за секунду."""
    mono = x.mean(axis=1)
    hop = max(1, int(0.01 * fs))
    frames = len(mono) // hop
    if frames < 4:
        return {"attacks_per_s": 0.0, "attack_strength": 0.0}
    env = np.array([np.sqrt(np.mean(mono[i * hop:(i + 1) * hop] ** 2))
                    for i in range(frames)])
    env = np.maximum(env, 1e-9)
    flux = np.diff(20 * np.log10(env))
    peaks = (flux > 6.0) & (flux > np.roll(flux, 1))
    return {"attacks_per_s": float(peaks.sum() / (len(mono) / fs)),
            "attack_strength": float(np.mean(flux[peaks])) if peaks.any() else 0.0}


def measure(path: str, a: float | None = None, b: float | None = None) -> dict:
    """Полный набор измеримых фронтов для одного файла."""
    x, fs = _read(path, a, b)
    mono = x.mean(axis=1)

    lv, t = P.band_levels(x, fs)
    lv_cal = P.calibrate(lv)
    N, phon, spec, sharp = P.zwicker_series(lv_cal)

    regions = {}
    for name, lo, hi in REGIONS:
        m = (P.BARK_Z >= lo) & (P.BARK_Z < hi)
        regions[name] = float(spec[:, m].sum(axis=1).mean())
    tot = sum(regions.values()) or 1e-9
    regions_pct = {k: 100.0 * v / tot for k, v in regions.items()}

    bands_db = np.median(lv, axis=0)
    # провалы: полоса, которая ниже сглаженного профиля больше чем на 12 дБ
    smooth = np.convolve(bands_db, np.ones(5) / 5, mode="same")
    holes = [int(i) for i in np.where(bands_db < smooth - 12.0)[0]]

    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    peak = float(np.max(np.abs(mono)))
    crest = 20 * np.log10((peak + 1e-12) / rms)
    lufs = float(20 * np.log10(rms + 1e-12) - 0.691)  # приближение RMS->LUFS

    # LDR: разница между громкими и тихими кадрами
    frame = max(1, int(0.4 * fs))
    nf = len(mono) // frame
    if nf > 2:
        rms_frames = np.array([np.sqrt(np.mean(mono[i * frame:(i + 1) * frame] ** 2))
                               for i in range(nf)])
        rms_frames = np.maximum(rms_frames, 1e-9)
        ldr = float(20 * np.log10(np.percentile(rms_frames, 95) /
                                  np.percentile(rms_frames, 10)))
        noise_floor = float(20 * np.log10(np.percentile(rms_frames, 5) + 1e-12))
    else:
        ldr, noise_floor = 0.0, -120.0

    stereo = {"corr": 1.0, "width": 0.0, "mono_loss_db": 0.0, "corr_spread": 0.0}
    if x.shape[1] >= 2:
        left, right = x[:, 0], x[:, 1]
        if np.std(left) > 1e-9 and np.std(right) > 1e-9:
            stereo["corr"] = float(np.corrcoef(left, right)[0, 1])
        mid = (left + right) / 2
        side = (left - right) / 2
        stereo["width"] = float(np.sqrt(np.mean(side ** 2)) /
                                (np.sqrt(np.mean(mid ** 2)) + 1e-12))
        mono_rms = float(np.sqrt(np.mean(mid ** 2)))
        per_ch = float(np.sqrt(np.mean(left ** 2) + np.mean(right ** 2)) / np.sqrt(2))
        stereo["mono_loss_db"] = float(20 * np.log10((mono_rms + 1e-12) / (per_ch + 1e-12)))
        # стабильность по секциям
        n = len(left)
        sec = max(1, n // 4)
        cors = []
        for i in range(4):
            sl = slice(i * sec, min(n, (i + 1) * sec))
            if np.std(left[sl]) > 1e-9 and np.std(right[sl]) > 1e-9:
                cors.append(float(np.corrcoef(left[sl], right[sl])[0, 1]))
        if len(cors) > 1:
            stereo["corr_spread"] = float(max(cors) - min(cors))

    clip = int(np.sum(np.abs(x) >= 0.999))
    dc = float(np.mean(mono))
    ons = _onsets(x, fs)

    return {
        "lufs": lufs, "peak_db": 20 * np.log10(peak + 1e-12), "crest": float(crest),
        "ldr": ldr, "n_mean": float(np.mean(N)), "phon_mean": float(np.mean(phon)),
        "sharp": float(np.mean(sharp)),
        "regions_pct": regions_pct, "bands_db": [float(v) for v in bands_db],
        # форма спектра без общего уровня: иначе тихий микс «ниже коридора» во всех полосах
        "bands_rel": [float(v - np.mean(bands_db)) for v in bands_db],
        "holes": holes, "clip_samples": clip, "dc": dc,
        "noise_floor_db": noise_floor, **ons, **stereo,
        **_stereo_bands(x, fs),
    }


# Направление «хуже»: low — меньше значит хуже, high — больше значит хуже,
# target — коридор в обе стороны (это про характер, а не про качество).
AXES = {
    "crest": ("крест (динамика)", "target"),
    "ldr": ("LDR (контраст)", "target"),
    "sharp": ("резкость", "target"),
    "corr": ("корреляция", "target"),
    "width": ("ширина", "target"),
    "mono_loss_db": ("потеря в моно", "low"),
    "noise_floor_db": ("шумовой пол", "high"),
    "attacks_per_s": ("плотность атак", "low"),
    "clip_samples": ("клиппинг", "high"),
    "holes": ("провалы в спектре", "high"),
}

# Эти фронты зависят от мастеринга и сравниваются отдельно, не как «качество».
MASTERING = {"lufs": "уровень", "peak_db": "пик"}


def _stats(values: list[float]) -> dict:
    v = [x for x in values if x == x]
    if not v:
        return {"median": None, "q25": None, "q75": None}
    return {"median": float(np.median(v)),
            "q25": float(np.percentile(v, 25)),
            "q75": float(np.percentile(v, 75))}


def audit(mine: dict, refs: list[dict]) -> dict:
    """Сравнить наш замер с коридором референсов по каждому фронту."""
    rows = []
    for key, (label, worse) in AXES.items():
        vals = [r.get(key) for r in refs if isinstance(r.get(key), (int, float))]
        if key == "holes":
            vals = [len(r.get("holes") or []) for r in refs]
            mine_v = len(mine.get("holes") or [])
        else:
            mine_v = mine.get(key)
        st = _stats(vals)
        if st["median"] is None or mine_v is None:
            continue
        lo, hi = st["q25"], st["q75"]
        if lo <= mine_v <= hi:
            verdict, flag = "в коридоре", "ok"
        elif mine_v < lo:
            verdict = "ниже"
            flag = "хуже" if worse == "low" else ("лучше" if worse == "high" else "нейтрально")
        else:
            verdict = "выше"
            flag = "хуже" if worse == "high" else ("лучше" if worse == "low" else "нейтрально")
        rows.append({"key": key, "label": label, "mine": float(mine_v),
                     "ref_median": st["median"], "q25": lo, "q75": hi,
                     "verdict": verdict, "flag": flag})

    # мастеринговая группа: уровень и пик — отдельно, это не «качество»
    mastering = []
    for key, label in MASTERING.items():
        vals = [r.get(key) for r in refs if isinstance(r.get(key), (int, float))]
        st = _stats(vals)
        if st["median"] is None or mine.get(key) is None:
            continue
        mastering.append({"key": key, "label": label, "mine": float(mine[key]),
                          "ref_median": st["median"]})

    # спектр: форма без общего уровня
    bands_detail, out_of = [], 0
    ref_bands = np.array([r["bands_rel"] for r in refs if r.get("bands_rel")])
    if ref_bands.size and mine.get("bands_rel"):
        b_lo = np.percentile(ref_bands, 25, axis=0)
        b_hi = np.percentile(ref_bands, 75, axis=0)
        mine_b = np.array(mine["bands_rel"])
        for i, (v, lo, hi) in enumerate(zip(mine_b, b_lo, b_hi)):
            if abs(v) < 1e-9 and abs(hi) < 1e-9:
                continue
            if v < lo - 1.5 or v > hi + 1.5:
                out_of += 1
                bands_detail.append({"band": i, "mine": float(v),
                                     "lo": float(lo), "hi": float(hi)})
    return {"rows": rows, "mastering": mastering,
            "bands_out_of_corridor": out_of,
            "bands_detail": bands_detail[:10],
            "stereo_bands": _compare_series(mine.get("width_bands"), refs, "width_bands"),
            "corr_bands": _compare_series(mine.get("corr_bands"), refs, "corr_bands"),
            "bad": [r for r in rows if r["flag"] == "хуже"]}


def _compare_series(mine_v, refs: list[dict], key: str) -> dict:
    """Сравнить по-полосную кривую с коридором референсов."""
    series = [r.get(key) for r in refs if r.get(key)]
    if not mine_v or not series:
        return {"bands_out": [], "count": 0, "worst": []}
    arr = np.array(series)
    lo = np.percentile(arr, 25, axis=0)
    hi = np.percentile(arr, 75, axis=0)
    out = []
    for i, (v, l, h) in enumerate(zip(mine_v, lo, hi)):
        if v < l - 0.05 or v > h + 0.05:
            out.append({"band": i, "mine": float(v), "lo": float(l), "hi": float(h)})
    worst = sorted(out, key=lambda b: abs(b["mine"] - (b["lo"] + b["hi"]) / 2), reverse=True)[:5]
    return {"bands_out": out, "count": len(out), "worst": worst}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Аудит микса против референсов")
    ap.add_argument("--mine", required=True)
    ap.add_argument("--refs", nargs="+", required=True)
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    mine = measure(a.mine, a.a, a.b)
    refs = [measure(r, a.a, a.b) for r in a.refs]
    res = audit(mine, refs)

    print("%-22s %10s %10s %-14s %-10s %s" % ("фронт", "наш", "медиана", "коридор", "вердикт", ""))
    for r in res["rows"]:
        print("%-22s %10.2f %10.2f %-14s %-10s %s" % (
            r["label"], r["mine"], r["ref_median"],
            "%.2f…%.2f" % (r["q25"], r["q75"]), r["verdict"], r["flag"]))
    if res["mastering"]:
        print("\nмастеринг (не качество, а ступень обработки):")
        for m in res["mastering"]:
            print("  %-10s наш %.2f, у референсов %.2f" % (
                m["label"], m["mine"], m["ref_median"]))
    print("\nполос вне коридора: %d" % res["bands_out_of_corridor"])
    for b in res["bands_detail"]:
        print("  полоса %2d: наш %.1f, коридор %.1f…%.1f дБ" % (
            b["band"], b["mine"], b["lo"], b["hi"]))
    for key, label in (("stereo_bands", "ширина по полосам"),
                       ("corr_bands", "корреляция по полосам")):
        info = res.get(key) or {}
        if info.get("count"):
            print("\n%s: вне коридора %d полос" % (label, info["count"]))
            for b in info["worst"][:3]:
                print("  полоса %2d: наш %.2f, коридор %.2f…%.2f" % (
                    b["band"], b["mine"], b["lo"], b["hi"]))
        else:
            print("\n%s: в коридоре" % label)
    print("\nхуже референсов по фронтам: %d" % len(res["bad"]))
    for r in res["bad"]:
        print("  %s: %.2f против %.2f" % (r["label"], r["mine"], r["ref_median"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"mine": mine, "refs": refs, "audit": res}, f,
                      ensure_ascii=False, indent=1)
        print("записано:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
