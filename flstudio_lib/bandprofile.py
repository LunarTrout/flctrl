"""bandprofile.py -- многoосевой профиль по 240 полосам Барка.

Три независимые оси на одной сетке полос:
  1. уровень (воспринимаемый вес) — dB по полосам;
  2. ширина — side/mid в каждой полосе, дБ;
  3. модуляция — разброс уровня полосы по времени (прокси roughness и «каши»).

  ../.venv313/Scripts/python.exe bandprofile.py mix.wav ref1.mp3 ref2.mp3
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402

NB = 240
HOP = 0.05
NFFT = 4096
BARK = np.arange(NB) * 0.1 + 0.05
HZ = 600.0 * np.sinh(BARK / 6.0)          # центры полос, Гц


def _matrix(fs: int):
    freqs = np.fft.rfftfreq(NFFT, 1.0 / fs)
    edges = np.concatenate([[HZ[0] * 0.9], (HZ[1:] + HZ[:-1]) / 2.0, [HZ[-1] * 1.1]])
    M = np.zeros((NB, len(freqs)))
    for i in range(NB):
        m = (freqs >= edges[i]) & (freqs < edges[i + 1])
        n = m.sum()
        if n:
            M[i, m] = 1.0 / n
    return M


def band_axes(path: str) -> dict:
    """Возвращает по полосам: уровень (дБ), ширину (дБ side/mid), модуляцию (дБ)."""
    x, fs = L.load_section(path, None)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    win = np.hanning(NFFT)
    hop = int(HOP * fs)
    nfr = max(1, 1 + (len(x) - NFFT) // hop)
    idx = np.arange(NFFT)[None, :] + hop * np.arange(nfr)[:, None]
    Lc = x[idx, 0] * win
    Rc = x[idx, 1] * win
    SL = np.fft.rfft(Lc, axis=1)
    SR = np.fft.rfft(Rc, axis=1)
    M = _matrix(fs)
    pL = (np.abs(SL) ** 2) @ M.T
    pR = (np.abs(SR) ** 2) @ M.T
    mid = (pL + pR) / 2.0
    side = (pL + pR) / 2.0 - np.sqrt(np.maximum(pL * pR, 0.0))
    lvl = 10.0 * np.log10(np.maximum(mid, 1e-20))
    wid = 10.0 * np.log10(np.maximum(side, 1e-20) / np.maximum(mid, 1e-20))
    active = lvl.mean(axis=1) > (lvl.mean() - 30)
    if active.sum() > 10:
        lvl_s, wid_s = lvl[active], wid[active]
    else:
        lvl_s, wid_s = lvl, wid
    return {
        "level": np.median(lvl_s, axis=0),
        "width": np.median(wid_s, axis=0),
        "mod": np.std(lvl_s, axis=0),
    }


def main() -> int:
    files = sys.argv[1:]
    if len(files) < 2:
        print("нужен микс и хотя бы один референс")
        return 2
    mine = band_axes(files[0])
    refs = [band_axes(r) for r in files[1:]]

    def ref_stats(key):
        arr = np.vstack([r[key] for r in refs])
        return np.median(arr, axis=0), arr.min(axis=0), arr.max(axis=0)

    lv_m, lv_lo, lv_hi = ref_stats("level")
    wd_m, wd_lo, wd_hi = ref_stats("width")
    md_m, md_lo, md_hi = ref_stats("mod")

    d_lv = mine["level"] - lv_m
    d_wd = mine["width"] - wd_m
    d_md = mine["mod"] - md_m

    score = np.abs(d_lv) + np.abs(d_wd) + np.abs(d_md)
    order = np.argsort(-score)

    print("микс: %s" % os.path.basename(files[0]))
    print("референсы: %s" % ", ".join(os.path.basename(r)[:16] for r in files[1:]))
    print()
    print("%7s %8s | %8s %8s | %8s %8s | %8s %8s" % (
        "Барк", "Гц", "ур.наш", "Δур.", "шир.наш", "Δшир.", "мод.наш", "Δмод."))
    print("-" * 78)
    for i in order[:18]:
        print("%7.1f %8.0f | %8.1f %+8.1f | %8.1f %+8.1f | %8.1f %+8.1f" % (
            BARK[i], HZ[i], mine["level"][i], d_lv[i],
            mine["width"][i], d_wd[i], mine["mod"][i], d_md[i]))

    out_lv = int(np.sum((mine["level"] < lv_lo) | (mine["level"] > lv_hi)))
    out_wd = int(np.sum((mine["width"] < wd_lo) | (mine["width"] > wd_hi)))
    out_md = int(np.sum((mine["mod"] < md_lo) | (mine["mod"] > md_hi)))
    print()
    print("вне коридора: уровень %d, ширина %d, модуляция %d (из %d полос)"
          % (out_lv, out_wd, out_md, NB))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
