"""sandbox.py -- песочница: набор ручек, а не фиксированный пайплайн.

Любая цепочка собирается из примитивов в произвольном порядке:

    ops = [
        {"op": "gain",  "db": -3},
        {"op": "hpf",   "f": 80,  "order": 2},
        {"op": "eq",    "bands": [[300, -4, 0.8], [6000, 2, 0.7]]},   # Гц, дБ, Q
        {"op": "comp",  "threshold": -18, "ratio": 3.5, "attack": 12, "release": 181},
        {"op": "sat",   "drive": 0.35},
        {"op": "pan",   "value": -1.0},
        {"op": "vst3",  "path": ".../FabFilter Pro-Q 4.vst3", "params": {"Output Gain": -2}},
    ]

    y = run(x, fs, ops)              # применили
    m0, m1 = compare(x, y, fs)       # померили до/после

Каждая операция независима — их можно ставить в любом количестве и порядке.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402


# --------------------------------------------------------------- примитивы

def _biquad_peak(x: np.ndarray, fs: int, f0: float, gain_db: float, q: float):
    """Пиковый эквалайзер (RBJ), каналы обрабатываются одинаково."""
    import scipy.signal as ss
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2 * np.pi * f0 / fs
    alpha = np.sin(w0) / (2 * q)
    b = [1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A]
    a = [1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A]
    return ss.lfilter(b, a, x, axis=0)


def _highpass(x: np.ndarray, fs: int, f0: float, order: int = 2):
    import scipy.signal as ss
    sos = ss.butter(order, f0 / (fs / 2), btype="highpass", output="sos")
    return ss.sosfilt(sos, x, axis=0)


def _lowpass(x: np.ndarray, fs: int, f0: float, order: int = 2):
    import scipy.signal as ss
    sos = ss.butter(order, min(f0 / (fs / 2), 0.99), btype="lowpass", output="sos")
    return ss.sosfilt(sos, x, axis=0)


def _compress(x: np.ndarray, fs: int, threshold: float, ratio: float,
              attack_ms: float, release_ms: float, knee: float = 6.0):
    """Простой пиковый компрессор со сглаживанием — для быстрой проверки гипотез."""
    mono = np.abs(x).max(axis=1) if x.ndim > 1 else np.abs(x)
    db = 20 * np.log10(np.maximum(mono, 1e-9))
    over = db - threshold
    gain = np.where(over > 0, -over * (1 - 1 / ratio), 0.0)
    # мягкое колено
    k = (over > -knee) & (over <= 0)
    gain[k] = -((over[k] + knee) ** 2) / (4 * knee) * (1 - 1 / ratio)
    a_att = np.exp(-1.0 / (attack_ms * 1e-3 * fs))
    a_rel = np.exp(-1.0 / (release_ms * 1e-3 * fs))
    sm = np.empty_like(gain)
    g = 0.0
    for i, v in enumerate(gain):
        coef = a_att if v < g else a_rel
        g = coef * g + (1 - coef) * v
        sm[i] = g
    lin = 10 ** (sm / 20)
    return x * lin[:, None] if x.ndim > 1 else x * lin


def _saturate(x: np.ndarray, drive: float):
    """Мягкое ограничение с сохранением уровня (tanh)."""
    d = max(0.01, float(drive))
    y = np.tanh(x * (1 + 5 * d))
    peak = np.max(np.abs(x)) + 1e-12
    return y / (np.max(np.abs(y)) + 1e-12) * peak


def _pan(x: np.ndarray, value: float):
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    v = float(np.clip(value, -1, 1))
    # равномощное панорамирование
    ang = (v + 1) * np.pi / 4
    l, r = np.cos(ang), np.sin(ang)
    return np.stack([x[:, 0] * (l * 1.4142), x[:, 1] * (r * 1.4142)], axis=1)


def _vst3(x: np.ndarray, fs: int, path: str, params: dict | None = None,
          bands: list | None = None):
    """Загрузить VST3 и применить.

    bands — удобный слой для параметрических EQ (Pro-Q и подобных):
    [[частота, дБ, Q], ...] раскладывается в band_N_frequency/gain/q.
    params — точные имена параметров плагина, если нужно больше.
    """
    import pedalboard as pb
    plugin = pb.load_plugin(path)
    for i, b in enumerate(bands or [], start=1):
        f0, db = float(b[0]), float(b[1])
        q = float(b[2]) if len(b) > 2 else 0.8
        for name, val in (("band_%d_used" % i, "Bell"),
                          ("band_%d_enabled" % i, True),
                          ("band_%d_frequency" % i, f0),
                          ("band_%d_gain" % i, db),
                          ("band_%d_q" % i, q)):
            try:
                setattr(plugin, name, val)
            except Exception:
                pass
    for k, v in (params or {}).items():
        try:
            setattr(plugin, k, v)
        except Exception:
            pass
    out = plugin(x.T if x.ndim > 1 else x, fs)
    out = np.asarray(out)
    return out.T if out.ndim > 1 else out


# ------------------------------------------------------------------ движок

def run(x: np.ndarray, fs: int, ops: list[dict]) -> np.ndarray:
    """Применить цепочку операций в указанном порядке."""
    y = np.asarray(x, dtype=np.float64)
    for op in ops:
        kind = op.get("op")
        if kind == "gain":
            y = y * (10 ** (float(op.get("db", 0.0)) / 20.0))
        elif kind == "hpf":
            y = _highpass(y, fs, float(op["f"]), int(op.get("order", 2)))
        elif kind == "lpf":
            y = _lowpass(y, fs, float(op["f"]), int(op.get("order", 2)))
        elif kind == "eq":
            for band in op.get("bands", []):
                f0, db = float(band[0]), float(band[1])
                q = float(band[2]) if len(band) > 2 else 0.8
                y = _biquad_peak(y, fs, f0, db, q)
        elif kind == "comp":
            y = _compress(y, fs, float(op["threshold"]), float(op.get("ratio", 3.0)),
                          float(op.get("attack", 10)), float(op.get("release", 120)))
        elif kind == "sat":
            y = _saturate(y, float(op.get("drive", 0.3)))
        elif kind == "pan":
            y = _pan(y, float(op["value"]))
        elif kind == "vst3":
            y = _vst3(y, fs, op["path"], op.get("params"), op.get("bands"))
        else:
            raise ValueError("неизвестная операция: %r" % kind)
    return y


def compare(x: np.ndarray, y: np.ndarray, fs: int, offset_db: float = 0.0) -> tuple[dict, dict]:
    """Замер до и после одной и той же калибровкой."""
    return L.measure_array(x, fs, offset_db), L.measure_array(y, fs, offset_db)


def delta_lines(a: dict, b: dict) -> list[str]:
    out = []
    out.append("соны       %7.1f → %7.1f  (%+.1f)" % (a["N_mean"], b["N_mean"], b["N_mean"] - a["N_mean"]))
    out.append("резкость   %7.3f → %7.3f  (%+.3f)" % (a["sharp_mean"], b["sharp_mean"], b["sharp_mean"] - a["sharp_mean"]))
    out.append("LUFS       %7.2f → %7.2f  (%+.2f)" % (a["lufs"], b["lufs"], b["lufs"] - a["lufs"]))
    out.append("crest      %7.2f → %7.2f  (%+.2f)" % (a["crest_db"], b["crest_db"], b["crest_db"] - a["crest_db"]))
    ra, rb = a.get("regions_pct") or {}, b.get("regions_pct") or {}
    for k in ra:
        out.append("%-9s %7.1f%% → %6.1f%%  (%+.1f)" % (k, ra[k], rb.get(k, 0), rb.get(k, 0) - ra[k]))
    return out


def main() -> int:
    import argparse
    import soundfile as sf

    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out")
    ap.add_argument("--from", dest="a", type=float)
    ap.add_argument("--to", dest="b", type=float)
    ap.add_argument("--ops", help="JSON-список операций")
    ap.add_argument("--ops-file")
    a = ap.parse_args()

    with sf.SoundFile(a.inp) as f:
        fs = f.samplerate
        if a.a is not None and a.b is not None:
            f.seek(int(a.a * fs))
            x = f.read(int((a.b - a.a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)

    spec = []
    if a.ops_file:
        with open(a.ops_file, encoding="utf-8") as fh:
            spec = json.load(fh)
    elif a.ops:
        spec = json.loads(a.ops)

    y = run(x, fs, spec)
    m0, m1 = compare(x, y, fs, L.track_offset(a.inp))
    print("операций: %d" % len(spec))
    for line in delta_lines(m0, m1):
        print("  " + line)
    if a.out:
        sf.write(a.out, y, fs)
        print("записано:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
