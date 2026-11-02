"""Проверка библиотеки на чистом проекте.

Запускается отдельно от дымовых тестов: открывает простой проект FL (по
умолчанию `FLCTRL_TEST_FLP` или `~/tools/fltest/small.flp`), прогоняет связь,
чтение, правку, слух и рендер, потом закрывает.

Зачем отдельно: дымовые тесты проверяют файловые слои без FL, а здесь —
живой проект, но заведомо не рабочий, чтобы не трогать чужую сессию.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "flctrl"))

import fl_lib as fl          # noqa: E402
import render_job            # noqa: E402

TEST = Path(os.environ.get("FLCTRL_TEST_FLP")
            or Path.home() / "tools" / "fltest" / "small.flp")
FL = os.environ.get("FLCTRL_FL_EXE") or r"C:\Programs\Fl Studio 2025\FL64.exe"
PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print("ок      %-32s %s" % (name, str(detail)[:60]))
    except Exception as e:
        FAIL.append((name, str(e)[:120]))
        print("ПАДЕНИЕ %-32s %s" % (name, str(e)[:80]))


def wait_project(needle: str, timeout: float = 180.0) -> str:
    started = time.time()
    while time.time() - started < timeout:
        title = render_job.window_title()
        if title and needle.lower() in title.lower():
            return title
        time.sleep(2.0)
    return render_job.window_title()


def main() -> int:
    if not TEST.exists():
        print("нет тестового проекта:", TEST)
        return 2
    if render_job.fl_running():
        print("закрываю FL (чужой проект не трогаем — он должен быть сохранён)")
        render_job.close_fl()
    subprocess.Popen([FL, str(TEST)], cwd=str(TEST.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    title = wait_project(TEST.stem)
    print("открыт:", title)
    time.sleep(5.0)

    check("связь: здоровье", lambda: fl.state.health())
    check("чтение: дорожки", lambda: "%d дорожек" % len(fl.state.tracks()))
    check("чтение: темп", fl.state.tempo)

    def set_volume():
        fl.act.volume(1, 0.4)
        time.sleep(0.4)
        got = fl.state.tracks()[1]["vol"]
        assert abs(float(got) - 0.4) < 0.02, "фейдер не встал: %r" % got
        return "0.4 подтверждено"
    check("правка: фейдер + чтение", set_volume)

    def measure_file():
        import numpy as np
        import soundfile as sf
        tmp = HERE / "_cache" / "clean_test.wav"
        tmp.parent.mkdir(exist_ok=True)
        t = np.arange(48000 * 3) / 48000.0
        sf.write(tmp, 0.3 * np.sin(2 * np.pi * 440 * t), 48000)
        m = fl.hear.measure(str(tmp))
        assert m["regions_pct"]["mid"] > 0, "пустой замер"
        return "mid %.0f%%" % m["regions_pct"]["mid"]
    check("слух: замер файла", measure_file)

    def sections():
        res = fl.hear.sections(HERE / "_cache" / "clean_test.wav")
        assert isinstance(res, list), "секции не списком"
        return "%d секций" % len(res)
    check("слух: секции", sections)

    def render():
        res = fl.create.render(str(TEST), timeout_s=600, close_fl=True)
        assert res["ok"], "рендер не дал файла"
        return "%d файл(ов)" % len(res["files"])
    check("рендер: CLI", render)

    print("\nитог: %d прошло, %d упало" % (len(PASS), len(FAIL)))
    for name, err in FAIL:
        print("  -", name, ":", err)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
