"""Сбор постов и комментариев с Reddit через официальные RSS-ленты.

Публичные .json-эндпоинты Reddit отдают 403, а RSS работает без ключей.
Минус RSS — жёсткий лимит (примерно запрос в минуту) и нет рейтинга, поэтому:
  - ответы кешируются в cache/ на CACHE_HOURS часов;
  - рейтинг подтягивается из архива Arctic Shift (если он недоступен — работаем без него).
"""
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET

import requests

import config

ATOM = {"a": "http://www.w3.org/2005/Atom"}
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/ids"
_next_ok = 0.0   # когда Reddit разрешит следующий запрос


def _rss(url: str, params: dict) -> list[ET.Element]:
    global _next_ok
    key = hashlib.md5((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    cfile = config.CACHE_DIR / f"{key}.xml"
    if cfile.exists() and time.time() - cfile.stat().st_mtime < config.CACHE_HOURS * 3600:
        return ET.fromstring(cfile.read_bytes()).findall("a:entry", ATOM)

    headers = {"User-Agent": config.REDDIT_USER_AGENT}
    for _ in range(4):
        wait = _next_ok - time.time()
        if wait > 0:
            print(f"  [reddit] лимит RSS, жду {wait:.0f} c...")
            time.sleep(wait)
        r = requests.get(url, params=params, headers=headers, timeout=30)
        reset = float(r.headers.get("x-ratelimit-reset", 60))
        if r.status_code == 429:
            _next_ok = time.time() + reset + 1
            continue
        if float(r.headers.get("x-ratelimit-remaining", 1)) < 1:
            _next_ok = time.time() + reset + 1
        r.raise_for_status()
        entries = ET.fromstring(r.content).findall("a:entry", ATOM)
        config.CACHE_DIR.mkdir(exist_ok=True)
        cfile.write_bytes(r.content)
        return entries
    raise RuntimeError(f"Reddit rate limit: {url}")


def _text(content: str | None) -> str:
    """HTML из RSS -> обычный текст. Берём только тело поста/комментария."""
    m = re.search(r"<!-- SC_OFF -->(.*?)<!-- SC_ON -->", content or "", re.S)
    if not m:
        return ""
    s = re.sub(r"</p>|<br\s*/?>|</li>|</h\d>|</blockquote>", "\n\n", m.group(1))
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def _entry(e: ET.Element) -> dict:
    def f(tag):
        return e.findtext(f"a:{tag}", "", ATOM)
    link = e.find("a:link", ATOM).attrib["href"]
    cat = e.find("a:category", ATOM)
    return {
        "id": f("id").split("_")[-1],
        "kind": f("id").split("_")[0],          # t3 — пост, t1 — комментарий
        "author": (e.findtext("a:author/a:name", "", ATOM) or "").removeprefix("/u/"),
        "subreddit": cat.attrib["term"] if cat is not None else "",
        "title": f("title"),
        "selftext": _text(f("content")),
        "permalink": link.replace("https://www.reddit.com", ""),
        "score": None,
        "over_18": False,
    }


def _enrich(posts: list[dict]) -> None:
    """Рейтинг и NSFW-флаг из Arctic Shift. Не получилось — не страшно."""
    try:
        for i in range(0, len(posts), 100):
            ids = ",".join(p["id"] for p in posts[i:i + 100])
            r = requests.get(ARCTIC, params={"ids": ids, "fields": "id,score,over_18,num_comments"},
                             headers={"User-Agent": config.REDDIT_USER_AGENT}, timeout=30)
            r.raise_for_status()
            info = {d["id"]: d for d in r.json()["data"]}
            for p in posts[i:i + 100]:
                d = info.get(p["id"], {})
                p["score"] = d.get("score")
                p["over_18"] = bool(d.get("over_18"))
    except Exception as e:
        print(f"  [arctic] рейтинг недоступен: {e}")


def fetch_top(subs: list[str]) -> list[dict]:
    """Топ постов за TIMEFRAME, в порядке Reddit. Несколько сабов — одним запросом."""
    entries = _rss(f"https://www.reddit.com/r/{'+'.join(subs)}/top.rss",
                   {"t": config.TIMEFRAME, "limit": 100})
    posts = [_entry(e) for e in entries]
    _enrich(posts)
    return posts


def fetch_thread(id_or_url: str) -> tuple[dict, list[dict]]:
    """Пост и его комментарии, отсортированные по рейтингу."""
    m = re.search(r"/comments/(\w+)", id_or_url)
    post_id = m.group(1) if m else id_or_url
    entries = [_entry(e) for e in _rss(f"https://www.reddit.com/comments/{post_id}.rss",
                                       {"sort": "top", "limit": 100})]
    post = next(e for e in entries if e["kind"] == "t3")
    return post, [e for e in entries if e["kind"] == "t1"]


def fetch_post(id_or_url: str) -> dict:
    post, comments = fetch_thread(id_or_url)
    _enrich([post])
    # короткий пост-вопрос — это обсуждение, сценарий делаем из комментариев
    if len(post["selftext"]) < config.MIN_CHARS:
        post["comments"] = top_comments(comments)
    return post


def top_comments(comments: list[dict]) -> list[str]:
    lo, hi = config.COMMENT_CHARS
    good = [c["selftext"] for c in comments
            if c["author"] not in ("", "AutoModerator")
            and lo <= len(c["selftext"]) <= hi
            and c["selftext"] not in ("[removed]", "[deleted]")]
    return good[: config.MAX_COMMENTS]


def _common_ok(p: dict) -> bool:
    if p["over_18"] or p["kind"] != "t3":
        return False
    if p["score"] is not None and p["score"] < config.MIN_SCORE:
        return False
    # апдейты обычно непонятны без первой части
    return not re.search(r"\bupdate\b", p["title"], re.I)


def is_story(p: dict) -> bool:
    text = p["selftext"]
    if text in ("[removed]", "[deleted]"):
        return False
    return _common_ok(p) and config.MIN_CHARS <= len(text) <= config.MAX_CHARS


def is_discussion(p: dict) -> bool:
    return _common_ok(p) and len(p["selftext"]) < config.MIN_CHARS and p["title"].rstrip().endswith("?")


def load_seen() -> set[str]:
    if config.SEEN_FILE.exists():
        return set(json.loads(config.SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    config.SEEN_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def _pick(posts: list[dict], seen: set[str], ok, taken: dict) -> list[dict]:
    """Не больше PER_SUB постов с каждого саба (чтобы один саб не забивал всё)."""
    out = []
    for p in posts:
        sub = p["subreddit"].lower()
        if p["id"] in seen or not ok(p) or taken.get(sub, 0) >= config.PER_SUB:
            continue
        taken[sub] = taken.get(sub, 0) + 1
        seen.add(p["id"])
        out.append(p)
    return out


def _feeds(subs: list[str], ok):
    """Сначала общий топ всех сабов одним запросом, потом — по одному сабу (медленно из-за лимита)."""
    seen, taken = load_seen(), {}
    for group in [subs] + ([[s] for s in subs] if len(subs) > 1 else []):
        if len(group) == 1 and taken.get(group[0].lower(), 0) >= config.PER_SUB:
            continue
        try:
            posts = fetch_top(group)
        except Exception as e:
            print(f"[reddit] r/{'+'.join(group)}: {e}")
            continue
        yield from _pick(posts, seen, ok, taken)


def stories():
    """Ещё не использованные истории. Генератор: лишних запросов к Reddit не делает."""
    yield from _feeds(config.SUBREDDITS, is_story)


def discussions():
    """Ещё не использованные обсуждения: вопрос + лучшие ответы из комментариев."""
    for p in _feeds(config.DISCUSSION_SUBREDDITS, is_discussion):
        try:
            _, comments = fetch_thread(p["id"])
        except Exception as e:
            print(f"[reddit] комментарии {p['id']}: {e}")
            continue
        p["comments"] = top_comments(comments)
        if len(p["comments"]) >= config.MIN_COMMENTS:
            yield p
