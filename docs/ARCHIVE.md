# Все документы, последние сверху

Один список: спеки, исследования и архив — по времени изменения.
Свежие (то, что писали последним) в самом верху.

| когда | документ | заголовок | о чём |
|---|---|---|---|
| 03.11 14:06 | [docs/DESIGN.md](docs/DESIGN.md) | DESIGN: как устроена библиотека и почему именно так | Точка входа. Здесь — принципы и связи; детали лежат в документах, на которые |
| 03.11 14:05 | [docs/SPEC-AUDIO.md](docs/SPEC-AUDIO.md) | SPEC-AUDIO: запись всех дорожек из памяти и провенанс плейли | Домен AUDIO. Отвечает на два вопроса, которые мы обсуждали: |
| 03.11 14:03 | [docs/SPEC-LIVE.md](docs/SPEC-LIVE.md) | SPEC-LIVE: доступ к живому FL без гонки | Домен LIVE — единственный, где есть общий ресурс: один живой экземпляр FL. |
| 03.11 13:58 | [docs/INTERFACE.md](docs/INTERFACE.md) | INTERFACE: один объект, пути и шесть глаголов | Документ описывает интерфейс библиотеки для вызывающего (человек или модель |
| 03.11 13:46 | [docs/SPEC-CONTEXT.md](docs/SPEC-CONTEXT.md) | SPEC: полный контекст проекта и всего, чего не хватает | Связка принципов и модель целиком — `docs/DESIGN.md`; здесь только пробелы. |
| 03.11 13:42 | [docs/ROADMAP.md](docs/ROADMAP.md) | ROADMAP: порядок работ и жёсткие правила | **Пока библиотека не реализована полностью, проект `vers1` не трогаем.** |
| 03.11 12:29 | [docs/STATUS.md](docs/STATUS.md) | Состояние библиотеки | Дата: 3 ноября 2026. Всё ниже проверено прогоном, а не заявлено. |
| 03.11 03:44 | [docs/example-report.md](docs/example-report.md) | Отчёт по миксу |  |
| 03.11 02:26 | [docs/DESIGN-BALANCE.md](docs/DESIGN-BALANCE.md) | Баланс инструментов: что известно и что делаем | Документ фиксирует **решения**, принятые по литературе, а не по интуиции. |
| 03.11 02:16 | [research/RESEARCH-EFFECTS-WEB.md](research/RESEARCH-EFFECTS-WEB.md) | RESEARCH-EFFECTS-WEB: объективная оценка и автоподстройка эф | Дата: 3 ноября 2026. Источник — только внешний веб (AES/DAFx/arXiv, GitHub, датасеты, документация). |
| 03.11 02:13 | [docs/ARCHITECTURE-PLAN.md](docs/ARCHITECTURE-PLAN.md) | Архитектура flctrl: план перестройки | Документ фиксирует, что сейчас мешает, и какой должна стать библиотека, чтобы |
| 03.11 02:12 | [research/README.md](research/README.md) | Исследования: что нарыли и что из этого следует | Здесь лежат все три отчёта и данные, собранные по теме «как объективно свести |
| 03.11 02:12 | [research/RESEARCH-ORCHESTRATION.md](research/RESEARCH-ORCHESTRATION.md) | Оркестровка микса: правила с числами, эффекты, приёмка | Свод для реализации в коде. Всё, что помечено **[И]**, измерено на этом проекте; **[Р]** — взято из литературы (источники в конце). Дата: 20 |
| 03.11 02:12 | [research/RESEARCH-HEARING.md](research/RESEARCH-HEARING.md) | RESEARCH-HEARING: каталог внешних методов и библиотек для «с | Дата: 3 ноября 2026. Это **обзор внешних источников** (статьи, GitHub, документация), а не |
| 03.11 02:12 | [docs/RESEARCH-SOTA.md](docs/RESEARCH-SOTA.md) | SOTA: автоматическое сведение и баланс — состояние на 2026 | Обзор собран из первоисточников (arXiv, AES, ISMIR, журналы). Задача обзора — |
| 03.11 01:31 | [archive/vers1/mixlog.md](archive/vers1/mixlog.md) | Журнал сведения — vers1 | Все правки внесены через FL Studio MCP (доступна отмена), замеры — peak-hold по 18 точкам трека. |
| 02.11 22:38 | [archive/vers1/MIX-STATE.md](archive/vers1/MIX-STATE.md) | Состояние сведения vers1 — что сделано и что осталось | Обновлено по замерам на 22:00. Все цифры — доли областей (%), резкость acum, |
| 02.11 19:51 | [archive/vers1/FL-LIVE-INSERT.md](archive/vers1/FL-LIVE-INSERT.md) | FL Studio 25.2.5 — живой контроль через Frida: что получилос | Объект: `C:\Programs\Fl Studio 2025\FL64.exe` (FLEngine_x64.dll, Fruity Wrapper_x64.dll). |
| 02.11 18:42 | [archive/vers1/LIBRARY-STATUS.md](archive/vers1/LIBRARY-STATUS.md) | Библиотека доступа к FL Studio — состояние на сейчас | Дата: 2026-11-02. Все пункты со статусом «проверено» подтверждены запуском, не описанием. |
| 02.11 18:22 | [archive/vers1/MIX-METHOD.md](archive/vers1/MIX-METHOD.md) | МЕТОДИКА СВЕДЕНИЯ vers1 ПО РЕФЕРЕНСАМ | Инструменты: `fl_lib` (hear/act/monitor/sim/predict/judge/create/safe), `listen_layer`, |
| 02.11 17:17 | [archive/vers1/FL-HOOK-PROTOCOL.md](archive/vers1/FL-HOOK-PROTOCOL.md) | FL Studio 25.2.5 — динамический протокол загрузки VST-плагин | Объект: `C:\Programs\Fl Studio 2025\FL64.exe` + `FLEngine_x64.dll` (ImageBase 0x400000) + |
| 02.11 17:04 | [archive/vers1/FLP-AUTOMATION.md](archive/vers1/FLP-AUTOMATION.md) | АВТОМАТИЗАЦИЯ В .FLP — подтверждённая раскладка (FL Studio 2 | Статус: **расшифровано и проверено**. Инструмент: `C:\Users\epicc\mcp\flstudio-mcp\tools-local\fl_automation.py`. |
| 02.11 17:04 | [archive/vers1/FLP-SPEC.md](archive/vers1/FLP-SPEC.md) | РАСКЛАДКА ФАЙЛА .flp — точные ID событий (из исходников pyfl | Получено из `pyflp/mixer.py`, `pyflp/arrangement.py`. Это ключ к переносу **всех** |
| 02.11 16:56 | [archive/vers1/PROFILE-SPEC.md](archive/vers1/PROFILE-SPEC.md) | Профиль микса: какие подходы применяются и какие данные полу | Документ описывает **полный профиль**, который библиотека снимает с трека, и метод |
| 02.11 16:25 | [archive/vers1/MIXING-ML.md](archive/vers1/MIXING-ML.md) | MIXING-ML — обученные модели для сведения и перцептивной оце | Ресерч-отчёт. Цель: найти то, чего в нашем стеке ещё нет — обученные модели, которые (а) предлагают решения по балансу/обработке, (б) оценив |
| 02.11 16:20 | [archive/vers1/FL-REVERSE.md](archive/vers1/FL-REVERSE.md) | FL Studio 25.2.5 — FLEngine_x64.dll: статический поиск загру | Объект: `C:\Programs\Fl Studio 2025\FLEngine_x64.dll` |
| 02.11 16:12 | [archive/vers1/FLSTATE-SPEC.md](archive/vers1/FLSTATE-SPEC.md) | FLSTATE — полный доступ к FL Studio: состояние, слух, управл | Документ описывает всё, что мы обсудили и к чему пришли: как я должен видеть проект |
| 02.11 15:32 | [archive/vers1/PERCEPTUAL-RESEARCH.md](archive/vers1/PERCEPTUAL-RESEARCH.md) | Перцептивный анализ микса: что нашлось и что с этим делать | Дата: 2026-11-02. Всё, что ниже, проверено запуском на этом компьютере (Windows 11, CPU, Python 3.13 через uv), |
| 02.11 14:12 | [archive/vers1/FINAL-PATH.md](archive/vers1/FINAL-PATH.md) | ИТОГ: путь к идентичному звуку | **Идентичен только рендер самого FL.** Никакая конвертация в другую DAW не даст |
| 02.11 13:43 | [archive/vers1/STATE-3.md](archive/vers1/STATE-3.md) | СОСТОЯНИЕ НА КОНЕЦ СЕССИИ | **MIDI-мост flapi.** Все внешние причины исключены: порты, демон, loopMIDI, реестр, |
| 02.11 13:34 | [archive/vers1/CLI-RENDER.md](archive/vers1/CLI-RENDER.md) | ПРОРЫВ: рендер из командной строки (без моста и GUI) | FL64.exe /Ewav /R"<путь>\out.wav" "<путь>\project.flp" |
| 02.11 13:05 | [archive/vers1/MIXER-MAP.md](archive/vers1/MIXER-MAP.md) | НАСТОЯЩАЯ КАРТА МИКШЕРА vers1 | Прочитана из `.flp` (событие 204 — имена дорожек микшера, нумерация с нуля, как в flapi). |
| 02.11 13:03 | [archive/vers1/PLAN-2.md](archive/vers1/PLAN-2.md) | ПЛАН ПРАВОК ПО НАСТОЯЩЕЙ КАРТЕ МИКШЕРА | Все номера — из `MIXER-MAP.md` (нумерация с нуля, как в flapi). |
| 02.11 12:36 | [archive/vers1/PERCEPTION-3.md](archive/vers1/PERCEPTION-3.md) | ТРЕБОВАНИЯ КО МНЕ: как я должен слышать (версия 3) | Собрано из всех указаний пользователя + ресерч современных методов. |
| 02.11 12:18 | [archive/vers1/TODO.md](archive/vers1/TODO.md) | НЕЗАВЕРШЁННЫЕ ЗАДАЧИ (не терять при прерывании) | Обновлено в момент, когда пользователь ушёл спать. |
| 02.11 03:31 | [archive/vers1/STATE-2.md](archive/vers1/STATE-2.md) | СОСТОЯНИЕ РАБОТЫ (для продолжения без потери контекста) |  |
| 02.11 03:02 | [archive/vers1/EVENT-REPORT.md](archive/vers1/EVENT-REPORT.md) | Событийный анализ (без окон) | Метрики считаются между онсетами, а не в фиксированных окнах. |
| 02.11 02:58 | [archive/vers1/PERCEPTION.md](archive/vers1/PERCEPTION.md) | ТРЕБОВАНИЯ: как я должен слышать микс | Собрано из всех указаний пользователя. Каждый пункт — с методом и статусом. |
| 02.11 02:40 | [archive/vers1/MAP.md](archive/vers1/MAP.md) | КАРТА РАБОТ: vers1 → референсы | **Измерено:** барабаны тише суммы на −12.6 дБ против −8.4 (GRIMA) и −4.7 (Wintersun) → **нужно +4…+6 дБ**. |
| 02.11 02:28 | [archive/vers1/PLAN.md](archive/vers1/PLAN.md) | План правок vers1 по измерениям (корректное сравнение) | Стемы Demucs по всему треку (352 с). **Сумма стемов нормируется к −23 LUFS**, затем каждый стем меряется относительно суммы. Нормировка кажд |
| 02.11 01:23 | [archive/vers1/GOAL.md](archive/vers1/GOAL.md) | ЦЕЛЬ: система понимания микса и сведения по референсам | Построить и применять систему, которая **по аудио понимает микс так, как его слышит инженер**, и с её помощью свести трек к уровню референсо |
| 02.11 01:20 | [archive/vers1/REQUIREMENTS.md](archive/vers1/REQUIREMENTS.md) | ТРЕБОВАНИЯ: система понимания микса | Документ описывает, какой должна быть система, чтобы «слышать» микс так, как слышит инженер, и качественно сравнивать его с референсами. Нап |
| 02.11 01:17 | [archive/vers1/MIX-UNDERSTANDING.md](archive/vers1/MIX-UNDERSTANDING.md) | Понимание микса: измерения и сравнение с референсами | Стемы: Demucs htdemucs, 45 с (100–145 с трека). |
| 02.11 00:11 | [archive/vers1/STATE.md](archive/vers1/STATE.md) | Состояние работы и что осталось | - Панорамы: 22/26/28 = −1.0, 9 = −0.88, 23/27/29 = +1.0, 10 = +0.88; шины 12/13 в центре. |
| 01.11 21:02 | [archive/vers1/TARGETS.md](archive/vers1/TARGETS.md) | Целевые профили по инструментам (Demucs + refcompare) | Источник: три референса скачаны в `_refs/`, 60-секундные фрагменты разложены Demucs |
| 01.11 19:08 | [archive/vers1/REPORT-4.md](archive/vers1/REPORT-4.md) | vers1 — анализ старого баунса и точные цели правок | Файл: `123сосать.mp3` (2026-10-12, 322 с) — предыдущая версия микса. Цифры получены `tools-local/mixcheck.py`. |
| 01.11 18:23 | [archive/vers1/REPORT-3.md](archive/vers1/REPORT-3.md) | vers1 — этап 3: найдена причина «не того звука» | **Трек `30 REF` (mp3-референс) играл прямо в мастер.** Он не был замьючен и имел активный |
| 01.11 16:25 | [archive/vers1/REPORT-2.md](archive/vers1/REPORT-2.md) | vers1 — отчёт по сведению (этап 2, по референсным приёмам) | Дата: 2026-11-01. Всё применено кодом через flapi (внутри FL), проверено чтением обратно. |
| 01.11 15:00 | [archive/vers1/MIX-PLAN.md](archive/vers1/MIX-PLAN.md) | План сведения vers1 — целевые цифры и порядок работ | Сейчас всё летит прямо в мастер. Предлагается 4 шины на свободные инсерты: |
