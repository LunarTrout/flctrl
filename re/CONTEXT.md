# Задача: читать аудио КАЖДОЙ дорожки FL Studio в онлайне, без кнопки записи

Цель: получить PCM каждой дорожки микшера во время обычного воспроизведения —
без transport.record(), без клипов в плейлисте, без зависаний. Работать на тестовом
проекте, НЕ на рабочем проекте пользователя.

## Жёсткие правила

1. **Не открывать, не сохранять и не менять** `C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp`
   и вообще ничего в `Desktop\PROJECTS\vers1`. Тестовые проекты — только копии в
   `C:\Users\epicc\AppData\Local\hermes\cache\scratch\re\`.
2. FL — один процесс, проект в нём один. Если нужно сменить проект: сначала
   `taskkill /F /IM FL64.exe`, потом запуск с нужным файлом. Никогда не сохранять чужой проект.
3. Отладчики (x64dbg, gdb) **убивают FL**. Работает только Frida. Никаких сканов всей
   памяти и Stalker — от них FL трижды падала. Читать только известные/найденные адреса,
   хуки ставить точечно.
4. Не вешать FL: если процесс не отвечает больше 10 с — `taskkill`, перезапуск.
5. Кнопка записи (transport.record) — НЕ решение. Нужен путь без неё.

## Что уже известно про FL 2025 (25.2.5 build 5319, Producer Edition)

### Мост (управление из Python)
- TCP `127.0.0.1:9787`, JSON `{'op':'call','cmd':'exec','params':{'expr': ...}}` + `\n`,
  ответ `data.result`; значения приходят **строками**.
- Демон: `C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\fl-studio-mcp-daemon.exe`,
  запускать с `FLSTUDIO_MCP_PORT_FROM_FL="FLStudioMCP RX"`, cwd `C:\Users\epicc\mcp\flstudio-mcp`.
  Если мост молчит — перезапустить демон (часто нужен после рестарта FL).
- Хелперы библиотеки: `sys.path.insert(0, r"C:/Users/epicc/mcp/flstudio-lib/flctrl")`,
  затем `import live`; `live._bridge("mixer.getTrackCount()")`, `live.fl_pid()`,
  `live._pid_of`, `live.fl_window()`.

### Полезное из API FL (проверено)
- `dir(mixer)`, `dir(transport)`, `dir(playlist)`, `dir(general)`, `dir(ui)` — можно
  печатать прямо через мост.
- `mixer.getTrackPeaks(i, ch)` — огибающая, не звук.
- `transport.setSongPos(f)` — доля 0..1 (не секунды).
- `general.getUndoLevelHint()` → строка вида `"1/5"` (в кавычках!), `general.undoUpDown(-1)`.
- Кнопка Record: `transport.record()`, `transport.isRecording()`.

### Запись (то, что работает, но ВРЕДНО)
- При Disk Recording FL пишет каждый armed-трек в WAV; вызов идёт из
  **`FLEngine_x64.dll+0x3b5fa` → `WriteFile`** (return address в стеке).
- В буфере: первые 4 байта — служебная длина, дальше сырой **float32 stereo**.
- Найденные при этом регистры (пример): `rbx` — объект писателя (в нём `+8` — хендл файла,
  `+0x40` — указатель рядом с буфером), `rdx/rsi` — буфер, `rdi/r8/r14` — длина в байтах,
  `rbp` — кадр стека.
- Опция `AutoCreateClip` (реестр `HKCU\Software\Image-Line\FL Studio 25`) вставляет клипы
  в плейлист при сохранении. Нас это больше не интересует: цель — вообще не включать запись.

### Движок: что уже пробовали
- Модуль: `C:\Programs\Fl Studio 2025\FLEngine_x64.dll` (57 МБ), ImageBase 0x400000,
  .text RVA 0x1000..0xE9569C.
- `FLEngine_x64.dll+0x78f624` — **thunk**, который зовёт ASIO-драйвер:
  ```
  mov rax, [rcx+8] ; mov rcx, rax ; mov rax, [rax] ; call qword ptr [rax+0x68]
  ```
  Регистры на входе (замерено): `rdi=2`, `rsi=0x400` (кадров), `rdx` и `r8` — указатели
  в буфер вывода (разница 8 байт), `rcx` — обёртка, `[rcx+8]` даёт объект.
- По `[rcx+8]` получили объект, в первых байтах которого строки "Apartment", "FL Studio ASIO",
  "ILWA..." — это конфиг/настройки аудио, **не микшер**. Цепочку `[[obj]]+0x68` брать нельзя.
- Старые смещения (с другой сборки, здесь НЕ работают): track array `FE+0x10A7EB0`,
  frames `FE+0x10AA730`, count `FE+0x10A9850`, шаг структуры 0x1474, звук `[track+0x34]`.
  Проверено на живой FL: мусор. Нужно найти заново.
- Ошибки с WriteFile: наши смещения выше — единственное, что подтверждено.

### Инструменты
- Python 3.12 с `frida`, `capstone`: `C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\python.exe`
- Python 3.13 с numpy/scipy/soundfile: `...\flstudio-mcp\.venv313\Scripts\python.exe`
- ffmpeg: `%LOCALAPPDATA%\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffmpeg.exe`
- Скрипты-заготовки в `C:\Users\epicc\AppData\Local\hermes\cache\scratch\`:
  `re_writefile.py` (хук WriteFile), `re_probe2.py` (чтение структур), `re_callback.py`,
  `re_chain.py` (хук thunk), `re_struct.py`, `dis84.py` (дизассемблер по RVA: `dis84.py 78F624 0x300`).
- Frida 17: `Module.getExportByName` не существует → `Process.getModuleByName('kernel32.dll').getExportByName('WriteFile')`.
- **Нельзя ставить хук изнутри другого хука на аудио-потоке** — это вешает FL. Сначала
  снять адрес и вернуть управление, второй хук ставить отдельным вызовом.

## Как проверять результат

Проект для тестов: копия `C:\Users\epicc\tools\fltest\small.flp` (16 дорожек, пусто) —
плохо для проверки звука. Лучше собрать тестовый проект с настоящим звуком:
- либо копия `components/demo2` (`C:\Users\epicc\tools\fltest\fixtures\demo2.flp` — демо «With You», 48 дорожек),
- либо `small.flp` + сэмпл на дорожку (можно вставить через FL API/скрипты).
Критерий: буферы, снятые новым способом, должны совпасть по форме сигнала с эталоном,
записанным через Disk Recording на ТОМ ЖЕ тестовом проекте (эталон снимать один раз,
на копии, и сохранить в `re/fixtures/`).

## Координация между двумя агентами

- Папка обмена: `C:\Users\epicc\mcp\flstudio-lib\re\mail\`
  Файлы: `<NNN>-<A|B>-to-<A|B>.md`, нумерация по порядку. Пиши коротко: что нашёл,
  адрес/смещение, как проверить, что нужно от второго.
- Свои находки — в `re/CONTEXT-<A|B>.md` (дописывать, не перезаписывать чужие).
- Проверяй папку `mail` перед каждым крупным шагом (раз в 1–2 минуты), отвечай.
- Не дублируй работу: A — статика (дизассемблер, поиск структур, карта смещений),
  B — динамика (Frida, проверка на живой FL, прототип чтения). Договоритесь, кто что делает,
  через первую же почту.

## Итог, который нужен

1. Карта структур: где массив дорожек, шаг, поля (имя, указатель на буфер, кадры, число каналов).
2. Рабочий прототип: `re/proto_stream.py` — снимает N секунд со всех дорожек во время
   обычного PLAY (без record) и складывает в `re/out/*.wav` + печатает rms по дорожкам.
3. Доказательство: rms/форма совпадает с эталоном Disk Recording на тестовом проекте
   (допуск: корреляция > 0.95 на тех же 2 с, тот же участок).
4. Короткий отчёт: `re/FINDINGS.md` — что нашли, какие адреса, что осталось.
