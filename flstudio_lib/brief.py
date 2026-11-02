"""brief.py -- одна команда = вся картина проекта по всем фронтам.

Собирает в один ответ: живое состояние микшера, перцептивный замер, доли полос,
стерео, сравнение с коридором референсов и список конкретных проблем с числами.

Запуск (Python 3.13 из-за phonometry):
  uv run --no-project --python 3.13 --with phonometry --with soundfile \
      --with numpy --with scipy --with pyloudnorm --with pystoi \
      python brief.py mix.wav --section 60:90 --refs DIR [--md]

Кэш замеров: _cache/listen/<hash>.json (по пути, mtime и отрезку).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_cache", "listen")
TCP = ("127.0.0.1", 9787)

# метрики, где «больше» не значит «лучше» — для них печатаем только позицию
NEUTRAL = {"N_mean", "N_p95", "phon_mean", "phon_max", "lufs", "crest_db",
           "ldr_db", "duration_s", "dur", "octave_4k_db", "side_mid_db"}


def _cache_key(path: str, sec) -> str:
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    raw = "%s|%s|%s" % (os.path.abspath(path), mt, sec)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def measure_cached(path: str, sec) -> dict:
    os.makedirs(CACHE, exist_ok=True)
    key = _cache_key(path, sec)
    cp = os.path.join(CACHE, key + ".json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    res = L.measure(path, section=sec)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return res


def live_state() -> dict | None:
    """Живое состояние FL через мост; None, если FL закрыт."""
    try:
        def call(cmd, params):
            s = socket.create_connection(TCP, timeout=8)
            s.sendall((json.dumps({"op": "call", "cmd": cmd, "params": params}) + "\n").encode())
            d = s.recv(400000)
            s.close()
            r = json.loads(d.decode("utf-8", "replace").strip())
            d2 = r.get("data") or {}
            return d2.get("result") if d2.get("ok") else None
        tracks = call("exec", {"expr": "mixer.trackCount()"})
        if not tracks:
            return None
        out = call("exec", {"expr": (
            "__import__('json').dumps({"
            "'tracks': [{'i': i, 'name': str(mixer.getTrackName(i)),"
            "'vol': round(mixer.getTrackVolume(i), 3),"
            "'pan': round(mixer.getTrackPan(i), 3)} for i in range(mixer.trackCount())],"
            "'tempo': general.getRecPPB()/1000.0})")})
        return json.loads(out) if out else None
    except Exception:
        return None


def flags(mine: dict, cor: dict) -> list[str]:
    """Все метрики вне коридора, кроме нейтральных (где «больше» не значит хуже)."""
    out = []
    for key, c in cor.items():
        if c.get("вывод") == "в коридоре":
            continue
        short = key.split(".")[-1]
        if short in NEUTRAL:
            continue
        out.append("%s = %.3f — %s (коридор %.3f..%.3f)" % (
            key, c["наш"], c["вывод"], c["коридор"][0], c["коридор"][1]))
    return out


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = prefix + k
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, (int, float)):
            out[key] = v
    return out


def report(mix: str, sec, refs: list[str], md: bool) -> dict:
    mine = measure_cached(mix, sec)
    plots = {r: measure_cached(r, sec) for r in refs}
    cor = L.corridor(flatten(mine), list(plots.values()))

    lines = []
    live = live_state()
    if live:
        lines.append("ПРОЕКТ  жив: %d дорожек, темп %.1f" % (len(live["tracks"]), live["tempo"]))
    else:
        lines.append("ПРОЕКТ  FL закрыт (только файл)")
    lines.append("ФАЙЛ    %s, %.1f с" % (mine["file"], mine["duration_s"]))
    if mine.get("silent"):
        lines.append("ВНИМАНИЕ  секция почти пустая: активных кадров %.0f%% — замер не показателен"
                     % (100 * mine.get("active_ratio", 0)))
    lines.append("ГРОМКОСТЬ  N %.1f сон | фоны %.1f | LUFS %.1f | crest %.1f | LDR %.1f"
                 % (mine["N_mean"], mine["phon_mean"], mine["lufs"], mine["crest_db"], mine["ldr_db"]))
    lines.append("РЕЗКОСТЬ   %.2f acum (p95 %.2f)" % (mine["sharp_mean"], mine["sharp_p95"]))
    rp = mine.get("regions_pct") or {}
    lines.append("ПОЛОСЫ     " + " ".join("%s %.0f%%" % (k, v) for k, v in rp.items()))
    st = mine.get("stereo") or {}
    lines.append("СТЕРЕО     corr %.2f | side/mid %.1f dB | ширина %.2f"
                 % (st.get("corr", 0), st.get("side_mid_db", 0), st.get("width", 0)))

    if cor:
        out_of = [k for k, v in cor.items() if v["вывод"] != "в коридоре"]
        lines.append("КОРИДОР    %d метрик, вне коридора: %d" % (len(cor), len(out_of)))

    fl = flags(mine, cor)
    lines.append("ФЛАГИ      " + ("нет" if not fl else ""))
    for i, f in enumerate(fl, 1):
        lines.append("  %d) %s" % (i, f))

    text = "\n".join(lines)
    if md:
        text = "```\n" + text + "\n```"
    return {"text": text, "mine": mine, "corridor": cor, "flags": fl}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mix")
    ap.add_argument("--section")
    ap.add_argument("--refs")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    sec = L.parse_section(a.section)
    refs = []
    if a.refs:
        refs = sorted(glob.glob(os.path.join(a.refs, "*.mp3")) +
                      glob.glob(os.path.join(a.refs, "*.wav")))
    r = report(a.mix, sec, refs, a.md)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        print(r["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
