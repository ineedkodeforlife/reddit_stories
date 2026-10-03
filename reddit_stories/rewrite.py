"""Адаптация поста в русский сценарий для озвучки через Groq API (бесплатный тариф)."""
import json
import re
import time

import requests

import config

SYSTEM = """Ты — сценарист русскоязычного TikTok-канала с историями из Reddit.
Тебе дают пост с Reddit. Сделай из него сценарий для озвучки на русском языке.

Правила:
- Пересказ своими словами, а не дословный перевод. Сохрани суть, ключевые детали и развязку.
- От первого лица, живой разговорный язык, короткие предложения.
- Первое предложение — хук: за 2 секунды цепляет конфликтом или интригой. Никаких «Привет» и «Сегодня история про».
- Объём: строго 100–125 слов (ролик должен быть короче 55 секунд). Лишние детали выкидывай, оставь завязку, конфликт и развязку.
- Без Reddit-сокращений (AITA, MIL, SO, BIL и т.п.) — пиши по-русски: «свекровь», «мой парень».
- Имена замени на короткие и легко произносимые; место действия можно оставить.
- Числа, суммы и даты пиши словами («три тысячи долларов»), чтобы голосовой движок читал их правильно.
- В тексте озвучки никаких эмодзи, скобок, хэштегов и ремарок.
- В конце — развязка и один короткий вопрос к зрителю, который провоцирует комментарии.
- Если история не подходит (NSFW, непонятна без контекста, нет развязки, чувствительные темы с участием детей, самоповреждение) — верни skip=true.

Ответь ТОЛЬКО валидным JSON без markdown, строго такой структуры:
{"skip": false, "skip_reason": "", "title": "заголовок для экрана, до 60 символов", "script": "текст озвучки", "caption": "описание для TikTok, 1–2 предложения", "hashtags": ["историиизреддит", "..."], "score": 7, "topic": "тема в 3–6 словах"}
где score — твоя оценка потенциальной вирусности от 1 до 10, topic — о чём история, коротко и конкретно («месть соседу за парковку»)."""

SYSTEM_DISCUSSION = """Ты — сценарист русскоязычного TikTok-канала с обсуждениями из Reddit.
Тебе дают вопрос с Reddit и лучшие ответы на него из комментариев. Сделай из них сценарий для озвучки на русском языке.

Правила:
- Первое предложение — сам вопрос, коротко и цепляюще. Никаких «Привет» и обращений к зрителю в начале.
- Дальше 3–4 самых интересных ответа. Каждый — отдельная мини-история в два-три коротких предложения, от первого лица, своими словами.
- Между ответами короткие связки: «Первый ответ», «Другой человек пишет», «А вот это меня добило». Никнеймы не упоминай.
- Самый сильный ответ ставь последним.
- Живой разговорный язык, короткие предложения. Объём: строго 100–125 слов (ролик должен быть короче 55 секунд).
- Без Reddit-сокращений и англицизмов, числа и суммы пиши словами.
- В тексте озвучки никаких эмодзи, скобок, хэштегов и ремарок.
- В конце — тот же вопрос к зрителю, чтобы отвечали в комментариях.
- Если ответы скучные, однотипные, NSFW или касаются чувствительных тем с участием детей и самоповреждения — верни skip=true.

Ответь ТОЛЬКО валидным JSON без markdown, строго такой структуры:
{"skip": false, "skip_reason": "", "title": "вопрос для экрана, до 60 символов", "script": "текст озвучки", "caption": "описание для TikTok, 1–2 предложения", "hashtags": ["реддит", "..."], "score": 7, "topic": "тема в 3–6 словах"}
где score — твоя оценка потенциальной вирусности от 1 до 10, topic — о чём вопрос, коротко и конкретно («факты, которые спасут жизнь»)."""

USED_TOPICS = """

На канале уже вышли ролики на эти темы:
{topics}
Если этот пост по сути о том же, что один из них (тот же сюжет, тот же конфликт или тот же вопрос) — верни skip=true и skip_reason="повтор темы: <какой>"."""

URL = "https://api.groq.com/openai/v1/chat/completions"
_exhausted: set[str] = set()   # модели, у которых кончился лимит


class LimitError(RuntimeError):
    pass


def _parse(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    m = re.search(r"\{.*\}", raw, re.S)   # на случай текста вокруг JSON
    return json.loads(m.group(0) if m else raw)


def adapt(post: dict, used_topics: list[str] = ()) -> dict:
    user = (f"Сабреддит: r/{post['subreddit']}\n"
            f"Заголовок: {post['title']}\n\n{post['selftext']}")
    system = SYSTEM
    if post.get("comments"):   # обсуждение: сценарий собираем из комментариев
        system = SYSTEM_DISCUSSION
        user += "\n\nОтветы:\n" + "\n\n".join(
            f"[{i}] {c}" for i, c in enumerate(post["comments"], 1))
    if used_topics:
        system += USED_TOPICS.format(topics="\n".join(f"- {t}" for t in used_topics))
    if not config.GROQ_API_KEY:
        raise RuntimeError("нет GROQ_API_KEY в .env")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for model in config.LLM_MODELS:
        if model in _exhausted:
            continue
        s = _ask(model, messages)
        if s is None:
            print(f"  дневной лимит {model} исчерпан, беру следующую модель")
            _exhausted.add(model)
            continue
        n = len((s.get("script") or "").split())
        if n > config.MAX_WORDS and not s.get("skip"):   # модели любят перебирать объём — просим сократить
            print(f"  вышло {n} слов, прошу сократить...")
            shorter = _ask(model, messages + [
                {"role": "assistant", "content": json.dumps(s, ensure_ascii=False)},
                {"role": "user", "content": f"В script {n} слов, это слишком длинно. Сократи script до 100–120 слов, "
                                            f"сохранив хук, развязку и вопрос в конце. Верни тот же JSON целиком."}])
            if shorter and shorter.get("script"):
                s = {**s, **shorter}
        return s
    raise LimitError("дневной лимит Groq исчерпан на всех моделях, попробуй позже")


def _ask(model: str, messages: list[dict]) -> dict | None:
    """Ответ модели или None, если её лимит кончился надолго."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_completion_tokens": 2500,   # бесплатный лимит Groq — 8000 токенов в минуту вместе с запросом
        "response_format": {"type": "json_object"},
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"

    for attempt in range(4):
        r = requests.post(URL, json=body, timeout=120,
                          headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})
        if r.status_code == 429:   # лимит бесплатного тарифа — ждём и пробуем снова
            wait = float(r.headers.get("retry-after", 20 * (attempt + 1)))
            if wait > 90:
                return None
            print(f"  лимит Groq, жду {wait:.0f} c...")
            time.sleep(wait + 1)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
        return _parse(r.json()["choices"][0]["message"]["content"])
    return None
