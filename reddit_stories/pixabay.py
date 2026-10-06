"""Фоновые видео с Pixabay: бесплатная лицензия, нужен бесплатный ключ API (pixabay.com/api/docs).

Скачанные клипы лежат в cache/pixabay/ и повторно не качаются.
"""
import random
import time
from pathlib import Path

import requests

import config

API = "https://pixabay.com/api/videos/"
DIR = config.CACHE_DIR / "pixabay"


def _stretch(f: dict) -> float:
    """Во сколько раз придётся растянуть файл, чтобы он закрыл вертикальный кадр."""
    return max(config.W / f["width"], config.H / f["height"])


def _link(video: dict) -> str | None:
    """Самый лёгкий файл, который растягивается не больше чем в STOCK_MAX_STRETCH раз, иначе самый чёткий."""
    files = [f for f in video.get("videos", {}).values() if f.get("url") and f.get("width") and f.get("height")]
    if not files:
        return None
    ok = [f for f in files if _stretch(f) <= config.STOCK_MAX_STRETCH]
    best = min(ok, key=lambda f: f["width"] * f["height"]) if ok else min(files, key=_stretch)
    return best["url"]


def clips(total: float) -> list[Path]:
    """Клипы по случайному запросу из STOCK_QUERIES, которых хватит на total секунд. Вертикальные — в первую очередь."""
    if not config.PIXABAY_API_KEY:
        return []
    query = random.choice(config.STOCK_QUERIES)
    for attempt in range(3):   # соединение иногда рвётся — повтор помогает
        try:
            r = requests.get(API, params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": 100,
                                          "safesearch": "true"}, timeout=30)
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise RuntimeError(f"нет связи с Pixabay: {type(e).__name__}") from None   # без ключа из адреса
            time.sleep(5)
    if r.status_code != 200:
        raise RuntimeError(f"Pixabay {r.status_code}: {r.text[:200]}")
    videos = [v for v in r.json().get("hits", [])
              if v.get("duration", 0) >= config.STOCK_CLIP_SECONDS / 2
              and all(w in v.get("tags", "").lower() for w in query.lower().split())]
    random.shuffle(videos)
    videos.sort(key=lambda v: v["videos"]["medium"]["width"] > v["videos"]["medium"]["height"])
    print(f"  фон с Pixabay: «{query}», найдено {len(videos)}")

    DIR.mkdir(parents=True, exist_ok=True)
    out, have = [], 0.0
    for v in videos:
        if have >= total:
            break
        path, link = DIR / f"{v['id']}.mp4", _link(v)
        if not link:
            continue
        if not path.exists():
            tmp = path.with_suffix(".part")
            with requests.get(link, stream=True, timeout=120) as d:
                d.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in d.iter_content(1 << 20):
                        f.write(chunk)
            tmp.replace(path)
        out.append(path)
        have += min(v["duration"], config.STOCK_CLIP_SECONDS)
    return out
