"""Живая проверка движения панорамы в реальном времени.

Проверяем не «код запустился», а что значение действительно едет: снимаем
панораму дорожки во время движения и смотрим, что она монотонно идёт от
начала к концу, а ошибок записи почти нет.

Нужен запущенный FL с тестовым проектом.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import fl_lib as fl  # noqa: E402
import realtime as rt  # noqa: E402

TRACK = 1
SECONDS = 3.0



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

    before = fl.state.tracks()[TRACK]["pan"]
    res = fl.move_pan(TRACK, [(0.0, -0.6), (SECONDS, 0.6)], SECONDS, rate_hz=25)
    samples = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < SECONDS:
        samples.append((round(time.perf_counter() - t0, 2),
                        fl.state.tracks()[TRACK]["pan"]))
        time.sleep(0.25)
    stats = res["mover"].stop()

    pans = [p for _t, p in samples]
    check("движение запустилось", stats["writes"] > 20, "записей %d" % stats["writes"])
    check("ошибок записи почти нет", stats["errors"] <= 2,
          "ошибок %d, последняя: %s" % (stats["errors"], stats["last_error"][:50]))
    check("панорама поехала вправо", pans[-1] > pans[0] + 0.4,
          "%.2f → %.2f" % (pans[0], pans[-1]))
    check("движение без возвратов", all(b >= a - 0.12 for a, b in zip(pans, pans[1:])),
          str(pans[:6]))
    check("дошло почти до цели", abs(pans[-1] - 0.6) <= 0.25, "%.2f при цели 0.60" % pans[-1])

    fl.act.pan(TRACK, before)
    check("панорама возвращена", abs(fl.state.tracks()[TRACK]["pan"] - before) <= 0.05,
          "было %.2f" % before)

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


def _stop():
    """Никакой тест не должен оставлять транспорт играющим."""
    try:
        import fl_lib as _fl
        _fl._State._live("transport.stop()", attempts=1)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        _stop()
