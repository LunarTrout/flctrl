# FL Studio 25.2.5 — FLEngine_x64.dll: статический поиск загрузчика плагинов

Объект: `C:\Programs\Fl Studio 2025\FLEngine_x64.dll`
Размер 57 367 032 Б, PE32+ x64 (machine 0x8664), ImageBase **0x400000**, EntryPoint RVA 0x3712302.
Метод: только чтение файла. FL не запускался, ничего не патчилось.
Инструмент: `C:\Users\epicc\mcp\flstudio-mcp\tools-local\fl_re_scan.py`.

**VA = RVA + 0x400000** (в отчёте везде RVA, если не сказано иное).

---

## 1. Короткий ответ

| Вопрос | Ответ |
|---|---|
| Есть ли в FLEngine «функция загрузки VST в слот» с плоской сигнатурой? | **Нет.** Загрузка VST-плагина в слот — это цепочка методов Delphi-объектов (`TVST3Plugin.Create/LoadPlugin`, `TVSTPlugin.*`), а установка в слот — команда UI-слоя (`insert effect plugin`). |
| Кто реально делает `LoadLibrary` на .dll плагина? | **Не FLEngine.** В движке всего **26** вызовов `LoadLibrary*` (24×`LoadLibraryW`, 1×`LoadLibraryExW`, 1×`LoadLibraryA`) — все бытовые (dwmapi, WebView2, declipper, stemextract, PSAPI, регистрация). Хостинг VST — в `Plugins\Fruity\Effects\Fruity Wrapper\Fruity Wrapper_x64.dll`. |
| Лучший кандидат «загрузчик VST3» | **RVA 0x006E3200** — `TVST3Plugin.LoadPlugin`, 3 226 Б. Имя метода оставили сами разработчики (trace-маркер). |
| Лучший кандидат «вставка плагина в слот» | **RVA 0x009B6BE0** — обработчик команд `insert effect plugin` / `replace effect plugin` (+ текст ошибки «There are no available effect slots on the mixer track.»). |
| Что можно вызвать из ctypes «как есть» | **RVA 0x0001A3D0** — `LoadLibraryExW(path, 0, LOAD_LIBRARY_AS_DATAFILE)` (185 Б). Это загрузчик **сканера** плагинов, не хост. |

Главный вывод: **в FLEngine нет единой функции вида `LoadPlugin(insert, slot, path)`**. Загрузка модуля и установка в слот разнесены: движок создаёт объект-обёртку (`TVST3Plugin`/`TVSTPlugin`), а сам VST-модуль грузит и вызывает `VSTPluginMain`/`GetPluginFactory` **Fruity Wrapper_x64.dll** (в его строках есть `VSTPluginMain` @RVA 0x3A6F10, и он импортирует `LoadLibraryExW`/`GetProcAddress`).

---

## 2. Почему «обычный» поиск по IAT даёт ноль (это важно для воспроизведения)

1. **IAT подменён.** Дескрипторы импорта указывают на «теневую» таблицу по RVA 0x362C000 (760 слотов, 731 с именем), а код ходит через вторую таблицу в `.idata` (RVA 0x1433000…0x14396CC). Поэтому поиск «call [slot из дескриптора]» находит 0 совпадений.
2. **Двойная индирекция на каждый вызов API.** Реальная цепочка:

   ```
   call rel32  ->  jmp rel32 thunk (.avm1/.avm2/.avm3; всего 295 044 таких jmp)
               ->  jmp qword ptr [rip+disp]  (import stub в низком .text)
               ->  IAT slot -> kernel32!LoadLibraryExW
   ```

   Стабы: `LoadLibraryExW` @0x7090, `GetProcAddress` @0x7070, `LoadLibraryW` @0x238A0, `FreeLibrary` @0x7020, `CoCreateInstance` @0x68B20, `LoadLibraryA` @0x1CF90. Разрешено 6 938 thunk-ов → имён API.
3. **Строки — UTF-16 (Delphi UnicodeString) внутри `.text`**, а не ASCII в `.rdata` (там всего 512 Б).
4. **Побайтовый поиск `lea` даёт ложные срабатывания** внутри immediates `call rel32`. Лечится валидацией по границам инструкций из `.pdata` (54 437 функций): из 25 497 найденных `lea` валидны 25 422; «ссылка на `vstpluginmain`» по адресу 0x2CA9CD оказалась байтами внутри `call` — отброшена.

Итоговая статистика движка: **7 009** валидных вызовов импортов, из них `FreeLibrary` 85, `LoadLibraryW` 24, `CoCreateInstance` 14, `GetProcAddress` 11, `LoadLibraryExW` 1, `LoadLibraryA` 1.

---

## 3. Имена, которые FL сам оставил в бинарнике

FL пишет trace-маркеры вида `TClass.Method` + `Finished (TClass.Method)` — это не RTTI, а отладочные строки, но они однозначно привязывают функцию к методу. Проверено по границам инструкций.

| Маркер | Функция (RVA) | Размер |
|---|---|---|
| `TVST3Plugin.LoadPlugin` + `Finished (...)` | **0x006E3200..0x006E3E9A** | 3 226 Б |
| `TVST3Plugin.Create` + `Finished (...)` | **0x006E0DB0..0x006E12F9** | 1 353 Б |
| `TVST3Plugin.Destroy` | 0x006E13B0..0x006E17D3 | 1 059 Б |
| `TVST3Editor.Create` | 0x006E9640..0x006E9784 | 324 Б |
| `TVSTPlugin.Destroy` | 0x006CD000..0x006CD3AD | 941 Б |
| `TVSTPlugin - SAVE` / `Finished (SAVE)` / `(RESTORE)` | 0x006CECE0..0x006D067A | 6 554 Б |
| `Finished (find plugins)` | 0x00711BC0..0x00711F2E | 878 Б |
| `TBridgedPlugin.Destroy`, `TBridge.Destroy` | 0x007DAC50, 0x007CE4B0 | 337 / 741 Б |
| *(без маркера, по строкам)* `Create`, `call main`, `failed to find import function`, `no effect` | **0x006CB690..0x006CCA00** | 4 960 Б |

Дополнительно в пуле строк рядом (0x6C9000–0x6D2000) лежат имена классов: `TVSTPlugin`, `TVSTPluginEditor`, `TVSTPanel`, `TVST3Plugin`, `Fruity Wrapper`.

---

## 4. Кандидаты (ранжировано)

### #1 — `TVST3Plugin.LoadPlugin`, RVA **0x006E3200** (VA 0x4E3200), 3 226 Б
Это ядро загрузки VST3: создаёт экземпляр компонента, вызывает `CreateVST3Instance` (0x006DCE20), `setup processing failed!` (0x006E8640), работает с `IsEqualGUID` (3 вызова — выбор класса компонента по IID).
Пролог: `55 57 56 53 48 81 EC E8 00 00 00 48 8B EC`.
Сигнатура (Delphi Win64 ABI): `procedure TVST3Plugin.LoadPlugin(Self: TVST3Plugin; ...)` — `Self` в **RCX**, далее RDX/R8/R9 + стек. Метод, а не свободная функция: без валидного `Self` (указатель на живой объект с VMT) вызывать нельзя.

### #2 — `TVST3Plugin.Create`, RVA **0x006E0DB0** (VA 0x4E0DB0), 1 353 Б
Конструктор объекта VST3-плагина; внутри вызывает `LoadPlugin` и виртуальный метод базового класса `call qword ptr [rax+0x378]`, результат кладёт в `[Self+0x7A0]`.
Пролог: `55 53 48 83 EC 58 48 8B EC 48 89 6D 38 48 89 4D …` — видно `Self` в RCX (`[rbp+0x70]`) и `bool` в DL (`[rbp+0x78]`).
Вывод: это **не** «загрузить по пути», а «создать объект», где путь/параметры приходят извне.

### #3 — фабрика класса `TVST3Plugin`, RVA **0x006FA0B0** (VA 0x4FA0B0), 46 Б
Крошечная функция: берёт класс из глобала (`mov rcx, [rip-0x1FFE8]`), переставляет аргументы и вызывает `0x0034DB840` (Delphi-хелпер создания экземпляра).
Байты: `48 83 EC 28 4C 89 44 24 40 48 8B 0D 18 00 FE FF`.
Статических `call`-еров нет — адрес лежит в таблице-реестре типов плагинов, вызов идёт через указатель. Это **самый вероятный «вход» для создания плагина по пути**, но сигнатура нестандартная: RCX — класс (загружается внутри), RDX/R8/R9 — параметры.

### #4 — VST2-сторона, RVA **0x006CB690** (VA 0x4CB690), 4 960 Б
Маркеры `Create`, `call main`, `failed to find import function`, `no effect` — это классический путь VST2: получить указатель на `VSTPluginMain` («failed to find import function»), вызвать его («call main»). На ошибке делает `FreeLibrary` (0x006CBD4E, 0x006CC0AB).
**Ключевое:** внутри функции **нет** `LoadLibrary` — только `FreeLibrary`. Значит HMODULE приходит снаружи (через bridge/хост), а сам модуль грузит `Fruity Wrapper_x64.dll`.

### #5 — обработчик команд вставки, RVA **0x009B6BE0** (VA 0x4B6BE0), 1 320 Б
Строки внутри: `insert effect plugin`, `replace effect plugin`, `insert channel`, `Error|There are no available effect slots on the mixer track.`
Сигнатура: `(Self: <UI-объект микшера>, bool)` — в прологе `mov [rbp+0x48], rcx` и `mov [rbp+0xC8], dl`, затем чтение `[Self+0x7FC]` (текущий слот) и `[Self+0x7F4]` (массив слотов). То есть это «вставить эффект в выбранный слот» на уровне UI-контекста.

### #6 — интерпретатор команд проекта, RVA **0x00A7DF40** (VA 0xE7DF40), 2 623 Б
Строки: `.FLP`, `.FST`, `insert effect plugin`, `load mixer state`, `insert channel`, `layer set children`, `load channel sample(s)`. Это слой воспроизведения сохранённых событий/журнала: команды адресуются **по имени**, а не по номеру. Полезно: подтверждает, что «insert effect plugin» — легальная внутренняя команда движка.

### #7 — загрузчик для сканера, RVA **0x0001A3D0** (VA 0x41A3D0), 185 Б
Единственный вызов `LoadLibraryExW` в движке:
```
GetModuleFileNameW(NULL, buf, 0x105)   ; каталог текущего модуля
... 3 хелпера нормализации пути ...
LoadLibraryExW(path, 0, 2)             ; 2 = LOAD_LIBRARY_AS_DATAFILE
```
Т.е. это «загрузить DLL как данные» (без DllMain) — так FL **сканирует** плагины, а не хостит их.
Байты: `55 48 81 EC 60 02 00 00 48 8B EC 48 89 4D 20 48`.
Обёртка над ним: 0x00018B80 (85 Б); её вызывает 0x00018BE0, а того — 0x1C820, 0x48660, 0x488A0, 0x54A60, 0x108470, 0x151B70.

### #8 — сканер каталога плагинов, RVA **0x00711BC0** (`Finished (find plugins)`), 878 Б

---

## 5. Где на самом деле грузится VST

`C:\Programs\Fl Studio 2025\Plugins\Fruity\Effects\Fruity Wrapper\Fruity Wrapper_x64.dll`
размер 5 998 896 Б, ImageBase 0x400000, `.text` 0x1000..0x495E60, 19 798 функций в `.pdata`.

* UTF-16 строка **`VSTPluginMain`** @RVA **0x3A6F10**;
* импортирует `LoadLibraryExW` (стаб 0x7020, единственный вызов — RVA **0x0001934B** в функции **0x000192C0..0x00019379**), `GetProcAddress` (3 стаба), `LoadLibraryA` (0x1BE70), `LoadLibraryW` (0x220D0);
* `Fruity Wrapper` присутствует 8× в UTF-16 + 1× ASCII.

Практический смысл: если нужен именно **факт загрузки DLL плагина в процессе FL**, то это функция в `Fruity Wrapper_x64.dll` (0x192C0 — обёртка LoadLibraryExW; точка 0x1934B — сам вызов), а `FLEngine` лишь создаёт объект-обёртку и передаёт ей путь.

---

## 6. Как проверить кандидата из ctypes (внутри процесса FL)

FL исполняет Python внутри своего процесса, поэтому адрес = база загруженного модуля + RVA.

### 6.1 База и безопасный smoke-тест

```python
import ctypes, ctypes.wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetModuleHandleW.restype  = ctypes.c_void_p
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

base = k32.GetModuleHandleW("FLEngine_x64.dll")   # FL уже загрузил движок
assert base, "FLEngine не в процессе"

# RVA 0x1A3D0: LoadLibraryExW(path, 0, LOAD_LIBRARY_AS_DATAFILE)
proto = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)   # 1 аргумент
fn = proto(base + 0x1A3D0)
h = fn(ctypes.cast(ctypes.c_wchar_p(r"C:\Windows\System32\version.dll"), ctypes.c_void_p))
print("hmodule =", hex(h or 0))
if h: k32.FreeLibrary(ctypes.c_void_p(h))
```

Это проверяет три вещи без риска для проекта: (а) база и RVA верны, (б) соглашение вызова угадано, (в) код в памяти не «прыгает» (не packed-секция).

Если вместо `c_wchar_p` функция ждёт **Delphi `UnicodeString`**, вызов упадёт по AV — тогда подавайте Delphi-строку: заголовок 12 Б перед данными
(`codePage=1200`, `elemSize=2`, `refCnt=-1`, `length=len`) и передавайте указатель на данные:

```python
def delphi_ustring(s: str):
    b = s.encode("utf-16-le")
    n = len(b) // 2
    hdr = (1200).to_bytes(2, "little") + (2).to_bytes(2, "little") \
        + (-1).to_bytes(4, "little", signed=True) + n.to_bytes(4, "little")
    buf = ctypes.create_string_buffer(hdr + b + b"\0\0", 12 + len(b) + 2)
    return buf, ctypes.c_void_p(ctypes.addressof(buf) + 12)
```

### 6.2 Проверка «загрузчика VST3» (0x6E3200) — только с живым объектом

`TVST3Plugin.LoadPlugin` — метод: RCX = `Self`. Просто позвать `(path)` нельзя, нужен объект. Два realistic-сценария:

1. **Через фабрику 0x6FA0B0.** Позвать её с путём к .vst3 и посмотреть, вернётся ли указатель на объект (`RAX != 0`), затем проверить, что по `[obj+0x7A0]` лежит что-то похожее на модуль.
   ```python
   fac = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(base + 0x6FA0B0)
   obj = fac(path_ptr, 0, 0)
   ```
   Ограничение: фабрика берёт класс из глобального реестра — если реестр ещё не инициализирован (плагины не сканировались), вернёт 0/NULL.
2. **Через `TVST3Plugin.Create` (0x6E0DB0)** — но `Self` всё равно надо откуда-то взять, поэтому без фабрики/движка не обойтись.

### 6.3 Рекомендуемый способ поймать «вставку в слот» (без патчинга)

Статически цепочка обрывается на виртуальных вызовах и таблицах указателей, поэтому надёжнее один раз снять её отладчиком:

1. x64dbg → attach к `FL64.exe` (не kill, не модифицировать код).
2. Breakpoint на `FLEngine_x64.dll+0x6FA0B0` (фабрика) и `+0x6E3200` (`LoadPlugin`).
3. В UI FL вручную вставить любой VST3 в свободный слот микшера.
4. На срабатывании снять: `RCX/RDX/R8/R9`, `[rsp+0x28…]` (5-й и далее аргументы), и главное — **caller** (`[rsp]` в точке входа): это и будет функция вставки в слот (ожидаемо — метод вида `TInsert.SetPlugin(Self, slot, plugin, ...)`).
5. Записать `Self`-указатели и повторить тот же вызов из Python с этими же указателями (объекты живы, пока жив проект).

Это единственный путь получить точную сигнатуру «в слот» без реконструкции Delphi-объектов вслепую.

### 6.4 Меры безопасности при вызове

* Всё, что трогает микшер/UI, должно вызываться **из главного потока** FL (иначе — deadlock или AV). В FL Python-скрипты обычно исполняются в рабочем потоке: оборачивайте вызов в очередь на UI-поток.
* `ctypes` не ловит AV. Перед вызовом проверяйте `VirtualQuery(addr)` → `PAGE_EXECUTE_*`; тестируйте на пустом проекте (`Ctrl+N`), а не на рабочем.
* Первый вызов делайте на «пустышке» (`version.dll`, см. 6.1), и только потом — на плагине.
* Не вызывайте `TVSTPlugin.Destroy`/`FreeLibrary` на объектах, созданных FL: двойное освобождение уронит процесс.

---

## 7. Инструмент

`C:\Users\epicc\mcp\flstudio-mcp\tools-local\fl_re_scan.py` (Python 3.12, capstone).
Запуск:

```
python fl_re_scan.py                          # общий отчёт: IAT, маркеры, кандидаты
python fl_re_scan.py --api LoadLibraryExW     # все вызовы одного API + строки
python fl_re_scan.py --context 0x6E3200       # дизасм функции с аннотациями
python fl_re_scan.py --callers 0x6E3200       # кто вызывает (через thunk-и)
python fl_re_scan.py --literals 0x6C9000:0x6D2000
python fl_re_scan.py --dll "…\Fruity Wrapper_x64.dll"
python fl_re_scan.py --json out.json
```

Что делает: парсит PE (секции, импорт/экспорт, `.pdata`), разрешает IAT **по содержимому** (qword → `IMAGE_IMPORT_BY_NAME`), строит карты stub-ов и thunk-ов, сканирует `call rel32` / `call [rip]` / `lea [rip]`, валидирует попадания по границам инструкций, декодирует UTF-16/ASCII литералы, вытаскивает trace-маркеры и ранжирует кандидатов.

---

## 8. Ограничения этого анализа

* Секции `.avm1/.avm3/.avm4` (≈19 МБ) содержат код без `.pdata` (записи есть только на 840 функций в `.avm2` и 33 в `.avm4`) — часть служебного кода вне анализа; впрочем, все 136 вызовов интересующих API лежат в `.text`, так что картина по загрузке полная.
* `TVST3Plugin`/`TVSTPlugin` — Delphi-классы; имена методов взяты из trace-строк, а не из RTTI, поэтому «Create» может быть не конструктором, а фабричным методом (пролог это допускает).
* Утверждение «VST2 грузит Fruity Wrapper_x64.dll» основано на: (а) отсутствии `LoadLibrary` в plugin-подсистеме движка, (б) наличии `VSTPluginMain` и `LoadLibraryExW` в этой DLL. Прямого динамического подтверждения не делалось (FL не запускался).
