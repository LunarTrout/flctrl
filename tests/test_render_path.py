"""Путь рендера: FL молча ничего не пишет при относительном имени.

Проверяем построение пути, потому что это единственная часть рендера, которую
можно проверить без запуска FL: остальное — сам FL.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import render_job as R  # noqa: E402

PROJ = r"C:/Users/epicc/tools/fltest/small.flp"
DIR = os.path.normcase(os.path.normpath(os.path.dirname(PROJ)))


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-52s %s" % (name, detail))

    t = R.render_target(PROJ)
    check("по умолчанию — абсолютный путь", os.path.isabs(t), t)
    check("имя проекта в имени файла", t.endswith("small_render.wav"), t)
    check("в папке проекта", _same(os.path.dirname(t), DIR), os.path.dirname(t))

    t = R.render_target(PROJ, "probe")
    check("имя без расширения получает .wav", t.endswith("probe.wav"), t)

    t = R.render_target(PROJ, "probe.flac", fmt="flac")
    check("расширение не дублируется", t.endswith("probe.flac") and not t.endswith(".flac.flac"), t)

    t = R.render_target(PROJ, "sub/take.wav")
    check("относительный подкаталог — в папке проекта",
          os.path.isabs(t) and _same(os.path.dirname(t), os.path.join(DIR, "sub")), t)

    t = R.render_target(PROJ, r"D:/renders/x.wav")
    check("абсолютный путь не переписывается", t == r"D:/renders/x.wav", t)

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
