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
ORANGE, BADGE_H = (255, 69, 0, 255), 76   # плашка «Часть 1»

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
    """Вопрос, каждый комментарий и концовка (если есть), разбитые на предложения."""
    out = []
    for text in [s["title"]] + [c["text"] for c in s["comments"]] + [s.get("outro") or ""]:
        text = " ".join(text.split())
        if text:
            out.append(sentences(text if text[-1] in ".!?…" else text + "."))
    return out


def build_script(s: dict) -> str:
    """Текст озвучки: вопрос, затем комментарии подряд, в конце — концовка."""
    return " ".join(sent for seg in _segments(s) for sent in seg)


def voice_segments(s: dict) -> list[tuple[str, str]]:
    """Те же куски текста с голосом для каждого: вопрос и концовку читает рассказчик,
    ответы — голос по полу автора, а если пол не указан — голоса чередуются."""
    genders, prev = [], config.NARRATOR
    for c in s["comments"]:
        prev = c.get("gender") if c.get("gender") in ("m", "f") else "f" if prev == "m" else "m"
        genders.append(prev)
    genders = [config.NARRATOR] + genders + [config.NARRATOR]
    return [(" ".join(seg), g) for seg, g in zip(_segments(s), genders)]


def split_parts(s: dict) -> list[dict]:
    """Ответов хватает на два ролика — делим пополам: в каждой части тот же вопрос и своя половина ответов."""
    comments = s.get("comments") or []
    if len(comments) < 2 * config.PART_COMMENTS[0]:
        return [s]
    half = (len(comments) + 1) // 2
    return [{**s, "comments": chunk, "part": i, "outro": config.OUTRO_NEXT if i == 1 else s.get("outro")}
            for i, chunk in enumerate((comments[:half], comments[half:]), 1)]


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


class _Outro:
    """Концовка: яркая плашка с призывом, появляется целиком."""

    def __init__(self, d, sents: list[str]):
        self.font = _font(50, True)
        self.line_h = 68
        self.lines = _wrap(d, [(w, 0) for sent in sents for w in sent.split()], self.font, CARD_W - 2 * PAD)
        self.height = 2 * PAD + len(self.lines) * self.line_h

    def draw(self, d, y: int, upto: int) -> None:
        d.rounded_rectangle([X, y, X + CARD_W, y + self.height], radius=18, fill=ORANGE)
        for i, line in enumerate(self.lines):
            d.text((config.W / 2, y + PAD + (i + 0.5) * self.line_h), " ".join(w for w, _ in line),
                   font=self.font, fill=WHITE, anchor="mm")


def build(job: Path, s: dict, words: list[dict], total: float) -> list[float]:
    """Рисует все состояния карточек и пишет список для ffmpeg (concat) с таймингами.

    Возвращает моменты появления новых карточек — по ним ставятся щелчки.
    """
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

    # подпись «Часть 1» / «Часть 2» над вопросом, если ролик разбит на части
    badge = f"Часть {s['part']}" if s.get("part") else ""
    badge_h = BADGE_H + GAP if badge else 0

    cards = []
    for c, seg in zip(s["comments"], segs[1:]):
        meta = f"  {_score(c['score'])} очков  ·  " if c.get("score") is not None else "  ·  "
        header = [(c.get("author") or "reddit_user", BLUE, True),
                  (meta + f"{random.randint(2, 23)} ч. назад", GREY, False)]
        for size in (46, 42, 38, 34, 30):   # длинный комментарий — уменьшаем шрифт, пока не влезет
            card = _Card(probe, header, seg, "Ответить    Поделиться    Пожаловаться    Сохранить", size, False)
            if TOP + badge_h + question.height + GAP + card.height <= BOTTOM:
                break
        cards.append(card)
    if s.get("outro"):   # концовка идёт последним сегментом, после всех комментариев
        cards.append(_Outro(probe, segs[-1]))

    # блок из вопроса и самого высокого комментария ставим чуть выше центра безопасной зоны
    free = BOTTOM - TOP - badge_h - question.height - GAP - max(c.height for c in cards)
    top = TOP + int(max(free, 0) * 0.4)

    out_dir = job / "cards"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    frames = []   # (время показа, файл)

    def frame(t: float, card: _Card | None, upto: int) -> None:
        img = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if badge:
            f = _font(40, True)
            w = d.textlength(badge, font=f) + 64
            d.rounded_rectangle([X, top, X + w, top + BADGE_H], radius=BADGE_H // 2, fill=ORANGE)
            d.text((X + w / 2, top + BADGE_H / 2), badge, font=f, fill=WHITE, anchor="mm")
        question.draw(d, top + badge_h, 99)
        if card:
            card.draw(d, top + badge_h + question.height + GAP, upto)
        name = f"cards/{len(frames):03d}.png"
        img.save(job / name)
        frames.append((t, name))

    frame(0.0, None, 0)
    pops = []
    for card, row in zip(cards, starts[1:]):
        for k, t in enumerate(row):
            frame(max(t - 0.05, frames[-1][0] + 0.05), card, k)
            if k == 0:
                pops.append(frames[-1][0])

    lines = []
    for (t, name), (t_next, _) in zip(frames, frames[1:] + [(total + 1, "")]):
        lines += [f"file '{name}'", f"duration {t_next - t:.3f}"]
    lines.append(f"file '{frames[-1][1]}'")   # concat требует повторить последний файл
    (job / "cards.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pops
