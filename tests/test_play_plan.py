"""Живая проверка: план панорамы играется сразу на нескольких дорожках.

Главное свойство — парность. Если левая гитара уходит влево, правая должна в
тот же момент уходить вправо, а не «когда поток проснётся». Поэтому движение
идёт одним потоком с общим тактом.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import fl_lib as fl  # noqa: E402

A, B = 1, 2
SEC = 8.0
DUR = 3.0



def _guard() -> bool:
    """Живые тесты пишут в проект — пускать их можно только в тестовый."""
    import render_job
    title = (render_job.window_title() or "").lower()
    if "small" not in title:
        print("открыт не тестовый проект (%r) — тест остановлен" % title[:60])
        return False
    return True


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    if not _guard():
        return 2
    h = fl.state.health()
    print("FL:", h.get("project"), "| дорожек:", h.get("tracks"))
    if not h.get("bridge"):
        print("нет живого управления:", h.get("hint"))
        return 2

    plan = {"section_s": SEC, "stems": {
        "left": {"points": [{"t": 0.0, "pan": -0.7}, {"t": DUR, "pan": -0.2}]},
        "right": {"points": [{"t": 0.0, "pan": 0.7}, {"t": DUR, "pan": 0.2}]},
    }}
    before = {A: fl.state.tracks()[A]["pan"], B: fl.state.tracks()[B]["pan"]}
    # стартовые позиции ставим сами: иначе первый замер ловит состояние «до»
    fl.act.pan(A, -0.7)
    fl.act.pan(B, 0.7)

    res = fl.rt.play_plan(plan, {"left": A, "right": B}, duration_s=DUR, rate_hz=25)
    check("движение запущено", bool(res.get("mover")), str(res.get("error", "")))

    samples = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < DUR:
        if time.perf_counter() - t0 > 0.4:      # прогрев: первый шаг уже записан
            samples.append((float(fl._State._live("mixer.getTrackPan(%d)" % A)),
                            float(fl._State._live("mixer.getTrackPan(%d)" % B))))
        time.sleep(0.2)
    stats = res["mover"].stop()

    check("обе дорожки писались", stats["writes"] >= 40, "записей %d" % stats["writes"])
    check("ошибок нет", stats["errors"] == 0, stats["last_error"][:60])

    first, last = samples[0], samples[-1]
    check("левая ушла к центру", last[0] > first[0] + 0.15,
          "%.2f → %.2f" % (first[0], last[0]))
    check("правая ушла к центру", last[1] < first[1] - 0.15,
          "%.2f → %.2f" % (first[1], last[1]))
    # парность: сумма позиций держится около нуля на всём протяжении
    sums = [round(a + b, 2) for a, b in samples]
    check("пара остаётся зеркальной", bool(sums) and all(abs(s) <= 0.3 for s in sums),
          str(sums[:6]))

    for track, value in before.items():
        fl.act.pan(track, value)
    check("панорамы возвращены",
          abs(fl.state.tracks()[A]["pan"] - before[A]) <= 0.05
          and abs(fl.state.tracks()[B]["pan"] - before[B]) <= 0.05, "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
