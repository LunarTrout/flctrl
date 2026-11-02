"""listen_layer.py -- слой «слушать» для библиотеки доступа к FL.

Опирается на perceptual.py (phonometry: ISO 532-1 Zwicker, DIN 45692, N'(z)).
Добавляет то, чего в нём нет:
  - метрики De Man 2015, которые коррелируют с предпочтением слушателей:
    side/mid (-0.32), LDR микродинамика (+0.26), энергия октавы 4 кГц (-0.21);
  - STOI: «насколько вокал забит в миксе» (стем против микса);
  - коридор из НЕСКОЛЬКИХ референсов вместо одной цифры;
  - одну точку входа measure() -> dict для программного использования.

Запуск (нужен Python 3.13 из-за phonometry):
  uv run --no-project --python 3.13 --with phonometry --with soundfile \
      --with numpy --with scipy --with pyloudnorm --with pystoi \
      python listen_layer.py measure mix.wav --section 95:140
  ... measure mix.wav --compare ref1.mp3 ref2.mp3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import perceptual as P  # noqa: E402

SR = P.SR
HOP = P.HOP
NFFT = P.NFFT


# ---------------------------------------------------------------- De Man 2015

def side_mid_db(x: np.ndarray) -> float:
    """Отношение side/mid в дБ. У De Man больше side/mid -> меньше нравится."""
    if x.ndim == 1 or x.shape[1] < 2:
        return 0.0
    mid = (x[:, 0] + x[:, 1]) / 2.0
    side = (x[:, 0] - x[:, 1]) / 2.0
    em = float(np.mean(mid ** 2)) + 1e-20
    es = float(np.mean(side ** 2)) + 1e-20
    return 10.0 * np.log10(es / em)


def ldr_db(x: np.ndarray, fs: int = SR, win_s: float = 3.0) -> float:
    """LDR (low dynamic range) — микродинамика: RMS коротких окон против длинного.

    Считается через кумулятивную сумму, а не свёрткой: свёртка с окном в 144 тыс.
    отсчётов даёт квадратичную сложность и вешает расчёт на минуты.
    """
    mono = x.mean(axis=1) if x.ndim > 1 else x
    n = int(win_s * fs)
    if len(mono) < max(n, 2):
        return 0.0
    p = mono.astype(np.float64) ** 2
    c = np.concatenate([[0.0], np.cumsum(p)])
    win = c[n:] - c[:-n]
    if win.size == 0:
        return 0.0
    short = np.sqrt(np.maximum(win / n, 1e-24))
    long_rms = float(np.sqrt(np.mean(p))) + 1e-20
    return 20.0 * np.log10(np.std(short) / long_rms)


def octave_share(x: np.ndarray, fs: int = SR, center: float = 4000.0,
                 width_ratio: float = 1.414) -> float:
    """Доля энергии в октаве вокруг center, дБ относительно полной энергии."""
    mono = x.mean(axis=1) if x.ndim > 1 else x
    spec = np.fft.rfft(mono * np.hanning(len(mono)))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / fs)
    p = np.abs(spec) ** 2
    lo, hi = center / width_ratio, center * width_ratio
    band = p[(freqs >= lo) & (freqs <= hi)].sum()
    total = p.sum() + 1e-20
    return 10.0 * np.log10((band + 1e-20) / total)


# ---------------------------------------------------------------------- STOI

def stoi_score(mix: np.ndarray, stem: np.ndarray, fs: int = SR) -> float | None:
    """STOI: стем как «чистый», микс как «испорченный». Ниже 1 — стем забит."""
    if mix.ndim > 1:
        mix = mix.mean(axis=1)
    if stem.ndim > 1:
        stem = stem.mean(axis=1)
    n = min(len(mix), len(stem))
    if n < fs:
        return None
    try:
        from pystoi import stoi
    except Exception:
        return None
    try:
        return float(stoi(stem[:n], mix[:n], fs, extended=False))
    except Exception:
        return None


def track_offset(path: str, target_spl: float = 90.0) -> float:
    """Усиление прослушивания, одинаковое для всех отрезков одного трека.

    Считается по ВСЕМУ файлу и в тех же единицах, что и полосы (dB SPL):
    иначе тишина «подтягивается» до нормальной громкости, а абсолютные
    значения уезжают на десятки дБ. Кэш — в памяти и на диске.
    """
    key = os.path.abspath(path)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    cached = _OFFSETS.get(key)
    if cached and cached[0] == mt:
        return cached[1]

    disk = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_cache", "offset")
    os.makedirs(disk, exist_ok=True)
    tag = hashlib.sha1(("%s|%s" % (key, mt)).encode()).hexdigest()[:16]
    fp = os.path.join(disk, tag + ".json")
    if os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                off = float(json.load(f)["offset"])
            _OFFSETS[key] = (mt, off)
            return off
        except Exception:
            pass

    x, fs = load_section(path, None)
    lv, _t = P.band_levels(x, fs)
    total = 10.0 * np.log10(np.maximum(np.sum(10 ** (lv / 10.0), axis=1), 1e-30))
    # энергетическое среднее, а не среднее кадров: иначе тишина тянет уровень вниз
    energy_mean = 10.0 * np.log10(max(float(np.mean(10 ** (total / 10.0))), 1e-30))
    off = float(target_spl - energy_mean)
    _OFFSETS[key] = (mt, off)
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"offset": off, "file": key}, f)
    except Exception:
        pass
    return off


_OFFSETS: dict = {}


# ----------------------------------------------------------------- measure

def _zw_chunk(lv):
    """Покадровый расчёт Zwicker для пачки кадров (работает в отдельном процессе)."""
    from phonometry import psychoacoustics as psy
    n = len(lv)
    N = np.empty(n)
    phon = np.empty(n)
    spec = np.empty((n, 240))
    sharp = np.empty(n)
    for i, row in enumerate(lv):
        r = psy.loudness_zwicker_from_spectrum(row)
        N[i] = r.loudness
        phon[i] = r.loudness_level
        spec[i] = r.specific
        sharp[i] = psy.sharpness_din_from_specific(r.specific, method="din")
    return N, phon, spec, sharp


def zwicker_fast(lv):
    """То же, что P.zwicker_series, но при желании в несколько процессов.

    По умолчанию считается в одном процессе: импорт phonometry в каждом
    дочернем процессе на Windows дороже самой работы на коротких отрезках.
    Включается переменной FL_LISTEN_PARALLEL=1 для длинных файлов.
    """
    n = len(lv)
    if os.environ.get("FL_LISTEN_PARALLEL") != "1" or n < 2000:
        return P.zwicker_series(lv)
    workers = min(os.cpu_count() or 2, 8)
    try:
        from concurrent.futures import ProcessPoolExecutor
        idx = np.array_split(np.arange(n), workers)
        chunks = [lv[i] for i in idx if len(i)]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(_zw_chunk, chunks))
        N = np.concatenate([p[0] for p in parts])
        phon = np.concatenate([p[1] for p in parts])
        spec = np.concatenate([p[2] for p in parts])
        sharp = np.concatenate([p[3] for p in parts])
        return N, phon, spec, sharp
    except Exception:
        return P.zwicker_series(lv)


def load_section(path: str, sec: tuple[float, float] | None = None):
    """Читает только нужный отрезок файла (seek/read), а не весь файл."""
    import soundfile as sf
    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if sec:
            start = max(0, int(sec[0] * fs))
            frames = max(1, int((sec[1] - sec[0]) * fs))
            f.seek(start)
            x = f.read(frames, dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    if fs != SR:
        import scipy.signal as ss
        g = np.gcd(int(fs), int(SR))
        x = ss.resample_poly(x, SR // g, int(fs) // g, axis=0)
        fs = SR
    return x, fs


def curve(path: str, section=None) -> dict:
    """Непрерывные кривые по всей длине: время, N(t), фоны, резкость, N′(z).

    Возвращает массивы, а не сводку — чтобы «видеть» сигнал, а не догадываться.
    """
    x, fs = load_section(path, section)
    lv, t = P.band_levels(x, fs)
    lv = lv + track_offset(path)
    N, phon, spec, sharp = zwicker_fast(lv)
    level = 10.0 * np.log10(np.maximum(np.sum(10 ** (lv / 10), axis=1), 1e-30))
    return {"t": t, "N": N, "phon": phon, "sharp": sharp,
            "spec": spec, "lv": lv, "level_db": level, "fs": fs,
            "offset_db": track_offset(path)}


def psycho(path: str, section=None, which=("roughness", "tonality", "fluctuation")) -> dict:
    """Модели ECMA-418-2: roughness, tonality, fluctuation.

    tonality и fluctuation отдают ещё и разбивку по полосам Барка — это то,
    чего не хватало для «каши» и «грязи» по полосам.
    """
    from phonometry import psychoacoustics as psy
    x, fs = load_section(path, section)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    mono = np.ascontiguousarray(mono.astype(np.float64))
    out: dict = {"fs": fs, "duration_s": round(len(mono) / fs, 2)}
    if "roughness" in which:
        try:
            r = psy.roughness_ecma(mono, float(fs))
            val = None
            for attr in ("roughness", "value", "asper", "total"):
                v = getattr(r, attr, None)
                if isinstance(v, (int, float)):
                    val = float(v)
                    break
            out["roughness"] = val
            spec = getattr(r, "specific_roughness", None)
            if spec is not None:
                out["roughness_bands"] = np.asarray(spec, dtype=float)
                out["roughness_peak_band"] = int(np.nanargmax(out["roughness_bands"]))
            over_time = getattr(r, "roughness_vs_time", None)
            if over_time is not None:
                out["roughness_vs_time"] = np.asarray(over_time, dtype=float)
        except Exception as e:
            out["roughness_err"] = "%s: %s" % (type(e).__name__, e)
    if "tonality" in which:
        try:
            t = psy.tonality_ecma(mono, float(fs))
            out["tonality"] = float(getattr(t, "tonality", float("nan")))
            spec = getattr(t, "specific_tonality", None)
            if spec is not None:
                out["tonality_bands"] = np.asarray(spec, dtype=float)
                out["tonality_peak_band"] = int(np.nanargmax(out["tonality_bands"]))
        except Exception as e:
            out["tonality_err"] = "%s: %s" % (type(e).__name__, e)
    if "fluctuation" in which:
        try:
            f = psy.fluctuation_strength_ecma(mono, float(fs))
            out["fluctuation"] = float(getattr(f, "fluctuation_strength", float("nan")))
            spec = getattr(f, "specific", None)
            if spec is not None:
                out["fluctuation_bands"] = np.asarray(spec, dtype=float)
        except Exception as e:
            out["fluctuation_err"] = "%s: %s" % (type(e).__name__, e)
    return out


def masking(stems: dict, section=None, mix_path: str | None = None) -> dict:
    """Кросс-адаптивная маскировка по полосам Барка.

    stems: {"имя": путь}. Считает N′(z) каждого стема, доли в каждой полосе
    и полосы, где стем задавлен остальными.

    Калибровка берётся по миксу (mix_path), чтобы доли были осмысленными.
    """
    off = track_offset(mix_path) if mix_path else 0.0
    specs = {}
    for name, path in stems.items():
        x, fs = load_section(path, section)
        lv, _t = P.band_levels(x, fs)
        lv = lv + off
        _N, _ph, spec, _sh = zwicker_fast(lv)
        specs[name] = np.median(spec, axis=0)

    total = np.sum([s for s in specs.values()], axis=0)
    total = np.maximum(total, 1e-12)

    out: dict = {"bands": int(P.BARK_Z.shape[0]), "stems": {}}
    for name, spec in specs.items():
        share = spec / total
        others = total - spec
        # задавлен там, где остальные громче стема на 6 дБ и более
        buried = others > spec * 4.0
        out["stems"][name] = {
            "share_mean": float(np.mean(share)),
            "share_max": float(np.max(share)),
            "busiest_band": int(np.argmax(spec)),
            "buried_ratio": float(np.mean(buried)),
            "buried_bands": [int(i) for i in np.where(buried)[0][:40]],
        }
    return out


def sections(path: str, min_len: float = 8.0) -> list[tuple[float, float, float]]:
    """Секции файла с их средней громкостью: [(начало, конец, N), ...]."""
    x, fs = load_section(path, None)
    lv, t = P.band_levels(x, fs)
    lv = lv + track_offset(path)
    N, _ph, _sp, _sh = zwicker_fast(lv)
    hop = float(np.median(np.diff(t))) if len(t) > 2 else 0.05
    out = []
    for (a, b) in P.auto_sections(lv, t, min_len=min_len, n=6):
        i0 = max(0, int(a / hop))
        i1 = max(i0 + 1, int(b / hop))
        seg = N[i0:min(i1, len(N))]
        if len(seg):
            out.append((float(a), float(b), float(np.mean(seg))))
    return out


def match_sections(path_a: str, path_b: str, min_len: float = 8.0,
                   max_diff: float = 10.0) -> list[tuple]:
    """Сопоставить секции двух файлов по близости громкости.

    Сравнивать плотное с плотным, а не по номеру секции: тихая вставка
    получает свою пару, а не подставляется под плотный припев.
    Пары с разницей больше max_diff сон не создаются — честнее сказать
    «нет подходящей секции», чем связать тишину с припевом.
    Возвращает [(секция_a, секция_b, |ΔN|), ...] без повторов.
    """
    sa = sections(path_a, min_len)
    sb = sections(path_b, min_len)
    used = set()
    pairs = []
    for a in sorted(sa, key=lambda s: -s[2]):
        best = None
        for j, b in enumerate(sb):
            if j in used:
                continue
            d = abs(a[2] - b[2])
            if d > max_diff:
                continue
            if best is None or d < best[0]:
                best = (d, j, b)
        if best:
            used.add(best[1])
            pairs.append((a, best[2], best[0]))
    return pairs


def unmask(stems: dict, masker: str, masked: str, mix_path: str | None = None,
           section=None, margin_db: float = 6.0, max_cut_db: float = 9.0,
           max_moves: int = 5) -> dict:
    """Из маскировки в действие: сколько срезать у masker, чтобы освободить masked.

    В каждой полосе Барка: если masker громче masked более чем на margin_db,
    предлагается срез на избыток (не больше max_cut_db). Соседние полосы
    с близким срезом объединяются в один ход эквалайзера.

    Возвращает {'moves': [{'hz':.., 'bark':.., 'cut_db':.., 'bands':..}], ...}.
    """
    off = track_offset(mix_path) if mix_path else 0.0

    def spec_of(path):
        x, fs = load_section(path, section)
        lv, _t = P.band_levels(x, fs)
        lv = lv + off
        _N, _ph, sp, _sh = zwicker_fast(lv)
        return np.median(sp, axis=0)

    a = spec_of(stems[masker])
    b = spec_of(stems[masked])
    excess_db = 10.0 * np.log10(np.maximum(a, 1e-12) / np.maximum(b, 1e-12))
    cut = np.clip((excess_db - margin_db) * 0.5, 0.0, max_cut_db)

    moves = []
    i = 0
    while i < len(cut):
        if cut[i] < 1.0:
            i += 1
            continue
        j = i
        while j + 1 < len(cut) and cut[j + 1] >= 1.0:
            j += 1
        seg = cut[i:j + 1]
        weight = seg / seg.sum()
        centre = float(np.sum(P.BARK_Z[i:j + 1] * weight))
        moves.append({
            "bark": round(centre, 1),
            "hz": round(float(600.0 * np.sinh(centre / 6.0)), 0),
            "cut_db": round(float(seg.max()), 1),
            "bands": int(j - i + 1),
        })
        i = j + 1

    moves.sort(key=lambda m: -m["cut_db"])
    moves = moves[:max_moves]
    return {"masker": masker, "masked": masked, "moves": moves,
            "note": "срез рассчитан по превышению %0.0f дБ, половина избытка, максимум %0.0f дБ"
                    % (margin_db, max_cut_db)}


def measure_array(x, fs: int, offset_db: float = 0.0) -> dict:
    """Замер прямо по массиву (без файлов) — для песочницы.

    offset_db — то же усиление прослушивания, что и track_offset для файла;
    передавай одно и то же значение при сравнении «до/после».
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    lv, t = P.band_levels(x, fs)
    lv = lv + offset_db
    N, phon, spec, sharp = zwicker_fast(lv)
    res = P.summarize(lv, N, phon, spec, sharp, t, x=x, fs=fs)
    active = float(np.mean(N >= 1.0)) if len(N) else 0.0
    res.update({
        "duration_s": round(len(x) / fs, 2),
        "active_ratio": round(active, 3),
        "silent": bool(active < 0.6),
        "lufs": round(P.lufs(x, fs), 2),
        "ldr_db": round(ldr_db(x, fs), 2),
        "octave_4k_db": round(octave_share(x, fs), 2),
    })
    return res


def measure(path: str, section: tuple[float, float] | None = None,
            max_dur: float | None = None, stem_path: str | None = None) -> dict:
    """Полный замер одного файла: перцепция + De Man + LUFS."""
    x, fs = load_section(path, section)
    if max_dur:
        x = x[: int(max_dur * fs)]
    lv, t = P.band_levels(x, fs)
    lv = lv + track_offset(path)
    N, phon, spec, sharp = zwicker_fast(lv)
    res = P.summarize(lv, N, phon, spec, sharp, t, x=x, fs=fs)
    # сколько кадров реально содержат звук: ниже 1 соны — тишина/хвост
    active = float(np.mean(N >= 1.0)) if len(N) else 0.0
    res.update({
        "file": os.path.basename(path),
        "duration_s": round(len(x) / fs, 2),
        "active_ratio": round(active, 3),
        "silent": bool(active < 0.6),
        "lufs": round(P.lufs(x, fs), 2),
        "ldr_db": round(ldr_db(x, fs), 2),
        "octave_4k_db": round(octave_share(x, fs), 2),
    })
    if stem_path:
        try:
            sx, _sfs = load_section(stem_path, section)
            v = stoi_score(x, sx, fs)
            res["stoi"] = round(v, 3) if v is not None else None
        except Exception as e:
            res["stoi_err"] = "%s: %s" % (type(e).__name__, e)
    return res


# ---------------------------------------------------------------- сравнение

def corridor(mine: dict, refs: list[dict]) -> dict:
    """Коридор из референсов: где наш замер относительно их разброса."""
    out = {}
    keys = [k for k in mine if isinstance(mine[k], (int, float))]
    for k in keys:
        vals = [r[k] for r in refs if isinstance(r.get(k), (int, float))]
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        med = float(np.median(vals))
        span = max(hi - lo, 1e-9)
        pos = (mine[k] - lo) / span
        if mine[k] < lo:
            verdict = "ниже коридора"
        elif mine[k] > hi:
            verdict = "выше коридора"
        else:
            verdict = "в коридоре"
        out[k] = {
            "наш": round(float(mine[k]), 3),
            "коридор": [round(lo, 3), round(hi, 3)],
            "медиана_рефов": round(med, 3),
            "позиция": round(pos, 2),
            "вывод": verdict,
        }
    return out


def parse_section(s: str | None):
    if not s:
        return None
    a, b = s.split(":")
    return float(a), float(b)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["measure", "compare", "stems"])
    ap.add_argument("target")
    ap.add_argument("refs", nargs="*")
    ap.add_argument("--section")
    ap.add_argument("--at", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    sec = parse_section(a.section)
    if a.at is not None and not sec:
        sec = (max(0.0, a.at - 2.0), a.at + 2.0)

    if a.cmd == "stems":
        print(json.dumps(P.masking_by_stem(a.target, a.refs[0], *(sec or (0, 60))),
                         ensure_ascii=False, indent=2, default=str))
        return 0

    mine = measure(a.target, sec)
    print(json.dumps({"замер": mine}, ensure_ascii=False, indent=2, default=str))

    if a.cmd == "compare" and a.refs:
        refs = [measure(r, sec) for r in a.refs]
        print(json.dumps({"коридор": corridor(mine, refs)},
                         ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
