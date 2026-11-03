# FINDINGS: ctrl — плагины, метры, автоматизация

Lane `ctrl`. Проект — моя копия `C:/Users/epicc/tools/fltest/fixtures/demo_ctrl.flp`
(снята с `fixtures/demo2.flp`, заголовок окна «With You», 50 дорожек, 104 плагина).
Стенд: `tools-local/ctrl_daemon.py spawn <flp> 9791` (Frida, вызовы на UI-потоке),
мост FL Studio Python API — TCP 9787.

## 0. Статус

| # | Задача | Состояние |
|---|---|---|
| 1 | метры плагинов (GR, уровень) | в работе: объект слота найден, поле метра ещё нет |
| 2 | event ID + `mixer.automateEvent` | **сигнатура выяснена** (§2), ID-пространство размечено (§2.1) |
| 3 | пресеты / bypass | не начато |
| 4 | сторонний VST в слот | не начато |

## 1. Объекты: слот → плагин

Таблица слотов инсерта 1 `FE+0x11842F4` верна только для инсерта 1; рабочий способ —
скан по vtable `FE+0xDB4960` (`buildSlotMap` в `flctrl/live_create.py`). В этом проекте
скан даёт 2560 объектов, `+0x160`=insert, `+0x164`=slot.

Объект слота (0x1C0 Б), проверено на 8:2, 9:1, 1:4:

| offset | что |
|---|---|
| +0x00 | vtable `FLEngine+0xDB4960` |
| +0x30 | интерфейс плагина (vtable внутри DLL плагина) |
| +0x38/+0x40/+0x48 | подобъекты/списки параметров |
| +0x50 | имя слота (Delphi UnicodeString, часто 0) |
| +0x64 | состояние: `2` занят, `-1` пуст |
| +0x68 | число параметров (Limiter 18, Reverb 15, Maximus 59, EQ 36) |
| +0x80 | HMODULE, +0x88 путь к DLL |
| +0x98 | указатель на объект-связку (heap) |
| +0xa0 | **счётчик сэмплов/блоков** (растёт при звуке: +1914 за 40 мс) |
| +0x160/+0x164 | insert / slot |

`loadWrapper` (`FE+0xE268B0`) возвращает RAX = экземпляр от `CreatePlugInstance`
(для Fruity Limiter в 9:1 — `0x78f60c70`, vtable `0x70c33458`). В объекте слота этого
указателя нет: `slot+0x30` — другой объект (host-адаптер внутри DLL плагина).

## 2. `mixer.automateEvent` — сигнатура (прежняя модель была неверной)

По стабам FL Studio Python API (`.../mixer/events/`):

```
getEventIDName(index: int, shortname: int = 0) -> str
automateEvent(index: int, value: int, flags: int, speed: int = 0,
              isIncrement: int = 0, res: float = midi.EKRes) -> int
getEventValue(index: int, value: int = midi.MaxInt, smoothTarget: int = 1) -> int
getEventIDValueString(index: int, value: int) -> str
```

`index` — это **event ID**, а не дорожка. Порядок: (eventId, value, flags).

### 2.1 Пространство event ID (снято живьём через `mixer.getEventIDName`)

| ID | что вернул `getEventIDName` |
|---|---|
| `0x00000000..0x0000000D` | `<имя канала> - Channel volume / panning / Filter frequency (modulation X) / …` |
| `0x00010000 + p` | следующий канал рэка; `p` — параметр канала (0 volume, 1 pan, 2 filter freq, 3 bandwidth, 4 pitch, 5 filter type, 6 portamento, 7 mute/solo, 8 target mixer track, 9 gate, 10 crossfade, 11 fine time shift, 12 swing, 13 sample start) |
| `0x40000000..0x40000005` | `Main volume`, `Main swing`, `Master pitch`, `Tempo` |
| `0x70000000 + *` | `Master - ` (объект микшера распознан, имя параметра пустое) |
| `0x80000000 + *` | `Omni - ` (remote-control события) |
| `0x00400000` | `BAS Uobaa - BodyLP - Output max level` — плагинные события канала |

Формула plugin ID из доков: `((track << 6) + plugIndex) << 16` (`mixer.getTrackPluginId`).

## 3. Файлы

* `tools-local/ctrl_api.py` — мост 9787 (`call(expr=…|code=…)`).
* `tools-local/ctrl_stand.py` — клиент Frida-стенда (9791).
* `tools-local/ctrl_daemon.py` — стенд (spawn/attach).
* `tools-local/ctrl_probe.py` — инвентарь плагинов проекта.
* `tools-local/ctrl_params.py` — параметры плагина через мост.
* `tools-local/ctrl_events.py` — скан event ID.
* `tools-local/ctrl_meter_scan.py`, `ctrl_memdiff.py` — диф памяти при звуке.
