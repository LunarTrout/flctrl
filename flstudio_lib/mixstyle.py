"""mixstyle.py -- стиль СВЕДЕНИЯ как эмбеддинг (MIXSTYLE FXencoder).

Отвечает на вопрос не «похож ли тембр», а «похож ли подход к сведению»:
сжатие, плотность, ширина, характер обработки. 2048-мерный вектор за доли
секунды на клип.

  python mixstyle.py embed mix.wav                 # размерность
  python mixstyle.py sim a.wav b.wav               # близость стиля
  python mixstyle.py near query.wav DIR/*.mp3      # что ближе по стилю

Требует torch (окружение 3.12) и веса FXencoder.
Путь к весам и коду: FL_STYLE_ROOT (по умолчанию — scratch/mixml).
"""
from __future__ import annotations

import glob
import os
import sys
import types

import numpy as np

DEFAULT_ROOT = r"C:\Users\epicc\AppData\Local\hermes\cache\scratch\mixml"
ROOT = os.environ.get("FL_STYLE_ROOT", DEFAULT_ROOT)
WEIGHTS = os.path.join(ROOT, "fxencoder.pth")
CODE = os.path.join(ROOT, "mst", "mixing_style_transfer")

SR = 44100
CHUNK_S = 10.0

_ENC = None


def _stub_lightning():
    """MIXSTYLE требует pytorch_lightning только ради LightningModule."""
    import torch
    if "pytorch_lightning" in sys.modules:
        return
    pl = types.ModuleType("pytorch_lightning")

    class _LM(torch.nn.Module):
        def save_hyperparameters(self, *a, **k):
            loc = dict(sys._getframe(1).f_locals)
            loc.pop("self", None)
            loc.update(k)
            self.hparams = types.SimpleNamespace(**loc)

        def log(self, *a, **k):
            pass

    pl.LightningModule = _LM
    sys.modules["pytorch_lightning"] = pl


def _encoder():
    global _ENC
    if _ENC is not None:
        return _ENC
    import torch
    _stub_lightning()
    if CODE not in sys.path:
        sys.path.insert(0, CODE)
    from networks import FXencoder

    cfg = dict(channels=[16, 32, 64, 128, 256, 256, 512, 512, 1024, 1024, 2048, 2048],
               kernels=[25, 25, 15, 15, 10, 10, 10, 10, 5, 5, 5, 5],
               strides=[4, 4, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1],
               dilation=[1] * 12, bias=True, norm="batch", conv_block="res",
               activation="relu")
    model = FXencoder(cfg).eval()
    ck = torch.load(WEIGHTS, map_location="cpu", weights_only=False)
    sd = ck["model"] if isinstance(ck, dict) and "model" in ck else ck
    sd = {k[7:] if k.startswith("module.") else k: v for k, v in sd.items()}
    model.load_state_dict(sd, strict=False)
    torch.set_num_threads(min(8, os.cpu_count() or 4))
    _ENC = model
    return model


def _chunks(path: str, start: float | None = None, dur: float | None = None):
    import librosa
    y, _ = librosa.load(path, sr=SR, mono=False, offset=start, duration=dur)
    if y.ndim == 1:
        y = np.stack([y, y])
    if y.shape[0] > 2:
        y = y[:2]
    n = int(CHUNK_S * SR)
    out = []
    for i in range(0, max(y.shape[1] - n, 0) + 1, n):
        out.append(y[:, i:i + n])
    if not out:
        pad = np.zeros((2, n))
        pad[:, :y.shape[1]] = y
        out.append(pad)
    return np.stack(out).astype(np.float32)


def embed(path: str, start: float | None = None, dur: float | None = None) -> np.ndarray:
    """Усреднённый стилевой эмбеддинг."""
    import torch
    model = _encoder()
    x = _chunks(path, start, dur)
    with torch.no_grad():
        z = model(torch.from_numpy(x))
    v = z.mean(dim=0).cpu().numpy().astype(np.float64).reshape(-1)
    return v / (np.linalg.norm(v) + 1e-12)


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12))


def nearest(query: str, paths: list[str], start: float | None = None,
            dur: float | None = None) -> list[tuple[str, float]]:
    q = embed(query, start, dur)
    res = []
    for p in paths:
        try:
            res.append((p, cos(q, embed(p, start, dur))))
        except Exception:
            continue
    return sorted(res, key=lambda t: -t[1])


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    if not os.path.exists(WEIGHTS):
        print("нет весов:", WEIGHTS)
        return 3
    cmd = sys.argv[1]
    if cmd == "embed":
        v = embed(sys.argv[2])
        print("стилевой эмбеддинг: %d, норма %.3f" % (len(v), float(np.linalg.norm(v))))
        return 0
    if cmd == "sim":
        print("%.4f" % cos(embed(sys.argv[2]), embed(sys.argv[3])))
        return 0
    if cmd == "near":
        paths = []
        for pat in sys.argv[3:]:
            paths.extend(glob.glob(pat))
        for p, s in nearest(sys.argv[2], paths)[:10]:
            print("%.4f  %s" % (s, os.path.basename(p)))
        return 0
    print("неизвестная команда")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
