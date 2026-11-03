# How It Works

**English** · [Русский](ARCHITECTURE.md)

A document for whoever is going to read the code. Here is how each layer is built:
what it measures, what it rests on, where it lies, and what has been verified by running it.

---

## 1. The Big Picture

FL Studio has no public API. Everything the user does by hand — inserting a plugin,
creating a channel, turning a knob — has no call you can make from code. And mixing
blind is impossible: decisions come from what you hear, and "what you hear" was,
until recently, not measurable.

The library closes that gap with three independent access channels:

| Channel | What it gives | Limitation |
|---|---|---|
| **the `.flp` file** | read everything that was saved: tracks, channels, plugins, playlist, clips with paths, automation, tempo | only the saved state |
| **the controller bridge** | live control: any plugin knob, volume, pan, mute/solo, routing, transport, notes | does not create objects |
| **Frida** | creating objects live: insert, delete, replace a plugin | plugins only, for now |

The point of the split: the file is the source of truth about project structure, the
bridge is the hands, Frida is what neither of them can do.

On top sits the listening layer: it turns sound into numbers you can make decisions
from and check them against.

---

## 2. The Listening Layer (`listen_layer.py`, `perceptual.py`)

### 2.1. Loudness

ISO 532-1 through `phonometry`: first a third-octave spectrum, then specific loudness
`N′(z)` over **240 Bark bands**, the integral is total loudness in **sones**, and in
parallel loudness in **phons**.

The key detail: sones depend on the listening level. The measurement is calibrated at
**90 dB SPL**, and the calibration is computed **over the whole track**, not per
segment. This is not pedantry: calibrating each segment separately pulls silence "up"
to normal loudness, and the quietest fragment becomes the "densest". That is exactly
how a center of 19.8 s, LUFS −44 and crest 40.8 were once produced — that is, a
measurement of silence instead of music.

Verified: **1 dB** of gain gives **+5.1 sones** (81.87 → 86.98). So sones are comparable
only at equal loudness, and area percentages almost not at all: they drift by
**±0.05 pp** per 1 dB.

### 2.2. Sharpness

DIN 45692, in **acum**. Computed from specific loudness with a weighting function that
emphasizes the high frequencies.

An important property, verified by direct experiment: sharpness is **almost invariant
to loudness** — the same +1 dB gives +0.002 acum (1.617 → 1.619). So it measures
**the brightness of the material**, not loudness.

Hence the rule: sharpness cannot be compared between segments of different makeup
(0.17 acum spread within a single track), but it does not need to be loudness-matched.

### 2.3. Roughness and Tonality

ECMA-418-2: `roughness_ecma` (asper) and `tonality_ecma`, both broken down by
**53 bands** and over time (101 × 53). Roughness catches beating — the thing detuned
guitars give you in black metal.

The cost: about **45 s per 10 s** of material. That is expensive, so the layer is
switched on selectively, not in every measurement.

### 2.4. Vocal Intelligibility

STOI against clean vocals. Works only on an **aligned** stem: a 29 ms shift, which
moves the correlation from 0.9 to 0.31, costs **0.26 STOI** — more than the difference
between all the references. So STOI is computed either from a stem out of FL, or not
at all.

### 2.5. Spectrum: 240 Bands and Seven Regions

The Bark profile is built along three axes: level, width (stereo), modulation (how much
a band "breathes"). For decisions it is handier to fold it into seven regions:

| Region | Bark | Hz | What that is by ear |
|---|---|---|---|
| sub | 0–2 | 0–204 | sub, the body of the kick |
| bass | 2–5 | 204–560 | boxiness, "cardboard" |
| lowmid | 5–9 | 560–1278 | body of the guitars, chest of the vocal |
| mid | 9–14 | 1278–3065 | intelligibility, attack |
| upmid | 14–18 | 3065–6011 | articulation, click |
| pres | 18–21 | 6011–9926 | presence |
| air | 21–24 | 9926–16374 | air |

The names do not match everyday usage: "bass" here is 204–560 Hz, that is,
**boom, not the low end**. Body in the low mids is lowmid, 560–1278.

### 2.6. Dynamics

LUFS (gated integral), crest (peak to RMS), LDR — the ratio of a short and a long RMS
window. LDR is computed through a cumulative sum: the naive `np.convolve` with a
144 000-sample window was quadratic and took 88 s on a 30-second segment; after the
replacement — **0.53 s**, and a whole 352 s track — **6.6 s**.

### 2.7. Stereo

Channel correlation, side/mid ratio in dB, width. Computing them only makes sense once
the tonal balance has settled: correlation is taken over all the energy, and 60 % of
the energy is low end.

### 2.8. Masking and Unmasking

`masking` shows by how many dB and in which bands one source covers another. `unmask`
is the inverse problem: how much comes back in a track if you remove its neighbor.
That is the numerical definition of "the instruments get in each other's way".

It requires valid stems. Separator stems will not do: the guitar stem has an SDR of
about 3 dB.

### 2.9. The Corridor

The comparison is not against a single number but against a **corridor** built from
several references, and only on like-for-like sections. The format:

```python
corridor = {"sharp_mean": {"коридор": [1.40, 1.70]}, ...}
```

---

## 3. Recognition and Style (`describe.py`, `mixstyle.py`)

Two different questions, two different tools.

**`describe`** — PANNs embeddings: "what does this sound like, material-wise". The
cosine between our mix and GRIMA is 0.858. Good for finding similar places in
references, no good for balance decisions.

**`mixstyle`** — the MIXSTYLE encoder (2048-dimensional): "is the approach to mixing
similar" — compression, density, width, character of processing. For the same pair it
gives 0.708.

The gap between the numbers is not an error but confirmation that the metrics measure
different things. The rule: **`measure` decides, `describe` searches.**

---

## 4. The Sandbox (`sandbox.py`)

Testing a hypothesis without risk: a processing chain is applied to a segment, with
real plugins, and the result is measured by the same listening layer.

Primitives: `gain`, `hpf`, `lpf`, `eq`, `comp`, `sat`, `pan`, `vst3`. The VST3s work
for real, through `pedalboard`, with real plugin files and real parameters.

A mandatory rule: after an edit, **match the loudness** before comparing, otherwise you
are measuring loudness, not timbre.

---

## 5. Prediction (`predict.py`)

An additive power model: a track's contribution sums with the mix per band. It lets you
answer "what changes if I drop this track by 2 dB and cut 3 dB at 300 Hz" without
applying it.

The honest boundary: the model is linear. Everything that depends on nonlinearity —
compression, saturation, the limiter on the master — is predicted poorly.

---

## 6. Accepting a Decision (`judge.py`)

Turns "it got better" into a checkable verdict: `accept`, `reject`, `insufficient`.

How it is built:

1. **Significance.** Every metric has its own noise threshold: sharpness 0.01 acum,
   sones 0.5, LUFS 0.1 dB, crest 0.2 dB. A change below the threshold does not count
   as a change.
2. **Target.** As a direction (`down` / `up`) or as a number.
3. **Collateral damage.** A metric is damaged if it ended up **further from the
   corridor than it was**. Not "outside the corridor", but specifically further —
   otherwise any metric that was already outside would block everything.
4. **Journal.** Every decision is written to `_cache/journal.jsonl`.

The subtlety: damage is counted only for the metrics that are in the corridor. The
rest may move freely — otherwise the system forbids everything.

---

## 7. Live Object Creation (`live_create.py`)

The hardest part. FL has no object-creation API, and debuggers are impossible:
Image-Line's protector kills the process in 0.3 s. Only Frida works.

### 7.1. How the Rig Is Built

1. `safe_fl.frida_spawn()` — closes stray FL instances, starts `FL64.exe` with the test
   project **suspended**, under Frida.
2. `Process.attachModuleObserver` — `FLEngine_x64.dll` does not load immediately, so
   the module base is caught at load time (`getModuleByName` fails at startup).
3. After loading: `resume`, find the main thread, and every call comes only from the
   **UI thread** (otherwise the engine does not update its state).
4. From there the commands go through `Daemon.run_op` — in the same process, without
   sockets or subprocesses.

### 7.2. How Slots Are Found

By scanning memory through the vtable: a slot object is identified by its pointer to
the method table. The object has:

| Offset | What |
|---|---|
| `+0x160` | insert (mixer channel) number |
| `+0x164` | slot number (0–9) |
| `+0x64` | state: `2` — occupied, `-1` (`0xFFFFFFFF`) — empty |
| `+0x80` | HMODULE of the loaded plugin |
| `+0x68` | parameter count |
| `+0x50` | pointer to the name |
| `+0x30`, `+0x38`, `+0x40` | subobjects (present only on an occupied slot) |

There is **no** fixed stride between inserts: the blocks live in separate allocations.
That is why addressing goes through `+0x160/+0x164` and not through arithmetic.

### 7.3. Deleting

The engine's native function `FLEngine+0xE29570`, `RCX` = slot object. It does a full
removal: takes the slot off the global list, calls the instance destructors, frees the
subobjects, `FreeLibrary` for the DLL, sets `+0x64 = -1`.

Nothing needs to be finished off by hand — and that matters, because attempts to zero
the fields directly (`+0x30`, `+0x38`, `+0x40`, `+0x50`, `+0x78`, `+0x80`) **do not
free** the slot: FL keeps seeing the plugin because the state field was never touched.

### 7.4. Inserting

The native function `FLEngine+0xE28E70`, `RCX` = slot object, `RDX` = path as a string.
Inside: preparation through the vtable, a file check, loading the `Fruity Wrapper_x64.dll`
wrapper, `CreatePlugInstance`, filling in the fields, and finally `[slot+0x64] = 2`.

The string is a **Delphi UnicodeString**, a pointer to the first characters, with the
header 12 bytes earlier:

```
[-12] u16 CodePage = 1200 (UTF-16LE)
[-10] u16 ElemSize = 2
[ -8] u32 RefCnt
[ -4] u32 Length (in characters)
[  0] UTF-16 characters + NUL
```

The trap that cost us the first run: `Memory.alloc` inside an RPC is freed after
return, and by the time anything reads through the pointer there is garbage there. The
slot silently stays empty. The fix is to keep the buffer in `globalThis`.

### 7.5. What This Proved

The insert was verified not only by "FL accepted it": the project was saved, the file
grew from 68 380 to 68 754 bytes, and after a restart the plugins were still there. So
the creation is real, not cosmetic.

---

## 8. The File Route

A fallback, and a fully working way to create plugins when live mode is unavailable.

A plugin record in the `.flp` is self-contained, which is why it can be cloned as a byte
block. The slot structure in the file:

```
0xC9 name → 0xD4 (insert, slot) → 0x9B icon → 0x80 color
          → 0x29 flag → 0xD5 state → 0x62 terminator
```

Inserts are separated by the `0xCC` event (mixer track name). For any installed plugin
there is a ready-made source — its `.fst` from the FL database.

The cost is a project reload of about 30 seconds, so inserts are collected in a batch
and the project is reloaded once.

---

## 9. Verified Numbers

Not "it should work" but measured:

| What | Result |
|---|---|
| measuring a 30-second segment | 0.5 s |
| measuring a whole 352 s track | 6.6 s |
| PANNs embedding (25 s) | 0.86 against GRIMA |
| MIXSTYLE mixing style | 0.708 against GRIMA |
| slot map | 0.21 s |
| inserting a plugin live | 1.22 s |
| deleting a plugin live | 1.45 s |
| reading the playlist of `vers1.flp` | 98 tracks, 778 clips |
| clip → file | 779 entries, 772 with a file |
| automation | 4 entries |
| live estimate against a real measurement | 65.2 against 66.4 sones |

---

## 10. Where We Lie to Ourselves

Listed honestly, because this matters more than a list of capabilities.

* **Stems cannot be summed**: the sum of specific loudness over the stems gives 1.55×
  the mix.
* **The separator's guitar stem** is unfit for conclusions about balance (SDR ~3 dB).
* **STOI without alignment** is meaningless: the sync error costs more than the
  difference between the references.
* **Sharpness across different segments** cannot be compared (0.17 acum spread).
* **MOS metrics for speech** (NISQA, SQUIM) must not be applied to music.
* **Prediction** does not account for compression and saturation.
* **Routing and tempo** have not been verified live yet.
* **Channels and clips** cannot be created — no hook has been found.

---

## 11. Tests

```bash
../.venv313/Scripts/python.exe tests/test_smoke.py
```

14 checks of the file layers: project reading, measurement, bands, curve, profile,
sections, matching, sandbox (zero gain and −6 dB), verdict, prediction. Live methods
are skipped — they need FL running.

The tests appeared after it turned out three methods were being used wrongly
(`playlist` returns a dict, `clip_files` a list, the corridor looks like
`{"коридор": [lo, hi]}`). Without tests those bugs would still be in the code.
