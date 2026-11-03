# Журнал сведения — vers1

Все правки внесены через FL Studio MCP (доступна отмена), замеры — peak-hold по 18 точкам трека.

## Базовая точка (до правок), пики dBFS

| Трек | Имя | Пик |
|---|---|---|
| 1 | Insert 1 (барабаны, SD3) | **+0.2** |
| 0 | Master | −0.3 |
| 22 | гитара L (Fortin) | −1.8 |
| 11 | Morphine | −1.9 |
| 15 | аудио-слой | −1.9 |
| 25 | вокал | −1.9 |
| 10 | гитара R (Fortin) | −2.0 |
| 23 | ритм-гитара + «вокалы1» | −2.1 |
| 9 | лид-гитара | −2.3 |
| 26–29 | гитарные слои | −3.2…−4.8 |
| 2 | **бас — ТИШИНА весь прогон** | — |
| 5, 6, 16, 30 | без сигнала в точках замера | — |

Проблемы: нет иерархии (всё в пределах 0.5 дБ), мастер в лимитере постоянно (−0.3…−0.5), мастер сдвинут вправо (pan +0.19), тарелки (канал 5, Kontakt 7 #3) в MUTE, первые 30 с тишины.

## Правки №1

| Что | Было | Стало |
|---|---|---|
| Master pan | +0.19 | 0.0 |
| Insert 1 (барабаны) | +0.85 дБ | −3.35 дБ |
| 22 / 23 / 9 / 10 (гитары) | −0.67 / −3.94 / −2.61 / −5.98 | −3 дБ каждой |
| 26–29 (слои) | −7.06…−9.11 | −9.06…−11.11 |
| 25 (вокал) | −4.46 | −6.46 |
| Канал 5 (тарелки) | mute | включён |

Результат: пик мастера −0.3 → **−1.5 dBFS** (в трёх контрольных точках).

## Открытые вопросы

1. **Бас (Insert 2, Ample Bass P)** — нулевой сигнал даже в соло. Проверить партию/плагин.
2. **Нет EQ**: FL API не загружает плагины. Нужен `Fruity Parametric EQ 2` в слот 0 на Insert 1 и Insert 2.
3. **Вокал «вокалы1» на Insert 23** — сидит на гитарной полосе, похоже на ошибку роутинга.
4. Слои 5, 6, 16, 30 не звучали в точках замера — проверить, играют ли они вообще.
5. FL грузит ~360% одного ядра: включить Smart disable, Ozone 11 на мастере — в байпас на время сведения.

## Правки №2 — архитектура шин

Всё шло напрямую в мастер. Собраны 4 шины с эксклюзивным роутингом (источник → шина вкл, источник → мастер выкл, шина → мастер вкл):

| Шина | Инсерт | Источники |
|---|---|---|
| `GTR BUS` | 31 | 22, 23, 9, 10 (усилки) + 26, 27, 28, 29 (слои) |
| `DRUM BUS` | 24 | 1 |
| `VOX BUS` | 20 | 25 |
| `AUX BUS` | 17 | 5, 6, 11 |

Панорамы шин выставлены в центр (у Insert 20 было −0.51), уровни шин — 0 дБ.
Проверка после применения: `mixer_set_route` вернул `state: true` на всех 30 маршрутах.


### auto 2026-11-01 18:04
- мост FL: не отвечает
- проанализирован рендер: `123сосать.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\123сосать.mp3  44100 Hz  322.40s
  peak -0.9 dBFS | clipped 0 | RMS -16.2 | crest 15.3 | loudest3s -14.3 dBFS @243.75s
  bands %: sub=3.9  bass=17.2  lowmid=18.0  mud=21.5  mid=20.6  upmid=10.3  harsh=5.8  presence=2.1  air=0.6
  mud/mid 1.04 | harsh% 5.8 | air+presence 2.7 | <120Hz 21.1
  L/R -1.7 dB | corr 0.42 | S/M -3.8 dB
  per-band corr: sub=0.97  bass=0.69  lowmid=0.38  mud=0.21  mid=0.38  upmid=0.49  harsh=0.37  presence=0.35  air=0.53
```

### auto 2026-11-01 21:08
- мост FL: не отвечает
- проанализирован рендер: `vers1_v5.mp3`

```
Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 196, in <module>
    main()
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 170, in main
    r = analyze(a.mix)
        ^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 104, in analyze
    x, sr = load(path)
            ^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 25, in load
    x, sr = sf.read(path, always_2d=True, dtype="float64")
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 313, in read
    with SoundFile(file, 'r', samplerate, channels,
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 708, in __init__
    self._file = self._open(file, mode_int, closefd)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 1296, in _open
    raise LibsndfileError(err, prefix=f"Error opening {self.name!r}: ")
soundfile.LibsndfileError: Error opening 'C:\\Users\\epicc\\Desktop\\PROJECTS\\vers1\\vers1_v5.mp3': System error.
```

### auto 2026-11-01 21:38
- мост FL: не отвечает
- проанализирован рендер: `vers1.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.mp3  44100 Hz  13.61s
  peak -240.0 dBFS | clipped 0 | RMS -240.0 | crest 0.0 | loudest3s -240.0 dBFS @0.0s
  bands %: sub=0.0  bass=0.0  lowmid=0.0  mud=0.0  mid=0.0  upmid=0.0  harsh=0.0  presence=0.0  air=0.0
  mud/mid 0.00 | harsh% 0.0 | air+presence 0.0 | <120Hz 0.0
  L/R -240.0 dB | corr 0.00 | S/M -240.0 dB
  per-band corr: sub=0.00  bass=0.00  lowmid=0.00  mud=0.00  mid=0.00  upmid=0.00  harsh=0.00  presence=0.00  air=0.00
```

### auto 2026-11-01 22:40
- мост FL: не отвечает
- проанализирован рендер: `vers1.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.mp3  44100 Hz  352.00s
  peak -1.5 dBFS | clipped 0 | RMS -20.3 | crest 18.8 | loudest3s -16.3 dBFS @279.0s
  bands %: sub=5.1  bass=23.0  lowmid=13.6  mud=9.4  mid=19.5  upmid=17.8  harsh=6.6  presence=2.5  air=2.4
  mud/mid 0.48 | harsh% 6.6 | air+presence 4.9 | <120Hz 28.1
  L/R 0.3 dB | corr 0.85 | S/M -10.8 dB
  per-band corr: sub=0.99  bass=0.99  lowmid=0.94  mud=0.63  mid=0.81  upmid=0.80  harsh=0.76  presence=0.57  air=0.81
```

### auto 2026-11-01 23:11
- мост FL: не отвечает
- проанализирован рендер: `preview15.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\preview15.mp3  44100 Hz  15.00s
  peak -1.7 dBFS | clipped 0 | RMS -20.0 | crest 18.2 | loudest3s -16.6 dBFS @0.0s
  bands %: sub=15.1  bass=47.1  lowmid=9.3  mud=10.1  mid=7.0  upmid=5.8  harsh=3.5  presence=1.4  air=0.6
  mud/mid 1.44 | harsh% 3.5 | air+presence 2.0 | <120Hz 62.2
  L/R -0.0 dB | corr 0.88 | S/M -11.9 dB
  per-band corr: sub=1.00  bass=0.99  lowmid=0.96  mud=0.52  mid=0.80  upmid=0.83  harsh=0.73  presence=0.47  air=0.48
```

### auto 2026-11-01 23:42
- мост FL: не отвечает
- проанализирован рендер: `preview_lim15.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\preview_lim15.mp3  44100 Hz  15.00s
  peak -2.9 dBFS | clipped 0 | RMS -20.2 | crest 17.3 | loudest3s -17.7 dBFS @0.0s
  bands %: sub=10.1  bass=32.8  lowmid=15.1  mud=13.3  mid=8.1  upmid=6.7  harsh=10.0  presence=3.5  air=0.5
  mud/mid 1.64 | harsh% 10.0 | air+presence 4.0 | <120Hz 42.9
  L/R 0.3 dB | corr 0.58 | S/M -5.8 dB
  per-band corr: sub=0.99  bass=0.96  lowmid=0.50  mud=0.18  mid=0.47  upmid=0.48  harsh=0.21  presence=0.09  air=0.38
```

### auto 2026-11-02 00:13
- мост FL: не отвечает
- проанализирован рендер: `vers1.mp3`

```
Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 196, in <module>
    main()
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 170, in main
    r = analyze(a.mix)
        ^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 104, in analyze
    x, sr = load(path)
            ^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 25, in load
    x, sr = sf.read(path, always_2d=True, dtype="float64")
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 313, in read
    with SoundFile(file, 'r', samplerate, channels,
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 708, in __init__
    self._file = self._open(file, mode_int, closefd)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 1296, in _open
    raise LibsndfileError(err, prefix=f"Error opening {self.name!r}: ")
soundfile.LibsndfileError: Error opening 'C:\\Users\\epicc\\Desktop\\PROJECTS\\vers1\\vers1.mp3': System error.
```

### auto 2026-11-02 00:44
- мост FL: не отвечает
- проанализирован рендер: `vers1.mp3`

```
Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 196, in <module>
    main()
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 170, in main
    r = analyze(a.mix)
        ^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 104, in analyze
    x, sr = load(path)
            ^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 25, in load
    x, sr = sf.read(path, always_2d=True, dtype="float64")
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 313, in read
    with SoundFile(file, 'r', samplerate, channels,
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 708, in __init__
    self._file = self._open(file, mode_int, closefd)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Lib\site-packages\soundfile.py", line 1296, in _open
    raise LibsndfileError(err, prefix=f"Error opening {self.name!r}: ")
soundfile.LibsndfileError: Error opening 'C:\\Users\\epicc\\Desktop\\PROJECTS\\vers1\\vers1.mp3': System error.
```

### auto 2026-11-02 01:15
- мост FL: не отвечает
- проанализирован рендер: `preview_v21.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\preview_v21.mp3  44100 Hz  20.00s
  peak -2.6 dBFS | clipped 0 | RMS -20.5 | crest 17.9 | loudest3s -17.7 dBFS @0.0s
  bands %: sub=8.3  bass=28.3  lowmid=15.2  mud=13.3  mid=7.4  upmid=6.7  harsh=13.7  presence=6.5  air=0.5
  mud/mid 1.80 | harsh% 13.7 | air+presence 7.0 | <120Hz 36.6
  L/R 0.0 dB | corr 0.57 | S/M -5.6 dB
  per-band corr: sub=0.99  bass=0.95  lowmid=0.45  mud=0.28  mid=0.49  upmid=0.51  harsh=0.28  presence=0.18  air=0.44
```

### auto 2026-11-02 01:46
- мост FL: жив
- проанализирован рендер: `preview_v22.mp3`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\preview_v22.mp3  44100 Hz  20.00s
  peak -2.6 dBFS | clipped 0 | RMS -20.5 | crest 17.9 | loudest3s -17.7 dBFS @0.0s
  bands %: sub=8.3  bass=28.3  lowmid=15.2  mud=13.3  mid=7.4  upmid=6.7  harsh=13.7  presence=6.5  air=0.5
  mud/mid 1.80 | harsh% 13.7 | air+presence 7.0 | <120Hz 36.6
  L/R 0.0 dB | corr 0.57 | S/M -5.6 dB
  per-band corr: sub=0.99  bass=0.95  lowmid=0.45  mud=0.28  mid=0.49  upmid=0.51  harsh=0.28  presence=0.18  air=0.44
```

### auto 2026-11-02 14:09
- мост FL: не отвечает
- проанализирован рендер: `vers1_converted.wav`

```
=== MIX ===
  C:\Users\epicc\Desktop\PROJECTS\vers1\vers1_converted.wav  44100 Hz  372.65s
  peak -1.2 dBFS | clipped 0 | RMS -12.4 | crest 11.3 | loudest3s -10.4 dBFS @191.25s
  bands %: sub=6.4  bass=28.2  lowmid=26.1  mud=9.8  mid=9.0  upmid=6.6  harsh=9.8  presence=3.7  air=0.4
  mud/mid 1.09 | harsh% 9.8 | air+presence 4.1 | <120Hz 34.6
  L/R -0.2 dB | corr 0.95 | S/M -16.3 dB
  per-band corr: sub=0.99  bass=0.99  lowmid=0.98  mud=0.98  mid=0.91  upmid=0.86  harsh=0.94  presence=0.82  air=0.41
```

### auto 2026-11-02 23:27
- мост FL: не отвечает
- проанализирован рендер: `mixrender.mp3`

```
Traceback (most recent call last):
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 24, in <module>
    from . import multiarray
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\multiarray.py", line 11, in <module>
    from . import _multiarray_umath, overrides
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\overrides.py", line 6, in <module>
    from numpy._core._multiarray_umath import (
ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 12, in <module>
    import numpy as np
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__init__.py", line 122, in <module>
    from numpy.__config__ import show_config
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__config__.py", line 4, in <module>
    from numpy._core._multiarray_umath import (
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 85, in <module>
    raise ImportError(msg) from exc
ImportError: 

IMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!

Importing the numpy C-extensions failed. This error can happen for
many reasons, often due to issues with your setup or how NumPy was
installed.
The following compiled module files exist, but seem incompatible
with with either python 'cpython-312' or the platform 'win32':

  * _multiarray_umath.cp314-win_amd64.lib
  * _multiarray_umath.cp314-win_amd64.pyd

We have compiled some common reasons and troubleshooting tips at:

    https://numpy.org/devdocs/user/troubleshooting-importerror.html

Please note and check the following:

  * The Python version is: Python 3.12 from "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\python.exe"
  * The NumPy version is: "2.5.4"

and make sure that they are the versions you expect.

Please carefully study the information and documentation linked above.
This is unlikely to be a NumPy issue but will be caused by a bad install
or environment on your machine.

Original error was: No module named 'numpy._core._multiarray_umath'
```

### auto 2026-11-02 23:58
- мост FL: не отвечает
- проанализирован рендер: `vers1_Current.wav`

```
Traceback (most recent call last):
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 24, in <module>
    from . import multiarray
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\multiarray.py", line 11, in <module>
    from . import _multiarray_umath, overrides
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\overrides.py", line 6, in <module>
    from numpy._core._multiarray_umath import (
ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 12, in <module>
    import numpy as np
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__init__.py", line 122, in <module>
    from numpy.__config__ import show_config
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__config__.py", line 4, in <module>
    from numpy._core._multiarray_umath import (
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 85, in <module>
    raise ImportError(msg) from exc
ImportError: 

IMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!

Importing the numpy C-extensions failed. This error can happen for
many reasons, often due to issues with your setup or how NumPy was
installed.
The following compiled module files exist, but seem incompatible
with with either python 'cpython-312' or the platform 'win32':

  * _multiarray_umath.cp314-win_amd64.lib
  * _multiarray_umath.cp314-win_amd64.pyd

We have compiled some common reasons and troubleshooting tips at:

    https://numpy.org/devdocs/user/troubleshooting-importerror.html

Please note and check the following:

  * The Python version is: Python 3.12 from "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\python.exe"
  * The NumPy version is: "2.5.4"

and make sure that they are the versions you expect.

Please carefully study the information and documentation linked above.
This is unlikely to be a NumPy issue but will be caused by a bad install
or environment on your machine.

Original error was: No module named 'numpy._core._multiarray_umath'
```

### auto 2026-11-03 01:31
- мост FL: не отвечает
- проанализирован рендер: `АХУЕННЫЙ ШТОРМ.mp3`

```
Traceback (most recent call last):
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 24, in <module>
    from . import multiarray
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\multiarray.py", line 11, in <module>
    from . import _multiarray_umath, overrides
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\overrides.py", line 6, in <module>
    from numpy._core._multiarray_umath import (
ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\epicc\mcp\flstudio-mcp\tools-local\mixcheck.py", line 12, in <module>
    import numpy as np
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__init__.py", line 122, in <module>
    from numpy.__config__ import show_config
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\__config__.py", line 4, in <module>
    from numpy._core._multiarray_umath import (
  File "C:\Users\epicc\AppData\Local\hermes\installs\cb15a00554ac4ef5\environments\fd9f5a3bad6c4153ad40347d3a422ebc\venv\Lib\site-packages\numpy\_core\__init__.py", line 85, in <module>
    raise ImportError(msg) from exc
ImportError: 

IMPORTANT: PLEASE READ THIS FOR ADVICE ON HOW TO SOLVE THIS ISSUE!

Importing the numpy C-extensions failed. This error can happen for
many reasons, often due to issues with your setup or how NumPy was
installed.
The following compiled module files exist, but seem incompatible
with with either python 'cpython-312' or the platform 'win32':

  * _multiarray_umath.cp314-win_amd64.lib
  * _multiarray_umath.cp314-win_amd64.pyd

We have compiled some common reasons and troubleshooting tips at:

    https://numpy.org/devdocs/user/troubleshooting-importerror.html

Please note and check the following:

  * The Python version is: Python 3.12 from "C:\Users\epicc\mcp\flstudio-mcp\.venv312\Scripts\python.exe"
  * The NumPy version is: "2.5.4"

and make sure that they are the versions you expect.

Please carefully study the information and documentation linked above.
This is unlikely to be a NumPy issue but will be caused by a bad install
or environment on your machine.

Original error was: No module named 'numpy._core._multiarray_umath'
```
