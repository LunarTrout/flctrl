"""ring.py — точный захват: кольцо в памяти FL, хук снимает себя сам.

Почему прошлые попытки вешали FL: во время захвата мы дёргали RPC (info/drain), и
JS-поток не успевал обслуживать вызовы. Теперь во время захвата не делается НИ
ОДНОГО RPC — хук копит блоки и через заданное время снимается сам, после чего
данные забираются кусками.

    python -m flctrl.ring --seconds 2 --tracks 0
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
let ring = null, cap = 0, wpos = 0, frames = 0, per = 0;
let order = [], listener = null, blocks = 0, running = false;
let notes = [], deadline = 0;

rpc.exports.arm = function (tracks, mb, seconds) {
  const fe = Process.getModuleByName('FLEngine_x64.dll');
  cap = (mb | 0) * 1024 * 1024;
  ring = Memory.alloc(cap);
  order = tracks.slice();
  wpos = 0; blocks = 0; notes = []; running = true;
  deadline = Date.now() + seconds * 1000;
  try {
    listener = Interceptor.attach(fe.base.add(0x78F624), {
      onEnter() {
        if (!running) return;
        if (Date.now() > deadline) { running = false; try { listener.detach(); } catch (e) {} return; }
        try {
          const arr = fe.base.add(0x10A7EB0).readPointer();
          const count = fe.base.add(0x10A9850).readPointer().readU32();
          frames = fe.base.add(0x10AA730).readPointer().readU32();
          per = frames * 8;
          const need = per * order.length;
          if (wpos + need > cap) { running = false; try { listener.detach(); } catch (e) {} return; }
          for (let i = 0; i < order.length; i++) {
            const t = order[i];
            if (t >= count) continue;
            const p = arr.add(t * 0x1474).add(0x34).readPointer();
            if (p.isNull()) continue;
            Memory.copy(ring.add(wpos + i * per), p, per);
          }
          wpos += need;
          blocks++;
        } catch (e) { notes.push(String(e)); }
      }
    });
  } catch (e) { running = false; return { error: String(e) }; }
  return { armed: true, cap: cap, deadline_ms: seconds * 1000 };
};

rpc.exports.info = function () {
  return { write: wpos, frames: frames, per: per, order: order, blocks: blocks,
           running: running, cap: cap, notes: notes };
};

rpc.exports.drain = function (offset, len) {
  const n = Math.min(len, wpos - offset);
  if (n <= 0) return '';
  const u = new Uint8Array(ring.add(offset).readByteArray(n));
  let hex = '';
  for (let i = 0; i < u.length; i++) hex += ('0' + u[i].toString(16)).slice(-2);
  return hex;
};

rpc.exports.stop = function () {
  running = false;
  if (listener) { try { listener.detach(); } catch (e) {} listener = null; }
  return { write: wpos, blocks: blocks };
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


class Ring:
    """Кольцо в памяти FL: захват без RPC, чтение после самоснятия хука."""

    def __init__(self, pid: int | None = None):
        self.pid = pid or pid_of()
        if not self.pid:
            raise RuntimeError("FL не запущена")
        self.session = frida.attach(self.pid)
        self.script = self.session.create_script(JS)
        self.script.load()

    def capture(self, tracks: list[int], seconds: float, mb: int = 128) -> dict:
        res = self.script.exports_sync.arm(tracks, mb, seconds)
        if isinstance(res, dict) and res.get("error"):
            return {"error": res["error"]}
        time.sleep(seconds + 0.4)                 # RPC не трогаем совсем
        info = self.script.exports_sync.info()
        data = bytearray()
        write = int(info["write"])
        pos = 0
        while pos < write:
            n = min(262144, write - pos)
            hexs = self.script.exports_sync.drain(pos, n)
            if not hexs:
                break
            data.extend(bytes.fromhex(hexs))
            pos += n
        self.script.exports_sync.stop()
        return {"info": info, "data": bytes(data)}

    def close(self) -> None:
        try:
            self.session.detach()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--tracks", default="0")
    ap.add_argument("--mb", type=int, default=128)
    a = ap.parse_args(argv)
    tracks = []
    for part in a.tracks.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            tracks.extend(range(int(lo), int(hi) + 1))
        elif part.strip():
            tracks.append(int(part))

    r = Ring()
    try:
        res = r.capture(tracks, a.seconds, a.mb)
        if res.get("error"):
            print("ошибка:", res["error"])
            return 3
        info = res["info"]
        per_block = int(info["per"]) * len(info["order"])
        blocks = len(res["data"]) // per_block if per_block else 0
        print("блоков %d | кадров %d | дорожек %d | %.2f с аудио | с/блок %.1f"
              % (blocks, info["frames"], len(info["order"]), blocks * info["frames"] / 44100.0,
                 blocks / max(a.seconds, 1e-9)))
        return 0
    finally:
        r.close()


if __name__ == "__main__":
    raise SystemExit(main())
