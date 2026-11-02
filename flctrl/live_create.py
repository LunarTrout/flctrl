"""fl_live_insert.py -- живой контроль над FL Studio через Frida.

Зачем: у FL нет API для вставки/удаления плагина, создания канала и клипа.
Всё это делает только UI. Здесь те же действия выполняются вызовом внутренних
функций FLEngine_x64.dll из главного (UI) потока процесса -- без кликов,
без отладчика (протектор FL убивает отладчики, Frida -- нет).

Запуск FL -- ТОЛЬКО через safe_fl.py (тестовый проект small.flp).

Режимы:
    python fl_live_insert.py daemon [--flp ...] [--port 9790]   # стенд для опытов
    python fl_live_insert.py state                              # состояние через мост
    python fl_live_insert.py ...                                # операции (см. ниже)

Протокол демона: одна строка JSON на запрос, одна на ответ.
    {"op":"js","code":"..."}                      -- выполнить JS в агенте
    {"op":"call","rva":"0xE268B0","args":[...]}    -- вызов на UI-потоке
    {"op":"dump","addr":"0x...","len":256}
    {"op":"scanptr","val":"0x..."}                 -- кто ссылается на указатель
    {"op":"state"}                                 -- plugins/channels через мост 9787
    {"op":"log","n":40}
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FL = r"C:\Programs\Fl Studio 2025\FL64.exe"
SMALL = r"C:\Users\epicc\tools\fltest\small.flp"
LOG = os.path.join(HERE, "_live.log")
BRIDGE = ("127.0.0.1", 9787)

RVA = {
    "loadWrapper": 0xE268B0,     # LoadLibraryExW(Fruity Wrapper) + CreatePlugInstance
    "pluginCreate": 0xE28E70,    # РЕАЛЬНЫЙ загрузчик: (RCX=объект слота, RDX=путь) -> ставит +0x64=2
    "pluginUnload": 0xE29570,    # РЕАЛЬНОЕ удаление: (RCX=объект слота) -> FreeLibrary, +0x64=-1
    "callSite": 0xE28F15,        # вызов loadWrapper из pluginCreate
    "uiInsert": 0x9B6BE0,        # UI: insert/replace effect plugin
    "uiInsertA": 0x9B6430,
    "uiInsertB": 0x9B7270,
    "factory": 0x6FA0B0,         # фабрика TVST3Plugin
    "loadPlugin3": 0x6E3200,     # TVST3Plugin.LoadPlugin
    "cmdInterp": 0xA7DF40,       # интерпретатор именованных команд (undo-слой)
}

# объект слота (класс с vtable FE+0xDB4960), 0x1C0 байт:
#   +0x30 интерфейс плагина, +0x38/+0x40/+0x48 подобъекты,
#   +0x50 имя слота (Delphi UnicodeString), +0x64 состояние (-1 пусто, 2 загружен),
#   +0x68 число параметров, +0x80 HMODULE, +0x88 путь к DLL (Delphi UnicodeString),
#   +0x160 номер инсерта, +0x164 номер слота.
SLOT_VT_RVA = 0xDB4960

# --------------------------------------------------------------------- JS

JS = r"""
'use strict';
const RVA = %(rva)s;
let feBase = null, wrapBase = null, uiTid = 0, ready = false;
// Frida 17: статический Module.getExportByName убран -- берём экспорт у модуля
function exp(mod, name) {
  try { const m = Process.getModuleByName(mod); const p = m.getExportByName(name); if (p) return p; } catch (e) {}
  try { if (Module.getGlobalExportByName) return Module.getGlobalExportByName(name); } catch (e) {}
  throw new Error('no export ' + mod + '!' + name);
}
const _u32 = { fw: null, tid: null };
function findWindow(cls) {
  if (!_u32.fw) _u32.fw = new NativeFunction(exp('user32.dll', 'FindWindowW'), 'pointer', ['pointer', 'pointer']);
  return _u32.fw(Memory.allocUtf16String(cls), ptr(0));
}
function threadOfWindow(h) {
  if (!_u32.tid) _u32.tid = new NativeFunction(exp('user32.dll', 'GetWindowThreadProcessId'),
                                              'uint32', ['pointer', 'pointer']);
  const out = Memory.alloc(4);
  return _u32.tid(h, out);
}

function modOf(p) {
  try { const m = Process.findModuleByAddress(p); return m ? m.name + '+0x' + p.sub(m.base).toString(16) : null; }
  catch (e) { return null; }
}
function bt(ctx, n) {
  const out = [];
  try {
    const fr = Thread.backtrace(ctx, Backtracer.ACCURATE);
    for (let i = 0; i < Math.min(fr.length, n); i++) out.push(modOf(fr[i]) || fr[i].toString());
  } catch (e) { out.push('BT_ERR ' + e); }
  return out;
}
function readStr(p) {
  try { const s = p.readUtf16String(260); if (s) return s; } catch (e) {}
  try { const s = p.readAnsiString(260); if (s) return s; } catch (e) {}
  return null;
}
function baseOf(which) {
  if (which === 'wrap' || which === 'wrapper') return wrapBase;
  return feBase;
}
function resolve(rva, which) {
  const b = baseOf(which);
  if (!b) throw new Error('module ' + (which || 'fe') + ' not loaded');
  return b.add(parseInt(rva));
}

// ---------------- главный поток: очередь заданий в цикле сообщений
const jobs = [];
let pumping = false;
function uiThreadId() {
  try {
    const h = findWindow('TFruityLoopsMainForm');
    if (!h || h.isNull()) return 0;
    return threadOfWindow(h);
  } catch (e) { return 0; }
}
function drain() {
  if (!jobs.length) return;
  const j = jobs.shift();
  try { j.run(); } catch (e) { try { j.fail(e); } catch (e2) {} }
}
function ensurePump() {
  if (pumping) return true;
  try {
    Interceptor.attach(exp('user32.dll', 'PeekMessageW'), {
      onEnter() {
        if (!jobs.length) return;
        if (uiTid && Process.getCurrentThreadId() !== uiTid) return;
        drain();
      }
    });
    pumping = true;
  } catch (e) { return false; }
  return true;
}

function callAddr(addr, args, types) {
  const t = types && types.length ? types : args.map(() => 'pointer');
  const f = new NativeFunction(addr, 'pointer', t);
  const a = args.map(x => (typeof x === 'string' ? ptr(x) : ptr(x)));
  return f.apply(null, a);
}
function onMain(fn) {
  return new Promise((resolve, reject) => {
    jobs.push({ run() { try { resolve(fn()); } catch (e) { reject(String(e)); } },
                fail(e) { reject(String(e)); } });
  });
}

// ---------------- хуки
const hooked = {};
function installHookAddr(addr, name, depth) {
  const key = 'addr:' + addr;
  if (hooked[key]) return 'already';
  Interceptor.attach(addr, {
    onEnter(args) {
      const rec = { ev: 'hook', name: name, tid: Process.getCurrentThreadId(),
                    bt: bt(this.context, depth || 12), args: [], str: [], stack: [] };
      for (let i = 0; i < 6; i++) {
        let v = null; try { v = args[i]; } catch (e) {}
        rec.args.push(v && !v.isNull() ? v.toString() : null);
        rec.str.push(v && !v.isNull() ? readStr(v) : null);
      }
      for (let i = 0; i < 48; i++) {
        try {
          const q = this.context.rsp.add(0x28 + i * 8).readPointer();
          rec.stack.push(q.toString());
          if (rec.stack.length > 40) break;
        } catch (e) { break; }
      }
      try { rec.rsp = this.context.rsp.toString(); } catch (e) {}
      send(rec);
    },
    onLeave(ret) { send({ ev: 'hookret', name: name, ret: ret.toString() }); }
  });
  hooked[key] = true;
  return 'ok';
}
function installHook(rva, name, which, depth) {
  const key = (which || 'fe') + ':' + rva;
  if (hooked[key]) return 'already';
  const addr = resolve(rva, which);
  const r = installHookAddr(addr, name, depth);
  hooked[key] = true;
  return r;
}

// ---------------- отложенные хуки (модуль ещё не загружен)
const pending = [];
function tryPending() {
  for (let i = pending.length - 1; i >= 0; i--) {
    const p = pending[i];
    try {
      if (resolve(p.rva, p.which)) { installHook(p.rva, p.name, p.which, p.depth); pending.splice(i, 1); }
    } catch (e) {}
  }
}
// ---------------- загрузка модулей
function onModule(m) {
  const n = m.name.toLowerCase();
  if (n === 'flengine_x64.dll' && !feBase) {
    feBase = m.base;
    send({ ev: 'fe', base: feBase.toString() });
    // ставим ДО загрузки проекта, но вне колбэка загрузчика (иначе можно поймать дедлок)
    setTimeout(function () {
      [['0xE268B0', 'loadWrapper'], ['0xE28E70', 'pluginCreate'],
       ['0x9B6BE0', 'uiInsert'], ['0xE29450', 'newPluginHelper'],
       ['0xDBE5A0', 'makePluginObj'], ['0xDC3860', 'makeEffect'],
       ['0xDC1740', 'makeGenerator']].forEach(function (t) {
        try { installHook(t[0], t[1], 'fe', 20); }
        catch (e) { send({ ev: 'hookfail', rva: t[0], err: String(e) }); }
      });
    }, 30);
  }
  // любая DLL плагина (нативная или Fruity Wrapper) отдаёт CreatePlugInstance
  if (m.name.toLowerCase().indexOf('_x64.dll') > 0) {
    setTimeout(function () {
      try {
        const e2 = Process.findModuleByName(m.name);
        if (!e2) return;
        const ex = e2.getExportByName('CreatePlugInstance');
        if (ex) installHookAddr(ex, 'CreatePlugInstance@' + m.name, 22);
      } catch (e3) {}
    }, 40);
  }
  if (n.indexOf('fruity wrapper_x64') === 0 && !wrapBase) {
    wrapBase = m.base;
    send({ ev: 'wrap', base: wrapBase.toString() });
    tryPending();
  }
}
Process.attachModuleObserver({ onAdded(m) { onModule(m); } });
['FLEngine_x64.dll', 'Fruity Wrapper_x64.dll'].forEach(function (nm) {
  try { const m = Process.getModuleByName(nm); if (m) onModule(m); } catch (e) {}
});

// ---------------- слоты микшера: карта (insert, slot) -> объект
// Объекты слотов одного класса (vtable FE+0xDB4960), 0x1C0 байт, в каждом
// +0x160 = номер инсерта, +0x164 = номер слота. Блоки на 10 слотов аллоцируются
// отдельно, поэтому фиксированного шага между инсертами НЕТ -- только скан.
function buildSlotMap() {
  const FE = feBase || Process.getModuleByName('FLEngine_x64.dll').base;
  const pat = FE.add(%(vt)d).toMatchPattern();
  const m = {};
  let dup = 0;
  const ranges = Process.enumerateRanges('rw-');
  for (let i = 0; i < ranges.length; i++) {
    const r = ranges[i];
    if (r.size > 256 * 1024 * 1024) continue;
    try {
      const found = Memory.scanSync(r.base, r.size, pat);
      for (let j = 0; j < found.length; j++) {
        const p = found[j].address;
        let ins = -1, sl = -1;
        try { ins = p.add(0x160).readU32(); sl = p.add(0x164).readU32(); } catch (e) { continue; }
        if (sl > 9 || ins > 255) continue;
        const k = ins + ':' + sl;
        if (m[k]) { dup++; continue; }
        m[k] = p.toString();
      }
    } catch (e) {}
  }
  globalThis.__slotMap = m;
  globalThis.__slotDup = dup;
  return { n: Object.keys(m).length, dup: dup };
}
function slotPtr(ins, sl) {
  if (!globalThis.__slotMap) buildSlotMap();
  const k = ins + ':' + sl;
  const v = globalThis.__slotMap[k];
  if (!v) throw new Error('нет объекта слота ' + k + ' (карта устарела? force=1)');
  return ptr(v);
}
function slotInfo(ins, sl) {
  const p = slotPtr(ins, sl);
  const rec = { ins: ins, slot: sl, obj: p.toString() };
  rec.state = p.add(0x64).readU32();
  rec.mod = p.add(0x80).readPointer().toString();
  rec.params = p.add(0x68).readU32();
  try {
    const nm = p.add(0x50).readPointer();
    if (!nm.isNull()) rec.name = nm.readUtf16String(80);
  } catch (e) {}
  try {
    const pt = p.add(0x88).readPointer();
    if (!pt.isNull()) rec.path = pt.readUtf16String(260);
  } catch (e) {}
  return rec;
}
// Delphi UnicodeString: [-12]=CodePage 1200, [-10]=ElemSize 2, [-8]=RefCnt, [-4]=Length
function mkDelphiStr(s) {
  globalThis.__strs = globalThis.__strs || [];
  const n = s.length;
  const buf = Memory.alloc(12 + (n + 1) * 2);
  buf.writeU16(1200);
  buf.add(2).writeU16(2);
  buf.add(4).writeU32(0x40000000);
  buf.add(8).writeU32(n);
  buf.add(12).writeUtf16String(s);
  globalThis.__strs.push(buf);
  return buf.add(12);
}

rpc.exports = {
  ping() { return { fe: feBase ? feBase.toString() : null, wrap: wrapBase ? wrapBase.toString() : null,
                    uiTid: uiTid }; },
  setuitid() { ensurePump(); uiTid = uiThreadId(); return uiTid; },
  hook(rva, name, which, depth) { return installHook(rva, name, which, depth); },
  hooklater(rva, name, which, depth) {
    pending.push({ rva: rva, name: name, which: which, depth: depth });
    tryPending();
    return pending.length;
  },
  hooks() { return Object.keys(hooked); },
  maincall(rva, args, which) {
    ensurePump();
    return onMain(() => callAddr(resolve(rva, which), args || []).toString());
  },
  slotmap() { const r = buildSlotMap(); return { n: r.n, dup: r.dup, map: globalThis.__slotMap }; },
  slotslot(ins, sl) { return slotPtr(ins, sl).toString(); },
  slotinfo(ins, sl) { return slotInfo(ins, sl); },
  mkdstr(s) { return mkDelphiStr(s).toString(); },
  calladdr(addr, args) { ensurePump(); return onMain(() => callAddr(ptr(addr), args || []).toString()); },
  dump(addr, len) {
    const b = new Uint8Array(ptr(addr).readByteArray(len));
    let s = '';
    for (const x of b) s += x.toString(16).padStart(2, '0');
    return s;
  },
  write(addr, hex) {
    const b = [];
    for (let i = 0; i < hex.length; i += 2) b.push(parseInt(hex.substr(i, 2), 16));
    ptr(addr).writeByteArray(b);
    return b.length;
  },
  readptr(addr) { try { return ptr(addr).readPointer().toString(); } catch (e) { return null; } },
  readu32(addr) { try { return ptr(addr).readU32(); } catch (e) { return null; } },
  writepointer(addr, val) { ptr(addr).writePointer(ptr(val)); return true; },
  scanptr(val, max) {
    const target = ptr(val);
    const hexs = target.toMatchPattern();
    const hits = [];
    const ranges = Process.enumerateRanges('rw-');
    let scanned = 0;
    for (const r of ranges) {
      if (max && scanned > max) break;
      if (r.size > 512 * 1024 * 1024) continue;
      try {
        const found = Memory.scanSync(r.base, r.size, hexs);
        for (const f of found) hits.push(f.address.toString());
      } catch (e) {}
      scanned += r.size;
    }
    return { hits: hits, scanned: scanned, ranges: ranges.length };
  },
  allocstr(s) { return Memory.allocUtf16String(s).toString(); },
  alloc(size) { return Memory.alloc(size).toString(); },
  // --- микшерные дорожки (RVA найдены статически по FLEngine_x64.dll 25.2.5) ---
  // 0x10A9850 -> qword -> u32  : число записей в массиве дорожек
  // 0x10A9D28 -> qword -> u32  : индекс выбранной дорожки
  // 0x10A7EB0 -> qword         : &tracks[0]   (шаг 0x1474)
  trackinfo() {
    const c = resolve(0x10A9850, 'fe').readPointer().readU32();
    const s = resolve(0x10A9D28, 'fe').readPointer().readU32();
    const a = resolve(0x10A7EB0, 'fe').readPointer();
    return { count: c, selected: s, arr: a.toString() };
  },
  // da7b30(index, count, flags): вставка count дорожек в позицию index
  trackadd(index, count, flags) {
    ensurePump();
    return onMain(() => {
      const before = resolve(0x10A9850, 'fe').readPointer().readU32();
      let a = index;
      if (a === undefined || a === null || a < 1)
        a = resolve(0x10A9D28, 'fe').readPointer().readU32();
      // мастер (0) и служебная запись (count-1) не годятся как позиция вставки
      if (a < 1 || a > before - 2) a = Math.max(1, before - 2);
      const r = callAddr(resolve(0xDA7B30, 'fe'), [a, count, flags]).toString();
      const after = resolve(0x10A9850, 'fe').readPointer().readU32();
      return { ret: r, index: a, count: count, flags: flags, before: before, after: after };
    });
  },
  // da86c0(int *list, lastIdx, flags): удаление дорожек из списка
  trackdel(index, flags) {
    ensurePump();
    return onMain(() => {
      const buf = Memory.alloc(8);
      buf.writeU32(index >>> 0);
      (globalThis.__keep = globalThis.__keep || []).push(buf);
      const before = resolve(0x10A9850, 'fe').readPointer().readU32();
      const r = callAddr(resolve(0xDA86C0, 'fe'), [buf, 0, flags]).toString();
      const after = resolve(0x10A9850, 'fe').readPointer().readU32();
      return { ret: r, index: index, flags: flags, before: before, after: after };
    });
  },
  evaljs(code) { return eval(code); },
  scanpat(pat, max) {
    const hits = [];
    const ranges = Process.enumerateRanges('rw-');
    let scanned = 0;
    for (const r of ranges) {
      if (max && scanned > max) break;
      if (r.size > 512 * 1024 * 1024) continue;
      try {
        const found = Memory.scanSync(r.base, r.size, pat);
        for (const f of found) hits.push(f.address.toString());
      } catch (e) {}
      scanned += r.size;
    }
    return { hits: hits, scanned: scanned, ranges: ranges.length };
  },
  ranges() {
    return Process.enumerateRanges('rw-').map(r => [r.base.toString(), r.size]);
  },
  threads() {
    return Process.enumerateThreads().map(t => [t.id, t.context ? t.context.pc.toString() : null]);
  }
};
ready = true;
send({ ev: 'ready', fe: feBase ? feBase.toString() : null });
""" % {"rva": json.dumps(RVA), "vt": SLOT_VT_RVA}


# ------------------------------------------------------------------ bridge

def bridge(expr, timeout=30):
    s = socket.create_connection(BRIDGE, timeout=timeout)
    s.sendall((json.dumps({"op": "call", "cmd": "exec", "params": {"expr": expr}}) + "\n").encode())
    buf = b""
    s.settimeout(timeout)
    while True:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            break
    s.close()
    r = json.loads(buf.decode("utf-8", "replace").strip())
    d = r.get("data") or {}
    if not d.get("ok"):
        raise RuntimeError(str(d.get("error") or r)[:300])
    return d.get("result")


def live_state() -> dict:
    return {
        "channels": bridge("channels.channelCount()"),
        "tracks": bridge("mixer.trackCount()"),
        "plugins": bridge("__import__('json').dumps([(i,s,str(plugins.getPluginName(i,s)))"
                          " for i in range(8) for s in range(10) if plugins.isValid(i,s)])"),
    }


def slot_valid(track: int, slot: int) -> bool:
    # мост отдаёт результат строкой ("0"/"1"), поэтому bool() напрямую врёт
    return int(bridge("plugins.isValid(%d,%d)" % (track, slot))) == 1


def slot_name(track: int, slot: int) -> str:
    try:
        return str(bridge("str(plugins.getPluginName(%d,%d))" % (track, slot)))
    except Exception:
        return ""


# ------------------------------------------------------------------ daemon

class Daemon:
    def __init__(self, flp: str, port: int, hooks: bool = True):
        import frida
        self.frida = frida
        self.flp = flp
        self.port = port
        self.log_path = LOG
        self.fh = open(self.log_path, "w", encoding="utf-8")
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.pid = None
        self.session = None
        self.script = None
        self.ready = threading.Event()
        self._slotmap: dict | None = None

    def start(self, timeout=90):
        import safe_fl
        safe_fl.TEST_PROJECT = self.flp
        self.pid = safe_fl.frida_spawn()
        self.session = self.frida.attach(self.pid)
        self.script = self.session.create_script(JS)
        self.script.on("message", self._on_message)
        self.script.load()
        self.frida.resume(self.pid)
        self.closing = False

        def _detached(*_a):
            print("FL отвалился, выходим", flush=True)
            if not getattr(self, "closing", False):
                os._exit(0)
        self.session.on("detached", _detached)
        if not self.ready.wait(timeout):
            print("агент не прислал ready", flush=True)
        # ui thread id
        for _ in range(60):
            try:
                tid = self.script.exports_sync.setuitid()
                if tid:
                    print("ui thread:", tid, flush=True)
                    break
            except Exception:
                pass
            time.sleep(1)
        print("FL pid=%d, лог: %s" % (self.pid, self.log_path), flush=True)

    def _on_message(self, msg, data):
        if msg.get("type") == "send":
            p = msg["payload"]
            self.fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            self.fh.flush()
            if p.get("ev") == "ready":
                self.ready.set()
        elif msg.get("type") == "error":
            line = json.dumps({"ev": "error", "desc": msg.get("description"),
                               "stack": msg.get("stack")}, ensure_ascii=False)
            self.fh.write(line + "\n")
            self.fh.flush()

    def call(self, rva, args=None, which="fe", timeout=25):
        fut = self.pool.submit(self.script.exports_sync.maincall, str(rva), args or [], which)
        return fut.result(timeout=timeout)

    def run_op(self, req: dict) -> dict:
        op = req.get("op", "ping")
        try:
            if op == "ping":
                return {"ok": True, "data": self.script.exports_sync.ping()}
            if op == "js":
                fut = self.pool.submit(self.script.exports_sync.evaljs, req["code"])
                return {"ok": True, "data": fut.result(timeout=req.get("timeout", 30))}
            if op == "call":
                r = self.call(req["rva"], req.get("args"), req.get("base", "fe"),
                              req.get("timeout", 25))
                return {"ok": True, "data": r}
            if op == "dump":
                fut = self.pool.submit(self.script.exports_sync.dump, req["addr"], req.get("len", 128))
                return {"ok": True, "data": fut.result(timeout=20)}
            if op == "readptr":
                fut = self.pool.submit(self.script.exports_sync.readptr, req["addr"])
                return {"ok": True, "data": fut.result(timeout=20)}
            if op == "scanptr":
                fut = self.pool.submit(self.script.exports_sync.scanptr, req["val"], req.get("max"))
                return {"ok": True, "data": fut.result(timeout=req.get("timeout", 120))}
            if op == "scanpat":
                fut = self.pool.submit(self.script.exports_sync.scanpat, req["pat"], req.get("max"))
                return {"ok": True, "data": fut.result(timeout=req.get("timeout", 120))}
            if op == "hooklater":
                r = self.script.exports_sync.hooklater(req["rva"], req.get("name", req["rva"]),
                                                       req.get("base", "fe"), req.get("depth", 12))
                return {"ok": True, "data": r}
            if op == "hook":
                r = self.script.exports_sync.hook(req["rva"], req.get("name", req["rva"]),
                                                  req.get("base", "fe"), req.get("depth", 12))
                return {"ok": True, "data": r}
            if op == "state":
                return {"ok": True, "data": live_state()}
            if op == "slots":
                tr = req.get("track")
                m = slot_map(self, force=True)
                out = {}
                for k in sorted(m, key=lambda x: (int(x.split(":")[0]), int(x.split(":")[1]))):
                    ins, sl = (int(x) for x in k.split(":"))
                    if tr is not None and ins != tr:
                        continue
                    try:
                        out[k] = slot_state(self, ins, sl)
                    except Exception as e:
                        out[k] = {"err": str(e)[:80]}
                return {"ok": True, "data": out}
            if op in ("insert", "delete", "replace"):
                tr, sl = int(req["track"]), int(req["slot"])
                st = slot_state(self, tr, sl)
                busy = st.get("state") != 0xFFFFFFFF
                res: dict = {"before": st}
                if op == "delete":
                    if not busy:
                        return {"ok": False, "error": "слот %d:%d уже пуст" % (tr, sl)}
                    res["ret"] = plugin_unload(self, tr, sl)
                elif op == "insert":
                    if busy and not req.get("force"):
                        return {"ok": False, "error": "слот %d:%d занят (%s) -- используй replace"
                                % (tr, sl, st.get("path"))}
                    path = resolve_plugin_path(req["plugin"])
                    res["path"] = path
                    res["ret"] = plugin_load(self, tr, sl, path)
                else:  # replace
                    if busy:
                        res["unload"] = plugin_unload(self, tr, sl)
                        time.sleep(0.3)
                    path = resolve_plugin_path(req["plugin"])
                    res["path"] = path
                    res["ret"] = plugin_load(self, tr, sl, path)
                # API FL видит изменение не мгновенно: слот обновляется на ближайшем
                # тике движка, поэтому читаем состояние с запасом ~1.2 с
                time.sleep(1.2)
                self._slotmap = None
                res["after"] = slot_state(self, tr, sl)
                try:
                    res["fl_valid"] = slot_valid(tr, sl)
                except Exception as e:
                    res["fl_valid"] = "ERR " + str(e)[:80]
                return {"ok": True, "data": res}
            if op in ("trackcount", "trackinfo"):
                info = self.script.exports_sync.trackinfo()
                if op == "trackcount":
                    return {"ok": True, "data": info.get("count")}
                return {"ok": True, "data": info}
            if op in ("trackadd", "trackdel"):
                idx = req.get("index")
                flg = int(req.get("flags", 7 if op == "trackadd" else 0x12))
                cur = self.script.exports_sync.trackinfo().get("count") or 0
                # 0 -- мастер, count-1 -- служебная запись "Current": их не трогаем
                if idx is not None:
                    idx = int(idx)
                    if idx < 1 or idx > max(1, cur - 2):
                        return {"ok": False,
                                "error": "index %d вне диапазона 1..%d (count=%d)"
                                         % (idx, max(1, cur - 2), cur)}
                if op == "trackadd":
                    if int(req.get("count", 1)) < 1 or cur + int(req.get("count", 1)) > 502:
                        return {"ok": False, "error": "не влезает: count=%d, просят +%d"
                                                      % (cur, int(req.get("count", 1)))}
                    fut = self.pool.submit(self.script.exports_sync.trackadd,
                                           idx, int(req.get("count", 1)), flg)
                else:
                    if idx is None:
                        return {"ok": False, "error": "index обязателен"}
                    fut = self.pool.submit(self.script.exports_sync.trackdel, idx, flg)
                res = fut.result(timeout=req.get("timeout", 90))
                time.sleep(1.0)          # движок применяет изменение на ближайшем тике
                try:
                    res["fl_trackCount"] = int(bridge("mixer.trackCount()"))
                except Exception as e:
                    res["fl_trackCount"] = "ERR " + str(e)[:60]
                return {"ok": True, "data": res}
            if op == "slot":
                return {"ok": True, "data": {"valid": slot_valid(req["track"], req["slot"]),
                                             "name": slot_name(req["track"], req["slot"])}}
            if op == "log":
                n = req.get("n", 40)
                with open(self.log_path, encoding="utf-8") as f:
                    lines = f.read().strip().splitlines()[-n:]
                return {"ok": True, "data": lines}
            return {"ok": False, "error": "unknown op %s" % op}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400])}

    def serve(self):
        import socketserver

        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if not line:
                    return
                try:
                    req = json.loads(line.decode("utf-8"))
                except Exception as e:
                    self.wfile.write((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
                    return
                resp = outer.run_op(req)
                self.wfile.write((json.dumps(resp, ensure_ascii=False, default=str) + "\n").encode())

        socketserver.ThreadingTCPServer.allow_reuse_address = False
        try:
            srv = socketserver.ThreadingTCPServer(("127.0.0.1", self.port), H)
        except OSError as e:
            print("порт %d занят (%s) -- похоже, уже работает другой демон; выходим"
                  % (self.port, e), flush=True)
            os._exit(3)
        srv.daemon_threads = True
        print("демон слушает 127.0.0.1:%d" % self.port, flush=True)
        srv.serve_forever()


def send(req: dict, port: int = 9790, timeout: float = 60) -> dict:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    s.settimeout(timeout)
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8", "replace"))


def daemon_alive(port: int = 9790) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def wait_bridge(timeout: float = 90) -> bool:
    """Дождаться, пока загрузится проект и мост 9787 начнёт отвечать."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            live_state()
            return True
        except Exception:
            time.sleep(2)
    return False


def hexdump(hexstr: str, base: int = 0) -> str:
    b = bytes.fromhex(hexstr)
    out = []
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        txt = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        out.append("%08x  %-47s  %s" % (base + i, " ".join("%02x" % x for x in chunk), txt))
    return "\n".join(out)


def strip_q(s) -> str:
    return str(s or "").strip().strip("'")


# --------------------------------------------------- живые insert / delete

FL_ROOT = r"C:\Programs\Fl Studio 2025"


def resolve_plugin_path(name: str) -> str:
    """Имя плагина -> путь к DLL. Полный путь отдаём как есть."""
    if name.lower().endswith(".dll"):
        return name
    base = os.path.join(FL_ROOT, "Plugins", "Fruity")
    cands = []
    for kind in ("Effects", "Generators"):
        d = os.path.join(base, kind)
        if not os.path.isdir(d):
            continue
        for sub in os.listdir(d):
            if sub.lower() == name.lower():
                cands.append(os.path.join(d, sub, sub + ".dll"))
    if not cands:
        raise RuntimeError("плагин %r не найден в %s (передай полный путь к .dll)" % (name, base))
    return cands[0]


def slot_map(dm: "Daemon", force: bool = True) -> dict:
    """Карта (insert, slot) -> адрес объекта слота. Строится сканом памяти."""
    if force or not dm._slotmap:
        r = dm.script.exports_sync.slotmap()
        dm._slotmap = r.get("map") or {}
        dm._slotmap_stats = {"n": r.get("n"), "dup": r.get("dup")}
    return dm._slotmap


def slot_ptr(dm: "Daemon", insert: int, slot: int) -> str:
    slot_map(dm, force=not getattr(dm, "_slotmap", None))
    return dm.script.exports_sync.slotslot(insert, slot)


def slot_state(dm: "Daemon", insert: int, slot: int) -> dict:
    return dm.script.exports_sync.slotinfo(insert, slot)


def plugin_unload(dm: "Daemon", insert: int, slot: int) -> str:
    """Удалить плагин из слота родной функцией FL (FE+0xE29570)."""
    p = slot_ptr(dm, insert, slot)
    return dm.call(RVA["pluginUnload"], [p], timeout=30)


def plugin_load(dm: "Daemon", insert: int, slot: int, path: str) -> str:
    """Загрузить плагин в слот (FE+0xE28E70: RCX=объект слота, RDX=Delphi-строка пути)."""
    p = slot_ptr(dm, insert, slot)
    sp = dm.script.exports_sync.mkdstr(path)
    return dm.call(RVA["pluginCreate"], [p, sp], timeout=60)


def cmd_slotmap(port: int, insert: int) -> int:
    """Таблица слотов инсерта из памяти (карта по vtable, см. §9 отчёта)."""
    dm = Daemon(os.path.join(os.path.dirname(SMALL), "small_drum.flp"), port)
    dm.start()
    try:
        wait_bridge()
        print("инсерт %d, объекты слота (vtable FE+0xDB4960, +0x160/+0x164)" % insert)
        print("  слот  объект              сост.  HMODULE      параметров  имя")
        for s in range(10):
            try:
                r = slot_state(dm, insert, s)
            except Exception as e:
                print("   %d   -- %s" % (s, str(e)[:60]))
                continue
            st = r.get("state")
            print("   %d    %-18s %-6s %-12s %-11s %s" % (
                s, r.get("obj"), "-1" if st == 0xFFFFFFFF else st,
                r.get("mod"), r.get("params"), r.get("name") or ""))
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print("slotmap не удался:", str(exc)[:300])
        return 5
    finally:
        import safe_fl
        dm.closing = True
        safe_fl.kill_all()


def cmd_bypass(track: int, slot: int, on: bool) -> int:
    """Bypass через живой API FL: mixer.setPluginMuteState (state=True -> mute)."""
    try:
        before = strip_q(bridge("str(mixer.getPluginMuteState(%d,%d))" % (track, slot)))
    except Exception as e:
        print("мост FL недоступен (%s).\n"
              "Bypass идёт через контроллер-скрипт FL, он поднимается только в проекте,\n"
              "где включён порт FLStudioMCP (small.flp). Запусти FL и повтори." % str(e)[:80])
        return 2
    bridge("str(mixer.setPluginMuteState(%d,%d,%d))" % (track, slot, 0 if on else 1))
    time.sleep(0.25)
    after = strip_q(bridge("str(mixer.getPluginMuteState(%d,%d))" % (track, slot)))
    print("track %d slot %d: было %s -> стало %s (%s)"
          % (track, slot, before, after, "включён" if on else "bypass"))
    return 0 if after != before else 4


REASONS = {
    "add-channel": "в API FL нет создания канала; внутренняя функция не найдена",
    "add-clip": "не найдена функция/структура добавления клипа в плейлист",
}


def cmd_unimplemented(name: str, args: list) -> int:
    print("%s: НЕ РЕАЛИЗОВАНО -- %s" % (name, REASONS.get(name, "?")))
    print("Инструмент намеренно ничего не делает, чтобы не портить проект.")
    return 3


def cmd_probe(flp: str, wait: float) -> int:
    dm = Daemon(flp, 9795)
    dm.start()
    time.sleep(wait)
    with open(dm.log_path, encoding="utf-8") as f:
        evs = [json.loads(l) for l in f if l.strip().startswith("{")]
    from collections import Counter
    c = Counter(e.get("name") for e in evs if e.get("ev") == "hook")
    print("событий хуков:", sum(c.values()))
    for k, v in c.most_common():
        print("  %-55s %d" % (k, v))
    print("лог:", dm.log_path)
    import safe_fl
    safe_fl.kill_all()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daemon")
    d.add_argument("--flp", default=SMALL)
    d.add_argument("--port", type=int, default=9790)
    s = sub.add_parser("send")
    s.add_argument("json")
    s.add_argument("--port", type=int, default=9790)
    sub.add_parser("state")
    sub.add_parser("kill")

    p = sub.add_parser("probe", help="запустить FL и снять цепочку создания плагинов")
    p.add_argument("--flp", default=os.path.join(os.path.dirname(SMALL), "small_drum.flp"))
    p.add_argument("--wait", type=float, default=30)

    m = sub.add_parser("slotmap", help="таблица слотов инсерта из памяти (Frida)")
    m.add_argument("--insert", type=int, default=1)
    m.add_argument("--port", type=int, default=9790)

    b = sub.add_parser("bypass", help="вкл/выкл плагин в слоте (живой API FL)")
    b.add_argument("track", type=int)
    b.add_argument("slot", type=int)
    b.add_argument("state", choices=["on", "off"], help="on = плагин работает")
    b.add_argument("--port", type=int, default=9792)

    for name in ("add-channel", "add-clip"):
        q = sub.add_parser(name)
        q.add_argument("args", nargs="*")

    sl = sub.add_parser("slots", help="карта слотов из памяти: состояние, имя, путь, HMODULE")
    sl.add_argument("--track", type=int, default=None)
    sl.add_argument("--port", type=int, default=9790)

    tc = sub.add_parser("trackcount", help="число дорожек микшера из памяти движка")
    tc.add_argument("--port", type=int, default=9790)

    ta = sub.add_parser("trackadd", help="вставить дорожку(и) микшера (родная функция FL)")
    ta.add_argument("--index", type=int, default=None,
                    help="куда вставлять; по умолчанию — выбранная дорожка, как в меню FL")
    ta.add_argument("--count", type=int, default=1)
    ta.add_argument("--flags", type=int, default=7)
    ta.add_argument("--port", type=int, default=9790)
    ta.add_argument("--flp", default=os.path.join(os.path.dirname(SMALL), "small_drum.flp"))

    td = sub.add_parser("trackdel", help="удалить дорожку микшера (родная функция FL)")
    td.add_argument("index", type=int)
    td.add_argument("--flags", type=int, default=0x12)
    td.add_argument("--port", type=int, default=9790)
    td.add_argument("--flp", default=os.path.join(os.path.dirname(SMALL), "small_drum.flp"))

    for name, hlp in (("insert", "вставить плагин в слот (живой вызов движка)"),
                      ("delete", "удалить плагин из слота (родная функция FL)"),
                      ("replace", "заменить плагин в слоте (unload + load)")):
        q = sub.add_parser(name, help=hlp)
        q.add_argument("track", type=int)
        q.add_argument("slot", type=int)
        if name != "delete":
            q.add_argument("plugin", help="имя плагина или полный путь к .dll")
        q.add_argument("--port", type=int, default=9790)
        q.add_argument("--flp", default=os.path.join(os.path.dirname(SMALL), "small_drum.flp"))
        q.add_argument("--force", action="store_true", help="вставить поверх занятого слота")

    a = ap.parse_args(argv)

    if a.cmd == "state":
        print(json.dumps(live_state(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "kill":
        import safe_fl
        safe_fl.kill_all()
        print("FL закрыт")
        return 0
    if a.cmd == "send":
        print(json.dumps(send(json.loads(a.json), a.port), ensure_ascii=False, indent=2)[:8000])
        return 0
    if a.cmd == "probe":
        return cmd_probe(a.flp, a.wait)
    if a.cmd == "slotmap":
        return cmd_slotmap(a.port, a.insert)
    if a.cmd == "bypass":
        return cmd_bypass(a.track, a.slot, a.state == "on")

    if a.cmd in ("insert", "replace", "delete", "slots"):
        req: dict = {"op": a.cmd}
        if a.cmd == "slots":
            req["track"] = a.track
        else:
            req["track"], req["slot"] = a.track, a.slot
            if a.cmd != "delete":
                req["plugin"] = a.plugin
                req["force"] = bool(getattr(a, "force", False))
        if daemon_alive(a.port):
            resp = send(req, a.port)
        else:
            print("демон на %d не отвечает — поднимаю FL с %s" % (a.port, a.flp), flush=True)
            dm = Daemon(a.flp, a.port)
            dm.start()
            wait_bridge()
            resp = dm.run_op(req)
        print(json.dumps(resp, ensure_ascii=False, indent=1, default=str)[:4000])
        return 0 if resp.get("ok") else 6

    if a.cmd in ("trackcount", "trackadd", "trackdel"):
        req = {"op": a.cmd}
        if a.cmd == "trackadd":
            req["index"] = a.index
            req["count"] = a.count
            req["flags"] = a.flags
        elif a.cmd == "trackdel":
            req["index"] = a.index
            req["flags"] = a.flags
        if daemon_alive(a.port):
            resp = send(req, a.port, timeout=120)
        else:
            print("демон на %d не отвечает — поднимаю FL с %s" % (a.port, a.flp), flush=True)
            dm = Daemon(a.flp, a.port)
            dm.start()
            wait_bridge()
            resp = dm.run_op(req)
        print(json.dumps(resp, ensure_ascii=False, indent=1, default=str)[:4000])
        return 0 if resp.get("ok") else 6

    if a.cmd in ("add-channel", "add-clip"):
        return cmd_unimplemented(a.cmd, a.args)

    dm = Daemon(a.flp, a.port)
    dm.start()
    dm.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
