"""pan.py — панорама как форма: где роль стоит, насколько широко и меняется ли.

Одна цифра «ширина микса» ничего не говорит о движении. Здесь считается:

* позиция роли ПО ПОЛОСАМ и ПО СЕКЦИЯМ: pan = (E_R − E_L)/(E_R + E_L);
* ширина роли в полосе: доля боковой энергии (side/mid) по её же сигналу;
* статичность: разброс позиции между секциями (0 — роль прибита гвоздями);
* занятость поля: какая роль в какой полосе/стороне доминирует.

На выходе — карта и конкретные предложения: что двигать, где и насколько,
с проверкой (моно-совместимость, низ в центре, верх не уже референса).

    python -m flctrl.pan --stems guitarL=a.wav --stems guitarR=b.wav --section 12
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

import perceptual as P

NFFT = 4096


def _read(path: str, a: float | None = None, b: float | None = None):
    import soundfile as sf

    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if a is not None and b is not None:
            f.seek(int(a * fs))
            x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x, fs


def _bands(freqs: np.ndarray):
    out = []
    for fc in P.FC:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        out.append((freqs >= lo) & (freqs < hi))
    return out


def _stft(x: np.ndarray, fs: int):
    win = np.hanning(NFFT)
    hop = NFFT // 2
    if len(x) <= NFFT:
        x = np.pad(x, ((0, NFFT - len(x) + 1), (0, 0)))
    frames = 1 + (len(x) - NFFT) // hop
    idx = np.arange(NFFT)[None, :] + hop * np.arange(frames)[:, None]
    L = np.fft.rfft(x[:, 0][idx] * win, axis=1)
    R = np.fft.rfft(x[:, 1][idx] * win, axis=1)
    return L, R, np.fft.rfftfreq(NFFT, 1 / fs), frames, hop / fs


def profile(path: str, a: float | None = None, b: float | None = None,
            section_s: float = 8.0) -> dict:
    """Профиль панорамы одного стема: по полосам и по секциям."""
    x, fs = _read(path, a, b)
    L, R, freqs, frames, hop_s = _stft(x, fs)
    masks = _bands(freqs)

    pl = np.abs(L) ** 2
    pr = np.abs(R) ** 2
    xr = np.real(L * np.conj(R))

    band_rows = []
    for fc, m in zip(P.FC, masks):
        if not m.any():
            continue
        e_l = float(pl[:, m].sum())
        e_r = float(pr[:, m].sum())
        e_x = float(xr[:, m].sum())
        total = e_l + e_r + 1e-30
        pan = (e_r - e_l) / total
        mid = (e_l + e_r + 2 * e_x) / 4.0
        side = (e_l + e_r - 2 * e_x) / 4.0
        width = float(np.sqrt(max(side, 0) / max(mid, 1e-30)))
        corr = float(e_x / np.sqrt(max(e_l * e_r, 1e-30)))
        band_rows.append({"fc": float(fc), "pan": float(pan),
                          "width": width, "corr": corr,
                          "energy": float(total)})

    # секции: позиция и ширина во времени
    sec_frames = max(1, int(section_s / max(hop_s, 1e-6)))
    sections = []
    for start in range(0, frames, sec_frames):
        sl = slice(start, min(frames, start + sec_frames))
        if sl.stop - sl.start < 2:
            continue
        e_l = float(pl[sl].sum())
        e_r = float(pr[sl].sum())
        e_x = float(xr[sl].sum())
        pan = (e_r - e_l) / (e_l + e_r + 1e-30)
        mid = (e_l + e_r + 2 * e_x) / 4.0
        side = (e_l + e_r - 2 * e_x) / 4.0
        sections.append({"t": round(start * hop_s, 1),
                         "pan": float(pan),
                         "width": float(np.sqrt(max(side, 0) / max(mid, 1e-30)))})

    pans = [s["pan"] for s in sections]
    widths = [s["width"] for s in sections]
    return {
        "bands": band_rows,
        "sections": sections,
        "pan_mean": float(np.mean(pans)) if pans else 0.0,
        "pan_range": float(max(pans) - min(pans)) if pans else 0.0,
        "width_mean": float(np.mean(widths)) if widths else 0.0,
        "static": bool(len(pans) > 1 and (max(pans) - min(pans)) < 0.05),
        "dur": float(len(x) / fs),
    }


def analyze(stems: dict[str, str], a: float | None = None, b: float | None = None,
            section_s: float = 8.0) -> dict:
    """Карта поля: кто где стоит, насколько широко и двигается ли."""
    out = {"stems": {}, "a": a, "b": b}
    for name, path in stems.items():
        try:
            out["stems"][name] = profile(path, a, b, section_s)
        except Exception as exc:
            out["stems"][name] = {"error": str(exc)[:120]}

    # кто доминирует в полосе по стороне
    dominance = []
    names = [n for n, p in out["stems"].items() if "bands" in p]
    if names:
        for i, fc in enumerate(P.FC):
            row = {"fc": float(fc)}
            best_l = best_r = None
            for n in names:
                bnd = out["stems"][n]["bands"]
                if i >= len(bnd):
                    continue
                pan = bnd[i]["pan"]
                e = bnd[i]["energy"]
                if pan < -0.05 and (best_l is None or e > best_l[1]):
                    best_l = (n, e)
                if pan > 0.05 and (best_r is None or e > best_r[1]):
                    best_r = (n, e)
            row["left"] = best_l[0] if best_l else None
            row["right"] = best_r[0] if best_r else None
            dominance.append(row)
    out["dominance"] = dominance
    return out


def overlap(map_: dict, a_name: str, b_name: str) -> float:
    """Спектральное перекрытие двух ролей: 0 — не пересекаются, 1 — одно и то же.

    Считается по распределению энергии по полосам. Именно перекрытие, а не
    «ширина вообще», оправдывает разведение по панораме.
    """
    a = (map_.get("stems") or {}).get(a_name, {}).get("bands")
    b = (map_.get("stems") or {}).get(b_name, {}).get("bands")
    if not a or not b:
        return 0.0
    va = np.array([x["energy"] for x in a])
    vb = np.array([x["energy"] for x in b])
    va = va / (va.sum() + 1e-30)
    vb = vb / (vb.sum() + 1e-30)
    return float(np.minimum(va, vb).sum())


def advice(map_: dict, ref_width: float | None = None,
           max_step: float = 0.25, overlap_min: float = 0.25) -> list[dict]:
    """Что двигать — только там, где это оправдано измерением.

    Правила выведены из чисел, а не из вкуса:
    * низ (<120 Гц) держим в центре: иначе теряется моно и разъезжается бочка;
    * роль двигаем по секциям, только если её панорама статична И она
      спектрально перекрывается с другой ролью (overlap >= overlap_min);
    * расширяем верх, только если он реально уже референсного коридора.
    Нет перекрытия и нет дефицита ширины — предложения нет.
    """
    tips = []
    stems = map_.get("stems") or {}
    names = [n for n, p in stems.items() if "bands" in p]

    for name in names:
        prof = stems[name]
        low = [b for b in prof["bands"] if b["fc"] < 120]
        total_e = sum(b["energy"] for b in prof["bands"]) or 1e-30
        low_share = sum(b["energy"] for b in low) / total_e if low else 0.0
        # низ — только если он реально есть в роли, иначе это шум пустой полосы
        if low and low_share >= 0.03 and abs(np.mean([b["pan"] for b in low])) > 0.15:
            tips.append({"stem": name, "kind": "center_low",
                         "why": "низ уведён в сторону: %.2f (доля низа %.1f%%)" %
                                (float(np.mean([b["pan"] for b in low])), 100 * low_share),
                         "action": "вернуть низ в центр до ±0.15",
                         "evidence": {"low_pan": float(np.mean([b["pan"] for b in low])),
                                      "low_share": round(low_share, 3)}})
        if not prof.get("static") or len(prof.get("sections") or []) < 3:
            continue
        # низовые/центральные роли (бочка, бас) не двигаем: их место — центр.
        # Это не ярлык, а измерение: больше половины энергии ниже 200 Гц.
        lowdom = sum(b["energy"] for b in prof["bands"] if b["fc"] < 200) / total_e
        if lowdom > 0.5:
            continue
        # статичность сама по себе — не повод двигать; нужен партнёр по перекрытию
        partners = []
        for other in names:
            if other == name:
                continue
            ov = overlap(map_, name, other)
            if ov >= overlap_min:
                partners.append((other, ov))
        if partners:
            other, ov = max(partners, key=lambda kv: kv[1])
            tips.append({"stem": name, "kind": "movement",
                         "why": "панорама статична, перекрытие с %s %.2f" % (other, ov),
                         "action": "развести по секциям с %s (шаг до %.2f)" % (other, max_step),
                         "evidence": {"overlap": round(ov, 3), "with": other}})

    if ref_width:
        for name in names:
            top = [b for b in stems[name]["bands"] if b["fc"] > 6000]
            if not top:
                continue
            w = float(np.mean([b["width"] for b in top]))
            if w < ref_width - 0.1:
                tips.append({"stem": name, "kind": "widen_top",
                             "why": "верх уже референса: %.2f против %.2f" % (w, ref_width),
                             "action": "расширить верх (стерео-EQ/дубль), шаг до %.2f" % max_step,
                             "evidence": {"width_top": round(w, 3), "ref": ref_width}})
    return tips


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Карта панорамы и предложения")
    ap.add_argument("--stems", action="append", default=[])
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--ref-width", type=float, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    stems = {}
    for item in args.stems:
        name, _, path = item.partition("=")
        stems[name] = path
    if not stems:
        print("нужны --stems name=path")
        return 2

    m = analyze(stems, args.a, args.b)
    print("%-10s %7s %8s %7s %7s %s" % ("стем", "pan", "разброс", "ширина", "секций", "статичен"))
    for name, p in m["stems"].items():
        if "error" in p:
            print("%-10s ошибка: %s" % (name, p["error"]))
            continue
        print("%-10s %+7.2f %8.2f %7.2f %7d %s" % (
            name, p["pan_mean"], p["pan_range"], p["width_mean"],
            len(p["sections"]), "да" if p["static"] else "нет"))

    tips = advice(m, args.ref_width)
    if tips:
        print("\nпредложения:")
        for t in tips:
            print("  %-10s %-12s %s" % (t["stem"], t["kind"], t["action"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"map": m, "advice": tips}, f, ensure_ascii=False, indent=1)
        print("записано:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
