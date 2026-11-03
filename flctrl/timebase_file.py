"""timebase_file.py -- .flp side of the time lane: exact time, tempo, playlist, notes.

Read-only parser built on ``flp_events.FLP``; no FL process involved.  It is the
reference the live (memory) reader is checked against, and the fallback when FL
is closed.

Layouts (verified against FL 25.2.5 build 5319, demo2.flp; see
docs/RE/time/FINDINGS.md):

* tempo            0x9C (156) u32 = bpm * 1000
* time signature   0x11 (17) u8 numerator, 0x12 (18) u8 denominator
* pattern header   0x41 (65) u16 pattern id, 0xC1 (193) name, 0xA4 (164) length
* pattern notes    0xE0 (224), 24-byte records
* playlist         0xE9 (233), 80-byte records
* channel          0x40 (64) new, 0x4B? name 0xCB, sample path 0xC4
* time markers     0x94 (148) u32 (bit 27 = signature), 0x21/0x22 num/den
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE / "flpresearch"):
    if (_cand / "flp_events.py").exists() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

from flp_events import FLP  # noqa: E402

ID_TEMPO = 0x9C
ID_TS_NUM = 0x11
ID_TS_DEN = 0x12
ID_PATTERN_NEW = 0x41
ID_PATTERN_NAME = 0xC1
ID_PATTERN_LENGTH = 0xA4
ID_NOTES = 0xE0
ID_PLAYLIST = 0xE9
ID_CHANNEL_NEW = 0x40
ID_CHANNEL_NAME = 0xCB
ID_SAMPLE_PATH = 0xC4
ID_TRACK_DATA = 0xEE
ID_MARKER = 0x94
ID_MARKER_NAME = 0xCD
ID_MARKER_NUM = 0x21
ID_MARKER_DEN = 0x22

PATTERN_BASE = 20480
CLIP_RECORD = 80


@dataclass
class Channel:
    index: int
    name: str = ""
    sample: str = ""
    kind: int | None = None


@dataclass
class Pattern:
    index: int
    name: str = ""
    length: int = 0


@dataclass
class Clip:
    track: int
    pos: int                 # ticks
    length: int              # ticks
    item: int                # channel index, or 20480 + pattern index
    start_offset: float      # float32 from file (seconds for audio clips)
    end_offset: float
    clip_id: int
    flags: int
    group: int

    @property
    def is_pattern(self) -> bool:
        return self.item > PATTERN_BASE

    @property
    def pattern(self) -> int | None:
        return self.item - PATTERN_BASE if self.is_pattern else None

    @property
    def channel(self) -> int | None:
        return None if self.is_pattern else self.item


@dataclass
class Note:
    pattern: int
    pos: int
    length: int
    key: int
    velocity: int
    channel: int
    pan: int = 64
    fine_pitch: int = 120


@dataclass
class Project:
    path: str
    ppq: int
    tempo: float
    ts_num: int
    ts_den: int
    channels: list[Channel] = field(default_factory=list)
    patterns: dict[int, Pattern] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    markers: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------------- queries
    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.tempo

    def beats_at(self, seconds: float) -> float:
        return seconds / self.beat_seconds

    def ticks_at(self, seconds: float) -> float:
        return self.beats_at(seconds) * self.ppq

    def seconds_at(self, ticks: float) -> float:
        return ticks / self.ppq * self.beat_seconds

    def clips_at_track(self, track: int) -> list[Clip]:
        return [c for c in self.clips if c.track == track]

    def tracks(self) -> list[int]:
        return sorted({c.track for c in self.clips})

    def active_at(self, ticks: float) -> list[dict]:
        """Every clip that sounds at ``ticks`` (clip-local time included)."""
        out = []
        for c in self.clips:
            if c.pos <= ticks < c.pos + c.length:
                local_ticks = ticks - c.pos
                entry = {
                    "track": c.track,
                    "clip_id": c.clip_id,
                    "pos": c.pos,
                    "length": c.length,
                    "local_ticks": local_ticks,
                    "local_seconds": self.seconds_at(local_ticks),
                    "pattern": c.pattern,
                    "channel": c.channel,
                }
                if c.is_pattern:
                    pat = self.patterns.get(c.pattern)
                    entry["pattern_name"] = pat.name if pat else ""
                    entry["notes"] = [
                        n for n in self.notes
                        if n.pattern == c.pattern
                        and n.pos <= local_ticks < n.pos + n.length
                    ]
                else:
                    if c.channel is not None and c.channel < len(self.channels):
                        ch = self.channels[c.channel]
                        entry["channel_name"] = ch.name
                        entry["sample"] = ch.sample
                    # local time inside the sample, seconds
                    entry["sample_offset"] = (c.start_offset or 0.0) + self.seconds_at(local_ticks)
                out.append(entry)
        return out


def _text(flp: FLP, ev) -> str:
    try:
        return flp.text(ev)
    except Exception:
        return ""


def load(path: str | Path) -> Project:
    flp = FLP.load(str(path))
    project = Project(path=str(path), ppq=flp.ppq, tempo=0.0, ts_num=4, ts_den=4)

    cur_pattern: int | None = None
    cur_channel: Channel | None = None
    notes_by_pattern: dict[int, list[Note]] = {}

    for ev in flp.events():
        if ev.id == ID_TEMPO:
            project.tempo = flp.u32(ev) / 1000.0
        elif ev.id == ID_TS_NUM:
            project.ts_num = flp.raw[ev.payload_off]
        elif ev.id == ID_TS_DEN:
            project.ts_den = flp.raw[ev.payload_off]
        elif ev.id == ID_PATTERN_NEW:
            cur_pattern = flp.u16(ev)
            project.patterns.setdefault(cur_pattern, Pattern(cur_pattern))
        elif ev.id == ID_PATTERN_NAME:
            if cur_pattern is not None:
                project.patterns[cur_pattern].name = _text(flp, ev)
        elif ev.id == ID_PATTERN_LENGTH:
            if cur_pattern is not None:
                project.patterns[cur_pattern].length = flp.u32(ev)
        elif ev.id == ID_NOTES:
            if cur_pattern is None:
                continue
            payload = flp.payload(ev)
            bucket = notes_by_pattern.setdefault(cur_pattern, [])
            for off in range(0, len(payload) - 23, 24):
                pos, flags, chan, ln, key, group, fine, _r, rel, midi, pan, vel, mx, my = \
                    struct.unpack_from("<IHHIHHBBBBBBBB", payload, off)
                bucket.append(Note(pattern=cur_pattern, pos=pos, length=ln, key=key,
                                   velocity=vel, channel=chan, pan=pan, fine_pitch=fine))
        elif ev.id == ID_CHANNEL_NEW:
            cur_channel = Channel(index=len(project.channels))
            project.channels.append(cur_channel)
        elif ev.id == ID_CHANNEL_NAME and cur_channel is not None:
            cur_channel.name = _text(flp, ev)
        elif ev.id == ID_SAMPLE_PATH and cur_channel is not None:
            cur_channel.sample = _text(flp, ev)
        elif ev.id == ID_PLAYLIST:
            payload = flp.payload(ev)
            if len(payload) % CLIP_RECORD:
                continue
            for off in range(0, len(payload), CLIP_RECORD):
                pos, base, item, length = struct.unpack_from("<IHHI", payload, off)
                track_rv, group, _u1, flags = struct.unpack_from("<HHHH", payload, off + 12)
                start_off, end_off = struct.unpack_from("<ff", payload, off + 24)
                clip_id = struct.unpack_from("<I", payload, off + 32)[0]
                if base != PATTERN_BASE or item > PATTERN_BASE + 512:
                    continue
                project.clips.append(Clip(
                    track=500 - track_rv, pos=pos, length=length, item=item,
                    start_offset=start_off, end_offset=end_off,
                    clip_id=clip_id, flags=flags, group=group))
        elif ev.id == ID_MARKER:
            raw = flp.u32(ev)
            project.markers.append({"pos": raw & 0x07FFFFFF,
                                    "signature": bool(raw & 0x08000000),
                                    "num": None, "den": None, "name": ""})

    for pat, bucket in notes_by_pattern.items():
        project.notes.extend(bucket)
        if pat not in project.patterns:
            project.patterns[pat] = Pattern(pat)
    return project


if __name__ == "__main__":
    import json
    p = load(sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/epicc/tools/fltest/fixtures/demo2.flp")
    print(f"ppq={p.ppq} tempo={p.tempo} ts={p.ts_num}/{p.ts_den} "
          f"channels={len(p.channels)} patterns={len(p.patterns)} clips={len(p.clips)} notes={len(p.notes)}")
    print("tracks with clips:", len(p.tracks()), "range", min(p.tracks()), "..", max(p.tracks()))
    audio = [c for c in p.clips if not c.is_pattern]
    print(f"audio clips={len(audio)} pattern clips={len(p.clips) - len(audio)}")
    for c in audio[:3]:
        ch = p.channels[c.channel] if c.channel is not None and c.channel < len(p.channels) else None
        print(" audio", c.track, c.pos, c.length, c.start_offset, c.end_offset,
              ch.name if ch else "?", (ch.sample if ch else "")[-40:])
    print("notes sample:", json.dumps([n.__dict__ for n in p.notes[:3]]))
