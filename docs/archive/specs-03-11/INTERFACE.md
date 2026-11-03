# INTERFACE: один объект, пути и шесть глаголов

Документ описывает интерфейс библиотеки для вызывающего (человек или модель
через MCP). Собран из того, что уже есть в `fl_lib`, без новой логики: меняется
только форма доступа.

## 1. Принцип

Не сценарий и не набор из 77 функций. **Один объект проекта, у каждого узла
есть путь, и шесть ортогональных глаголов.** Любая композиция разрешена,
порядок не навязан, политики нет.

```
list(path)              что там лежит и какого типа
get(path)               значение
set(path, value)        записать значение
call(op, args)          действие, которое не является значением
measure(spec)           посчитать метрику → кадр
compare(a, b)           сравнить два кадра → дельты
```

Плюс два свойства, а не глагола: `dry_run` (ничего не менять, вернуть «было
бы») и `undo(n)` (откатить последние n изменений).

## 2. Дерево

```
/                             проект: имя, темп, транспорт, счётчики
/track/{i}                    дорожка микшера
/track/{i}/slot/{s}           плагин в слоте
/track/{i}/slot/{s}/param/{p} параметр плагина (значение + подпись)
/track/{i}/slot/{s}/band/{b}  полоса эквалайзера (hz, gain, q)
/track/{i}/send/{d}           посыл на дорожку d (active, level)
/channel/{i}                  канал рэка (имя, громкость, панорама, target_track)
/transport                    playing, position, seconds, tempo, time_sig, loop
/playlist/clip/{id}           клип: track, position, length, source_offset, sample
/section                      рабочее окно: a, b, bars
/refs/{name}                  референс: те же метрики, что у проекта
/measure/{id}                 результат замера (кадр)
```

Правила адресации:

- индексы — те же, что в FL (дорожка 0 — мастер);
- `*` в пути означает «все»: `get("/track/*/volume")` → список;
- отсутствующий узел — ошибка `path_not_found`, а не `None`;
- тип узла известен из `list`, запись проверяется по типу.

## 3. Глаголы

### list(path, depth=1)

Возвращает детей с типом и краткой сводкой. Это единственный способ узнать,
что доступно — вместо чтения документации.

```
list("/")            → [{path:"/track", type:"collection", n:32},
                        {path:"/transport", type:"node", fields:["playing","position",…]},
                        {path:"/refs", type:"collection", n:6}]
list("/track/12")    → [{path:"/track/12/volume", type:"float", unit:"linear", value:0.72},
                        {path:"/track/12/slots", type:"collection", n:2}, …]
```

### get(path)

```
get("/track/12/volume")              → {value:0.72, type:"float"}
get("/track/12/slot/0/band/3")       → {hz:700.0, gain:2.3, q:1.1, unit:"Гц/дБ"}
get("/track/*/volume")               → [{track:0, value:0.95}, …]
```

### set(path, value) / set([{path, value}, …])

Возвращает «было → стало» для каждого узла. Пачка применяется атомарно: если
хоть один путь невалиден — не применяется ничего.

```
set("/track/12/volume", 0.70)
→ {changes:[{path:"/track/12/volume", before:0.72, after:0.70}]}

set("/track/12/slot/0/band/3", {hz:700, gain:-1.5})
→ {changes:[{path:"…", before:{hz:632,gain:2.3}, after:{hz:700,gain:-1.5}}]}
```

### call(op, args)

Действия, которые не являются значением:

```
call("plugin.insert",  {track:12, slot:2, name:"Fruity Parametric EQ 2"})
call("plugin.delete",  {track:12, slot:2})
call("plugin.replace", {track:12, slot:2, name:"…"})
call("route.connect",  {src:12, dst:0, level:0.8})
call("track.add",      {index:12, count:1})
call("transport.play", {})
call("render",         {path:"…", from_bar:17, to_bar:24})
call("record.start",   {tracks:[12,13], path:"…"})
call("record.stop",    {})
```

### measure(spec)

```
measure({metric:"rms",       by:["track","band"], where:{bar:[17,24]}})
measure({metric:"partial",   by:["role","band"]})
measure({metric:"transient", by:["track"],       where:{section:"chorus"}})
measure({metric:"spectrum",  source:"/refs/GRIMA"})
```

Возвращает кадр:

```
{id:"f17", metric:"rms", unit:"дБ",
 axes:{track:[0..31], band:["sub",…,"air"]},
 values:[[…],[…]],
 coverage:{measured:["project"], missing:["стемы: нет записи дорожек"]}}
```

Правило: `values` — прямоугольная таблица, `axes` подписаны, `unit` один на
кадр, пропуски помечены `null` и перечислены в `coverage`.

### compare(a, b, mode="delta")

Принимает два кадра (или id) с одинаковой метрикой. Возвращает разреженный
список, отсортированный по модулю дельты:

```
[{track:12, band:"mid", a:-14.0, b:-12.1, d:+1.9},
 {track:5,  band:"sub", a:-18.2, b:-16.1, d:-2.1}, …]
```

Если оси не совпадают — сравнение идёт по пересечению, а разница осей
перечисляется в `coverage.unmatched`. Никаких «средних» вместо пропусков.

## 4. Типы и проверка

| тип | пример | проверка при set |
|---|---|---|
| `float` | громкость 0..1 | диапазон узла |
| `int` | номер слота | 0..9 |
| `bool` | mute | только bool |
| `enum` | режим петли | список значений узла |
| `struct` | полоса {hz, gain, q} | поля и диапазоны |
| `string` | имя | длина |

Ошибка всегда структурная:

```
{ok:false, code:"range", path:"/track/12/volume", value:1.4, allowed:[0,1]}
{ok:false, code:"path_not_found", path:"/track/99"}
{ok:false, code:"type", path:"/track/12/mute", expected:"bool", got:"string"}
```

Никаких «подсказок, что делать дальше» — только факт отказа и границы.

## 5. Метрики (реестр)

Метрика — запись, а не инструмент. Поля: имя, оси, единицы, источник
(поток/файл/производная), как считается.

Уже есть и переносится как есть:

| метрика | оси | единицы | откуда |
|---|---|---|---|
| `rms`, `peak`, `crest` | track × time | дБ | файл/поток |
| `bands` (240 Барка) | track × band × time | дБ | файл |
| `erb` (24–30) | track × band × time | дБ | файл/поток |
| `partial` | role × band | доля 0..1 | стемы/запись |
| `masking` | role × role × band | дБ | стемы |
| `sii` | band | 0..1 | вокал + микс |
| `transient` | track × band | дБ, мс | файл/поток |
| `harmony` | pitch class | доли | файл |
| `stereo` | track × band | ширина, корреляция | файл |
| `wall` | band × section | дБ | файл |
| `sections` | time | границы | файл |
| `lufs` | track/микс | LUFS | файл |

Добавить метрику = добавить строку в реестр (`metrics.py`), а не инструмент.

## 6. MCP-поверхность

Шесть инструментов, повторяющих глаголы:

```
fl_list(path, depth)          fl_get(path)
fl_set(paths)                 fl_call(op, args)
fl_measure(spec)              fl_compare(a, b, mode)
```

Плюс два служебных: `fl_undo(n)` и `fl_dry_run(on)`.

Схемы короткие: путь — строка, значение — произвольное, аргументы — объект.
Всё остальное вызывающий узнаёт через `fl_list`, а не из описаний.

Отображение старых имён: `fl_tracks` → `fl_get("/track/*")`,
`fl_set_volume` → `fl_set("/track/N/volume")`, `fl_wall` →
`fl_measure({metric:"wall"})`. Старые остаются обёртками, пока не отвалятся.

## 7. Батч и откат

```
fl_set([{path:"/track/12/volume", value:0.7},
        {path:"/track/12/pan",    value:-0.3}])
```

Одна транзакция: снимок затронутых узлов → применение → проверка чтением.
При ошибке — откат всех. `fl_undo(1)` отменяет последнюю транзакцию.

## 8. Кэш и стоимость

- `list`/`get` кэшируются по версии проекта; версия растёт на каждом `set`/`call`;
- `measure` кэшируется по (метрика, окно, источник, версия проекта);
- коллекционные пути читаются кусками внутри, наружу — одна таблица;
- `get("/track/*/slot/*/param/*")` на 32 дорожках — это сотни значений, поэтому
  по умолчанию `depth=1`, а глубина задаётся явно.

## 9. Пример работы агента

```
list("/")                                  → где что
get("/track/*/volume")                     → картина громкостей
measure({metric:"rms", by:["track","band"]})   → кадр f1
measure({metric:"rms", by:["track","band"], source:"/refs/GRIMA"}) → f2
compare(f1, f2)                            → где расходимся
set("/track/12/slot/0/band/3", {hz:700, gain:-1.5})
measure({metric:"rms", by:["track","band"]})   → f3
compare(f1, f3)                            → что изменилось
```

Ни одного обязательного шага: можно начать с `set`, можно вообще не мерить.
Интерфейс не диктует порядок — он делает любой порядок выразимым и сравнимым.
