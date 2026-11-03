# Library status

**English** · [Русский](STATUS.md)

Date: November 3, 2026. Everything below was verified by a run, not claimed.

## What hearing can do

| layer | module | what it provides |
|---|---|---|
| fronts | `audit.py` | 10 fronts against the reference corridor: crest, LDR, sharpness, noise, attacks, clipping, mono loss, spectrum bands, stereo |
| wall | `wall.py` | dips across 9 bands and sections relative to the track's own median; wall thickness |
| stereo | `pan.py`, `panplan.py` | position and width per band; a plan for pan movement by section |
| audibility | `partial.py` | ISO 532-2: `A = N_partial/N_solo`, per band and **per section** |
| intelligibility | `speech.py` | SII per ANSI S3.5, 18 bands, over active frames, with calibration against the master |
| effects | `effects.py` | RT60/EDT/C50, THD, ducking |
| balance | `balance.py` | the target as an ensemble of the k nearest references |
| targets | `snrtargets.py` | genre ratios across 7 regions from the corpus |
| decisions | `decide.py` | edits: instrument × band × region × dB, with a cap and a gain check |
| verification | `simulate.py` | edits are applied to the stems, the mix is rebuilt and measured again |
| report | `report.py`, `checks.py` | one call, a registry of 12 checks, coverage and the reasons for skips |

## Verified numbers

- **Edit accuracy:** a 3 dB boost gives +3.00 dB in the measurement (before the units were
  fixed it was +0.7 — we were counting loudness units, not level).
- **Intelligibility:** our SII 0.21 against 0.015…0.32 for the references — the vocal is
  within the genre norm, the weak spot is 5 kHz.
- **Wall:** thickness 1.00 against the genre, the only dip is 12 kHz, −8.4 dB.
- **Plan on real material:** bass 0.091 → 0.310 in audibility after application (share
  3.5 → 13.1 %), guitars 0.536 → 0.447 (share 88.2 → 77.2 %).
- **Gain check:** a plan of 12 edits gives the mix +3.4 dB and honestly warns about it.
- **Cache:** a repeat report takes 1.8 s instead of 12.3 s, the reports are byte-identical.

## Tests

`python tools/run_tests.py` — 17 files, 14 run offline, **0 failures**.
Live FL tests are switched on by the `--fl` flag and require a test project.

## FL API

Works live (verified on a test project): reading state, tracks, plugins, routes, levels,
writing faders and pan, live insertion and removal of native plugins, creating and deleting
tracks, writing a parameter without stopping the transport, self-diagnostics (`fl_doctor`),
an API map (`fl_capabilities`).

**The plan is applied to the sound.** `fl.apply.eq(track, slot, {"mid": 3.0})` sets the
bands of a live equalizer: the plugin's scales are undocumented, so the values are matched
against its readout (set — read — correct). Verified: "mid +3 dB" landed as 632 Hz / +3.4 dB,
"presence −2" as 4413 Hz / −2.3.

**Transients.** A new front of hearing: distinct hits per second, attack time and the
hit/tail ratio across three bands (`transients.py`). A base of six references lives in
`docs/transients_refs.json`: median hit/tail −0.43/−0.13/−0.25 dB (low/mid/high), attack
42/48/22 ms, 2.66 distinct hits per second. Synthetic material tests the sensitivity:
clicks give 3.7 hits per second and +6.3 dB of hit/tail, a steady tone gives 0 hits.
Verdict: a drop of 1.5 dB or more means a smeared attack.

**The whole plan in one call.** `fl.apply.plan(edits, track_map)` groups edits by
instrument, inserts an equalizer where the slot is empty, sets the bands and saves the
project once. Verified on two tracks: guitar "mid +2 / presence −1.5" landed as 2.3 dB and
−1.1 dB, bass −2.0 as −2.3 dB; a repeated call did not create a second plugin (36 parameters
before and after).

**Pan movement in real time.** `fl.move_pan(track, points, seconds)` drives pan as a
stream. The ordinary write saved the project on every step (1.5 s), so `pan_live` appeared —
value only: 10 writes in 11 ms. Live test: 75 writes in 3 s, −0.6 → 0.54 with no rebounds,
zero errors.

## What remains unverified and why

1. **Automation API** (`mixer.automateEvent`). The signature has been found
   (`eventId, trackIndex, value:int, flags:int`), but the event ids for mixer tracks are
   described nowhere, and the errors are silent. The workaround works and is verified:
   movement is written as a real-time stream (`fl.move_pan`). Limitation: the movement lives
   as long as the writing process does.
2. **Live insertion of a third-party VST** inside `Fruity Wrapper`. Native plugins are
   inserted and removed, VSTs are not; four paths have been checked.

## Honest limitations of the model

- Targets are computed relative to "the rest of the mix" (master minus the stem). The set of
  stems may be incomplete, so the total boost across all roles need not be zero — this is a
  property of the method, not an error.
- Edits in `simulate` are applied to the whole stem, not to their own section: this is a
  check of direction and magnitude, not a copy of the future automation.
- Reference separation does not distinguish guitars and keys — both roles take their target
  from `other`, this is recorded in `docs/snr_baseline.json`.
