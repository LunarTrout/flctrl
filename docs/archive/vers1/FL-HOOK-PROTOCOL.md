# FL Studio 25.2.5 — динамический протокол загрузки VST-плагина

Объект: `C:\Programs\Fl Studio 2025\FL64.exe` + `FLEngine_x64.dll` (ImageBase 0x400000) +
`Plugins\Fruity\Effects\Fruity Wrapper\Fruity Wrapper_x64.dll`.
Метод: **Frida 17.25.1** (spawn + attach, без отладчика) на маленьком тестовом проекте
`C:\Users\epicc\tools\fltest\small.flp` (Empty.flp + FabFilter Pro-L 2 в инсерт 1 слот 0).
Дата: 2026-11-02. Всё ниже — **снято с живого процесса**, не выведено статически.

`RVA` = смещение от базы модуля. В скобках — фактические адреса одного из прогонов
(FLEngine @ 0x74200000, Fruity Wrapper @ 0x50C30000).

---

## 0. Короткий ответ

| Вопрос | Ответ |
|---|---|
| Кто грузит DLL плагина | `Fruity Wrapper_x64.dll`, функция по **RVA 0x497A0** (вызывает `LoadLibraryW` с путём плагина) |
| Единственный экспорт обёртки | **`CreatePlugInstance` @ RVA 0x469390** — больше экспортов у DLL нет |
| Кто её вызывает | `FLEngine_x64.dll` из точки **RVA 0xE2695F** |
| Функция-загрузчик в движке | **RVA 0xE268B0** (`LoadWrapperAndCreateInstance`), 250 байт |
| Её вызывающий | **RVA 0xE28F15**, внутри функции 0xE28E70..0xE2920D |
| Аргументы загрузчика | `RCX` = путь к `Fruity Wrapper.dll`, `RDX` = &out HMODULE, `R8` = объект-плагин (params), `R9` = 0 |
| Возврат | `RAX` = указатель на созданный экземпляр плагина |
| Вставка в слот микшера | это **отдельный** шаг; UI-обработчик `0x9B6BE0` при загрузке проекта не вызывается (см. §6) |

---

## 1. Почему обычный отладчик не работает (и чем заменили)

Протектор Image-Line (секции `.avm0/.avm1` в FL64.exe, `.avm1/.avm3/.avm4` в FLEngine)
убивает процесс при любом отладчике:

| Способ | Результат |
|---|---|
| `CreateProcess(DEBUG_ONLY_THIS_PROCESS)` + свой Debug-API цикл | выход через **0.32–0.49 с**, `ExitProcess`=0 |
| То же + патч `PEB.BeingDebugged`, `PEB.NtGlobalFlag`, heap flags | без изменений |
| То же + брейкпоинты на `ExitProcess` / `RtlExitUserProcess` / `NtTerminateProcess` (обе копии ntdll) | **0 срабатываний** — процесс умирает, не вызывая ни один API завершения (похоже на прямой syscall) |
| `DebugActiveProcess` (attach к живому FL) | смерть через ~0.3 с |
| ScyllaHide Generic, инжект на системном брейкпоинте | живёт 10.4 с, затем всё равно выход |
| **x64dbg + ScyllaHide (плагин .dp64)** | **FL работает штатно** — в `Startup *.log` видна полная загрузка проекта и `Wrapper says: Create VST plugin: file="…FabFilter Pro-L 2.dll"` |
| **Frida 17.25.1 (spawn+attach)** | **работает, полный контроль, протектор не реагирует** |

Отдельно: в этой сборке x64dbg **не исполняет** ни `-c`, ни `-cf`, ни headless-команды
(`SetLogFile`/`log` не срабатывают, процесс стоит на entry-брейкпоинте) — поэтому
автоматизировать съём регистров через x64dbg не удалось. Frida дала то же самое без отладчика.

> Практический вывод: **для выборки данных по FL использовать Frida, не отладчик.**

---

## 2. Реальная цепочка загрузки (подтверждено хуками)

```
FL64.exe
 └─ FLEngine_x64.dll + 0xE28F15            (внутри 0xE28E70..0xE2920D)
     └─ FLEngine_x64.dll + 0xE268B0        RCX = "…\Fruity Wrapper.dll"
        │                                  RDX = &Self->Module (Self+0x80)
        │                                  R8  = Self  (объект-плагин, params)
        │                                  R9  = 0
        ├─ строит имя: <dir>\Fruity Wrapper + "_X64"
        ├─ LoadLibraryExW(path, 0, 0x8000)      ; 0x8000 = LOAD_LIBRARY_SAFE_CURRENT_DIRS
        │     → HMODULE  (реально грузится "Fruity Wrapper_x64.dll")
        ├─ GetProcAddress(h, "CreatePlugInstance")
        └─ CreatePlugInstance(host = [FLEngine+0x1432B90], params)   ; вызов из 0xE2695F
              └─ Fruity Wrapper_x64.dll + 0x497A0
                    └─ LoadLibraryW("C:\Programs\VST\FabFilter\FabFilter Pro-L 2.dll")
                          → VSTPluginMain → экземпляр
```

Факты, снятые хуками:

* `Fruity Wrapper_x64.dll` грузится в процесс FL через `LoadLibraryW`/`LoadLibraryExW`
  (перехвачено: `LoadLibraryW` из `FLEngine+0x4D8E0`, `LoadLibraryExW` из `FLEngine+0xE268B0`).
* У обёртки **ровно один экспорт**: `CreatePlugInstance` @ **RVA 0x469390**.
* `LoadLibraryW("…\FabFilter Pro-L 2.dll")` вызывается **изнутри Fruity Wrapper**,
  адрес возврата `Fruity Wrapper_x64.dll+0x497A0` (в прогоне `0x50C797A0` при базе `0x50C30000`).
  Это расходится со статическим отчётом (`LoadLibraryExW` @ 0x1934B): реальный путь VST2 —
  **`LoadLibraryW` из +0x497A0**, а не ExW из +0x1934B.

---

## 3. Сигнатуры (Delphi Win64 ABI: RCX, RDX, R8, R9, далее стек)

### 3.1 `FLEngine_x64.dll+0xE268B0` — загрузчик обёртки + создание экземпляра

```
function LoadWrapperAndCreateInstance(
    WrapperPath : PWideChar;      // RCX  — "…\Plugins\Fruity\Effects\Fruity Wrapper\Fruity Wrapper.dll"
    out Module  : PHMODULE;       // RDX  — сюда пишется HMODULE обёртки
    Params      : Pointer;        // R8   — объект-плагин (см. §4)
    Reserved    : Pointer         // R9   — 0
  ): Pointer;                     // RAX  — экземпляр плагина, 0 при ошибке
```

Снятые значения (прогон f12):

```
ENTER  ret=FLEngine+0xE28F15
       RCX = 0x0B53B800 -> "C:\Programs\Fl Studio 2025\Plugins\Fruity\Effects\Fruity Wrapper\Fruity Wrapper.dll"
       RDX = 0x091A31D0        (out HMODULE; адрес = Self+0x80, статически)
       R8  = 0x091A3150        (params = Self объекта-плагина)
       R9  = 0
LEAVE  RAX = экземпляр плагина
```

Внутренний код (дизасм 0xE268B0..0xE269AA) подтверждает: `_X64` приклеивается к имени,
затем `LoadLibraryExW(path,0,0x8000)`, `GetProcAddress("CreatePlugInstance")`,
`mov rcx,[FLEngine+0x1432B90]`, `mov rdx,arg3`, `call rax`.

### 3.2 `Fruity Wrapper_x64.dll!CreatePlugInstance` (RVA 0x469390)

```
function CreatePlugInstance(
    Host   : Pointer;   // RCX = [FLEngine_x64+0x1432B90] — глобальный синглтон хоста
    Params : Pointer;   // RDX — тот же объект, что R8 в §3.1
  ): Pointer;           // RAX — экземпляр
```

Снятые значения (прогон f10, адрес возврата — `FLEngine_x64.dll+0xE2695F`):

```
RCX = 0x06D6EDC0      (значение глобала [FLEngine+0x1432B90])
RDX = 0x096D2E20      (params)
R8  = 0x44C2DA79      (мусор — не аргумент)
R9  = 0
```

Дамп `params` (0x80 байт):

```
+0x00  0x74FB4960   -> FLEngine_x64.dll+0xDB4960   (указатель на интерфейс/таблицу хоста)
+0x08  0xFFFFFFFFFFFFFFFF
+0x10  0x00000000FFFFFFFF
+0x18  0
+0x20  0
+0x28  0x00000000020007D0
+0x30  0
+0x38  0
+0x40  0
+0x48  0x00000000085028F0   (указатель на кучу — вероятно, состояние/путь)
+0x50..0x5F  0
+0x60  0xFFFFFFFF00000001
+0x68  0xFFFFFFFF00000000
+0x70  0
+0x78  0x00000000006A655C   (похоже на inline ShortString "\ej")
```

Путь к DLL плагина **не лежит инлайн** в первых 0x80 байтах — он за одним из указателей
(+0x48 либо +0x00). То есть `params` — не плоская структура, а Delphi-объект.

### 3.3 UI-обработчик вставки (для полноты, статически)

`FLEngine_x64.dll+0x9B6BE0` — «вставить эффект в текущий выбранный слот»:

```
Self : Pointer;  // RCX, сохраняется в [rbp+0x48]
Flag : Boolean;  // DL,  сохраняется в [rbp+0xC8]
```
Читает `[Self+0x7FC]` (индекс текущего слота) и `[Self+0x7F4]` (массив слотов);
строки `insert effect plugin` / `replace effect plugin` / `There are no available effect slots…`.
Вызывается только из UI-слоя (0x9B6430 и 0x9B7270), **при загрузке проекта не срабатывает**.

---

## 4. Что нужно инжектору

**Минимальный рабочий вариант — переиспользовать живой объект-плагин.**
Так как `Params` (§3.1/§3.2) — это объект, а не запись, собрать его «с нуля» снаружи
нереально: внутри указатели на интерфейс хоста (`FLEngine+0xDB4960`) и на кучу.
Практичные пути, по возрастанию сложности:

1. **Взять `params` у существующего плагина.** Найти в проекте любой уже загруженный
   VST-плагин, снять его объект (например, перехватом §3.1 при загрузке проекта и
   сохранением `R8`), затем вызывать `0xE268B0` с этим же `R8` и нужным путём к обёртке.
   Годится для «клон»-сценария (тот же плагин, другой слот).
2. **Вызвать `CreatePlugInstance` напрямую** (§3.2) — на 1 уровень ниже, нужен только
   `Host` = `[FLEngine+0x1432B90]` и `params`.
3. **Полный путь через классы FL** (`TVST3Plugin.Create` 0x6E0DB0 / фабрика 0x6FA0B0,
   VST2-фабрика 0x6D6C00) — тогда `params` строит сам движок.
4. **UI-обработчик 0x9B6BE0** — самый простой по аргументам (`Self`, `bool`), но требует
   валидного UI-объекта микшера и «текущего выбранного слота»; для внешнего вызова
   нужен указатель на живой микшер-объект.

Обязательные условия для любого варианта:

* вызов строго **из главного (UI) потока** FL — иначе deadlock/AV;
* `params`/`Self` — живые объекты; после вызова нельзя освобождать их вручную;
* обёртка (`Fruity Wrapper_x64.dll`) уже загружена — её грузит сам FL при первом плагине;
* `R9` = 0, стек выровнен по 16 (Delphi Win64);
* для VST3 путь другой: `FLEngine+0x6E3200` (`TVST3Plugin.LoadPlugin`), но обёртка та же.

**Чего НЕ хватает для полного автомата:** функция, которая кладёт созданный экземпляр
в конкретный инсерт/слот микшера. В UI это `0x9B6BE0`, но при загрузке проекта вставка
идёт другим путём (через загрузчик состояния микшера), и его точку входа ещё надо снять.

---

## 5. Готовый воспроизводимый скрипт

`C:\Users\epicc\tools\fltest\f12.py` — Frida, снимает §3.1 и §3.2 целиком:

```python
dev = frida.get_local_device()
pid = dev.spawn([r"C:\Programs\Fl Studio 2025\FL64.exe",
                 r"C:\Users\epicc\tools\fltest\small.flp"],
                cwd=r"C:\Programs\Fl Studio 2025")
s = dev.attach(pid); sc = s.create_script(JS); sc.load(); dev.resume(pid)
```

Ключевой приём: хуки на `kernel32!LoadLibraryW/ExW` ставятся **до** `resume`, а хук на
`CreatePlugInstance` — в `onLeave` загрузки `Fruity Wrapper_x64.dll` (иначе не успеваем:
проект грузится через ~0.9 с). Хуки на сам FLEngine ставятся в `onLeave` его загрузки.

Рабочие прогоны: `f10.py` (params-дамп), `f12.py` (аргументы загрузчика), `f6.py` (кто грузит DLL).

---

## 6. Ограничения этого прогона

* Снят путь **VST2** (Pro-L 2 через `Fruity Wrapper`). VST3-путь (`0x6E3200`) не проверялся
  динамически — статически он описан в `FL-REVERSE.md`.
* Не снята функция постановки экземпляра в слот микшера (см. §4, последний абзац).
* Содержимое `params` разобрано только на 0x80 байт; путь к DLL плагина лежит глубже.
* x64dbg в этой сборке непригоден для автоматизации (`-c`/`-cf`/headless не исполняются),
  но **x64dbg + ScyllaHide запускает FL штатно** — если понадобится ручная отладка, это
  единственная рабочая связка.
