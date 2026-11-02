"""Дымовые тесты библиотеки: то, что должно работать всегда.

Запуск:  ../.venv313/Scripts/python.exe tests/test_smoke.py

Живой FL не нужен. Проверяются слои, работающие на файлах:
чтение проекта, замер, полосы, песочница, предсказание, приёмка.
Живые методы (tracks/channels/plugins/routing/tempo) сюда не входят —
они требуют запущенного FL и проверяются отдельно.
"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(HERE, "flctrl")
sys.path.insert(0, PKG)
sys.path.insert(0, HERE)

import numpy as np

FLP = os.environ.get("FLCTRL_TEST_FLP", r"C:/Users/epicc/Desktop/PROJECTS/vers1/vers1.flp")
WAV = os.path.join(HERE, "_cache", "desc", "mix25.wav")


def make_test_wav(path: str, seconds: float = 20.0, sr: int = 44100) -> None:
    """Синтетический материал, если своего wav нет.

    Смесь синусов с шумом и затуханием — достаточно, чтобы замер, полосы,
    кривая и песочница считались. Стерео, чтобы работали метрики ширины.
    """
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rng = np.random.default_rng(7)
    t = np.arange(int(seconds * sr)) / sr
    env = np.exp(-0.5 * (t % 4.0))          # пульсация раз в 4 секунды
    left = 0.5 * np.sin(2 * np.pi * 55 * t) + 0.2 * np.sin(2 * np.pi * 440 * t)
    right = 0.5 * np.sin(2 * np.pi * 55 * t + 0.3) + 0.2 * np.sin(2 * np.pi * 660 * t)
    noise = rng.normal(0, 0.05, len(t))
    x = np.stack([(left + noise) * env, (right + noise) * env], axis=1).astype(np.float32)
    x /= max(1e-9, np.abs(x).max()) * 1.1
    try:
        import soundfile as sf
        sf.write(path, x, sr)
    except Exception:
        import wave
        with wave.open(path, "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(sr)
            f.writeframes((x * 32767).astype("<i2").tobytes())

PASS, FAIL, SKIP = [], [], []


def check(name: str, fn):
    try:
        fn()
        PASS.append(name)
        print("  ок       %s" % name)
    except Exception as e:
        FAIL.append((name, repr(e)))
        print("  ПАДЕНИЕ  %s: %s" % (name, repr(e)[:160]))
        traceback.print_exc(limit=2)


def skip(name: str, why: str):
    SKIP.append((name, why))
    print("  пропуск  %s (%s)" % (name, why))


def main() -> int:
    import fl_lib as fl

    make_test_wav(WAV)

    print("== state: чтение проекта (файл)")
    if not os.path.exists(FLP):
        skip("state.*", "нет проекта")
    else:
        def t_playlist():
            r = fl.state.playlist(FLP)
            assert isinstance(r, dict), "плейлист не словарь: %s" % type(r).__name__
            n = sum(len(v) for v in r.values())
            assert n >= 10, "клипов на плейлисте: %d" % n
            print("       дорожек: %d, клипов: %d" % (len(r), n))

        def t_clip_files():
            r = fl.state.clip_files(FLP)
            assert isinstance(r, list) and len(r) >= 10, "клипы не прочитаны"
            named = sum(1 for v in r if v.get("sample"))
            print("       записей: %d, с файлом: %d" % (len(r), named))

        def t_automation():
            r = fl.state.automation(FLP)
            assert isinstance(r, list), "автоматизация не список"
            print("       автоматизаций: %d" % len(r))

        check("playlist", t_playlist)
        check("clip_files", t_clip_files)
        check("automation", t_automation)

    skip("tracks/channels/plugins/routing/tempo", "только живьём")

    if not os.path.exists(WAV):
        skip("hear.*", "нет тестового wav")
    else:
        print("== hear: замер")

        def t_measure():
            r = fl.hear.measure(WAV, 0, 5)
            for k in ("N_mean", "lufs", "crest_db"):
                assert k in r, "нет метрики " + k
            sharp = [k for k in r if k.startswith("sharp")]
            assert sharp, "нет резкости вовсе: %s" % sorted(r)[:20]
            assert 20 < r["N_mean"] < 140, "соны вне разумного: %r" % r["N_mean"]
            assert -45 < r["lufs"] < 0, "LUFS вне разумного: %r" % r["lufs"]

        def t_bands():
            r = fl.hear.bands(WAV)
            assert isinstance(r, dict), "полосы не словарь"
            for k in ("level", "width", "mod"):
                assert k in r, "нет оси " + k
                assert 100 < len(r[k]) < 500, "странное число полос у " + k

        check("measure", t_measure)
        check("bands", t_bands)

        def t_curve():
            r = fl.hear.curve(WAV)
            assert r is not None, "кривая пустая"
            n = len(r) if hasattr(r, "__len__") else None
            assert n is None or 2 < n < 5000, "странная длина кривой: %r" % n

        def t_profile():
            r = fl.hear.profile(WAV)
            assert isinstance(r, dict) and r, "профиль пустой"
            print("       полей профиля: %d" % len(r))

        def t_sections():
            r = fl.hear.sections(WAV)
            assert r is not None, "секции не посчитались"

        def t_match():
            r = fl.hear.match(WAV, WAV)
            assert r is not None, "сопоставление не прошло"

        check("curve", t_curve)
        check("profile", t_profile)
        check("sections", t_sections)
        check("match (сам с собой)", t_match)

        print("== sim: песочница")
        def t_sim_identity():
            x, fs = fl.sim.load(WAV, 0, 5)
            y = fl.sim.run(x, fs, [{"op": "gain", "db": 0.0}])
            assert y.shape == x.shape, "длина изменилась"
            d = 20 * np.log10((np.sqrt(np.mean(y ** 2)) + 1e-12) /
                              (np.sqrt(np.mean(x ** 2)) + 1e-12))
            assert abs(d) < 0.05, "нулевое усиление сдвинуло уровень на %.2f дБ" % d

        def t_sim_gain():
            x, fs = fl.sim.load(WAV, 0, 5)
            y = fl.sim.run(x, fs, [{"op": "gain", "db": -6.0}])
            d = 20 * np.log10((np.sqrt(np.mean(y ** 2)) + 1e-12) /
                              (np.sqrt(np.mean(x ** 2)) + 1e-12))
            assert -6.4 < d < -5.6, "усиление не на -6 дБ: %.2f" % d

        check("sim: нулевое усиление", t_sim_identity)
        check("sim: -6 дБ", t_sim_gain)

        print("== judge: приёмка")
        def t_judge_accept():
            r = fl.judge.verdict({"N_mean": 80.0, "sharp_mean": 1.60},
                                 {"N_mean": 76.0, "sharp_mean": 1.60},
                                 {"N_mean": "down"})
            assert r.get("verdict") == "accept", "не принял улучшение: %r" % r

        def t_judge_reject():
            r = fl.judge.verdict({"N_mean": 80.0, "sharp_mean": 1.60},
                                 {"N_mean": 80.0, "sharp_mean": 1.95},
                                 {"N_mean": "down"},
                                 corridor={"sharp_mean": {"коридор": [1.40, 1.70]}})
            assert r.get("verdict") == "reject", "не отклонил ущерб: %r" % r

        check("judge: принимает улучшение", t_judge_accept)
        check("judge: отклоняет ущерб", t_judge_reject)

    print("== predict: модель")
    def t_predict():
        assert callable(fl.predict.change), "нет predict.change"
        assert callable(fl.predict.lines), "нет predict.lines"
    check("predict: интерфейс", t_predict)

    print("== render: CLI и сторож")
    def t_render_progress():
        import render_job as R
        assert R._parse_progress("Rendering: 17/219") == (17, 219)
        assert R._parse_progress("vers1.flp - FL Studio 2025") is None
        assert R._parse_progress("Rendering: мусор") is None
    check("render: разбор прогресса", t_render_progress)

    def t_render_pid():
        import render_job as R
        pid = R.fl_pid()
        assert isinstance(pid, int) and pid >= 0, "pid не число: %r" % (pid,)
        assert R.fl_running() == (pid != 0)
    check("render: определяет FL по pid", t_render_pid)

    def t_render_watch_shape():
        import tempfile
        import render_job as R
        tmp = tempfile.mkdtemp(prefix="flctrl_render_")
        res = R.watch(tmp, since=0.0, timeout_s=0.0, poll_s=0.1)
        for key in ("crashed", "progress", "files", "split", "log"):
            assert key in res, "в ответе нет %s" % key
    check("render: сторож отдаёт структуру", t_render_watch_shape)

    print()
    print("итого: %d прошло, %d упало, %d пропущено" % (len(PASS), len(FAIL), len(SKIP)))
    for n, e in FAIL:
        print("  - %s: %s" % (n, e[:120]))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
