"""Адаптация поста в русский сценарий для озвучки через Groq API (бесплатный тариф)."""
import json
import re
import time

import requests

import cards
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

SYSTEM_DISCUSSION = """Ты — редактор русскоязычного TikTok-канала, который показывает настоящие комментарии с Reddit.
Тебе дают вопрос с Reddit и пронумерованные ответы на него. Выбери лучшие и переведи их на русский — их покажут на экране как комментарии и озвучат.

Зрители живут в России и СНГ. Ролик должен выглядеть так, будто вопрос задали им: тема общая и житейская, ответы понятны и близки без пояснений.

Тема:
- Подходят общечеловеческие вопросы: быт, работа, отношения, семья, деньги, детство, привычки, странные случаи.
- Если сам вопрос про американские реалии (политика и президенты США, медстраховка, чаевые, колледжи и студенческие долги, оружие, американские праздники, спорт, бренды и знаменитости, которых в России не знают) — верни skip=true и skip_reason="тема не для русской аудитории".
- title — вопрос по-русски, естественно и коротко, до 90 символов, с вопросительным знаком в конце. Формулируй общо, как спросил бы русский человек; привязку к США и к Reddit убирай.

Отбор ответов:
- Бери только ответы, которые зритель из России поймёт сразу и мог бы сказать сам. Ответы, где суть держится на американских политиках, знаменитостях, передачах, магазинах, спорте, законах или школьных порядках, не бери, даже если у них высокий рейтинг.
- Если после такого отбора осталось меньше трёх хороших ответов — верни skip=true и skip_reason="ответы слишком американские".
- Обычный ролик: comments — 3–4 самых интересных и разных ответа, всё вместе (вопрос и ответы) — строго 90–115 слов, ролик должен быть короче 55 секунд. Самый сильный ответ ставь последним.
- Две части: только если тема правда цепляет (score 8 и выше) и сильных разных ответов много — верни 6–8 ответов. Первая половина списка пойдёт в первую часть, вторая — во вторую. Каждая половина вместе с вопросом — 90–115 слов и заканчивается сильным ответом. Слабыми ответами до двух частей не добивай.

Перевод:
- n — номер исходного ответа, text — его перевод.
- Это настоящие комментарии: смысл не меняй и ничего не выдумывай. Сокращать можно и нужно — убирай вводные слова и лишние детали.
- Мелкие американские детали переводи в привычные: мили в километры, фунты в килограммы, градусы Фаренгейта в градусы Цельсия, названия местных магазинов и сетей — нейтрально («супермаркет», «заправка»), high school и college — «школа» и «универ». Суммы в долларах оставляй в долларах. Людей, события и факты не подменяй.
- Каждый ответ — одно-три коротких предложения, не больше 30 слов, живым разговорным языком, от первого лица, если так в оригинале.
- Без Reddit-сокращений и англицизмов, числа и суммы пиши словами. Никаких эмодзи, скобок, ссылок и мата.
- Если ответы скучные, однотипные, NSFW или касаются чувствительных тем с участием детей и самоповреждения — верни skip=true.

Ответь ТОЛЬКО валидным JSON без markdown, строго такой структуры:
{"skip": false, "skip_reason": "", "title": "вопрос", "comments": [{"n": 1, "text": "перевод ответа"}], "caption": "описание для TikTok, 1–2 предложения", "hashtags": ["реддит", "..."], "score": 7, "topic": "тема в 3–6 словах"}
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
            f"[{i}] {c['text']}" for i, c in enumerate(post["comments"], 1))
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
        s = _finish(s, post)
        # у обсуждения в двух частях считаем слова в каждой части отдельно
        parts = [] if s.get("skip") else [cards.build_script(p) if "part" in p else p.get("script") or ""
                                          for p in cards.split_parts(s)]
        n = max((len(p.split()) for p in parts), default=0)
        if n > config.MAX_WORDS:   # модели любят перебирать объём — просим сократить
            print(f"  вышло {n} слов, прошу сократить...")
            what, where = "script", "в сумме"
            if len(parts) > 1:
                what, where = "тексты в comments", "в каждой половине ответов вместе с вопросом"
            elif post.get("comments"):
                what = "тексты в comments (или убери один ответ)"
            shorter = _ask(model, messages + [
                {"role": "assistant", "content": json.dumps(s, ensure_ascii=False)},
                {"role": "user", "content": f"Получилось {n} слов, это слишком длинно. Сократи {what} до 100–115 слов "
                                            f"{where}, сохранив самое интересное. Верни тот же JSON целиком."}])
            if shorter and (shorter.get("script") or shorter.get("comments")):
                s = _finish({**s, **shorter}, post)
        return s
    raise LimitError("дневной лимит Groq исчерпан на всех моделях, попробуй позже")


def _finish(s: dict, post: dict) -> dict:
    """Для обсуждения: возвращаем комментариям авторов и рейтинг, собираем текст озвучки."""
    if not post.get("comments") or s.get("skip"):
        return s
    picked = []
    for c in s.get("comments") or []:
        n, text = c.get("n"), (c.get("text") or "").strip()
        if text and isinstance(n, int) and 1 <= n <= len(post["comments"]):
            src = post["comments"][n - 1]
            picked.append({"author": src["author"], "score": src["score"], "text": text})
    if len(picked) < 2:
        return {**s, "skip": True, "skip_reason": "модель не выбрала комментарии"}
    # в ролик влезает не больше PART_COMMENTS[1] ответов; самые сильные стоят в конце — лишние убираем с начала
    lo, hi = config.PART_COMMENTS
    picked = picked[-2 * hi:] if len(picked) >= 2 * lo else picked[-hi:]
    s = {**s, "comments": picked}
    s["script"] = cards.build_script(s)
    return s


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
