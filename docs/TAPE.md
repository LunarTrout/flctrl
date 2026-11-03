# The tape: every field, what it means, how to read it

The tape is the library's unit of description. One tape per track per capture, plus
derived layers (events, masking, provenance).

Everything below is produced by `flctrl.timbre.analyze(stereo, fs)` and assembled by
`flctrl.live`.

---

## 1. Grid

| Parameter | Value | Note |
|---|---|---|
| Cell (hop) | 0.1 s | `hop_s` argument, changeable |
| Analysis window | 4096 samples | ~93 ms at 44.1 kHz |
| Window function | Hann | |
| Band set | 28 third-octave | 25 Hz … 12.5 kHz, `FC` in `incr.py` |
| ERB set | 24 bands | geometric 25 Hz … 12 kHz |
| Pitch floor | 45 Hz | below that, f0 needs more than one window |
| Loudness blocks | 400 ms / 100 ms step | BS.1770 via `incr.LoudnessMeter` |

Time resolution and frequency resolution trade against each other: 4096 samples give 21 Hz
frequency resolution but smear anything shorter than ~93 ms. That is why short events live
in a separate layer (see [LIVE.md](LIVE.md) and `events.py`).

---

## 2. Per-cell fields

### Loudness and level

| Field | Unit | Meaning |
|---|---|---|
| `rms_db` | dBFS | RMS of the cell, averaged L/R |
| `peak` | linear | absolute sample peak in the cell |
| `crest_db` | dB | peak / RMS; high = transient, low = compressed or sustained |
| `lufs_st` | LUFS | short-term loudness of a 400 ms block starting at this cell |

### Dynamics

| Field | Unit | Meaning |
|---|---|---|
| `attack_s` | s | time from 10 % to 90 % of the cell peak |
| `decay_db` | dB | drop from peak to the tail RMS inside the cell |
| `flux_db` | dB | level change versus the previous cell |
| `zcr` | 0…1 | zero-crossing rate — noise vs tone, cheap |
| `hits` | count | regular peaks inside the cell if they are periodic 6–40 Hz |

### Spectrum

| Field | Unit | Meaning |
|---|---|---|
| `bands` | dB | 28 third-octave powers; a unit sine reads 0 dB |
| `erb` | dB | 24 ERB-like bands, closer to hearing than third-octaves |
| `centroid_hz` | Hz | spectral centre of gravity — brightness |
| `spread_hz` | Hz | spectral standard deviation — width of the spectrum |
| `flatness` | 0…1 | 1 = noise, 0 = tonal |
| `rolloff_hz` | Hz | frequency below which 85 % of energy lies |

### Tone and harmony

| Field | Unit | Meaning |
|---|---|---|
| `f0_hz` | Hz | fundamental (autocorrelation on the loudest 2048 samples; spectral fallback) |
| `note` | name | nearest pitch class of the chroma peak (empty if f0 = 0) |
| `hnr_db` | dB | harmonic-to-noise ratio: >8 dB tonal, <0 noisy |
| `chroma` | 12 × 0…1 | pitch-class distribution, normalised |
| `harm` | 5 × dB | levels of 2f0…6f0 relative to f0 |
| `inharm` | 0…1 | share of energy outside the harmonic series |
| `formants` | up to 3 × Hz | peaks of the smoothed spectral envelope (200–4000 Hz) |

### Band behaviour

| Field | Unit | Meaning |
|---|---|---|
| `low_db`, `mid_db`, `high_db` | dB | energy in 20–200, 200–2000, 2000–16000 Hz |
| `decay_bands` | dB/s | decay slope inside the cell for the same three bands |
| `wall` | 0…1 | density: mid share plus roughness; 0 transparent, 1 wall |

### Stereo

| Field | Unit | Meaning |
|---|---|---|
| `corr` | −1…1 | L/R correlation of the cell |
| `width` | 0…1+ | side energy / mid energy |
| `pan` | −1…1 | energy balance L/R (−1 hard left) |
| `coherence` | 3 × 0…1 | L/R coherence per band group: 1 mono, 0 independent |

---

## 3. Per-track fields

| Field | Unit | Meaning |
|---|---|---|
| `lufs` | LUFS | integrated loudness with BS.1770 gating |
| `lra` | LU | loudness range (95th − 10th percentile of blocks) |
| `sii` | 0…1 | crude speech-intelligibility share (500–4000 Hz vs total) |
| `harmony` | object | `pitch_classes` (12), `major`/`minor` fit with root, `top` degrees |
| `checks` | object | `dc`, `clip_samples`, `mono_corr` |
| `transients` | object | `onsets`, `onsets_s`, per-band `attack_ms` and `ts_db` |
| `mod_rate_hz`, `mod_depth` | Hz, 0…1 | amplitude modulation: reed beating, tremolo |
| `jitter_pct` | % | f0 instability across cells with confident pitch |
| `technique` | object | `rate_hz`, `fast`, `per_cell` — regular pick/roll rate at 5 ms resolution |
| `rms`, `peak`, `silent` | dBFS / linear / bool | summary of the take |

---

## 4. Per-capture (cross-track) layers

| Field | Meaning |
|---|---|
| `masking` | `pairs`: `{masker, target, band_hz, db}` for pairs separated by ≥10 dB in a band |
| `provenance` | every 0.5 s: `{t, bar, active:[channel/pattern/clip names]}` from `clip.at` |
| `position` | `{start_seconds, bpm}` — the anchor of the take in the song |
| `backup` | save point: `{ok, file, origin: "save"|"autosave", points}` |
| `cleaned` | how many header-only files were removed from `Recorded` |

---

## 5. Size

Measured on real material (drums, 1.52 s, 15 cells):

| Item | Bytes |
|---|---|
| one cell | ~1029 |
| cells, all cells | 15 448 |
| events | 1 486 |
| whole tape (JSON) | 22 512 |

Scaling: 14.5 KB per second per track; 29 tracks × 10 s ≈ 4.1 MB. Raw PCM for the same
audio is 97.6 MB.

Practical rule: keep tapes in memory, keep audio only if you need to listen.

---

## 6. How to read it

Read the tape as evidence, not as labels:

- **Level and dynamics** — `rms_db` and `crest_db` together: high crest means hits, low
  crest with high RMS means sustained or over-compressed material.
- **Brightness** — `centroid_hz` and `high_db − low_db`. A move of 3 dB in the top band
  shifts the centroid measurably; that is the number to quote.
- **Tone vs noise** — `hnr_db` and `flatness` separate a distorted guitar from cymbals
  without any instrument labels.
- **Pitch content** — `f0_hz` + `chroma` + `harm`: a riff has a stable f0 and a harmonic
  series; an inharmonic hit (`inharm` near 1) does not.
- **Stereo** — `corr` and `width` together: `corr` 0.95/`width` 0.15 is a centred mono
  source; `corr` 0.2/`width` 0.6 is two independent sources hard-panned.
- **Change over time** — `flux_db` spikes mark section boundaries and attacks; `lufs_st`
  shows the loudness contour.
- **Buses vs sources** — a bus correlates with its source (we measured 0.61 for DRUM BUS ↔
  DRUMS SD3) and is usually a few dB louder in the same bands. Do not mistake that for a
  duplicate.

---

## 7. What the tape deliberately does not contain

- Instrument names or technique labels. Heuristics were removed on purpose: numbers plus
  context are more honest than a guess dressed as a fact.
- Reference deltas. They belong to a comparison step, not to a single capture.
- Sample-level waveforms. If you need the audio, ask for it explicitly
  (`tracks.dump`).
