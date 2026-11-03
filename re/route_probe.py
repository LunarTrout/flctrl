"""route_probe.py — ищем метку блока в объекте route.

Печатает поля [route+0x40..0x60] на первых вызовах FE+0x82F0B0 для одной дорожки:
что инкрементится ровно раз в блок — то и будем использовать для дедупа.

    python route_probe.py --track 1 --calls 40
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BASE = r"C:\Users\epicc\mcp\flstudio-lib"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "flctrl"))

JS = r"""
'use strict';
const fe = Process.getModuleByName('FLEngine_x64.dll');
const S = {on: false, rows: [], want: 1, limit: 40};
rpc.exports = {
  setup: function (track, limit) {
    S.want = track; S.limit = limit;
    Interceptor.attach(fe.base.add(0x82F0B0), {
      onEnter: function () {
        if (!S.on || S.rows.length >= S.limit) return;
        try {
          const r = ptr(this.context.rcx);
          const t = r.add(0x38).readPointer();
          const idx = t.add(0x160).readU32();
          if (idx !== S.want) return;
          const row = {idx: idx, n: r.add(0x48).readU32()};
          for (const off of [0x40, 0x44, 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x60]) {
            row['o' + off.toString(16)] = r.add(off).readU32();
          }
          row.buf = r.add(8).readPointer().toString();
          S.rows.push(row);
        } catch (e) {}
      }
    });
    return true;
  },
  begin: function () { S.on = true; S.rows = []; return true; },
  dump: function () { S.on = false; return S.rows; }
};
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=int, default=1)
    ap.add_argument("--calls", type=int, default=40)
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()

    import frida

    import live
    import rt

    dev = frida.get_local_device()
    pid = next((p.pid for p in dev.enumerate_processes() if p.name.lower() == "fl64.exe"), None)
    if not pid:
        print("FL не запущена")
        return 2
    session = rt._attach(pid)
    script = session.create_script(JS)
    script.load()
    script.exports_sync.setup(a.track, a.calls)

    live._bridge("transport.stop()", timeout=4)
    time.sleep(0.2)
    script.exports_sync.begin()
    live._bridge("transport.setSongPos(0.30)", timeout=4)
    live._bridge("transport.start()", timeout=4)
    time.sleep(a.seconds)
    live._bridge("transport.stop()", timeout=4)
    rows = script.exports_sync.dump()

    print("вызовов записано:", len(rows))
    cols = [c for c in (rows[0].keys() if rows else []) if c.startswith("o")]
    print("%4s %6s %10s %s" % ("#", "n", "buf", " ".join("%6s" % c for c in cols)))
    for i, r in enumerate(rows[:a.calls]):
        print("%4d %6d %10s %s" % (i, r["n"], r["buf"][-6:],
                                   " ".join("%6d" % r[c] for c in cols)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
