"""Озвучка с таймингами слов (для субтитров Whisper не нужен).

Yandex SpeechKit, если в .env есть его ключ, затем ElevenLabs (ключ и голос), иначе бесплатный Edge TTS.
"""
import asyncio
import base64
import json
import re
import time
from pathlib import Path

import requests

import config


def words_from_alignment(al: dict) -> list[dict]:
    # pos — позиция слова в исходном тексте, по ней карточки находят начало предложения
    words, cur, start, end, pos = [], "", 0.0, 0.0, 0
    for i, (ch, a, b) in enumerate(zip(al["characters"],
                                       al["character_start_times_seconds"],
                                       al["character_end_times_seconds"])):
        if ch.isspace():
            if cur:
                words.append({"text": cur, "start": start, "end": end, "pos": pos})
                cur = ""
            continue
        if not cur:
            start, pos = a, i
        cur += ch
        end = b
    if cur:
        words.append({"text": cur, "start": start, "end": end, "pos": pos})
    return words


def _eleven(text: str, out_mp3: Path, voice: str) -> list[dict]:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    r = requests.post(
        url,
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": config.ELEVEN_API_KEY},
        json={"text": text, "model_id": config.ELEVEN_MODEL,
              "voice_settings": config.VOICE_SETTINGS},
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    data = r.json()
    out_mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    return words_from_alignment(data["alignment"])


async def _edge_stream(text: str, out_mp3: Path, voice: str) -> list[dict]:
    import edge_tts

    com = edge_tts.Communicate(text, voice, rate=config.EDGE_RATE,
                               boundary="WordBoundary")
    words = []
    with open(out_mp3, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":   # offset/duration — в 100-нс тиках
                words.append({"text": ch["text"], "start": ch["offset"] / 1e7,
                              "end": (ch["offset"] + ch["duration"]) / 1e7})
    return words


def _locate(words: list[dict], text: str) -> list[dict]:
    """Движки отдают слова без пунктуации и без позиций: находим каждое слово в исходном тексте
    и возвращаем ему знаки препинания — по ним субтитры режутся на фразы, а карточки ищут начало предложения."""
    low, pos = text.lower().replace("ё", "е"), 0
    for w in words:
        i = low.find(w["text"].lower().replace("ё", "е"), pos)
        w["pos"] = i if i >= 0 else pos
        if i < 0:
            continue
        pos = i + len(w["text"])
        w["text"] = text[i:pos]
        while pos < len(text) and not text[pos].isspace() and not text[pos].isalnum():
            w["text"] += text[pos]
            pos += 1
    return words


def _edge(text: str, out_mp3: Path, voice: str) -> list[dict]:
    # сервис Edge иногда отвечает пустым аудио, особенно на серию запросов подряд — ждём и повторяем
    for attempt in range(5):
        try:
            words = asyncio.run(_edge_stream(text, out_mp3, voice))
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    return _locate(words, text)


def _yandex(text: str, out_mp3: Path, voice: str, role: str) -> list[dict]:
    """Yandex SpeechKit API v3: один запрос — один кусок до YANDEX_MAX_CHARS символов, ответ — поток JSON
    с кусками аудио и таймингами слов."""
    hints = [{"voice": voice}, {"speed": config.YANDEX_SPEED}] + ([{"role": role}] if role else [])
    r = requests.post(
        "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis",
        headers={"Authorization": f"Api-Key {config.YANDEX_API_KEY}"},
        json={"text": text, "hints": hints,
              "outputAudioSpec": {"containerAudio": {"containerAudioType": "MP3"}},
              "loudnessNormalizationType": "LUFS"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Yandex SpeechKit {r.status_code}: {r.text[:300]}")
    audio, words = b"", []
    for line in r.text.splitlines():   # по сообщению на строку, каждое завёрнуто в result
        if not line.strip():
            continue
        msg = json.loads(line)
        if "error" in msg:
            raise RuntimeError(f"Yandex SpeechKit: {str(msg['error'])[:300]}")
        msg = msg.get("result", msg)
        audio += base64.b64decode((msg.get("audioChunk") or {}).get("data") or "")
        for w in msg.get("wordTimings") or []:
            start = int(w.get("startMs") or 0) / 1000
            words.append({"text": (w.get("word") or "").replace("+", ""), "start": start,   # «+» — знак ударения
                          "end": start + int(w.get("lengthMs") or 0) / 1000})
    if not audio:
        raise RuntimeError("Yandex SpeechKit вернул пустое аудио")
    out_mp3.write_bytes(audio)
    return _locate([w for w in words if w["text"]], text)


def engine() -> str:
    """Каким движком идёт озвучка: Yandex SpeechKit, если есть его ключ, затем ElevenLabs, иначе Edge."""
    if config.YANDEX_API_KEY:
        return "yandex"
    return "eleven" if config.ELEVEN_API_KEY and config.ELEVEN_VOICE_ID else "edge"


def pieces(text: str) -> list[str]:
    """Куски текста, которые движок озвучивает одним запросом. У Яндекса запрос ограничен
    YANDEX_MAX_CHARS символами, поэтому длинный текст режем по границам предложений, а если надо — слов."""
    if engine() != "yandex":
        return [text]
    out, cur = [], ""
    for sent in re.findall(r"\S.*?(?:[.!?…]+(?=\s|$)|$)", text, re.S):
        for word in ([sent] if len(sent) <= config.YANDEX_MAX_CHARS else sent.split()):
            if cur and len(cur) + 1 + len(word) > config.YANDEX_MAX_CHARS:
                out.append(cur)
                cur = ""
            cur = f"{cur} {word}".strip()
    return out + [cur] if cur else out


def synthesize(text: str, out_mp3: Path, gender: str = "") -> list[dict]:
    """gender — "m" или "f": каким голосом читать; пусто — голосом рассказчика."""
    gender = gender if gender in config.EDGE_VOICES else config.NARRATOR
    if engine() == "yandex":
        try:
            return _yandex(text, out_mp3, *config.YANDEX_VOICES[gender])
        except Exception as e:   # кончились деньги или сервис лежит — ролик всё равно должен выйти
            print(f"  {e} — озвучиваю через Edge")
    elif engine() == "eleven":
        voice = config.ELEVEN_VOICE_ID_F if gender == "f" and config.ELEVEN_VOICE_ID_F else config.ELEVEN_VOICE_ID
        return _eleven(text, out_mp3, voice)
    return _edge(text, out_mp3, config.EDGE_VOICES[gender])
