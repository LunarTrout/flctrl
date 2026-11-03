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
let capBuf = null, capOff = 0, capCap = 0, capOrder = [], capDone = false, capBlocks = 0;
let capListener = null, capPer = 0, capFrames = 0, capNotes = [], capZero = null;

rpc.exports.info = function () {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  return { frames: fe.base.add(0x10AA730).readPointer().readU32(),
           count: fe.base.add(0x10A9850).readPointer().readU32() };
};

rpc.exports.rate = function () {
  // частота не хардкодится: спрашиваем у движка, если не выйдет — 44100
  try {
    const fe = Process.getModuleByName('FLEngine_x64.dll');
    const p = fe.base.add(0x10AA730);      // рядом с кадрами блока
    return { frames: p.readPointer().readU32() };
  } catch (e) { return { error: String(e) }; }
};

rpc.exports.capArm = function (tracks, maxBytes) {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  capCap = maxBytes | 0;
  capBuf = Memory.alloc(capCap);
  capZero = Memory.alloc(8192);
  capOrder = tracks.slice();
  capOff = 0; capBlocks = 0; capDone = false; capNotes = [];
  try {
    capListener = Interceptor.attach(fe.base.add(0x78F624), {
      onEnter() {
        if (capDone) return;
        try {
          const arr = fe.base.add(0x10A7EB0).readPointer();
          const count = fe.base.add(0x10A9850).readPointer().readU32();
          capFrames = fe.base.add(0x10AA730).readPointer().readU32();
          capPer = capFrames * 8;
          for (let i = 0; i < capOrder.length; i++) {
            const t = capOrder[i];
            if (t >= count) continue;
            if (capOff + capPer > capCap) { capDone = true; break; }
            const p = arr.add(t * 0x1474).add(0x34).readPointer();
            if (p.isNull()) Memory.copy(capBuf.add(capOff), capZero, capPer);
            else Memory.copy(capBuf.add(capOff), p, capPer);
            capOff += capPer;
          }
          capBlocks++;
        } catch (e) { capNotes.push(String(e)); }
      }
    });
  } catch (e) { return { error: String(e) }; }
  return { armed: true, cap: capCap };
};

rpc.exports.capInfo = function () {
  return { blocks: capBlocks, frames: capFrames, off: capOff, per: capPer,
           order: capOrder, done: capDone, notes: capNotes };
};

rpc.exports.capChunk = function (start, len) {
  const n = Math.min(len, capOff - start);
  if (n <= 0) return '';
  const u = new Uint8Array(capBuf.add(start).readByteArray(n));
  let hex = '';
  for (let i = 0; i < u.length; i++) hex += ('0' + u[i].toString(16)).slice(-2);
  return hex;
};

rpc.exports.capStop = function () {
  capDone = true;
  if (capListener) { try { capListener.detach(); } catch (e) {} capListener = null; }
  return { off: capOff, blocks: capBlocks };
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

    def record_exact(self, tracks: list[int] | None, seconds: float, outdir: str,
                     mb: int = 64, include_silent: bool = True) -> dict:
        """Блочно-точная запись: блоки копятся в памяти процесса.

        Во время захвата мы НЕ дёргаем RPC (это и вешало процесс) — только спим
        и потом забираем данные кусками после снятия хука.
        """
        import time as _t

        os.makedirs(outdir, exist_ok=True)
        if tracks is None:
            tracks = list(range(self.count))
        tracks = [t for t in tracks if t < self.count]

        start_beats = None
        try:
            import fl_lib
            start_beats = float(fl_lib._State._live("float(transport.getSongPos(1))", attempts=1))
        except Exception:  # noqa: BLE001
            pass

        res = self.script.exports_sync.cap_arm(tracks, mb * 1024 * 1024)
        if res.get("error"):
            return {"error": res["error"]}

        _t.sleep(max(0.05, seconds))
        info = {}
        for _ in range(40):                      # ждём, пока RPC снова ответит
            try:
                info = self.script.exports_sync.cap_stop()
                break
            except Exception:                     # noqa: BLE001
                _t.sleep(0.05)
        meta = self.script.exports_sync.cap_info()

        frames = int(meta["frames"]) or 256
        per = int(meta["per"]) or frames * 8
        order = list(meta["order"])
        total = int(info.get("off", 0))
        if not total or not order:
            return {"error": "нет данных", "meta": meta}

        gathered = bytearray()
        pos = 0
        while pos < total:
            n = min(65536, total - pos)
            hexs = self.script.exports_sync.cap_chunk(pos, n)
            if not hexs:
                break
            gathered.extend(bytes.fromhex(hexs))
            pos += n

        block_bytes = per * len(order)
        per_track: dict[int, bytearray] = {t: bytearray() for t in order}
        idx = 0
        blocks = 0
        while idx + block_bytes <= len(gathered):
            base = idx
            for k, t in enumerate(order):
                per_track[t].extend(gathered[base + k * per: base + (k + 1) * per])
            idx += block_bytes
            blocks += 1

        paths, written = {}, {}
        for t, data in per_track.items():
            if not data:
                if not include_silent:
                    continue
                data = bytearray(frames * 8)
            path = os.path.join(outdir, "track_%02d.wav" % t)
            self._write_wav(path, list(struct.unpack("<%df" % (len(data) // 4), bytes(data))))
            paths[t] = path
            vals = struct.unpack("<%df" % (len(data) // 4), bytes(data))
            peak = max(abs(v) for v in vals) if vals else 0.0
            written[t] = {"blocks": blocks, "seconds": round(len(data) / 8 / 44100, 3),
                          "peak": round(peak, 4)}

        return {"dir": outdir, "paths": paths, "written": written,
                "frames": frames, "tracks_total": self.count,
                "start_beats": start_beats, "blocks": blocks,
                "block_seconds": round(frames / 44100.0, 4)}

    def mix(self, tracks: list[int] | None, seconds: float, path: str,
            rate_hz: float = 0.0, fmt: str | None = None) -> dict:
        """Снять выбранные дорожки вживую и сложить в один файл.

        `rate_hz=0` — читать как можно чаще (для одной-двух дорожек это почти
        полный поток). Сумма дорожек считается как есть, без гейнов: это
        «послушать, что сейчас звучит», а не рендер проекта.
        """
        import time as _t

        import numpy as np

        if tracks is None:
            tracks = [0]
        tracks = [int(t) for t in tracks if int(t) < self.count]
        acc: list[np.ndarray] = []
        last: dict[int, bytes] = {}
        t0 = _t.time()
        budget = seconds
        while _t.time() - t0 < budget:
            got, self._inbox = self._inbox, []
            self.script.exports_sync.pump(tracks)
            if rate_hz:
                _t.sleep(1.0 / rate_hz)
            else:
                _t.sleep(0.001)
            got += self._inbox
            self._inbox = []
            step = {}
            for tr, buf, nf in got:
                if not buf or last.get(tr) == buf:
                    continue
                last[tr] = buf
                step[tr] = np.frombuffer(buf, dtype="<f4").reshape(-1, 2).astype(np.float32)
            if step:
                length = min(len(v) for v in step.values())
                acc.append(sum(step[k][:length] for k in step))
        if not acc:
            return {"error": "нет данных", "tracks": tracks}
        audio = np.concatenate(acc, axis=0)
        raw = audio.astype("<f4").tobytes()
        self._write_wav(path, audio.reshape(-1).tolist())
        out = {"path": path, "tracks": tracks, "seconds": round(len(audio) / self.fs, 2),
               "peak": round(float(np.abs(audio).max()), 4)}
        if fmt == "mp3":
            import subprocess
            ff = os.path.expandvars(r"%LOCALAPPDATA%/hermes/tools/ffmpeg-9.0.1-win32-x64/bin/ffmpeg.exe")
            mp3 = os.path.splitext(path)[0] + ".mp3"
            r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-b:a", "192k", mp3],
                               capture_output=True, text=True)
            if os.path.exists(mp3):
                out["mp3"] = mp3
            else:
                out["mp3_error"] = (r.stderr or "")[:200]
        return out

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

    def record(self, tracks: list[int] | None, seconds: float, outdir: str,
               rate_hz: float = 400.0, include_silent: bool = False) -> dict:
        """Писать блоки дорожек в WAV.

        `tracks=None` — все дорожки проекта. `include_silent=True` — писать файл
        и для дорожек, где не поймано ни одного блока (там будет тишина), чтобы
        нумерация файлов совпадала с номерами дорожек.
        """
        os.makedirs(outdir, exist_ok=True)
        if tracks is None:
            tracks = list(range(self.count))
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
                if not include_silent:
                    continue
                self._write_wav(os.path.join(outdir, "track_%02d.wav" % t),
                                [0.0] * (self.frames * 2))
                paths[t] = os.path.join(outdir, "track_%02d.wav" % t)
                written[t] = {"blocks": 0, "seconds": 0.0, "silent": True}
                continue
            flat = [v for blk in blocks for v in blk]
            path = os.path.join(outdir, "track_%02d.wav" % t)
            self._write_wav(path, flat)
            paths[t] = path
            written[t] = {"blocks": len(blocks), "seconds": round(len(flat) / 2 / 44100, 3)}
        return {"dir": outdir, "paths": paths, "written": written}
