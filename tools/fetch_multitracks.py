"""fetch_multitracks.py — собрать корпус НАСТОЯЩИХ мультитреков (без Demucs).

Источник: библиотека Cambridge-MT «Mixing Secrets» — свободные мультитреки
по инструментам, включая рок и метал. У каждого проекта есть ZIP и список
треков, поэтому роли (drums/bass/guitar/keys/vocals) берутся из имён файлов,
а не из нейросетевого разделения.

    python tools/fetch_multitracks.py --list --genres metal,rock
    python tools/fetch_multitracks.py --get "Project Name" --out data/multitracks
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile

LIB_PAGE = "https://cambridge-mt.com/ms3/mtk/"
GENRE_HINTS = ("metal", "hard rock", "heavy", "punk", "grunge", "stoner", "core")


def _html() -> str:
    out = subprocess.run(["curl", "-sL", LIB_PAGE], capture_output=True, text=True,
                         timeout=120)
    if not out.stdout:
        raise RuntimeError("каталог не открылся: " + (out.stderr or "")[:200])
    return out.stdout


def parse(html: str) -> list[dict]:
    """Вытащить проекты: имя, жанр, треки, размер, ссылка на полный ZIP."""
    # Разбиваем по заголовкам проектов; внутри ищем жанр и ссылку на Full ZIP.
    chunks = re.split(r"<h4[^>]*>", html)
    items: list[dict] = []
    for ch in chunks[1:]:
        name = re.sub(r"<[^>]+>", "", ch.split("</h4>")[0]).strip()
        if not name:
            continue
        genre_m = re.search(r"<strong>\s*([^<]{2,60})\s*</strong>", ch)
        genre = genre_m.group(1).strip() if genre_m else ""
        zips = re.findall(r'href="(https?://[^"]+_Full\.zip)"[^>]*>([^<]*)</a>', ch)
        if not zips:
            zips = re.findall(r'href="(https?://[^"]+\.zip)"[^>]*>([^<]*)</a>', ch)
        for url, label in zips:
            size_m = re.search(r"(\d+)\s*MB", label)
            tracks_m = re.search(r"(\d+)\s*Tracks?", label)
            items.append({
                "name": name,
                "genre": genre,
                "tracks": int(tracks_m.group(1)) if tracks_m else None,
                "size_mb": int(size_m.group(1)) if size_m else None,
                "url": url,
            })
    return items


def filter_items(items: list[dict], genres: list[str], max_mb: int = 900) -> list[dict]:
    out = []
    for it in items:
        g = (it.get("genre") or "").lower()
        if genres and not any(k in g for k in genres):
            continue
        if it.get("size_mb") and it["size_mb"] > max_mb:
            continue
        out.append(it)
    return out


def download(item: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", item["name"])[:60]
    zip_path = os.path.join(out_dir, safe + ".zip")
    if not os.path.exists(zip_path):
        print("качаю %s (%s МБ)" % (item["name"], item.get("size_mb")), flush=True)
        subprocess.run(["curl", "-L", "--fail", "-o", zip_path, item["url"]], check=True)
    folder = os.path.join(out_dir, safe)
    if not os.path.isdir(folder):
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(folder)
    return folder


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Загрузчик настоящих мультитреков")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", default=None, help="имя проекта (подстрока)")
    ap.add_argument("--genres", default="metal,hard rock,rock")
    ap.add_argument("--max-mb", type=int, default=900)
    ap.add_argument("--out", default="data/multitracks")
    a = ap.parse_args(argv)

    genres = [g.strip().lower() for g in a.genres.split(",") if g.strip()]
    items = filter_items(parse(_html()), genres, a.max_mb)
    seen = set()
    uniq = []
    for it in items:
        key = (it["name"], it["url"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    if a.list or not a.get:
        for it in uniq:
            print("%-38s %-18s %4s трек %5s МБ" % (
                it["name"][:38], (it["genre"] or "")[:18], it["tracks"], it["size_mb"]))
        print("\nвсего:", len(uniq))
        return 0

    match = [it for it in uniq if a.get.lower() in it["name"].lower()]
    if not match:
        print("не найдено:", a.get)
        return 2
    item = match[0]
    folder = download(item, a.out)
    index = {"name": item["name"], "genre": item["genre"], "folder": folder}
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("готово:", folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
