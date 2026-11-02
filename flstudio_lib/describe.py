"""describe.py -- узнавание: эмбеддинги, близость, поиск похожего.

Это НЕ измерение. Отвечает на «на что похоже» и «где в референсах похожее
место», но ничего не говорит об уровне, громкости и жёсткости.

  python describe.py embed mix.wav                  # размерность и норма
  python describe.py near query.wav DIR/*.mp3       # что ближе всего
  python describe.py sim a.wav b.wav                # косинус между двумя

Требует panns_inference + torch (окружение 3.12).
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np

SR = 32000
WIN_S = 10.0
HOP_S = 5.0

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        import torch
        from panns_inference import AudioTagging
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        _MODEL = AudioTagging(checkpoint_path=None, device=dev)
    return _MODEL


def _windows(path: str, start: float | None = None, dur: float | None = None):
    import librosa
    y, _ = librosa.load(path, sr=SR, mono=True, offset=start, duration=dur)
    win = int(WIN_S * SR)
    hop = int(HOP_S * SR)
    chunks = []
    for i in range(0, max(len(y) - win, 0) + 1, hop):
        chunks.append(y[i:i + win])
    if not chunks:
        chunks = [np.pad(y, (0, max(win - len(y), 0)))[:win]]
    return np.stack(chunks).astype(np.float32)


def embed(path: str, start: float | None = None, dur: float | None = None) -> np.ndarray:
    """Усреднённый эмбеддинг файла (или его отрезка)."""
    x = _windows(path, start, dur)
    model = _model()
    try:
        _, emb = model.inference(x, batch_size=8)
    except TypeError:
        _, emb = model.inference(x)
    emb = np.asarray(emb, dtype=np.float64)
    v = emb.mean(axis=0)
    n = np.linalg.norm(v) + 1e-12
    return v / n


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12))


def nearest(query: str, paths: list[str], start: float | None = None,
            dur: float | None = None) -> list[tuple[str, float]]:
    q = embed(query, start, dur)
    out = []
    for p in paths:
        try:
            out.append((p, cos(q, embed(p, start, dur))))
        except Exception:
            continue
    return sorted(out, key=lambda t: -t[1])


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "embed":
        v = embed(sys.argv[2])
        print("размерность: %d, норма %.3f" % (len(v), float(np.linalg.norm(v))))
        return 0
    if cmd == "sim":
        print("%.4f" % cos(embed(sys.argv[2]), embed(sys.argv[3])))
        return 0
    if cmd == "near":
        q = sys.argv[2]
        paths = []
        for pat in sys.argv[3:]:
            paths.extend(glob.glob(pat))
        for p, s in nearest(q, paths)[:10]:
            print("%.4f  %s" % (s, os.path.basename(p)))
        return 0
    print("неизвестная команда")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
