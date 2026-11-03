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

ОЦЕНКА ЗВУКА: РЕЕСТР ПРОВЕРОК
fl_report — один вызов, который обходит ВЕСЬ реестр проверок и возвращает
markdown, покрытие и список действий из чисел. Пропущенная проверка не
исчезает: она печатается с причиной («нет стемов», «нет импульса»). Поэтому
отчёт всегда полный — сначала смотри fl_report, потом уже отдельные инструменты.
* fl_audit — фронты против коридора референсов: крест, LDR, резкость, шум,
  атаки, клиппинг, моно-потеря, полосы спектра и стерео.
* fl_sections_compare — секции против похожих участков референсов (не по времени).
* fl_attribute — сколько каждого инструмента реально СЛЫШНО (ISO 532-2):
  N_partial = N(микс) − N(микс без источника), A = N_partial/N_solo.
* fl_balance_build / fl_balance_project / fl_balance_solve — жанровая модель
  баланса: цель как ансамбль k ближайших референсов, правки с капом.
* fl_pan — панорама: статичность, перекрытие ролей, подсказки из измерений.
* fl_masking — кто кого забивает по полосам.

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
    """Живо ли управление: FL, контроллер, дорожки, задержка, проект, стенд."""
    return fl.state.health()


@tool
def fl_ping() -> dict:
    """Быстрый отклик моста с задержкой в миллисекундах (для проверки живости)."""
    return fl.state.ping()


@tool
def fl_simulate(mix_path: str, stems: dict, targets_path: str | None = None,
                cap_db: float = 3.0, section_s: float = 8.0) -> dict:
    """Проверить план до записи в FL: применить правки к стемам и перемерить.

    Возвращает новый микс во временной папке и сравнение слышимости до/после —
    видно, даёт ли план тот эффект, ради которого он предложен.
    """
    import decide
    import partial
    import roles as roles_mod
    import simulate
    import snrtargets

    attr0 = partial.attribute_mg(mix_path, stems, None, None, section_s=section_s)
    roles_map = {n: roles_mod.role_of(n, p) for n, p in stems.items()}
    tg = {}
    if targets_path:
        model = snrtargets.load(targets_path)
        for name, role in roles_map.items():
            t = snrtargets.for_role(model, role)
            if t:
                tg[role] = t
    plan = decide.plan(attr0, cap_db=cap_db, targets=tg or None, roles_map=roles_map)
    if not plan["edits"]:
        return {"plan": plan, "note": "правок нет — проверять нечего"}
    sim = simulate.simulate(mix_path, stems, plan["edits"])
    attr1 = partial.attribute_mg(sim["mix"], sim["stems"], None, None,
                                 section_s=section_s)
    return {"plan_count": plan["count"], "applied": sim["applied"],
            "mix": sim["mix"], "dir": sim["dir"],
            "before_after": simulate.compare_before_after(attr0, attr1)["rows"]}


@tool
def fl_context(with_params: bool = False, with_routing: bool = True,
               with_levels: bool = False, level_seconds: float = 2.0) -> dict:
    """Полный снимок проекта: дорожки, каналы, слоты, плагины, роутинг, темп.

    Отдаёт факты, а не решения. `coverage` показывает, что в снимок не попало.
    with_params=True добавляет параметры плагинов — видно полосы эквалайзеров
    и ручки приборов (медленнее). with_levels=True включает транспорт на
    level_seconds и добавляет уровень каждой дорожки (единственная звуковая
    величина, которую FL отдаёт без рендера).
    """
    return fl.state.context(with_params=with_params, with_routing=with_routing,
                            with_levels=with_levels, level_seconds=level_seconds)


@tool
def fl_meters(tracks: list | None = None, seconds: float = 3.0, play: bool = True) -> dict:
    """Громкость каждой дорожки по её пику: медиана и максимум в дБ.

    Пока транспорт идёт, FL отдаёт пик каждой дорожки. `play=True` включает
    транспорт на время замера и потом останавливает, возвращая позицию.
    Тихие дорожки помечены silent — это тоже факт, а не ошибка.
    """
    return fl.state.meters(tracks, seconds, play)


@tool
def fl_slot_info(track: int, slot: int) -> dict:
    """Что стоит в слоте и можно ли работать с ним как с эквалайзером.

    Ничего не меняет: возвращает имя плагина и найденные полосы
    (номер, текущие Гц и дБ). Это примитив — решение принимает вызывающий.
    """
    return fl.apply.info(track, slot)


@tool
def fl_eq_slots(track: int) -> list:
    """Слоты дорожки, ведущие себя как эквалайзер (определяется по параметрам)."""
    return fl.apply.slots(track)


@tool
def fl_set_band(track: int, slot: int, band: int, hz: float | None = None,
                db: float | None = None, q: float | None = None) -> dict:
    """Поставить одну полосу эквалайзера: частота, усиление, добротность.

    Без семантики «низ/середина»: что именно поставить — решает вызывающий.
    Незаданные параметры не трогаются; значения подбираются по подписи плагина.
    """
    return fl.apply.band(track, slot, band, hz, db, q)


@tool
def fl_apply_plan(edits: list, track_map: dict, slot: int | None = None) -> dict:
    """Применить весь план к живым дорожкам: правки -> эквалайзеры.

    `edits` — список правок плана ({stem, region, do_db}), `track_map` — какой
    стем на какой дорожке микшера. Если в слоте пусто, ставит Fruity Parametric
    EQ 2 в первый СВОБОДНЫЙ слот; если эквалайзер уже стоит — использует его,
    чужой плагин в слоте не трогает. Сохраняет проект один раз.
    """
    return fl.apply.plan(edits, track_map, slot)


@tool
def fl_move_pan(track: int, points: list, duration_s: float,
                rate_hz: float = 20.0) -> dict:
    """Провести панораму дорожки по точкам [(время_с, значение -1..1)] вживую.

    Движение пишется потоком без сохранения проекта на каждом шаге (иначе шаг
    занимает 1.5 с). Возвращает статистику: сколько записей, ошибок, худшее
    опоздание. После остановки остаётся последнее значение.
    """
    return fl.move_pan(track, points, duration_s, rate_hz)


@tool
def fl_apply_eq(track: int, slot: int, gains: dict) -> dict:
    """Применить правки плана к живому эквалайзеру: {"mid": 3.0, "pres": -2.0}.

    Шкалы плагина не документированы, поэтому значения подбираются по его же
    подписи: поставили — прочитали — поправили. Проект не перезагружается.
    """
    return fl.apply.eq(track, slot, gains)


@tool
def fl_doctor() -> dict:
    """Самодиагностика живого API: десять чтений с временем и результатом.

    Показывает, отвалился мост целиком или отдельная ветка (каналы, слоты,
    транспорт). Только чтения — проект не меняется.
    """
    return fl.state.doctor()


@tool
def fl_capabilities() -> dict:
    """Какие функции реально существуют в живом API FL (dir по модулям)."""
    return fl.state.capabilities()


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
def fl_cache(action: str = "stats", scope: str | None = None) -> dict:
    """Кэш измерений: статистика или очистка.

    Считает один и тот же файл один раз: правка файла или смена окна
    сбрасывают запись автоматически. action="clear" чистит всё или одну
    область (audit, wall, seccomp).
    """
    import cache

    if action == "clear":
        return cache.clear(scope)
    return cache.stats()


@tool
def fl_harmony(mine: str, refs: list | None = None, a: float | None = None,
               b: float | None = None) -> dict:
    """Гармония числами: хрома, тональность, строй в центах, гармоничность.

    Без `refs` — только замер. С `refs` — сравнение с профилями: близость хромы,
    разница гармоничности, отклонение строя.
    """
    import harmony
    m = harmony.analyse(mine, a, b)
    if refs:
        return harmony.compare(m, [harmony.analyse(r, a, b) for r in refs])
    return m


@tool
def fl_transients(mine: str, refs: list, max_seconds: float = 45.0) -> dict:
    """Транзиенты: плотность ударов, атака и «удар/хвост» по полосам.

    `mine` — наш микс (wav), `refs` — список референсов. Сравнивает удар/хвост
    с медианой референсов: сглаженная атака видна сразу, в дБ.
    """
    import transients
    m = transients.analyse(mine, max_seconds=max_seconds)
    rs = [transients.analyse(r, max_seconds=max_seconds) for r in refs]
    return transients.compare(m, rs)


@tool
def fl_wall(mine: str, refs: list[str], a: float | None = None,
            b: float | None = None, section_s: float = 8.0) -> dict:
    """Провалы по полосам и толщина «стены», в сравнении с референсами.

    Провал — выпадение полосы относительно самого трека (секция, где середина
    рухнула при живых низах и верхах), а не тихий микс. Толщина — доля времени,
    когда полная энергия держится в коридоре ±6 дБ от медианы.
    """
    import wall

    m = wall.analyze(mine, a, b, section_s)
    r = [wall.analyze(x, a, b, section_s) for x in refs]
    return {"mine": m, "compare": wall.compare(m, r)}


@tool
def fl_pan_plan(stems: dict, a: float | None = None, b: float | None = None,
                section_s: float = 8.0, max_step: float = 0.15,
                max_pan: float = 0.7) -> dict:
    """План движения панорамы по секциям, выведенный из измерений.

    Куда инструмент отходит и насколько — задаёт измеренная ширина секции:
    плотно — шире, разрежено — ближе к центру; шаг ограничен, низ не двигается.
    Возвращает точки {t, pan, why} на каждый стем и проверки плана.
    """
    import panplan as pp

    res = pp.plan(stems, a, b, section_s, max_step, max_pan)
    res["check"] = pp.check(res)
    return res


@tool
def fl_sii(vocal: str, masker: str) -> dict:
    """Разборчивость вокала в миксе по ANSI S3.5 (SII 0..1).

    vocal — вокал отдельным файлом, masker — остальной микс (всё, что его
    маскирует). Возвращает SII, оценку словами, слышимость по 18 полосам
    160…8000 Гц и самую забитую полосу.
    """
    return fl.hear.sii(vocal, masker)


@tool
def fl_decide(mix_path: str, stems: dict, a: float | None = None,
              b: float | None = None, target: float = 0.6,
              cap_db: float = 3.0, section_s: float = 5.0,
              targets_path: str | None = None) -> dict:
    """Точные правки: где, в какой полосе и на сколько дБ (по секциям).

    Считает слышимость по ISO 532-2 в каждой секции и каждой области, затем
    переводит недостаток в децибелы. Полосы, где инструмент вообще не звучит,
    не трогаются; одна правка ограничена капом.

    `targets_path` — JSON целей из корпуса (`docs/snr_baseline.json`). Без него
    цель учебная: «быть наравне с остальным».
    """
    import decide
    import partial
    import roles as roles_mod
    import snrtargets

    attr = partial.attribute_mg(mix_path, stems, a, b, section_s=section_s)
    roles_map = {name: roles_mod.role_of(name, path) for name, path in stems.items()}
    tg = {}
    if targets_path:
        model = snrtargets.load(targets_path)
        for name, role in roles_map.items():
            t = snrtargets.for_role(model, role)
            if t:
                tg[role] = t
    res = decide.plan(attr, target=target, cap_db=cap_db,
                      targets=tg or None, roles_map=roles_map)
    res["audibility"] = {k: {"audib": v["audib"], "share_pct": v["share_pct"],
                             "role": roles_map.get(k)}
                         for k, v in attr["stems"].items()}
    res["targets_used"] = bool(tg)
    return res


@tool
def fl_snr_targets(refs: list[str], out: str, b: float = 60.0,
                   section_s: float = 10.0) -> dict:
    """Собрать жанровые цели по полосам: список «микс.mp3:папка_стемов»."""
    import snrtargets

    pairs = snrtargets._parse_pairs(refs)
    if not pairs:
        return {"error": "ни одной пары «микс:стемы» не разобрано"}
    model = snrtargets.build(pairs, 0.0, b, section_s)
    import json as _json
    with open(out, "w", encoding="utf-8") as f:
        _json.dump(model, f, ensure_ascii=False, indent=1)
    return {"saved": out, "roles": list(model["roles"]), "tracks": len(pairs)}


@tool
def fl_attribute(mix_path: str, stems: dict, a: float | None = None,
                 b: float | None = None, section_s: float | None = None) -> dict:
    """Сколько каждого инструмента реально СЛЫШНО в миксе (ISO 532-2).

    Частичная громкость `N_partial = N(микс) − N(микс без источника)`,
    слышимость `A = N_partial / N_solo`, измеренная чувствительность `dN/dB`
    и по-полосная слышимость. В отличие от доли полос учитывает маскировку.
    `section_s` добавляет разбивку по секциям — видно, ГДЕ инструмент пропадает.
    """
    return fl.hear.attribute(mix_path, stems, a, b, section_s)


@tool
def fl_balance_build(refs_dir: str, out: str) -> dict:
    """Собрать жанровую модель баланса по папкам <трек>/{drums,bass,other,vocals}."""
    return fl.balance.build(refs_dir, out)


@tool
def fl_balance_project(mapping: dict, mix_path: str | None = None,
                       a: float | None = None, b: float | None = None) -> dict:
    """Вектор слышимости проекта по ролям: A, доля, N_partial, dN/dB."""
    return fl.balance.project(mapping, mix_path, a, b)


@tool
def fl_balance_solve(vec: dict, model: dict, cap_db: float = 6.0) -> dict:
    """Каким ролям и на сколько дБ двинуть фейдер, с капом и предсказанием."""
    return fl.balance.solve(vec, model, cap_db)


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
def fl_set_param_live(track: int, slot: int, index: int, value: float,
                      dry_run: bool = False) -> dict:
    """Поставить одну ручку, НЕ останавливая воспроизведение, и проверить чтением.

    Для живой работы: транспорт не трогается, значение перечитывается, при
    расхождении запись повторяется один раз. dry_run=true — показать, что было
    бы записано, ничего не меняя.
    """
    return fl.act.param_live(track, slot, index, value, dry_run=dry_run)


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
