"""Живой тест: поканальный звук микшера через flctrl.live.

Требует запущенную FL с открытым проектом и переменную FLCTRL_TEST_ANY=1
(тест ничего не сохраняет и не меняет: только чтение + снимок 1 с).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flctrl"))

live = pytest.importorskip("live")


def _ready() -> bool:
    if os.environ.get("FLCTRL_TEST_ANY") != "1":
        return False
    return live.fl_pid() is not None


pytestmark = pytest.mark.skipif(not _ready(), reason="нужна живая FL (FLCTRL_TEST_ANY=1)")


def test_capture_two_tracks_distinct():
    res = live.capture(tracks=[1, 12], seconds=1.0, want_audio=True, mode="files")
    assert not res.get("error"), res.get("error")
    assert len(res["tracks"]) >= 2
    assert len(res["frames"]) >= 2
    for fr in res["frames"].values():
        assert len(fr["axes"]["time"]) == len(fr["values"])
        assert len(fr["axes"]["band"]) == len(fr["values"][0])

    samples = res.get("_samples") or {}
    loud = [k for k, v in res["tracks"].items() if not v["silent"]]
    if len(loud) >= 2:
        a = samples[int(loud[0])].mean(axis=1)
        b = samples[int(loud[1])].mean(axis=1)
        n = min(len(a), len(b))
        corr = float(np.corrcoef(a[:n], b[:n])[0, 1])
        assert abs(corr) < 0.98, "дорожки не должны быть копией друг друга"


def test_disk_is_clean_after_capture():
    live.capture(tracks=[1], seconds=1.0, mode="files")
    assert len(os.listdir(live.RECORDED)) == 0


def test_snapshots_exist():
    rows = live.snapshots(5)
    assert isinstance(rows, list)
