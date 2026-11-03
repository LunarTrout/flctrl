"""tap.py — живое аудио дорожек микшера FL Studio.

Проверено на 25.2.5: буфер дорожки лежит по адресу
`[[FE+0x10A7EB0] + i*0x1474 + 0x34]` — float32 стерео, кадров `[FE+0x10AA730]`
(обычно 256). Читается опросом из Python, без хуков: хук внутри аудио-колбэка
забивает поток Frida, и RPC перестаёт отвечать.

Что умеет:
* `levels()` — RMS/пик по дорожкам за окно времени;
* `record()` — писать блоки дорожек в WAV (по файлу на дорожку).

Ограничение честное: опрос может пропустить блок или взять его дважды —
для уровней это неважно, для записи возможны редкие повторы.
"""
from __future__ import annotations

import os
import struct
import subprocess
import time

import frida

JS = r"""
'use strict';
rpc.exports.info = function () {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  return { frames: fe.base.add(0x10AA730).readPointer().readU32(),
           count: fe.base.add(0x10A9850).readPointer().readU32() };
};

rpc.exports.peek = function (track, nbytes) {
  try {
    const fe = Process.getModuleByName('FLEngine_x64.dll');
    const frames = fe.base.add(0x10AA730).readPointer().readU32();
    const rec = fe.base.add(0x10A7EB0).readPointer().add(track * 0x1474);
    const p = rec.add(0x34).readPointer();
    if (p.isNull()) return null;
    const want = Math.min(nbytes, frames * 2 * 4);
    const u = new Uint8Array(p.readByteArray(want));
    let hex = '';
    for (let i = 0; i < u.length; i++) hex += ('0' + u[i].toString(16)).slice(-2);
    return { at: p.toString(), hex: hex };
  } catch (e) { return { error: String(e) }; }
};
"""


def _pid(name: str = "FL64.exe") -> int | None:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
                         capture_output=True)
    for line in out.stdout.decode("cp866", "replace").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == name.lower():
            try:
                return int(parts[1])
            except (ValueError, IndexError):
                return None
    return None


class Tap:
    """Живой читатель дорожек: один attach, много коротких запросов."""

    def __init__(self, pid: int | None = None):
        self.pid = pid or _pid()
        if not self.pid:
            raise RuntimeError("FL не запущена")
        self.session = frida.attach(self.pid)
        self.script = self.session.create_script(JS)
        self.script.load()
        info = self.script.exports_sync.info()
        self.frames = int(info["frames"]) or 256
        self.count = int(info["count"])

    def close(self) -> None:
        try:
            self.session.detach()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def block(self, track: int) -> list[float] | None:
        """Текущий блок дорожки как список float (стерео, чередование)."""
        r = self.script.exports_sync.peek(track, self.frames * 8)
        if not r or r.get("error") or not r.get("hex"):
            return None
        raw = bytes.fromhex(r["hex"])
        if len(raw) % 4:
            raw = raw[:len(raw) // 4 * 4]
        return list(struct.unpack("<%df" % (len(raw) // 4), raw))

    def levels(self, tracks: list[int] | None = None, seconds: float = 1.0,
               rate_hz: float = 120.0) -> dict:
        """RMS и пик по каждой дорожке за окно времени."""
        tracks = [t for t in (tracks or range(self.count)) if t < self.count]
        stats = {t: {"hits": 0, "sum_rms": 0.0, "peak": 0.0, "best_rms": 0.0}
                 for t in tracks}
        period = 1.0 / max(rate_hz, 1.0)
        end = time.time() + seconds
        rounds = 0
        while time.time() < end:
            for t in tracks:
                vals = self.block(t)
                if not vals:
                    continue
                peak = max(abs(v) for v in vals)
                rms = (sum(v * v for v in vals) / len(vals)) ** 0.5
                st = stats[t]
                st["hits"] += 1
                st["sum_rms"] += rms
                if rms > st["best_rms"]:
                    st["best_rms"] = rms
                if peak > st["peak"]:
                    st["peak"] = peak
            rounds += 1
            time.sleep(period)
        out = {}
        for t, st in stats.items():
            out[t] = {"hits": st["hits"],
                      "rms": round(st["sum_rms"] / st["hits"], 5) if st["hits"] else 0.0,
                      "best_rms": round(st["best_rms"], 5),
                      "peak": round(st["peak"], 4)}
        return {"frames": self.frames, "tracks": self.count, "rounds": rounds,
                "levels": out}

    @staticmethod
    def _write_wav(path: str, flat: list[float], fs: int = 44100,
                   channels: int = 2) -> None:
        """WAV с float32 — без сторонних библиотек (frida-окружение их не имеет)."""
        n = len(flat)
        data = struct.pack("<%df" % n, *flat)
        block_align = channels * 4
        byte_rate = fs * block_align
        header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, channels, fs,
                                        byte_rate, block_align, 32)
        header += b"data" + struct.pack("<I", len(data))
        with open(path, "wb") as fh:
            fh.write(header)
            fh.write(data)

    def record(self, tracks: list[int], seconds: float, outdir: str,
               rate_hz: float = 400.0) -> dict:
        """Писать блоки дорожек в WAV. Возвращает пути и статистику."""
        os.makedirs(outdir, exist_ok=True)
        tracks = [t for t in tracks if t < self.count]
        chunks: dict[int, list[list[float]]] = {t: [] for t in tracks}
        last = {t: None for t in tracks}
        period = 1.0 / max(rate_hz, 1.0)
        end = time.time() + seconds
        while time.time() < end:
            for t in tracks:
                vals = self.block(t)
                if not vals:
                    continue
                # отпечаток блока: первый сэмпл + длина — чтобы не писать одно дважды
                key = (len(vals), round(vals[0], 6), round(vals[-1], 6))
                if last[t] == key:
                    continue
                last[t] = key
                chunks[t].append(vals)
            time.sleep(period)

        paths, written = {}, {}
        for t, blocks in chunks.items():
            if not blocks:
                continue
            flat = [v for blk in blocks for v in blk]
            path = os.path.join(outdir, "track_%02d.wav" % t)
            self._write_wav(path, flat)
            paths[t] = path
            written[t] = {"blocks": len(blocks), "seconds": round(len(flat) / 2 / 44100, 3)}
        return {"dir": outdir, "paths": paths, "written": written}
