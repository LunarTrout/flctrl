# Instrument balance: what is known and what we do

**English** · [Русский](DESIGN-BALANCE.md)

The document records **decisions** taken from the literature, not from intuition.
Every decision comes with a source and with a way to check it.

## 1. What the literature establishes

**Two mixing directives (Deruty, Pachet, Roy, JAES 62(10), 2014).**
On 55 rock multitracks: under good listening conditions mixers match the genre's
**typical spectral profile**; under poor ones they maximise the **audibility of each
track**. A track's loudness is linearly related to its spectral centroid (brightness).
Conclusion for us: balance cannot be described with a single axis. Two are needed — the
mix profile (shape) and track audibility (intelligibility).

**Masking between stems is structural (Cofer, Belmont University, 2019).**
30 rock songs, a cross-analytic loudness model, five bands:
guitars are the **least masked** stem in every band, then vocals, bass,
drums; the guitar↔vocal relationship is tight, drums↔bass is weaker.
Conclusion: the "who masks whom" relationships are a property of the genre, not of the
track; they can be measured on a corpus and used as a target.

**Two-stage mixing beats one-stage mixing (Shi et al., ISMIR 2026).**
Three listening experiments: splitting into within-group processing and
between-group coordination significantly improves the result; wrong grouping
noticeably damages the outcome, while loudness errors transfer less strongly. For
within-group balance **ELL — equal local loudness** is used, and no training is required.
Conclusion: first the balance within subgroups, then between subgroups. This also makes
the approach transferable: the grouping is set by roles, not by track names.

**Partial loudness as a measure of audibility (Ma, Reiss, Black, AES 53rd, 2014;
PEASS — Emiya, Vincent, Harlander, Hohmann, 2011).**
A source's audibility in the mix is computed through the loudness increment:
`N_partial = N(mix) − N(mix without the source)`, and PEASS builds four components on
the same basis (OPS/IPS/APS/TPS) through gammatone filters and a per-frame decomposition
of distortions.
Conclusion: our `flctrl/partial.py` follows a proven path; the alternative is to port
PEASS, but it is about evaluating separation, not about mixing.

**Type-specific targets are transferable (Martinez et al., 2022, FxNorm).**
Stems are normalised to the average loudness of **their own type**, and the model learns
the deviation from it; this made it possible to train automatic mixing on
"wet" data and get a result indistinguishable from a professional one.
Conclusion: targets are set by role/instrument type, not by absolute numbers.

## 2. Decisions for flctrl

1. **The unit of analysis is the role**, not the track: `drums / bass / guitar / keys /
   vocals / other` (`roles.py`). The role is determined from the name, then from the spectrum.
2. **The measure is audibility**, not loudness: `A = N_partial / N_solo` per frame and
   band (`partial.py`). Stem loudness remains only for calibration.
3. **Two stages**: first within a subgroup (equal local audibility),
   then between subgroups (genre profile + masking relationships).
4. **Targets are an ensemble, not a median.** The corpus is stored as rows (one
   vector per track), and the target for a project is computed as a weighted blend of the k
   nearest references with distance-based weights. The reason: the corpus contains different
   mixing schools, and the mean over all of them asks for edits that no single
   real mix wants. The answer always carries a list of sources and their weights — you can see
   who we are matching (`balance.targets_for`).
5. **Edits with a cap and a prediction**: dB is computed through the measured
   sensitivity `dN/dB`, not "by eye"; anything above the cap is clipped.
6. **Acceptance only by measurement**: edit → re-measure → accept/reject
   (`balance.check`, `judge`). A model without verification does not count as proof.
7. **Transferability** is tested honestly: the model is built on the corpus without one
   track and applied to the held-out one. If on the held-out track it asks for large
   edits, the model is bad, not the track.

## 3. The inverse problem: targets from the mix, gains are ours

Roles cannot be extracted from a stereo reference: separation by a neural network lies
more than the effect itself. That is why targets are taken **at the mix level** (28-band
profile, sharpness, crest, stereo), and the unknowns are the gains of our roles (`invmix.py`).

What matters for verification:

- With seven regions the problem is **underdetermined**: different balances give similar
  shares. Moving to 28 bands made the answer unique (test: recovered −3.5 against a
  true −3.0 and +3.5 against +4.0, distance 0.84 → 0.00).
- **The overall level is unidentifiable**: calibration normalises loudness, so
  any uniform shift of all gains gives the same result. This is not a defect —
  faders are relative, the differences are what matter.
- The depth of the descent matters: with 4 passes of 0.5 dB the solver ran into the
  ceiling and "did not find" the answer. Now it is 14 passes, cap 6 dB.

## 4. What we take from this

- There is no single objective criterion of a "good mix"; every metric is a proxy.
- The influence of non-linear effects (saturation, compression) on audibility is poorly
  predictable analytically — a measurement after the edit is required.
- Genre targets are sensitive to arrangement: density and the number of doubles
  change the distribution, so sampling comparable sections is mandatory.
- In dense material panning removes masking more strongly than faders
  (Tom, Reiss, Depalle, AES 146, 2019) — the order "fader, then pan"
  is justified, but the optimum depends on the material.

## 4. Sources

- E. Deruty, F. Pachet, P. Roy. Human-Made Rock Mixes Feature Tight Relations
  Between Spectrum and Loudness. JAES 62(10), 2014.
- B. H. Cofer. An Analysis of Perceptual Masking between Stems in Manually-Mixed
  Multitrack Audio using a Cross-Analytic Loudness Model. Belmont University, 2019.
- J. Shi, W. Hua, K. Xie, M. Li, Y. Liu, J. Reiss. Understanding Automatic
  Mixing: A Subtask-Oriented Analysis of Two-Stage Mixing System. ISMIR 2026.
- Z. Ma, J. Reiss, M. Black. Partial Loudness in Multitrack Mixing. AES 53rd, 2014.
- V. Emiya, E. Vincent, N. Harlander, V. Hohmann. The PEASS Toolkit, 2011.
- M. Martinez et al. Automatic music mixing with deep learning and out-of-domain
  data (FxNorm), 2022.
- A. Tom, J. Reiss, P. Depalle. An automatic mixing system for multitrack
  spatialization for stereo based on unmasking and best panning practices. AES 146, 2019.
- D. Moffat, M. Sandler. Approaches in Intelligent Music Production. 2019.
