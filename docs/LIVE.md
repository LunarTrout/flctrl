# Live capture: how per-track audio is obtained

**English** · [Русский](LIVE.ru.md)

The problem: FL Studio has no API that returns the audio of a mixer track. Meters give
envelopes, the song renders as a single file, and stems require a manual export option.

The solution uses the one place where FL itself emits per-track PCM: **Disk Recording**.
Each armed mixer track writes its own WAV. If you intercept or read that stream, you have
the track's audio — after its processing, before the master.

---

## 1. What FL provides

- Arm a mixer track: `mixer.armTrack(index)` (toggles), `mixer.isTrackArmed(index)`.
- Record: `transport.record()` toggles; `transport.isRecording()` reads.
- Playback: `transport.start()`, `transport.setSongPos(position)`, `transport.stop()`.
- The recorded files land in `…/Documents/Image-Line/FL Studio/Audio/Recorded`.
- A modal **Recording** dialog appears on the first Record press unless suppressed;
  `HKCU\Software\Image-Line\FL Studio 25\DSTITF\PopupRec = 1` disables it
  (`live.suppress_popup`).

Two facts that cost time to learn:

1. **Song mode is mandatory.** In pattern mode FL plays the selected pattern, not the song,
   and the capture comes back silent.
2. **Recording is realtime.** Two seconds of music take two seconds; there is no faster
   path for a live take (offline render is a different mechanism).

---

## 2. Mode `memory` (default, zero disk)

FL writes PCM with `WriteFile` from `FLEngine_x64.dll+0x3b5fa`. The payload is raw float32
stereo; the first write of a file carries a 4-byte prefix, the rest are contiguous PCM. We
verified byte-for-byte equality between the captured buffer and the resulting WAV payload
(59 392 bytes matched exactly).

The hook:

- `CreateFileW` → remembers handles whose path matches `Recorded/*.wav`;
- `WriteFile` → for those handles, copies the buffer into a Python-side list and sets the
  write length to zero, so FL believes the write succeeded and only a 76-byte header
  reaches the disk.

Because unloading a Frida script while FL's writer thread is inside the hook can crash FL,
the hook lives in a **separate long-running daemon** (`tools-local/hook_daemon.py`,
`127.0.0.1:9788`):

- one attach per FL session;
- commands `ping`, `start`, `stop`, `quit` over a length-prefixed JSON socket;
- audio is transferred compressed (`zlib` + base64) because the whole track list as raw JSON exceeded
  the timeout;
- `ensure_daemon()` starts it on demand and re-attaches if FL's PID changed.

This mode is the default: it writes nothing but headers, which `live.clean()` removes. It was
less stable than `files` on FL 25.2.5, so `capture()` falls back to `files` if the daemon is
not up.

---

## 3. Mode `files` (fallback)

```
save point  →  Ctrl+S + copy .flp
arm         →  mixer.armTrack for the requested tracks
record on   →  transport.record()
play        →  setSongPos(start); start()
wait        →  seconds
stop        →  stop(); record() off
read        →  new files in Recorded → soundfile → tape
cleanup     →  delete those files
restore     →  disarm what we armed; restore song/pattern mode
```

Properties:

- disk is touched only for the duration of the take, then emptied;
- FL is not restarted; the project is not reloaded;
- armed state and mode are restored even on exceptions (`try/finally`);
- verified: every track of the test project (29 at the time) in one take, `Recorded` empty
  afterwards; the list comes from `mixer.getTrackCount()`, so the count is whatever the
  project has;

The playlist is not touched — see section 4. `files` is the fallback: `capture()` uses it
automatically when the hook daemon is not reachable, and says so in `mode_used`.

## 4. The playlist stays untouched

FL's Mixer → Disk Recording → **Auto-create audio clip** is on by default: every recorded
take becomes an audio clip in the playlist, and stacked takes play on top of each other —
this is what made the project jump in level in one spot.

Two details matter, both measured on a live project:

1. FL reads `AutoCreateClip` (registry, `HKCU\\Software\\Image-Line\\FL Studio 25`) **at
   record time**, so it can be switched off just for the take.
2. The clip itself is materialised **when the project is saved**, not at record time. Clearing
   the flag for the take and restoring it before the save still produced clips; the take has
   to be discarded from the undo history.

So `capture()` does three things, for both modes:

```
flag = AutoCreateClip            # usually 1
AutoCreateClip := 0
...  take: arm, record, play, read PCM, stop, disarm
general.undoUpDown(-1)           # drop the recording step
AutoCreateClip := flag           # user's own recording keeps creating clips
```

The result reports `clips: {before, during, after}`, `take_discarded` and `clips_clean`.
Verified on `vers1`: two tracks captured (DRUMS SD3 rms 0.052, BASS rms 0.106), the `Recorded`
folder unchanged, and after saving the project the number of clips recorded that day stayed
at zero.

## 5. Safety

**Save points.** Before every capture: `Ctrl+S` in FL (the window is brought to the
foreground; if it cannot be, the save is refused rather than typing into another window),
then a copy of the `.flp` into `~/Backups/flctrl/`. Identical states are skipped by SHA-1
and the oldest points are pruned (20 by default). If the save fails, the newest FL autosave
is copied instead and the result says `origin: "autosave"`.

**State restoration.** The capture records the previous mode and armed set, and restores
both in `finally`, including when the bridge drops mid-take.

**Bounded waits.** No `sleep` loops over unknown conditions: playback is time-bounded by
design, the presence of new files is checked once, and the bridge calls have timeouts.

---

## 6. Failure modes seen in practice

| Symptom | Cause | Fix |
|---|---|---|
| Capture returns silence | pattern mode, or `start` outside the arrangement | song mode; check `start` |
| FL blocks on a modal dialog | `PopupRec = 0` | `live.suppress_popup(True)` (automatic) |
| Daemon does not start | stale daemon bound to a dead PID | `ensure_daemon()` quits it and re-attaches |
| FL disappears during a take | unloading the hook while the writer thread is inside it | use `files`, or keep the daemon attached |
| Files left in `Recorded` | process killed mid-take | `live.clean()` removes headers |

---

## 7. What is verified, and what is not

Verified on FL 25.2.5 (build 5319):

- all 29 tracks of the test project captured in one take, one WAV per track, correct names;
- captured tracks are not copies of the master (pairwise correlation: GTR L1 ↔ GTR R1
  0.046; maximum 0.614 for DRUM BUS ↔ DRUMS SD3);
- `Recorded` empty after capture; FL alive; project unchanged except restored armed state;
- save points created from a real Ctrl+S (`origin: "save"`), deduplicated by SHA-1;
- `memory` mode: file stays at 76 bytes while 712 784 bytes of PCM are captured.

Not verified:

- captures longer than a few seconds; the row is bounded by the take length;
- long takes (> 30 s) — untested beyond a few seconds;
- any FL version other than 25.2.5.
