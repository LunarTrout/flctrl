"""rt_arena.py — лёгкий захват: арена в памяти процесса + memcpy, без аллокаций в хуке.

Идея: на аудиопотоке только `memcpy` в заранее выделенный буфер. Всё остальное —
чтение и разбор — делает Python после съёма.

Ключи:
  o<obj>  — канальный путь (0x849287, доказан на demo2)
  t<idx>  — route-путь (FE+0x82F0B0, [route+8], индекс из [track+0x160])
  p<idx>  — per-track PDC (0x85250C, ecx = индекс дорожки)

    python rt_arena.py --seconds 10 --start 0.30
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time

import numpy as np

BASE = r"C:\Users\epicc\mcp\flstudio-lib"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "flctrl"))

JS = r"""
'use strict';
const fe = Process.getModuleByName('FLEngine_x64.dll');
const chunkPtr = fe.base.add(0x10AC2B0).readPointer();
const CAP = 44100 * 2 * 4 * 30;            // 30 секунд стерео на ключ
let memcpy = null;
try { memcpy = new NativeFunction(Module.getExportByName('msvcrt.dll', 'memcpy'), 'pointer', ['pointer', 'pointer', 'ulong']); }
catch (e1) {
  try { memcpy = new NativeFunction(Module.getExportByName('ucrtbase.dll', 'memcpy'), 'pointer', ['pointer', 'pointer', 'ulong']); }
  catch (e2) { memcpy = null; }
}
const S = {on: false, arena: {}, mask: null, calls: 0, bad: 0};

function chunkN() { try { const n = chunkPtr.readU32(); return (n > 0 && n < 8192) ? n : 0; } catch (e) { return 0; } }

function slot(key) {
  let a = S.arena[key];
  if (!a) { a = S.arena[key] = {ptr: Memory.alloc(CAP), pos: 0}; }
  return a;
}

function sig(src, n) {
  try {
    let s = 0.0;
    const step = Math.max(1, Math.floor(n / 16));
    for (let i = 0; i < n; i += step) s += src.add(i * 8).readFloat();
    return s.toFixed(6) + ':' + n;
  } catch (e) { return 'x'; }
}

function push(key, src, n) {
  const bytes = n * 8;
  const a = slot(key);
  if (a.pos + bytes > CAP) return;         // переполнение — просто перестаём писать
  const sg = sig(src, n);
  if (a.last === sg) return;               // тот же буфер уже скопирован в этом блоке
  a.last = sg;

  try {
    if (memcpy) memcpy(a.ptr.add(a.pos), src, bytes);
    else a.ptr.add(a.pos).writeByteArray(src.readByteArray(bytes));
    a.pos += bytes; S.calls++;
  } catch (e) { S.bad++; }
}

function allowed(idx) {
  if (S.mask === null) return true;
  return S.mask.indexOf(idx) >= 0;
}

rpc.exports = {
  setup: function () {
    const errs = [];
    try {
      // канальный путь
      Interceptor.attach(fe.base.add(0x849287), { onEnter: function () {
        if (!S.on) return;
        const n = chunkN(); if (!n) return;
        let obj, buf;
        try { obj = this.context.rbp.add(0x38).readPointer(); } catch (e) { return; }
        if (obj.isNull()) return;
        try { buf = obj.add(0x16C).readPointer(); } catch (e) { return; }
        if (buf.isNull()) return;
        push('o' + obj.toString(), buf, n);
      }});
      // route-путь: FE+0x82F0B0, на выходе [route+8] уже обработан FX
      Interceptor.attach(fe.base.add(0x82F0B0), {
        onEnter: function () { if (!S.on) return; try { this.route = ptr(this.context.rcx); } catch (e) { this.route = null; } },
        onLeave: function () {
          if (!S.on || !this.route) return;
          try {
            const route = this.route;
            const track = route.add(0x38).readPointer();
            const idx = track.add(0x160).readU32();
            if (idx > 512 || !allowed(idx)) return;
            const buf = route.add(8).readPointer();
            let n = route.add(0x48).readU32();
            if (!n || n > 8192) n = chunkN();
            if (!n || buf.isNull()) return;
            // route вызывается три раза за блок (сухой/посыл/…): берём только одну полосу
            const lane = route.add(0x40).readPointer().toString();
            const key = 't' + idx;
            const a = slot(key);
            if (!a.lane) a.lane = lane;
            if (lane !== a.lane) return;
            push(key, buf, n);
          } catch (e) { S.bad++; }
        }
      });
      // per-track PDC
      Interceptor.attach(fe.base.add(0x8524A0), {
        onEnter: function () { if (!S.on) return; this.idx = this.context.ecx >>> 0; },
        onLeave: function () {
          if (!S.on || this.idx === undefined) return;
          try {
            const idx = this.idx;
            if (idx > 512 || !allowed(idx)) return;
            const arr = fe.base.add(0x10A7EB0).readPointer();
            const track = arr.add(idx * 0x1474);
            const host = track.add(0x28C).readPointer();
            if (host.isNull()) return;
            const buf = host.add(0x48).readPointer();
            if (buf.isNull()) return;
            let n = chunkN();
            if (!n) return;
            push('p' + idx, buf, n);
          } catch (e) { S.bad++; }
        }
      });
    } catch (e) { errs.push('install: ' + e); }
    return {ok: errs.length === 0, errors: errs};
  },
  begin: function (mask) {
    S.on = true; S.arena = {}; S.calls = 0; S.bad = 0;
    S.mask = (mask && mask.length) ? mask : null;
    return true;
  },
  off: function () { S.on = false; return true; },
  info: function () {
    const out = [];
    for (const k in S.arena) out.push({key: k, bytes: S.arena[k].pos});
    return {calls: S.calls, bad: S.bad, items: out};
  },
  drain: function (key, off, len) {
    const a = S.arena[key];
    if (!a) return 0;
    const n = Math.min(len, a.pos - off);
    if (n <= 0) return 0;
    send({t: 'chunk', key: key, off: off}, a.ptr.add(off).readByteArray(n));
    return n;
  }
};
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--start", type=float, default=0.30)
    ap.add_argument("--wake", type=float, default=2.0)
    ap.add_argument("--out", default=r"C:\Users\epicc\Desktop\PROJECTS\vers1\_rt_take")
    a = ap.parse_args()

    import frida

    import live
    import rt

    dev = frida.get_local_device()
    pid = next((p.pid for p in dev.enumerate_processes() if p.name.lower() == "fl64.exe"), None)
    if not pid:
        print("FL не запущена")
        return 2

    t0 = time.time()
    session = rt._attach(pid)
    script = session.create_script(JS)
    chunks: dict = {}

    def on_msg(message, data):
        if message.get("type") == "send" and isinstance(message.get("payload"), dict):
            if message["payload"].get("t") == "chunk" and data:
                chunks.setdefault(message["payload"]["key"], []).append(bytes(data))

    script.on("message", on_msg)
    script.load()
    st = script.exports_sync.setup()
    print("хуки:", st, "| %.1f с" % (time.time() - t0), flush=True)

    print("wake:", rt.wake(track=1, seconds=a.wake), flush=True)

    script.exports_sync.begin(None)
    live._bridge("transport.stop()", timeout=5)
    time.sleep(0.3)
    live._bridge("transport.setSongPos(%r)" % a.start, timeout=5)
    live._bridge("transport.start()", timeout=5)
    time.sleep(a.seconds)
    live._bridge("transport.stop()", timeout=5)
    info = script.exports_sync.info()
    print("вызовов %d, ключей %d, bad %d | %.1f с" % (
        info["calls"], len(info["items"]), info["bad"], time.time() - t0), flush=True)

    os.makedirs(a.out, exist_ok=True)
    import soundfile as sf
    kept = 0
    for item in sorted(info["items"], key=lambda x: -x["bytes"]):
        key, total = item["key"], item["bytes"]
        if total < 8192:
            continue
        parts, off = [], 0
        chunks.pop(key, None)
        while off < total:
            n = min(1 << 20, total - off)
            got = script.exports_sync.drain(key, off, n)
            if not got:
                break
            off += got
        raw = b"".join(chunks.get(key, []))
        if raw:
            parts.append(np.frombuffer(raw, dtype=np.float32))
        x = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        x = x[: x.size - (x.size % 2)].reshape(-1, 2)
        if x.shape[0] < 2048:
            continue
        rms = float(np.sqrt((x ** 2).mean()))
        if rms < 1e-5:
            continue
        p = os.path.join(a.out, "arena_%s.wav" % key.replace("0x", ""))
        sf.write(p, x, 44100, subtype="FLOAT")
        kept += 1
        print("  %-10s rms %.4f peak %.2f  %.2f с -> %s" % (
            key, rms, float(np.abs(x).max()), x.shape[0] / 44100, os.path.basename(p)), flush=True)

    script.exports_sync.off()
    print("готово: %d файлов, всего %.1f с" % (kept, time.time() - t0), flush=True)
    return 0 if kept else 3


if __name__ == "__main__":
    raise SystemExit(main())
