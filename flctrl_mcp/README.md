# flctrl-mcp

MCP-сервер поверх библиотеки [flctrl](../README.md). Один вход ко всему:
видеть проект, мерить звук, править параметры, создавать плагины вживую.

## Что важно

Сервер **ничего не знает про конкретную машину**. Пути к окружениям, FL и
посреднику ищутся сами (переменная окружения → соседние папки → реестр →
стандартные места). Если чего-то нет — скажет, чего именно.

Хост-интерпретатор — тот, где есть `frida` и `pyflp` (обычно 3.12).
Замеры он вызывает через `hear_cli.py` в окружении с `phonometry` (обычно 3.13).

## Проверить без харнесса

```bash
python flctrl_mcp/server.py --selftest
```

Печатает список инструментов и найденные окружения. Ничего не запускает.

## Подключение

```json
{
  "mcpServers": {
    "flctrl": {
      "command": "<путь к python с fastmcp/frida>",
      "args": ["<путь к репозиторию>/flctrl_mcp/server.py"]
    }
  }
}
```

Переопределить найденное можно переменными:

| Переменная | Что задаёт |
|---|---|
| `FLCTRL_PY_HEAR` | python для замеров (с phonometry) |
| `FLCTRL_DAEMON` | путь к `fl-studio-mcp-daemon` |
| `FLCTRL_FL_EXE` | путь к `FL64.exe` |
| `FLCTRL_FLP` | проект по умолчанию |

## Инструменты

**Связь и чтение:** `fl_health`, `fl_link_ensure`, `fl_tracks`, `fl_channels`,
`fl_plugins`, `fl_playlist`, `fl_clip_files`, `fl_automation`.

**Слух:** `fl_measure`, `fl_bands`, `fl_profile`, `fl_sections`, `fl_masking`,
`fl_describe`, `fl_style_sim`.

**Правка:** `fl_set_volume`, `fl_set_pan`, `fl_mute`, `fl_solo`, `fl_params`,
`fl_set_param`.

**Создание:** `fl_live_start`, `fl_live_stop`, `fl_live_slots`, `fl_live_insert`,
`fl_live_delete`, `fl_live_replace`, `fl_plugin_file`, `fl_reload`.

**Проверка решений:** `fl_judge`, `fl_predict_change`.

## Что сервер делает сам

* если посредник до FL умер — поднимает его (`fl.link.ensure`);
* перед работой полезно спросить `fl_health`: он скажет, запущена ли FL и
  отвечает ли контроллер, а не оставит с невнятной ошибкой;
* никогда не закрывает FL с пользовательским проектом: `safe_fl` бросает
  исключение вместо убийства процесса.
