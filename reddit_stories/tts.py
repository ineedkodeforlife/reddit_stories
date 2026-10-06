"""Озвучка с таймингами слов (для субтитров Whisper не нужен).

ElevenLabs, если в .env есть ключ и голос, иначе бесплатный Edge TTS.
"""
import asyncio
import base64
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
    # Edge отдаёт слова без пунктуации — возвращаем её из исходного текста,
    # по ней субтитры режутся на фразы
    pos = 0
    for w in words:
        i = text.find(w["text"], pos)
        w["pos"] = i if i >= 0 else pos
        if i < 0:
            continue
        pos = i + len(w["text"])
        while pos < len(text) and not text[pos].isspace() and not text[pos].isalnum():
            w["text"] += text[pos]
            pos += 1
    return words


def synthesize(text: str, out_mp3: Path, gender: str = "") -> list[dict]:
    """gender — "m" или "f": каким голосом читать; пусто — голосом рассказчика."""
    gender = gender if gender in config.EDGE_VOICES else config.NARRATOR
    if config.ELEVEN_API_KEY and config.ELEVEN_VOICE_ID:
        voice = config.ELEVEN_VOICE_ID_F if gender == "f" and config.ELEVEN_VOICE_ID_F else config.ELEVEN_VOICE_ID
        return _eleven(text, out_mp3, voice)
    return _edge(text, out_mp3, config.EDGE_VOICES[gender])
