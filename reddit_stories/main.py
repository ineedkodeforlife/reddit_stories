"""
Пайплайн Reddit -> русский сценарий -> озвучка -> вертикальное видео.

  python main.py scripts --n 5              # набрать 5 новых сценариев (дёшево, без озвучки)
  python main.py scripts --mode stories     # истории с субтитрами вместо обсуждений (или all — вперемешку)
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
from datetime import date
from pathlib import Path

import cards
import config
import otvet
import reddit_fetch
import render as rnd
import rewrite
import subtitles
import telegram
import tts


def _discussions():
    """Обсуждения из всех источников по очереди: Ответы Mail.ru, русские сабы, переводные сабы.

    Каждый день очередь начинается со следующего источника, чтобы при --n 2 не выходило одно и то же.
    """
    feeds = []
    if config.OTVET_PAGES:
        feeds.append(otvet.discussions())
    feeds += [reddit_fetch.discussions(subs)
              for subs in (config.RU_DISCUSSION_SUBREDDITS, config.DISCUSSION_SUBREDDITS) if subs]
    shift = date.today().toordinal() % len(feeds)
    for group in itertools.zip_longest(*(feeds[shift:] + feeds[:shift])):
        yield from (p for p in group if p)


def _mixed():
    """Истории и обсуждения по очереди."""
    for pair in itertools.zip_longest(reddit_fetch.stories(), _discussions()):
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
        posts = {"stories": reddit_fetch.stories, "discussions": _discussions,
                 "all": _mixed}[args.mode]()
    seen = reddit_fetch.load_seen()
    topics = load_topics()
    made = 0
    for p in posts:
        kind = "обсуждение" if p.get("comments") else "история"
        print(f"→ {p.get('label') or 'r/' + p['subreddit']} | {kind} | {p['score'] or '?'}↑ | {p['title'][:70]}")
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
        s["source"] = {"id": p["id"], "subreddit": p["subreddit"], "score": p["score"], "author": p["author"],
                       "label": p.get("label") or f"r/{p['subreddit']}",   # подпись источника на карточке вопроса
                       "url": p.get("url") or "https://www.reddit.com" + p["permalink"], "title": p["title"]}
        parts = cards.split_parts(s)   # много сильных ответов — выходит две части, у каждой своя папка
        for part in parts:
            if part.get("part"):
                part["script"] = cards.build_script(part)
            job = config.OUT_DIR / (p["id"] + (f"_{part['part']}" if part.get("part") else ""))
            job.mkdir(parents=True, exist_ok=True)
            (job / "script.json").write_text(json.dumps(part, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ {job / 'script.json'}  (оценка {s.get('score')}, {len(part['script'].split())} слов)")
        topics.append({"id": p["id"], "topic": s.get("topic") or s["title"], "title": p["title"]})
        config.TOPICS_FILE.write_text(json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8")
        made += len(parts)
        if made >= args.n:
            break
    print(f"Готово сценариев: {made}. Дальше: python main.py render")


def _voice(job: Path, segments: list[tuple[str, str]]) -> list[dict]:
    """Озвучка с кешем: повторно платим только если текст изменился.

    segments — куски текста по порядку, у каждого свой голос ("m" / "f").
    """
    script = " ".join(text for text, _ in segments)
    voices = "".join(g for _, g in segments)
    h = hashlib.md5(f"{script}|{voices}|{config.MAX_PAUSE}|{config.SPEED}".encode()).hexdigest()
    wfile, audio = job / "words.json", job / "voice.mp3"
    if wfile.exists() and audio.exists():
        cached = json.loads(wfile.read_text(encoding="utf-8"))
        if cached.get("hash") == h and all("pos" in w for w in cached["words"]):
            return cached["words"]
    print("  озвучка...")
    files = [job / f"voice_{i}.mp3" for i in range(len(segments))]
    chunks = [tts.synthesize(text, f, gender) for (text, gender), f in zip(segments, files)]
    # склеиваем куски и переводим время и позиции слов в общие для всего текста
    words, t0, pos0 = [], 0.0, 0
    for (text, _), chunk, dur in zip(segments, chunks, rnd.join_voice(files, audio)):
        words += [{**w, "start": w["start"] + t0, "end": w["end"] + t0, "pos": w["pos"] + pos0} for w in chunk]
        t0, pos0 = t0 + dur, pos0 + len(text) + 1
    words = rnd.trim_pauses(audio, words)
    # ролик должен уложиться в MAX_SECONDS: если озвучка длиннее — ускоряем её сильнее обычного
    limit = config.MAX_SECONDS - rnd.TAIL - 0.3
    before = rnd.duration(audio)
    factor = max(before / limit, config.SPEED)
    if factor > config.MAX_SPEEDUP:
        audio.unlink()
        raise RuntimeError(f"текст слишком длинный: {len(script.split())} слов, "
                           f"сократи script примерно до {config.MAX_WORDS}")
    if factor > config.SPEED:
        print(f"  ускоряю озвучку в {factor:.2f} раза, чтобы уложиться в {config.MAX_SECONDS} c")
    if factor > 1:
        rnd.speed_up(audio, factor)
        scale = rnd.duration(audio) / before   # по факту, а не по расчёту
        words = [{**w, "start": w["start"] * scale, "end": w["end"] * scale} for w in words]
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
            pops = None
            if s.get("comments"):   # обсуждение: карточки Reddit вместо субтитров
                words = _voice(job, cards.voice_segments(s))
                pops = cards.build(job, s, words, rnd.duration(job / "voice.mp3") + rnd.TAIL)
            else:
                words = _voice(job, [(s["script"], config.NARRATOR)])
                subtitles.write_ass(words, s["title"], job / "subs.ass")
            print("  монтаж...")
            out = rnd.render(job, pops)
        except Exception as e:
            print(f"  ошибка: {e}")
            continue
        tags = " ".join("#" + t.lstrip("#") for t in s.get("hashtags", []))
        part = f"Часть {s['part']}. " if s.get("part") else ""
        (job / "caption.txt").write_text(f"{part}{s.get('caption', '')}\n\n{tags}\n", encoding="utf-8")
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
    a.add_argument("--mode", choices=["all", "stories", "discussions"], default="discussions")
    a.set_defaults(func=cmd_scripts)
    b = sub.add_parser("render")
    b.add_argument("jobs", nargs="*")
    b.set_defaults(func=cmd_render)
    sub.add_parser("send").set_defaults(func=cmd_send)
    c = sub.add_parser("daily")
    c.add_argument("--n", type=int, default=2)
    c.add_argument("--mode", choices=["all", "stories", "discussions"], default="discussions")
    c.set_defaults(func=cmd_daily, id=None, jobs=[])
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):   # консоль Windows не умеет «→» и «✓» в cp866/cp1251
        stream.reconfigure(encoding="utf-8", errors="replace")
    config.OUT_DIR.mkdir(exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
