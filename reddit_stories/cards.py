"""Карточки в стиле Reddit для обсуждений: вопрос сверху, под ним комментарий,
текст которого появляется по предложениям вслед за голосом.

Карточки рисуются прозрачными PNG на весь кадр и накладываются на фон в render.py.
"""
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
import host

X, CARD_W = 40, config.W - 80        # карточка почти на всю ширину
TOP, BOTTOM = 230, 1500              # безопасная зона TikTok: сверху и снизу интерфейс приложения
PAD, GUTTER, GAP = 34, 76, 28        # поля, колонка со стрелками, зазор между карточками

WHITE, TEXT, GREY, BLUE = (255, 255, 255, 255), (34, 34, 34), (136, 136, 136), (51, 102, 153)
ARROW, ARROW_UP = (198, 198, 198), (255, 139, 96)
ORANGE, BADGE_H = (255, 69, 0, 255), 76   # плашка «Часть 1»
HOST_OVERLAP = 34                          # на сколько ведущий заходит на карточку вопроса

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
    # делим, только когда ответов хватает на два ролика и в один они по длине не влезают
    if len(comments) < 2 * config.PART_COMMENTS[0] or len(build_script(s).split()) <= config.MAX_WORDS:
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

    # когда начинается каждое предложение: время первого слова, стоящего в тексте не раньше него;
    # заодно запоминаем, какой кусок текста занимает каждый сегмент
    starts, spans, pos = [], [], 0
    for seg in segs:
        row, begin = [], None
        for sent in seg:
            at = script.index(sent, pos)
            begin = at if begin is None else begin
            pos = at + len(sent)
            row.append(next((w["start"] for w in words if w.get("pos", -1) >= at), total))
        starts.append(row)
        spans.append((begin, pos))

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    src = s.get("source", {})
    q_header = [(src.get("label") or f"r/{src.get('subreddit', 'AskReddit')}", TEXT, True)]
    if src.get("author"):
        q_header.append((f"  •  u/{src['author']}", GREY, False))
    q_footer, q_score = "Комментарии    Поделиться    Сохранить", _score(src.get("score")).replace(" тыс.", "k")
    question = _Card(probe, q_header, segs[0], q_footer, 50, True, q_score)

    # над вопросом — полоса для подписи «Часть 1» / «Часть 2» (слева) и ведущего (справа)
    badge = f"Часть {s['part']}" if s.get("part") else ""
    band = max(BADGE_H + GAP if badge else 0, config.HOST_SIZE - HOST_OVERLAP if config.HOST else 0)
    # между вопросом и ответом — место для того, кто этот ответ читает (девочка или мужчина)
    gap = max(GAP, config.ANSWER_SIZE - HOST_OVERLAP + 8 if config.HOST else 0)
    speakers = [g for _, g in voice_segments(s)][1:len(s["comments"]) + 1]

    cards = []
    for c, seg in zip(s["comments"], segs[1:]):
        meta = f"  {_score(c['score'])} очков  ·  " if c.get("score") is not None else "  ·  "
        header = [(c.get("author") or "reddit_user", BLUE, True),
                  (meta + f"{random.randint(2, 23)} ч. назад", GREY, False)]
        for size in (46, 42, 38, 34, 30):   # длинный комментарий — уменьшаем шрифт, пока не влезет
            card = _Card(probe, header, seg, "Ответить    Поделиться    Пожаловаться    Сохранить", size, False)
            if TOP + band + question.height + gap + card.height <= BOTTOM:
                break
        cards.append(card)
    if s.get("outro"):   # концовка идёт последним сегментом, после всех комментариев
        cards.append(_Outro(probe, segs[-1]))

    # блок из вопроса и самого высокого комментария ставим чуть выше центра безопасной зоны
    free = BOTTOM - TOP - band - question.height - gap - max(c.height for c in cards)
    top = TOP + int(max(free, 0) * 0.4)

    # первый кадр: пока звучит вопрос, он показан крупно по центру экрана — так ролик цепляет с первой секунды
    hook = None
    if config.HOOK:
        for size in (84, 76, 68, 60):   # длинный вопрос — шрифт поменьше
            hook = _Card(probe, q_header, segs[0], q_footer, size, True, q_score)
            if band + hook.height <= 1000:
                break
        hook_top = TOP + (BOTTOM - TOP - band - hook.height) // 2

    scenes = []   # (время показа, картинка без персонажей, верх карточки вопроса, верх карточки ответа, кто отвечает)

    def scene(t: float, card: _Card | None, upto: int, question: _Card = question, top: int = top) -> None:
        img = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        q_top = top + band
        if badge:
            f = _font(40, True)
            w, y = d.textlength(badge, font=f) + 64, q_top - GAP - BADGE_H
            d.rounded_rectangle([X, y, X + w, y + BADGE_H], radius=BADGE_H // 2, fill=ORANGE)
            d.text((X + w / 2, y + BADGE_H / 2), badge, font=f, fill=WHITE, anchor="mm")
        question.draw(d, q_top, 99)
        c_top, who = None, None
        if isinstance(card, _Outro):   # концовку читает ведущий, поэтому она вплотную к вопросу
            card.draw(d, q_top + question.height + GAP, upto)
        elif card:
            c_top, who = q_top + question.height + gap, speakers[cards.index(card)]
            card.draw(d, c_top, upto)
        scenes.append((t, img, q_top, c_top, who))

    if hook:
        scene(0.0, None, 0, hook, hook_top)
    else:
        scene(0.0, None, 0)
    pops = []
    for card, row in zip(cards, starts[1:]):
        for k, t in enumerate(row):
            scene(max(t - 0.05, scenes[-1][0] + 0.05), card, k)
            if k == 0:
                pops.append(scenes[-1][0])

    # ведущий говорит вопрос и концовку (их читает рассказчик), остальное время слушает
    # отвечающие говорят свои ответы; на экране всегда только один из них, так что им хватает общей шкалы
    states, replies = [(0.0, "closed")], [(0.0, "closed")]
    if config.HOST:
        mine = [spans[0]] + ([spans[-1]] if s.get("outro") else [])
        spoken = [w for w in words if any(a <= w.get("pos", -1) < b for a, b in mine)]
        states += host.timeline(spoken, total)
        theirs = spans[1:len(s["comments"]) + 1]
        replies += host.timeline([w for w in words if any(a <= w.get("pos", -1) < b for a, b in theirs)], total)

    out_dir = job / "cards"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    frames, saved = [], {}   # (время показа, файл); уже сохранённые сочетания «сцена + состояния персонажей»
    for t in sorted({t for t, *_ in scenes} | {t for t, _ in states + replies}):
        i = max(k for k, sc in enumerate(scenes) if sc[0] <= t)
        _, img, q_top, c_top, who = scenes[i]
        state = [st for at, st in states if at <= t][-1]
        reply = [st for at, st in replies if at <= t][-1] if who else ""
        if (i, state, reply) not in saved:
            if config.HOST:   # каждый сидит на правом верхнем углу своей карточки, руки лежат на её краю
                img = img.copy()
                img.alpha_composite(host.sprite("host", state),
                                    (X + CARD_W - config.HOST_SIZE - 36,
                                     q_top - config.HOST_SIZE + HOST_OVERLAP - host.lift(state)))
                if who:
                    img.alpha_composite(host.sprite(who, reply),
                                        (X + CARD_W - config.ANSWER_SIZE - 36,
                                         c_top - config.ANSWER_SIZE + HOST_OVERLAP - host.lift(reply)))
            saved[i, state, reply] = f"cards/{len(saved):03d}.png"
            img.save(job / saved[i, state, reply], compress_level=1)
        if frames and t - frames[-1][0] < 1 / config.FPS:   # короче одного кадра видео всё равно не покажется
            frames[-1] = (frames[-1][0], saved[i, state, reply])
        else:
            frames.append((t, saved[i, state, reply]))

    lines = []
    for (t, name), (t_next, _) in zip(frames, frames[1:] + [(total + 1, "")]):
        lines += [f"file '{name}'", f"duration {t_next - t:.3f}"]
    lines.append(f"file '{frames[-1][1]}'")   # concat требует повторить последний файл
    (job / "cards.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pops
