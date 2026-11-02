"""flctrl-mcp: MCP-сервер поверх библиотеки flctrl.

Даёт ассистенту один вход ко всему: видеть проект, мерить звук, править
параметры, создавать плагины вживую, проверять решения.

Запуск:
    .venv312\\Scripts\\python.exe flctrl_mcp\\server.py

Окружения: сам сервер живёт на 3.12 (frida, pyflp), а замеры вызывает
у hear_cli.py на 3.13 — слух требует phonometry.

Настройка (Hermes / Claude):
    "mcpServers": {
      "flctrl": {
        "command": "C:/Users/epicc/mcp/flstudio-mcp/.venv312/Scripts/python.exe",
        "args": ["C:/Users/epicc/mcp/flstudio-lib/flctrl_mcp/server.py"]
      }
    }
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "flctrl")
sys.path.insert(0, PKG)

from fastmcp import FastMCP  # noqa: E402

import discovery  # noqa: E402

PKG = discovery.package_dir()
sys.path.insert(0, PKG)

import fl_lib as fl  # noqa: E402

# всё находим на месте: пути не вбиты, окружения ищутся сами
PY_HEAR = None          # выбирается лениво: см. hear_python_checked()
HEAR_CLI = os.path.join(PKG, "hear_cli.py")
if discovery.daemon():
    fl.link.DEFAULT_DAEMON = discovery.daemon()


def hear_python_checked() -> str:
    """Интерпретатор, в котором реально есть phonometry.

    Раньше путь вычислялся один раз при импорте: если сервер поднимался до
    правки поиска окружения, слух оставался сломанным до перезапуска всего
    харнесса. Теперь интерпретатор проверяется вызовом и кэшируется, а при
    неудаче перебираются запасные варианты.
    """
    global PY_HEAR
    if PY_HEAR:
        return PY_HEAR
    candidates = []
    try:
        candidates.append(discovery.hear_python())
    except Exception:
        pass
    env = os.environ.get("FLCTRL_PY_HEAR")
    if env:
        candidates.append(env)
    candidates += [
        r"C:\Users\epicc\mcp\flstudio-mcp\.venv313\Scripts\python.exe",
        os.path.join(os.path.dirname(PKG), ".venv313", "Scripts", "python.exe"),
    ]
    seen = set()
    for path in candidates:
        if not path or path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        probe = subprocess.run([path, "-c", "import phonometry"],
                               capture_output=True)
        if probe.returncode == 0:
            PY_HEAR = path
            return path
    raise RuntimeError("нет интерпретатора с phonometry; задайте FLCTRL_PY_HEAR")

mcp = FastMCP("flctrl", instructions="""FL Studio под управлением из кода: читать проект, мерить звук, править параметры,
создавать плагины вживую.

ПОРЯДОК РАБОТЫ
1. Всегда сначала fl_health. Если bridge=false — fl_link_ensure (он же поднимается сам).
2. Читай состояние инструментами (fl_tracks, fl_plugins, fl_routing), а не догадывайся.
3. Меняй по одному действию, после каждого — чтение обратно.
4. СОХРАНЯЙ ПОСТОЯННО. FL держит правки только в памяти, падение процесса
   стирает всё несохранённое. fl_save после каждой правки или пачки правок;
   fl_set_vol/fl_set_pan/fl_set_param уже сохраняют сами (автосейв включён).
   Отключать автосейв (fl_set_autosave false) — только для серии опытов.
5. Перед правкой звука измерь: fl_measure на файле (рендер или запись).
6. После правки измерь снова и сравни через fl_judge (принимает/отклоняет по числам).

ОСОБЕННОСТИ FL, КОТОРЫЕ ЛОМАЮТ «ОЧЕВИДНЫЕ» РЕШЕНИЯ
* transport.setSongPos принимает ДОЛЮ песни 0..1, не секунды и не биты.
  Длина песни: transport.getSongLength(1). Иначе перемотка молча не работает.
* Маршруты: порядок аргументов (ИСТОЧНИК, ПОЛУЧАТЕЛЬ). FL молча НЕ включает
  посыл, если уже есть обратный (защита от петли) — сначала сними обратный.
  getRouteToLevel падает с Index out of range на дальних дорожках:
  использовать getRouteSendActive.
* Длинные выражения через мост режутся — отсюда все чтения идут кусками.
  Не пиши длинные однострочники в обход библиотеки.
* Живые операции (fl_live_*) НЕ перезапускают проект: если FL уже открыта,
  стенд делает attach (fl_live_attach), и плагин ставится/удаляется родными
  функциями движка. Спавн — только если FL не запущена вовсе.
* Живая вставка работает для родных плагинов (по имени из Plugins\\Fruity) и
  для Fruity Wrapper; голый .dll VST и путь к .fst дают пустой слот — VST
  ставится только файловым путём (запись в .flp + перезагрузка проекта).
* Живые дорожки: fl_live_track_add/delete. Удаление принимает индексы 1..17;
  создавать и удалять надо в середине (16), последние дорожки не удаляются.
* Мьюты и соло читаются в state.tracks() (поля mute/solo); раньше их не было.
* Если measure вернул warning «окно в тишине» — инструмент в этом месте просто
  не играет; не делай выводов по форме.
* safe_fl отказывается закрывать FL с пользовательским проектом — так и должно быть.

ЗВУК: ЧТО ЧЕМ МЕРЯЕТСЯ
* measure: соны/фоны (громкость), резкость acum (яркость), LUFS, crest, LDR,
  доли областей (sub/bass/lowmid/mid/upmid/pres/air), стерео (corr, side/mid).
* Резкость почти не зависит от громкости; соны растут на ~5 на каждый дБ;
  доли областей от громкости почти не плывут — поэтому баланс решать по долям.
* «bass» здесь 204–560 Гц — это картон, а не низ. Тело в lowmid 560–1278.
* Стемы нельзя суммировать (Σ даёт ~1.55× микса).
* Замер требует файла. Запись вживую идёт через звуковую карту, нужен предролл.

ПРАВИЛА ПРИЁМКИ
Правка принимается, если цель достигнута по значимой величине и не испорчена
другая метрика из коридора. Порог шума: резкость 0.01 acum, соны 0.5, LUFS 0.1 дБ.
""")

# ------------------------------------------------------------------ горячая перезагрузка
_WATCH: dict = {}


def _maybe_reload() -> bool:
    """Перечитать модули библиотеки, если файлы изменились.

    Зачем: MCP-сервер живёт долго и загружает код один раз. Без этого любая
    правка библиотеки требовала перезапуска харнесса — а на практике правим
    мы её постоянно.
    """
    import importlib
    changed = False
    for name in ("fl_lib", "sandbox", "listen_layer", "judge", "predict", "live_create"):
        path = os.path.join(PKG, name + ".py")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        prev = _WATCH.get(name)
        _WATCH[name] = mtime
        if prev is not None and mtime > prev and name in sys.modules:
            try:
                importlib.reload(sys.modules[name])
                changed = True
            except Exception:
                pass
    return changed


def tool(fn):
    """Регистрирует инструмент и перед вызовом подхватывает свежий код."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        _maybe_reload()
        return fn(*a, **kw)

    return mcp.tool()(wrapper)


def hear(cmd: str, *args: str, timeout: int = 900):
    """Вызвать слой слуха в окружении с phonometry и вернуть разобранный ответ."""
    p = subprocess.run([hear_python_checked(), HEAR_CLI, cmd, *args],
                       capture_output=True, text=True, timeout=timeout)
    txt = (p.stdout or "").strip()
    if not txt:
        return {"error": (p.stderr or "")[-400:]}
    try:
        return json.loads(txt)
    except Exception:
        return {"raw": txt[-1500:], "stderr": (p.stderr or "")[-300:]}


# ---------------------------------------------------------------- связь и чтение

@tool
def fl_guide() -> str:
    """Краткая инструкция по этому серверу: порядок работы и особенности FL."""
    return mcp.instructions or ""


@tool
def fl_seek_bar(bar: int) -> dict:
    """Перевести плейхед на такт N (родная единица FL) и проверить попадание."""
    import track_capture as T
    return T.seek_bar(bar)


@tool
def fl_seek(seconds: float) -> dict:
    """Перемотка по секундам — ПРИБЛИЗИТЕЛЬНО. Точнее fl_seek_bar: FL считает такты."""
    import track_capture as T
    frac = T.seek_seconds(seconds)
    return {"asked_s": seconds, "fraction": round(frac, 4), "hint": T._hint(),
            "note": "при смене темпа секунды нелинейны — для точной работы fl_seek_bar"}


@tool
def fl_render(flp: str, fmt: str = "wav", timeout_s: int = 3600,
              close_fl: bool = False) -> dict:
    """Отрендерить проект через CLI FL (без диалогов).

    Если в проекте включён Split mixer tracks — вернёт по файлу на дорожку
    (split=true). При запущенной FL рендер не стартует: передай close_fl=true,
    чтобы библиотека закрыла её штатно (не kill).
    """
    import render_job as R
    return R.render(flp, fmt=fmt, timeout_s=timeout_s, close_fl_first=close_fl)


@tool
def fl_render_watch(project_dir: str, since_ts: float = 0.0, fmt: str = "wav",
                    timeout_s: int = 3600) -> dict:
    """Дождаться конца рендера: прогресс, падение FL, список файлов.

    since_ts — unix-время старта рендера; 0 значит «сейчас».
    Падение распознаётся по исчезновению или смене pid FL.
    """
    import render_job as R
    return R.watch(project_dir, since=since_ts or None, fmt=fmt,
                   timeout_s=timeout_s)


@tool
def fl_stems_report(stem_dir: str, refs_dir: str = "", prefix: str = "",
                    a: float = 0.0, b: float = 30.0) -> dict:
    """Профиль всех стемов папки и расхождение с референсами по группам.

    stem_dir — папка с дорожками после Split mixer tracks, prefix — общая часть
    имени (например 'vers1_'), refs_dir — папка с референсными стемами
    (подпапки drums/bass/other/vocals.wav). Возвращает строки, отсортированные
    по убыванию ошибки: первыми идут дорожки, которые сильнее всего расходятся.
    """
    import stems as S
    return S.report(stem_dir, refs_dir or None, prefix=prefix, a=a, b=b)


@tool
def fl_drums_balance(path: str, a: float = 0.0, dur: float = 30.0) -> dict:
    """Бочка/малый/тело/железо и плотность атак в файле (обычно стем барабанов)."""
    import stems as S
    return S.drums_balance(path, a, dur)


@tool
def fl_health() -> dict:
    """Живо ли управление: запущена ли FL, отвечает ли контроллер, сколько дорожек."""
    return fl.state.health()


@tool
def fl_link_ensure() -> dict:
    """Поднять посредник, если он упал. Библиотека делает это и сама."""
    return fl.link.ensure()


@tool
def fl_tracks() -> list:
    """Дорожки микшера: индекс, имя, громкость, панорама, число плагинов."""
    return fl.state.tracks()


@tool
def fl_channels() -> list:
    """Каналы рэка: что на чём играет."""
    return fl.state.channels()


@tool
def fl_plugins() -> list:
    """Плагины по слотам: какая дорожка, какой слот, что стоит."""
    return fl.state.plugins()


@tool
def fl_playlist(flp: str | None = None) -> dict:
    """Плейлист проекта: дорожки и клипы (по умолчанию — из открытого проекта)."""
    return fl.state.playlist(flp) if flp else fl.state.playlist(_current_flp())


@tool
def fl_clip_files(flp: str | None = None) -> list:
    """Связь «клип → файл»: позиция, длина, путь к аудио."""
    return fl.state.clip_files(flp) if flp else fl.state.clip_files(_current_flp())


@tool
def fl_automation(flp: str | None = None) -> list:
    """Автоматизация из файла проекта."""
    return fl.state.automation(flp) if flp else fl.state.automation(_current_flp())


@tool
def fl_routing(track: int) -> list:
    """Куда дорожка шлёт: список [получатель, уровень]. Уровень 0 = посыл выключен."""
    return [[d, lvl] for d, lvl in fl.state.routing(track)]


@tool
def fl_set_route(track: int, dest: int, level: float) -> dict:
    """Включить/выключить посыл: level=0 выключает, 0.8 — обычный уровень."""
    fl.act.route(track, dest, level)
    return {"track": track, "dest": dest, "level": level}


# ---------------------------------------------------------------- слух

@tool
def fl_measure(path: str, a: float = 0.0, b: float = 30.0) -> dict:
    """Замер отрезка: соны, фоны, резкость, LUFS, crest, LDR, области, стерео."""
    return hear("measure", path, "--a", str(a), "--b", str(b))


@tool
def fl_bands(path: str) -> dict:
    """240 полос Барка: уровень, ширина, модуляция."""
    return hear("bands", path)


@tool
def fl_profile(path: str) -> dict:
    """Полный профиль файла (кэшируется)."""
    return hear("profile", path)


@tool
def fl_sections(path: str) -> dict:
    """Секции трека по плотности (границы в секундах и уровень)."""
    res = hear("sections", path)
    if isinstance(res, list):
        return {"sections": res}
    return res


@tool
def fl_audit(mine: str, refs: list[str], a: float | None = None,
             b: float | None = None) -> dict:
    """Аудит микса против коридора референсов по всем измеримым фронтам.

    Возвращает вердикты по динамике, спектру, стерео, шуму, атакам, клиппингу
    и отдельно группу «мастеринг» (уровень и пик — это ступень обработки,
    а не качество).
    """
    return fl.hear.audit(mine, refs, a, b)


@tool
def fl_sections_compare(mine: str, refs: list[str], a: float | None = None,
                        b: float | None = None) -> dict:
    """Сравнить наши секции с самыми похожими участками референсов.

    Сравнение идёт по форме спектра, а не по времени: вступление с вступлением,
    кульминация с кульминацией.
    """
    return fl.hear.sections_compare(mine, refs, a, b)


@tool
def fl_pan(stems: dict, a: float | None = None, b: float | None = None) -> dict:
    """Карта панорамы: позиция роли по полосам и секциям, статичность,
    спектральное перекрытие ролей и подсказки, выведенные из измерений."""
    return fl.hear.pan(stems, a, b)


@tool
def fl_report(mine: str, refs: list[str] | None = None,
              stems: dict | None = None, a: float | None = None,
              b: float | None = None, impulse: str | None = None,
              sine: str | None = None, ducked: str | None = None) -> dict:
    """Полный отчёт одним вызовом: реестр проверок, покрытие, действия, markdown.

    Пропущенные проверки не исчезают — они перечислены с причиной, поэтому
    «забыл посмотреть» видно в самом отчёте.
    """
    return fl.hear.report(mine, refs, stems, a, b, impulse, sine, ducked)


@tool
def fl_masking(mix_path: str, stems: dict | None = None) -> dict:
    """Кто кого забивает: маскировка по полосам между стемами."""
    return hear("masking", mix_path, json.dumps(stems or {}))


@tool
def fl_describe(path: str) -> dict:
    """Узнавание: на что похоже по материалу (эмбеддинг)."""
    return hear("describe", path)


@tool
def fl_style_sim(a: str, b: str) -> dict:
    """Близость стиля сведения двух файлов."""
    return hear("style", a, b)


# ---------------------------------------------------------------- правка

@tool
def fl_save(backup: bool = False) -> dict:
    """Сохранить проект в файл (Ctrl+S) и подтвердить это временем файла.

    FL держит изменения только в памяти: падение процесса стирает всё, что
    не сохранено. Вызывай после каждой правки или пачки правок.
    backup=True — перед сохранением сделать копию текущего файла.
    """
    return fl.safe.save(backup=backup)


@tool
def fl_set_autosave(on: bool = True) -> dict:
    """Включить/выключить автосохранение после каждой правки (по умолчанию включено)."""
    return {"autosave": fl.safe.autosave(on)}


@tool
def fl_set_volume(track: int, value: float) -> dict:
    """Громкость дорожки (0..1)."""
    fl.act.volume(track, value)
    return {"track": track, "volume": value}


@tool
def fl_set_pan(track: int, value: float) -> dict:
    """Панорама дорожки (-1 лево .. 1 право)."""
    fl.act.pan(track, value)
    return {"track": track, "pan": value}


@tool
def fl_mute(track: int, state: bool = True) -> dict:
    """Mute дорожки."""
    fl.act.mute(track, state)
    return {"track": track, "muted": state}


@tool
def fl_solo(track: int, state: bool = True) -> dict:
    """Solo дорожки."""
    fl.act.solo(track, state)
    return {"track": track, "solo": state}


@tool
def fl_params(track: int, slot: int) -> list:
    """Все ручки плагина в слоте."""
    return fl.act.params(track, slot)


@tool
def fl_set_param(track: int, slot: int, index: int, value: float) -> dict:
    """Поставить ручку плагина (0..1)."""
    fl.act.param(track, slot, index, value)
    return {"track": track, "slot": slot, "index": index, "value": value}


@tool
def fl_set_params(track: int, slot: int, values: dict, gap: float = 0.12) -> dict:
    """Записать пачку параметров плагина безопасно (с паузами и стопом).

    values — {индекс: значение}. Каждая запись идёт отдельным сообщением в
    UI-поток FL, и очередь из десятков подряд (особенно на играющем
    транспорте) роняет движок: отсюда пауза gap секунд между записями и
    автосохранение каждые четыре.
    """
    items = {int(k): float(v) for k, v in (values or {}).items()}
    return fl.act.param_batch(track, slot, items, gap=gap)


# ---------------------------------------------------------------- создание

@tool
def fl_live_start(flp: str | None = None) -> dict:
    """Поднять FL под Frida для живых операций (создание, удаление плагинов)."""
    return fl.create.live_start(flp or _current_flp())


@tool
def fl_live_attach(pid: int | None = None, flp: str | None = None) -> dict:
    """Прицепиться к уже запущенной FL — без спавна и перезапуска проекта.

    Работает с проектом, который пользователь открыл сам: стенд не закрывает
    и не переоткрывает его. `pid=None` — найти FL64.exe автоматически.
    """
    return fl.create.live_attach(pid=pid, flp=flp)


@tool
def fl_live_stop() -> dict:
    """Закрыть стенд Frida."""
    return fl.create.live_stop()


@tool
def fl_live_slots(track: int) -> dict:
    """Карта слотов дорожки из памяти: занят/пуст, параметры, модуль."""
    return fl.create.live_slots(track)


@tool
def fl_live_insert(track: int, slot: int, plugin: str) -> dict:
    """Вставить плагин вживую, без перезапуска проекта."""
    return fl.create.live_insert(track, slot, plugin)


@tool
def fl_live_delete(track: int, slot: int) -> dict:
    """Удалить плагин из слота вживую."""
    return fl.create.live_delete(track, slot)


@tool
def fl_live_replace(track: int, slot: int, plugin: str) -> dict:
    """Заменить плагин в слоте вживую."""
    return fl.create.live_replace(track, slot, plugin)


@tool
def fl_plugin_file(track: int, slot: int, fst: str) -> dict:
    """Вставить плагин через файл .fst (нужна перезагрузка проекта после)."""
    return {"result": fl.create.plugin(fst, track, slot)}


@tool
def fl_reload(flp: str | None = None) -> dict:
    """Перечитать проект в FL (нужно после правки .flp)."""
    return {"result": fl.create.reload(flp or _current_flp())}


@tool
def fl_live_track_add(index: int | None = None, count: int = 1) -> dict:
    """Создать дорожку в микшере вживую (index None — в конец)."""
    return fl.create.live_track_add(index, count)


@tool
def fl_live_track_delete(index: int) -> dict:
    """Удалить дорожку микшера вживую (штатное удаление с undo)."""
    return fl.create.live_track_delete(index)


@tool
def fl_live_track_count() -> dict:
    """Сколько дорожек в микшере — из движка и из API, для сверки."""
    return fl.create.live_track_count()


@tool
def fl_reload_midi(port: int = 9790) -> dict:
    """Перезагрузить MIDI-скрипты (аналог Update MIDI scripts), без кликов."""
    import fl_midi_reload
    return fl_midi_reload.reload_midi_scripts(port=port)


# ---------------------------------------------------------------- проверка

@tool
def fl_judge(before: dict, after: dict, goals: dict | None = None,
             corridor: dict | None = None, tolerance: float = 0.0) -> dict:
    """Принять или отклонить правку по числам: значимость, цель, ущерб."""
    return fl.judge.verdict(before, after, goals or {}, corridor, tolerance)


@tool
def fl_predict_change(mix_profile: dict, track_profile: dict, gain_db: float,
                      eq: list | None = None) -> dict:
    """Предсказать, что даст правка дорожки, не применяя её."""
    return fl.predict.change(mix_profile, track_profile, gain_db, eq or [])


# ---------------------------------------------------------------- служебное

def _current_flp() -> str:
    """Проект, открытый сейчас в FL (по заголовку окна)."""
    try:
        r = subprocess.run(["tasklist", "/V", "/FI", "IMAGENAME eq FL64.exe"],
                           capture_output=True, timeout=20)
        import re
        txt = r.stdout.decode("cp866", "ignore")
        m = re.search(r"([A-Za-z]:\\[^\"]+\.flp)", txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return os.environ.get("FLCTRL_FLP", "")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import asyncio
        import json as _json
        tools = asyncio.run(mcp.list_tools())
        print(_json.dumps({"tools": len(tools), "names": sorted(t.name for t in tools),
                           "env": discovery.summary()}, ensure_ascii=False, indent=1))
        raise SystemExit(0)
    # посредник до FL должен быть живым до первого вызова инструмента:
    # иначе каждая сессия начинается с «связи нет»
    try:
        fl.link.ensure()
    except Exception:
        pass
    mcp.run()
