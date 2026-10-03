"""Все настройки пайплайна в одном месте."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# --- ключи (берутся из .env) ---
def _key(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return None if v in ("", "...") else v   # «...» — незаполненная заглушка из .env.example


GROQ_API_KEY = _key("GROQ_API_KEY")
ELEVEN_API_KEY = _key("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = _key("ELEVENLABS_VOICE_ID")
TELEGRAM_BOT_TOKEN = _key("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _key("TELEGRAM_CHAT_ID")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "")
if not REDDIT_USER_AGENT.isascii() or not REDDIT_USER_AGENT:   # HTTP-заголовок с кириллицей не отправится
    REDDIT_USER_AGENT = "script:reddit-stories:0.1 (by u/yourname)"

# --- источники ---
SUBREDDITS = [
    "AmItheAsshole",
    "tifu",
    "MaliciousCompliance",
    "pettyrevenge",
    "ProRevenge",
    "EntitledParents",
    "relationship_advice",
]
TIMEFRAME = "week"          # hour / day / week / month / year / all
MIN_SCORE = 1000            # минимальный рейтинг поста
MIN_CHARS, MAX_CHARS = 700, 6000
PER_SUB = 3                 # сколько лучших постов брать с каждого саба (чтобы AITA не забивал всё)

# --- обсуждения: вопрос из заголовка + лучшие ответы из комментариев ---
DISCUSSION_SUBREDDITS = ["AskReddit", "AskMen", "AskWomen", "NoStupidQuestions"]
COMMENT_CHARS = (80, 700)   # мин. и макс. длина комментария
MIN_COMMENTS, MAX_COMMENTS = 4, 8

CACHE_DIR = ROOT / "cache"  # кеш RSS-лент: у Reddit лимит около запроса в минуту
CACHE_HOURS = 6

# --- LLM для адаптации (Groq) ---
# если русский текст выходит слабым — попробуй "llama-3.3-70b-versatile" или другую модель из console.groq.com/docs/models
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
# у каждой модели свой дневной лимит токенов: кончился у первой — берём следующую
LLM_MODELS = [LLM_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]

# --- озвучка ---
# Есть ключ и голос ElevenLabs в .env — озвучивает ElevenLabs, иначе бесплатный Edge TTS.
EDGE_VOICE = "ru-RU-DmitryNeural"    # или ru-RU-SvetlanaNeural
EDGE_RATE = "+12%"
ELEVEN_MODEL = "eleven_multilingual_v2"
VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.8,
    "style": 0.25,
    "use_speaker_boost": True,
    "speed": 1.1,           # TikTok-нарратив обычно чуть быстрее обычной речи
}

# --- видео ---
W, H, FPS = 1080, 1920, 30
BACKGROUNDS_DIR = ROOT / "backgrounds"
FONTS_DIR = ROOT / "fonts"
FONT_NAME = "Montserrat ExtraBold"   # шрифт из папки fonts/
SUB_FONT_SIZE = 88
WORDS_PER_CHUNK = 4                  # сколько слов показывать одновременно
MAX_CHUNK_CHARS = 26                 # макс. длина фразы на экране (в две строки)
LINE_CHARS = 13                      # фраза длиннее — переносим на вторую строку
HIGHLIGHT = "&H0000E6FF&"            # цвет произносимого слова, формат ASS: &H00BBGGRR& (тут жёлтый)
# фон, когда в backgrounds/ нет видео: aurora / bokeh / warp / random
BACKGROUND_STYLE = "random"
MAX_SECONDS = 55                     # ролик строго не длиннее этого
MAX_WORDS = 130                      # сценарий длиннее — просим модель сократить
MAX_SPEEDUP = 1.3                    # во сколько раз можно ускорить озвучку, чтобы уложиться
TITLE_SECONDS = 3.0                  # сколько висит плашка с заголовком в начале

OUT_DIR = ROOT / "out"
SEEN_FILE = ROOT / "seen.json"
# темы уже сделанных роликов — чтобы новые не повторяли старые
TOPICS_FILE = ROOT / "topics.json"
TOPICS_IN_PROMPT = 60       # сколько последних тем показывать модели
TITLE_SIMILARITY = 0.6      # доля общих слов в заголовках, после которой пост считается повтором
