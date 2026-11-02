"""Рендер проекта: командная строка FL, сплит по дорожкам и сторож прогресса.

Зачем отдельный модуль: диалог экспорта FL автоматизируется плохо (DirectUI,
кнопки не берут BM_CLICK), а CLI-рендер работает без рук. Сплит по дорожкам
включается в самом проекте, и тогда FL выдаёт по wav на дорожку.

Что здесь есть:
- fl_pid()/fl_running() — запущена ли FL (нужен CSV tasklist: обычный вывод
  в cp866, utf-8 на нём падает).
- close_fl() — штатное закрытие главного окна (CLI не стартует при живой FL).
- render(flp, ...) — запуск рендера и сбор ВСЕХ появившихся файлов, а не одного.
- watch(...) — ожидание с прогрессом «Rendering: N/M», детектом падения
  (pid сменился/исчез) и списком созданных файлов.
"""
import glob
import os
import subprocess
import time

FL_EXE = os.environ.get("FLCTRL_FL_EXE") or r"C:\Programs\Fl Studio 2025\FL64.exe"


def fl_pid() -> int:
    """pid процесса FL64 или 0. tasklist отдаёт cp866, не utf-8."""
    proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV"],
                          capture_output=True)
    text = (proc.stdout or b"").decode("cp866", errors="ignore")
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) > 1 and parts[0].lower() == "fl64.exe":
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def fl_running() -> bool:
    return fl_pid() != 0


def window_title() -> str:
    """Заголовок главного окна FL ('' если окна нет)."""
    try:
        import win32gui
    except ImportError:
        return ""
    h = win32gui.FindWindow("TFruityLoopsMainForm", None)
    return win32gui.GetWindowText(h) if h else ""


def close_fl(timeout_s: float = 90.0) -> bool:
    """Закрыть FL штатно (как File → Exit), без kill.

    Force-kill ломает MIDI-скрипты FL, поэтому только WM_CLOSE.
    """
    try:
        import win32con
        import win32gui
    except ImportError:
        return not fl_running()
    h = win32gui.FindWindow("TFruityLoopsMainForm", None)
    if h:
        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    started = time.time()
    while time.time() - started < timeout_s:
        if not fl_running():
            return True
        time.sleep(1.5)
    return not fl_running()


def _wavs(project_dir: str, fmt: str) -> dict:
    out = {}
    for path in glob.glob(os.path.join(project_dir, "**", "*." + fmt), recursive=True):
        try:
            out[path] = os.path.getsize(path)
        except OSError:
            continue
    return out


def _parse_progress(title: str) -> tuple[int, int] | None:
    if not title.startswith("Rendering"):
        return None
    try:
        part = title.split(":", 1)[1].strip()
        done, total = part.split("/")
        return int(done), int(total)
    except (IndexError, ValueError):
        return None


def watch(project_dir: str, since: float | None = None, fmt: str = "wav",
          timeout_s: float = 3600, poll_s: float = 4.0) -> dict:
    """Дождаться окончания рендера и вернуть прогресс, падение и файлы.

    Падение распознаётся по исчезновению процесса или смене pid: FL после
    падения перезапускается сама, и «рендер закончился» без файлов — это
    именно падение, а не успешный рендер.
    """
    since = time.time() if since is None else since
    pid0 = fl_pid()
    log: list[str] = []
    progress = (0, 0)
    crashed = False
    started = time.time()

    while time.time() - started < timeout_s:
        pid = fl_pid()
        title = window_title()
        p = _parse_progress(title)
        if p and p != progress:
            progress = p
            log.append("%s %d/%d" % (time.strftime("%H:%M:%S"), p[0], p[1]))
        if pid == 0 or (pid0 and pid != pid0):
            crashed = True
            log.append("%s FL пропала (была %s, стала %s)"
                       % (time.strftime("%H:%M:%S"), pid0, pid))
            break
        if p is None and progress[0]:
            time.sleep(8.0)      # дать FL дописать файлы
            break
        time.sleep(poll_s)

    files = [(path, size) for path, size in _wavs(project_dir, fmt).items()
             if os.path.getmtime(path) >= since - 60]
    files.sort(key=lambda item: item[0])
    return {"crashed": crashed, "progress": progress, "log": log[-40:],
            "elapsed_s": round(time.time() - started, 1),
            "files": [{"path": path, "size": size} for path, size in files],
            "split": len(files) > 1}


def render(flp: str, fmt: str = "wav", timeout_s: float = 3600,
           close_fl_first: bool = False, out_name: str | None = None) -> dict:
    """Отрендерить проект через CLI FL.

    FL пишет в папку экспорта проекта и путь из /R игнорирует, поэтому
    ориентируемся на появившиеся файлы, а не на переданное имя.
    Если в проекте включён Split mixer tracks — вернёт по файлу на дорожку.
    """
    project_dir = os.path.dirname(os.path.abspath(flp))
    if fl_running():
        if not close_fl_first:
            raise RuntimeError("FL запущена: CLI-рендер требует закрытой FL")
        if not close_fl():
            raise RuntimeError("FL не закрылась штатно")

    # FL понимает /R как папку или как имя файла: если передали каталог,
    # результат уйдёт туда, иначе — в папку проекта с именем проекта.
    out = out_name or "render"
    target_dir = out if os.path.isabs(out) else os.path.join(project_dir, out)
    if not os.path.splitext(out)[1]:
        os.makedirs(target_dir, exist_ok=True)
        search_dirs = [target_dir, project_dir]
    else:
        search_dirs = [os.path.dirname(target_dir) or project_dir, project_dir]

    def collect() -> dict:
        found = {}
        for directory in search_dirs:
            found.update(_wavs(directory, fmt))
        return found

    before = collect()
    started = time.time()
    args = [FL_EXE, "/E" + fmt, "/R" + out, flp]
    subprocess.Popen(args, cwd=os.path.dirname(FL_EXE),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    last_change = time.time()
    seen = dict(before)
    while time.time() - started < timeout_s:
        time.sleep(3.0)
        now = collect()
        if now != seen:
            seen = now
            last_change = time.time()
        elif time.time() - last_change > 25 and any(
                path not in before for path in now):
            break
        if not fl_running() and time.time() - started > 20 and not any(
                path not in before for path in now):
            break

    new = [path for path in collect() if path not in before]
    return {"ok": bool(new), "files": sorted(new),
            "split": len(new) > 1,
            "elapsed_s": round(time.time() - started, 1)}


def cleanup(files: list[str]) -> int:
    """Удалить созданные рендером файлы (для проверок и повторных прогонов)."""
    removed = 0
    for path in files:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            continue
    return removed
