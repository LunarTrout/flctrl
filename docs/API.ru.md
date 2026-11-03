# Операции API

Единственная точка входа: `flctrl.api.call(op, args) -> dict`. MCP-сервер выставляет ту же
поверхность десятью инструментами (`fl_help`, `fl_list`, `fl_get`, `fl_set`, `fl_call`,
`fl_measure`, `fl_compare`, `fl_undo`, `fl_dry_run`, `fl_project`).

Операция либо возвращает словарь, либо поднимает `ApiError(code, op, …)` с
машиночитаемым кодом.

---

## Захват и измерения

| Операция | Аргументы | Что возвращает |
|---|---|---|
| `tracks.live` | `tracks?`, `seconds?=2`, `start?` | `tracks` (на дорожку: имя, роль, rms, пик, лента, транзиенты), `frames`, `masking`, `provenance`, `position`, `backup`, `cleaned` |
| `tracks.dump` | `tracks?`, `seconds?=5`, `start?`, `outdir?`, `fmt?="wav"` | пути записанных файлов |
| `measure` | `spec` или `path`, опционально `a`,`b` (секунды) | метрики кадра, файла или живого источника |
| `compare` | `a`, `b`, `mode?="delta"`, `top?` | дельта или коридор между кадрами |
| `clip.at` | `seconds` или `bar` | что звучит: `active`, `bar`, `tempo` |
| `stream.run` | `seconds?`, `tracks?`, `rate_hz?`\|`"max"`, `ring_s?`, `rows?` | поток метрик во время игры |
| `tap.levels` | `tracks?`, `seconds?=1`, `rate_hz?` | живой RMS/пик по дорожкам (метры, не звук) |
| `tap.record` | `tracks?`, `seconds`, `outdir?` | WAV по дорожкам из живого потока |
| `tap.mix` | `tracks`, `seconds`, `path`, `fmt?` | несколько дорожек сняты и сложены в один файл |

## Решения по миксу

| Операция | Аргументы | Что возвращает |
|---|---|---|
| `inverse.solve` | `stems`, `against`, `cap_db?` | гейны, подводящие сумму к цели |
| `pan.plan` | `stems`, `a`, `b`, `section_s?` | план панорамы по секциям |
| `pan.check` | `plan` | проверка шага и зеркальности |
| `pan.move` | `track`, `points`, `seconds`, `rate_hz?` | выполняет движение вживую, отдаёт статистику |
| `mix.report` | `frame` или `path` | читаемый отчёт |
| `mix.decide` | `before`, `after`, `goals` | принять/отклонить с причинами |
| `mix.simulate` | `stems`, `moves` | предсказанный баланс без применения |

## Обработка и структура

| Операция | Аргументы | Что возвращает |
|---|---|---|
| `eq.set` | `track`, `slot`, `gains` | ставит полосы EQ на живой плагин |
| `plugin.insert` | `track`, `slot`, `plugin` | описание вставленного плагина |
| `plugin.delete` | `track`, `slot` | состояние пустого слота |
| `plugin.replace` | `track`, `slot`, `plugin` | описание нового плагина |
| `plan.apply` | `plan` | применяет многошаговый план с откатом |
| `track.add` / `track.delete` | index? | новое число дорожек |
| `transport.play` / `transport.stop` | — | состояние транспорта |
| `record.start` / `record.stop` | — | состояние записи |

## Диагностика

| Операция | Аргументы | Что возвращает |
|---|---|---|
| `system.health` | — | состояние моста и FL |
| `system.doctor` | — | чеклист с прохождением по каждой возможности |

---

## Пути

`fl_get(path)` / `fl_set(path, value)` адресуют проект напрямую:

```
/track/12/volume          /track/12/pan
/track/12/mute            /transport
/mixer/track/8/name       /channel/3/volume
```

Поддерживаются шаблоны: `/track/*/volume`. Структурные ошибки возвращаются как
`{ok:false, code:"range", path, allowed}` — никогда не подрезаются молча.

## Кадры

`measure` возвращает кадр:

```json
{
  "id": "bands-123456",
  "metric": "bands",
  "unit": "дБ",
  "axes": {"time": [0.0, 0.1], "band": [25.0, 31.5]},
  "values": [[-68.0, -66.1], [-67.4, -65.9]],
  "coverage": {"measured": ["DRUMS SD3"], "missing": []}
}
```

`compare(a, b)` вычитает кадры по пересечению осей и сообщает несовпавшие оси в
`coverage.unmatched`. Пропуски остаются `None`.

## Ошибки

| Код | Значение |
|---|---|
| `bad_args` | не хватает аргументов или они противоречат друг другу (`need` перечисляет нужное) |
| `unknown_op` | операция не зарегистрирована |
| `range` | путь есть, значение вне диапазона |
| `busy` | другая эксклюзивная операция держит FL |
| `fl_not_running` | мост или сама FL недоступна |
