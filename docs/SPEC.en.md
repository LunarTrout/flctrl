# SPEC — the library's single spec

**English** · [Русский](SPEC.md)

One living document. Everything else is history: `docs/ARCHIVE.md` (the index,
newest on top) and `docs/archive/` (34 earlier documents + previous spec
versions).

---

## 0. Goal

> **A hearing system.** For any spot in the mix — numbers: what's playing, what it
> is built from, how audible it is, who masks whom, what the processing does, how
> far it sits from the genre, and what happens if you change it.

Not an archive, not a render, not a player. The system's product is **numbers and
conclusions**, not files.

## 1. Requirement

> Get **every track** out of the mixer live and understand what each one is made
> of: which **clips and MIDI**, and **at which point in their own time**.

Expanded: for any track at any moment — **know the make-up** (clips, notes, tails,
the source's local time), **know the processing** (plugins, automation), **know the
perception** (partial loudness, masking, transient, stereo) and **be able to
change it** with a predictable result and a check by number.

| part of the requirement | what it means technically |
|---|---|
| "every track" | every mixer track, master included, no subsets |
| "what they're made of" | sources per track: audio clips, MIDI notes, effect tails |
| "at which point in their time" | local time inside the source (file sample / position in the pattern) |
| "live" | from the live process, no render and no solo passes, current to the present state |

## 1. Principles

1. **Estimate, not record.** We don't keep the sound of our own mix: we compute it
   along the way and throw it away. A file in the system exists only where it is
   the source: a reference, an explicit fragment export, offline tools
   (IR/THD/ducking).
2. **Primitives, not policy.** The tool doesn't decide for the model: it doesn't
   start the transport, doesn't pick tracks, doesn't save projects. It reports
   state and does exactly what was asked.
3. **Everything online.** Any metric is available from the live process with no
   project reload; the file version remains for references and as the benchmark
   for cross-checking.
4. Facts, not advice: number, unit, comparison basis, coverage.
5. Comparison instead of explanation: two pictures of one shape → a delta along
   the axes.
6. Invariance: a metric doesn't depend on the track count or the overall level.
7. Provenance: a number has an address — what it's built from, what processed it,
   where to listen.
8. A gap is visible: missing data is marked, never zero.
9. Research first, implementation second. No blind `sleep` and no "wait a bit
   longer".

## 3. What already existed (not passed off as new)

| idea | where from |
|---|---|
| one tree and path addressing | `archive/vers1/FLSTATE-SPEC.md` §4.1 |
| layers: static / time / hearing | ibid. §4.2 |
| freshness ("the hearing is stale") | ibid. §4.3 |
| comparison by section/point/instrument/metric/ensemble, corridor | ibid. §4.4 |
| the `fl_picture/fl_query/fl_diff/fl_set` interface | ibid. §5 |
| profile: levels, perception, spectrum, stereo, masking, SII, recognition | `PROFILE-SPEC.md` |
| requirements on the hearing | `GOAL.md`, `REQUIREMENTS.md`, `PERCEPTION-3.md` |
| perceptual methods and ML | `PERCEPTUAL-RESEARCH.md`, `MIXING-ML.md` |
| mixing method | `MIX-METHOD.md` |

New in this spec: the **cube** (time × track/role × band × metric),
**recording every track from memory** with provenance, **roles and granularity**,
**the inverse problem instead of reference separation**.

## 4. Tree and addressing

Everything is one object, every node has a path:

```
/project            name, tempo
/transport          playing, position, seconds, tempo, loop
/track/{i}          name, volume, pan, mute, solo
/track/{i}/slot/{s}              plugin
/track/{i}/slot/{s}/param/{p}    parameter (value + label)
/track/{i}/slot/{s}/band/{b}     EQ band (hz, gain, q)
/track/{i}/send/{d}              send
/channel/{i}        channel-rack channel → target_track
/playlist/clip/{id} clip: track, position, length, source_offset, sample
/section            the working window
/refs/{name}        reference
/measure/{id}       measurement frame
```

`*` — all nodes: `get("/track/*/volume")`. An error is structural:
`{ok:false, code:"range", path, allowed}`. No "what to do".

## 5. The cube: one shape for all pictures

```
axes: time (sample → bar) × track (→ role) × band (ERB) × metric
```

A matrix from the stream is the slice `rms[time, track, band]`. A file's spectrum is
the same slice computed differently. A reference is the same cube.

- a picture = a projection of the cube, a comparison = a delta along the same axes;
- a cell has provenance: sources → processing → the raw segment;
- additive metrics sum inside a role, non-additive ones are computed as
  "role against the rest";
- a gap is explicit, not zero.

## 6. Granularity: references don't have 32 tracks

- `roles.py` reduces the project to `drums / bass / guitar / keys / vocals / other`;
- references exist at the level of roles;
- comparison happens at the coarsest shared granularity (ref ↔ us — roles; our
  "before/after" — tracks);
- the metric is invariant: `role / rest`.

## 7. Getting around reference separation

Stem separation is the weak link. The main path is the **inverse problem**
(`invmix.py`): mix targets from the reference, the unknowns are the gains of our
real tracks, the sum has to land on the target vector. Per-role targets are a
by-product of the solution. Reference stems are only a cross-check with a quality
metric (reconstruction error + leakage). `snr_baseline.json` from stems is
approximate and due for replacement.

## 8. Access to the live FL: three classes

| class | what | coordination |
|---|---|---|
| passive read (tap) | process memory: position, tempo, buffers, meters, structure | not needed, doesn't touch FL |
| control write | volume, pan, parameters, slots, transport | queue, `project_version` grows |
| exclusive lease | render, project switch, restart | a lease on everyone, the rest get `busy` |

The tap reads passively: one process, many consumers via memmap/socket; if it dies,
consumers see `stale` rather than hanging.

## 9. Live audio and provenance

**What is read:** L/R float32 buffers block by block for every track and the master;
meters; position; tempo; clips with their **offset inside the sample**; pattern
notes; channels → tracks.

**What is done with it:** metrics are computed immediately and the audio thrown
away. Sound is held only in a ≤2 s ring — for what can't be computed without
samples (SII, harmony, attacks).

**Writing to a file** is not the pipeline but a separate operation on an explicit
command (`call("record.start", {tracks, path})`), when a fragment has to be handed
to a human. By default nothing is written.

**Provenance at a moment in time:**

```
track 12 (GTR L BUS), t = 114.01 s (sample 5 472 512)
  ├ clip #1847: guitar_L_03.wav, locally 12.7 s of 31.0, gain −1.5 dB
  ├ clip #1902: same file, locally 0.4 s, gain −1.5 dB
  ├ tail: reverb from clip #1801, RT60 1.4 s, −18 dB
  └ channel "Fortin L" → sample, processing in the slots
```

Several sources on one track are normal. Tails are part of the list. The structure
is read from memory, so it matches the current state, not the file.

## 10. Interface: paths and six verbs

```
list(path)            what's there and of what type
get(path)             value
set(path, value)      write (a batch is atomic, with rollback)
call(op, args)        action: plugin.insert/delete/replace, transport, save, track.add
measure(spec)         metric → frame
compare(a, b)         deltas along the axes
```

Properties, not verbs: `dry_run`, `undo`. The MCP surface is the same six plus
`undo`/`dry_run`; the old 77 names remain as wrappers.

Metric registry: `rms`, `peak`, `crest`, `bands` (240 Bark), `erb`, `partial`,
`masking`, `sii`, `transient`, `harmony`, `stereo`, `wall`, `sections`, `lufs`.
A metric is a registry entry (axes, units, source), not a tool.

### 10.0. Example session

```
list("/")                                        what lives where
get("/track/*/volume")                           the loudness picture
measure({metric:"bands", source:"mix.wav"})      the band picture
measure({metric:"bands", source:"ref.mp3"})      the reference picture
compare(a, b)                                    deltas across 28 bands
call("plan.apply", {edits, track_map})           apply the edits
measure({...}) + compare(before, after)          check what it turned into
```

There is no mandatory order: you can start with `set`, or only measure.

### 10.1. Contract: what is expressed by what

| group | paths / operations |
|---|---|
| state | `list("/")`, `get("/project")`, `get("/track/*")`, `get("/track/{i}/slot")`, `get("/channel/*")`, `get("/playlist/clip/*")`, `get("/transport")` |
| values | `set("/track/{i}/volume|pan|mute|solo")`, `set("/track/{i}/slot/{s}/param/{p}")`, `set("/track/{i}/slot/{s}/band/{b}", {hz,gain,q})`, `set("/track/{i}/send/{d}", {active,level})` |
| actions | `call("plugin.insert|delete|replace")`, `call("track.add|delete")`, `call("save")`, `call("render")`, `call("transport.play|stop")` |
| recording | `call("record.start", {tracks, path})`, `call("record.stop")`; read as `get("/record/{id}")` |
| hearing | `measure({metric, by, where, source})`, `compare(a, b)` |
| references | `get("/refs")`, `measure({..., source:"/refs/GRIMA"})` |
| decisions | `call("plan.build", {diff})`, `call("plan.apply", {plan})`, `call("verify", {change})` |
| system | `call("system.health|doctor|reset")`, `call("lease.take|release")` |

The rule: no operation dictates the order of work. `set` works without
`measure`, `compare` without `plan`, `measure` without `set`.

Replacing the old names (77 tools) is mechanical: `fl_tracks` →
`get("/track/*")`, `fl_set_volume` → `set("/track/N/volume")`, `fl_wall` →
`measure({metric:"wall"})`, `fl_apply_plan` → `call("plan.apply")`. Until the new
ones cover everything, the old ones stay as wrappers.

## 11. What's missing (condensed)

The full register is §3 in `docs/archive/specs-03-11/SPEC-CONTEXT.md`. The main
items:

1. **track audio** (tap) — the blocker for everything;
2. exact position in samples and exact tempo;
3. offset inside the sample and clip fades; map freshness;
4. notes from patterns;
5. tails and the master;
6. plugin meters, presets, bypass;
7. automation: reading and writing;
8. render without closing the project, VST into a slot, creating channels.

## 11.5. What's already done on the interface

- `flctrl/frames.py` — the frame (axes, values, units, coverage), a registry of 14
  metrics, comparison over the intersection of axes with a report of what couldn't
  be compared; test 14/14.
- `flctrl/api.py` — the six verbs: `list/get/set/call` on paths, `measure`
  and `compare`; a `set` batch is atomic with rollback; refusals are structural;
  test 14/14.
  `measure({..., "by": "section", "section_s": 8})` — a picture by sections
  (the question "where exactly does it fall apart", not "on average across the
  track");
  `measure({..., "by": "role", "stems": {role: path}})` — by roles; you can pass
  just `sources` — `roles_for` will determine the roles (name → spectrum → other).
  `call("inverse.solve", {stems, against, cap_db})` — the inverse problem: we look
  for the gains of our tracks so that the sum lands on the reference's mix targets.
  `measure({..., "against": [references]})` — in one call, the corridor over the
  references (median and p10..p90) plus a list of outliers with the worst cells:
  exactly the question "are we outside the corridor or inside it, and where".
  Metrics in `measure`: `bands` (28 third-octave bands in dB relative to the
  mean), `rms`, `peak`, `crest`, `lufs`, `transients` (attack/tail across three
  bands), `harmony` (12 degrees, L2), `wall` (band medians), `sii`
  (needs vocal+masker), `partial` (needs stems+mix).
- Frames live under an id (`f1`, `f2`): `compare` takes ids, not megabytes of
  tables; `get("/measure/f1")` fetches a frame, `get("/measure")` the list.
- `dry_run(on)` — "show only" mode: `set` changes nothing and returns the plan;
  `undo(n)` — rolls back the last changes (stack in application order).
- MCP by default exposes **nine** tools: `fl_help`, `fl_list`, `fl_get`,
  `fl_set`, `fl_call`, `fl_measure`, `fl_compare`, `fl_undo`, `fl_dry_run`. The full set (83) is enabled
  by the `FLCTRL_MCP_FULL=1` variable, the old names remain as wrappers.
- The live source (`"live"`) honestly answers `not_available` until the tap is ready.
- `docs/snr_baseline.json` is flagged: the targets came from separating references,
  quality was not checked, they are to be replaced by the inverse-problem solution.

## 12. Order of work

1. audio stream from the tracks in stereo + recording to disk;
2. exact position and tempo;
3. provenance: offset in the sample, freshness;
4. notes from patterns;
5. tails and the master;
6. perception on the recorded tracks (file metrics already exist);
7. plugin meters and automation.

## 13. Definition of done

First milestone: **any segment of any track is written to a file without rendering
the project and without solo passes**, with exact position and tempo, and from it
you can see which clips and MIDI it was built from and at which point in their
time.

The full system answers six questions with numbers and no render: what's playing
and from where; how audible it is; who is masking; what the plugin is doing; how
far it is from the genre; what happens if you change it.
