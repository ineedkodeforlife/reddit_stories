"""Вопросы и ответы с Ответов Mail.ru — те же обсуждения, что с Reddit, только сразу на русском.

Официального API у сайта нет. Списки вопросов читаются из данных, вшитых в страницы «популярное» и
«обсуждаемое» (сайт на Nuxt, данные лежат в теге __NUXT_DATA__), ответы — через адрес, которым
пользуется сам сайт. Вход в аккаунт не нужен. Если сайт поменяет вёрстку, источник просто замолчит.
"""
import html
import json
import re
import time

import requests

import config
import reddit_fetch

SITE = "https://otvet.mail.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
# обёртки формата devalue, внутри которых лежит ссылка на настоящее значение
WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def _get(url: str, **params) -> requests.Response:
    for attempt in range(3):   # соединение с сайтом иногда рвётся — повтор помогает
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == 2:
                raise RuntimeError(f"{type(e).__name__}") from None
            time.sleep(4)


def _page_data(html: str) -> dict:
    """Данные страницы из __NUXT_DATA__: плоский массив, где значения ссылаются друг на друга по номерам."""
    raw = json.loads(re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S).group(1))
    memo = {}

    def value(i):
        if i < 0:
            return None
        if i in memo:
            return memo[i]
        v = raw[i]
        if isinstance(v, dict):
            out = memo[i] = {}
            for k, j in v.items():
                out[k] = value(j)
        elif isinstance(v, list) and v and isinstance(v[0], str):
            out = memo[i] = value(v[1]) if v[0] in WRAPPERS and isinstance(v[1], int) else None
        elif isinstance(v, list):
            out = memo[i] = []
            for j in v:
                out.append(value(j))
        else:
            out = memo[i] = v
        return out

    return value(0)


def _text(doc: dict) -> str:
    """Текст из документа редактора: абзацы через пробел."""
    if not isinstance(doc, dict):
        return ""
    if doc.get("type") == "text":
        return html.unescape(doc.get("text") or "")
    sep = " " if doc.get("type") == "doc" else ""
    return " ".join(sep.join(_text(n) for n in doc.get("content") or []).split())


def _questions(page: str) -> list[dict]:
    posts = _page_data(_get(f"{SITE}/{page}").text)["pinia"]["posts"]["posts"]
    return [p for p in posts.values() if isinstance(p, dict)]


def _answers(qid: int) -> list[dict]:
    """Ответы верхнего уровня: автор, текст, число лайков. Лучшие — первыми."""
    lo, hi = config.OTVET_COMMENT_CHARS
    out, seen, pos = [], set(), None
    for _ in range(5):
        res = _get(f"{SITE}/api/topic/answers/{qid}", **({"pos": pos} if pos else {})).json().get("result") or {}
        new = [r for r in res.get("replies") or [] if r["id"] not in seen]
        for r in new:
            seen.add(r["id"])
            text = _text(r.get("content"))
            if r.get("reply_to") or not lo <= len(text) <= hi or "http" in text:
                continue
            author = r.get("author") or {}
            likes = sum(c.get("Count", 0) for c in r.get("reaction_counter") or [] if c.get("Type") == 1)
            out.append({"author": author.get("nick") or author.get("username") or "", "text": text, "score": likes})
        pos = (res.get("params") or {}).get("last")
        if not new or not pos:
            break
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[: config.MAX_COMMENTS]


def discussions():
    """Ещё не использованные вопросы с достаточным числом ответов, самые обсуждаемые — первыми."""
    seen, found = reddit_fetch.load_seen(), {}
    for page in config.OTVET_PAGES:
        try:
            for q in _questions(page):
                found[q["id"]] = q
        except Exception as e:
            print(f"[otvet] {page}: {e}")
    for q in sorted(found.values(), key=lambda q: q.get("repliesCount") or 0, reverse=True):
        pid, title = f"otvet{q['id']}", " ".join((q.get("title") or "").split())
        if (pid in seen or q.get("repliesCount", 0) < config.OTVET_MIN_REPLIES
                or not title.endswith("?") or len(title) < config.MIN_TITLE_CHARS
                or any(w in title.lower() for w in config.OTVET_SKIP_WORDS)):
            continue
        try:
            comments = _answers(q["id"])
        except Exception as e:
            print(f"[otvet] ответы {q['id']}: {e}")
            continue
        if len(comments) < config.MIN_COMMENTS:
            continue
        yield {"id": pid, "kind": "t3", "author": "", "subreddit": "Ответы Mail.ru", "label": "Ответы Mail.ru",
               "title": title, "selftext": _text((q.get("content") or {}).get("jsonWithoutMedia")),
               "url": f"{SITE}/question/{q['id']}", "permalink": "", "score": None, "over_18": False,
               "verbatim": True, "comments": comments}
