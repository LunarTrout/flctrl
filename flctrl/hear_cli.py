"""Мост к слою слуха для MCP: считает и отдаёт JSON.

Запускается окружением с phonometry (Python 3.13), потому что слух живёт там,
а живое управление FL — в 3.12. MCP вызывает этот файл подпроцессом.

    python hear_cli.py measure mix.wav --a 60 --b 90
    python hear_cli.py bands mix.wav
    python hear_cli.py profile mix.wav
    python hear_cli.py sections mix.wav
    python hear_cli.py masking mix.wav stems.json
    python hear_cli.py describe mix.wav
    python hear_cli.py style a.wav b.wav
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _out(obj) -> None:
    def conv(v):
        try:
            import numpy as np
            if isinstance(v, np.ndarray):
                return v.tolist()
            if isinstance(v, (np.floating, np.integer)):
                return float(v)
        except Exception:
            pass
        return v

    if isinstance(obj, dict):
        obj = {k: conv(v) for k, v in obj.items()}
    print(json.dumps(obj, ensure_ascii=False, default=str))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    # этот CLI вызывается из fl_lib в окружении без phonometry, поэтому
    # слуховые команды обязаны идти в listen_layer напрямую: через fl.hear.*
    # они бы снова звали этот же CLI и падали на проверке окружения
    import listen_layer as L

    if cmd == "measure":
        path = args[0]
        a = float(args[args.index("--a") + 1]) if "--a" in args else 0.0
        b = float(args[args.index("--b") + 1]) if "--b" in args else 30.0
        _out(L.measure(path, (a, b)))
        return 0
    if cmd == "bands":
        import bandprofile as B
        _out(B.band_axes(args[0]))
        return 0
    if cmd == "profile":
        import profile_cache as P
        _out(P.build(args[0]))
        return 0
    if cmd == "curve":
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        _out(L.curve(args[0], (a, b) if a is not None and b is not None else None))
        return 0
    if cmd == "sections":
        _out(L.sections(args[0]))
        return 0
    if cmd == "masking":
        # напрямую в listen_layer: через fl.hear.masking была бы рекурсия,
        # потому что тот сам запускает этот CLI
        import listen_layer as L
        stems = json.loads(open(args[1], encoding="utf-8").read()) if len(args) > 1 else {}
        _out(L.masking(stems, mix_path=args[0]))
        return 0
    if cmd == "match":
        import listen_layer as L
        _out(L.match_sections(args[0], args[1]))
        return 0
    if cmd == "compare":
        import listen_layer as L
        from brief import flatten
        refs = json.loads(open(args[1], encoding="utf-8").read())
        mine = flatten(L.measure(args[0]))
        rs = [flatten(L.measure(r)) for r in refs]
        _out(L.corridor(mine, rs))
        return 0
    if cmd == "psycho":
        import listen_layer as L
        path = args[0]
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        _out(L.psycho(path, section=(a, b) if a is not None and b is not None else None))
        return 0
    if cmd == "attribute":
        # частичная громкость: сколько каждого источника реально слышно
        import partial

        stems = json.loads(open(args[1], encoding="utf-8").read()) if len(args) > 1 else {}
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        sec = float(args[args.index("--section") + 1]) if "--section" in args else None
        if sec:
            _out(partial.attribute_mg(args[0], stems, a, b, section_s=sec))
        else:
            _out(partial.attribute(args[0], stems, a, b))
        return 0
    if cmd == "balance":
        import balance

        mode = args[1]
        if mode == "build":
            refs = args[2]
            out = args[3] if len(args) > 3 else None
            _out(balance.build(refs, out))
            return 0
        if mode == "project":
            mapping = json.loads(open(args[2], encoding="utf-8").read())
            mix = args[3] if len(args) > 3 else None
            _out(balance.project(mapping, mix))
            return 0
    if cmd == "audit":
        import audit

        mine = args[0]
        refs = json.loads(open(args[1], encoding="utf-8").read()) if len(args) > 1 else []
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        m = audit.measure(mine, a, b)
        rs = [audit.measure(r, a, b) for r in refs]
        _out(audit.audit(m, rs))
        return 0
    if cmd == "harmony":
        import harmony

        refs = []
        if len(args) > 1:
            raw = args[1]
            refs = (json.loads(raw) if raw.lstrip().startswith("[")
                    else json.loads(open(raw, encoding="utf-8").read()))
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        m = harmony.analyse(args[0], a, b)
        if refs:
            _out(harmony.compare(m, [harmony.analyse(r, a, b) for r in refs]))
        else:
            _out(m)
        return 0
    if cmd == "transients":
        import transients

        refs = []
        if len(args) > 1:
            raw = args[1]
            refs = (json.loads(raw) if raw.lstrip().startswith("[")
                    else json.loads(open(raw, encoding="utf-8").read()))
        lim = float(args[args.index("--seconds") + 1]) if "--seconds" in args else 45.0
        m = transients.analyse(args[0], max_seconds=lim)
        rs = [transients.analyse(r, max_seconds=lim) for r in refs]
        _out(transients.compare(m, rs))
        return 0
    if cmd == "seccomp":
        import seccomp

        mine = args[0]
        refs = json.loads(open(args[1], encoding="utf-8").read()) if len(args) > 1 else []
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        _out(seccomp.compare(mine, refs, a, b))
        return 0
    if cmd == "pan":
        import pan as pan_mod

        stems = json.loads(open(args[0], encoding="utf-8").read()) if args else {}
        a = float(args[args.index("--a") + 1]) if "--a" in args else None
        b = float(args[args.index("--b") + 1]) if "--b" in args else None
        m = pan_mod.analyze(stems, a, b)
        _out({"map": m, "advice": pan_mod.advice(m)})
        return 0
    if cmd == "report":
        import report as rep

        mine = args[0]
        payload = json.loads(open(args[1], encoding="utf-8").read()) if len(args) > 1 else {}
        a = payload.get("a")
        b = payload.get("b")
        res = rep.build(mine, payload.get("refs"), a, b, payload.get("stems"),
                        payload.get("impulse"), payload.get("sine"), payload.get("ducked"))
        _out({"markdown": rep.to_markdown(res),
              "coverage": res["coverage"]["coverage"],
              "actions": res["actions"],
              "skipped": res["coverage"]["skipped"]})
        return 0
    if cmd == "sii":
        import speech

        vocal = args[args.index("--vocal") + 1] if "--vocal" in args else args[0]
        masker = args[args.index("--masker") + 1] if "--masker" in args else args[1]
        res = speech.intelligibility(vocal, masker)
        res["grade"] = speech._grade(res["sii"])
        _out(res)
        return 0
    if cmd == "describe":
        import fl_lib as fl
        _out(fl.hear.describe(args[0]))
        return 0
    if cmd == "nearest":
        import fl_lib as fl
        paths = json.loads(args[1]) if len(args) > 1 else []
        _out(fl.hear.nearest(args[0], paths))
        return 0
    if cmd == "style":
        import fl_lib as fl
        _out({"similarity": fl.hear.style_sim(args[0], args[1])})
        return 0
    print("неизвестная команда: %s" % cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
