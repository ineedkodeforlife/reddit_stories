"""Карточки в стиле Reddit для обсуждений: вопрос сверху, под ним комментарий,
текст которого появляется по предложениям вслед за голосом.

Карточки рисуются прозрачными PNG на весь кадр и накладываются на фон в render.py.
"""
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config

X, CARD_W = 40, config.W - 80        # карточка почти на всю ширину
TOP, BOTTOM = 230, 1500              # безопасная зона TikTok: сверху и снизу интерфейс приложения
PAD, GUTTER, GAP = 34, 76, 28        # поля, колонка со стрелками, зазор между карточками

WHITE, TEXT, GREY, BLUE = (255, 255, 255, 255), (34, 34, 34), (136, 136, 136), (51, 102, 153)
ARROW, ARROW_UP = (198, 198, 198), (255, 139, 96)

# обычный и жирный шрифт с кириллицей: Windows, Linux (GitHub Actions), запасной — из fonts/
FONTS = {
    False: ["C:/Windows/Fonts/verdana.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    True: ["C:/Windows/Fonts/verdanab.ttf", "C:/Windows/Fonts/arialbd.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in FONTS[bold] + [str(config.FONTS_DIR / "Montserrat-ExtraBold.ttf")]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("не найден шрифт для карточек")


def sentences(text: str) -> list[str]:
    parts = re.findall(r"[^.!?…]+[.!?…]+[»\")]*|[^.!?…]+$", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _segments(s: dict) -> list[list[str]]:
    """Вопрос и каждый комментарий, разбитые на предложения."""
    out = []
    for text in [s["title"]] + [c["text"] for c in s["comments"]]:
        text = " ".join(text.split())
        out.append(sentences(text if text[-1] in ".!?…" else text + "."))
    return out


def build_script(s: dict) -> str:
    """Текст озвучки: вопрос, затем комментарии подряд."""
    return " ".join(sent for seg in _segments(s) for sent in seg)


def _score(n) -> str:
    if n is None:
        return ""
    return f"{n / 1000:.1f}".replace(".", ",") + " тыс." if n >= 1000 else str(n)


def _wrap(d: ImageDraw.ImageDraw, tokens: list[tuple[str, int]], font, width: int) -> list[list[tuple[str, int]]]:
    lines, cur = [], []
    for tok in tokens:
        if cur and d.textlength(" ".join(t for t, _ in cur + [tok]), font=font) > width:
            lines.append(cur)
            cur = []
        cur.append(tok)
    return lines + [cur] if cur else lines


def _arrows(d: ImageDraw.ImageDraw, cx: int, y: int, label: str = "") -> None:
    d.polygon([(cx, y), (cx - 17, y + 20), (cx + 17, y + 20)], fill=ARROW_UP)
    y += 30
    if label:
        d.text((cx, y + 16), label, font=_font(22, True), fill=GREY, anchor="mm")
        y += 40
    d.polygon([(cx, y + 20), (cx - 17, y), (cx + 17, y)], fill=ARROW)


class _Card:
    """Одна карточка: шапка, текст (с переносами), подвал. Умеет рисовать текст частично."""

    def __init__(self, d, header: list[tuple[str, tuple, bool]], sents: list[str], footer: str,
                 size: int, bold: bool, score: str = ""):
        self.header, self.footer, self.score = header, footer, score
        self.font, self.small = _font(size, bold), _font(28)
        self.line_h = int(size * 1.34)
        tokens = [(w, i) for i, sent in enumerate(sents) for w in sent.split()]
        self.lines = _wrap(d, tokens, self.font, CARD_W - GUTTER - PAD)
        self.height = PAD + 40 + 18 + len(self.lines) * self.line_h + 22 + 34 + PAD

    def draw(self, d, y: int, upto: int) -> None:
        """upto — номер последнего показанного предложения."""
        d.rounded_rectangle([X, y, X + CARD_W, y + self.height], radius=18, fill=WHITE)
        _arrows(d, X + GUTTER // 2 + 4, y + PAD + 4, self.score)
        x, ty = X + GUTTER, y + PAD
        for text, color, bold in self.header:
            f = _font(30, bold)
            d.text((x, ty), text, font=f, fill=color)
            x += d.textlength(text, font=f)
        ty += 40 + 18
        for line in self.lines:
            shown = " ".join(w for w, i in line if i <= upto)
            d.text((X + GUTTER, ty), shown, font=self.font, fill=TEXT)
            ty += self.line_h
        d.text((X + GUTTER, ty + 22), self.footer, font=_font(26, True), fill=GREY)


def build(job: Path, s: dict, words: list[dict], total: float) -> Path:
    """Рисует все состояния карточек и пишет список для ffmpeg (concat) с таймингами."""
    segs = _segments(s)
    script = build_script(s)

    # когда начинается каждое предложение: время первого слова, стоящего в тексте не раньше него
    starts, pos = [], 0
    for seg in segs:
        row = []
        for sent in seg:
            at = script.index(sent, pos)
            pos = at + len(sent)
            row.append(next((w["start"] for w in words if w.get("pos", -1) >= at), total))
        starts.append(row)

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    src = s.get("source", {})
    q_header = [(f"r/{src.get('subreddit', 'AskReddit')}", TEXT, True)]
    if src.get("author"):
        q_header.append((f"  •  u/{src['author']}", GREY, False))
    question = _Card(probe, q_header, segs[0], "Комментарии    Поделиться    Сохранить",
                     50, True, _score(src.get("score")).replace(" тыс.", "k"))

    cards = []
    for c, seg in zip(s["comments"], segs[1:]):
        meta = f"  {_score(c['score'])} очков  ·  " if c.get("score") is not None else "  ·  "
        header = [(c.get("author") or "reddit_user", BLUE, True),
                  (meta + f"{random.randint(2, 23)} ч. назад", GREY, False)]
        for size in (46, 42, 38, 34, 30):   # длинный комментарий — уменьшаем шрифт, пока не влезет
            card = _Card(probe, header, seg, "Ответить    Поделиться    Пожаловаться    Сохранить", size, False)
            if TOP + question.height + GAP + card.height <= BOTTOM:
                break
        cards.append(card)

    # блок из вопроса и самого высокого комментария ставим чуть выше центра безопасной зоны
    free = BOTTOM - TOP - question.height - GAP - max(c.height for c in cards)
    top = TOP + int(max(free, 0) * 0.4)

    out_dir = job / "cards"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    frames = []   # (время показа, файл)

    def frame(t: float, card: _Card | None, upto: int) -> None:
        img = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        question.draw(d, top, 99)
        if card:
            card.draw(d, top + question.height + GAP, upto)
        name = f"cards/{len(frames):03d}.png"
        img.save(job / name)
        frames.append((t, name))

    frame(0.0, None, 0)
    for card, row in zip(cards, starts[1:]):
        for k, t in enumerate(row):
            frame(max(t - 0.05, frames[-1][0] + 0.05), card, k)

    lines = []
    for (t, name), (t_next, _) in zip(frames, frames[1:] + [(total + 1, "")]):
        lines += [f"file '{name}'", f"duration {t_next - t:.3f}"]
    lines.append(f"file '{frames[-1][1]}'")   # concat требует повторить последний файл
    path = job / "cards.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
