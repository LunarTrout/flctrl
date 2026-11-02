#!/usr/bin/env python3
"""fl_automation.py -- read automation clips out of a FL Studio .flp (read-only).

Reverse engineered against FL Studio 2025 (25.2.5 build 5319) and validated on
728 automation events across 180 projects (see _analysis/FLP-AUTOMATION.md).

EVENT
-----
``0xEA`` (234) = ``ChannelID.Automation`` (pyflp: ``DATA + 26``), a varint-sized
event that lives inside a channel block.  Exactly one per automation clip; the
channel's ``0x15`` (type) byte is ``5`` = ``ChannelType.Automation``.

PAYLOAD LAYOUT (little endian)
------------------------------
    0x00  u32   version        always 1
    0x04  i32   lfo_amount     always 64 in every sampled project
    0x08  u8    lfo_shape      0 (1 observed once)
    0x09  u16   ?              always 4
    0x0B  u16   ?              always 0
    0x0D  u32   ?              always 3
    0x11  u32   point_count    N
    0x15  point[N], 24 bytes each:
              +0x00  f64  dx       delta in ticks w.r.t. the previous point
                                   (point 0 is always 0.0 -> starts at tick 0)
              +0x08  f64  value    normalised 0..1, MAY exceed 1 (e.g. 1.3333)
              +0x10  f32  tension  curve shape, -1..1
              +0x14  u32  flags    0x00000000 / 0x02000000 / 0xFF000000 ...
    then   112-byte trailer   (channel defaults; byte-identical in all 728
                               events sampled, so it carries no per-clip data)

    payload length == 133 + 24 * point_count      (verified for all 728 events)

The point X axis is **ticks at the project PPQ** (96 here): 96 ticks = 1 beat.
The Y axis is normalised.  The real range comes from the automation channel's
own ``0xDB`` Levels event, where field0 = MIN and field1 = MAX of the driven
parameter in that parameter's native integer units:

* insert/channel Volume -> 0 .. 12800, where 12800 = 0.0 dB (linear in value,
  so ``resolved`` is exact;
* project Tempo -> 1250 .. 4250 native units, **not** linear in the normalised
  value (two projects with different tempos disprove a linear BPM mapping), so
  ``resolved`` there is indicative only.

TARGET
------
The clip's target is not stored as a separate pointer.  It is recoverable from
three mutually consistent sources, all used here:

* the channel index (block order, == the channel iid; also repeated in the
  ``0xE3`` link table as its second u16),
* the channel name, which FL writes as ``"<target> - <parameter>"`` when the
  clip is created from a parameter (``"Insert 5 - Volume"``, ``"Tempo"``),
* the ``0xDB`` Min/Max pair, which identifies the parameter family.

Placement in the arrangement is read from ``0xE9`` (ArrangementID.Playlist,
80-byte items in FL 24.2+) so a clip that is not on the timeline is reported as
unused.

Read-only: nothing here writes to the project.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for candidate in (_HERE, _HERE / "flpresearch"):
    if (candidate / "flp_events.py").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from flp_events import FLP, Event  # noqa: E402

ID_CHANNEL_NEW = 0x40
ID_CHANNEL_TYPE = 0x15
ID_CHANNEL_NAME = 0xCB
ID_LEVELS = 0xDB
ID_AUTOMATION = 0xEA
ID_INSERT_NAME = 0xCC
ID_PATTERN_CONTROLLERS = 0xDF
ID_PLAYLIST = 0xE9
ID_PLUGIN_WRAPPER = 0xD4

PLAYLIST_ITEM_SIZE = 80        # FL 24.2+ (32 up to 20.8, 60 for 20.99..24.1)
PLAYLIST_PATTERN_BASE = 20480  # item_index above this == pattern clip

CHANNEL_TYPE_AUTOMATION = 5

HEADER_LEN = 21
POINT_LEN = 24
TRAILER_LEN = 112
FLAT_SIZE = HEADER_LEN + TRAILER_LEN  # 133

DEFAULT_FLP = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"

#: Min/Max pairs seen for automation channels -> parameter family (name fallback).
RANGE_HINTS = {
    (0, 12800): "Volume",
    (1250, 4250): "Tempo",
    (5600, 7200): "Channel pitch",
}

#: Human note per parameter family.
PARAM_NOTES = {
    "Volume": "insert/channel volume, 12800 = 0.0 dB; linear in the normalised value",
    "Tempo": "project tempo, native units; NOT linear in the normalised value",
    "Mute / solo": "on/off switch on the 0..12800 scale: 0 = off, 12800 = on",
    "Channel pitch": "channel pitch in cents, 6400 = 0",
}


# --------------------------------------------------------------------- model

@dataclass
class Point:
    index: int
    tick: float
    value: float
    tension: float
    flags: int

    @property
    def beats(self) -> float:
        return self.tick / 96.0


@dataclass
class Automation:
    channel: int
    name: str
    param: str
    target: str
    minimum: int | None
    maximum: int | None
    range_note: str
    points: list[Point] = field(default_factory=list)
    offset: int = 0
    payload_len: int = 0
    declared_count: int = 0
    layout_ok: bool = True
    event: Event | None = None
    uses: list[dict] = field(default_factory=list)   # playlist placements

    @property
    def length_ticks(self) -> float:
        return self.points[-1].tick if self.points else 0.0

    def resolved(self, value: float) -> float | None:
        if self.minimum is None or self.maximum is None:
            return None
        return self.minimum + value * (self.maximum - self.minimum)


# ------------------------------------------------------------------ decoding

def decode_automation(payload: bytes) -> tuple[list[Point], int, bool]:
    """Return (points, declared_count, layout_ok)."""
    if len(payload) < HEADER_LEN:
        return [], 0, False
    count = struct.unpack_from("<I", payload, 17)[0]
    ok = len(payload) == FLAT_SIZE + POINT_LEN * count
    points: list[Point] = []
    tick = 0.0
    limit = min(count, max(0, (len(payload) - HEADER_LEN) // POINT_LEN))
    for i in range(limit):
        dx, value = struct.unpack_from("<dd", payload, HEADER_LEN + i * POINT_LEN)
        tension = struct.unpack_from("<f", payload, HEADER_LEN + i * POINT_LEN + 16)[0]
        flags = struct.unpack_from("<I", payload, HEADER_LEN + i * POINT_LEN + 20)[0]
        tick += dx
        points.append(Point(i, tick, value, tension, flags))
    return points, count, ok


def split_target(name: str, range_pair: tuple[int, int] | None = None
                 ) -> tuple[str, str]:
    """Split a clip name into (target, parameter).

    FL writes ``"<target> - <parameter>"`` for clips made from a parameter
    ("Insert 5 - Volume"), plain ``"Tempo"`` for global params, and appends
    ``" #2"`` when the name is already taken.  The Levels Min/Max pair is used
    as a tie-breaker when the name carries no parameter suffix.
    """
    hint = RANGE_HINTS.get(range_pair) if range_pair else None
    hint_param = hint or ""
    base = re.sub(r"\s*#\d+$", "", name or "").strip()
    if " - " in base:
        head, tail = base.rsplit(" - ", 1)
        return head.strip(), (tail.strip() or hint_param)
    if hint_param and base.lower() == hint_param.lower():
        return "project", hint_param
    return "", base or hint_param


def read_playlist_items(flp: FLP) -> dict[int, list[dict]]:
    """Channel-index -> playlist placements, from 0xE9 (ArrangementID.Playlist).

    Item = 80 bytes in FL 24.2+:
        pos u32, pattern_base u16 (const 20480), item_index u16, length u32,
        track_rvidx u16 (reversed: track 1 == 499), ...
    A channel clip has ``item_index <= pattern_base``; pattern clips store
    ``pattern.iid + 20480``, which is how the two are told apart.
    """
    out: dict[int, list[dict]] = {}
    for ev in flp.events():
        if ev.id != ID_PLAYLIST:
            continue
        payload = flp.payload(ev)
        if len(payload) % PLAYLIST_ITEM_SIZE:
            return {}          # older item size, not decoded here
        for off in range(0, len(payload), PLAYLIST_ITEM_SIZE):
            pos, base, item, length = struct.unpack_from("<IHHI", payload, off)
            track_rv = struct.unpack_from("<H", payload, off + 12)[0]
            if base != PLAYLIST_PATTERN_BASE or item > PLAYLIST_PATTERN_BASE:
                continue
            out.setdefault(item, []).append(
                {"track": 500 - track_rv, "position": pos, "length": length})
        return out
    return out


def read_automations(flp: FLP) -> list[Automation]:
    """Walk channel blocks and pull out every 0xEA automation clip."""
    blocks: list[dict] = []
    current: dict | None = None
    seen_inserts = False
    for ev in flp.events():
        if ev.id == ID_INSERT_NAME:
            seen_inserts = True
            current = None
            continue
        if seen_inserts:
            continue
        if ev.id == ID_CHANNEL_NEW:
            current = {"ids": {}}
            blocks.append(current)
        elif current is not None:
            current["ids"].setdefault(ev.id, []).append(ev)

    out: list[Automation] = []
    playlist = read_playlist_items(flp)
    for index, block in enumerate(blocks):
        autos = block["ids"].get(ID_AUTOMATION)
        if not autos:
            continue
        typ = block["ids"].get(ID_CHANNEL_TYPE)
        ch_type = flp.raw[typ[0].payload_off] if typ else None
        if ch_type is not None and ch_type != CHANNEL_TYPE_AUTOMATION:
            continue
        name_ev = block["ids"].get(ID_CHANNEL_NAME)
        name = flp.text(name_ev[0]) if name_ev else ""
        minimum = maximum = None
        note = ""
        levels = block["ids"].get(ID_LEVELS)
        if levels:
            minimum, maximum = struct.unpack_from("<ii", flp.payload(levels[0]), 0)
            note = PARAM_NOTES.get(RANGE_HINTS.get((minimum, maximum), ""), "")
        target, param = split_target(
            name, (minimum, maximum) if minimum is not None and maximum is not None else None)
        if not note:
            note = PARAM_NOTES.get(param, "")

        ev = autos[0]
        payload = flp.payload(ev)
        points, count, ok = decode_automation(payload)
        out.append(Automation(
            channel=index, name=name, param=param,
            target=target or ("project" if param == "Tempo" else ""),
            minimum=minimum, maximum=maximum, range_note=note,
            points=points, offset=ev.offset, payload_len=len(payload),
            declared_count=count, layout_ok=ok, event=ev,
            uses=playlist.get(index, []),
        ))
    return out


def pattern_controller_count(flp: FLP) -> tuple[int, int]:
    """Records / events of 0xDF (pattern-level knob automation)."""
    events = records = 0
    for ev in flp.events():
        if ev.id == ID_PATTERN_CONTROLLERS:
            events += 1
            records += ev.length // 12
    return events, records


# ----------------------------------------------------------------- printing

def bar_beat_tick(tick: float, ppq: int) -> str:
    bar_len = ppq * 4
    bar, rem = divmod(max(tick, 0.0), bar_len)
    beat, t = divmod(rem, ppq)
    return f"{int(bar) + 1}:{int(beat) + 1}:{t:06.2f}"


def fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.3f}"


def cmd_list(flp: FLP, autos: list[Automation], as_json: bool) -> int:
    if as_json:
        print(json.dumps([{
            "channel": a.channel, "name": a.name, "target": a.target,
            "param": a.param, "points": len(a.points),
            "length_ticks": a.length_ticks, "min": a.minimum, "max": a.maximum,
            "offset": a.offset, "payload_len": a.payload_len, "layout_ok": a.layout_ok,
        } for a in autos], indent=2, ensure_ascii=False))
        return 0
    if not autos:
        print(f"{flp.path}: no automation clips (no 0xEA event).")
        print("Run `census` to prove the event id is absent from the stream.")
        return 0
    print(f"{flp.path}")
    print(f"PPQ {flp.ppq}   channels {flp.channels}   automation clips {len(autos)}\n")
    print(f"{'#':>3} {'chan':>4} {'points':>6} {'length':>9} {'items':>5}  {'target':<26} "
          f"{'param':<8} {'range':<15} name")
    print("-" * 124)
    for i, a in enumerate(autos):
        rng = f"{a.minimum}..{a.maximum}" if a.minimum is not None else "-"
        target = a.target or "-"
        print(f"{i:>3} {a.channel:>4} {len(a.points):>6} "
              f"{fmt_num(a.length_ticks):>9} {len(a.uses):>5}  {target[:26]:<26} {a.param[:8]:<8} "
              f"{rng:<15} {a.name}")
    return 0


def cmd_show(flp: FLP, autos: list[Automation], needle: str, as_json: bool,
             csv: bool) -> int:
    if needle.isdigit():
        chosen = [a for a in autos if a.channel == int(needle)]
    else:
        q = needle.lower()
        chosen = [a for a in autos if q in a.name.lower()
                  or q in a.param.lower() or q in a.target.lower()]
    if not chosen:
        print(f"no automation clip matches {needle!r}", file=sys.stderr)
        if autos:
            print("available: " + ", ".join(f"{a.channel}:{a.name}" for a in autos),
                  file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps([{
            "channel": a.channel, "name": a.name, "target": a.target, "param": a.param,
            "min": a.minimum, "max": a.maximum, "ppq": flp.ppq,
            "points": [{"i": p.index, "tick": p.tick, "beat": p.tick / flp.ppq,
                        "value": p.value, "resolved": a.resolved(p.value),
                        "tension": p.tension, "flags": p.flags} for p in a.points],
        } for a in chosen], indent=2, ensure_ascii=False))
        return 0

    for a in chosen:
        rng = f"{a.minimum} .. {a.maximum}" if a.minimum is not None else "unknown"
        print(f"\nchannel {a.channel}  {a.name!r}")
        print(f"  target {a.target or '-'}   param {a.param or '-'}   range {rng}"
              + (f"  ({a.range_note})" if a.range_note else ""))
        print(f"  points {len(a.points)}   length {fmt_num(a.length_ticks)} ticks "
              f"= {a.length_ticks / flp.ppq:.3f} beats   event@0x{a.offset:x} "
              f"payload {a.payload_len}B  layout {'OK' if a.layout_ok else 'MISMATCH'}")
        if csv:
            print("  i,tick,beat,bar:beat:tick,value,resolved,tension")
            for p in a.points:
                print(f"  {p.index},{p.tick:.4f},{p.tick / flp.ppq:.4f},"
                      f"{bar_beat_tick(p.tick, flp.ppq)},{p.value:.6f},"
                      f"{fmt_num(a.resolved(p.value))},{p.tension:.4f}")
            continue
        print(f"  {'i':>3} {'tick':>9} {'bar:beat:tick':>15} {'beats':>8} "
              f"{'value':>9} {'resolved':>9} {'tension':>8}")
        for p in a.points:
            print(f"  {p.index:>3} {p.tick:>9.2f} {bar_beat_tick(p.tick, flp.ppq):>15} "
                  f"{p.tick / flp.ppq:>8.3f} {p.value:>9.4f} "
                  f"{fmt_num(a.resolved(p.value)):>9} {p.tension:>8.3f}")
        print("  value = normalised Y (0..1, can exceed 1); "
              "resolved = min + value*(max-min) in native units")
        if len(a.points) > 1:
            values = [p.value for p in a.points]
            span = max(values) - min(values)
            kind = "flat (no movement)" if span < 1e-6 else f"moving, span {span:.4f}"
            print(f"  -> {kind}")
        if a.uses:
            print(f"  placed on the timeline {len(a.uses)}x:")
            for use in a.uses:
                print(f"     track {use['track']:>2}  @ tick {use['position']:>7} "
                      f"({use['position'] / flp.ppq:8.3f} beats)  length {use['length']} ticks")
        else:
            print("  placed on the timeline: no (not used anywhere)")
    return 0


def cmd_summary(flp: FLP, autos: list[Automation], as_json: bool) -> int:
    ev_events, ev_records = pattern_controller_count(flp)
    total_points = sum(len(a.points) for a in autos)
    bad = [a for a in autos if not a.layout_ok]
    data = {
        "file": flp.path, "ppq": flp.ppq, "channels": flp.channels,
        "automation_clips": len(autos), "points": total_points,
        "by_target": {}, "clips": [], "pattern_controllers": {
            "events": ev_events, "records": ev_records},
        "layout_mismatches": len(bad),
    }
    for a in autos:
        key = f"{a.target or '-'} / {a.param or '-'}"
        data["by_target"].setdefault(key, {"clips": 0, "points": 0})
        data["by_target"][key]["clips"] += 1
        data["by_target"][key]["points"] += len(a.points)
        data["clips"].append({"channel": a.channel, "name": a.name,
                              "points": len(a.points), "length_ticks": a.length_ticks,
                              "playlist_items": len(a.uses)})
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"file        : {flp.path}")
    print(f"PPQ         : {flp.ppq}   channels: {flp.channels}")
    print(f"automation  : {len(autos)} clip(s), {total_points} point(s)   "
          f"(0xEA events, layout mismatches: {len(bad)})")
    if autos:
        print(f"targets     :")
        for key, agg in sorted(data["by_target"].items(), key=lambda kv: -kv[1]["clips"]):
            print(f"   {key:<34} {agg['clips']:>3} clip(s) {agg['points']:>4} points")
        lengths = [a.length_ticks for a in autos]
        print(f"length      : min {min(lengths):.2f} / max {max(lengths):.2f} ticks "
              f"({min(lengths) / flp.ppq:.2f}..{max(lengths) / flp.ppq:.2f} beats)")
        moving = [a for a in autos
                  if len({round(p.value, 9) for p in a.points}) > 1]
        placed = [a for a in autos if a.uses]
        print(f"moving      : {len(moving)} of {len(autos)} clip(s) actually change value")
        print(f"placed      : {len(placed)} of {len(autos)} clip(s) appear in the playlist "
              f"({sum(len(a.uses) for a in autos)} item(s))")
    else:
        print("            : this project contains no automation clips")
    print(f"0xDF ctrl   : {ev_events} pattern-controller event(s), {ev_records} record(s) "
          f"(pattern-level knob automation, 12B records)")
    return 0


def cmd_census(flp: FLP) -> int:
    from collections import Counter
    counts: Counter[int] = Counter()
    sizes: dict[int, Counter] = {}
    payload: Counter[int] = Counter()
    for ev in flp.events():
        counts[ev.id] += 1
        sizes.setdefault(ev.id, Counter())[ev.length] += 1
        payload[ev.id] += ev.length
    print(f"{flp.path}  ({len(flp.raw):,} bytes, PPQ {flp.ppq})")
    print(f"{'id':>6} {'count':>7} {'payload':>11}  sizes")
    print("-" * 72)
    for eid in sorted(counts, key=lambda k: -counts[k]):
        sz = " ".join(f"{s}:{c}" for s, c in sizes[eid].most_common(4))
        mark = "  <-- automation" if eid == ID_AUTOMATION else ""
        print(f"0x{eid:02X} {counts[eid]:>7} {payload[eid]:>11,}  {sz}{mark}")
    n = counts.get(ID_AUTOMATION, 0)
    print(f"\n0xEA automation events: {n}")
    return 0


# --------------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="fl_automation.py",
        description="Read FL Studio automation clips (.flp), read-only.")
    ap.add_argument("command", choices=["list", "show", "summary", "census"])
    ap.add_argument("target", nargs="?", default=None,
                    help="show: channel index, clip name, target or parameter")
    ap.add_argument("--flp", default=DEFAULT_FLP, help="path to the .flp")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--csv", action="store_true", help="show: CSV point table")
    args = ap.parse_args(argv[1:])

    path = Path(args.flp)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 2
    flp = FLP.load(path)

    if args.command == "census":
        return cmd_census(flp)

    autos = read_automations(flp)
    if args.command == "list":
        return cmd_list(flp, autos, args.json)
    if args.command == "summary":
        return cmd_summary(flp, autos, args.json)
    if args.command == "show":
        if args.target is None:
            print("show needs a target (channel index or name substring)", file=sys.stderr)
            return 2
        return cmd_show(flp, autos, args.target, args.json, args.csv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
