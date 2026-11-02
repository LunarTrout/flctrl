# RESEARCH-EFFECTS-WEB: объективная оценка и автоподстройка эффектов

Дата: 3 ноября 2026. Источник — только внешний веб (AES/DAFx/arXiv, GitHub, датасеты, документация).
Проект `flstudio-lib` не запускался, файлы `vers1` не измерялись, код не менялся.

Обозначения в колонке «лицензия»:
✅ — лицензия подтверждена на странице репозитория/датасета;
⚠️ — лицензию не удалось подтвердить автоматически (лимит GitHub API / нет файла LICENSE) — проверять перед коммерческим использованием;
📄 — открыта только аннотация/название, полный текст не отдался (PDF за пейволом/таймаут).

---

## 0. Короткий ответ

1. **Готового «универсального микшера-оценщика» нет.** Объективная оценка автоматического микса — открытая проблема, это признают и сами авторы: [emergentmind: Objective evaluation of automatic music mixing systems](https://www.emergentmind.com/open-problems/objective-evaluation-automatic-music-mixing).
2. **Но по каждому эффекту есть готовый измерительный аппарат** — из акустики (ISO 3382-1), из аудиоинженерии (AES17, BS.1770) и из research-кода (pyroomacoustics, dasp-pytorch, ViSQOL, CDPAM).
3. **Ключ — разделить эффекты на три класса** и мерить их разными пробниками (см. §1). Один и тот же тест на все эффекты даёт мусор.
4. **Для автоподстройки есть два рабочих пути:** дифференцируемые модели (dasp-pytorch, FLAMO, DiffVox, DeepAFx-ST) и black-box поиск (Optuna) по метрике на реальном рендере FL. Второй — дешевле и не врёт про наш плагин.
5. **«Ganymed» как микшер не существует.** В вебе это бесплатный FM-синтезатор 2016 г. ([VST4YOU Ganymed](https://www.youtube.com/watch?v=wbbGkwgobSk)) и куча посторонних проектов. Автоматического микшера с таким именем не найдено. Подробнее — §14.

---

## 1. Три класса эффектов → три метода замера

| класс | эффекты | что валидно мерить | чем |
|---|---|---|---|
| **LTI** (линейный, не меняется во времени) | реверб, дилей, EQ, фильтры, стерео-матрица | импульсный отклик: RT60/EDT, C50/D50, Ts, плотность эха, IACC, АЧХ хвоста | импульс или exp-sweep → деконволюция → IR → метрики |
| **Статически нелинейный** | сатурация, дистошн, лимитер (в статике) | THD, THD+N, IMD (CCIF/SMPTE), алиасинг, наклон transfer curve | синусы по уровням, двухтоновые сигналы, свип |
| **Время-зависимый** | компрессор, сайдчейн, де-эссер, гейт | статическая кривая, attack/release, глубина и тайминг дакинга, полосное подавление | ступенька/burst, огибающая (Hilbert/STFT), полосные энергии |

Практическое следствие: **сначала снимаем IR** (для LTI-части цепи), **потом** гоняем уровневые и временные пробники. IR снимается один раз на пресет и переиспользуется.

---

## 2. Реверб

### 2.1 Что мерить (метрики)

| метрика | что означает | как считать |
|---|---|---|
| **RT60 / T30 / T20** | время спада на 60/30/20 дБ | обратное интегрирование EDC (Шредер) по IR |
| **EDT** | early decay time (0…−10 дБ) — коррелирует с воспринимаемой «мокростью» | EDC на первых 10 дБ |
| **C50 / D50** | ratio ранней (0–50 мс) к поздней энергии / доля ранней | интегралы EDC |
| **Ts** (centre time) | «центр тяжести» энергии — баланс ранней/поздней | ∫t·E(t)dt / ∫E(t)dt |
| **NED / echo density** | плотность отражений во времени; ловит «зернистый»/«flutters» хвост | Sorted Center Time / нормализованный профиль |
| **IACC** | межканальная корреляция хвоста → ширина реверба | нормированная кросс-корреляция L/R по окнам |
| **Относительная громкость реверба** | LUFS хвоста минус LUFS прямого звука | BS.1770 на разделённых dry/wet |

### 2.2 Инструменты

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **EIR + relative reverb loudness + EDT** (De Man, McNally, Reiss, JAES 65(1/2), 2017) | https://dspace.library.uvic.ca/server/api/core/bitstreams/bb2c7b52-aca8-45a1-b87f-f699d433aa31/content | Предиктор воспринимаемого количества реверба: относительная громкость реверба + EDT, извлечённые из «эквивалентного импульсного отклика»; обе фичи значимо влияют на восприятие и задают верх/низ желаемого количества | CC BY 4.0 ✅ | Считать на шине: IR реверба + сухой сигнал → EIR → LUFS (pyloudnorm) + EDT | Сравнить предсказание с оценкой на 10–15 отрезках; откалибровать границы «сухо/норма/мокро» |
| **pyroomacoustics** `measure_rt60` | https://pyroomacoustics.readthedocs.io/en/latest/pyroomacoustics.experimental.rt60.html | RT60 по Шредеру из IR (+T20/T30) | MIT ✅ | `pip install pyroomacoustics`; `measure_rt60(ir, fs)` — RT60 по обратному интегрированию EDC (T20/T30 считаются из той же EDC) | Импульс через цепь → RT60; сверить T30 с паспортом пресета |
| **blind_rt60** (Ratnam et al.) | https://github.com/nuniz/blind_rt60 | Слепая оценка RT60 по музыке/речи, **без IR** | MIT ✅ | Скопировать класс или поставить из репо | Прогнать сухой и мокрый стем: ΔRT60 = «мокрость» |
| **EchoDensity** (Microsoft) | https://github.com/microsoft/EchoDensity | Sorted Center Time — плотность эха во времени | MIT ✅ | Python-скрипты из репо | NED(t) на IR: ранние отражения ≈0, диффузный хвост → 1 |
| **normalized echo density** (Echo2Reverb `tools.py`) | https://github.com/guanamusic/Echo2Reverb/blob/main/tools.py | Готовая функция NED на torch (окна 750/50) — годится как loss | ⚠️ | Импорт функции | NED(t) для целевого и своего IR — сравнить кривые |
| **differentiable-fdn** | https://github.com/nickmartins-lambda/differentiable-fdn | Дифференцируемая FDN + loss = EDC + soft-NED; подгонка параметров под целевой IR | ⚠️ | PyTorch | Обучить на целевом IR → сравнить RT60/NED/спектр |
| **FLAMO** (Dal Santo et al., ICASSP 2025) | https://github.com/gdalsanto/delay-network-reverbs | Библиотека частотно-доменной дифференцируемой обработки; FDN-ревербы с per-band T60 | ⚠️ | Python/C++ | Подбор T60 по полосам, сверка с целевым IR |
| **Faust `reverbs.lib`** | https://faustlibraries.grame.fr/libs/reverbs/ | Референсные реализации: freeverb, `zita_rev1_stereo`, `dattorro_rev`, `fdnrev0` с **отдельными T60 по полосам** | см. LICENSE Faust ⚠️ | `faust2vst`/`faust2api` или читать как спецификацию | Сравнить per-band RT60 пресета с эталоном |
| **DragonFly Reverb** | https://github.com/michaelwillis/dragonfly-reverb | Бесплатный реверб (Freeverb3): room/hall/plate/early reflections | GPL-3.0 ✅ | VST3/LV2 в FL | IR-замер + A/B как «эталон жанра» |
| **Airwindows** | https://github.com/airwindows/airwindows | ~200 эффектов, десятки ревербов; единый consolidated-плагин (CLAP/AU/VST3/LV2) | MIT ✅ | Собрать или взять готовый VST3 | IR-замер (RT60, NED) + слепое прослушивание |
| **OpenAIR** | https://audeering.github.io/datasets/datasets/openair.html | Реальные IR с метками RT60 — эталон свёртки | CC BY 4.0 ✅ | Скачать → `scipy.signal.fftconvolve` | Свернуть сухой стем → измерить RT60, должен совпасть с меткой |

### 2.3 Как проверить свой реверб за 10 минут

1. Рендерим единичный импульс через цепь (dry=0, wet=100 %) → `ir.wav`.
2. `measure_rt60(ir)` → RT60; `EDT` из EDC; `C50` из интегралов.
3. Строим EDC в дБ и смотрим, нет ли излома (признак «двух хвостов» — дилей+реверб).
4. NED(t): если выходит на 1 позже ~80 мс — хвост диффузный; если дёргается — «зернистый».
5. Повторяем на 3 пресетах (short/med/long) и на 3 IR из OpenAIR — сравниваем форму, не абсолют.

---

## 3. Дилей

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **Анализ тапов по импульсу** (рецепт) | — | Времена тапов (мс), затухание feedback (дБ на повтор), ping-pong (чередование L/R), фильтр в петле (наклон АЧХ по повторам) | — | `scipy.signal.find_peaks` по огибающей; FFT по каждому тапу | Импульс → пики; времена перевести в доли beat |
| **librosa** `beat.tempo` | https://librosa.org/doc/latest/generated/librosa.beat.tempo.html | BPM для проверки «1/4, 1/8, dotted, triplet» | ISC ✅ | `librosa.beat.tempo(y=y, sr=sr)` | Сравнить tap/BPM с сеткой проекта; расхождение >2 % — не в темп |
| **DiffVox** | https://ar5iv.labs.arxiv.org/html/2504.14735 | Дифференцируемая модель **ping-pong дилея** + FDN-реверба; подгонка под распределение «профессиональных эффектов» | ⚠️ | PyTorch (код при статье) | Обучить на референс-дилее → сравнить позиции тапов и спад |
| **Faust `delays.lib`** | https://faustlibraries.grame.fr/libs/delays/ | Референс семантики `de.delay`, `fdelay` (интерполяция, модуляция) | см. LICENSE ⚠️ | faust2vst | Сверить времена/интерполяцию с эталоном |
| **FFmpeg `aecho`** | https://ffmpeg.org/ffmpeg-filters.html#aecho | Простейший эталон многократного эха для тест-сигналов | LGPL ✅ | `ffmpeg -af aecho=...` | Прогнать один и тот же импульс, сверить тапы |

**Подводный камень:** модулированный дилей (chorus-подобный) — уже не LTI, IR «плывёт». Для него мерить только распределение задержек по времени (STFT-кросс-корреляция), а не один IR.

---

## 4. Сатурация / нелинейность

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **dasp-pytorch** `distortion` | https://github.com/csteinmetz1/dasp-pytorch | Дифференцируемый distortion(drive); в README — восстановление drive градиентным спуском (пример на 2500 итераций) | Apache-2.0 ✅ | `pip install dasp-pytorch`; `dasp_pytorch.functional.distortion(x, sr, drive)` | Взять wet, восстановить drive → ошибка в дБ; <1 дБ — модель адекватна |
| **AES17** (THD+N) | https://www.aes.org/standards/ | Стандарт измерения THD+N: полоса, взвешивание, уровень | стандарт, платно | Реализовать FFT-метод по определению | Синус 1 кГц, уровни −60…0 dBFS → кривая THD vs level |
| **IMD: CCIF (19+20 кГц) и SMPTE (60 Гц + 7 кГц, 4:1)** (рецепт) | — | Продукты интермодуляции — чувствительнее THD к «грязи» на сложном сигнале | — | numpy FFT, искать 1 кГц и боковые | Рост IMD при увеличении drive; сравнить с эталонным плагином |
| **ToneTwisT AFx** | https://github.com/mcomunita/tonetwist-afx-dataset | Сухие/обработанные пары: аналог и цифра, в т.ч. **параметрические** (хватает на обучение модели) | MIT ✅ | Zenodo-скачивание по README | Обучить/проверить модель; null-тест на паре |
| **Open-Amp** (Wright et al., 2024) | https://arxiv.org/abs/2411.14972 | Синтетическая генерация dry/wet на сотнях устройств, рендер онлайн | ⚠️ | Фреймворк + краудсорс-эмуляции | Датасет для обучения/валидации нелинейных моделей |
| **Airwindows** | https://github.com/airwindows/airwindows | Множество читаемых алгоритмов сатурации (MIT) — можно сравнить формулы | MIT ✅ | Сборка/чтение кода | Сравнить THD-кривые своего и эталонного алгоритма |
| **ChowTapeModel** | https://github.com/Chowdhury-DSP/ChowTapeModel | Ленточная сатурация: гистерезис + оверсэмплинг — эталон по алиасингу | ⚠️ | VST3 | Тест алиасинга (ниже) |
| **Тест алиасинга** (рецепт) | — | Свип 20 Гц–20 кГц на −3 dBFS → STFT: образы выше/ниже гармоник | — | STFT + маска гармоник | Уровень образов ниже −60 dBFS — хорошо; выше −40 — слышно |

**Что важно:** «тёплая» сатурация — это в первую очередь **спектр гармоник** (2-я/3-я), а не THD целиком. Одинаковый THD при разном соотношении H2/H3 звучит по-разному. Считать отдельно амплитуды H2, H3, H5 на нескольких уровнях.

---

## 5. Компрессия

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **DAFx-26: Evaluating DRC models using control-voltage measurements** (Thompson, Heilemann) | https://dafx.de/paper-archive/2026/papers/DAFx26_paper_30.pdf | Подход + датасет для оценки моделей компрессоров через измерения управляющего напряжения (то, что реально управляет усилением) | 📄 | — | Сравнить статические кривые и огибающие с эталоном |
| **SignalTrain LA-2A** (Colburn, Hawley) | https://zenodo.org/records/3824876 | 20.7 ГБ пар вход/выход реального LA-2A при разных положениях Peak Reduction | CC BY 4.0 ✅ | Скачать tgz (20 ГБ) | Прогнать модель на паре: SI-SDR / MR-STFT; сравнить с бейзлайном |
| **SignalTrain** (arXiv 1905.11928) | https://arxiv.org/abs/1905.11928 | Методика профилирования компрессора нейросетью + как собирались данные | 📄 | — | — |
| **pyloudnorm** | https://github.com/csteinmetz1/pyloudnorm | LUFS по ITU-R BS.1770-4, LRA | MIT ✅ | `pip install pyloudnorm` | LUFS до/после; падение LRA = «зажали» |
| **Essentia** `DynamicComplexity` / `LoudnessEBUR128` | https://essentia.upf.edu/algorithms_reference.html | Динамическая сложность (мера сжатости) + EBU R128 | AGPL-3.0 ✅ | `pip install essentia` | Δ DynamicComplexity dry→wet |
| **Faust `compressors.lib`** | https://faustlibraries.grame.fr/libs/compressors/ | `compressor_mono/stereo`, `limiter_1176_R4` — эталон семантики ratio/attack/release | см. LICENSE ⚠️ | faust2vst или читать | Статическая кривая совпадает с формулой |
| **dasp-pytorch** (DRC) | https://github.com/csteinmetz1/dasp-pytorch | Дифференцируемый компрессор → подбор параметров под референс | Apache-2.0 ✅ | pip | Восстановить threshold/ratio из wet |
| **Статическая кривая + step response** (рецепт) | — | Ratio, knee, threshold, attack/release в мс | — | Синус 1 кГц, уровни −60…0 dBFS шагом 3 дБ; burst 50 мс | Наклон 1:1 вне knee; ratio = Δin/Δout; attack/release по 90 %/10 % огибающей |

**Порядок проверки:** сначала статика (ratio/knee/threshold), потом время (attack/release), потом «музыкальность» (GR на реальном стеме ≤ 3–6 дБ, иначе слышно «давление»).

---

## 6. Сайдчейн

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **Огибающая несущей + триггер** (рецепт) | — | Глубина дакинга (дБ), attack/release (мс), тайминг (задержка между ударом и провалом), «pumping» | — | Hilbert/STFT огибающая; триггер — kick; кросс-корреляция onset↔провал | Тест: kick + pad. Глубина 3–6 дБ типична; задержка <10 мс; провал только на ударе |
| **FFmpeg `sidechaingate`** | https://ffmpeg.org/ffmpeg-filters.html#sidechaingate | Референсная реализация сайдчейн-гейта (порог, attack/release, диапазон) | LGPL ✅ | `ffmpeg -filter_complex` с двумя входами | Прогнать тест-сигнал, сверить огибающую с формулой |
| **Faust** SC-варианты (`expanderSC_N_chan`) | https://faustlibraries.grame.fr/libs/compressors/ | Как устроен sidechain-вход (SCsignal, SCfunction) — эталон архитектуры | см. LICENSE ⚠️ | faust | — |
| **Проверка маршрутизации в FL** | (наш `fl_set_route`) | Убедиться, что сайдчейн реально включён, а не «нарисован» | — | `src→dst` + mute триггера | Mute триггера → дакинг должен исчезнуть; иначе цепь не та |

**Метрика «стало лучше» для сайдчейна:** не «глубже», а **стабильность баса** — std огибающей низа в такт (должна падать) при неизменной общей громкости (±0.3 LU).

---

## 7. Де-эссер

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **LSP Deesser** | https://lsp-plug.in/?page=manuals&section=deesser_mono | Референсный split-band де-эссер; CLAP/LADSPA/LV2/VST2/VST3 | GPL-3.0 ✅ | Установить, A/B в FL | Спектр 5–9 кГц до/после; проверить, что гласные не провалились |
| **FFmpeg `deesser`** | https://ffmpeg.org/ffmpeg-filters.html#deesser | Скриптуемый де-эссер (`intensity`, `max_de`, `f`, `m`) | LGPL ✅ | `ffmpeg -af deesser` | Прогнать синтетический HF-шум и реальный вокал |
| **Cytrus** (Springer, 2020) | https://link.springer.com/content/pdf/10.1007/978-3-030-43887-6_49.pdf | CNN-де-эссер + методика оценки (данные, метрики) | 📄 | — | Сравнить с классическим split-band |
| **Sibilance ratio** (рецепт) | — | Отношение энергии 5–9 кГц к 1–4 кГц; до/после; отдельно на «с/ш/ц» и на гласных | — | STFT-полосы, порог по кадрам | Снижение 3–6 дБ на сибилянтах, ≤1 дБ на гласных; иначе «шепелявость» |

**Ключевая проверка:** де-эссер должен **не трогать** не-сибилянтные кадры. Считать распределение Δ(5–9 кГц) по кадрам: медиана ≈0, хвост вниз — то, что надо.

---

## 8. Стерео и моносовместимость

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **ODAQ stereo** (Delgado et al., AES 159th, 2025) | https://arxiv.org/abs/2512.10689 | Бенчмарк объективных метрик на M/S- и L/R-деградациях. Вывод: чисто тембральные метрики (PEAQ, NMR, PEMO-Q, HAAQI) **ломаются** на сложном стереоконтексте; нужны бинауральные признаки (IACC, ILD, ITD) | arXiv (CC BY 4.0) ✅ | Датасет ODAQ + метрики | Прогнать свою метрику на ODAQ, сравнить корреляцию с MUSHRA |
| **QMUL, Convention Paper 10186** (Mourgela et al., 2024) | https://www.eecs.qmul.ac.uk/~josh/documents/2024/Mourgela%20Exploring.pdf | Разбор понятий stereo width и mono compatibility + метрики для них | 📄 | — | Использовать как словарь определений |
| **Matchering** | https://github.com/sergree/matchering | Подгоняет RMS, АЧХ, пик и **stereo width** целевого трека под референс | GPL-3.0 ✅ | `pip install matchering` | Сравнить width/M-S до и после; ΔLUFS при моно-сумме |
| **Essentia** `FalseStereoDetector` | https://essentia.upf.edu/algorithms_reference.html | Ловит «фальшивое стерео» (одинаковые каналы) | AGPL-3.0 ✅ | `es.FalseStereoDetector()` | Прогнать стемы: флаг = канал фактически моно |
| **FFmpeg** `aphasemeter`, `stereotools`, `extrastereo` | https://ffmpeg.org/ffmpeg-filters.html#aphasemeter | Корреляция/фаза, M/S-инструменты, расширение | LGPL ✅ | ffmpeg | Корреляция ≥0 на низах; иначе бас «исчезнет» в моно |
| **M/S + mono-sum** (рецепт) | — | side/mid (дБ), коэффициент корреляции (общий и по полосам), ΔLUFS и Δспектр при сумме в моно | — | numpy: `M=(L+R)/2`, `S=(L−R)/2` | Эвристика: ΔLUFS ≤ 1 LU, corr > 0.2 на 60–120 Гц, провал 1–3 кГц при моно ≤ 1.5 дБ |

**Важно:** «ширина» без «моносовместимости» — ловушка. Любой расширитель проверять двумя числами сразу: прирост side/mid **и** потеря при сумме в моно.

---

## 9. Глобальные метрики «стало лучше»

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **ViSQOL** | https://github.com/google/visqol | Full-reference MOS-LQO 1…5 (spectro-temporal similarity), есть режим audio | Apache-2.0 ✅ | Собрать bin, вызывать из Python | Сравнить dry/wet против референса; рост MOS-LQO = ближе к цели |
| **CDPAM / PAM** (Manocha et al.) | https://github.com/pranaymanocha/PerceptualAudio | Обученная перцептивная метрика из JND; **дифференцируема** — годится как loss | MIT ✅ | PyTorch | Как loss в оптимизации + как оценка «насколько слышно разницу» |
| **auraloss** | https://github.com/csteinmetz1/auraloss | MR-STFT и другие спектральные лоссы | Apache-2.0 ✅ | `pip install auraloss` | Loss для подбора параметров |
| **LD / LSD** (log-spectral distance) | — | Спектральное расстояние в дБ; **нечувствительно к фазе и задержке** — годится для статической окраски (сатурация, EQ), не для времени | — | numpy: RMS по кадрам от `20·log10(abs(X)/abs(Y))` | Считать по полосам; для реверба/дилея — только как доп. сигнал |
| **FAD** (Fréchet Audio Distance) | https://github.com/gudgud96/frechet-audio-distance | Расстояние распределений: «похож ли микс на корпус референсов» | MIT ✅ | `pip install` | FAD(wet, refs) vs FAD(dry, refs); нужен корпус ≥20 треков |
| **PESQ** | https://github.com/ludlows/PESQ | PESQ (речь), narrow/wideband | MIT ✅ | `pip install pesq` | Только вокал/речь, **не** микс |
| **STOI** (pystoi) | https://github.com/mpariente/pystoi | Intelligibility | ⚠️ (обычно MIT) | `pip install pystoi` | Только вокал |
| **SI-SDR** (torchmetrics) | https://lightning.ai/docs/torchmetrics/stable/audio/signal_distortion_ratio.html | SI-SDR | Apache-2.0 ✅ | `torchmetrics.audio` | Только если есть истинный target (например, «эталонно обработанный» стем) |
| **Обзор объективных мер** (arXiv 2110.11438) | https://arxiv.org/pdf/2110.11438 | Сравнение PEAQ, ViSQOLAudio, PEMO-Q, HAAQI, 2f, PEASS и др. + границы применимости | 📄 | — | Выбор метрики под задачу, а не «первая из гугла» |
| **MEI20190207** (AI Mastering) | https://github.com/ai-mastering/mastering_comparison | Эмпирический индекс качества микса, построенный на субъективных оценках The Mix Evaluation Dataset | ⚠️ нет явной лицензии | Python-код из репо | Автор сам предупреждает о bias датасета — только как доп. сигнал |
| **The Mix Evaluation Dataset** (DAFx-17) | https://www.dafx.de/paper-archive/2017/papers/DAFx17_paper_49.pdf | Миксы, собранные в реальной практике, + субъективные оценки и параметры обработки | 📄 | — | Валидация любой «метрики качества микса» |

### 9.1 Что **не** работает (важно)

| метрика | где ломается | почему |
|---|---|---|
| **SI-SDR** на ревербе/дилее | любая задержка или фаза | метрика штрафует за сдвиг, а эффект его и вносит |
| **LD / LSD** на дилее | время | LD игнорирует фазу → «эхо» выглядит как «норма» |
| **Null-тест** (вычитание dry) | нелинейные и время-зависимые | остаток не интерпретируем: это и есть эффект |
| **PESQ / STOI** на миксе | музыка | обучены на речи |
| **FAD на одном файле** | малая выборка | оценка распределения нестабильна |
| **«Одна метрика = истина»** | всё | Goodhart: оптимизация метрики уводит от звука |

### 9.2 Reference-based: близость к референсу

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **Matchering** | https://github.com/sergree/matchering | Подгоняет RMS, АЧХ, пик и stereo width целевого трека под референс — самый прямой «reference matching» в открытом коде | GPL-3.0 ✅ | `pip install matchering` | Прогнать target+reference, сравнить АЧХ/width/LUFS до и после |
| **Music Mixing Style Transfer** (Koo et al., ICASSP 2023) | https://github.com/jhtonyKoo/music_mixing_style_transfer | Перенос стиля обработки с референса; contrastive-обучение разделяет «эффект» и «содержание» | MIT ✅ | Код + предобученные модели из репо | Сравнить выход с референс-треком по FAD/CDPAM и слепым тестом |
| **DeepAFx-ST** (Steinmetz et al., 2022) | https://github.com/adobe-research/DeepAFx-ST | Сеть предсказывает параметры дифференцируемой цепи эффектов по примеру записи | Adobe Research License (research) ✅ | Клонировать | Сравнить параметры/выход с целевой записью |
| **Целевые диапазоны по корпусу референсов** (метод) | — | Не «похоже на один трек», а попадание в распределение метрик (медиана ± IQR) по 20–50 референсам | — | Считать §2–§8 на корпусе → квантили → пороги | Проверка: наш микс внутри IQR по RT60/crest/LRA/ширине |

**Практика:** «reference matching» на одном треке даёт переобучение под жанр/аранжировку. Рабочая схема — **корпус референсов + распределение**, а один трек использовать только для A/B вслепую.

---

## 10. Автоподстройка параметров

### 10.1 Дифференцируемый путь (градиент по параметрам)

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **dasp-pytorch** | https://github.com/csteinmetz1/dasp-pytorch | Реверб, дистошн, DRC, EQ, стерео — всё дифференцируемо; батчи на GPU | Apache-2.0 ✅ | `pip install dasp-pytorch` | Восстановить известный параметр из wet (см. quickstart) |
| **DeepAFx-ST** (Steinmetz et al., 2022) | https://github.com/adobe-research/DeepAFx-ST | Перенос стиля эффектов «по примеру»: сеть предсказывает параметры дифференцируемой цепи | **Adobe Research License — только research** ✅ | Клонировать | Сравнить выход с референс-записью |
| **NablAFx** (Comunità et al., 2025) | https://github.com/mcomunita/nablafx | Фреймворк для black-box и gray-box моделирования эффектов (обучение, метрики, логи) | ⚠️ | pip из репо | Единые бенчмарки для нескольких архитектур |
| **FLAMO** | https://github.com/gdalsanto/delay-network-reverbs | Частотно-доменные дифференцируемые FDN | ⚠️ | Python | Подбор T60 по полосам |
| **DiffVox** | https://ar5iv.labs.arxiv.org/html/2504.14735 | Дифференцируемый ping-pong дилей + FDN | ⚠️ | PyTorch | Подгонка под распределение эффектов |
| **torchaudio `lfilter`** | https://docs.pytorch.org/audio/stable/generated/torchaudio.functional.lfilter.html | Дифференцируемый IIR — можно собрать EQ/кроссовер и учить коэффициенты | BSD-2-Clause ✅ | `torchaudio.functional.lfilter` | Проверить `loss.backward()` — градиент не NaN |

### 10.2 Black-box путь (без градиента, по метрике на реальном рендере)

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **Optuna** (TPE) | https://optuna.org | Байесовский поиск 3–8 параметров по скалярной метрике | MIT ✅ | `pip install optuna` | 100–300 итераций; смотреть, что лучший результат стабилен между seed'ами |
| **scipy.optimize** (Nelder-Mead/Powell) | https://docs.scipy.org/doc/scipy/reference/optimize.html | Локальная доводка после Optuna | BSD ✅ | scipy | Сравнить с глобальным поиском |
| **nevergrad** | https://github.com/facebookresearch/nevergrad | Градиентно-свободная оптимизация, много алгоритмов | MIT ✅ | pip | — |

**Схема для нашего случая (FL):** параметры → `fl_plugin_set_param` → рендер → метрика (§2–§8) → Optuna. Один рендер 8–15 с, 200 итераций ≈ 30–50 мин на эффект. Это **единственный** путь, который меряет именно наш плагин, а не его питоновский аналог.

### 10.3 LLM / агентный путь (новое, 2025–2026)

| метод/инструмент | ссылка | что даёт | лицензия | как подключить | как проверить |
|---|---|---|---|---|---|
| **LLM2Fx** (Sony, WASPAA 2025) | https://github.com/SonyResearch/LLM2Fx | Текст → параметры эффекта zero-shot; in-context стратегии (DSP-фичи, код функций, few-shot) | ⚠️ | Код + датасет на HF | Сравнить предсказание с ручной настройкой инженера |
| **LLM2Fx-Tools** (Sony, 2025) | https://arxiv.org/abs/2512.01559 | Аудио-пара (dry/wet) → последовательность вызовов эффектов с параметрами | ⚠️ | SFT на tool-sequences | Точность типа эффекта/порядка/параметров |
| **MixAssist** (COLM 2025) | https://github.com/mclemcrew/MixAssist | Audio-language датасет для co-creative ассистента микса + скрипты «LLM-as-a-judge» | MIT ✅ (код) | Датасет + скрипты | Сравнить judge-оценки с людьми |
| **FxSearcher** (KAIST, 2025) | https://arxiv.org/pdf/2511.14138 | Градиентно-свободный text-driven подбор эффекта (по описанию) | 📄 | — | — |
| **RIME / POEMS** (Dartmouth, 2026) | https://arxiv.org/html/2607.19605v1 | Агентный пост-продакшн: POEMS — тулкит со стемами, миксом и дифференцируемыми эффектами (компрессор, лимитер, waveshaping, гейт); 3000 пар «инструкция → аудио» | ⚠️ | — | Оценка агента по инструкциям |

---

## 11. Датасеты сухих/обработанных пар

| датасет | ссылка | что внутри | лицензия | как использовать |
|---|---|---|---|---|
| **ToneTwisT AFx** | https://github.com/mcomunita/tonetwist-afx-dataset | Dry/wet пары: аналог, аналог-параметрический, цифра, цифра-параметрическая; ссылки на внешние данные | MIT ✅ | Эталон для проверки моделей эффектов |
| **SignalTrain LA-2A** | https://zenodo.org/records/3824876 | 20.7 ГБ пар вход/выход LA-2A, вариации Peak Reduction | CC BY 4.0 ✅ | Обучение/валидация модели компрессора |
| **IDMT-SMT-Audio-Effects** | https://www.idmt.fraunhofer.de/en/publications/datasets/audio_effects.html | Крупная база гитары/баса с эффектами для автоматического распознавания | research, по запросу ⚠️ | Классификация/детекция эффектов |
| **Magnetic Tape Recorder Dataset** (Aalto) | https://research.aalto.fi/en/datasets/magnetic-tape-recorder-dataset/ | Пары вход/выход магнитной ленты | ⚠️ | Моделирование ленточной сатурации |
| **GuitarSet** | https://zenodo.org/records/3371780 | Сухая гитара (hex + mono mic) | CC BY 4.0 ✅ | Источник «сухого» материала |
| **Slakh2100** | http://www.slakh.com/ | 2100 синтезированных треков со стемами и MIDI — **сухие** источники | CC BY 4.0 (Zenodo; на HF-зеркале встречается NC) ⚠️ | Рендерить свои эффекты на чистых стемах |
| **MoisesDB** | https://huggingface.co/datasets/wearemusicai/moisesdb | Стемы (больше 4), разные жанры | CC BY-NC-SA 4.0 ✅ | Только research |
| **MUSDB18** | https://zenodo.org/records/1117371 | 150 треков, 4 стема | CC BY-NC-SA 4.0 / educational ✅ | Только research |
| **Cambridge-MT «Mixing Secrets»** | https://cambridge-mt.com/ms3/mtk/ | ~30 сырых мультитреков (24/44.1), без обработки | «free for mixing practice» — явной лицензии нет ⚠️ | Практика/тесты, не для коммерческого релиза |
| **OpenAIR** | https://audeering.github.io/datasets/datasets/openair.html | IR с RT60 | CC BY 4.0 ✅ | Свёрточные эталоны |
| **ODAQ** | https://github.com/Fraunhofer-IIS/ODAQ | 240 сэмплов, 26 экспертов, MUSHRA; включает стерео-обработку | permissive (CC) ✅ | Бенчмарк перцептивных метрик |
| **ReverbFX** | https://arxiv.org/html/2505.20533v2 | RIR для задачи деревербации | ⚠️ | Тесты реверба |

---

## 12. Открытые VST/плагины с исходниками (референс-реализации)

| плагин | ссылка | что даёт | лицензия | зачем нам |
|---|---|---|---|---|
| **Airwindows** | https://github.com/airwindows/airwindows | ~200 эффектов: ревербы, сатурация, компрессоры; consolidated-плагин CLAP/AU/VST3/LV2 | MIT ✅ | Читаемые алгоритмы + бесплатный A/B |
| **DragonFly Reverb** | https://github.com/michaelwillis/dragonfly-reverb | Room/Hall/Plate/Early (Freeverb3) | GPL-3.0 ✅ | Эталон реверба |
| **LSP Plugins** | https://github.com/sadko4u/lsp-plugins | Deesser, компрессоры, лимитеры, стерео-инструменты; CLAP/LV2/VST2/VST3 | GPL-3.0 ✅ | Эталон де-эссера и динамики |
| **Calf Studio Gear** | https://github.com/calf-studio-gear/calf | Компрессор, **sidechain-компрессор**, реверб, дилей, де-эссер — LV2/JACK, только Linux; проект EOL, форк [calfNXT](https://github.com/boomshop/calfnxt) (VST3) | LGPL-2.1 ✅ | В FL напрямую не вставить — читать код/эталон |
| **zam-plugins** | https://github.com/zamaudio/zam-plugins | ZamComp, ZamDelay, ZaMultiComp и др. | GPL-2.0 ✅ | Эталон компрессора |
| **x42-plugins** | https://github.com/x42/x42-plugins | Лимитер, EQ, метрики (LV2) | GPL-2.0+ ⚠️ | Эталон лимитера |
| **ChowTapeModel** | https://github.com/Chowdhury-DSP/ChowTapeModel | Лента: гистерезис, оверсэмплинг | ⚠️ | Эталон алиасинга/сатурации |
| **Surge XT** | https://github.com/surge-synthesizer/surge | Синтезатор + полноценные эффекты (реверб, дилей, хорус, дисторшн, компрессор) | GPL-3.0 ✅ | Читаемые реализации эффектов |
| **Vital** | https://github.com/mtytel/vital | Синтезатор + эффекты | GPL-3.0 ✅ | То же |
| **JUCE** | https://github.com/juce-framework/JUCE | Фреймворк + `juce::dsp` (Reverb, Compressor, Chorus, Phaser, DelayLine, WaveShaper) | GPL-3.0 / коммерческая ✅ | Если делать свой плагин |
| **DPF** | https://github.com/DISTRHO/DPF | Лёгкий фреймворк плагинов | ISC ✅ | Альтернатива JUCE |
| **iPlug2** | https://github.com/iPlug2/iPlug2 | Фреймворк плагинов | ⚠️ | Альтернатива JUCE |

---

## 13. Как проверять на практике

| инструмент | ссылка | что даёт | лицензия | как использовать |
|---|---|---|---|---|
| **webMUSHRA** | https://github.com/audiolabs/webmushra | MUSHRA-совместимые веб-тесты (YAML-конфиг) | ⚠️ | Слепые тесты «dry / A / B / ref» |
| **Netflix listening-test-app** | https://github.com/Netflix-Skunkworks/listening-test-app | Приложение для MUSHRA (на нём делали ODAQ) | GPL-3.0 ✅ | Тот же протокол, что в ODAQ |
| **abxpy** | https://github.com/bootphon/abxpy | ABX-тесты в Python | MIT ✅ | Быстрая проверка «слышно ли разницу» |
| **ITU-R BS.1534** | https://www.itu.int/rec/R-REC-BS.1534 | Стандарт MUSHRA | стандарт | Формальная процедура |
| **ITU-R BS.1770 / EBU R128** | https://www.itu.int/rec/R-REC-BS.1770 | LUFS, true peak, LRA | стандарт | Громкость и пики |
| **AES17** | https://www.aes.org/standards/ | THD+N, уровни, полосы | стандарт | Метрология нелинейности |
| **ISO 3382-1** | https://www.iso.org/standard/40979.html | Определения EDT/T30/C50/D50/Ts/IACC | стандарт | Словарь метрик реверба |

### 13.1 Набор пробников (предлагаемая спецификация)

| файл | сигнал | для чего |
|---|---|---|
| `impulse.wav` | 1 сэмпл на −6 dBFS, 48 кГц | IR для LTI-цепей |
| `sweep.wav` | exp-sweep 20 Гц–20 кГц, 10 с (Farina) | IR + АЧХ + THD |
| `sine_levels.wav` | 1 кГц, −60…0 dBFS шаг 3 дБ, по 1 с | статическая кривая, THD vs level |
| `imd_ccif.wav` | 19 + 20 кГц, равные уровни | IMD (CCIF) |
| `imd_smpte.wav` | 60 Гц + 7 кГц, 4:1 | IMD (SMPTE) |
| `burst.wav` | 1 кГц, 50 мс burst + 500 мс тишины | attack/release |
| `kick_pad.wav` | kick + pad, 4 такта | сайдчейн: глубина/тайминг |
| `sibilance.wav` | вокал с «с/ш» или синтетический HF-шум 5–9 кГц | де-эссер |
| `ms_probe.wav` | L=R (моно), затем L/R декоррелированы | ширина/моносовместимость |
| `dc_step.wav` | ступенька DC | DC-блок, клиппинг |

### 13.2 Правила, без которых всё разваливается

1. **Один эффект — один пробник — одна метрика.** Не мерить «микс целиком» после каждого поворота ручки.
2. **Сначала IR, потом всё остальное.** IR даёт базу (АЧХ, RT60), без неё остальные числа не интерпретируются.
3. **Жёсткие ограничения важнее оптимума:** true peak ≤ −1 dBTP, моно-потеря ≤ 1 LU, THD не растёт выше −40 dB на рабочем уровне. Только потом — «нравится/не нравится».
4. **Сравнение с референсом, а не с абсолютом.** Абсолютных «правильных» RT60/THD нет — есть распределение по корпусу референсов.
5. **Слепой тест обязателен** для всего, что меняет больше 0.5 дБ в полосе: ABX на 10–15 прогонах.
6. **Перепроверка после правки:** применил → перемерил → сравнил с предсказанием. Если метрика не сдвинулась — правка не сработала (или не туда).

---

## 14. Чего не нашлось (честно)

| запрос | результат |
|---|---|
| **Ganymed** (как автоматический микшер) | Не существует. В вебе «Ganymed» — бесплатный 3-operator FM-синтезатор (VST4YOU, 2016), а также куча посторонних проектов (SSH-библиотека, GEOS-5 модуль, Goa-транс пресеты). Автоматического микшера/плагина с таким именем нет. |
| **«Synthesizer of mix»** | Прямого инструмента с таким названием нет. Ближайшее по смыслу: генеративные микшеры (§10.3, RIME/POEMS; «Automatic Music Mixing using a Generative Model of Effect Embeddings», arXiv 2511.08040) и style-transfer (DeepAFx-ST, Music Mixing Style Transfer). |
| **Единая «метрика качества микса»** | Существует только эмпирическая MEI20190207 (§9) с известным bias; универсальной нет — это открытая проблема. |
| **Готовая библиотека «THD/IMD за один вызов»** | Не найдено. Считается FFT-методом в 30–50 строк; стандарты (AES17) задают определения. |
| **Де-эссер в Faust-библиотеках** | В `faustlibraries` де-эссера нет — собирается из band-split + `compressors.lib`. |

---

## 15. Что внедрять: приоритеты

| приоритет | что | зачем | трудозатраты |
|---|---|---|---|
| **P0** | IR-пробник + `measure_rt60` + EDC/EDT + C50 | Объективный реверб и дилей; база для всего остального | 1 день |
| **P0** | Статическая кривая + burst + огибающая | Компрессор, сайдчейн, де-эссер, лимитер | 1 день |
| **P1** | THD/IMD/алиасинг по уровням | Сатурация: «сколько грязи» и «какого порядка» | 0.5 дня |
| **P1** | M/S + корреляция + моно-сумма | Стерео: ширина **и** совместимость | 0.5 дня |
| **P2** | ViSQOL / CDPAM / auraloss-MR-STFT | «Стало лучше» относительно референса | 2–3 дня |
| **P2** | Optuna по 3–5 параметрам на реальном рендере FL | Автоподстройка без обучения моделей | 3–5 дней |
| **P3** | dasp-pytorch / FLAMO / DiffVox | Подбор параметров градиентом, перенос стиля | 2–4 недели |

---

## 16. Резюме

Готового «объективного оценщика микса» нет — это открытая проблема. Но по каждому эффекту есть готовый измерительный аппарат, и его надо применять по классам: LTI (реверб, дилей) — снимать импульсный отклик и считать RT60/EDT/C50/плотность эха (pyroomacoustics, blind_rt60, EchoDensity, De Man 2017); нелинейность — THD/IMD/алиасинг по уровням (AES17, dasp-pytorch); время-зависимые (компрессор, сайдчейн, де-эссер) — статическая кривая + переходная характеристика + огибающая. Для «стало лучше» есть ViSQOL (Apache-2.0), CDPAM (MIT, дифференцируема), auraloss, FAD и эмпирический MEI с оговоркой о bias. Автоподстройка реальна двумя путями: дифференцируемые модели (dasp-pytorch, FLAMO, DiffVox, DeepAFx-ST — последний только research) и black-box Optuna по метрике на реальном рендере FL — второй точнее для наших закрытых плагинов. Датасеты пар: ToneTwisT (MIT), SignalTrain LA-2A (CC BY 4.0), IDMT-SMT-Audio-Effects, Slakh2100. Открытые VST: Airwindows (MIT), DragonFly, LSP (GPL-3.0). «Ganymed» — FM-синтезатор 2016 г., не микшер. Главный риск — Goodhart: оптимизация одной метрики уводит от звука, поэтому нужны жёсткие ограничения (моносовместимость, true peak, THD) плюс слепой ABX.

---

*Файл: `C:\Users\epicc\mcp\flstudio-mcp\_analysis\RESEARCH-EFFECTS-WEB.md`*
