# Streaming: breaking down every tool, and the design

**English** · [Русский](STREAMING.md)

The document answers the question "can everything be computed in a stream, without
files". The short answer: **yes, but not every metric and not at one rate** — and that
has to be visible in the data, not blurred over.

## 1. Why "just stream it" does not work

| quantity | value |
|---|---|
| raw audio per track | 44100 × 2 × 4 bytes = 353 KB/s |
| 32 tracks | **11.3 MB/s** (a 6-minute track — 4 GB) |
| block | 256 frames = 5.8 ms, **172 blocks/s** |
| one RPC call from Python | ~0.3–1 ms; 32 tracks per round ≈ 32 ms |
| polling at 30 Hz over 32 tracks | we see **~17 %** of the audio |

Two conclusions follow:

1. There is no reason to keep the audio in full: the picture (metrics over time) is tens
   of MB against 4 GB of raw material.
2. Polling cannot be the source of "exact" metrics: it only sees a fraction of the
   blocks. Precision must either be delivered in the callback or honestly labelled.

## 2. Inventory: what we have and what each metric needs

All current analysers take a **file path** and compute an STFT over the whole material
(`bandprofile.band_axes`, `speech.intelligibility`, `wall.analyze`,
`transients.analyse`, `partial.bands29`, `harmony._spectra`,
`perceptual.band_levels`). That is an offline architecture; streaming needs incremental
versions with state.

| metric | function today | what it needs from the data | in a stream |
|---|---|---|---|
| `rms` | `perceptual`/`partial` | sum of squares | **yes, exact** |
| `peak` | — | maximum of the absolute value | **yes, exact** |
| `crest` | — | peak − rms | **yes, exact** (from the two above) |
| `lufs` | `perceptual.lufs` | K-weighting (IIR) + gating | **yes**, the IIR keeps state |
| `bands` (28 bands) | `bandprofile.band_axes` | spectrum of the block (FFT 256) | **yes**, FFT per block |
| `erb` | `perceptual.band_levels` | spectrum + ERB matrix | **yes** |
| `stereo` | `listen_layer.side_mid_db` | L/R per block | **yes** |
| `masking` | `api.measure` | band levels per track | **yes** (from `bands`, in pairs) |
| `transient` | `transients.analyse` | envelope + 200 ms history | **yes**, with state |
| `wall` | `wall.analyze` | spectrum + medians per section | **yes**, sliding medians |
| `sections` | `wall`/`frames` | feature vectors per window | **yes**, incrementally |
| `partial` (sones) | `partial.bands29` + `perceptual` | 29 bands + Zwicker | **yes**, more costly (per window) |
| `sii` | `speech.intelligibility` | vocal + masker, 18 bands | **via the ring**, in 0.5–1 s windows |
| `harmony` | `harmony._spectra` | long spectrum (tuning/chroma) | **via the ring**, in windows |
| IR / THD / ducking | `effects.py` | impulse/sine/special signal | **no, offline only** |
| comparison with a reference | `frames.compare` | reference file | **offline** (the reference is a file) |

## 3. Three classes of precision

Every row of the picture is labelled so that an estimate is never passed off as a
measurement:

* `exact` — computed on **every** block (callback, cheap arithmetic, ≤8 channels);
* `sampled` — from a sample of blocks (polling), the field `coverage` = the share of
  audio seen;
* `windowed` — from the ring every N seconds (SII, harmony, sections);
* `offline` — inherently requires a file or a special signal.

## 4. Design

```
FL callback (JS)          Python (30–60 Hz)  picture
─────────────             ─────────────────  ───────
buses: sum/sumsq/peak   → exact row        ─┐
tracks: hex block       → FFT, bands       ─┼→  rows[t] = {exact|sampled, bands, …}
ring 2–10 s (raw)       → SII, harmony     ─┘
                                             ↓
                                                cube: time × track × band
```

* **The ring** — the only place where audio is stored: 2–10 s, enough to finish whatever
  needs samples (transients, SII, waveform).
* **Analysers** — objects with `feed(block)` and `value()`: IIR filters, a per-block
  FFT, envelopes, chroma accumulators. The state is small, the history does not grow.
* **The exact tier** — buses only (master + roles): the callback computes the sum, the
  sum of squares and the peak, with no allocation per block.
* **Provenance** — position in beats (bridge, every 0.3–0.5 s) → clips from the `.flp`
  parse → the same row of the picture.

## 5. How to check that the stream is not lying

A mandatory condition, otherwise this is a guess rather than a measurement:

1. take a known WAV, run it through the file analyser — get the reference;
2. run the same file through the streaming analyser (in blocks of 256) — get a series;
3. compare: `rms`/`peak` — to within 0.01 dB, `bands` — to within 0.1 dB, `lufs` — to
   within 0.1 LU, `transient` — onsets matching within a single block.

Until that comparison is green, the metric is marked `sampled` and does not enter
decisions.

## 6. What this changes in the API

Nothing breaks: the source of a frame is no longer only a file but also a stream.

```python
cap = stream.run(seconds=20, exact=[0, 1, 2], tracks=range(32))
measure({"metric": "bands", "source": "cap1", "by": "section"})   # the same frame
compare("cap1", "ref")                                            # the same shape
```

Files are an explicit export only (`cap.export(path)`); by default nothing is written to
disk.

## 7. Open questions

* The exact tier on all 32 tracks: what it costs in the callback (it must be measured,
  not assumed).
* Poll rate against load: 60 Hz × 32 tracks × FFT 256 — measure it.
* SII needs the vocal and the masker separately — which tracks count as the pair (the
  `vocals` role against the sum of the rest).
* The threshold below which `sampled` is not used in decisions (`decide.py`).
