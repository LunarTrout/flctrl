# SOTA: automatic mixing and balance — the state in 2026

**English** · [Русский](RESEARCH-SOTA.md)

The survey is assembled from primary sources (arXiv, AES, ISMIR, journals). Its job is
not to enumerate everything but to separate what is genuinely applicable to our library
from what requires training on large data.

## 1. How the problem is framed today

**Implicit (end-to-end)**: the model takes stems and outputs a finished mix.
Example — FxNorm / the automatic mixing of Martinez et al. (Sony, 2022):
stems normalised by type, training on "wet" data, a result indistinguishable from a
professional's mix in a blind test. Limitation: a fixed instrument set.
Note: "Ganymed" is a 2016 FM synthesiser, not a mixer; the name
has nothing to do with automatic mixing.

**Explicit (effect parameters)**: the model predicts gain/EQ/compression/pan,
then a differentiable console DSP applies them. Examples: Diff-MST
(arXiv 2407.08889, 2024), Diff2Mix (diffusion + differentiable effects),
MEGAMI (Sony, 2024–2025: a generative model of effect embeddings, open source).
Limitation: the effect topology is fixed in advance.

**Sequential stem blending** (arXiv 2608.05506, 2026): mixing as the
sequential addition of a stem to a growing submix — this is how people work.
The model is conditioned on the submix context, and the number of stems is arbitrary.
For us this is a confirmation: the incremental "one stem at a time" approach is
not a simplification but a reproduction of the human strategy.

## 2. Training-free approaches (important: we have no training dataset)

- **ELL — equal local loudness**: evening out local loudness within
  a subgroup. Used as a baseline at ISMIR 2026 and shows that
  a coarse but stable normalisation already gives a workable result.
- **Unmasking panning** (Tom, Reiss, Depalle, AES 146, 2019): minimising
  inter-track masking with the MPEG psychoacoustic model + panning rules
  (low end and lead in the centre). In dense material panning removes masking
  more strongly than faders.
- **Masking through subgroups**: balance within a group, then between groups.
  ISMIR 2026 confirms: the two-stage scheme is significantly better than the one-stage
  one, and wrong grouping damages the result more than loudness errors do.
- **Mix-space** (Wilson, Fazenda, Salford, 2015): representing gains in a
  relative space. Key fact: different engineers **converge on
  consistent** balance settings, even though the path depends on the starting point.
  So a target balance exists and can be evaluated numerically.

## 3. What is known about hearing and audibility

- **Partial loudness in multitrack** (Ma, Reiss, Black, AES 53, 2014):
  a source's audibility = the increment in mix loudness when it is added.
- **PEASS** (Emiya, Vincent, Harlander, Hohmann, 2011): gammatone filters,
  per-frame decomposition of distortions, four components (OPS/IPS/APS/TPS);
  a metric for sources within a mixture, not for the mix as a whole.
- **The structure of masking by genre** (Cofer, Belmont 2019, 30 rock songs):
  guitars are the least masked, then vocals, bass, drums; the guitar↔vocal link is
  tight, drums↔bass weaker. The relationships are a property of the genre.
- **Spectrum and loudness** (Deruty, Pachet, Roy, JAES 2014, 55 rock multitracks):
  with good monitoring mixers aim at a typical spectral profile,
  with poor monitoring at track audibility; loudness is linearly related to brightness.

## 4. Datasets (if training becomes necessary)

MUSDB18 (4 stems), MedleyDB (instruments annotated), MoisesDB (many stems,
up to 11), Slakh2100 (synthetic with notes), Cambridge-MT (multitracks for
research), MixAssist (audio-language dialogues about mixing, 2025).

## 5. Effect probes and the limits of metrics

From the web survey on effects (`_analysis/RESEARCH-EFFECTS-WEB.md`, 117 references).

**Three classes — three probes.** The same test for every effect gives garbage:

| class | effects | probe | metrics |
|---|---|---|---|
| linear | reverb, delay, EQ, stereo | impulse / exp-sweep → deconvolution | RT60, EDT, C50/D50, Ts, echo density, IACC |
| statically non-linear | saturation, limiter | level-stepped sines, two-tone | THD, THD+N, IMD (CCIF/SMPTE), aliasing |
| time-dependent | compressor, sidechain, de-esser | step/burst, envelope | static curve, attack/release, ducking depth |

**Tools with confirmed licences**: ViSQOL (Apache-2.0, full-reference
MOS-LQO), CDPAM (MIT, a differentiable perceptual metric — usable as a loss),
auraloss (Apache-2.0, MR-STFT), FAD (MIT, distance to a corpus), dasp-pytorch
(Apache-2.0), Optuna (MIT).

**What does not work — and must not be built on**:
SI-SDR on reverb/delay (it penalises the delay that the effect itself introduces);
the null test on non-linear and time-dependent effects (the residual is uninterpretable);
PESQ/STOI on music (trained on speech); FAD on a single file (unstable);
"one metric = truth" (optimising a metric leads away from the sound).

**Parameter auto-tuning**: black-box search (Optuna/Nevergrad) over a metric on a
real render — the only path that measures our actual plugin.
Differentiable models (dasp-pytorch, FLAMO, DiffVox, DeepAFx-ST) are offline,
for understanding and as a source of gradient losses.

**Reference matching**: a single track overfits to the arrangement; the workable
scheme is a corpus and a distribution, with a single track used only for a blind A/B.

## 6. What we take from this

1. Roles and subgroups → balance in two stages (ELL inside, coordination between).
2. Audibility through partial loudness as the primary measure, loudness only for
   calibration.
3. Targets are a distribution of the corpus by role, not absolute numbers.
4. Incrementality: one edit → measurement → acceptance (matches both people
   and sequential blending).
5. Panning as the second lever after faders, when masking remains.
6. We test transferability on a held-out track, not on the one the model
   was built from.

## 7. What the literature does not have

- A single objective metric of mix quality; all are proxies, judged by
  listening tests.
- Predictions of how non-linear effects affect audibility without a measurement.
- A ready-made tool for "role-based balance for an arbitrary instrument set" —
  the closest is ELL + unmasking + corpus targets, which is what we are assembling.

## 7. Sources

- Martinez et al. Automatic music mixing with deep learning and out-of-domain
  data. arXiv 2208.11428, 2022. Code: sony/FxNorm-automix.
- Sai-Soum et al. Diff-MST: Differentiable Mixing Style Transfer. arXiv 2407.08889,
  2024. Code: sai-soum/Diff-MST.
- Sony Research. MEGAMI: Automatic Music Mixing Using a Generative Model of Effect
  Embeddings, 2024–2025. Code: SonyResearch/MEGAMI.
- Rethinking Automatic Music Mixing as Sequential Stem Blending. arXiv 2608.05506, 2026.
- J. Shi et al. Understanding Automatic Mixing: A Subtask-Oriented Analysis of
  Two-Stage Mixing System. ISMIR 2026.
- A. Wilson, B. Fazenda. Navigating the Mix-Space. Salford, 2015.
- Z. Ma, J. Reiss, M. Black. Partial Loudness in Multitrack Mixing. AES 53, 2014.
- A. Tom, J. Reiss, P. Depalle. Automatic mixing system for multitrack
  spatialization based on unmasking. AES 146, 2019.
- B. Cofer. Perceptual Masking between Stems in Manually-Mixed Multitrack Audio.
  Belmont, 2019.
- E. Deruty et al. Human-Made Rock Mixes … JAES 62(10), 2014.
- V. Emiya et al. The PEASS Toolkit, 2011.
- MixAssist: An Audio-Language Dataset for Co-Creative AI Assistance in Music
  Mixing. arXiv 2507.06329, 2025.
