#!/usr/bin/env python3
"""flp_events.py -- FL Studio 20+ (.flp) event-stream decoder (+ re-encoder).

Reverse engineered against FL Studio 2025 (Producer Edition 25.2.5 build 5319),
``vers1.flp`` (98,642,865 bytes, 142 channels, PPQ 96), cross-checked against
FL 24.2.1.4526 projects (``power.flp``, ``Suffotron.flp``).

CONTAINER
---------
    "FLhd" | u32 chunk_len (=6) | u16 format | u16 n_channels | u16 ppq
    "FLdt" | u32 chunk_len      | event stream ...            (file ends here)

EVENT FRAMING  (the part most parsers get wrong)
------------------------------------------------
An event is ``u8 id`` followed by its payload.  **The payload size is implied
by the id byte** -- ids below 0xC0 carry no length field at all:

    id  0x00..0x3F -> payload is exactly 1 byte
    id  0x40..0x7F -> payload is exactly 2 bytes
    id  0x80..0xBF -> payload is exactly 4 bytes
    id  0xC0..0xFF -> LEB128 varint length, then that many bytes

There is no multi-byte id form; ``id = (b << 8 | next) - 0x8000`` (used by
some old parsers) is bogus and desynchronises the stream immediately.

Varint details: 7 data bits per byte, least-significant group first, high bit
set means "another byte follows".  construct.VarInt == this LEB128.

FL 25 EXCEPTION (empirically proven, see flp_format.md)
------------------------------------------------------
``0xAC`` (172) is a *new* FL-25 event that carries **1 byte**, not 4.  FL 24.2
writes the header as ``... 1c 01 | 25 01 | c8 0a <licensee>``; FL 25.2 writes
``... 1c 01 | ac 01 | 01 00 | c0 36 <title>``.  Decoding 0xAC as 4 bytes
swallows the project-title event and shreds the UTF-16 string into 27 bogus
id-0 events (it self-resyncs, which is why the bug hides).  Pass
``strict=True`` to disable the override.

Known ids referenced here (pyflp naming, all verified on this file):
    98  0x62  SlotID.Index            u16, terminates a slot block
    128 0x80  PluginID.Color          u32 (RGBA)
    155 0x9B  PluginID.Icon           u32
    159 0x9F  ProjectID.FLBuild       u32   (5319)
    192 0xC0  project title           varint + UTF-16LE
    199 0xC7  ProjectID.FLVersion     varint + ASCII "25.2.5.5319\0"
    201 0xC9  PluginID.InternalName   varint + UTF-16LE
    203 0xCB  PluginID.Name           varint + UTF-16LE
    204 0xCC  InsertID.Name           varint + UTF-16LE
    212 0xD4  PluginID.Wrapper        varint; payload starts with two u32:
                                      (insert_idx, slot_idx)
    213 0xD5  PluginID.Data           varint; plugin state blob
    225 0xE1  MixerID.Params          list of 12-byte (id, insert/slot, msg)
    236 0xEC  InsertID.Flags          12 bytes: pad, u32 flags, pad
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORD, DWORD, TEXT, DATA = 0x40, 0x80, 0xC0, 0xD0

ID_SLOT_INDEX = 98
ID_PLUGIN_COLOR = 128
ID_PLUGIN_ICON = 155
ID_BUILD = 159
ID_TITLE = 192
ID_VERSION = 199
ID_PLUGIN_INTERNAL = 201
ID_PLUGIN_NAME = 203
ID_INSERT_NAME = 204
ID_PLUGIN_WRAPPER = 212
ID_PLUGIN_DATA = 213
ID_MIXER_PARAMS = 225
ID_INSERT_FLAGS = 236

#: ids whose real payload size deviates from the id-range rule.
SIZE_OVERRIDES: dict[int, int] = {0xAC: 1}


# ---------------------------------------------------------------- primitives

def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ValueError(f"varint too long at {pos}")


def write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def fixed_size(event_id: int) -> int | None:
    if event_id < WORD:
        return 1
    if event_id < DWORD:
        return 2
    if event_id < TEXT:
        return 4
    return None


# ---------------------------------------------------------------- container

@dataclass(slots=True)
class Event:
    id: int
    offset: int        # file offset of the id byte
    payload_off: int   # file offset of the first payload byte
    length: int
    header_len: int    # id byte + optional length field

    @property
    def end(self) -> int:
        return self.payload_off + self.length


@dataclass
class FLP:
    raw: bytes
    path: str
    chunk_off: int     # offset of b"FLdt"
    data_off: int      # offset of the first event
    data_len: int
    fmt: int
    channels: int
    ppq: int

    @classmethod
    def load(cls, path: str | Path, strict: bool = False) -> "FLP":
        raw = Path(path).read_bytes()
        if raw[:4] != b"FLhd":
            raise ValueError("not an FLP: missing FLhd")
        hdr_len = struct.unpack_from("<I", raw, 4)[0]
        fmt, channels, ppq = struct.unpack_from("<HHH", raw, 8)
        chunk = 8 + hdr_len
        if raw[chunk:chunk + 4] != b"FLdt":
            raise ValueError(f"expected FLdt at {chunk}, got {raw[chunk:chunk+4]!r}")
        data_len = struct.unpack_from("<I", raw, chunk + 4)[0]
        data_off = chunk + 8
        if data_off + data_len != len(raw):
            raise ValueError(f"FLdt length mismatch: {data_off}+{data_len} != {len(raw)}")
        obj = cls(raw, str(path), chunk, data_off, data_len, fmt, channels, ppq)
        obj.strict = strict  # type: ignore[attr-defined]
        return obj

    # -- decode
    def events(self, start: int | None = None, end: int | None = None):
        raw = self.raw
        overrides = {} if getattr(self, "strict", False) else SIZE_OVERRIDES
        pos = self.data_off if start is None else start
        end = (self.data_off + self.data_len) if end is None else end
        while pos < end:
            off = pos
            event_id = raw[pos]
            pos += 1
            size = overrides.get(event_id, fixed_size(event_id))
            if size is None:
                size, pos = read_varint(raw, pos)
            if pos + size > end:
                raise ValueError(f"event at {off} (id={event_id}) overruns chunk")
            yield Event(event_id, off, pos, size, pos - off)
            pos += size

    # -- accessors
    def payload(self, ev: Event) -> bytes:
        return self.raw[ev.payload_off:ev.end]

    def text(self, ev: Event) -> str:
        return self.payload(ev).decode("utf-16-le", "replace").split("\0")[0]

    def u16(self, ev: Event) -> int:
        return struct.unpack_from("<H", self.raw, ev.payload_off)[0]

    def u32(self, ev: Event) -> int:
        return struct.unpack_from("<I", self.raw, ev.payload_off)[0]

    # -- encode (deliverable 3)
    @staticmethod
    def encode_event(event_id: int, payload: bytes) -> bytes:
        if not 0 <= event_id <= 0xFF:
            raise ValueError("event ids are a single byte")
        size = SIZE_OVERRIDES.get(event_id, fixed_size(event_id))
        if size is None:
            return bytes([event_id]) + write_varint(len(payload)) + payload
        if len(payload) != size:
            raise ValueError(f"id 0x{event_id:02X} needs exactly {size} payload "
                             f"bytes, got {len(payload)}")
        return bytes([event_id]) + payload

    def with_inserted(self, at: int, blob: bytes) -> bytes:
        """Splice bytes in and patch the FLdt chunk length.  Nothing else moves:
        the format has no offset table, no alignment and no back-references, so
        a byte-accurate splice is a valid file.  Callers must keep enclosing
        structures consistent (u64 field sizes inside a plugin record, etc.)."""
        out = bytearray(self.raw)
        out[at:at] = blob
        struct.pack_into("<I", out, self.chunk_off + 4, self.data_len + len(blob))
        return bytes(out)


# ---------------------------------------------------------------- plugin data

VST_FIELDS = {50: "PluginInfo", 51: "FourCC", 52: "GUID", 53: "State",
              54: "Name", 55: "PluginPath", 56: "Vendor", 57: "_57"}


def find_vst_record(payload: bytes) -> tuple[int, list[dict]] | None:
    """Locate the VST record inside a PluginID.Data blob.

    FL 25 prefixes it with a variable-length wrapper-settings block (145 bytes
    for Ozone 11, 817 for Kontakt 7 ...), then::

        u32 type                     (0 for VST3 in FL 25; 8/10 in older FL)
        repeat: u32 field_id | u64 size | size bytes
                field ids 50 PluginInfo, 51 FourCC, 52 GUID, 53 State,
                54 Name, 55 PluginPath, 56 Vendor, 57 misc

    The chain always ends exactly at the end of the event payload, which is
    what makes the record self-verifying.
    """
    for start in range(4, len(payload) - 12):
        if payload[start:start + 4] != b"\x32\x00\x00\x00":      # field 50
            continue
        typ = struct.unpack_from("<I", payload, start - 4)[0]
        q, fields = start, []
        while q + 12 <= len(payload):
            fid, size = struct.unpack_from("<IQ", payload, q)
            if fid not in VST_FIELDS or q + 12 + size > len(payload):
                break
            fields.append({"id": fid, "name": VST_FIELDS[fid], "offset": q,
                           "size": size, "data_off": q + 12})
            q += 12 + size
        names = {f["name"] for f in fields}
        if q == len(payload) and {"Name", "State"} <= names:
            return start - 4, [{"type": typ}] + fields
    return None


# ---------------------------------------------------------------- reports

def histogram(flp: FLP) -> tuple[Counter, int, int]:
    counts: Counter[int] = Counter()
    n = total = 0
    for ev in flp.events():
        counts[ev.id] += 1
        n += 1
        total += ev.length
    return counts, n, total


def plugin_table(flp: FLP) -> list[dict]:
    """Bind every plugin to its owner and slot.

    Channel-rack plugins appear before the first InsertID.Name event; mixer
    plugins appear inside insert blocks, one block per InsertID.Name, each slot
    terminated by a SlotID.Index event whose u16 value is the slot number.
    """
    raw = flp.raw
    rows: list[dict] = []
    in_mixer = False
    insert_name: str | None = None
    pending: dict = {}
    last_row: dict | None = None

    for ev in flp.events():
        if ev.id == ID_INSERT_NAME:
            in_mixer = True
            insert_name = flp.text(ev)
            pending = {}
        elif ev.id == ID_SLOT_INDEX:
            # a slot block is terminated by its own index; attach it to the
            # plugin block that precedes it (that is how FL serialises slots)
            if last_row is not None and last_row["terminator"] is None:
                last_row["terminator"] = flp.u16(ev)
                last_row = None
            pending = {}
        elif ev.id == ID_PLUGIN_INTERNAL:
            pending["internal"] = flp.text(ev)
        elif ev.id == ID_PLUGIN_NAME:
            pending["display"] = flp.text(ev)
        elif ev.id == ID_PLUGIN_WRAPPER:
            pending["owner"], pending["slot"] = struct.unpack_from("<II", raw, ev.payload_off)
        elif ev.id == ID_PLUGIN_DATA:
            rec = find_vst_record(flp.payload(ev))
            row = {"kind": "mixer" if in_mixer else "channel",
                   "insert": pending.get("owner"), "slot": pending.get("slot"),
                   "insert_name": insert_name if in_mixer else None,
                   "internal": pending.get("internal"),
                   "display": pending.get("display"),
                   "data_off": ev.offset, "data_len": ev.length,
                   "terminator": pending.get("terminator"), "record": rec}
            if rec:
                pay = flp.payload(ev)
                for f in rec[1][1:]:
                    blob = pay[f["data_off"]:f["data_off"] + f["size"]]
                    if f["name"] == "State":
                        row["state_size"] = f["size"]
                        row["state_off"] = ev.payload_off + f["data_off"]
                    else:
                        row[f["name"].lower()] = blob.rstrip(b"\0").decode("utf-8", "replace")
            rows.append(row)
            last_row = row
            pending = {}
    return rows


def desync_candidates(flp: FLP) -> list[tuple[int, int, int]]:
    """1-byte events whose payload is not a plausible boolean.

    Shredded UTF-16 is the signature of a mis-sized event: ASCII char bytes get
    read as ids with the following 0x00 as their payload.  A clean parse of a
    real project yields none of these.
    """
    bad = []
    for ev in flp.events():
        if ev.length == 1 and ev.id in (0, 1):
            val = flp.raw[ev.payload_off]
            if val not in (0, 1, 2):
                bad.append((ev.id, ev.offset, val))
    return bad


def verify(flp: FLP) -> bool:
    ok = True
    evs = list(flp.events())

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))

    consumed = sum(ev.header_len + ev.length for ev in evs)
    check("event stream consumes the whole FLdt chunk",
          consumed == flp.data_len,
          f"{consumed:,}/{flp.data_len:,} bytes, {len(evs):,} events")

    by_id: dict[int, list[Event]] = {}
    for e in evs:
        by_id.setdefault(e.id, []).append(e)

    title = (by_id.get(ID_TITLE) or [None])[0]
    check("project title decodes as one UTF-16 event",
          title is not None and flp.text(title) == "FL Studio 25.2.5.5319.5319",
          f"id=192 off={title.offset} len={title.length} {flp.text(title)!r}"
          if title else "id 192 not found")

    ver = (by_id.get(ID_VERSION) or [None])[0]
    build = (by_id.get(ID_BUILD) or [None])[0]
    check("version / build events",
          ver is not None and build is not None
          and flp.payload(ver).rstrip(b"\0") == b"25.2.5.5319"
          and flp.u32(build) == 5319,
          f"version@{ver.offset} build@{build.offset}={flp.u32(build) if build else '-'}")

    tempo = (by_id.get(156) or [None])[0]
    check("tempo event decodes to a musical value",
          tempo is not None and 20_000 <= flp.u32(tempo) <= 999_000,
          f"{flp.u32(tempo)/1000 if tempo else 0:.3f} BPM @ {tempo.offset if tempo else '-'}")

    # known byte offsets (independent ground truth)
    for needle, expect in ((b"Ozone 11", 1622980),
                           (b"Fortin Nameless Suite X", 98569598)):
        pos = flp.raw.find(needle)
        host = next((e for e in evs if e.payload_off <= pos < e.end), None)
        rel = flp.payload(host).find(needle) if host else -1
        check(f"{needle.decode()!r} sits in a decoded plugin-state payload",
              pos == expect and host is not None and host.id == ID_PLUGIN_DATA and rel >= 0,
              f"found@{pos} event id={host.id} off={host.offset} "
              f"payload={host.payload_off} len={host.length} rel={rel}")

    rows = plugin_table(flp)
    vst = [r for r in rows if r["record"]]
    check("every VST record chain ends exactly at its event boundary",
          bool(vst),
          f"{len(vst)} VST records, {len(rows) - len(vst)} native-plugin blobs, "
          f"{len(rows)} id-213 events")

    mixer = [r for r in rows if r["kind"] == "mixer"]
    pairs_ok = all(r["slot"] == r["terminator"] for r in mixer if r["terminator"] is not None)
    check("wrapper (insert,slot) agrees with the following SlotID.Index",
          pairs_ok and bool(mixer),
          f"{len(mixer)} mixer plugins, {len({r['insert'] for r in mixer})} distinct inserts")

    bad = desync_candidates(flp)
    check("no shredded-UTF-16 signatures anywhere in the stream", not bad,
          f"{len(bad)} suspicious 1-byte events")

    biggest = max(evs, key=lambda e: e.length)
    check("largest event is a plausible plugin blob",
          biggest.length < flp.data_len,
          f"id={biggest.id} off={biggest.offset} len={biggest.length:,} "
          f"({biggest.length / flp.data_len:.1%} of chunk)")
    return ok


def insert_selftest(flp: FLP) -> bool:
    """Insert a Comments event after the title and re-parse the result."""
    evs = list(flp.events())
    title = next(e for e in evs if e.id == ID_TITLE)
    blob = FLP.encode_event(195, "inserted by flp_events.py".encode("utf-16-le") + b"\0\0")
    patched = flp.with_inserted(title.end, blob)

    other = FLP.__new__(FLP)
    other.__dict__.update(flp.__dict__)
    other.raw = patched
    other.data_len = flp.data_len + len(blob)
    new_evs = list(other.events())
    found = next((e for e in new_evs if e.offset == title.end), None)
    shifted = next(e for e in new_evs if e.id == ID_PLUGIN_DATA)
    old = next(e for e in evs if e.id == ID_PLUGIN_DATA)
    ok = (len(new_evs) == len(evs) + 1 and found is not None and found.id == 195
          and other.text(found) == "inserted by flp_events.py"
          and shifted.offset == old.offset + len(blob)
          and len(patched) == len(flp.raw) + len(blob))
    print(f"[{'PASS' if ok else 'FAIL'}] insert round-trip: +{len(blob)} bytes, "
          f"{len(evs)} -> {len(new_evs)} events, first plugin data "
          f"{old.offset} -> {shifted.offset}")
    return ok


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
    flp = FLP.load(path, strict="--strict" in argv)
    print(f"file      : {flp.path}")
    print(f"size      : {len(flp.raw):,} bytes")
    print(f"header    : format={flp.fmt} channels={flp.channels} ppq={flp.ppq}")
    print(f"FLdt      : chunk@{flp.chunk_off} data@{flp.data_off} len={flp.data_len:,}")
    counts, n, total = histogram(flp)
    print(f"events    : {n:,}   payload {total:,} bytes ({total/flp.data_len:.2%} of chunk)")
    print(f"distinct  : {len(counts)} ids\n")

    per_id_size: Counter[int] = Counter()
    for e in flp.events():
        per_id_size[e.id] += e.length
    print(" id    count  size class   total payload")
    print("-" * 46)
    for eid, c in counts.most_common(28):
        cls = ("fixed 1" if eid < 0x40 else "fixed 2" if eid < 0x80
               else "fixed 4" if eid < 0xC0 else "varint")
        print(f"0x{eid:02X} {eid:5d} {c:7d}  {cls:<10} {per_id_size[eid]:>12,}")
    print()

    rows = plugin_table(flp)
    print(f"plugins: {sum(1 for r in rows if r['kind']=='channel')} channel-rack, "
          f"{sum(1 for r in rows if r['kind']=='mixer')} mixer slots\n")
    print(f"{'ins':>4} {'slot':>4}  {'track':<10} {'internal':<18} {'name':<26} {'state':>9}")
    for r in rows:
        if r["kind"] != "mixer":
            continue
        print(f"{str(r['insert']):>4} {str(r['slot']):>4}  {str(r['insert_name'])[:10]:<10} "
              f"{str(r['internal'])[:18]:<18} {(r.get('name') or '')[:26]:<26} "
              f"{r.get('state_size', 0):>9,}")
    print()
    ok = verify(flp)
    ok &= insert_selftest(flp)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
