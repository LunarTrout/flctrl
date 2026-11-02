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
    import fl_lib as fl

    if cmd == "measure":
        path = args[0]
        a = float(args[args.index("--a") + 1]) if "--a" in args else 0.0
        b = float(args[args.index("--b") + 1]) if "--b" in args else 30.0
        _out(fl.hear.measure(path, a, b))
        return 0
    if cmd == "bands":
        _out(fl.hear.bands(args[0]))
        return 0
    if cmd == "profile":
        _out(fl.hear.profile(args[0]))
        return 0
    if cmd == "sections":
        _out(fl.hear.sections(args[0]))
        return 0
    if cmd == "masking":
        stems = json.loads(args[1]) if len(args) > 1 and os.path.exists(args[1]) else {}
        _out(fl.hear.masking(stems, mix_path=args[0]))
        return 0
    if cmd == "describe":
        _out(fl.hear.describe(args[0]))
        return 0
    if cmd == "nearest":
        paths = json.loads(args[1]) if len(args) > 1 else []
        _out(fl.hear.nearest(args[0], paths))
        return 0
    if cmd == "style":
        _out({"similarity": fl.hear.style_sim(args[0], args[1])})
        return 0
    print("неизвестная команда: %s" % cmd)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
