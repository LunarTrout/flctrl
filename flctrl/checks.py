"""checks.py — реестр всех проверок, чтобы ничего не забыть при оценке.

Идея: список проверок — это данные, а не код отчёта. Отчёт обязан обойти ВЕСЬ
реестр. Если для проверки не хватает входа (нет стемов, нет импульса), она не
исчезает из отчёта, а помечается «пропущена» с причиной. Так «забыл
посмотреть» становится видимым, а не тихим.

Каждая запись: id, что проверяем, что нужно на входе, и как считать.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Check:
    id: str
    label: str
    needs: tuple[str, ...]        # ("mix",) | ("mix","refs") | ("stems",) ...
    fn: Callable | None = None
    group: str = "общее"
    note: str = ""
    extra: dict = field(default_factory=dict)


def _fronts(ctx: dict) -> dict:
    import audit
    mine = audit.measure(ctx["mine"], ctx.get("a"), ctx.get("b"))
    refs = [audit.measure(r, ctx.get("a"), ctx.get("b")) for r in ctx["refs"]]
    return audit.audit(mine, refs)


def _sections(ctx: dict) -> dict:
    import seccomp
    return seccomp.compare(ctx["mine"], ctx["refs"], ctx.get("a"), ctx.get("b"))


def _roles(ctx: dict) -> dict:
    import balance
    return balance.project(ctx["stems"], None, ctx.get("a"), ctx.get("b"))


def _pan(ctx: dict) -> dict:
    import pan
    return {"advice": pan.advice(pan.analyze(ctx["stems"], ctx.get("a"), ctx.get("b")))}


def _effects_ir(ctx: dict) -> dict:
    import effects
    x, fs = effects._read(ctx["impulse"])
    return effects.ir_metrics(x, fs)


def _effects_thd(ctx: dict) -> dict:
    import effects
    x, fs = effects._read(ctx["sine"])
    return effects.thd(x, fs)


def _effects_duck(ctx: dict) -> dict:
    import effects
    x, fs = effects._read(ctx["ducked"])
    return effects.ducking(x, fs)


def _speech(ctx: dict) -> dict:
    import speech
    return speech.intelligibility(ctx["vocal"], ctx["masker"],
                                  master_path=ctx.get("mine"),
                                  baseline=ctx.get("sii_baseline"))


REGISTRY: list[Check] = [
    Check("fronts", "Фронты против коридора референсов", ("mix", "refs"),
          _fronts, "микс"),
    Check("spectrum", "Форма спектра по 28 полосам", ("mix", "refs"),
          None, "микс", "входит в проверку фронтов",
          extra={"part_of": "fronts"}),
    Check("stereo_bands", "Ширина и корреляция по полосам", ("mix", "refs"),
          None, "стерео", "входит в проверку фронтов",
          extra={"part_of": "fronts"}),
    Check("sections", "Сопоставление одинаковых участков", ("mix", "refs"),
          _sections, "микс"),
    Check("roles", "Слышимость ролей (partial loudness)", ("stems",),
          _roles, "роли"),
    Check("speech", "Разборчивость вокала (SII, ANSI S3.5)",
          ("vocal", "masker"), _speech, "роли",
          "нужен вокал отдельно и остальной микс как маскер"),
    Check("pan", "Панорама: статичность и перекрытие ролей", ("stems",),
          _pan, "стерео"),
    Check("effects_ir", "Реверб/дилей: RT60, EDT, C50", ("impulse",),
          _effects_ir, "эффекты", "нужен импульсный отклик цепи"),
    Check("effects_thd", "Нелинейность: THD, THD+N", ("sine",),
          _effects_thd, "эффекты", "нужен синус через цепь"),
    Check("effects_duck", "Дакинг: глубина и частота", ("ducked",),
          _effects_duck, "эффекты", "нужен сигнал с сайдчейном"),
]

GROUP_LABELS = {
    "микс": "Микс целиком",
    "стерео": "Стерео",
    "роли": "Инструменты",
    "эффекты": "Эффекты",
    "общее": "Общее",
}


def missing(check: Check, ctx: dict) -> list[str]:
    """Чего не хватает для проверки (пустой список — можно запускать)."""
    out = []
    for need in check.needs:
        if not ctx.get(need):
            out.append(need)
    if check.fn is None:
        out.append("встроено")
    return out


def run_all(ctx: dict) -> dict:
    """Обойти весь реестр. Возвращает результаты и покрытие."""
    ctx = dict(ctx)
    if not ctx.get("mix"):
        ctx["mix"] = ctx.get("mine")          # «микс» и «наш файл» — одно и то же
    results, skipped, ran, covered = {}, [], [], []
    for chk in REGISTRY:
        part = (chk.extra or {}).get("part_of")
        if part:
            covered.append(chk.id)
            continue
        lack = missing(chk, ctx)
        if lack:
            skipped.append({"id": chk.id, "label": chk.label, "why": lack,
                            "note": chk.note, "group": chk.group})
            continue
        try:
            results[chk.id] = chk.fn(ctx)
            ran.append(chk.id)
        except Exception as exc:
            skipped.append({"id": chk.id, "label": chk.label,
                            "why": ["ошибка"], "note": str(exc)[:120],
                            "group": chk.group})
    return {"results": results, "ran": ran, "covered": covered,
            "skipped": skipped,
            "coverage": "%d из %d" % (len(ran) + len(covered), len(REGISTRY)),
            "complete": not skipped}
