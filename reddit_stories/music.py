"""Фоновая музыка: свои треки из music/, а если их нет — случайный трек с лицензией CC0 из каталога Openverse.

CC0 — общественное достояние: можно использовать где угодно и без указания автора. Ключ API не нужен.
Скачанные треки лежат в cache/music/ и повторно не качаются.
"""
import random
import time
from pathlib import Path

import requests

import config

API = "https://api.openverse.org/v1/audio/"
DIR = config.CACHE_DIR / "music"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg"}


def _search(query: str, page: int) -> list[dict]:
    for attempt in range(3):   # соединение иногда рвётся — повтор помогает
        try:
            r = requests.get(API, params={"q": query, "license": "cc0,pdm", "page_size": 20, "page": page},
                             headers={"User-Agent": config.REDDIT_USER_AGENT}, timeout=30)
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise RuntimeError(f"нет связи с Openverse: {type(e).__name__}") from None
            time.sleep(5)
    if r.status_code != 200:
        raise RuntimeError(f"Openverse {r.status_code}: {r.text[:200]}")
    return r.json().get("results", [])


def _fits(track: dict, query: str) -> bool:
    """Нужной длины, действительно про запрос и без слов из MUSIC_SKIP_WORDS (голос, эффекты и т. п.)."""
    lo, hi = config.MUSIC_SECONDS
    text = " ".join([track.get("title") or ""] + [t.get("name") or "" for t in track.get("tags") or []]).lower()
    return (track.get("url") and lo <= (track.get("duration") or 0) / 1000 <= hi
            and all(w in text for w in query.lower().split())
            and not any(w in text for w in config.MUSIC_SKIP_WORDS))


def pick() -> Path | None:
    """Трек для ролика или None, если музыки нет. Ошибки сети ролик не ломают — он выйдет без музыки."""
    own = [p for p in config.MUSIC_DIR.glob("*") if p.suffix.lower() in AUDIO_EXT]
    if own:
        return random.choice(own)
    if not config.MUSIC_QUERIES:
        return None
    try:
        query = random.choice(config.MUSIC_QUERIES)
        tracks = [t for t in _search(query, random.randint(1, 3)) if _fits(t, query)]
        if not tracks:
            tracks = [t for t in _search(query, 1) if _fits(t, query)]
        if not tracks:
            print(f"  музыка: по запросу «{query}» ничего подходящего")
            return None
        track = random.choice(tracks)
        print(f"  музыка: «{track.get('title')}» ({track['license']}, {track.get('foreign_landing_url')})")
        DIR.mkdir(parents=True, exist_ok=True)
        path = DIR / f"{track['id']}.{track.get('filetype') or 'mp3'}"
        if not path.exists():
            d = requests.get(track["url"], headers={"User-Agent": config.REDDIT_USER_AGENT}, timeout=120)
            d.raise_for_status()
            path.write_bytes(d.content)
        return path
    except Exception as e:
        print(f"  музыка не подобралась ({e}), ролик выйдет без неё")
        return None
