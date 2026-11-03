"""rt.py — чтение PCM дорожек FL в реальном времени, без кнопки записи.

Точка съёма (доказана посимвольным совпадением с Disk Recording на дорожке 16):
`FE+0x849287`, буфер `[obj+0x16C]` сразу после FX-вызова `[[obj+0x160]+0x30]`.

Что важно знать:
* движок отдаёт сигнал только после «раскрутки» — короткого прохода Disk Recording
  (арм → record → play → stop). Без него буферы пустые при живых метрах;
* блок режется на куски: длина куска = `[[FE+0x10AC2B0]]`, блок = `*[FE+0x10AA730]`;
* буфер несут единицы объектов (посылы часто пустые);
* привязка «объект → индекс дорожки» делается корреляцией огибающих с pre-FX
  буфером `[track+0x34]`, который снимается в контуре индикаторов (`FE+0x849580`).
"""
from __future__ import annotations

import os
import time

import numpy as np

RVA_TAP = 0x849287
RVA_IND = 0x849580
RVA_TRACKS = 0x10A7EB0

JS = r"""
'use strict';
const fe = Process.getModuleByName('FLEngine_x64.dll');
const chunkPtr = fe.base.add(0x10AC2B0).readPointer();

function chunkN() { try { const n = chunkPtr.readU32(); return (n > 0 && n < 8192) ? n : 0; } catch (e) { return 0; } }

const S = {on: false, post: {}, pre: {}, calls: 0, bad: 0};

function add(map, key, buf, n, idx) {
  let c = map[key];
  if (!c) { c = map[key] = {chunks: [], frames: 0, sum: 0, peak: 0, index: idx}; }
  try {
    const f = new Float32Array(buf.readByteArray(n * 8));
    c.chunks.push(f); c.frames += n;
    let s2 = 0, m = 0;
    for (let i = 0; i < f.length; i++) { s2 += f[i] * f[i]; const a = Math.abs(f[i]); if (a > m) m = a; }
    c.sum += s2; if (m > c.peak) c.peak = m;
  } catch (e) { S.bad++; }
}

rpc.exports = {
  setup: function () {
    // ставим хуки отложенно: синхронный attach на горячей функции вешает загрузку скрипта
    setImmediate(function () {
    // пост-FX источник сложения: rcx = [obj+0x16C], r8d = кадры*2. Копируем ровно блок.
    Interceptor.attach(fe.base.add(0x849287), {
      onEnter: function () {
        if (!S.on) return;
        S.calls++;
        const n = chunkN(); if (!n) return;
        let obj, buf;
        try { obj = this.context.rbp.add(0x38).readPointer(); } catch (e) { return; }
        if (obj.isNull()) return;
        try { buf = obj.add(0x16C).readPointer(); } catch (e) { return; }
        if (buf.isNull()) return;
        add(S.post, obj.toString(), buf, n, -1);
      }
    });
    Interceptor.attach(fe.base.add(0x849580), {
      onEnter: function () {
        if (!S.on) return;
        const n = chunkN(); if (!n) return;
        let idx, arr, buf;
        try { idx = this.context.rbp.add(0x30).readU32(); } catch (e) { return; }
        try {
          arr = fe.base.add(0x10A7EB0).readPointer();
          buf = arr.add(idx * 0x1474 + 0x34).readPointer();
        } catch (e) { return; }
        if (buf.isNull()) return;
        add(S.pre, String(idx), buf, n, idx);
      }
    });
    // route-путь: FX применяется к [route+8] внутри FE+0x82F0B0, индекс дорожки — [track+0x160]
    Interceptor.attach(fe.base.add(0x82F0B0), {
      onEnter: function () { if (!S.on) return; try { this.route = ptr(this.context.rcx); } catch (e) { this.route = null; } },
      onLeave: function () {
        if (!S.on || !this.route) return;
        try {
          const track = this.route.add(0x38).readPointer();
          const idx = track.add(0x160).readU32();
          const buf = this.route.add(8).readPointer();
          let n = this.route.add(0x48).readU32();
          if (!n || n > 8192) n = chunkN();
          if (!n || buf.isNull() || idx > 512) return;
          add(S.post, 't' + idx, buf, n, idx);
        } catch (e) { S.bad++; }
      }
    });
    // per-track PDC-копия: [track+0x34] -> буфер шины, на входе копии уже обработанный звук
    Interceptor.attach(fe.base.add(0x85250C), {
      onEnter: function () {
        if (!S.on) return;
        try {
          const src = ptr(this.context.rcx);
          let n = (this.context.r8 >> 1) >>> 0;
          if (!n || n > 8192) n = chunkN();
          if (!n || src.isNull()) return;
          const key = 'p' + this.threadId;
          add(S.post, key, src, n, -1);
        } catch (e) { S.bad++; }
      }
    });
    S.hooked = true;
    });
    return true;
  },
  begin: function () { S.on = true; S.post = {}; S.pre = {}; S.calls = 0; S.bad = 0; return true; },
  off: function () { S.on = false; return true; },
  end: function () {
    S.on = false;
    const out = {calls: S.calls, bad: S.bad, post: [], pre: []};
    for (const k in S.post) {
      const c = S.post[k];
      out.post.push({key: k, frames: c.frames,
                     rms: Math.sqrt(c.sum / Math.max(c.frames * 2, 1)), peak: c.peak});
    }
    for (const k in S.pre) {
      const c = S.pre[k];
      out.pre.push({key: k, index: c.index, frames: c.frames,
                    rms: Math.sqrt(c.sum / Math.max(c.frames * 2, 1)), peak: c.peak});
    }
    return out;
  },
  take: function (which, key, off, count) {
    const src = (which === 'pre') ? S.pre[key] : S.post[key];
    if (!src) return null;
    const out = new Float32Array(count);
    let w = 0, seen = 0;
    for (let i = 0; i < src.chunks.length && w < count; i++) {
      const c = src.chunks[i];
      if (seen + c.length <= off) { seen += c.length; continue; }
      const start = Math.max(0, off - seen);
      for (let j = start; j < c.length && w < count; j++) out[w++] = c[j];
      seen += c.length;
    }
    return Array.from(out.slice(0, w));
  },
  env: function (which, key, hop) {
    const src = (which === 'pre') ? S.pre[key] : S.post[key];
    if (!src) return null;
    hop = hop || 512;
    const frames = Math.floor(src.frames / hop);
    const out = new Float32Array(frames);
    let acc = 0, cnt = 0, frame = 0;
    for (let i = 0; i < src.chunks.length; i++) {
      const c = src.chunks[i];
      for (let j = 0; j < c.length; j += 2) {
        acc += c[j] * c[j]; cnt++;
        if (cnt >= hop) { if (frame < frames) out[frame++] = Math.sqrt(acc / cnt); acc = 0; cnt = 0; }
      }
    }
    return Array.from(out.slice(0, frame));
  }
};
"""


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default



def _attach(pid: int, timeout: float = 12.0):
    """frida.attach с жёстким таймаутом: не даём процессу уйти в бесконечное ожидание."""
    import threading

    import frida

    box: dict = {}

    def work():
        try:
            box["s"] = frida.attach(pid)
        except Exception as exc:  # noqa: BLE001
            box["e"] = exc

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if "s" in box:
        return box["s"]
    if "e" in box:
        raise box["e"]
    raise TimeoutError("frida.attach не ответил за %.0f с" % timeout)


def wake(track: int = 1, seconds: float = 0.4, bridge=None, undo: bool = True) -> dict:
    """Разбудить движок FL: короткий проход Disk Recording и полная уборка за собой.

    Проверено замером: во время самой записи буферы каналов ещё пустые, а на
    следующем обычном воспроизведении сигнал появляется и держится до конца
    сессии. Арм без записи не будит, запись без взведённой дорожки — тоже.

    Что делаем, чтобы не насорить: гасим ``AutoCreateClip``, после прохода
    возвращаем флаг, удаляем появившиеся WAV и откатываем шаг записи в истории
    FL. Проект НЕ сохраняется — иначе тейк превратится в клип поверх плейлиста.
    """
    try:
        from . import live
    except ImportError:
        import live

    clip_before = live.clip_flag(0)
    rec_dir = getattr(live, "RECORDED", "")
    before = set(os.listdir(rec_dir)) if rec_dir and os.path.isdir(rec_dir) else set()
    hint = live._undo_hint()
    armed_before = int(_num(live._bridge("mixer.isTrackArmed(%d)" % track)))
    discarded = 0
    files = 0
    try:
        if not armed_before:
            live._bridge("mixer.armTrack(%d)" % track)
        if not int(_num(live._bridge("transport.isRecording()"))):
            live._bridge("transport.record()")
        live._bridge("transport.start()")
        time.sleep(max(0.15, float(seconds)))
        live._bridge("transport.stop()")
        if int(_num(live._bridge("transport.isRecording()"))):
            live._bridge("transport.record()")
    finally:
        if not armed_before:
            try:
                live._bridge("mixer.armTrack(%d)" % track)
            except OSError:
                pass
        time.sleep(0.35)
        if rec_dir and os.path.isdir(rec_dir):
            for name in sorted(set(os.listdir(rec_dir)) - before):
                try:
                    os.remove(os.path.join(rec_dir, name))
                    files += 1
                except OSError:
                    pass
        if undo and hint is not None:
            try:
                if live._undo_hint() != hint:
                    live._bridge("general.undoUpDown(-1)")
                    discarded = 1
            except OSError:
                discarded = -1
        try:
            live.clip_flag(int(clip_before.get("before") or 1))
        except Exception:  # noqa: BLE001
            pass
    return {"woke": True, "track": track, "seconds": seconds,
            "take_discarded": discarded, "files_removed": files,
            "clip_flag": clip_before.get("before")}


class RT:
    """Один attach, много коротких съёмов."""

    def __init__(self, pid: int | None = None):
        import frida

        self._frida = frida
        if pid is None:
            dev = frida.get_local_device()
            found = [p.pid for p in dev.enumerate_processes() if p.name.lower() == "fl64.exe"]
            if not found:
                raise RuntimeError("FL не запущена")
            pid = found[0]
        self.pid = pid
        self.session = _attach(pid)
        self.script = self.session.create_script(JS)
        self.errors: list = []
        self.script.on("message", self._on_msg)
        self.script.load()
        self.script.exports_sync.setup()

    def _on_msg(self, message, data):
        if message.get("type") == "error":
            self.errors.append(message.get("description"))

    def close(self):
        try:
            self.script.exports_sync.off()
            time.sleep(0.4)          # даём аудио-потоку выйти из хука
        except Exception:
            pass
        try:
            self.session.detach()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- съём ---
    def capture(self, seconds: float = 2.0, start: float | None = None,
                bridge=None) -> dict:
        if bridge is None:
            try:
                from . import live as bridge
            except ImportError:  # импорт как top-level модуль
                import live as bridge
        bridge._bridge("transport.stop()", timeout=5)
        time.sleep(0.2)
        if start is not None:
            bridge._bridge("transport.setSongPos(%r)" % min(1.0, max(0.0, float(start))), timeout=5)
            time.sleep(0.1)
        self.script.exports_sync.begin()
        bridge._bridge("transport.start()", timeout=5)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            if _num(bridge._bridge("transport.isPlaying()", timeout=3)) > 0.5:
                break
            time.sleep(0.05)
        time.sleep(max(0.05, seconds))
        info = self.script.exports_sync.end()
        bridge._bridge("transport.stop()", timeout=5)
        return info

    def pcm(self, key: str, which: str = "post") -> np.ndarray:
        parts, off = [], 0
        while True:
            chunk = self.script.exports_sync.take(which, key, off, 65536)
            if not chunk:
                break
            parts.append(np.asarray(chunk, dtype=np.float32))
            off += len(chunk)
            if len(chunk) < 65536:
                break
        x = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        return x[: x.size - (x.size % 2)].reshape(-1, 2)

    def _env_rpc(self, key: str, which: str, hop: int = 512) -> np.ndarray:
        v = self.script.exports_sync.env(which, key, hop)
        return np.asarray(v, dtype=np.float32) if v else np.zeros(0, np.float32)

    # --- карта объект -> дорожка ---
    @staticmethod
    def _env(x: np.ndarray, hop: int = 512) -> np.ndarray:
        m = x.mean(axis=1) if x.ndim > 1 else x
        n = (m.size // hop) * hop
        if not n:
            return np.zeros(0, np.float32)
        return np.sqrt((m[:n].reshape(-1, hop) ** 2).mean(axis=1))

    def mapping(self, info: dict) -> dict:
        post = [c for c in info.get("post", []) if c["frames"] > 400]
        pre = {int(c["index"]): c for c in info.get("pre", []) if c["frames"] > 400}
        if not post or not pre:
            return {}
        pre_env = {i: self._env_rpc(c["key"], "pre") for i, c in pre.items()}
        out = {}
        for c in post:
            e = self._env_rpc(c["key"], "post")
            if e.size < 8:
                continue
            best, best_r = None, -2.0
            for idx, pe in pre_env.items():
                n = min(e.size, pe.size)
                if n < 8:
                    continue
                a, b = e[:n], pe[:n]
                if a.std() < 1e-9 or b.std() < 1e-9:
                    continue
                r = float(np.corrcoef(a, b)[0, 1])
                if r > best_r:
                    best, best_r = idx, r
            if best is not None and best_r > 0.5:
                out[int(best)] = {"key": c["key"], "corr": round(best_r, 3),
                                  "rms": round(float(c["rms"]), 5),
                                  "frames": c["frames"]}
        return out

    def tracks(self, seconds: float = 2.0, start: float | None = None,
               bridge=None) -> dict:
        """Снять N секунд и вернуть {индекс дорожки: {'wav': ndarray, ...}}."""
        info = self.capture(seconds=seconds, start=start, bridge=bridge)
        m = self.mapping(info)
        out = {}
        for idx, meta in m.items():
            out[int(idx)] = {"wav": self.pcm(meta["key"], "post"), **meta,
                             "rms": meta["rms"], "corr": meta["corr"]}
        return out


def capture_tracks(tracks=None, seconds: float = 2.0, start: float | None = None,
                   min_rms: float = 1e-4) -> dict:
    """Живой съём post-FX. Пусто (нет сигнала в буферах) → ``{}``, без исключений.

    Это тонкая обёртка для ``live.capture(mode='rt')``: она не делает раскрутку и не
    трогает проект, только слушает микшер. Если движок в этом инстансе FL не пишет в
    буферы каналов (бывает после холодного старта), вернётся пустой словарь, и
    вызывающий код откатится на запись через диск.
    """
    want = {int(t) for t in tracks} if tracks else None
    with RT() as r:
        got = r.tracks(seconds=seconds, start=start)
    if not got:
        return {}
    picked = {i: v for i, v in got.items()
              if (want is None or i in want)
              and float(np.sqrt((v["wav"] ** 2).mean())) >= min_rms}
    return picked
