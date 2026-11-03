"""stream.py — потоковая обработка живого FL: агрегаты вместо хранения звука.

Идея: блоки читаются из памяти FL на опросе, каждый сразу превращается в строку
картины (время × дорожка × метрика), звук держится только коротким кольцом для
того, что без сэмплов не считается. Файлов не пишем вообще.

Сырьё для сравнения: 32 дорожки × 44.1 кГц × 8 байт = 11.3 МБ/с (4 ГБ на трек),
а картина — десятки мегабайт. Поэтому хранить звук целиком не нужно.

    python -m flctrl.stream --seconds 5 --tracks 0-15
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time

import frida

try:
    from . import incr
except ImportError:          # запуск как отдельного модуля (sys.path = flctrl)
    import incr

JS = r"""
'use strict';
rpc.exports.meta = function () {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  return { tracks: fe.base.add(0x10A9850).readPointer().readU32(),
           frames: fe.base.add(0x10AA730).readPointer().readU32() };
};

let exTracks = [], exData = {}, exListener = null, exNotes = [];

rpc.exports.exactArm = function (tracks) {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  exTracks = tracks.slice();
  exData = {};
  for (const t of exTracks) exData[t] = { sum: 0, sumsq: 0, peak: 0, n: 0, blocks: 0 };
  try {
    exListener = Interceptor.attach(fe.base.add(0x78F624), {
      onEnter() {
        try {
          const arr = fe.base.add(0x10A7EB0).readPointer();
          const count = fe.base.add(0x10A9850).readPointer().readU32();
          const frames = fe.base.add(0x10AA730).readPointer().readU32();
          for (let i = 0; i < exTracks.length; i++) {
            const t = exTracks[i];
            if (t >= count) continue;
            const p = arr.add(t * 0x1474).add(0x34).readPointer();
            if (p.isNull()) continue;
            const v = new Float32Array(p.readByteArray(frames * 8));
            const d = exData[t];
            for (let k = 0; k < v.length; k++) {
              const x = v[k];
              if (x !== x) continue;
              d.sum += x; d.sumsq += x * x;
              const ax = x < 0 ? -x : x;
              if (ax > d.peak) d.peak = ax;
              d.n++;
            }
            d.blocks++;
          }
        } catch (e) { exNotes.push(String(e)); }
      }
    });
  } catch (e) { return { error: String(e) }; }
  return { armed: exTracks.length };
};

rpc.exports.exactRead = function () {
  const out = {};
  for (const t in exData) out[t] = { sum: exData[t].sum, sumsq: exData[t].sumsq,
                                     peak: exData[t].peak, n: exData[t].n,
                                     blocks: exData[t].blocks };
  return { data: out, notes: exNotes };
};

rpc.exports.exactStop = function () {
  if (exListener) { try { exListener.detach(); } catch (e) {} exListener = null; }
  return true;
};

rpc.exports.pump = function (tracks) {
  // блоки уходят бинарём через send(ArrayBuffer): вдвое меньше, без hex-строк
  try {
    const fe = Process.getModuleByName('FLEngine_x64.dll');
    const arr = fe.base.add(0x10A7EB0).readPointer();
    const count = fe.base.add(0x10A9850).readPointer().readU32();
    const frames = fe.base.add(0x10AA730).readPointer().readU32();
    for (const t of tracks) {
      if (t >= count) continue;
      const p = arr.add(t * 0x1474).add(0x34).readPointer();
      if (p.isNull()) continue;
      const buf = p.readByteArray(frames * 8);
      send({ kind: 'block', track: t, frames: frames }, buf);
    }
    return { frames: frames, count: count };
  } catch (e) { return { error: String(e) }; }
};

rpc.exports.blocks = function (tracks) {
  // один вызов на круг: все дорожки сразу, hex-ом
  try {
    const fe = Process.getModuleByName('FLEngine_x64.dll');
    const arr = fe.base.add(0x10A7EB0).readPointer();
    const count = fe.base.add(0x10A9850).readPointer().readU32();
    const frames = fe.base.add(0x10AA730).readPointer().readU32();
    const out = [];
    for (const t of tracks) {
      if (t >= count) continue;
      const p = arr.add(t * 0x1474).add(0x34).readPointer();
      if (p.isNull()) { out.push([t, '']); continue; }
      const u = new Uint8Array(p.readByteArray(frames * 8));
      let hex = '';
      for (let i = 0; i < u.length; i++) hex += ('0' + u[i].toString(16)).slice(-2);
      out.push([t, hex]);
    }
    return { frames: frames, count: count, blocks: out };
  } catch (e) { return { error: String(e) }; }
};
"""

BANDS = [(20, 60), (60, 120), (120, 250), (250, 500), (500, 1000), (1000, 2000),
         (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000)]


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


def _bridge(expr, default=None):
    lib = os.path.dirname(os.path.abspath(__file__))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import fl_lib
        return fl_lib._State._live(expr, attempts=1)
    except Exception:  # noqa: BLE001
        return default


class Stream:
    """Поток: блоки → агрегаты. Звук не хранится, кроме короткого кольца."""

    def __init__(self, tracks: list[int] | None = None, ring_seconds: float = 2.0):
        self.pid = pid_of()
        if not self.pid:
            raise RuntimeError("FL не запущена")
        self.session = frida.attach(self.pid)
        self.script = self.session.create_script(JS)
        self.script.load()
        meta = self.script.exports_sync.meta()
        self.count = int(meta["tracks"])
        self.frames = int(meta["frames"]) or 256
        self.fs = 44100
        self.tracks = [t for t in (tracks or range(self.count)) if t < self.count]
        self.ring_seconds = ring_seconds
        self.meters: dict[int, dict] = {}
        self.ring: list[dict] = []
        self._inbox: list[tuple[int, bytes, int]] = []
        self.script.on('message', self._on_message)
        self.rows: list[dict] = []
        self._start_beats = None
        self._start_time = None

    def _on_message(self, message, data):
        if message.get("type") == "send":
            p = message.get("payload") or {}
            if p.get("kind") == "block" and data:
                self._inbox.append((int(p["track"]), bytes(data), int(p["frames"])))

    def close(self) -> None:
        try:
            self.session.detach()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ поток
    def run(self, seconds: float = 5.0, rate_hz: float = 30.0,
            exact_tracks: list[int] | None = None) -> dict:
        import numpy as np

        self._start_beats = _bridge("float(transport.getSongPos(1))", 0.0)
        self._start_time = time.time()
        playing = bool(_bridge("transport.isPlaying()", 0))
        if exact_tracks:
            self.exact_tracks = [int(t) for t in exact_tracks]
            self.script.exports_sync.exact_arm(self.exact_tracks)
        else:
            self.exact_tracks = []
        period = 1.0 / max(rate_hz, 1.0)
        end = self._start_time + seconds
        rounds = 0

        while time.time() < end:
            meta = self.script.exports_sync.pump(self.tracks)
            if not meta or meta.get("error"):
                time.sleep(period)
                continue
            frames = int(meta["frames"]) or self.frames
            t_now = time.time() - self._start_time
            posted, self._inbox = self._inbox, []
            got = {}
            for track, buf, nf in posted:
                got[track] = buf

            # кольцо: сырьё последних секунд (для транзиентов/формы волны)
            self.ring.append({"t": t_now, "blocks": got})
            while self.ring and t_now - self.ring[0]["t"] > self.ring_seconds:
                self.ring.pop(0)

            row = {"t": round(t_now, 3), "tracks": {}}
            for t, raw in got.items():
                if not raw or len(raw) % 8:
                    continue
                frame = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)
                if frame.size == 0:
                    continue
                m = self.meters.setdefault(t, {
                    "rms": incr.RmsMeter(self.fs),
                    "band": incr.BandMeter(self.fs),
                    "stereo": incr.StereoMeter(),
                })
                m["rms"].feed(frame)
                m["band"].feed(frame)
                m["stereo"].feed(frame)
                r = m["rms"].value()
                b = m["band"].value()
                row["tracks"][t] = {"rms": round(r["rms"], 5),
                                    "peak": round(r["peak"], 4),
                                    "bands": b["bands"]}
            if self.exact_tracks:
                ex = self.script.exports_sync.exact_read()
                for t, d in (ex.get("data") or {}).items():
                    n = d["n"] or 1
                    row.setdefault("exact", {})[int(t)] = {
                        "rms": round((d["sumsq"] / n) ** 0.5, 5),
                        "peak": round(d["peak"], 4),
                        "blocks": d["blocks"], "samples": d["n"]}
            self.rows.append(row)
            rounds += 1
            time.sleep(period)

        if exact_tracks:
            self.script.exports_sync.exact_stop()
        beats_per_round = (self.frames / 96.0) * (self.fs / self.frames) / max(rate_hz, 1)
        silent = [t for t in self.tracks
                  if not any(r["tracks"].get(t, {}).get("rms") for r in self.rows)]
        out = {"rounds": rounds, "tracks": self.tracks, "frames": frames,
               "start_beats": self._start_beats, "seconds": round(seconds, 2),
               "row_seconds": round(seconds / max(rounds, 1), 4),
               "playing_at_start": playing,
               "silent_tracks": silent,
               "coverage": round(rounds * frames / max(seconds * self.fs, 1e-9), 3)}
        if not playing:
            out["note"] = ("транспорт стоял: захвачена тишина. Запусти воспроизведение "
                           "(transport.start) и повтори — инструмент сам ничего не запускает")
        return out

    # --------------------------------------------------------------- картина
    def summary(self) -> dict:
        """Сводка: по дорожке средние rms/пик и средние полосы."""
        if not self.rows:
            return {}
        out = {}
        for t in self.tracks:
            vals = [r["tracks"][t] for r in self.rows if t in r["tracks"]]
            if not vals:
                continue
            rms = sum(v["rms"] for v in vals) / len(vals)
            peak = max(v["peak"] for v in vals)
            bands = {}
            for lo, _hi in BANDS:
                bands[lo] = round(sum(v["bands"][lo] for v in vals) / len(vals), 2)
            out[t] = {"rms": round(rms, 5), "peak": round(peak, 4), "bands": bands}
        return out

    def export(self, path: str) -> dict:
        """Сохранить картину (метрики, без звука) — по явной команде."""
        import json
        data = {"frames": self.frames, "rate": self.fs,
                "start_beats": self._start_beats,
                "tracks": self.tracks, "rows": self.rows,
                "summary": self.summary()}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return {"path": path, "rows": len(self.rows),
                "size_kb": round(os.path.getsize(path) / 1024, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--tracks", default="")
    ap.add_argument("--rate", type=float, default=30.0)
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

    with Stream(tracks) as st:
        res = st.run(a.seconds, a.rate)
        print("кругов: %d, строк: %d, дорожек: %d, кадров: %d"
              % (res["rounds"], len(st.rows), len(st.tracks), res["frames"]))
        summ = st.summary()
        rows = sorted(summ.items(), key=lambda kv: -kv[1]["rms"])[:8]
        print("\nдорожка | rms | пик | полосы (дБ, 20/120/1к/8к)")
        for t, v in rows:
            b = v["bands"]
            print("%7d | %.5f | %.4f | %6.1f %6.1f %6.1f %6.1f"
                  % (t, v["rms"], v["peak"], b[20], b[120], b[1000], b[8000]))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
