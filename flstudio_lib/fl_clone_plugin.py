"""fl_clone_plugin.py -- копирует плагин из одного слота микшера в другой.

Зачем: в API FL нет функции вставки плагина. Но запись плагина в .flp
самодостаточна (обёртка + состояние), поэтому её можно продублировать
байтовой хирургией -- и FL загрузит плагин как обычный.

Как устроен блок слота в файле (проверено дампом):
    0xC9 (внутреннее имя) -> 0xD4 (insert, slot) -> 0x9B (иконка)
    -> 0x80 (цвет) -> 0x29 (флаг) -> 0xD5 (состояние) -> 0x62 (номер слота)

Использование:
    python fl_clone_plugin.py SRC_INSERT SRC_SLOT DST_INSERT DST_SLOT [--dry]
    python fl_clone_plugin.py 5 0 19 0 --file copy.flp
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

FLP = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
ID_SLOT_TERM = 98


def find_block(evs, raw, insert: int, slot: int) -> tuple[int, int] | None:
    """Return (start_idx, end_idx) of the slot block for (insert, slot)."""
    for i, e in enumerate(evs):
        if e.id != fe.ID_PLUGIN_WRAPPER or e.length < 8:
            continue
        ins, sl = struct.unpack_from("<II", raw, e.payload_off)
        if (ins, sl) != (insert, slot):
            continue
        # начало блока: ближайший предшествующий 0xC9
        start = i
        for j in range(i, max(-1, i - 6), -1):
            if evs[j].id == fe.ID_PLUGIN_INTERNAL:
                start = j
                break
        # конец: первый 0x62 после i
        end = i
        for j in range(i, min(len(evs), i + 12)):
            if evs[j].id == ID_SLOT_TERM:
                end = j
                break
        return start, end
    return None


def insert_point(evs, insert: int) -> int:
    """Индекс события, перед которым вставлять блок в целевой инсерт.

    Инсерты разделяются событиями 0xCC (имя дорожки микшера): блок N
    заканчивается там, где начинается 0xCC для N+1.
    """
    starts = [i for i, e in enumerate(evs) if e.id == fe.ID_INSERT_NAME]
    if not starts:
        return len(evs)
    if insert >= len(starts) - 1:
        return len(evs)
    return starts[insert + 1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src_insert", type=int)
    ap.add_argument("src_slot", type=int)
    ap.add_argument("dst_insert", type=int)
    ap.add_argument("dst_slot", type=int)
    ap.add_argument("--file", default=FLP)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    flp = fe.FLP.load(a.file)
    raw = flp.raw
    evs = list(flp.events())

    src = find_block(evs, raw, a.src_insert, a.src_slot)
    if not src:
        print("источник не найден: insert=%d slot=%d" % (a.src_insert, a.src_slot))
        return 2
    s0, s1 = src
    start_off = evs[s0].offset
    end_off = evs[s1].end
    block = bytearray(raw[start_off:end_off])
    print("блок источника: события %d..%d, байт %d (%.1f КБ)"
          % (s0, s1, len(block), len(block) / 1024))

    # переписать (insert, slot) в копии
    for e in evs[s0:s1 + 1]:
        rel = e.offset - start_off
        if e.id == fe.ID_PLUGIN_WRAPPER:
            struct.pack_into("<II", block, rel + e.header_len, a.dst_insert, a.dst_slot)
        elif e.id == ID_SLOT_TERM:
            # payload: u16 номер слота
            struct.pack_into("<H", block, rel + e.header_len, a.dst_slot)

    at_idx = insert_point(evs, a.dst_insert)
    at_off = evs[at_idx].offset if at_idx < len(evs) else len(raw)
    print("вставка перед событием %d (offset %d), инсерт %d слот %d"
          % (at_idx, at_off, a.dst_insert, a.dst_slot))

    if a.dry:
        print("--dry: файл не изменён")
        return 0

    out = bytearray(raw)
    out[at_off:at_off] = block
    struct.pack_into("<I", out, flp.chunk_off + 4, flp.data_len + len(block))

    bak = a.file + ".bak2"
    if not os.path.exists(bak):
        shutil.copy2(a.file, bak)
        print("бэкап:", bak)
    with open(a.file, "wb") as f:
        f.write(out)
    print("записано, добавлено байт:", len(block))

    # проверка
    chk = fe.FLP.load(a.file)
    cevs = list(chk.events())
    cons = sum(e.header_len + e.length for e in cevs)
    print("поток: %d из %d -> %s" % (cons, chk.data_len, "OK" if cons == chk.data_len else "РАСХОЖДЕНИЕ"))
    rows = fe.plugin_table(chk)
    hit = [r for r in rows if r.get("insert") == a.dst_insert and r.get("slot") == a.dst_slot]
    print("плагин на целевом слоте:", hit[0].get("name") if hit else "НЕ НАЙДЕН")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
