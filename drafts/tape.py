"""tape.py — «лента»: живой захват FL как один объект со звуком, метриками и провенансом.

Идея: не снимки-файлы, а один объект на отрезок времени. Внутри:
* звук по дорожкам (float32, копится в памяти процесса FL);
* время: стартовая позиция в долях + шаг блока;
* провенанс: из разобранного .flp для каждого отрезка видно, какие клипы звучали.

Из ленты считается всё остальное по запросу (метрики, сравнения), а на диск она
материализуется одним комплектом: WAV по дорожкам + index.json.

Честные ограничения:
* бюджет памяти: 32 дорожки float32 — это ~11.3 МБ/с, то есть 500 МБ ≈ 44 с;
* во время захвата RPC не дёргаем (иначе висит), данные забираем после снятия.

    python -m flctrl.tape --seconds 20 --outdir C:/tmp/tape
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
let buf = null, cap = 0, off = 0, per = 0, frames = 0, count = 0;
let order = [], blocks = 0, done = false, listener = null, notes = [], zero = null;

rpc.exports.arm = function (tracks, maxBytes) {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  cap = maxBytes | 0;
  buf = Memory.alloc(cap);
  zero = Memory.alloc(16384);
  order = tracks.slice();
  off = 0; blocks = 0; done = false; notes = [];
  try {
    listener = Interceptor.attach(fe.base.add(0x78F624), {
      onEnter() {
        if (done) return;
        try {
          const arr = fe.base.add(0x10A7EB0).readPointer();
          count = fe.base.add(0x10A9850).readPointer().readU32();
          frames = fe.base.add(0x10AA730).readPointer().readU32();
          per = frames * 8;
          for (let i = 0; i < order.length; i++) {
            const t = order[i];
            if (t >= count) continue;
            if (off + per > cap) { done = true; break; }
            const p = arr.add(t * 0x1474).add(0x34).readPointer();
            if (p.isNull()) Memory.copy(buf.add(off), zero, per);
            else Memory.copy(buf.add(off), p, per);
            off += per;
          }
          blocks++;
        } catch (e) { notes.push(String(e)); }
      }
    });
  } catch (e) { return { error: String(e) }; }
  return { armed: true, cap: cap };
};

rpc.exports.info = function () {
  return { blocks: blocks, frames: frames, off: off, per: per,
           order: order, done: done, notes: notes };
};

rpc.exports.chunk = function (start, len) {
  const n = Math.min(len, off - start);
  if (n <= 0) return '';
  const u = new Uint8Array(buf.add(start).readByteArray(n));
  let hex = '';
  for (let i = 0; i < u.length; i++) hex += ('0' + u[i].toString(16)).slice(-2);
  return hex;
};

rpc.exports.stop = function () {
  done = true;
  if (listener) { try { listener.detach(); } catch (e) {} listener = null; }
  return { off: off, blocks: blocks };
};

rpc.exports.meta = function () {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  return { tracks: fe.base.add(0x10A9850).readPointer().readU32(),
           frames: fe.base.add(0x10AA730).readPointer().readU32() };
};
"""


def pid_of(name: str = "FL64.exe"):
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


def _bridge(expr):
    lib = os.path.dirname(os.path.abspath(__file__))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import fl_lib
    return fl_lib._State._live(expr, attempts=2)


def wav_write(path: str, samples: bytes, fs: int = 44100, channels: int = 2) -> None:
    header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, channels, fs,
                                    fs * channels * 4, channels * 4, 32)
    header += b"data" + struct.pack("<I", len(samples))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(samples)


class Tape:
    """Живой захват: звук по дорожкам + время + провенанс."""

    def __init__(self, pid: int | None = None):
        self.pid = pid or pid_of()
        if not self.pid:
            raise RuntimeError("FL не запущена")
        self.session = frida.attach(self.pid)
        self.script = self.session.create_script(JS)
        self.script.load()
        meta = self.script.exports_sync.meta()
        self.tracks_total = int(meta["tracks"])
        self.frames = int(meta["frames"]) or 256
        self.fs = 44100
        self._armed = False

    # ---------------------------------------------------------------- захват
    def record(self, seconds: float = 20.0, tracks: list[int] | None = None,
               budget_mb: int = 512) -> dict:
        """Писать ленту. Возвращает объект с данными (в памяти)."""
        if tracks is None:
            tracks = list(range(self.tracks_total))
        tracks = [t for t in tracks if t < self.tracks_total]
        need = int(seconds * self.fs * 8 * len(tracks))
        cap = min(budget_mb * 1024 * 1024, max(need, 4 * 1024 * 1024))

        start_beats = None
        try:
            start_beats = float(_bridge("float(transport.getSongPos(1))"))
        except Exception:  # noqa: BLE001
            pass

        res = self.script.exports_sync.arm(tracks, cap)
        if res.get("error"):
            return {"error": res["error"]}
        self._armed = True

        time.sleep(max(0.05, seconds))     # RPC во время захвата не трогаем

        info = {}
        for _ in range(80):
            try:
                info = self.script.exports_sync.stop()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        meta = self.script.exports_sync.info()
        self._armed = False

        frames = int(meta["frames"]) or self.frames
        per = int(meta["per"]) or frames * 8
        order = list(meta["order"])
        total = int(info.get("off", 0))
        if not total or not order:
            return {"error": "нет данных", "meta": meta}

        raw = bytearray()
        pos = 0
        while pos < total:
            n = min(65536, total - pos)
            hexs = self.script.exports_sync.chunk(pos, n)
            if not hexs:
                break
            raw.extend(bytes.fromhex(hexs))
            pos += n

        block_bytes = per * len(order)
        blocks = len(raw) // block_bytes
        per_track: dict[int, bytearray] = {t: bytearray() for t in order}
        for b in range(blocks):
            base = b * block_bytes
            for k, t in enumerate(order):
                per_track[t].extend(raw[base + k * per: base + (k + 1) * per])

        return {"start_beats": start_beats, "frames": frames, "per": per,
                "order": order, "blocks": blocks, "data": per_track,
                "seconds": round(blocks * frames / self.fs, 3),
                "tracks_total": self.tracks_total}

    # ------------------------------------------------------------ провенанс
    @staticmethod
    def provenance(tape: dict, flp: str | None, beat_seconds: float | None = None) -> list[dict]:
        """Для каждого отрезка — какие клипы звучали (из разбора .flp)."""
        if not flp or not tape.get("blocks"):
            return []
        lib = os.path.dirname(os.path.abspath(__file__))
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import timebase_file

        pr = timebase_file.load(flp)
        bs = beat_seconds or getattr(pr, "beat_seconds", 0.5)
        start_beats = tape.get("start_beats") or 0.0
        seg = []
        for i in range(tape["blocks"]):
            t0 = start_beats + i * tape["frames"] / 96.0     # 96 PPQ
            clip_list = pr.active_at(float(t0))
            seg.append({"block": i,
                        "beat": round(float(t0), 3),
                        "seconds": round(float(t0) * bs, 3),
                        "sources": clip_list[:8],
                        "count": len(clip_list)})
        return seg

    # ------------------------------------------------------------- комплекс
    def bundle(self, tape: dict, outdir: str, flp: str | None = None,
               keep: int = 5) -> dict:
        """Материализовать ленту: папка с WAV по дорожкам + index.json."""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(outdir, "tape_" + stamp)
        os.makedirs(folder, exist_ok=True)

        paths = {}
        rows = {}
        for t, data in tape["data"].items():
            path = os.path.join(folder, "track_%02d.wav" % t)
            wav_write(path, bytes(data), self.fs, 2)
            vals = struct.unpack("<%df" % (len(data) // 4), bytes(data))
            peak = max(abs(v) for v in vals) if vals else 0.0
            rms = (sum(v * v for v in vals) / len(vals)) ** 0.5 if vals else 0.0
            paths[t] = path
            rows[t] = {"seconds": round(len(data) / 8 / self.fs, 3),
                       "rms": round(rms, 5), "peak": round(peak, 4)}

        index = {"created": stamp, "flp": flp, "frames": tape["frames"],
                 "rate": self.fs, "blocks": tape["blocks"],
                 "start_beats": tape.get("start_beats"),
                 "seconds": tape.get("seconds"),
                 "tracks": rows,
                 "provenance": self.provenance(tape, flp)[:400]}
        with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)

        self._prune(outdir, keep)
        return {"dir": folder, "tracks": rows, "index": os.path.join(folder, "index.json")}

    @staticmethod
    def _prune(outdir: str, keep: int) -> None:
        try:
            items = sorted(d for d in os.listdir(outdir) if d.startswith("tape_"))
        except OSError:
            return
        for old in items[:-keep]:
            path = os.path.join(outdir, old)
            for root, _dirs, files in os.walk(path, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass
                try:
                    os.rmdir(root)
                except OSError:
                    pass

    def close(self) -> None:
        if self._armed:
            try:
                self.script.exports_sync.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.session.detach()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--tracks", default="")
    ap.add_argument("--outdir", default=os.path.join(os.environ.get("TEMP", "."), "tape"))
    ap.add_argument("--flp", default=None)
    ap.add_argument("--keep", type=int, default=5)
    a = ap.parse_args(argv)

    tracks = None
    if a.tracks:
        tracks = []
        for part in a.tracks.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                tracks.extend(range(int(lo), int(hi) + 1))
            elif part.strip():
                tracks.append(int(part))

    t = Tape()
    try:
        res = t.record(a.seconds, tracks)
        if res.get("error"):
            print("ошибка:", res["error"])
            return 3
        print("лента: %.2f с, блоков %d, дорожек %d"
              % (res["seconds"], res["blocks"], len(res["order"])))
        out = t.bundle(res, a.outdir, a.flp, a.keep)
        print("комплект:", out["dir"])
        for k, v in list(out["tracks"].items())[:6]:
            print("   дорожка %-3d %s" % (k, v))
        return 0
    finally:
        t.close()


if __name__ == "__main__":
    raise SystemExit(main())
