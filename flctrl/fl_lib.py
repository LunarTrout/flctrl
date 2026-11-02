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


# ---------------------------------------------------------------- разбор ответов

def _parse(value):
    """Разобрать ответ контроллера: он приходит строкой repr, не JSON."""
    import ast
    import json
    if isinstance(value, (list, dict)):
        return value
    s = str(value).strip()
    try:
        r = json.loads(s)
    except Exception:
        return ast.literal_eval(s)
    if isinstance(r, str):
        return ast.literal_eval(r)
    return r


# ---------------------------------------------------------------- state

class _Link:
    """Транспорт до FL: демон-посредник и порт 9787.

    Библиотека не должна требовать ручных шагов: если посредник не поднят,
    она поднимает его сама и проверяет, что связь появилась.
    """

    PORT = 9787
    DEFAULT_DAEMON = r"C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\fl-studio-mcp-daemon.exe"

    def _port_open(self, timeout: float = 1.0) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", self.PORT), timeout=timeout):
                return True
        except OSError:
            return False

    def daemon(self) -> str:
        return os.environ.get("FLCTRL_DAEMON", self.DEFAULT_DAEMON)

    def midi_ports(self) -> list:
        """Есть ли порты loopMIDI (без них контроллер не заговорит)."""
        import subprocess
        try:
            r = subprocess.run([
                "powershell", "-NoProfile", "-Command",
                "Get-ChildItem 'HKCU:\\Software\\Tobias Erichsen\\loopMIDI\\Ports'"
                " | Select-Object -ExpandProperty Name"],
                capture_output=True, timeout=20)
            return [x for x in r.stdout.decode("cp866", "ignore").split() if x.strip()]
        except Exception:
            return []

    def status(self) -> dict:
        import subprocess
        out = {"daemon": self._port_open(), "fl": False, "midi_ports": self.midi_ports()}
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe"],
                               capture_output=True, timeout=20)
            out["fl"] = b"FL64.exe" in (r.stdout or b"")
        except Exception:
            pass
        return out

    def ensure(self, timeout: float = 45.0) -> dict:
        """Поднять посредник, если он не отвечает. Возвращает что было сделано."""
        import subprocess
        import time

        was = self._port_open()
        if was:
            return {"daemon": True, "started": False, "port": self.PORT}
        path = self.daemon()
        if not os.path.exists(path):
            return {"daemon": False, "started": False, "error": "нет файла %s" % path}
        env = dict(os.environ)
        env.setdefault("FLSTUDIO_MCP_PORT_FROM_FL", "FLStudioMCP RX")
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([path], cwd=os.path.dirname(path), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(1.0)
            if self._port_open():
                return {"daemon": True, "started": True, "port": self.PORT,
                        "seconds": round(time.time() - t0, 1)}
        return {"daemon": False, "started": True, "port": self.PORT,
                "error": "порт не открылся за %.0f с" % timeout}


class _State:
    """Чтение состояния: живое (мост) и из файла."""

    @staticmethod
    def _live(expr, heal: bool = True):
        import q
        try:
            return q.live(expr)
        except Exception:
            if not heal:
                raise
            # посредник мог умереть — библиотека поднимает его сама
            res = link.ensure()
            if not res.get("daemon"):
                raise
            return q.live(expr)

    def tracks(self, chunk: int = 6):
        """Живой микшер. Запрашивается кусками: длинные выражения транспорт режет."""
        n = int(self._live("mixer.trackCount()"))
        out = []
        for a in range(0, n, chunk):
            expr = ("[(i, str(mixer.getTrackName(i)), round(mixer.getTrackVolume(i), 2), "
                    "round(mixer.getTrackPan(i), 2), sum(1 for s in range(10) "
                    "if plugins.isValid(i, s))) for i in range(%d, %d)]"
                    % (a, min(a + chunk, n)))
            for row in _parse(self._live(expr)):
                out.append({"i": row[0], "name": row[1], "vol": row[2],
                            "pan": row[3], "fx": row[4]})
        return out

    def routing(self, track: int, dests: list | None = None) -> list:
        """Куда дорожка шлёт: список (получатель, уровень).

        Сначала спрашиваем, какие посылы вообще включены (getRouteSendActive —
        переживает перебор всех дорожек), потом уточняем уровень у каждого.
        Напрямую перебирать уровни нельзя: на части индексов FL отвечает
        «Index out of range».
        """
        n = int(self._live("mixer.trackCount()"))
        cand = dests if dests is not None else list(range(n))
        expr = "[d for d in %s if mixer.getRouteSendActive(%d, d)]" % (cand, track)
        try:
            active = _parse(self._live(expr))
        except Exception:
            active = []
        out = []
        for d in active:
            try:
                lvl = float(self._live("mixer.getRouteToLevel(%d, %d)" % (track, int(d))))
            except Exception:
                lvl = -1.0
            out.append((int(d), lvl))
        return out

    def health(self) -> dict:
        """Живо ли управление и что именно не так. Не бросает исключение."""
        import subprocess
        out = {"fl_running": False, "bridge": False, "tracks": None, "hint": ""}
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe"],
                               capture_output=True, timeout=20)
            out["fl_running"] = b"FL64.exe" in (r.stdout or b"")
        except Exception:
            pass
        try:
            out["tracks"] = int(self._live("mixer.trackCount()"))
            out["bridge"] = True
        except Exception as e:
            out["hint"] = str(e)[:200]
        if not out["fl_running"]:
            out["hint"] = "FL Studio не запущена"
        elif not out["bridge"]:
            out["hint"] = ("FL запущена, но контроллер не отвечает. "
                           "F10 -> MIDI settings -> Update MIDI scripts -> OK. "
                           "Обычно причина в принудительном закрытии FL: "
                           "настройки MIDI сохраняются только при нормальном выходе.")
        return out

    def channels(self):
        import q
        import json
        return _parse(self._live(q.LIVE_EXPR["channels"]))

    def plugins(self):
        return self._live("[list(x) for x in %s]" % q_plugins())

    def tempo(self):
        import q
        return self._live(q.LIVE_EXPR["tempo"])

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

    def unmask(self, stems: dict, masker: str, masked: str,
               *args, **kwargs) -> dict:
        """Сколько вернётся в дорожке, если убрать соседа (разгрузка)."""
        import listen_layer as L
        return L.unmask(stems, masker, masked, *args, **kwargs)

    def match(self, path_a: str, path_b: str, min_len: float = 8.0):
        """Сопоставить однотипные секции двух файлов по громкости."""
        import listen_layer as L
        return L.match_sections(path_a, path_b, min_len)

    def psycho(self, path: str, a: float | None = None, b: float | None = None,
               which=("roughness", "tonality", "fluctuation")):
        """ECMA-418-2: roughness (с полосами), tonality (с полосами), fluctuation.

        Медленно по своей природе (~4.5× реального времени) — для точечных
        проверок, а не для потока.
        """
        import listen_layer as L
        sec = (a, b) if a is not None and b is not None else None
        return L.psycho(path, sec, which)

    def _py312(self) -> str:
        return os.path.join(os.path.dirname(HERE), ".venv312", "Scripts", "python.exe")

    def style(self, path: str, start: float | None = None,
              dur: float | None = None) -> dict:
        """Стиль сведения: 2048-мерный эмбеддинг MIXSTYLE."""
        import subprocess
        cmd = [self._py312(), os.path.join(HERE, "mixstyle.py"), "embed", path]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        return {"raw": (out.stdout or "").strip(), "err": (out.stderr or "").strip()[:200]}

    def style_sim(self, a: str, b: str) -> float | None:
        """Близость стиля сведения двух файлов (не тембра!)."""
        import subprocess
        cmd = [self._py312(), os.path.join(HERE, "mixstyle.py"), "sim", a, b]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        try:
            return float((out.stdout or "").strip().splitlines()[-1])
        except Exception:
            return None

    def describe(self, path: str, start: float | None = None,
                 dur: float | None = None) -> dict:
        """Узнавание: размерность и норма эмбеддинга (не измерение!)."""
        import subprocess
        cmd = [self._py312(), os.path.join(HERE, "describe.py"), "embed", path]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return {"raw": (out.stdout or "").strip(), "err": (out.stderr or "").strip()[:200]}

    def nearest(self, query: str, paths: list[str], start: float | None = None,
                dur: float | None = None) -> list[tuple[str, float]]:
        """Поиск самого похожего среди файлов (косинус эмбеддингов)."""
        import subprocess
        cmd = [self._py312(), os.path.join(HERE, "describe.py"), "near", query] + list(paths)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        res = []
        for line in (out.stdout or "").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                try:
                    res.append((parts[1], float(parts[0])))
                except ValueError:
                    continue
        return res

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

    def route(self, track: int, dest: int, level: float):
        """Включить/выключить посыл (level=0 выключает)."""
        return _State._live("mixer.setRouteToLevel(%d, %d, %r)" % (track, dest, level))

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

    def profile_tracks(self, seconds: float = 8.0, start: float = 0.0,
                       cache: str | None = None) -> dict:
        """Снять профили всех дорожек по очереди (соло-захват каждой).

        Медленно (по секунд на дорожку), но делается один раз: дальше живые
        пики дают оценку микса без повторных захватов.
        """
        import json
        import track_capture as T
        import liveview as V

        n = int(_State._live("mixer.trackCount()"))
        names = [str(_State._live("mixer.getTrackName(%d)" % i)) for i in range(n)]
        out = {"tracks": [], "names": names}
        for i in range(n):
            try:
                path = T.grab_any(seconds, start, i)
                prof = V.profile_file(path)
                prof["i"] = i
                prof["name"] = names[i]
                out["tracks"].append(prof)
            except Exception as e:
                out["tracks"].append({"i": i, "name": names[i], "error": str(e)[:80]})
        if cache:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        return out

    def estimate_live(self, profiles: dict, offset_db: float = 0.0) -> dict:
        """Живая оценка микса: профили + текущие пики."""
        import liveview as V
        peaks = self.peaks()
        usable = [p for p in profiles.get("tracks", []) if "levels_db" in p]
        pl = [peaks[p["i"]] if p["i"] < len(peaks) else 0.0 for p in usable]
        r = V.estimate(usable, pl, offset_db=offset_db)
        r["names"] = [p.get("name", "") for p in usable]
        return r

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
    """Создание объектов. Живые операции — через Frida, без перезагрузки."""

    LIVE = "fl_live_insert.py"
    PORT = 9790

    def _py312(self) -> str:
        return os.path.join(os.path.dirname(HERE), ".venv312", "Scripts", "python.exe")

    def _live_run(self, argv: list, port: int | None = None, timeout: int = 240) -> dict:
        """Выполнить живую операцию через стенд fl_live_insert."""
        import json
        import subprocess
        tool = os.path.join(HERE, self.LIVE)
        cmd = [self._py312(), tool] + [str(a) for a in argv] + ["--port", str(port or self.PORT)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        txt = p.stdout or ""
        res = None
        for line in reversed(txt.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    res = json.loads(line)
                    break
                except Exception:
                    continue
        return {"ok": p.returncode == 0, "result": res,
                "raw": txt[-600:], "err": (p.stderr or "")[-300:]}

    _LIVE = None  # (Daemon, port, flp) — стенд, поднятый в этом же процессе

    def _live_op(self, op: str, **kw) -> dict:
        """Операция через живой стенд текущего процесса (без подпроцесса)."""
        dm, port, _flp = self._LIVE
        r = dm.run_op(dict(op=op, **kw))
        return {"ok": bool(r.get("ok")), "result": r.get("data"),
                "err": r.get("error", ""), "port": port}

    def live_start(self, flp: str, port: int | None = None) -> dict:
        """Поднять FL под Frida в этом процессе. Требует frida (окружение 3.12)."""
        try:
            import live_create
        except ImportError:
            import fl_live_insert as live_create
        port = port or self.PORT
        dm = live_create.Daemon(flp, port)
        dm.start()
        _Create._LIVE = (dm, port, flp)
        return {"started": True, "pid": dm.pid, "port": port, "flp": flp}

    def live_stop(self) -> dict:
        """Закрыть стенд: отсоединиться от Frida и убрать FL."""
        if not self._LIVE:
            return {"stopped": False, "reason": "стенд не поднят"}
        dm = self._LIVE[0]
        try:
            dm.closing = True
            dm.session.detach()
        except Exception:
            pass
        try:
            import safe_fl
            safe_fl.kill_all()
        except Exception:
            pass
        _Create._LIVE = None
        return {"stopped": True}

    def live_insert(self, track: int, slot: int, plugin: str, port: int | None = None) -> dict:
        """Поставить плагин в слот вживую (имя из базы FL или путь к .dll)."""
        if self._LIVE:
            return self._live_op("insert", track=track, slot=slot, plugin=plugin)
        return self._live_run(["insert", track, slot, plugin], port)

    def live_delete(self, track: int, slot: int, port: int | None = None) -> dict:
        """Освободить слот вживую (родная функция движка, DLL выгружается)."""
        if self._LIVE:
            return self._live_op("delete", track=track, slot=slot)
        return self._live_run(["delete", track, slot], port)

    def live_replace(self, track: int, slot: int, plugin: str, port: int | None = None) -> dict:
        """Сменить плагин в слоте вживую (выгрузить и поставить другой)."""
        if self._LIVE:
            return self._live_op("replace", track=track, slot=slot, plugin=plugin)
        return self._live_run(["replace", track, slot, plugin], port)

    def live_slots(self, track: int, port: int | None = None) -> dict:
        """Карта слотов инсерта прямо из памяти."""
        if self._LIVE:
            return self._live_op("slots", track=track)
        return self._live_run(["slots", "--track", track], port)

    # ---- дорожки микшера (живое создание/удаление через движок) ----------------
    # FLEngine_x64.dll 25.2.5:
    #   0xDA7B30  InsertMixerTracks(index, count, flags)  — вставка дорожек
    #   0xDA86C0  DeleteMixerTracks(int *list, lastIdx, flags) — удаление дорожек
    def live_track_add(self, index: int | None = None, count: int = 1,
                       flags: int = 7, port: int | None = None) -> dict:
        """Вставить дорожку микшера вживую.

        index=None — как в меню FL: вставка на месте выбранной дорожки.
        index=N    — явная позиция; count — сколько дорожек сразу.
        Возвращает before/after счётчика движка и mixer.trackCount() из API.
        """
        if self._LIVE:
            return self._live_op("trackadd", index=index, count=count, flags=flags)
        argv = ["trackadd"]
        if index is not None:
            argv += ["--index", str(index)]
        argv += ["--count", str(count), "--flags", str(flags)]
        return self._live_run(argv, port)

    def live_track_delete(self, index: int, flags: int = 0x12,
                          port: int | None = None) -> dict:
        """Удалить дорожку микшера вживую (штатная функция движка, с undo-записью)."""
        if self._LIVE:
            return self._live_op("trackdel", index=index, flags=flags)
        return self._live_run(["trackdel", str(index), "--flags", str(flags)], port)

    def live_track_count(self, port: int | None = None) -> dict:
        """Число дорожек микшера из памяти движка (для проверки add/delete)."""
        if self._LIVE:
            return self._live_op("trackcount")
        return self._live_run(["trackcount"], port)

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


class _Judge:
    """Приёмка правки по числам: значимость, цель, побочный ущерб, журнал."""

    def verdict(self, before: dict, after: dict, goals: dict | None = None,
                corridor: dict | None = None, tolerance: float = 0.0) -> dict:
        import judge as J
        return J.judge(before, after, goals or {}, corridor, tolerance)

    def log(self, entry: dict) -> None:
        import judge as J
        J.log(entry)

    def flatten(self, d: dict) -> dict:
        import judge as J
        return J.flatten(d)


state = _State()
link = _Link()
hear = _Hear()
act = _Act()
monitor = _Monitor()
sim = _Sim()
predict = _Predict()
judge = _Judge()
create = _Create()


def safe() -> _Safe:
    """Транзакционный слой: снапшот и откат микшера."""
    return _Safe()
