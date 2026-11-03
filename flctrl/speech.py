"""speech.py — разборчивость вокала в миксе по ANSI S3.5-1997 (SII).

Зачем это отдельно от громкости: инструмент может быть громким и при этом
нечитаемым — его маскирует остальной микс. SII считает долю речи, которая
реально доходит до слушателя: по каждой третьоктавной полосе сравнивается
уровень вокала с уровнем маскирующего остатка, и полосы складываются с
важностями из стандарта.

Процедура — 1/3-октавная, 18 полос 160…8000 Гц, без слуховых потерь и без
вставных усилений (музыкальный микс, а не слуховой аппарат). Уровни берутся
в дБ SPL относительно 20 мкПа: сигнал 1.0 RMS трактуется как 94 дБ SPL.

    python -m flctrl.speech --vocal vox.wav --masker rest.wav
"""
from __future__ import annotations

import argparse
import json

import numpy as np

# 1/3-октавные центры 160…8000 Гц (SII, таблица 3)
FREQS = np.array([160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
                  2000, 2500, 3150, 4000, 5000, 6300, 8000], dtype=float)

# Важность полос для «стандартной речи» (ANSI S3.5-1997, таблица 3, колонка 1)
IMPORTANCE = np.array([
    0.0083, 0.0095, 0.0150, 0.0289, 0.0440, 0.0578, 0.0653, 0.0711, 0.0818,
    0.0844, 0.0882, 0.0898, 0.0868, 0.0844, 0.0771, 0.0527, 0.0364, 0.0185])

# Спектр стандартной речи при нормальном усилии, дБ (таблица 3)
SPEECH_LEVEL = np.array([
    32.41, 34.48, 34.75, 33.98, 34.59, 34.27, 32.06, 28.30, 25.01, 23.00,
    20.15, 17.32, 13.18, 11.55, 9.33, 5.31, 2.59, 1.13])

# Внутренний шум слуха, дБ (таблица 3)
INTERNAL_NOISE = np.array([
    0.6, -1.7, -3.9, -6.1, -8.2, -9.7, -10.8, -11.9, -12.5, -13.5, -15.4,
    -17.7, -21.2, -24.2, -25.9, -23.6, -15.8, -7.1])


def bands18(x: np.ndarray, fs: int, hop_s: float = 0.20) -> np.ndarray:
    """Уровни 18 полос SII (дБ), усреднённые по времени по мощности."""
    import partial

    if x.ndim == 2:
        x = x.mean(axis=1)
    full, _t = partial.bands29(x, fs, hop_s)      # 29 полос ISO, дБ
    start = int(np.argmin(np.abs(partial.TC - 160)))
    part = full[:, start:start + len(FREQS)]
    return 10 * np.log10(np.maximum((10 ** (part / 10)).mean(axis=0), 1e-30))


def sii_from_levels(e: np.ndarray, n: np.ndarray) -> dict:
    """SII по уровням речи (E) и маскирующего шума (N), дБ SPL, 18 полос."""
    e = np.asarray(e, dtype=float)
    n = np.asarray(n, dtype=float)
    if e.shape != (18,) or n.shape != (18,):
        raise ValueError("нужно ровно 18 полос, получено %s и %s" % (e.shape, n.shape))

    # Самомаскировка речи и итоговый маскирующий спектр (4.3.2)
    v = e - 24.0
    b = np.maximum(v, n)

    # Наклон верхних склонов маскировки (4.3.2.3, ур. 7)
    c = 0.6 * (b + 10 * np.log10(FREQS) - 6.353) - 80.0

    # Эквивалентный маскирующий спектр с учётом распространения (4.3.2.5, ур. 9)
    z = np.empty(18)
    z[0] = n[0]
    for i in range(1, 18):
        spread = b[:i] + 3.32 * c[:i] * np.log10(0.89 * FREQS[i] / FREQS[:i])
        z[i] = 10 * np.log10(10 ** (0.1 * n[i]) + np.sum(10 ** (0.1 * spread)))

    # Внутренний шум слуха (4.4)
    d = np.maximum(z, INTERNAL_NOISE)

    # Ограничение уровня речи и слышимость полосы (4.7)
    l = np.clip(1 - (e - SPEECH_LEVEL - 10) / 160.0, 0, 1)
    k = np.clip((e - d + 15) / 30.0, 0, 1)
    a = l * k

    value = float(np.sum(IMPORTANCE * a))
    return {
        "sii": round(value, 4),
        "audibility": [round(float(x), 4) for x in a],
        "bands": [{"f": int(f), "speech": round(float(ee), 1),
                   "masker": round(float(nn), 1), "masked": round(float(dd), 1),
                   "audibility": round(float(aa), 3),
                   "importance": round(float(ii), 4)}
                  for f, ee, nn, dd, aa, ii in zip(FREQS, e, n, d, a, IMPORTANCE)],
        "worst_band": int(FREQS[int(np.argmin(np.where(IMPORTANCE > 0, a, 9)))]),
    }


def _read(path: str, target_fs: int = 16000) -> tuple[np.ndarray, int]:
    """Читает файл и приводит к единой частоте: SII всё равно смотрит до 8 кГц,
    а разная частота у стемов и мастера — обычное дело."""
    import soundfile as sf

    x, fs = sf.read(path, dtype="float64", always_2d=False)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if fs != target_fs:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(fs), int(target_fs))
        x = resample_poly(x, target_fs // g, fs // g)
        fs = target_fs
    return x, fs


def calibrate_gain(vocal_power: np.ndarray, masker_power: np.ndarray,
                   master_power: np.ndarray) -> float:
    """Множитель для маскера, при котором вокал+маскер по мощности = мастер.

    Стемы часто отрендерены до фейдеров: их сумма по громкости не равна
    мастеру, и SII без выравнивания показывал бы разницу уровней файлов, а не
    баланс микса. Один скаляр на все полосы — намеренно: он не подгоняет
    форму, только уровень.
    """
    v = float(np.sum(vocal_power))
    m = float(np.sum(masker_power))
    t = float(np.sum(master_power))
    if m <= 0 or t <= v:
        return 1.0
    return float(np.sqrt((t - v) / m))


def intelligibility(vocal_path: str, masker_path: str,
                    active_gate_db: float | None = -45.0,
                    master_path: str | None = None,
                    baseline: list[float] | None = None) -> dict:
    """SII вокала против остального микса. 0 — не разобрать, 1 — идеально.

    Считается по кадрам и усредняется только по тем, где вокал реально звучит:
    иначе паузы между фразами (в них речи нет) тянут число вниз, и микс с
    длинными инструментальными частями выглядит «нечитаемым» без причины.

    `master_path` — мастер микса: по нему маскер выравнивается по уровню,
    чтобы сравнивались балансы, а не громкости отрендеренных файлов.
    """
    import partial

    xv, fsv = _read(vocal_path)
    xm, fsm = _read(masker_path)
    if fsv != fsm:
        raise ValueError("частоты дискретизации разные: %d и %d" % (fsv, fsm))

    start = int(np.argmin(np.abs(partial.TC - FREQS[0])))
    ev_all, _t = partial.bands29(xv, fsv)
    em_all, _t2 = partial.bands29(xm, fsm)
    if master_path:
        xM, fsM = _read(master_path)
        if fsM != fsv:
            raise ValueError("частота мастера %d не совпадает с %d" % (fsM, fsv))
        eM_all, _t3 = partial.bands29(xM, fsM)
        frames = min(len(ev_all), len(em_all), len(eM_all))
        g = calibrate_gain(10 ** (ev_all[:frames, start:start + 18] / 10),
                           10 ** (em_all[:frames, start:start + 18] / 10),
                           10 ** (eM_all[:frames, start:start + 18] / 10))
        em_all = em_all + 20 * np.log10(max(g, 1e-6))
    else:
        g = 1.0
        frames = min(len(ev_all), len(em_all))

    ev_all = ev_all[:frames, start:start + len(FREQS)]
    em_all = em_all[:frames, start:start + len(FREQS)]

    # Активность вокала: полная энергия полос выше порога
    power = 10 ** (ev_all / 10)
    total_db = 10 * np.log10(np.maximum(power.sum(axis=1), 1e-30))
    if active_gate_db is None:
        active = np.ones(frames, dtype=bool)
    else:
        active = total_db > active_gate_db
    if not active.any():
        raise ValueError("вокал не звучит ни в одном кадре (порог %.0f дБ)"
                         % (active_gate_db or 0))

    per_frame = np.empty(frames)
    for i in range(frames):
        per_frame[i] = sii_from_levels(ev_all[i], em_all[i])["sii"]

    value = float(per_frame[active].mean())

    # Итоговые уровни — только по активным кадрам, чтобы таблица была честной
    e = 10 * np.log10(np.maximum((10 ** (ev_all[active] / 10)).mean(axis=0), 1e-30))
    n = 10 * np.log10(np.maximum((10 ** (em_all[active] / 10)).mean(axis=0), 1e-30))
    out = sii_from_levels(e, n)
    out["sii"] = round(value, 4)
    out["grade"] = _grade(value, baseline)
    if baseline:
        vals = sorted(float(v) for v in baseline)
        out["percentile"] = round(
            100 * sum(1 for v in vals if v <= value) / max(len(vals), 1), 1)
    out["frames_active"] = int(active.sum())
    out["frames_total"] = int(frames)
    out["masker_gain_db"] = round(20 * float(np.log10(max(g, 1e-6))), 2)
    out["vocal_path"] = vocal_path
    out["masker_path"] = masker_path
    return out


def _grade(sii_value: float, baseline: list[float] | None = None) -> str:
    """Оценка разборчивости.

    Абсолютные пороги SII — речевые: в плотном металле даже хорошо сведённый
    вокал даёт 0.2–0.4, и «нечитаемо» по школьной шкале было бы враньём.
    Поэтому при наличии базовой линии (SII референсов того же жанра) оценка
    относительная: где мы стоим среди них.
    """
    if baseline:
        vals = sorted(float(v) for v in baseline)
        rank = sum(1 for v in vals if v <= sii_value) / max(len(vals), 1)
        pct = round(rank * 100)
        if rank >= 0.8:
            return "вокал читается лучше %d%% референсов" % pct
        if rank >= 0.5:
            return "вокал в норме жанра (лучше %d%% референсов)" % pct
        if rank >= 0.2:
            return "вокал слабее большинства: лучше только %d%% референсов" % pct
        return "вокал хуже %d%% референсов" % (100 - pct)
    if sii_value >= 0.75:
        return "по речевой шкале: слова разбираются без усилия"
    if sii_value >= 0.60:
        return "по речевой шкале: разборчиво, но текст требует внимания"
    if sii_value >= 0.45:
        return "по речевой шкале: среднее"
    if sii_value >= 0.30:
        return "по речевой шкале: плохо (для речи)"
    return "по речевой шкале: нечитаемо (для речи; для жанра может быть нормой)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Разборчивость вокала (SII, ANSI S3.5)")
    ap.add_argument("--vocal", required=True)
    ap.add_argument("--masker", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = intelligibility(a.vocal, a.masker)
    res["grade"] = _grade(res["sii"])
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("SII %.3f — %s" % (res["sii"], res["grade"]))
    print("\nполоса  речь  маскер  слышимость  важность")
    for b in res["bands"]:
        print("%6d %6.1f %7.1f %10.3f %9.4f" % (
            b["f"], b["speech"], b["masker"], b["audibility"], b["importance"]))
    print("\nхуже всего полоса %d Гц" % res["worst_band"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
