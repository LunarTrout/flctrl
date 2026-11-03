# flctrl — measurement-first mixing for FL Studio

`flctrl` is a Python library that lets a machine *hear* an FL Studio project the way an
engineer does: it captures the audio of every mixer track **live**, turns it into a dense
but readable description (a "tape"), catches short events that a 100 ms window would
smear, and compares any two pictures on the same axes.

It exists because of one stubborn constraint: **reference tracks arrive as a single stereo
file, there are no stems, and there never will be.** So this library never tries to split a
reference. It measures the reference in exactly the same units as your own mix and solves
the inverse problem instead: which gains, EQ moves and pan positions make *your* tracks sum
into the reference's numbers.

---

## Contents

- [What it solves](#what-it-solves)
- [Three ideas](#three-ideas)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Live capture](#live-capture)
- [The tape](#the-tape)
- [Events](#events)
- [Save points](#save-points)
- [API surface](#api-surface)
- [Repository layout](#repository-layout)
- [Limits](#limits)
- [Documentation](#documentation)
- [Status](#status)

---

## What it solves

Mixing by ear against a reference is slow and unrepeatable. Mixing by numbers usually
fails for another reason: the numbers describe the *master*, while the reference has no
tracks at all. `flctrl` closes the gap from both sides.

| Side | What we have | What we do |
|---|---|---|
| Your project | 29 mixer tracks, every knob reachable | capture each track live, measure it, keep the numbers |
| Reference | one stereo file, no stems | measure it with the same code, same bands, same time grid |
| Comparison | different sources | put both in one cube (`time × track/role × band × metric`) and subtract |

Everything else — EQ moves, pan plans, the inverse solver — consumes those numbers instead
of guessing.

## Three ideas

**The cube.** Every measurement has the same shape: `time × track (or role) × band ×
metric`. A frame is a 2-D slice with named axes, units and explicit coverage
(`frames.make_frame`). Missing cells are `None`, never zero, so comparison is a plain
subtraction rather than a special case.

**The tape.** For each track, for each 0.1 s cell, a full feature vector: 28 third-octave
bands, 24 ERB bands, loudness, timbre descriptors, pitch, harmony, stereo, dynamics. About
1 KB per cell — three orders of magnitude smaller than the audio it describes (14.5 KB per
second per track versus ~350 KB of 44.1 kHz stereo float).

**Events.** A 100 ms cell cannot represent a 20 ms blast-beat hit. A separate 2 ms pass
emits short events — onsets, level jumps, clipping — each with peak, duration, crest, band
and centroid. Measured: **39 of 40 hits detected at 300 BPM 32nd notes (25 ms apart)**.

---

## Requirements

- Windows 10/11 (live capture relies on FL's Disk Recording and Windows registry specifics)
- FL Studio 2025 (tested on 25.2.5 build 5319, Producer Edition)
- Python 3.12 for the live path: `frida`, `numpy`, `scipy`, `soundfile`, `pytest`
- The FL controller bridge on `127.0.0.1:9787` (see the companion MCP repository)
- FL's built-in **Disk Recording** — no plugins, no manual project setup

Offline work (files, references, frames) needs only `numpy` + `scipy` + `soundfile`.

## Quick start

```python
import sys
sys.path.insert(0, r"C:/path/to/flstudio-lib/flctrl")

from flctrl import api, live

# Capture every mixer track for 2 seconds starting at bar 8
res = api.call("tracks.live", {"seconds": 2.0, "start": 8.0})

for tid, tr in res["tracks"].items():
    print(tid, tr["name"], tr["role"], tr["rms"], tr["peak"],
          tr["transients"]["onsets_s"])

print(res["masking"]["pairs"][:5])     # who covers whom
print(res["provenance"][:3])           # what is playing in the project, every 0.5 s

# The audio itself is available on explicit request, never as a side effect
files = api.call("tracks.dump", {"tracks": [1, 12], "seconds": 5.0,
                                 "outdir": r"C:/tmp/stems", "fmt": "mp3"})["files"]
```

```python
# Any file goes through the same code: a reference, a render, a bounced stem
import soundfile as sf
from flctrl import timbre, events

x, fs = sf.read("reference.wav", always_2d=True)
tape = timbre.analyze(x, fs)   # cells + LUFS + LRA + SII + harmony + checks
ev = events.detect(x, fs)      # the 2 ms layer
print(tape["lufs"], tape["lra"], tape["sii"], ev["count"])
```

## Live capture

Two implementations, identical output.

**`files` (default, recommended).** Save the project with Ctrl+S and copy the `.flp` as a
restore point, arm the tracks, start recording, play from `start`, read the WAVs FL wrote,
compute the tape, delete them, disarm, restore the previous mode. The `Recorded` folder is
empty afterwards (verified). FL stays alive and the project is unchanged apart from
armed-state, which is restored.

**`memory` (zero disk).** A Frida hook on `CreateFileW`/`WriteFile` recognises the record
files, copies the PCM into memory and zeroes the write length, so FL believes it wrote
while only a 76-byte header reaches disk (removed by `live.clean()`). The hook lives in a
long-running daemon (`tools-local/hook_daemon.py`, port 9788) so it is attached once per
session. This is the purest path, and it is the one that occasionally destabilised FL
during development — hence the default.

Both modes share: no project reload, no modal dialogs (FL's "Recording" popup is
suppressed via `PopupRec`), and a save point before every capture.

Details: [docs/LIVE.md](docs/LIVE.md) · [docs/LIVE.ru.md](docs/LIVE.ru.md)

## The tape

Cell = 0.1 s, window 4096 samples (~93 ms). Fields per cell:

| Group | Fields |
|---|---|
| Loudness | `rms_db`, `peak`, `crest_db`, `lufs_st` (BS.1770, 400/100 ms blocks) |
| Dynamics | `attack_s`, `decay_db`, `flux_db`, `zcr`, `hits` |
| Spectrum | `bands` (28 third-octave), `erb` (24), `centroid_hz`, `spread_hz`, `flatness`, `rolloff_hz` |
| Tone | `f0_hz`, `note`, `hnr_db`, `chroma` (12), `harm` (2f0…6f0), `inharm`, `formants` |
| Bands | `low_db`, `mid_db`, `high_db`, `decay_bands` (dB/s), `wall` |
| Stereo | `corr`, `width`, `pan`, `coherence` (L/R per band) |

Per track: `lufs`, `lra`, `sii`, `harmony`, `checks` (DC, clipped samples, mono
correlation), `transients`, `mod_rate_hz`, `mod_depth`, `jitter_pct`, `technique`.
Per capture: `masking`, `provenance`, `position`.

Measured sizes: cell ~1.0 KB; one track one second 14.5 KB; 29 tracks × 10 s ≈ 4.1 MB
(versus 97.6 MB of raw PCM).

Full reference: [docs/TAPE.md](docs/TAPE.md) · [docs/TAPE.ru.md](docs/TAPE.ru.md)

## Events

`events.detect(stereo, fs)` uses a 4 ms envelope with a 2 ms hop and emits `onset`
(level rise ≥5 dB after a dip, 12 ms refractory), `spike` (+15 dB within 20 ms) and
`clip` (samples at 0 dBFS). Each onset carries peak, duration, crest, dominant band, band
levels and centroid.

| Signal | Spacing | Detected | Expected |
|---|---|---|---|
| 300 BPM 32nds | 25 ms | 39 | 40 |
| Tremolo 20 Hz | 50 ms | 19 | 20 |
| Tremolo 12 Hz | 83 ms | 11 | 12 |

## Save points

`live.save_project()` brings FL to the foreground, sends Ctrl+S, waits for the file's mtime
to change, and returns the path. `live.backup()` copies it into `~/Backups/flctrl/`,
skipping identical states (SHA-1) and pruning to 20 points. If the save cannot be made, it
falls back to FL's autosave and reports `origin: "autosave"`.

Restore = open the snapshot. FL's undo history lives in memory and dies with the process;
these points survive a crash.

## API surface

`flctrl.api.call(op, args)` is the single entry point; the MCP server wraps it in ten
tools. Full table: [docs/API.md](docs/API.md) · [docs/API.ru.md](docs/API.ru.md).

| Op | Purpose |
|---|---|
| `tracks.live` | capture + tape + events + masking + provenance |
| `tracks.dump` | explicit WAV/MP3 export of tracks |
| `measure` | metrics of a frame, a file or the live stream |
| `compare` | delta or corridor between two frames |
| `clip.at` | what is sounding at a given second or bar |
| `pan.plan`, `pan.move` | plan and perform pan moves |
| `eq.set`, `plugin.insert/delete/replace` | live processing changes |
| `stream.run` | streaming metrics while playing |
| `inverse.solve` | reference targets → gains for our tracks |
| `system.health`, `system.doctor` | bridge and library sanity |

## Repository layout

```
flctrl/          the library
  live.py        live capture, daemon control, save points, tape assembly
  timbre.py      feature set: bands, pitch, harmony, loudness, stereo, checks
  events.py      2 ms event layer
  incr.py        incremental meters (RMS, bands, LUFS, stereo, transients)
  frames.py      the cube: frames, comparison, corridors
  api.py         operations surface
  fl_lib.py      low-level FL access (bridge, project, mixer, plugins)
  roles.py       track → role mapping
  apply.py       applying a plan to live tracks
  pan.py, panplan.py, invmix.py, decide.py, balance.py, …
docs/            specification and documentation (EN + RU)
tests/           unit and live tests
tools/           test runner and audits
```

## Limits

- **Time resolution of the tape** is 100 ms by design; short events are the event layer's
  job, not the cells'.
- **Frequency resolution** is 28 third-octave or 24 ERB bands; finer costs size.
- **Pitch** needs ≥2 periods — 40 ms at 50 Hz. Faster than that, `f0` is unreliable by
  construction.
- **Loudness** uses 400 ms blocks with 100 ms steps, so `lufs_st` is short-term, not
  instantaneous.
- **Live capture requires record mode** for the duration of the take: a snapshot, not a
  continuous monitor.
- **`memory` mode** is zero-disk but was less stable than `files` on FL 25.2.5.

## Documentation

| Document | EN | RU |
|---|---|---|
| The tape, field by field | [docs/TAPE.md](docs/TAPE.md) | [docs/TAPE.ru.md](docs/TAPE.ru.md) |
| Live capture internals | [docs/LIVE.md](docs/LIVE.md) | [docs/LIVE.ru.md](docs/LIVE.ru.md) |
| Mixing against one reference | [docs/REFERENCE.md](docs/REFERENCE.md) | [docs/REFERENCE.ru.md](docs/REFERENCE.ru.md) |
| API operations | [docs/API.md](docs/API.md) | [docs/API.ru.md](docs/API.ru.md) |
| Operational rules learned the hard way | [docs/RULES.en.md](docs/RULES.en.md) | [docs/RULES.md](docs/RULES.md) |
| Design specification | — | [docs/SPEC.md](docs/SPEC.md) |
| Status | — | [docs/STATUS.md](docs/STATUS.md) |

## Status

Verified on a real project (29 mixer tracks, FL 25.2.5): live per-track capture in both
modes; the full tape; events up to 40 per second; masking, provenance, roles, save points;
and a correlation proof that captured tracks are not copies of the master (GTR L1 ↔ GTR R1:
0.046; the maximum pair, 0.614, is a bus and its source).

Next: the reference frame builder (`reference.frame(path)`) and the closed
measure → solve → apply → measure loop described in
[docs/REFERENCE.md](docs/REFERENCE.md).

## License

Private research project. See the repository owner for terms.
