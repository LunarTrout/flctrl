"""Кэш измерений: не считать одно и то же, но и не отдавать старьё.

Главная опасность кэша — тихо вернуть устаревшие числа после правки файла.
Поэтому ключ включает размер и время правки, и это проверяется здесь.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import cache  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flcache_")
    os.environ["FLCTRL_CACHE"] = os.path.join(tmp, "store")
    os.environ.pop("FLCTRL_NO_CACHE", None)

    audio = os.path.join(tmp, "a.wav")
    with open(audio, "wb") as f:
        f.write(b"0" * 1024)

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"value": 41 + calls["n"]}

    first = cache.memo("t", [audio, 0.0, 10.0], compute)
    second = cache.memo("t", [audio, 0.0, 10.0], compute)
    check("первый вызов считает", first["value"] == 42, str(first))
    check("второй берёт из кэша", second == first and calls["n"] == 1,
          "вызовов %d" % calls["n"])

    third = cache.memo("t", [audio, 0.0, 20.0], compute)
    check("другое окно — другой ключ", third["value"] == 43 and calls["n"] == 2,
          "вызовов %d" % calls["n"])

    time.sleep(0.05)
    with open(audio, "ab") as f:
        f.write(b"1" * 16)
    fourth = cache.memo("t", [audio, 0.0, 10.0], compute)
    check("правка файла сбрасывает кэш", fourth["value"] == 44 and calls["n"] == 3,
          "вызовов %d" % calls["n"])

    os.environ["FLCTRL_NO_CACHE"] = "1"
    before = calls["n"]
    cache.memo("t", [audio, 0.0, 10.0], compute)
    check("отключение работает", calls["n"] == before + 1, "вызовов %d" % calls["n"])
    check("при отключении ничего не пишется", cache.get("t", [audio, 0.0, 10.0]) is None, "")
    os.environ.pop("FLCTRL_NO_CACHE", None)

    st = cache.stats()
    check("статистика видит записи", st["files"] >= 1, str(st["files"]))
    res = cache.clear("t")
    check("очистка по области", res["removed"] >= 1, str(res["removed"]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
