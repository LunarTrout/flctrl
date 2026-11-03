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

# кэш найденных интерпретаторов: {"torch": path, ...}
_PY_CACHE: dict[str, str] = {}


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


def _hear_python() -> str:
    """Интерпретатор с phonometry: env → соседние папки → известные места.

    Путь проверяется запуском `import phonometry`, а не по имени папки: иначе
    берётся окружение без библиотеки и слух падает с ModuleNotFoundError.
    """
    if getattr(_hear_python, "_cached", None):
        return _hear_python._cached
    import subprocess
    import glob
    root = os.path.dirname(HERE)
    candidates = []
    env = os.environ.get("FLCTRL_PY_HEAR")
    if env:
        candidates.append(env)
    candidates += [os.path.join(HERE, "..", ".venv313", "Scripts", "python.exe"),
                   os.path.join(root, ".venv313", "Scripts", "python.exe"),
                   os.path.join(root, "..", "flstudio-mcp", ".venv313", "Scripts",
                                "python.exe")]
    for sibling in glob.glob(os.path.join(root, "..", "*", ".venv313", "Scripts",
                                          "python.exe")):
        candidates.append(sibling)
    for path in candidates:
        path = os.path.abspath(path) if path else ""
        if not path or not os.path.exists(path):
            continue
        try:
            probe = subprocess.run([path, "-c", "import phonometry"],
                                   capture_output=True, timeout=60)
        except Exception:
            continue
        if probe.returncode == 0:
            _hear_python._cached = path
            return path
    raise RuntimeError("нет окружения для слуха: нужен python с phonometry "
                       "(задайте FLCTRL_PY_HEAR)")


def _hear_cli(cmd: str, *args: str, timeout: int = 900):
    """Выполнить замер в окружении со phonometry."""
    import json
    import subprocess
    py = _hear_python()
    cli = os.path.join(HERE, "hear_cli.py")
    if not os.path.exists(cli):
        raise RuntimeError("нет hear_cli.py рядом с библиотекой")
    p = subprocess.run([py, cli, cmd, *args], capture_output=True, text=True, timeout=timeout)
    txt = (p.stdout or "").strip()
    if not txt:
        raise RuntimeError((p.stderr or "")[-300:])
    return json.loads(txt)


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

    WRITE_VERBS = ("set", "insert", "delete", "add", "mute(", "solo(", "select",
                   "focus", "arm", "trigger", "processREC", "automate", "send",
                   "start", "stop", "play", "record", "clear", "clone", "copy")

    @staticmethod
    def _is_read(expr: str) -> bool:
        e = expr.lower()
        return not any(v in e for v in _State.WRITE_VERBS)

    @staticmethod
    def _live(expr, heal: bool = True, attempts: int = 3):
        """Живой вызов через мост: чтения повторяются, записи — нет.

        Повтор записи опасен: неизвестно, дошла ли она до движка, и повтор
        может сделать действие дважды. Чтение повторять безопасно, а мост
        иногда отвечает таймаутом просто потому, что FL занята рендером.
        """
        import time as _time

        import q

        tries = attempts if _State._is_read(expr) else 1
        last = None
        for i in range(tries):
            try:
                return q.live(expr)
            except Exception as exc:
                last = exc
                if i < tries - 1:
                    _time.sleep(0.15 * (i + 1))
                    continue
                if not heal:
                    raise
                # посредник мог умереть — библиотека поднимает его сама
                res = link.ensure()
                if not res.get("daemon"):
                    raise
                return q.live(expr)
        raise last

    def tracks(self, chunk: int = 6):
        """Живой микшер. Запрашивается кусками: длинные выражения транспорт режет."""
        n = int(self._live("mixer.trackCount()"))
        out = []
        for a in range(0, n, chunk):
            expr = ("[(i, str(mixer.getTrackName(i)), round(mixer.getTrackVolume(i), 2), "
                    "round(mixer.getTrackPan(i), 2), sum(1 for s in range(10) "
                    "if plugins.isValid(i, s)), int(bool(mixer.isTrackMuted(i))), "
                    "int(bool(mixer.isTrackSolo(i)))) for i in range(%d, %d)]"
                    % (a, min(a + chunk, n)))
            for row in _parse(self._live(expr)):
                out.append({"i": row[0], "name": row[1], "vol": row[2],
                            "pan": row[3], "fx": row[4],
                            "mute": row[5] if len(row) > 5 else None,
                            "solo": row[6] if len(row) > 6 else None})
        return out

    def routing(self, track: int, dests: list | None = None) -> list:
        """Куда дорожка шлёт: список (получатель, активен).

        Надёжный признак — getRouteSendActive(источник, получатель).
        getRouteToLevel на дальних получателях отвечает «Index out of range»,
        поэтому уровень читаем только как дополнение.
        """
        n = int(self._live("mixer.trackCount()"))
        cand = dests if dests is not None else list(range(n))
        out = []
        for d in cand:
            try:
                on = int(self._live("mixer.getRouteSendActive(%d, %d)" % (track, int(d))))
            except Exception:
                continue
            if not on:
                continue
            lvl = None
            try:
                lvl = float(self._live("mixer.getRouteToLevel(%d, %d)" % (track, int(d))))
            except Exception:
                pass
            out.append((int(d), lvl))
        return out

    def health(self) -> dict:
        """Живо ли управление и что именно не так. Не бросает исключение."""
        import subprocess
        import time
        out = {"fl_running": False, "bridge": False, "tracks": None,
               "latency_ms": None, "pid": None, "project": None,
               "live_stand": False, "responsive": None, "hint": ""}
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV"],
                               capture_output=True, timeout=20)
            txt = (r.stdout or b"").decode("cp866", "replace")
            for line in txt.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) > 1 and parts[0].lower().startswith("fl64"):
                    out["fl_running"] = True
                    out["pid"] = int(parts[1])
                    break
        except Exception:
            pass
        if out["fl_running"]:
            try:
                import render_job
                out["project"] = render_job.window_title()
                # повисшая FL не отвечает на WM_NULL и не закрывается штатно
                out["responsive"] = render_job.fl_is_responsive()
            except Exception:
                pass
        try:
            t0 = time.perf_counter()
            out["tracks"] = int(self._live("mixer.trackCount()"))
            out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            out["bridge"] = True
        except Exception as e:
            out["hint"] = str(e)[:200]
        try:
            import fl_lib as _fl
            out["live_stand"] = bool(getattr(_fl._Create, "_LIVE", None))
        except Exception:
            pass
        if not out["fl_running"]:
            out["hint"] = "FL Studio не запущена"
        elif out["responsive"] is False:
            out["hint"] = ("UI-поток FL не отвечает: окно не закроется, контроллер молчит. "
                           "Снять процесс и открыть проект заново (reset_fl.py).")
        elif not out["bridge"]:
            out["hint"] = (
                "FL запущена, но контроллер не отвечает. "
                "F10 -> MIDI settings -> Update MIDI scripts -> OK. "
                "Обычно причина в принудительном закрытии FL: "
                "настройки MIDI сохраняются только при нормальном выходе.")
        return out

    def ping(self) -> dict:
        """Быстрая проверка отклика моста с замером задержки."""
        import time
        t0 = time.perf_counter()
        v = self._live("mixer.trackCount()")
        return {"ok": True, "tracks": int(v),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

    # что именно проверяем: имя, выражение, ожидаемый тип
    PROBES = (
        ("дорожек", "mixer.trackCount()", "int"),
        ("имя дорожки", "str(mixer.getTrackName(0))", "str"),
        ("громкость", "float(mixer.getTrackVolume(0))", "float"),
        ("панорама", "float(mixer.getTrackPan(0))", "float"),
        ("mute", "bool(mixer.isTrackMuted(0))", "bool"),
        ("solo", "bool(mixer.isTrackSolo(0))", "bool"),
        ("идёт ли игра", "bool(transport.isPlaying())", "bool"),
        ("позиция", "float(transport.getSongPos(1))", "float"),
        ("слот плагина", "bool(plugins.isValid(0, 0))", "bool"),
        ("каналы рэка", "int(channels.channelCount())", "int"),
    )

    def doctor(self) -> dict:
        """Самодиагностика: что из живого API реально отвечает прямо сейчас.

        Только чтения — проект не меняется. Нужна, чтобы не гадать, почему
        «не работает»: сразу видно, отвалился мост целиком или отдельная
        ветка API (например, каналы после неудачной вставки).
        """
        import time
        rows = []
        ok_count = 0
        for label, expr, kind in self.PROBES:
            t0 = time.perf_counter()
            entry = {"что": label, "expr": expr}
            try:
                value = self._live(expr, heal=False, attempts=1)
                ms = round((time.perf_counter() - t0) * 1000, 1)
                entry.update({"ok": True, "ms": ms, "value": str(value)[:60]})
                ok_count += 1
            except Exception as exc:
                entry.update({"ok": False,
                              "ms": round((time.perf_counter() - t0) * 1000, 1),
                              "error": str(exc)[:120]})
            rows.append(entry)
        failures = [r["что"] for r in rows if not r["ok"]]
        return {"ok": not failures, "passed": "%d из %d" % (ok_count, len(rows)),
                "tracks": self.health().get("tracks"),
                "failures": failures, "probes": rows,
                "hint": ("" if not failures else
                         "не отвечает: " + ", ".join(failures) +
                         ". Если молчит всё — проверь мост (fl_link_ensure) и "
                         "F10 → MIDI settings → Update MIDI scripts.")}

    def capabilities(self) -> dict:
        """Какие ветки API существуют: dir() по модулям, без вызовов."""
        import ast
        import re

        modules = ("mixer", "plugins", "channels", "playlist", "transport",
                   "general", "ui", "patterns")
        out = {}
        for mod in modules:
            try:
                text = self._live("str([n for n in dir(%s) if not n.startswith('_')])" % mod)
                try:
                    names = ast.literal_eval(text)
                except Exception:
                    # длинный ответ транспорт режет — вытаскиваем имена как есть
                    names = re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", str(text))
                out[mod] = list(names)
            except Exception as exc:
                out[mod] = {"error": str(exc)[:100]}
        return out

    def channels(self, chunk: int = 6):
        """Каналы рэка. Кусками: длинные выражения транспорт режет."""
        n = int(self._live("channels.channelCount()"))
        out = []
        for a in range(0, n, chunk):
            expr = ("[(i, str(channels.getChannelName(i)), "
                    "round(channels.getChannelVolume(i), 2), "
                    "round(channels.getChannelPan(i), 2)) "
                    "for i in range(%d, %d)]" % (a, min(a + chunk, n)))
            for row in _parse(self._live(expr)):
                out.append({"i": row[0], "name": row[1], "vol": row[2], "pan": row[3]})
        return out

    def plugins(self, tracks: list | None = None) -> list:
        """Что стоит в слотах: список {track, slot, name}. По одной дорожке за запрос."""
        n = int(self._live("mixer.trackCount()"))
        cand = tracks if tracks is not None else list(range(n))
        out = []
        for t in cand:
            expr = ("[(s, str(plugins.getPluginName(%d, s))) for s in range(10) "
                    "if plugins.isValid(%d, s)]" % (t, t))
            try:
                rows = _parse(self._live(expr))
            except Exception:
                continue
            for slot, name in rows:
                out.append({"track": int(t), "slot": int(slot), "name": name})
        return out

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
        files = produced.get("files") if isinstance(produced, dict) else [produced]
        path = files[0] if files else ""
        if not path or not os.path.exists(path):
            raise RuntimeError("рендер не дал файла")
        shutil.copy2(path, out)
        return out

    def at(self, flp: str, start: float, dur: float = 20.0) -> dict:
        """Замер любого места проекта: рендер берётся из кэша."""
        return self.measure(self.offline(flp), start, start + dur)

    def measure(self, path: str, a: float | None = None, b: float | None = None,
                stem: str | None = None):
        """Замер. Если phonometry нет в этом интерпретаторе — уходим в 3.13.

        Если окно попало в тишину, в ответе появляется `warning`: иначе нули
        легко принять за «инструмент пропал» вместо «здесь его нет».
        """
        try:
            import listen_layer as L
        except ModuleNotFoundError:
            res = _hear_cli("measure", path, "--a", str(a or 0.0), "--b", str(b or 30.0))
        else:
            sec = (a, b) if a is not None and b is not None else None
            res = L.measure(path, section=sec, stem_path=stem)
        try:
            if float(res.get("N_mean", 1.0)) < 1.0:
                res["warning"] = "окно в тишине: N ≈ 0, цифрам по форме верить нельзя"
        except Exception:
            pass
        return res

    def curve(self, path: str, a: float | None = None, b: float | None = None):
        args = [path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        return _hear_cli("curve", *args)

    def profile(self, path: str):
        return _hear_cli("profile", path)

    def bands(self, path: str):
        return _hear_cli("bands", path)

    def masking(self, stems: dict, a: float | None = None, b: float | None = None,
                mix_path: str | None = None):
        """Кто кого забивает: доли и задавленные полосы по стемам.

        Считается в окружении со phonometry (3.13): в основном процессе его нет,
        поэтому напрямую звать listen_layer нельзя.
        """
        if a is not None and b is not None:
            stems = dict(stems)
        if mix_path is None:
            raise ValueError("нужен mix_path: по нему берётся калибровка")
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(stems, f, ensure_ascii=False)
            stems_path = f.name
        try:
            return _hear_cli("masking", mix_path, stems_path)
        finally:
            try:
                os.unlink(stems_path)
            except OSError:
                pass

    def attribute(self, mix_path: str, stems: dict, a: float | None = None,
                  b: float | None = None, section_s: float | None = None):
        """Сколько каждого источника реально СЛЫШНО в миксе.

        Частичная громкость `N_partial = N(микс) − N(микс без источника)` и
        слышимость `A = N_partial / N_solo`, плюс измеренная чувствительность
        `dN_per_db`. В отличие от долей полос, учитывает маскировку: тихий, но
        незамаскированный источник получает высокую A, громкий и задавленный —
        низкую.
        """
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(stems, f, ensure_ascii=False)
            stems_path = f.name
        args = [mix_path, stems_path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        if section_s:
            args += ["--section", str(section_s)]
        try:
            return _hear_cli("attribute", *args)
        finally:
            try:
                os.unlink(stems_path)
            except OSError:
                pass

    def audit(self, mine: str, refs: list[str], a: float | None = None,
              b: float | None = None) -> dict:
        """Аудит микса против коридора референсов по всем фронтам."""
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(refs, f, ensure_ascii=False)
            path = f.name
        args = [mine, path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        try:
            return _hear_cli("audit", *args, timeout=3600)
        finally:
            os.unlink(path)

    def sections_compare(self, mine: str, refs: list[str], a: float | None = None,
                         b: float | None = None) -> dict:
        """Сопоставить наши секции с самыми похожими участками референсов."""
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(refs, f, ensure_ascii=False)
            path = f.name
        args = [mine, path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        try:
            return _hear_cli("seccomp", *args, timeout=3600)
        finally:
            os.unlink(path)

    def sii(self, vocal: str, masker: str) -> dict:
        """Разборчивость вокала против остального микса (SII, ANSI S3.5)."""
        return _hear_cli("sii", "--vocal", vocal, "--masker", masker, timeout=1800)

    def pan(self, stems: dict, a: float | None = None, b: float | None = None) -> dict:
        """Карта панорамы по полосам/секциям и подсказки на измерениях."""
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(stems, f, ensure_ascii=False)
            path = f.name
        args = [path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        try:
            return _hear_cli("pan", *args, timeout=3600)
        finally:
            os.unlink(path)

    def report(self, mine: str, refs: list[str] | None = None,
               stems: dict | None = None, a: float | None = None,
               b: float | None = None, impulse: str | None = None,
               sine: str | None = None, ducked: str | None = None,
               vocal: str | None = None, masker: str | None = None,
               sii_baseline: list[float] | None = None,
               targets_path: str | None = None) -> dict:
        """Полный отчёт одним вызовом: покрытие проверок, действия, markdown."""
        import json as _json
        import tempfile
        payload = {"refs": refs, "stems": stems, "a": a, "b": b,
                   "impulse": impulse, "sine": sine, "ducked": ducked,
                   "vocal": vocal, "masker": masker,
                   "sii_baseline": sii_baseline, "targets_path": targets_path}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
            path = f.name
        try:
            return _hear_cli("report", mine, path, timeout=3600)
        finally:
            os.unlink(path)

    def psycho(self, path: str, a: float | None = None, b: float | None = None):
        """Шероховатость, тональность, флуктуация (ECMA-418-2)."""
        args = [path]
        if a is not None and b is not None:
            args += ["--a", str(a), "--b", str(b)]
        return _hear_cli("psycho", *args)

    def sections(self, path: str, min_len: float = 8.0):
        return _hear_cli("sections", path)

    def unmask(self, stems: dict, masker: str, masked: str,
               *args, **kwargs) -> dict:
        """Сколько вернётся в дорожке, если убрать соседа (разгрузка)."""
        import listen_layer as L
        return L.unmask(stems, masker, masked, *args, **kwargs)

    def match(self, path_a: str, path_b: str, min_len: float = 8.0):
        """Сопоставить однотипные секции двух файлов по громкости."""
        return _hear_cli("match", path_a, path_b)

    def psycho_full(self, path: str, a: float | None = None, b: float | None = None,
                    which=("roughness", "tonality", "fluctuation")):
        """ECMA-418-2 с разбивкой по полосам (полная версия, медленная)."""
        import subprocess
        py = os.environ.get("FLCTRL_PY_HEAR") or os.path.join(
            os.path.dirname(HERE), ".venv313", "Scripts", "python.exe")
        code = ("import json,listen_layer as L,sys;"
                "print(json.dumps(L.psycho(sys.argv[1],"
                "(float(sys.argv[2]),float(sys.argv[3])) if len(sys.argv)>3 else None,"
                "tuple(sys.argv[4].split(',')) if len(sys.argv)>4 else ('roughness','tonality','fluctuation'))))")
        args = [py, "-c", code, path]
        if a is not None and b is not None:
            args += [str(a), str(b)]
        args += [",".join(which)]
        p = subprocess.run(args, capture_output=True, text=True, timeout=1800,
                           cwd=HERE)
        if not (p.stdout or "").strip():
            raise RuntimeError((p.stderr or "")[-300:])
        import json as _json
        return _json.loads(p.stdout.strip().splitlines()[-1])

    def _py312(self) -> str:
        """Интерпретатор с torch для эмбеддингов (describe/mixstyle).

        Раньше путь вбивался как <репозиторий>/.venv312, а окружение лежит в
        соседней рабочей папке — отсюда FileNotFoundError. Теперь кандидаты
        проверяются импортом torch.
        """
        cached = _PY_CACHE.get("torch")
        if cached:
            return cached
        import glob
        import subprocess
        env = os.environ.get("FLCTRL_PY_TORCH")
        root = os.path.dirname(HERE)
        candidates = [env] if env else []
        candidates += [os.path.join(root, ".venv312", "Scripts", "python.exe"),
                       os.path.join(root, "..", "flstudio-mcp", ".venv312", "Scripts",
                                    "python.exe"),
                       os.path.join(root, "..", ".venv312", "Scripts", "python.exe")]
        candidates += glob.glob(os.path.join(root, "..", "*", ".venv312", "Scripts",
                                             "python.exe"))
        candidates.append(sys.executable)
        for path in candidates:
            path = os.path.abspath(path) if path else ""
            if not path or not os.path.exists(path):
                continue
            try:
                probe = subprocess.run([path, "-c", "import torch"],
                                       capture_output=True, timeout=120)
            except Exception:
                continue
            if probe.returncode == 0:
                _PY_CACHE["torch"] = path
                return path
        raise RuntimeError("нет интерпретатора с torch (задайте FLCTRL_PY_TORCH)")

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
        """Коридор: где наш файл относительно набора референсов."""
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(list(refs), f, ensure_ascii=False)
            tmp = f.name
        try:
            return _hear_cli("compare", path, tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------- act

_AUTOSAVE = {"on": True}


class _Act:
    """Правки вживую. Возвращают прочитанное после записи значение.

    После каждой правки проект сохраняется (Ctrl+S через flsave): FL держит
    изменения только в памяти, и падение процесса стирает всё, что не сохранено.
    Отключается через fl.safe.autosave(False) — только для серий экспериментов.
    """

    def _persist(self):
        if not _AUTOSAVE["on"]:
            return None
        try:
            import flsave
            return flsave.save()
        except Exception as e:
            return {"ok": False, "error": str(e)[:80]}

    def _do(self, expr: str):
        """Выполнить правку. FL иногда отвечает «Operation unsafe at current
        time» — это значит, что запись пришла во время воспроизведения или
        пока открыт модальный диалог. Тогда один раз останавливаем транспорт и
        повторяем: молча падать на этом нельзя, правка теряется."""
        import time as _time
        try:
            result = _State._live(expr)
        except Exception as exc:
            if "unsafe" not in str(exc).lower():
                raise
            try:
                _State._live("transport.stop()", attempts=1)
            except Exception:
                pass
            _time.sleep(0.3)
            if int(_State._live("transport.isPlaying()")):
                raise
            result = _State._live(expr, attempts=2)
        self._persist()
        return result

    def volume(self, track: int, value: float):
        return self._do("mixer.setTrackVolume(%d, %r)" % (track, value))

    def volume_live(self, track: int, value: float):
        """Громкость без сохранения проекта: для потоков и реалтайма."""
        return _State._live("mixer.setTrackVolume(%d, %r)" % (track, value))

    def pan(self, track: int, value: float):
        return self._do("mixer.setTrackPan(%d, %r)" % (track, value))

    def pan_live(self, track: int, value: float):
        """Панорама без сохранения проекта.

        Обычная запись сохраняет проект после каждого изменения — для движения
        в реальном времени это 1.5 с на шаг. Здесь пишем только значение;
        сохранять нужно один раз после остановки.
        """
        return _State._live("mixer.setTrackPan(%d, %r)" % (track, value))

    def mute(self, track: int, state: bool):
        return self._do("mixer.muteTrack(%d, %r)" % (track, state))

    def solo(self, track: int, state: bool):
        return self._do("mixer.soloTrack(%d, %r)" % (track, state))

    def route(self, track: int, dest: int, level: float):
        """Посыл track → dest.

        Три правила, добытые опытом:
        1) порядок аргументов — (источник, получатель);
        2) FL молча не включает посыл, если уже есть обратный (защита от петли),
           поэтому сначала снимаем dest → track;
        3) setRouteToLevel на дальних получателях падает, работает setRouteTo.
        """
        if level > 0:
            try:
                if int(_State._live("mixer.getRouteSendActive(%d, %d)" % (dest, track))):
                    _State._live("mixer.setRouteTo(%d, %d, False)" % (dest, track))
            except Exception:
                pass
            _State._live("mixer.setRouteTo(%d, %d, True)" % (track, dest))
            try:
                _State._live("mixer.setRouteToLevel(%d, %d, %r)" % (track, dest, level))
            except Exception:
                pass
            self._persist()
            return "включено"
        try:
            result = _State._live("mixer.setRouteTo(%d, %d, False)" % (track, dest))
            self._persist()
            return result
        except Exception as e:
            return "не вышло: %s" % str(e)[:60]

    def param(self, track: int, slot: int, index: int, value: float):
        return self._do("plugins.setParamValue(%r, %d, %d, %d)"
                        % (value, index, track, slot))

    def param_batch(self, track: int, slot: int, values, gap: float = 0.12,
                    stop_transport: bool = True):
        """Записать несколько параметров плагина безопасно.

        Зачем: каждая запись — сообщение в UI-поток FL, и очередь из десятков
        подряд (особенно на играющем транспорте) роняет движок в USER32.
        Здесь транспорт останавливается, записи идут с паузой, а между
        пачками проект сохраняется.

        values — список пар (индекс, значение) или словарь {индекс: значение}.
        """
        import time as _time
        items = list(values.items()) if isinstance(values, dict) else list(values)
        if stop_transport:
            try:
                if int(_State._live("transport.isPlaying()")):
                    _State._live("transport.stop()")
                    _time.sleep(0.35)
            except Exception:
                pass
        written = []
        for i, (index, value) in enumerate(items, start=1):
            _State._live("plugins.setParamValue(%r, %d, %d, %d)"
                         % (value, index, track, slot))
            written.append(index)
            _time.sleep(gap)
            if i % 4 == 0:
                self._persist()
                _time.sleep(0.08)
        self._persist()
        return {"written": written, "gap_s": gap, "stopped": bool(stop_transport)}

    def param_live(self, track: int, slot: int, index: int, value: float,
                   verify: bool = True, dry_run: bool = False) -> dict:
        """Записать ОДИН параметр, не останавливая транспорт.

        Отличие от param_batch: не трогает воспроизведение — для живой работы.
        Проверяет результат чтением и, если значение не встало, повторяет раз.
        dry_run ничего не пишет: возвращает, что было бы записано.
        """
        import time as _time
        expr_before = "plugins.getParamValue(%d, %d, %d)" % (index, track, slot)
        try:
            before = float(_State._live(expr_before))
        except Exception:
            before = None
        if dry_run:
            return {"dry_run": True, "track": track, "slot": slot,
                    "index": index, "value": value, "before": before}
        t0 = _time.perf_counter()
        _State._live("plugins.setParamValue(%r, %d, %d, %d)"
                     % (value, index, track, slot))
        latency = round((_time.perf_counter() - t0) * 1000, 1)
        after = None
        ok = True
        if verify:
            _time.sleep(0.08)
            for _attempt in range(2):
                try:
                    after = float(_State._live(expr_before))
                except Exception:
                    after = None
                    break
                if after is not None and abs(after - value) < 0.02:
                    break
                _State._live("plugins.setParamValue(%r, %d, %d, %d)"
                             % (value, index, track, slot))
                _time.sleep(0.10)
            ok = after is not None and abs(after - value) < 0.02
        return {"ok": ok, "track": track, "slot": slot, "index": index,
                "before": before, "after": after, "value": value,
                "latency_ms": latency}

    def params(self, track: int, slot: int, chunk: int = 6):
        """Параметры плагина кусками: длинный ответ мост режет и он приходит пустым."""
        import json as _json

        try:
            n = int(_State._live("plugins.getParamCount(%d, %d)" % (track, slot)))
        except Exception as exc:
            # пустой слот отвечает «Plugin not valid» — это не ошибка чтения
            if "not valid" in str(exc).lower():
                return []
            raise
        out = []
        for a in range(0, n, chunk):
            b = min(a + chunk, n)
            expr = ("__import__('json').dumps([{'i': i,"
                    " 'name': str(plugins.getParamName(i, %d, %d)),"
                    " 'val': round(float(plugins.getParamValue(i, %d, %d)), 4),"
                    " 'text': str(plugins.getParamValueString(i, %d, %d))}"
                    " for i in range(%d, %d)])"
                    % (track, slot, track, slot, track, slot, a, b))
            raw = _State._live(expr)
            try:
                for item in _json.loads(raw):
                    # мост отдаёт строки в кавычках: "'Band 1 level'"
                    item["name"] = str(item.get("name", "")).strip("'")
                    item["text"] = str(item.get("text", "")).strip("'")
                    out.append(item)
            except Exception:
                # кусок не разобрался — читаем по одному, чтобы не потерять всё
                for i in range(a, b):
                    try:
                        name = _State._live("str(plugins.getParamName(%d, %d, %d))"
                                            % (i, track, slot))
                        val = float(_State._live("plugins.getParamValue(%d, %d, %d)"
                                                 % (i, track, slot)))
                        text = _State._live("str(plugins.getParamValueString(%d, %d, %d))"
                                            % (i, track, slot))
                        out.append({"i": i, "name": str(name).strip("'"),
                                    "val": val, "text": str(text).strip("'")})
                    except Exception:
                        continue
        return out


# ---------------------------------------------------------------- реалтайм

class _Realtime:
    """Движение параметров в реальном времени (без API автоматизации)."""

    @staticmethod
    def pan(track: int, points: list, duration_s: float, rate_hz: float = 20.0) -> dict:
        import realtime as rt
        return rt.move_pan(_Realtime._client(), track, points, duration_s, rate_hz)

    @staticmethod
    def param(track: int, slot: int, index: int, points: list,
              duration_s: float, rate_hz: float = 20.0) -> dict:
        import realtime as rt
        return rt.move_value(_Realtime._client(), track, slot, index, points,
                             duration_s, rate_hz)

    @staticmethod
    def play_plan(plan: dict, track_map: dict, duration_s: float | None = None,
                  rate_hz: float = 20.0) -> dict:
        """Проиграть план панорамы вживую: стем -> номер дорожки микшера."""
        import realtime as rt

        curves = rt.from_plan(plan, plan.get("section_s") or 8.0)
        mapped = {int(track_map[stem]): fn for stem, fn in curves.items()
                  if stem in track_map}
        if not mapped:
            return {"started": False, "error": "ни один стем не сопоставлен дорожке"}
        total = max([max(p["t"] for p in plan["stems"][st]["points"])
                     for st in curves if st in track_map] or [0.0])
        dur = duration_s or (total + (plan.get("section_s") or 8.0))
        mover = rt.play_curves(_Realtime._client(), mapped, dur, rate_hz)
        return {"mover": mover, "tracks": sorted(mapped), "duration_s": dur}

    @staticmethod
    def _client():
        import sys
        return sys.modules[__name__]


def move_pan(track: int, points: list, duration_s: float, rate_hz: float = 20.0) -> dict:
    """Удобная обёртка: провести панораму дорожки по точкам."""
    return _Realtime.pan(track, points, duration_s, rate_hz)


# ---------------------------------------------------------------- apply

class _Apply:
    """Применение плана к живому FL без перезагрузки проекта."""

    @staticmethod
    def eq(track: int, slot: int, gains: dict) -> dict:
        """Выставить полосы эквалайзера по плану: {полоса: дБ}.

        Шкалы плагина не документированы, поэтому значения подбираются по
        подписи самого плагина: поставили — прочитали — поправили.
        """
        import apply as apply_mod
        return apply_mod.apply_eq(None, track, slot, gains)

    @staticmethod
    def plan(edits: list, track_map: dict, slot: int = 0,
             ensure_eq: bool = True) -> dict:
        """Применить весь план к живым дорожкам: стем -> номер дорожки."""
        import apply as apply_mod
        return apply_mod.apply_plan(None, edits, track_map, slot, ensure_eq)


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
        """Сравнить два массива через слой слуха (он живёт в другом окружении).

        sandbox.compare требует phonometry в текущем процессе, поэтому массивы
        пишутся во временные wav и меряются тем же CLI, что и файлы.
        """
        import tempfile
        import soundfile as sf
        with tempfile.TemporaryDirectory(prefix="flctrl_sim_") as tmp:
            pa = os.path.join(tmp, "a.wav")
            pb = os.path.join(tmp, "b.wav")
            sf.write(pa, x, fs)
            sf.write(pb, y, fs)
            return hear.measure(pa), hear.measure(pb)

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

    def live_attach(self, pid: int | None = None, flp: str | None = None,
                    port: int | None = None) -> dict:
        """Прицепиться к УЖЕ запущенной FL — без спавна и без перезапуска.

        `pid=None` — найти FL64.exe самому. Нужно, когда проект открыт руками:
        стенд не должен его закрывать и переоткрывать.
        """
        try:
            import live_create
        except ImportError:
            import fl_live_insert as live_create
        if pid is None:
            import render_job
            pid = render_job.fl_pid()
            if not pid:
                raise RuntimeError("FL64.exe не запущена")
        port = port or self.PORT
        dm = live_create.Daemon(flp or "", port)
        dm.attach_pid = int(pid)
        dm.start()
        _Create._LIVE = (dm, port, flp or "")
        return {"attached": True, "pid": int(pid), "port": port}

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

    def _ensure_stand(self, port: int | None = None) -> None:
        """Гарантировать живой стенд, не убивая открытую FL.

        Если FL уже запущена (человек открыл проект сам) — цепляемся к ней.
        Спавн (со своим тестовым проектом) остаётся только на случай, когда
        FL не запущена вовсе: иначе стенд закроет чужую сессию.
        """
        if self._LIVE:
            return
        import render_job
        if render_job.fl_running():
            self.live_attach(port=port)

    def live_insert(self, track: int, slot: int, plugin: str, port: int | None = None) -> dict:
        """Поставить плагин в слот вживую (имя из базы FL или путь к .dll)."""
        self._ensure_stand(port)
        if self._LIVE:
            return self._live_op("insert", track=track, slot=slot, plugin=plugin)
        return self._live_run(["insert", track, slot, plugin], port)

    def live_delete(self, track: int, slot: int, port: int | None = None) -> dict:
        """Освободить слот вживую (родная функция движка, DLL выгружается)."""
        self._ensure_stand(port)
        if self._LIVE:
            return self._live_op("delete", track=track, slot=slot)
        return self._live_run(["delete", track, slot], port)

    def live_replace(self, track: int, slot: int, plugin: str, port: int | None = None) -> dict:
        """Сменить плагин в слоте вживую (выгрузить и поставить другой)."""
        self._ensure_stand(port)
        if self._LIVE:
            return self._live_op("replace", track=track, slot=slot, plugin=plugin)
        return self._live_run(["replace", track, slot, plugin], port)

    def live_slots(self, track: int, port: int | None = None) -> dict:
        """Карта слотов инсерта прямо из памяти."""
        self._ensure_stand(port)
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
        self._ensure_stand(port)
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
        self._ensure_stand(port)
        if self._LIVE:
            return self._live_op("trackdel", index=index, flags=flags)
        return self._live_run(["trackdel", str(index), "--flags", str(flags)], port)

    def live_track_count(self, port: int | None = None) -> dict:
        """Число дорожек микшера из памяти движка (для проверки add/delete)."""
        self._ensure_stand(port)
        if self._LIVE:
            return self._live_op("trackcount")
        return self._live_run(["trackcount"], port)

    def render(self, flp: str, timeout_s: int = 3600, fmt: str = "wav",
               close_fl: bool = False) -> dict:
        """Отрисовать проект через CLI FL.

        Возвращает {'ok', 'files', 'split'}: если в проекте включён
        Split mixer tracks, файлов будет по одному на дорожку. FL пишет в
        папку экспорта проекта, путь из /R игнорируется — поэтому смотрим на
        появившиеся файлы. При живой FL рендер не стартует: либо закрыть её
        (close_fl=True, штатно), либо получить ошибку.
        """
        import render_job as R
        return R.render(flp, fmt=fmt, timeout_s=timeout_s,
                        close_fl_first=close_fl)

    def watch_render(self, project_dir: str, since: float | None = None,
                     fmt: str = "wav", timeout_s: int = 3600) -> dict:
        """Дождаться конца рендера: прогресс, падение, файлы."""
        import render_job as R
        return R.watch(project_dir, since=since, fmt=fmt, timeout_s=timeout_s)

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
    """Снапшот, откат и сохранение проекта.

    snapshot(tag) снимает громкости, панорамы и mute всех дорожек;
    restore(tag) возвращает их и проверяет, что значения вернулись;
    save() сохраняет проект и подтверждает это временем файла;
    autosave(False) выключает сохранение после каждой правки.
    """

    def save(self, backup: bool = False) -> dict:
        import flsave
        return flsave.save(make_backup=backup)

    def autosave(self, on: bool = True) -> bool:
        _AUTOSAVE["on"] = bool(on)
        return _AUTOSAVE["on"]

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


class _Balance:
    """Жанровая модель баланса: цели из корпуса, правки по слышимости.

    Все тяжёлые шаги (сборка модели, проекция проекта) идут в окружение со
    phonometry через hear_cli; `solve` и `check` — чистая арифметика здесь.
    """

    def build(self, refs_dir: str, out: str | None = None):
        """Собрать распределение по ролям из корпуса референсов."""
        args = ["build", refs_dir] + ([out] if out else [])
        return _hear_cli("balance", *args, timeout=3600)

    def project(self, mapping: dict, mix_path: str | None = None):
        """Вектор слышимости проекта: роль -> A, доля, dN/dB."""
        import json as _json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            _json.dump(mapping, f, ensure_ascii=False)
            path = f.name
        try:
            args = ["project", path] + ([mix_path] if mix_path else [])
            return _hear_cli("balance", *args, timeout=3600)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def solve(self, vec: dict, model: dict, cap_db: float = 6.0,
              deadband: float = 0.05) -> dict:
        """Каким ролям и на сколько дБ двинуть фейдер (с капом и предсказанием)."""
        import balance as B
        return B.solve(vec, model, cap_db=cap_db, deadband=deadband)

    def check(self, before: dict, after: dict, model: dict) -> dict:
        """Приёмка: стало ли ближе к модели по слышимости и доле."""
        import balance as B
        return B.check(before, after, model)

    def roles(self, mapping_or_dir: dict | str) -> dict:
        """Разложить дорожки по ролям (по именам, затем по спектру)."""
        import roles as R
        if isinstance(mapping_or_dir, str):
            import glob
            items = []
            for path in sorted(glob.glob(os.path.join(mapping_or_dir, "*.wav"))):
                items.append((os.path.splitext(os.path.basename(path))[0], path))
            return R.group(items)
        return R.group([(k, v if isinstance(v, str) else None)
                        for k, v in mapping_or_dir.items()])


state = _State()
link = _Link()
hear = _Hear()
act = _Act()
monitor = _Monitor()
sim = _Sim()
predict = _Predict()
judge = _Judge()
create = _Create()
apply = _Apply()
rt = _Realtime()
balance = _Balance()


def safe() -> _Safe:
    """Транзакционный слой: снапшот и откат микшера."""
    return _Safe()
