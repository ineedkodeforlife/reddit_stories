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
ELEVEN_VOICE_ID_F = _key("ELEVENLABS_VOICE_ID_F")   # женский голос для ответов; нет — всё читает основной
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
# русскоязычные обсуждения идут через одно с переводными; пустой список — только переводные
RU_DISCUSSION_SUBREDDITS = ["rusAskReddit"]
SUB_MIN_SCORE = {"rusaskreddit": 150}   # свой порог рейтинга для небольших сабов (имя в нижнем регистре)
MIN_TITLE_CHARS = 20        # короче — обычно подпись к картинке («Это правда?»), без неё вопрос непонятен
DISCUSSION_PER_SUB = 6      # кандидатов с саба больше, чем у историй: «американские» темы отсеиваются
COMMENT_CHARS = (40, 450)   # мин. и макс. длина комментария: в ролик до 55 с влезают только короткие
# MAX_COMMENTS — сколько ответов показываем модели: с запасом, чтобы было из чего выбрать понятные в России
MIN_COMMENTS, MAX_COMMENTS = 4, 16
PART_COMMENTS = (3, 4)      # мин. и макс. ответов в ролике; набралось на два ролика — выходит две части

CACHE_DIR = ROOT / "cache"  # кеш RSS-лент: у Reddit лимит около запроса в минуту
CACHE_HOURS = 6

# --- LLM для адаптации (Groq) ---
# если русский текст выходит слабым — попробуй "llama-3.3-70b-versatile" или другую модель из console.groq.com/docs/models
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
# у каждой модели свой дневной лимит токенов: кончился у первой — берём следующую
LLM_MODELS = [LLM_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]

# --- озвучка ---
# Есть ключ и голос ElevenLabs в .env — озвучивает ElevenLabs, иначе бесплатный Edge TTS.
# голоса по полу: "m" читает вопрос и концовку, ответы — голос по полу автора комментария
EDGE_VOICES = {"m": "ru-RU-DmitryNeural", "f": "ru-RU-SvetlanaNeural"}
NARRATOR = "m"
EDGE_RATE = "+12%"
ELEVEN_MODEL = "eleven_multilingual_v2"
VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.8,
    "style": 0.25,
    "use_speaker_boost": True,
    "speed": 1.1,           # TikTok-нарратив обычно чуть быстрее обычной речи
}
# после озвучки: паузы диктора ужимаются, а вся дорожка немного ускоряется
MAX_PAUSE = 0.18            # паузы длиннее этого (в секундах) режутся до этого значения
SILENCE_DB = -35            # всё, что тише, считается паузой
SPEED = 1.08                # во сколько раз ускорить готовую озвучку (1 — не ускорять)

# --- звук ---
MUSIC_DIR = ROOT / "music"  # свои треки (mp3/wav/m4a/ogg): для ролика берётся случайный файл
# в music/ пусто — трек подбирается сам: случайный запрос, лицензия CC0, каталог Openverse; пустой список — без музыки
MUSIC_QUERIES = ["lofi loop", "chill lofi", "lofi beat", "chill beat", "lofi piano"]
MUSIC_SECONDS = (15, 300)   # мин. и макс. длина трека; короткий зацикливается
MUSIC_SKIP_WORDS = ["vocal", "voice", "speech", "fx", "sfx", "horror", "scary", "glitch", "noise",
                    "sad", "melanchol", "somber", "dark", "drama"]   # ролики лёгкие — грустное не берём
MUSIC_VOLUME = 0.10         # громкость музыки относительно голоса
POP_VOLUME = 0.5            # громкость щелчка при появлении комментария (0 — без щелчков)

# --- концовка обсуждения: карточка с озвучкой после последнего ответа ---
OUTRO = "А у вас как? Пишите в комментариях."      # если модель не придумала вопрос по теме
OUTRO_NEXT = "Продолжение во второй части."         # в конце первой части

# --- видео ---
W, H, FPS = 1080, 1920, 30
BACKGROUNDS_DIR = ROOT / "backgrounds"
# в backgrounds/ пусто и в .env есть PIXABAY_API_KEY — фон склеивается из клипов Pixabay по одному из запросов
PIXABAY_API_KEY = _key("PIXABAY_API_KEY")
# поиск Pixabay неточный, поэтому берутся только видео, у которых все слова запроса есть в тегах;
# эти запросы проверены: по ним много съёмок растекающейся краски и чернил
STOCK_QUERIES = ["ink in water", "liquid paint", "motion paint", "soap bubbles"]
STOCK_DIM = 0.78                     # яркость стокового фона (1 — как есть): на светлом фоне белые карточки теряются
STOCK_CLIP_SECONDS = 8               # сколько секунд брать от каждого клипа
STOCK_MAX_STRETCH = 1.8              # горизонтальные клипы обрезаются по центру и растягиваются; сильнее — уже мыльно
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
