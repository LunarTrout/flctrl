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
    """Заголовок главного окна FL ('' если окна нет).

    pywin32 есть не везде, поэтому основной путь — ctypes: без него все проверки
    «какой проект открыт» молча возвращали пустоту и тесты пропускались.
    """
    try:
        import win32gui
        h = win32gui.FindWindow("TFruityLoopsMainForm", None)
        return win32gui.GetWindowText(h) if h else ""
    except ImportError:
        pass
    import ctypes
    u = ctypes.windll.user32
    h = u.FindWindowW(ctypes.c_wchar_p("TFruityLoopsMainForm"), None)
    if not h:
        return ""
    n = u.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(max(n + 1, 2))
    u.GetWindowTextW(h, buf, max(n + 1, 2))
    return buf.value


def _fl_pids() -> set:
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FL64.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=20).stdout.decode("cp866", "ignore")
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _fl_dialogs() -> list:
    """Модальные окна диалогов, принадлежащие процессу FL."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    pids = _fl_pids()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value != "#32770":
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(max(n + 1, 2))
            u.GetWindowTextW(hwnd, buf, max(n + 1, 2))
            found.append((hwnd, buf.value))
        return True

    u.EnumWindows(cb, 0)
    return found


def fl_is_responsive(timeout_ms: int = 1500) -> bool:
    """Отвечает ли UI-поток FL: WM_NULL через SendMessageTimeout.

    Если не отвечает — FL повисла (обычно на живом вызове через Frida), и
    штатное закрытие невозможно: WM_CLOSE просто не обрабатывается.
    """
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    hwnd = u.FindWindowW(ctypes.c_wchar_p("TFruityLoopsMainForm"), None)
    if not hwnd:
        return not fl_running()
    res = ctypes.c_size_t()
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_size_t)]
    ok = u.SendMessageTimeoutW(hwnd, 0x0000, 0, 0, 0x0002, timeout_ms, ctypes.byref(res))
    return bool(ok)


def fl_msg_dialogs() -> list:
    """Висящие окна «Confirm» (TMsgForm) процесса FL: они блокируют закрытие."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    pids = _fl_pids()
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("TMsgForm", "#32770") and u.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return out


def dismiss_fl_dialogs(max_rounds: int = 12) -> int:
    """Снять висящие окна Confirm: Enter в каждом, пока они не кончатся."""
    import ctypes
    import time

    u = ctypes.windll.user32
    closed = 0
    for _ in range(max_rounds):
        dialogs = fl_msg_dialogs()
        if not dialogs:
            break
        for hwnd in dialogs:
            u.SetForegroundWindow(hwnd)
            u.keybd_event(0x0D, 0, 0, 0)      # VK_RETURN down
            u.keybd_event(0x0D, 0, 2, 0)      # VK_RETURN up
            time.sleep(0.25)
            closed += 1
        time.sleep(0.4)
    return closed


def _press_enter(hwnd: int) -> None:
    import ctypes
    u = ctypes.windll.user32
    u.PostMessageW(hwnd, 0x0100, 0x0D, 0)      # WM_KEYDOWN VK_RETURN
    u.PostMessageW(hwnd, 0x0101, 0x0D, 0)      # WM_KEYUP


def render_target(flp: str, out_name: str | None = None, fmt: str = "wav") -> str:
    """Куда FL должна положить рендер: всегда абсолютный путь с расширением.

    Относительное имя («render») FL принимает и молча ничего не пишет —
    проверено: код возврата 0, файла нет. С абсолютным путём рендер идёт.
    """
    project_dir = os.path.dirname(os.path.abspath(flp))
    out = out_name or (os.path.splitext(os.path.basename(flp))[0] + "_render")
    if not os.path.splitext(out)[1]:
        out += "." + fmt
    return out if os.path.isabs(out) else os.path.join(project_dir, out)


def close_fl(timeout_s: float = 90.0, save_first: bool = True) -> bool:
    """Закрыть FL штатно (как File → Exit), без kill.

    Force-kill ломает MIDI-скрипты FL, поэтому только WM_CLOSE. pywin32 есть не
    везде, поэтому окно ищется через ctypes по классу формы — это тот же
    WM_CLOSE, но без зависимости от стороннего пакета.
    """
    if not fl_running():
        return True
    if save_first:
        try:
            import flsave
            flsave.save(make_backup=False)
        except Exception:
            pass
    h = 0
    try:
        import win32con
        import win32gui
        h = win32gui.FindWindow("TFruityLoopsMainForm", None)
        if h:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    except ImportError:
        import ctypes
        h = ctypes.windll.user32.FindWindowW(ctypes.c_wchar_p("TFruityLoopsMainForm"), None)
        if h:
            ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)
    # Сначала снимаем висящие Confirm: с ними WM_CLOSE не доходит до формы,
    # и FL остаётся висеть до принудительного снятия.
    dismissed = dismiss_fl_dialogs()
    if dismissed:
        print("снято окон подтверждения: %d" % dismissed)

    # Повисшая FL не обрабатывает WM_CLOSE: её UI-поток занят. Держать её
    # бессмысленно — снимаем и говорим об этом прямо.
    if not fl_is_responsive(2000):
        import subprocess
        print("FL не отвечает (UI занят) — снимаю принудительно", flush=True)
        subprocess.run(["taskkill", "/F", "/IM", "FL64.exe"], capture_output=True, timeout=30)
        time.sleep(2.0)
        return not fl_running()

    started = time.time()
    last_poke = started
    handled: set = set()
    while time.time() - started < timeout_s:
        if not fl_running():
            return True
        # Закрытию может мешать модальный диалог («сохранить изменения?», «плагин
        # не найден»). Тогда WM_CLOSE не доходит до формы, и FL висит до kill.
        # Жмём Enter в диалоге: по умолчанию это «да»/«сохранить».
        for hwnd, title in _fl_dialogs():
            if hwnd in handled:
                continue
            handled.add(hwnd)
            _press_enter(hwnd)
        # окно могло не принять первое сообщение (например, был открыт диалог)
        if time.time() - last_poke > 10:
            last_poke = time.time()
            try:
                import ctypes
                hh = ctypes.windll.user32.FindWindowW(
                    ctypes.c_wchar_p("TFruityLoopsMainForm"), None)
                if hh:
                    ctypes.windll.user32.PostMessageW(hh, 0x0010, 0, 0)
            except Exception:
                pass
        time.sleep(1.5)
    if not fl_is_responsive(1500):
        import subprocess
        print("закрытие не сработало, FL не отвечает — снимаю принудительно", flush=True)
        subprocess.run(["taskkill", "/F", "/IM", "FL64.exe"], capture_output=True, timeout=30)
        time.sleep(2.0)
    return not fl_running()


def _wavs(project_dir: str, fmt: str) -> dict:
    """Файлы формата с размером и временем: размер один не годится — повторный
    рендер пишет то же имя с тем же размером, и «нового» файла не видно."""
    out = {}
    for path in glob.glob(os.path.join(project_dir, "**", "*." + fmt), recursive=True):
        try:
            st = os.stat(path)
            out[path] = (st.st_size, st.st_mtime)
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

    target_path = render_target(flp, out_name, fmt)
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    search_dirs = [target_dir, project_dir]

    def collect() -> dict:
        found = {}
        for directory in search_dirs:
            found.update(_wavs(directory, fmt))
        return found

    before = collect()
    started = time.time()
    args = [FL_EXE, "/E" + fmt, "/R" + target_path, flp]
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
                p not in before or now.get(p) != before.get(p) for p in now):
            break
        if not fl_running() and time.time() - started > 20 and not any(
                p not in before or now.get(p) != before.get(p) for p in now):
            break

    after = collect()
    new = [path for path, sig in after.items()
           if path not in before or sig != before[path]]
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
