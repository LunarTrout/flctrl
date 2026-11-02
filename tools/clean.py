"""clean.py -- уборка рабочих артефактов.

Что чистит:
  _cache/         кэш замеров, эмбеддингов, захватов (по возрасту)
  __pycache__/    байткод
  *.log           журналы стенда
  *.png           скриншоты отладки

По умолчанию — только то, что старше 7 дней. Свежее не трогает: в нём может
идти работа.

    python clean.py --days 7 --dry     показать, что будет удалено
    python clean.py --days 7           удалить
    python clean.py --all              снести всё старше суток
"""
from __future__ import annotations

import argparse
import os
import shutil
import time

KEEP_DIRS = {"_cache", "__pycache__"}
KEEP_SUFFIX = (".log", ".png", ".tmp", ".bak")


def collect(root: str, days: float, dry: bool) -> tuple[int, int, int]:
    limit = time.time() - days * 86400
    files = 0
    freed = 0
    dirs = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirpath.split(os.sep) or ".venv" in dirpath:
            continue
        for d in list(dirnames):
            if d in KEEP_DIRS:
                full = os.path.join(dirpath, d)
                try:
                    newest = max((os.path.getmtime(os.path.join(full, f))
                                  for f in os.listdir(full)), default=0)
                except OSError:
                    continue
                if newest < limit:
                    size = sum(os.path.getsize(os.path.join(dp, f))
                               for dp, _dn, fn in os.walk(full) for f in fn
                               if os.path.exists(os.path.join(dp, f)))
                    if dry:
                        print("  каталог %s (%.1f МБ)" % (full, size / 1e6))
                    else:
                        shutil.rmtree(full, ignore_errors=True)
                    dirs += 1
                    freed += size
                    dirnames.remove(d)
                continue
        for f in filenames:
            if not f.endswith(KEEP_SUFFIX):
                continue
            full = os.path.join(dirpath, f)
            try:
                if os.path.getmtime(full) >= limit:
                    continue
                size = os.path.getsize(full)
                if dry:
                    print("  файл %s (%.1f КБ)" % (full, size / 1024))
                else:
                    os.remove(full)
                files += 1
                freed += size
            except OSError:
                continue
    return files, dirs, freed


def main() -> int:
    ap = argparse.ArgumentParser(description="уборка рабочих артефактов")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    help="что убирать (по умолчанию — корень репозитория)")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--all", action="store_true", help="всё старше суток")
    ap.add_argument("--dry", action="store_true", help="только показать")
    a = ap.parse_args()
    days = 1.0 if a.all else a.days
    print("уборка в %s, старше %.1f дн.%s" % (a.root, days, " (пробный прогон)" if a.dry else ""))
    files, dirs, freed = collect(a.root, days, a.dry)
    print("итого: файлов %d, каталогов %d, освобождено %.1f МБ" % (files, dirs, freed / 1e6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
