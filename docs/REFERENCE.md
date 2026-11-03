# Mixing against one reference, without stems

**English** · [Русский](REFERENCE.ru.md)

A reference arrives as one stereo file. There are no stems, and there will not be any.
This document describes how the library turns that constraint into a working method.

---

## 1. Why not separate the reference

Source separation of a finished master is lossy in exactly the range that matters for
balance: guitars bleed into vocals, drums smear into everything. Published SDR for musical
stems sits around a few dB — enough to *listen*, not enough to set a fader by. And the
separation error is not random: it is correlated with the very masking we are trying to
measure.

So the reference is used as a **target vector**, not as a set of tracks.

## 2. The formulation

Our mix, per band `b` and time segment `t`, is (to first order) linear in our track gains:

```
M[b, t] = Σ_i  g_i · a_i[b, t] · X_i[b, t]
```

where `X_i` is track `i`'s measured band energy, `a_i` is its EQ correction in that band,
and `g_i` its gain. The reference gives the target `R[b, t]`.

The task: find `g` (and a small EQ per track) minimising

```
Σ_{b,t}  w[b,t] · ( M[b,t] − R[b,t] )²   +  λ · ‖g − 1‖²
```

subject to side constraints:

| Constraint | Why |
|---|---|
| `|g_i| ≤ 6 dB` | avoid destroying the arrangement to match a number |
| `|EQ_i[b]| ≤ 4 dB` per broad band | broad moves only; surgical EQ is not a balance tool |
| role hierarchy preserved | drums must not end up quieter than guitars |
| loudness target within ±0.5 LU | a level shift must not be "solved" by faders alone |

The regularisation term matters: without it the solver happily proposes extreme gains that
match the reference and ruin the mix. `invmix.py` implements the core of this; the missing
piece is the target builder (below).

## 3. Building targets from a stereo file

`reference.frame(path)` — the next module — produces a frame with the same axes as our
tape:

| Axis | Resolution | Why this resolution |
|---|---|---|
| time | sections, not cells | the reference has no tracks; cell-level matching is noise |
| band | third-octave or octave | fine enough for balance, coarse enough to be stable |
| metric | level, loudness, width, centroid, transient density, LRA | what a reference can actually constrain |

Sections are cut automatically from the reference's own shape: LUFS contour changes and
onset-density changes. Our song is segmented the same way, then the two are aligned **by
order and relative shape**, never by absolute time — tempo and arrangement lengths differ.

## 4. What can and cannot be matched

| Matchable | Not matchable |
|---|---|
| band balance per section | exact instrument timbre |
| loudness contour and LRA | instruments we do not have |
| stereo width and correlation per band | performance and arrangement |
| transient density (compression amount) | room and mic character |
| spectral centroid trajectory | nonlinear processing artifacts |

Nonlinear processing (compression, saturation) is matched indirectly, through LRA, crest
and transient density — not through band levels, which it changes unevenly.

## 5. The loop

```
reference.frame(path)          → targets
tracks.live(seconds, start)    → our tape (per track)
invmix.solve(tape, targets)    → {gain_i, eq_i, pan_i}
apply                          → live faders / EQ / pan
tracks.live(...)               → measure again
compare(tape_before, tape_after) → delta vs target
```

Stop when the band deltas are within ±1…2 dB and the loudness delta within ±0.5 LU, or when
further moves would violate the constraints. Every step is a number, so every step is
reproducible and reversible.

## 6. Practical notes

- Capture the same section twice: once before the move, once after. Comparing different
  parts of the song is a classic way to measure the arrangement instead of the mix.
- Use the reference's *shape*, not its absolute level: a master at −9 LUFS and our mix at
  −14 LUFS are not "wrong", they are at different stages.
- Watch `masking` while solving: a gain that improves the band balance but increases
  masking in 2–5 kHz is a regression.
- Keep save points on: the solver proposes, the engineer accepts, FL can always be rolled
  back.

## 7. Current state

Implemented: `tracks.live`, `timbre`, `events`, `frames.compare`, `invmix` (solver core),
`clip.at` (provenance).

Missing: `reference.frame()` (target builder from a single file) and section alignment.
Until those exist, reference matching is done by hand: measure the reference with
`timbre.analyze`, measure our mix, compare frames, and decide.
