"""Отправка готовых роликов в Telegram через бота."""
from pathlib import Path

import requests

import config

MAX_BYTES = 49 * 1024 * 1024   # боты могут отправлять файлы до 50 МБ


def _call(method: str, data: dict, files: dict | None = None) -> None:
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
                      data={"chat_id": config.TELEGRAM_CHAT_ID, **data}, files=files, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram {r.status_code}: {r.text[:300]}")


def configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send_video(video: Path, caption: str) -> None:
    if video.stat().st_size > MAX_BYTES:
        _call("sendMessage", {"text": f"Ролик {video.parent.name} готов, но он больше 50 МБ — "
                                      f"в Telegram не пролезет.\n\n{caption}"[:4000]})
        return
    with open(video, "rb") as f:
        _call("sendVideo", {"caption": caption[:1024], "width": config.W, "height": config.H,
                            "supports_streaming": "true"},
              files={"video": (f"{video.parent.name}.mp4", f, "video/mp4")})
