"""Пути и глаголы: один объект вместо семидесяти функций.

Проверяем на живом FL: список, чтение, запись с откатом, отказы по типам.
Работает только на тестовом проекте из tools/fltest — рабочие проекты не трогает.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import fl_lib as fl  # noqa: E402
import tree  # noqa: E402

TEST_PREFIX = "tools/fltest"


def _guard() -> bool:
    import render_job
    title = (render_job.window_title() or "").replace("\\", "/")
    if TEST_PREFIX not in title:
        print("открыт не тестовый проект (%r) — тест остановлен" % title[:70])
        return False
    return True


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    if not _guard():
        return 2

    # --- список
    root = tree.list_node("/")
    check("корень перечисляет узлы", any(x["path"] == "/track" for x in root), str(len(root)))
    tracks = tree.list_node("/track")
    check("список дорожек не пуст", len(tracks) > 1, "%d дорожек" % len(tracks))

    one = tree.list_node("/track/1")
    check("дорожка отдаёт поля", any(x["path"] == "/track/1/volume" for x in one), str(len(one)))

    # --- чтение
    v = tree.get("/track/1/volume")
    check("громкость читается", isinstance(v.get("value"), float), str(v))
    wild = tree.get("/track/*/volume")
    check("звёздочка даёт список", len(wild) == len(tracks), "%d значений" % len(wild))

    # --- запись и откат
    before = v["value"]
    target = 0.42 if abs(before - 0.42) > 0.02 else 0.55
    ch = tree.set_value("/track/1/volume", target)
    check("запись вернула было/стало",
          abs(float(ch["before"]) - before) < 1e-6 and abs(float(ch["after"]) - target) < 0.02,
          "%.3f → %.3f" % (ch["before"], ch["after"]))
    tree.set_value("/track/1/volume", before)
    check("значение вернулось", abs(float(tree.get("/track/1/volume")["value"]) - before) < 0.02,
          "%.3f" % tree.get("/track/1/volume")["value"])

    # --- отказы
    try:
        tree.set_value("/track/1/volume", 5.0)
        check("диапазон отвергнут", False, "приняло 5.0")
    except tree.TreeError as e:
        check("диапазон отвергнут", e.code == "range", e.code)
    try:
        tree.get("/track/9999/volume")
        check("несуществующий путь отвергнут", False, "")
    except tree.TreeError as e:
        check("несуществующий путь отвергнут", e.code in ("path_not_found", "range"), e.code)

    # --- действия: плагин, полоса, удаление
    slot = 9
    ins = tree.call("plugin.insert", {"track": 1, "slot": slot, "name": "Fruity Parametric EQ 2"})
    check("плагин вставлен", bool(ins.get("ok")), str(ins.get("ok")))
    info = fl.apply.info(1, slot)
    check("плагин виден как эквалайзер", info["is_eq"], "полос %d" % len(info["bands"]))

    res = tree.set_value("/track/1/slot/9/band/2", {"hz": 900, "gain": -2.0})
    after = tree.get("/track/1/slot/9/band/2")
    check("полоса записана через путь",
          abs((after.get("hz") or 0) - 900) < 150 and abs((after.get("gain") or 0) + 2.0) < 0.7,
          "%.0f Гц / %.1f дБ" % (after.get("hz") or 0, after.get("gain") or 0))

    tree.call("plugin.delete", {"track": 1, "slot": slot})
    check("плагин удалён", fl.apply.info(1, slot)["used"] is False, "")

    # --- пачка атомарно
    vol_before = tree.get("/track/2/volume")["value"]
    pan_before = tree.get("/track/2/pan")["value"]
    tree.set_many([{"path": "/track/2/volume", "value": 0.5},
                   {"path": "/track/2/pan", "value": 0.25}])
    ok = (abs(tree.get("/track/2/volume")["value"] - 0.5) < 0.02
          and abs(tree.get("/track/2/pan")["value"] - 0.25) < 0.02)
    check("пачка применилась", ok, "")
    tree.set_many([{"path": "/track/2/volume", "value": vol_before},
                   {"path": "/track/2/pan", "value": pan_before}])

    bad = False
    try:
        tree.set_many([{"path": "/track/2/volume", "value": 0.4},
                       {"path": "/track/2/volume", "value": 9.0}])
    except tree.TreeError:
        bad = True
    check("пачка с ошибкой откатилась",
          bad and abs(tree.get("/track/2/volume")["value"] - vol_before) < 0.02,
          "%.3f" % tree.get("/track/2/volume")["value"])

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
