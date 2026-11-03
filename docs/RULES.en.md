# Operational rules learned the hard way

Not preferences — things that cost crashed FL sessions and lost time. Apply them in every
new project.

---

## 1. Never write parameters in a queue

Every write is a message to FL's UI thread. A burst of dozens in a row, especially while the
transport is playing, kills the engine: a crash in `USER32` → `FLEngine+0x246F9E`, a null
read at `0x90`.

Use the batch helpers: transport stopped, ≥0.1 s between writes, autosave every four.

## 2. A Pro-Q band has four different "enabled" flags

23 parameters per band: `+0` Band Used (momentary button, reading always returns zero),
`+1` Enabled (the real switch), `+2` Frequency, `+3` Gain, `+4` Q, `+5` Shape, `+6` Slope.
Check a band's existence only via `+1`.

## 3. Split mixer tracks works with WAV only

With MP3/OGG/FLAC the checkbox is ignored. CLI render (`/R`) does **not** split — it writes a
single master; splitting is only available through the export dialog. FL interprets `/R` as
a folder.

## 4. An empty name in the export dialog renders nothing

FL walks the whole song and writes zero files. Do not pass a name — FL fills its own.

## 5. Synthetic clicks in export dialogs crash FL

DirectUI buttons ignore `BM_CLICK`, `SetFocus` does nothing, and keyboard events crash.
Leave the export dialog to a human; automate the CLI only.

## 6. A bus with no sources is silent

Muting "everything except the bus" also mutes the bus's sources. To measure a bus, keep its
sources alive.

## 7. Compare identical sections only

A stem measured at 180–210 s and a live capture at bar 35 are different parts of the song; a
2–4 point profile difference means exactly that, not a processing error.

## 8. Hearing lives in its own environment

`phonometry` exists only for 3.13; the main process (3.12) calls `hear_cli.py` as a
subprocess. Pick the interpreter by testing `import phonometry`, not by folder name.

## 9. Embeddings and style need torch

`describe` / `style` go through the interpreter that has torch — search for it rather than
hard-coding a path.

## 10. Fortin Nameless writes parameters but does not read them

`getParamValue` returns 0.5 for every knob, so copying "the value as is" produces zero.
Compute the normalised value from the range:

- percentages (Middle, Treble, Presence, MVC, Bass, Gain): `norm = percent / 100`;
- EQ (250/500/1k/2k/4k, ±12 dB): `norm = 0.5 + dB / 24`;
- cabinet distance: 1:1 (`0.15` = 0.15 m);
- cabinet level is non-linear: 0.0 → −40 dB, 0.05 → −14.9, 0.10 → −11.1, 0.15 → −8.6,
  0.20 → −6.7, 0.35 → −2.8, 0.45 → −0.8;
- cabinet microphone: 0.00 Dynamic 57, 0.10 Dynamic 421, 0.25 Dynamic 906, 0.45 Condenser
  184, 0.60 Ribbon 121, 0.80 Ribbon 160, 0.95 Custom IR.

Verify each write by reading the string back — otherwise it is easy to "copy" an amp into
silence.

## 11. Track capture requires song mode

In pattern mode FL plays the selected pattern instead of the song, and the capture comes back
silent. Capture switches to song mode and restores the previous mode afterwards.

## 12. The modal "Recording" window stops automation

`transport.record()` through the bridge raises the dialog and FL stops answering commands.
Fix: `PopupRec = 1` in `HKCU\Software\Image-Line\FL Studio 25\DSTITF`
(`live.suppress_popup`), otherwise the capture hangs.

## 13. Never unload the hook while the writer thread is inside it

Detaching the Frida script at that moment crashes FL. Hence the separate daemon
(`hook_daemon.py`) with one attachment per session and `quit` only after recording has
stopped and settled.

## 14. Do not capture on a working project without asking

Song-mode recording may insert an audio clip into the playlist ("auto-create audio clip").
Capture on a working project only with permission; a project copy is safer.

## 15. A save point before every risky step

Ctrl+S plus a copy of the `.flp` into `~/Backups/flctrl/` before a capture or a batch edit.
Undo history lives in memory and dies with the process; a file snapshot survives a crash.
