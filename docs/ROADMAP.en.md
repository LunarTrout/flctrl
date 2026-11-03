# ROADMAP: order of work and hard rules

**English** · [Русский](ROADMAP.md)

## HARD RULE (do not cross)

**Until the library is fully implemented, we do not touch the `vers1` project.**
No edits, no "trial" changes, no saves in it.
Only reading files to check tools — and even that minimally.

The same applies to: live pan across a track, guitars moving between sections, spreading
doubles, "pretty" stereo, polishing the sound up to the references.
All of that is phase C, that is, **after** the library.

**Once the library is fully implemented, the project can be twisted and turned** — any
edits, any experiments, including on real material.
There is one condition: the library is finished entirely, not "almost".

## Principle: no hardcoded rules

Any "everything must move", "always wider", "always cut 300 Hz" is hardcode, and it is
forbidden. Every suggestion the library makes must be derived from a measurement: spectrum,
role overlap, spread across sections, corpus distribution. If it cannot be justified with a
number, there is no suggestion.

The same goes for the rules themselves: they must be refined as they are tested
("did it → measured it → better/worse"), not stay nailed down.

## Phase A. Hearing (now)

Goal: a system that understands from the sound everything that is measurable and says what
to turn — for each instrument, loudness, spectrum, frequencies.

- [x] 29/28 bands, calibration, Zwicker
- [x] Partial loudness per ISO 532-2 (Moore-Glasberg), audibility A, dN/dB
- [x] Instrument roles by names and spectrum
- [x] Targets from the corpus: distributions + an ensemble of the nearest references
- [x] Inverse problem: gains for a mix-level target (28 bands)
- [x] Audit by fronts: dynamics, spectrum, stereo, noise, attacks, clipping
- [ ] Audit: fix mono loss and the "worse/better" directions, run the test
- [ ] Stereo per band (width in each band, not a single number)
- [x] Vocal intelligibility (SII/STOI) and "drowning" in specific bands —
      `speech.py`: the full ANSI S3.5 procedure (18 bands, importances and internal
      noise from the standard), per frame and only where the vocal sounds, with the
      masker level calibrated against the master. The estimate is relative: the base of
      reference SII is in `docs/sii_baseline.json`, otherwise the scale is a speech one
      and inapplicable to metal (our 0.21 against 0.01–0.32 for the references).
- [x] Effect metrics: IR probe (RT60/EDT/C50), THD/IMD, ducking
- [x] **Wall and dips** — `wall.py`: a hole is sought relative to the track's own median
      across 9 bands and sections, thickness is the share of time within a ±6 dB corridor.
      Real mix: thickness 1.00 against the genre, one dip — 12 kHz (8.4 dB).
- [x] **Pan as a form of movement:** `panplan.py` builds a plan by section from the
      measured width: dense — towards the edge of its own side, sparse — towards the
      centre; the step is limited, the low end does not move, the mirroring of pairs is
      preserved. Real stems: guitars −0.37/−0.52/−0.37 against +0.37/+0.52/+0.37.
- [ ] Applying the pan plan in FL as automation (`mixer.automateEvent`) —
      needs a window with a test project
- [x] Report: one call → "where we are worse than the references and what to turn" (report.py + docs/example-report.md)
- [x] **Per-section edits from measurements** — `decide.py` + targets from the corpus
      (`snrtargets.py`, `docs/snr_baseline.json`). About 140 lines: instrument, band,
      region, our level, target, edit size with a cap.
- [x] **Checking the plan before writing** — `simulate.py`: edits are applied to the stems
      by region, the mix is rebuilt and re-measured. On real stems: bass 0.091 → 0.310 in
      audibility (share 3.5 → 13.1 %), guitars 0.536 → 0.447 (share 88.2 → 77.2 %) — that
      is, exactly what the plan promised.

## Phase B. FL API (after hearing)

Goal: everything a human does in FL is available to code, and in real time.
**The main criterion: no operation requires reloading the project.**
Live mode (attach to an open FL) is the main path; reloading the project is an extreme
case, not a working technique.

- [ ] Full coverage: tracks, buses, routes, plugins, parameters, automation
- [ ] Real time: reading state and writing without stopping the transport, with a queue
- [ ] Creating objects **without a reload**: channels, clips, automation
- [ ] VST live insertion (wrapper/state) — the only known gap
- [ ] Convenience: one call — one action, clear names, human-readable errors
- [ ] Resilience: reconnection, timeouts, recovery after crashes
- [ ] One canon of code: the repository, no copies in the working folder

## Phase C. Project (only after A and B)

- Mix edits on all fronts, bringing it up to the level of the references
- **Pan across the whole track:** varied and alive — guitars moving between sections,
  spreading doubles, atmospheres breathing; not static roles
- An audit check after every step, rollback on degradation
- Mixing and mastering, comparison against the corridor

## How to understand "the library is finished"

Criteria, not a feeling:
1. All items of phase A are closed, the audit and hearing pass their tests.
2. The phase B API is closed per the checklist, tests on a clean project are green.
3. No known "stubs" and no places where the code lies (as with Demucs stems).
4. `tools/check_duplicates.py` is clean, smoke tests are green.

## Full project context

The target scheme and the register of everything that is missing (sound, time, provenance,
plugins, perception, knowledge, infrastructure) — `docs/archive/specs-03-11/SPEC-CONTEXT.md`.
The order of work and the readiness criterion are there too.

## The cube of pictures

A single data shape (axes: time × track/role × band × metric), pictures as projections,
comparison as a delta along the same axes, provenance down to sources and raw material.
The granularity alignment rules (references live only by role) — in
`docs/archive/specs-03-11/SPEC-CONTEXT.md`, section 2.5.
