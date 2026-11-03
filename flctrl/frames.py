"""frames.py — кадры и сравнение: одна форма для любых картин.

Кадр — это таблица значений с подписанными осями и единицей:

    {
      "id": "f17",
      "metric": "rms",
      "unit": "дБ",
      "axes": {"track": [0, 1, 2], "band": ["sub", "bass"]},
      "values": [[-18.2, -14.0], [-17.1, -13.8], [-19.0, -15.2]],
      "coverage": {"measured": ["project"], "missing": []}
    }

`values` прямоугольная: len(values) == len(axes[axis0]), len(values[0]) ==
len(axes[axis1]). Пропуск — `None`, он же попадает в coverage.missing.

Сравнение работает по пересечению осей: если у одной картины полос 7, у другой
5, сравнятся общие 5, а разница осей уйдёт в coverage.unmatched. Никаких
«средних вместо пропусков».
"""
from __future__ import annotations

import itertools
import json

# --- реестр метрик: единица, оси, откуда берётся -----------------------------

METRICS = {
    "rms":       {"unit": "дБ", "axes": ("track", "band"), "source": "file|tap"},
    "peak":      {"unit": "дБ", "axes": ("track",), "source": "file|tap"},
    "crest":     {"unit": "дБ", "axes": ("track",), "source": "file"},
    "bands":     {"unit": "дБ", "axes": ("track", "band"), "source": "file"},
    "erb":       {"unit": "дБ", "axes": ("track", "band"), "source": "file|tap"},
    "partial":   {"unit": "доля", "axes": ("role", "band"), "source": "stems|tap"},
    "masking":   {"unit": "дБ", "axes": ("role", "role", "band"), "source": "stems"},
    "sii":       {"unit": "0..1", "axes": ("band",), "source": "file"},
    "transient": {"unit": "дБ", "axes": ("track", "band"), "source": "file|tap"},
    "harmony":   {"unit": "доли", "axes": ("pitch",), "source": "file"},
    "stereo":    {"unit": "0..1", "axes": ("track", "band"), "source": "file"},
    "wall":      {"unit": "дБ", "axes": ("band", "section"), "source": "file"},
    "sections":  {"unit": "с", "axes": ("time",), "source": "file"},
    "lufs":      {"unit": "LUFS", "axes": ("track",), "source": "file"},
}


def metric_info(name: str) -> dict:
    if name not in METRICS:
        raise ValueError("неизвестная метрика %r; есть: %s"
                         % (name, ", ".join(sorted(METRICS))))
    return METRICS[name]


# --- кадр -------------------------------------------------------------------

def make_frame(metric: str, axes: dict, values: list, coverage: dict | None = None,
               frame_id: str | None = None, unit: str | None = None) -> dict:
    """Собрать кадр с проверкой формы.

    Проверяется, что оси прямоугольные и что длины совпадают с values —
    иначе сравнение молча даст ерунду.
    """
    info = metric_info(metric)
    names = list(axes)
    if len(names) > 2:
        raise ValueError("кадр держит максимум две оси, получено %d: %s"
                         % (len(names), names))
    if len(names) == 1:
        n = len(axes[names[0]])
        if len(values) != n:
            raise ValueError("values %d != ось %s %d" % (len(values), names[0], n))
    else:
        n0 = len(axes[names[0]])
        n1 = len(axes[names[1]])
        if len(values) != n0:
            raise ValueError("values %d != ось %s %d" % (len(values), names[0], n0))
        for i, row in enumerate(values):
            if len(row) != n1:
                raise ValueError("строка %d: %d значений, ось %s ждёт %d"
                                 % (i, len(row), names[1], n1))
    cov = {"measured": [], "missing": []}
    if coverage:
        cov.update(coverage)
    missing = []
    if len(names) == 1:
        for i, v in enumerate(values):
            if v is None:
                missing.append({names[0]: axes[names[0]][i]})
    else:
        for i, row in enumerate(values):
            for j, v in enumerate(row):
                if v is None:
                    missing.append({names[0]: axes[names[0]][i],
                                    names[1]: axes[names[1]][j]})
    if missing:
        cov["missing"] = list(cov.get("missing") or []) + missing[:50]
    return {"id": frame_id or ("%s-%d" % (metric, abs(hash(json.dumps(axes, default=str))) % 10**6)),
            "metric": metric,
            "unit": unit or info["unit"],
            "axes": {k: list(v) for k, v in axes.items()},
            "values": values,
            "coverage": cov}


def _split_source(frame: dict, side: str) -> dict:
    """Снять ось source из кадра с одним источником, запомнив имя."""
    out = dict(frame)
    out["axes"] = {k: v for k, v in frame["axes"].items() if k != "source"}
    out["values"] = frame["values"][0]
    out["coverage"] = dict(frame.get("coverage") or {})
    out["coverage"]["source"] = frame["axes"]["source"][0]
    return out


def _index(axis_values: list) -> dict:
    return {v: i for i, v in enumerate(axis_values)}


def compare(a: dict, b: dict, mode: str = "delta", top: int | None = None) -> dict:
    """Сравнить два кадра: разреженный список дельт по общим осям.

    Возвращает:
      rows      — [{ось: значение, a, b, d}], по модулю дельты
      coverage  — что не сравнилось (unmatched) и сколько пропусков
      stats     — медиана, среднее |d|, число сравнённых
    """
    if a.get("metric") != b.get("metric"):
        raise ValueError("разные метрики: %s и %s" % (a.get("metric"), b.get("metric")))

    # две картины по одному источнику сравниваются как «наш против рефа»:
    # ось источника снимается, имена переходят в отчёт
    if (list(a["axes"]) == ["source", "band"] and list(b["axes"]) == ["source", "band"]
            and len(a["axes"]["source"]) == 1 and len(b["axes"]["source"]) == 1):
        a = _split_source(a, "a")
        b = _split_source(b, "b")

    names = list(a["axes"])
    if names != list(b["axes"]):
        raise ValueError("разные оси: %s и %s" % (names, list(b["axes"])))

    idx_b = [_index(b["axes"][n]) for n in names]
    rows, unmatched = [], {}

    if len(names) == 1:
        ax = a["axes"][names[0]]
        only_a = [v for v in ax if v not in idx_b[0]]
        only_b = [v for v in b["axes"][names[0]] if v not in _index(ax)]
        unmatched = {names[0]: {"only_a": only_a, "only_b": only_b}}
        for i, key in enumerate(ax):
            if key not in idx_b[0]:
                continue
            va = a["values"][i]
            vb = b["values"][idx_b[0][key]]
            if va is None or vb is None:
                continue
            rows.append({names[0]: key, "a": va, "b": vb, "d": round(vb - va, 3)})
    else:
        ax0, ax1 = names
        m1 = _index(b["axes"][ax1])
        only_b = [v for v in b["axes"][ax1] if v not in _index(a["axes"][ax1])]
        only_a = [v for v in a["axes"][ax1] if v not in m1]
        unmatched = {ax1: {"only_a": only_a, "only_b": only_b}}
        m0 = _index(b["axes"][ax0])
        for i, k0 in enumerate(a["axes"][ax0]):
            if k0 not in m0:
                continue
            for j, k1 in enumerate(a["axes"][ax1]):
                if k1 not in m1:
                    continue
                va = a["values"][i][j]
                vb = b["values"][m0[k0]][m1[k1]]
                if va is None or vb is None:
                    continue
                rows.append({ax0: k0, ax1: k1, "a": va, "b": vb, "d": round(vb - va, 3)})

    rows.sort(key=lambda r: -abs(r["d"]))
    compared = len(rows)
    if top:
        rows = rows[:top]
    ds = [r["d"] for r in rows]
    stats = {"compared": compared,
             "median": round(sorted(ds)[len(ds) // 2], 3) if ds else None,
             "mean_abs": round(sum(abs(d) for d in ds) / len(ds), 3) if ds else None,
             "max_abs": round(max((abs(d) for d in ds), default=0.0), 3)}
    return {"metric": a["metric"], "unit": a.get("unit"),
            "axes": names, "rows": rows, "stats": stats,
            "sources": {"a": (a.get("coverage") or {}).get("source"),
                        "b": (b.get("coverage") or {}).get("source")},
            "coverage": {"unmatched": unmatched,
                         "compared": compared,
                         "missing_a": len(a["coverage"].get("missing") or []),
                         "missing_b": len(b["coverage"].get("missing") or [])}}


def to_table(frame_or_cmp: dict, limit: int = 20) -> str:
    """Человеческая таблица — для логов и отчётов, не для модели."""
    if "rows" in frame_or_cmp:
        names = frame_or_cmp["axes"]
        head = " | ".join(names) + " | было | стало | дельта"
        lines = [head, "---" * (len(names) + 3)]
        for r in frame_or_cmp["rows"][:limit]:
            cells = " | ".join(str(r[n]) for n in names)
            lines.append("%s | %.2f | %.2f | %+.2f" % (cells, r["a"], r["b"], r["d"]))
        return "\n".join(lines)
    names = list(frame_or_cmp["axes"])
    lines = []
    for i, k in enumerate(frame_or_cmp["axes"][names[0]]):
        row = frame_or_cmp["values"][i]
        if isinstance(row, list):
            lines.append("%s: %s" % (k, " ".join("%.2f" % (v if v is not None else float("nan"))
                                                 for v in row)))
        else:
            lines.append("%s: %.2f" % (k, row))
    return "\n".join(lines)
