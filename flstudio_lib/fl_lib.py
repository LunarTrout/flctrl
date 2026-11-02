"""fl_lib.py -- единая библиотека доступа к FL Studio.

Четыре раздела, один импорт:

    import fl_lib as fl

    fl.state.tracks()            # живой микшер
    fl.state.channels()          # каналы рэка
    fl.state.plugins()           # плагины по слотам
    fl.state.playlist(flp)       # клипы из файла
    fl.state.automation(flp)     # автоматизация из файла

    fl.hear.measure(wav, 60, 90) # замер отрезка
    fl.hear.curve(wav)           # непрерывные кривые
    fl.hear.profile(wav)         # кэш всего профиля
    fl.hear.bands(wav)           # полосы: уровень/ширина/модуляция

    fl.act.volume(track, value)  # правка вживую
    fl.act.pan(track, value)
    fl.act.param(track, slot, idx, value)

    fl.monitor.only(track)       # слушать только эту дорожку в реальном времени
    fl.monitor.listen(track, 10) # солит, пишет выход карты, возвращает wav
    fl.monitor.measure(wav)      # замер услышанного
    fl.monitor.knob(track, slot, idx, value)   # крутить, слушая
    fl.monitor.all()             # вернуть все дорожки

    fl.safe()                    # снапшот/откат/правки с проверкой (MixSafe)

    fl.create.plugin(fst, insert, slot)   # вставка (нужно перечитывание)
    fl.create.clone(src_ins, src_slot, dst_ins, dst_slot)
    fl.create.reload()
    fl.create.render(flp)        # отрисовать проект через CLI FL

Ничего не «помнит»: каждый вызов читает текущее состояние.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "flpresearch"))


# ---------------------------------------------------------------- state

class _State:
    """Чтение состояния: живое (мост) и из файла."""

    @staticmethod
    def _live(expr):
        import q
        return q.live(expr)

    def tracks(self):
        import q
        import json
        return json.loads(self._live("__import__('json').dumps(%s)" % q.LIVE_EXPR["tracks"]))

    def channels(self):
        import q
        import json
        return json.loads(self._live("__import__('json').dumps(%s)" % q.LIVE_EXPR["channels"]))

    def plugins(self):
        return self._live("[list(x) for x in %s]" % q_plugins())

    def tempo(self):
        import q
        return self._live(q.LIVE_EXPR["tempo"])

    def routing(self):
        """Маршруты: для каждой дорожки — список получателей.

        Читается живьём: перебираем все пары и оставляем включённые.
        Требует открытого FL (мост на 9787).
        """
        expr = ("__import__('json').dumps({str(i): [d for d in range(mixer.trackCount())"
                " if mixer.getRouteToLevel(i, d) > 0] for i in range(mixer.trackCount())})")
        raw = self._live(expr)
        import json
        return json.loads(raw) if raw else {}

    def clip_files(self, flp: str):
        """Связь «клип → файл»: позиция, длина, канал и путь к аудио.

        Канал несёт sample_path, клип ссылается на канал — этого достаточно,
        чтобы понять, какой файл лежит в каждом месте плейлиста.
        """
        import pyflp
        p = pyflp.parse(flp)
        chans = list(p.channels)
        out = []
        arr = p.arrangements[0]
        for ti, tr in enumerate(arr.tracks):
            try:
                items = list(tr)
            except TypeError:
                continue
            for it in items:
                ref = getattr(it, "channel", None)
                ch = None
                if isinstance(ref, int):
                    ch = chans[ref] if 0 <= ref < len(chans) else None
                elif hasattr(ref, "sample_path"):
                    ch = ref
                elif ref is not None and hasattr(ref, "iid"):
                    idx = getattr(ref, "iid", None)
                    ch = chans[idx] if isinstance(idx, int) and idx < len(chans) else None
                out.append({
                    "track": ti,
                    "position": getattr(it, "position", None),
                    "length": getattr(it, "length", None),
                    "name": str(getattr(ch, "name", "") or ""),
                    "sample": str(getattr(ch, "sample_path", "") or ""),
                    "muted": bool(getattr(it, "muted", False)),
                })
        return out

    def playlist(self, flp: str):
        import fl_automation as A
        import flp_events as fe
        return A.read_playlist_items(fe.FLP.load(flp))

    def automation(self, flp: str):
        import fl_automation as A
        import flp_events as fe
        return A.read_automations(fe.FLP.load(flp))


def q_plugins():
    import q
    return q.LIVE_EXPR["plugins"]


# ---------------------------------------------------------------- hear

def _PROJECT_IS_OPEN() -> bool:
    """Открыт ли FL прямо сейчас (офлайн-рендер требует закрытого FL)."""
    try:
        import q
        q.live("mixer.trackCount()")
        return True
    except Exception:
        return False


class _Hear:
    """Слой слуха: восприятие, полосы, сравнение."""

    def _render_cache(self, flp: str) -> str:
        import hashlib
        d = os.path.join(HERE, "_cache", "render")
        os.makedirs(d, exist_ok=True)
        st = os.stat(flp)
        tag = hashlib.sha1(("%s|%s|%s" % (os.path.abspath(flp), st.st_mtime, st.st_size))
                           .encode()).hexdigest()[:16]
        return os.path.join(d, tag + ".wav")

    def offline(self, flp: str, force: bool = False) -> str:
        """Отрисовать проект один раз и запомнить по состоянию файла.

        Повторный вызов при неизменённом .flp ничего не рендерит — возвращает
        готовый wav. Дальше любой отрезок мерится за доли секунды.
        """
        import shutil
        out = self._render_cache(flp)
        if os.path.exists(out) and not force and os.path.getsize(out) > 100000:
            return out
        if _PROJECT_IS_OPEN():
            raise RuntimeError("FL открыт с этим проектом — офлайн-рендер невозможен")
        produced = create.render(flp)
        if not produced or not os.path.exists(produced):
            raise RuntimeError("рендер не дал файла")
        shutil.copy2(produced, out)
        return out

    def at(self, flp: str, start: float, dur: float = 20.0) -> dict:
        """Замер любого места проекта: рендер берётся из кэша."""
        return self.measure(self.offline(flp), start, start + dur)

    def measure(self, path: str, a: float | None = None, b: float | None = None,
                stem: str | None = None):
        import listen_layer as L
        sec = (a, b) if a is not None and b is not None else None
        return L.measure(path, section=sec, stem_path=stem)

    def curve(self, path: str, a: float | None = None, b: float | None = None):
        import listen_layer as L
        sec = (a, b) if a is not None and b is not None else None
        return L.curve(path, sec)

    def profile(self, path: str):
        import profile_cache as P
        return P.build(path)

    def bands(self, path: str):
        import bandprofile as B
        return B.band_axes(path)

    def masking(self, stems: dict, a: float | None = None, b: float | None = None,
                mix_path: str | None = None):
        """Кто кого забивает: доли и задавленные полосы по стемам."""
        import listen_layer as L
        sec = (a, b) if a is not None and b is not None else None
        return L.masking(stems, section=sec, mix_path=mix_path)

    def sections(self, path: str, min_len: float = 8.0):
        import listen_layer as L
        return L.sections(path, min_len)

    def match(self, path_a: str, path_b: str, min_len: float = 8.0):
        """Сопоставить однотипные секции двух файлов по громкости."""
        import listen_layer as L
        return L.match_sections(path_a, path_b, min_len)

    def live(self, seconds: float = 20.0, start: float = 0.0, track: int | None = None) -> str:
        """Быстрый цикл: играем отрезок и пишем выход карты (без рендера).

        track=None — весь микс; иначе только эта дорожка. Ожидание равно длине
        отрезка, а не минутам рендера.
        """
        import track_capture as T
        return T.grab_any(seconds, start, track)

    def live_measure(self, seconds: float = 20.0, start: float = 0.0,
                     track: int | None = None) -> dict:
        """Записать отрезок вживую и сразу его померить."""
        path = self.live(seconds, start, track)
        return self.measure(path)

    def compare(self, path: str, refs: list, a=None, b=None):
        import listen_layer as L
        from brief import flatten
        sec = (a, b) if a is not None and b is not None else None
        mine = flatten(L.measure(path, section=sec))
        rs = [flatten(L.measure(r, section=sec)) for r in refs]
        return L.corridor(mine, rs)


# ---------------------------------------------------------------- act

class _Act:
    """Правки вживую. Возвращают прочитанное после записи значение."""

    def volume(self, track: int, value: float):
        return _State._live("mixer.setTrackVolume(%d, %r)" % (track, value))

    def pan(self, track: int, value: float):
        return _State._live("mixer.setTrackPan(%d, %r)" % (track, value))

    def mute(self, track: int, state: bool):
        return _State._live("mixer.muteTrack(%d, %r)" % (track, state))

    def solo(self, track: int, state: bool):
        return _State._live("mixer.soloTrack(%d, %r)" % (track, state))

    def param(self, track: int, slot: int, index: int, value: float):
        return _State._live("plugins.setParamValue(%r, %d, %d, %d)"
                            % (value, index, track, slot))

    def params(self, track: int, slot: int):
        return _State._live(
            "__import__('json').dumps([{'i': i, 'name': str(plugins.getParamName(i, %d, %d)),"
            " 'val': plugins.getParamValue(i, %d, %d),"
            " 'text': str(plugins.getParamValueString(i, %d, %d))}"
            " for i in range(plugins.getParamCount(%d, %d))])"
            % (track, slot, track, slot, track, slot, track, slot))


# ---------------------------------------------------------------- create

class _Monitor:
    """Реальное время: слушать одну дорожку и крутить её, не прерывая звук.

    only() глушит все дорожки кроме выбранной; all() возвращает как было.
    capture() пишет выход звуковой карты; listen() = only + захват + файл.
    """

    def only(self, track: int):
        import track_capture as T
        T.solo_only(track)
        return track

    def all(self):
        import track_capture as T
        T.unmute_all()
        return True

    def capture(self, seconds: float, fs: int = 48000):
        import track_capture as T
        return T.capture(seconds, fs)

    def listen(self, track: int, seconds: float = 10.0, start: float | None = None) -> str:
        """Солит дорожку, слушает её N секунд, возвращает путь к wav."""
        import track_capture as T
        st = start if start is not None else float(
            _State._live("general.getSongPos() / 1000.0") or 0.0)
        return T.grab(track, seconds, st)

    def measure(self, path: str, a: float | None = None, b: float | None = None):
        import listen_layer as L
        sec = (a, b) if a is not None and b is not None else None
        return L.measure(path, section=sec)

    def peaks(self) -> list:
        """Текущие пики всех дорожек (максимум из L/R)."""
        n = int(_State._live("mixer.trackCount()"))
        out = []
        for i in range(n):
            l = float(_State._live("mixer.getTrackPeaks(%d,0)" % i) or 0.0)
            r = float(_State._live("mixer.getTrackPeaks(%d,1)" % i) or 0.0)
            out.append(max(l, r))
        return out

    def watch(self, seconds: float = 10.0, every: float = 0.1,
              start: float | None = None, play: bool = True) -> dict:
        """Наблюдение: пики всех дорожек за отрезок времени.

        Возвращает по каждой дорожке максимум и среднее, плюс число отсчётов.
        Если play=True — сам запускает и останавливает воспроизведение.
        """
        import time as _t
        if start is not None:
            _State._live("transport.setSongPos(%r)" % start)
        if play:
            _State._live("transport.start()")
            _t.sleep(0.25)
        n = int(_State._live("mixer.trackCount()"))
        names = [str(_State._live("mixer.getTrackName(%d)" % i)) for i in range(n)]
        series = [[] for _ in range(n)]
        t0 = _t.time()
        while _t.time() - t0 < seconds:
            for i in range(n):
                l = float(_State._live("mixer.getTrackPeaks(%d,0)" % i) or 0.0)
                r = float(_State._live("mixer.getTrackPeaks(%d,1)" % i) or 0.0)
                series[i].append(max(l, r))
            _t.sleep(every)
        if play:
            try:
                _State._live("transport.stop()")
            except Exception:
                pass
        out = []
        for i, s in enumerate(series):
            arr = __import__("numpy").array(s, dtype=float) if s else __import__("numpy").zeros(1)
            out.append({
                "i": i,
                "name": names[i],
                "max": float(arr.max()),
                "mean": float(arr.mean()),
                "samples": int(arr.size),
            })
        return {"seconds": seconds, "every": every, "tracks": out}

    def stream(self, seconds: float = 20.0, window: float = 2.0,
               track: int | None = None, offset_db: float = 0.0) -> list:
        """Живая перцептивная лента: захват кусками по window и замер каждого.

        Возвращает список окон: время, соны, резкость, LUFS, доли областей.
        Это то, что нельзя увидеть по пикам: спектр и воспринимаемая громкость
        в каждый момент времени.
        """
        import time as _t
        import listen_layer as L
        import track_capture as T

        if track is not None:
            T.solo_only(track)
            _t.sleep(0.2)
        out = []
        done = 0.0
        while done < seconds:
            step = min(window, seconds - done)
            audio = T.capture(step, 48000)
            m = L.measure_array(audio, 48000, offset_db)
            out.append({
                "t": round(done, 2),
                "dur": round(step, 2),
                "N": round(m["N_mean"], 1),
                "sharp": round(m["sharp_mean"], 3),
                "lufs": m["lufs"],
                "regions_pct": {k: round(v, 1) for k, v in (m.get("regions_pct") or {}).items()},
            })
            done += step
        if track is not None:
            T.unmute_all()
        return out

    def knob(self, track: int, slot: int, index: int, value: float):
        """Крутить ручку плагина вживую, не прерывая звук."""
        return act.param(track, slot, index, value)


class _Sim:
    """Песочница: применить любую цепочку к звуку и померить результат.

    ops — список примитивов в любом порядке: gain, hpf, lpf, eq, comp, sat,
    pan, vst3 (с параметрами bands/params). Это инструменты, не пайплайн:
    собирай как нужно, по одному или двадцать.
    """

    def load(self, path: str, a: float | None = None, b: float | None = None):
        import soundfile as sf
        with sf.SoundFile(path) as f:
            fs = f.samplerate
            if a is not None and b is not None:
                f.seek(int(a * fs))
                x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
            else:
                x = f.read(dtype="float64", always_2d=True)
        return x, fs

    def run(self, x, fs: int, ops: list):
        import sandbox as S
        return S.run(x, fs, ops)

    def compare(self, x, y, fs: int, offset_db: float = 0.0):
        import sandbox as S
        return S.compare(x, y, fs, offset_db)

    def delta(self, before: dict, after: dict) -> list:
        import sandbox as S
        return S.delta_lines(before, after)

    def vst(self, path: str, bands: list | None = None, params: dict | None = None):
        """Готовый оператор для конкретного VST3 — вставляй в ops как есть."""
        return {"op": "vst3", "path": path, "bands": bands or [], "params": params or {}}


class _Predict:
    """Предсказание: как правка дорожки сдвинет метрики микса, без её применения.

    Работает на полосных профилях и аддитивной модели мощности: учитывает
    громкость и EQ, НЕ учитывает компрессию, сатурацию и маскировку.
    """

    def profile(self, path: str, a: float | None = None, b: float | None = None):
        import predict as P
        return P.profile_db(path, a, b)

    def change(self, mix_profile, track_profile, gain_db: float = 0.0,
               eq: list | None = None) -> dict:
        import predict as P
        return P.predict(mix_profile, track_profile, gain_db, eq or [])

    def lines(self, before: dict, after: dict) -> list:
        import predict as P
        return P.delta(before, after)


class _Create:
    """Создание объектов. Плагин — через файл, затем перечитывание."""

    def render(self, flp: str, timeout_s: int = 900, fmt: str = "wav") -> str:
        """Отрисовать проект через CLI FL (FL должен быть закрыт).

        FL игнорирует путь /R и пишет файл в папку экспорта проекта под его
        именем — поэтому возвращаем фактический путь, найденный после запуска.
        """
        import glob
        import subprocess
        import time

        fl = r"C:\Programs\Fl Studio 2025\FL64.exe"
        proj_dir = os.path.dirname(os.path.abspath(flp))
        name = os.path.splitext(os.path.basename(flp))[0]
        before = set(glob.glob(os.path.join(proj_dir, "**", "*." + fmt), recursive=True))
        cmd = [fl, "/E" + fmt, flp]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        out = ""
        while time.time() - t0 < timeout_s:
            time.sleep(3)
            found = set(glob.glob(os.path.join(proj_dir, "**", "*." + fmt), recursive=True))
            new = [f for f in found - before if name.lower() in os.path.basename(f).lower()]
            if new:
                out = max(new, key=os.path.getmtime)
                if os.path.getsize(out) > 100000:
                    break
        return out

    def plugin(self, fst: str, insert: int, slot: int, flp: str | None = None):
        import fl_add_plugin as A
        argv = [fst, str(insert), str(slot)]
        if flp:
            argv += ["--file", flp]
        return A.main(argv)

    def clone(self, src_ins: int, src_slot: int, dst_ins: int, dst_slot: int, flp: str | None = None):
        import fl_clone_plugin as C
        argv = [str(src_ins), str(src_slot), str(dst_ins), str(dst_slot)]
        if flp:
            argv += ["--file", flp]
        return C.main(argv)

    def reload(self, flp: str | None = None):
        import fl_reload as R
        old = sys.argv
        try:
            sys.argv = [old[0]] + ([flp] if flp else [])
            return R.main()
        finally:
            sys.argv = old


class _Safe:
    """Снапшот и откат микшера через мост (без внешних зависимостей).

    snapshot(tag) снимает громкости, панорамы и mute всех дорожек;
    restore(tag) возвращает их и проверяет, что значения вернулись.
    """

    def _dir(self) -> str:
        d = os.path.join(HERE, "_cache", "snap")
        os.makedirs(d, exist_ok=True)
        return d

    def snapshot(self, tag: str = "auto") -> str:
        import json
        st = state.tracks()
        path = os.path.join(self._dir(), "%s.json" % tag)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        return path

    def restore(self, tag: str = "auto") -> int:
        import json
        path = os.path.join(self._dir(), "%s.json" % tag)
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        n = 0
        for t in st:
            i = t["i"]
            act.volume(i, t["vol"])
            act.pan(i, t["pan"])
            n += 1
        return n

    def list(self) -> list:
        return sorted(os.path.basename(p) for p in
                      __import__("glob").glob(os.path.join(self._dir(), "*.json")))


state = _State()
hear = _Hear()
act = _Act()
monitor = _Monitor()
sim = _Sim()
predict = _Predict()
create = _Create()


def safe() -> _Safe:
    """Транзакционный слой: снапшот и откат микшера."""
    return _Safe()
