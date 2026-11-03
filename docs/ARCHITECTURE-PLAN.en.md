# flctrl architecture: the rebuild plan

**English** · [Русский](ARCHITECTURE-PLAN.md)

The document records what is in the way today, and what the library should become so
that our tasks can be solved with it conveniently and reliably — and so that it stays
general-purpose rather than "for one track".

## 1. What the audit showed

**Two copies of the library.** In the repository `flstudio-lib/flctrl` there are 35
modules, and that is the canon (fresh: 1334 lines in `fl_lib.py`). In the working folder
`flstudio-mcp/tools-local` sit **27 modules with the same names**, of which
`fl_lib.py` has diverged by 435 lines and `hear_cli.py` by 72. Any edit
risks going to the wrong file, and already has.

**237 one-off scripts** in `tools-local`. Half of them are things that should
be library methods (`_body.py`, `_vox_fix.py`, `_drums_clean.py`…).

**Two environments.** Live control lives in 3.12, hearing in 3.13, because
`phonometry` exists only there. The subprocess bridge works, but it is smeared across the
code: every method decides for itself where to go.

**Inconsistent API.** `measure(path, a, b)` takes the bounds positionally,
`masking(stems, mix_path)` — a mandatory mix, `attribute` — a dict of stems.
The same meaning of "segment" is expressed in three ways.

**No session object.** Every call is self-contained: project state,
the selected section, the edit history and rollback live in the caller's head.

**The decision layer is almost empty.** There is `judge` (accept/reject two measurements) and
`predict` (linear prediction), but not the thing that turns a deviation
from the targets into a concrete edit plan.

## 2. Target structure

One package, layers bottom-up; every layer knows only about the one below.

```
flctrl/
  core/            access to FL and the life of the project
    link.py        bridge, port, health, auto-start
    state.py       tracks, channels, plugins, routes, playlist
    act.py         writing parameters, volumes, pan, routes
    create.py      render, live insert, tracks, reload
    safe.py        snapshot, rollback, backups
  ear/             measurement. Pure functions, no FL
    bands.py       bands, calibration, spectra
    loudness.py    N, N'(z), sharpness, LUFS, crest
    partial.py     LOO, audibility A, sensitivity dN/dB
    masking.py     masking matrix, unmask
    psycho.py      roughness, tonality
    sections.py    auto-sections, active windows
    stereo.py      correlation, width, loss in mono
  model/           corpus and targets. JSON in and out
    roles.py       instrument roles
    targets.py     distributions by role and axis
    balance.py     project vector, solve with a cap, check
    objective.py   distance to the targets, worst places
  decide/          rules
    advice.py      deviation → concrete edits (gain / EQ / pan)
    verify.py      acceptance: significance, target, damage
    journal.py     journal of decisions and results
  session.py       MixSession: project, section, history, rollback
  recipes/         genre profiles as data (black_metal.json, rock.json)
  mcp/server.py    the surface for models
```

**Principles that solve the current problems.**

1. **One canon.** Code lives only in the repository. `tools-local` is for
   experiments, and files named after library modules are forbidden in it.
2. **Layers do not leak.** `ear` knows nothing about FL; `model` knows nothing about
   subprocesses; `decide` does not write to FL; `core` does not compute audio.
3. **One environment adapter.** Everything that requires `phonometry` is called
   only through `ear/interp.py`. The rest of the code runs on any Python 3.10+.
4. **One way to specify a segment.** Everywhere `section=(a, b)` — one object,
   `None` means "the whole file". No positional pairs.
5. **Everything that is computed returns a dict with numbers and units**, not a
   bare array: the result must be usable for the journal and for MCP.
6. **Generality through roles and the corpus.** A project is described by roles;
   the genre by a recipe file; nothing is wired to a specific track.

## 3. MixSession — what is missing today

```python
s = fl.session.open("song.flp")        # or a live project
s.section = (120, 150)                 # the working window
s.baseline()                           # the "before" measurement, a snapshot
plan = s.plan(refs="black_metal")      # corpus targets → edits
s.apply(plan[0])                       # one edit
v = s.verify()                         # accept / reject / not enough data
s.log                                  # history: what, why, what came of it
s.rollback()                           # roll back the last edit
```

Session rules without which it is dangerous:
one edit per step; a snapshot before the first; automatic rollback on `reject`;
every step written to the journal; no closing someone else's project (already in `safe`).

## 4. Migration (in steps, each with a check)

| step | what we do | how to check |
|---|---|---|
| 1 | Freeze `tools-local`: delete the copies of library modules, keep only the scripts | `comm -12` on the names comes back empty |
| 2 | Move measurement into `ear/` with a single `section=` | smoke tests 17/17, `measure`/`bands`/`masking` give the same numbers on the same files |
| 3 | Move the corpus and targets into `model/` | `balance.build` on the corpus gives the same medians as `partial_corpus.json` |
| 4 | Add `decide/advice.py` | on synthetic material: a +3 dB deficit → advice +3±0.7; a 3 kHz cut → advice on pres, not the fader |
| 5 | Introduce `session.py` | the "edit → measurement → accept/reject → rollback" scenario passes on `small.flp` |
| 6 | Move MCP onto the new layers, keeping the old names | `--selftest` shows the same number of tools |

## 5. What this plan does NOT do

- It does not promise a "perfect mix at the push of a button": the targets are a corpus
  distribution, and the last word still belongs to the listener.
- It does not drag in trained models: we have no dataset, and a black box over a real
  render gives a verifiable result without training.
- It does not break backwards compatibility: the old method names remain
  wrappers until the new ones cover them completely.
