"""Метры дорожек: замер по пику, транспорт возвращается как был.

Тестовый проект тихий, поэтому числа тут не важны — проверяем механику:
замер отрабатывает, транспорт останавливается, позиция возвращается,
тихие дорожки честно помечены.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import fl_lib as fl  # noqa: E402


def _guard() -> bool:
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
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    if not _guard():
        return 2
    h = fl.state.health()
    if not h.get("bridge"):
        print("мост не отвечает")
        return 2

    pos_before = fl._State._live("transport.getSongPos()", attempts=2)
    r = fl.state.meters(seconds=1.0, play=True)
    check("замер вернул дорожки", len(r["tracks"]) == h.get("tracks"),
          "%d из %s" % (len(r["tracks"]), h.get("tracks")))
    check("замер шёл, транспорт включали мы", r["played_by_us"] is True, str(r["played_by_us"]))
    check("после замера транспорт стоит",
          int(fl._State._live("transport.isPlaying()", attempts=2)) == 0, "")
    pos_after = fl._State._live("transport.getSongPos()", attempts=2)
    check("позиция восстановлена", abs(float(pos_after) - float(pos_before)) < 0.2,
          "%.3f → %.3f" % (float(pos_before), float(pos_after)))
    check("у каждой дорожки есть поля",
          all("peak_db" in v and "median_db" in v and "silent" in v
              for v in r["tracks"].values()), "")
    check("покрытие перечисляет тишину",
          isinstance(r["coverage"].get("silent"), list),
          "%d тихих" % len(r["coverage"]["silent"]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            fl._State._live("transport.stop()", attempts=1)
        except Exception:
            pass
