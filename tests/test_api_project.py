"""Файловое время: точный темп, плейлист, провенанс в момент времени.

FL не нужен: читается .flp. Проверяем на демо-копии.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import api  # noqa: E402

FLP = r"C:/Users/epicc/tools/fltest/fixtures/demo2.flp"


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-48s %s" % (name, detail))

    if not os.path.exists(FLP):
        print("нет файла", FLP)
        return 2

    info = api.use_project(FLP)
    check("проект разобран", info["clips"] > 0, str(info))

    pr = api.get("/project")
    check("темп точный (126.0, не 115)", abs(pr["tempo"] - 126.0) < 0.01, str(pr["tempo"]))
    check("размер и ppq", pr["time_sig"] == "4/4" and pr["ppq"] == 96, str(pr))
    check("источник — файл", pr["source"] == "file", pr["source"])

    pl = api.get("/playlist")
    check("клипов больше 600", pl["clips"] > 600, str(pl["clips"]))

    clip = api.get("/playlist/clip/0")
    check("клип отдаёт позицию и длину", clip["length_s"] > 0, str(clip))

    at = api.call("clip.at", {"seconds": 60.0})
    check("в момент времени есть источники", at["count"] > 0, str(at["count"]))
    check("у источника есть локальное время",
          all("local_seconds" in x for x in at["active"]), "")
    check("такт считается из темпа", at["bar"] > 0, str(at["bar"]))

    none = api.call("clip.at", {"seconds": 3.0})
    check("ранний момент тоже отвечает", isinstance(none.get("active"), list), "")

    wav = r"C:/Users/epicc/tools/fltest/fixtures/demo2.wav"
    if os.path.exists(wav):
        f = api.measure({"metric": "bands", "source": wav, "a": 0, "b": 40,
                         "by": "section", "sections_from": "markers"})
        check("секции по маркерам", len(f["axes"]["section"]) >= 2,
              str(f["axes"]["section"]))

    cov = api.help("coverage")
    check("покрытие перечисляет метрики", len(cov["rows"]) >= 10, str(len(cov["rows"])))
    check("покрытие знает пропуски", isinstance(cov["missing"], list), str(cov["missing"][:3]))
    check("покрытие видит измеренное", "bands" in cov["measured"], str(cov["measured"][:4]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
