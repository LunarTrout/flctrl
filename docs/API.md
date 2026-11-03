# API operations

**English** · [Русский](API.ru.md)

Single entry point: `flctrl.api.call(op, args) -> dict`. The MCP server exposes the same
surface through ten tools (`fl_help`, `fl_list`, `fl_get`, `fl_set`, `fl_call`, `fl_measure`,
`fl_compare`, `fl_undo`, `fl_dry_run`, `fl_project`).

An operation either returns a plain dict or raises `ApiError(code, op, …)` with a
machine-readable code.

---

## Capture and measurement

| Op | Args | Returns |
|---|---|---|
| `tracks.live` | `tracks?`, `seconds?=2`, `start?`, | `tracks` (per track: name, role, rms, peak, tape, transients), `frames`, `masking`, `provenance`, `position`, `backup`, `cleaned` |
| `tracks.dump` | `tracks?`, `seconds?=5`, `start?`, `outdir?`, `fmt?="wav"` | written file paths |
| `measure` | `spec` or `path`, optional `a`,`b` (seconds) | metrics of a frame, file or live source |
| `compare` | `a`, `b`, `mode?="delta"`, `top?` | delta or corridor between two frames |
| `clip.at` | `seconds` or `bar` | what is sounding: `active`, `bar`, `tempo` |
| `stream.run` | `seconds?`, `tracks?`, `rate_hz?`\|`"max"`, `ring_s?`, `rows?` | streaming metrics while playing |
| `tap.levels` | `tracks?`, `seconds?=1`, `rate_hz?` | live RMS/peak per track (meters, not audio) |
| `tap.record` | `tracks?`, `seconds`, `outdir?` | per-track WAVs written from the live stream |
| `tap.mix` | `tracks`, `seconds`, `path`, `fmt?` | several tracks captured and summed into one file |

## Mixing decisions

| Op | Args | Returns |
|---|---|---|
| `inverse.solve` | `stems`, `against`, `cap_db?` | gains that bring the sum towards the target |
| `pan.plan` | `stems`, `a`, `b`, `section_s?` | pan plan by section |
| `pan.check` | `plan` | step and mirroring validation |
| `pan.move` | `track`, `points`, `seconds`, `rate_hz?` | performs the move live, returns stats |
| `mix.report` | `frame` or `path` | human-readable report |
| `mix.decide` | `before`, `after`, `goals` | accept/reject with reasons |
| `mix.simulate` | `stems`, `moves` | predicted balance without applying |

## Processing and structure

| Op | Args | Returns |
|---|---|---|
| `eq.set` | `track`, `slot`, `gains` | sets EQ bands on a live plugin |
| `plugin.insert` | `track`, `slot`, `plugin` | inserted plugin description |
| `plugin.delete` | `track`, `slot` | empty slot state |
| `plugin.replace` | `track`, `slot`, `plugin` | new plugin description |
| `plan.apply` | `plan` | applies a multi-step plan with rollback |
| `track.add` / `track.delete` | index? | new track count |
| `transport.play` / `transport.stop` | — | transport state |
| `record.start` / `record.stop` | — | record state |

## Diagnostics

| Op | Args | Returns |
|---|---|---|
| `system.health` | — | bridge state, FL state |
| `system.doctor` | — | checklist with pass/fail per capability |

---

## Live capture result

`tracks.live` / `live.capture(...)` returns:

| Field | Meaning |
|---|---|
| `tracks` | per track: `name`, `role`, `rms`, `peak`, `silent`, `tape`, `transients` |
| `mode_used` | `memory` (stream, default) or `files` (fallback when the hook daemon is down) |
| `clips` | `{before, during, after}` — `AutoCreateClip` around the take |
| `take_discarded` | `1` when the recording step was rolled back, `-1` if the rollback could not be verified |
| `clips_clean` | the playlist is guaranteed untouched (flag restored and the take dropped) |
| `undo` | undo hint before/after (`general.getUndoLevelHint`) |
| `project_restored` | only with `discard_take=False`: what the transaction did |
| `masking`, `provenance`, `position` | cross-track layers, see [TAPE.md](TAPE.md) |

`start` is a **fraction of the project length** (0…1), the same unit `transport.setSongPos`
takes; `seconds` is the length of the take. Track indices come from the project
(`mixer.getTrackCount()`), nothing is hard-coded.

## Paths

`fl_get(path)` / `fl_set(path, value)` address the project directly:

```
/track/12/volume          /track/12/pan
/track/12/mute            /transport
/mixer/track/8/name       /channel/3/volume
```

Wildcards are supported: `/track/*/volume`. Structural errors are reported as
`{ok:false, code:"range", path, allowed}` — never silently clamped.

## Frames

`measure` returns a frame:

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

`compare(a, b)` subtracts frames on the intersection of their axes and reports unmatched
axes in `coverage.unmatched`. Missing cells stay `None`.

## Errors

| Code | Meaning |
|---|---|
| `bad_args` | missing or contradictory arguments (`need` lists what is required) |
| `unknown_op` | operation not registered |
| `range` | a path exists but the value is out of range |
| `busy` | another exclusive operation holds FL |
| `fl_not_running` | the bridge or FL itself is unavailable |
