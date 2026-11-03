"""run_tests.py — один прогон всех тестов библиотеки с понятным итогом.

Зачем: тесты писались по одному и запускались вручную, поэтому «всё зелено»
держалось в голове, а не в машине. Здесь весь набор идёт одним вызовом, каждый
файл в своём процессе (падение одного не рушит остальные), а итог — таблица.

Живые тесты (нужен запущенный FL Studio) по умолчанию пропускаются: они меняют
чужой проект, если он открыт. Запускать их явно: --fl.

    python tools/run_tests.py            # только офлайн
    python tools/run_tests.py --fl       # плюс живой FL
    python tools/run_tests.py -k wall    # только совпавшие по имени
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(ROOT, "tests")

# Живые тесты: им нужен FL Studio и они пишут в открытый проект
LIVE = {"test_clean_project.py", "test_live_plugin.py", "test_live_tracks.py",
        "test_apply_eq.py", "test_realtime_pan.py", "test_play_plan.py"}
TEST_FLP = os.environ.get("FLCTRL_TEST_FLP", r"C:/Users/epicc/tools/fltest/small.flp")


def _python_live() -> str:
    """Живые тесты требуют 3.12: в 3.13 нет frida, а без неё нет стенда."""
    here = os.path.dirname(sys.executable)
    up = os.path.dirname(os.path.dirname(here))
    for p in (os.path.join(up, ".venv312", "Scripts", "python.exe"),
              os.path.join(os.path.dirname(up), ".venv312", "Scripts", "python.exe")):
        if os.path.exists(p):
            return p
    return _python()


def _python() -> str:
    """Интерпретатор для тестов: соседний .venv313, иначе текущий."""
    here = os.path.dirname(sys.executable)
    cand = os.path.join(os.path.dirname(here), ".venv312", "Scripts", "python.exe")
    up = os.path.dirname(os.path.dirname(here))
    for p in (os.path.join(up, ".venv313", "Scripts", "python.exe"),
              os.path.join(up, ".venv312", "Scripts", "python.exe"),
              os.path.join(os.path.dirname(up), ".venv313", "Scripts", "python.exe")):
        if os.path.exists(p):
            return p
    return cand if os.path.exists(cand) else sys.executable


def _ensure_test_project() -> str:
    """Открыть тестовый проект, если FL не запущена. Чужой проект не трогаем.

    Возвращает причину пропуска или пустую строку.
    """
    import subprocess
    import time

    try:
        import render_job
    except Exception as exc:
        return "нет доступа к FL-слою: %s" % str(exc)[:60]

    if not os.path.exists(TEST_FLP):
        return "нет файла тестового проекта: %s" % TEST_FLP
    want = os.path.basename(TEST_FLP).lower()

    def title() -> str:
        try:
            return (render_job.window_title() or "").lower()
        except Exception:
            return ""

    if render_job.fl_running():
        if want in title():
            return ""
        # окно может быть ещё пустым: FL грузится, это не «другой проект»
        t_wait = time.time()
        while time.time() - t_wait < 120:
            t = title()
            if want in t:
                return ""
            if t and ".flp" in t:
                return "открыт другой проект — живой тест не запускаю"
            time.sleep(1.0)
        return "FL не показала проект за 120 с"

    fl_exe = os.environ.get("FLCTRL_FL_EXE") or r"C:\Programs\Fl Studio 2025\FL64.exe"
    # рабочий каталог FL важен: из чужого каталога окно не показывается
    subprocess.Popen([fl_exe, TEST_FLP], close_fds=True,
                     cwd=os.path.dirname(fl_exe))
    t0 = time.time()
    while time.time() - t0 < 180:
        if want in title():
            return ""
        time.sleep(1.0)
    return "тестовый проект не открылся"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Все тесты библиотеки")
    ap.add_argument("--fl", action="store_true", help="включить живые тесты FL")
    ap.add_argument("-k", default=None, help="фильтр по имени файла")
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args(argv)

    files = sorted(f for f in os.listdir(TESTS)
                   if f.startswith("test_") and f.endswith(".py"))
    if a.k:
        files = [f for f in files if a.k in f]
    if not files:
        print("нет тестов по фильтру", a.k)
        return 2

    py = _python()
    py_live = _python_live()
    sys.path.insert(0, os.path.join(ROOT, "flctrl"))
    rows = []
    for name in files:
        if name in LIVE and not a.fl:
            rows.append((name, "пропущен", 0.0, "нужен FL (--fl)"))
            continue
        if name in LIVE:
            why = _ensure_test_project()
            if why:
                rows.append((name, "пропущен", 0.0, why))
                continue
        t0 = time.perf_counter()
        runner = py_live if name in LIVE else py
        try:
            r = subprocess.run([runner, os.path.join(TESTS, name)], cwd=ROOT,
                               capture_output=True, timeout=a.timeout)
            out = (r.stdout or b"").decode("utf-8", "replace")
            tail = ""
            for line in out.splitlines():
                if line.startswith("итог:") or line.startswith("итого:"):
                    tail = line.strip()
            if not tail:
                err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                tail = err[-1][:120] if err else "нет итога"
            status = "ок" if r.returncode == 0 else "ПАДЕНИЕ"
        except subprocess.TimeoutExpired:
            status, tail = "ПАДЕНИЕ", "таймаут %.0f с" % a.timeout
        rows.append((name, status, time.perf_counter() - t0, tail))

    print("\n%-26s %-10s %7s  %s" % ("тест", "статус", "сек", "итог"))
    print("-" * 74)
    bad = 0
    for name, status, dur, tail in rows:
        if status == "ПАДЕНИЕ":
            bad += 1
        print("%-26s %-10s %7.1f  %s" % (name, status, dur, tail))
    ran = sum(1 for r in rows if r[1] != "пропущен")
    print("-" * 74)
    print("файлов: %d, запущено: %d, провалов: %d" % (len(rows), ran, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
