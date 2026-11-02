"""fl_add_plugin.py -- вставляет плагин в слот микшера из его .fst-файла базы FL.

Почему так: в API FL нет вставки плагина, но FL хранит запись каждого
установленного плагина в базе плагинов как .fst -- а это тот же контейнер
FLP, внутри которого лежат готовые события:

    0xC9  внутреннее имя
    0xD4  обёртка (insert, slot)
    0xD5  состояние плагина (GUID, FourCC, Name, Path, Vendor, State)

Значит, чтобы добавить в проект ЛЮБОЙ установленный плагин (даже тот,
которого в проекте никогда не было), достаточно взять его .fst, переписать
в обёртке (insert, slot) на целевые и вставить блок в нужный инсерт.

Файл .fst ищется по имени:
    python fl_add_plugin.py "FabFilter Pro-Q 4" 5 2
    python fl_add_plugin.py --list "pro-q"
"""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "flpresearch"))
import flp_events as fe  # noqa: E402

DB = r"C:\Users\epicc\Documents\Image-Line\FL Studio\Presets\Plugin database\Installed"
FLP = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
ID_SLOT_TERM = 98


def find_fst(name: str) -> str | None:
    """Найти .fst по подстроке имени (без учёта регистра)."""
    low = name.lower()
    best = None
    for root, _dirs, files in os.walk(DB):
        for fn in files:
            if not fn.lower().endswith(".fst"):
                continue
            if low in fn.lower():
                p = os.path.join(root, fn)
                # предпочитаем папку Effects
                if "effects" in p.lower():
                    return p
                best = best or p
    return best


def list_fst(fragment: str) -> None:
    low = fragment.lower()
    for root, _dirs, files in os.walk(DB):
        for fn in sorted(files):
            if fn.lower().endswith(".fst") and low in fn.lower():
                print(os.path.join(root, fn))


def build_block(fst_path: str, dst_insert: int, dst_slot: int) -> bytes:
    """Собрать байты блока плагина из .fst с переписанной обёрткой."""
    sub = fe.FLP.load(fst_path)
    raw = sub.raw
    evs = list(sub.events())

    keep = [e for e in evs if e.id in (fe.ID_PLUGIN_INTERNAL, fe.ID_PLUGIN_WRAPPER,
                                       fe.ID_PLUGIN_DATA, 155, 128, 41)]
    if not any(e.id == fe.ID_PLUGIN_DATA for e in keep):
        raise SystemExit("в .fst нет состояния плагина (0xD5)")

    out = bytearray()
    for e in keep:
        blob = bytearray(raw[e.offset:e.end])
        if e.id == fe.ID_PLUGIN_WRAPPER and e.length >= 8:
            struct.pack_into("<II", blob, e.header_len, dst_insert, dst_slot)
        out += blob
    # терминатор слота
    out += bytes([ID_SLOT_TERM]) + struct.pack("<H", dst_slot)
    return bytes(out)


def insert_point(evs, insert: int) -> int:
    starts = [i for i, e in enumerate(evs) if e.id == fe.ID_INSERT_NAME]
    if not starts:
        return len(evs)
    if insert >= len(starts) - 1:
        return len(evs)
    return starts[insert + 1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("dst_insert", nargs="?", type=int)
    ap.add_argument("dst_slot", nargs="?", type=int)
    ap.add_argument("--fst")
    ap.add_argument("--file", default=FLP)
    ap.add_argument("--list")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    if a.list:
        list_fst(a.list)
        return 0

    fst = a.fst or (find_fst(a.name) if a.name else None)
    if not fst:
        print("не нашёл .fst для:", a.name)
        return 2
    if a.dst_insert is None or a.dst_slot is None:
        print("нужны dst_insert и dst_slot")
        return 2

    print("плагин:", fst)
    block = build_block(fst, a.dst_insert, a.dst_slot)
    print("блок: %d байт -> insert %d slot %d" % (len(block), a.dst_insert, a.dst_slot))

    flp = fe.FLP.load(a.file)
    evs = list(flp.events())
    at = insert_point(evs, a.dst_insert)
    at_off = evs[at].offset if at < len(evs) else len(flp.raw)

    if a.dry:
        print("--dry: файл не изменён")
        return 0

    out = bytearray(flp.raw)
    out[at_off:at_off] = block
    struct.pack_into("<I", out, flp.chunk_off + 4, flp.data_len + len(block))

    bak = a.file + ".bak3"
    if not os.path.exists(bak):
        shutil.copy2(a.file, bak)
        print("бэкап:", bak)
    with open(a.file, "wb") as f:
        f.write(out)

    chk = fe.FLP.load(a.file)
    cevs = list(chk.events())
    cons = sum(e.header_len + e.length for e in cevs)
    print("поток: %d из %d -> %s" % (cons, chk.data_len, "OK" if cons == chk.data_len else "РАСХОЖДЕНИЕ"))
    rows = fe.plugin_table(chk)
    hit = [r for r in rows if r.get("insert") == a.dst_insert and r.get("slot") == a.dst_slot]
    print("на целевом слоте:", hit[0].get("name") if hit else "не видно парсеру")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
