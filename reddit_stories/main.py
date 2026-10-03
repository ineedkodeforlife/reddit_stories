"""
Пайплайн Reddit -> русский сценарий -> озвучка -> вертикальное видео.

  python main.py scripts --n 5              # набрать 5 новых сценариев (дёшево, без озвучки)
  python main.py scripts --mode discussions # только обсуждения (вопрос + лучшие комментарии)
  python main.py scripts --id <url или id>  # сценарий по конкретному посту
  python main.py render                     # озвучить и смонтировать все сценарии без видео
  python main.py render out/abc123          # только конкретный
  python main.py send                       # отправить готовые ролики в Telegram
  python main.py daily --n 2                # всё сразу: сценарии + рендер + Telegram
"""
import argparse
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

import config
import reddit_fetch
import render as rnd
import rewrite
import subtitles
import telegram
import tts


def _mixed():
    """Истории и обсуждения по очереди."""
    for pair in itertools.zip_longest(reddit_fetch.stories(), reddit_fetch.discussions()):
        yield from (p for p in pair if p)


def load_topics() -> list[dict]:
    if config.TOPICS_FILE.exists():
        return json.loads(config.TOPICS_FILE.read_text(encoding="utf-8"))
    # файла ещё нет — собираем темы из уже готовых сценариев
    topics = []
    for f in sorted(config.OUT_DIR.glob("*/script.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        topics.append({"id": f.parent.name, "topic": s.get("topic") or s["title"],
                       "title": s.get("source", {}).get("title", "")})
    return topics


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яё']+", title.lower()) if len(w) > 3}


def _same_title(title: str, topics: list[dict]) -> str | None:
    """Почти такой же заголовок уже был — отсекаем без запроса к LLM."""
    a = _words(title)
    for t in topics:
        b = _words(t["title"])
        if a and b and len(a & b) / len(a | b) >= config.TITLE_SIMILARITY:
            return t["topic"]
    return None


def cmd_scripts(args):
    if args.id:
        posts = (reddit_fetch.fetch_post(i) for i in args.id)
    else:
        posts = {"stories": reddit_fetch.stories, "discussions": reddit_fetch.discussions,
                 "all": _mixed}[args.mode]()
    seen = reddit_fetch.load_seen()
    topics = load_topics()
    made = 0
    for p in posts:
        kind = "обсуждение" if p.get("comments") else "история"
        print(f"→ r/{p['subreddit']} | {kind} | {p['score'] or '?'}↑ | {p['title'][:70]}")
        dup = None if args.id else _same_title(p["title"], topics)
        if dup:
            print(f"  пропуск: повтор темы «{dup}»")
            seen.add(p["id"])
            reddit_fetch.save_seen(seen)
            continue
        used = [] if args.id else [t["topic"] for t in topics[-config.TOPICS_IN_PROMPT:]]
        try:
            s = rewrite.adapt(p, used)
        except rewrite.LimitError as e:
            print(f"  {e}")
            break
        except Exception as e:
            print(f"  ошибка LLM: {e}")
            continue
        seen.add(p["id"])
        reddit_fetch.save_seen(seen)
        if s.get("skip") or not s.get("script"):
            print(f"  пропуск: {s.get('skip_reason')}")
            continue
        s["kind"] = "discussion" if p.get("comments") else "story"
        s["source"] = {"id": p["id"], "subreddit": p["subreddit"], "score": p["score"],
                       "url": "https://www.reddit.com" + p["permalink"], "title": p["title"]}
        job = config.OUT_DIR / p["id"]
        job.mkdir(parents=True, exist_ok=True)
        (job / "script.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ {job / 'script.json'}  (оценка {s.get('score')}, {len(s['script'].split())} слов)")
        topics.append({"id": p["id"], "topic": s.get("topic") or s["title"], "title": p["title"]})
        config.TOPICS_FILE.write_text(json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8")
        made += 1
        if made >= args.n:
            break
    print(f"Готово сценариев: {made}. Дальше: python main.py render")


def _voice(job: Path, script: str) -> list[dict]:
    """Озвучка с кешем: повторно платим только если текст изменился."""
    h = hashlib.md5(script.encode()).hexdigest()
    wfile, audio = job / "words.json", job / "voice.mp3"
    if wfile.exists() and audio.exists():
        cached = json.loads(wfile.read_text(encoding="utf-8"))
        if cached.get("hash") == h:
            return cached["words"]
    print("  озвучка...")
    words = tts.synthesize(script, audio)
    wfile.write_text(json.dumps({"hash": h, "words": words}, ensure_ascii=False), encoding="utf-8")
    return words


def cmd_render(args):
    if args.jobs:
        jobs = [Path(j) for j in args.jobs]
    else:
        jobs = sorted(d for d in config.OUT_DIR.iterdir()
                      if (d / "script.json").exists() and not (d / "video.mp4").exists())
    for job in jobs:
        s = json.loads((job / "script.json").read_text(encoding="utf-8"))
        print(f"→ {job.name}: {s['title']}")
        try:
            words = _voice(job, s["script"])
            subtitles.write_ass(words, s["title"], job / "subs.ass")
            print("  монтаж...")
            out = rnd.render(job)
        except Exception as e:
            print(f"  ошибка: {e}")
            continue
        tags = " ".join("#" + t.lstrip("#") for t in s.get("hashtags", []))
        (job / "caption.txt").write_text(f"{s.get('caption', '')}\n\n{tags}\n", encoding="utf-8")
        print(f"  ✓ {out}  ({rnd.duration(out):.0f} c)")


def cmd_send(args):
    if not telegram.configured():
        print("Telegram не настроен: добавь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env")
        return
    for job in sorted(d for d in config.OUT_DIR.iterdir()
                      if (d / "video.mp4").exists() and not (d / "sent").exists()):
        caption = (job / "caption.txt").read_text(encoding="utf-8").strip()
        src = json.loads((job / "script.json").read_text(encoding="utf-8")).get("source", {})
        try:
            telegram.send_video(job / "video.mp4", f"{caption}\n\n{src.get('url', '')}".strip())
        except Exception as e:
            print(f"  ошибка отправки {job.name}: {e}")
            continue
        (job / "sent").touch()   # метка, чтобы не отправлять повторно
        print(f"  ✓ отправлено в Telegram: {job.name}")


def cmd_daily(args):
    cmd_scripts(args)
    cmd_render(args)
    cmd_send(args)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("scripts")
    a.add_argument("--n", type=int, default=3)
    a.add_argument("--id", nargs="*")
    a.add_argument("--mode", choices=["all", "stories", "discussions"], default="all")
    a.set_defaults(func=cmd_scripts)
    b = sub.add_parser("render")
    b.add_argument("jobs", nargs="*")
    b.set_defaults(func=cmd_render)
    sub.add_parser("send").set_defaults(func=cmd_send)
    c = sub.add_parser("daily")
    c.add_argument("--n", type=int, default=2)
    c.add_argument("--mode", choices=["all", "stories", "discussions"], default="all")
    c.set_defaults(func=cmd_daily, id=None, jobs=[])
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):   # консоль Windows не умеет «→» и «✓» в cp866/cp1251
        stream.reconfigure(encoding="utf-8", errors="replace")
    config.OUT_DIR.mkdir(exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
