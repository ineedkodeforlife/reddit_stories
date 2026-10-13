"""Карточки в стиле Reddit для обсуждений: вопрос сверху, под ним комментарий,
текст которого появляется по предложениям вслед за голосом, а звучащее слово подсвечивается.

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
HILITE = (255, 69, 0)                      # цвет слова, которое сейчас звучит

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


def _clean(text: str) -> str:
    """Текст в одну строку и с точкой на конце, если автор её не поставил."""
    text = " ".join((text or "").split())
    return text if not text or text[-1] in ".!?…" else text + "."


def _segments(s: dict) -> list[list[str]]:
    """Вопрос (вместе с затравкой, если она есть), каждый комментарий и концовка, разбитые на предложения."""
    out = [sentences(_clean(s["title"])) + sentences(_clean(s.get("teaser")))]
    for text in [c["text"] for c in s["comments"]] + [s.get("outro") or ""]:
        if _clean(text):
            out.append(sentences(_clean(text)))
    return out


def build_script(s: dict) -> str:
    """Текст озвучки: вопрос и затравка, затем комментарии подряд, в конце — концовка."""
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
    """Одна карточка: шапка, текст (с переносами), подвал. Умеет рисовать текст частично
    и подсвечивать слово, которое сейчас звучит."""

    def __init__(self, d, header: list[tuple[str, tuple, bool]], sents: list[str], footer: str,
                 size: int, bold: bool, score: str = "", counter: str = ""):
        self.header, self.footer, self.score, self.counter = header, footer, score, counter
        self.font, self.small = _font(size, bold), _font(28)
        self.line_h = int(size * 1.34)
        tokens = [(w, i) for i, sent in enumerate(sents) for w in sent.split()]
        self.lines = _wrap(d, tokens, self.font, CARD_W - GUTTER - PAD)
        self.height = PAD + 40 + 18 + len(self.lines) * self.line_h + 22 + 34 + PAD

    def draw(self, d, y: int, upto: int, hi: int = -1) -> None:
        """upto — номер последнего показанного предложения, hi — номер слова, которое сейчас звучит."""
        d.rounded_rectangle([X, y, X + CARD_W, y + self.height], radius=18, fill=WHITE)
        _arrows(d, X + GUTTER // 2 + 4, y + PAD + 4, self.score)
        x, ty = X + GUTTER, y + PAD
        for text, color, bold in self.header:
            f = _font(30, bold)
            d.text((x, ty), text, font=f, fill=color)
            x += d.textlength(text, font=f)
        ty += 40 + 18
        first = 0   # номер первого слова строки среди всех слов карточки
        for line in self.lines:
            shown = [w for w, i in line if i <= upto]
            d.text((X + GUTTER, ty), " ".join(shown), font=self.font, fill=TEXT)
            if first <= hi < first + len(shown):   # звучащее слово перекрашиваем поверх обычного текста
                before = " ".join(shown[:hi - first])
                dx = d.textlength(before + " ", font=self.font) if before else 0
                d.text((X + GUTTER + dx, ty), shown[hi - first], font=self.font, fill=HILITE)
            first += len(line)
            ty += self.line_h
        d.text((X + GUTTER, ty + 22), self.footer, font=_font(26, True), fill=GREY)
        if self.counter:   # «2 из 5» справа в подвале: зритель видит, сколько ответов осталось
            f = _font(26, True)
            w = d.textlength(self.counter, font=f) + 36
            x1 = X + CARD_W - PAD
            d.rounded_rectangle([x1 - w, ty + 12, x1, ty + 60], radius=24, fill=ORANGE)
            d.text((x1 - w / 2, ty + 36), self.counter, font=f, fill=WHITE, anchor="mm")


class _Outro:
    """Концовка: яркая плашка с призывом, появляется целиком."""

    def __init__(self, d, sents: list[str], size: int = 50):
        self.font = _font(size, True)
        self.line_h = int(size * 1.36)
        self.lines = _wrap(d, [(w, 0) for sent in sents for w in sent.split()], self.font, CARD_W - 2 * PAD)
        self.height = 2 * PAD + len(self.lines) * self.line_h

    def draw(self, d, y: int, upto: int = 99, hi: int = -1) -> None:
        d.rounded_rectangle([X, y, X + CARD_W, y + self.height], radius=18, fill=ORANGE)
        for i, line in enumerate(self.lines):
            d.text((config.W / 2, y + PAD + (i + 0.5) * self.line_h), " ".join(w for w, _ in line),
                   font=self.font, fill=WHITE, anchor="mm")


def build(job: Path, s: dict, words: list[dict], total: float) -> list[float]:
    """Рисует кадры карточек и пишет список для ffmpeg (concat) с таймингами.

    Возвращает моменты появления новых карточек — по ним ставятся щелчки и меняется фон.
    """
    segs = _segments(s)
    script = build_script(s)
    n_title = len(sentences(_clean(s["title"])))   # в первом сегменте после вопроса может идти затравка
    comments = s["comments"]

    # для каждого сегмента: где в тексте стоит каждое его слово и к какому предложению оно относится
    tokens, spans, pos = [], [], 0
    for seg in segs:
        row, begin = [], pos
        for i, sent in enumerate(seg):
            for tok in sent.split():
                at = script.index(tok, pos)
                row.append((at, i))
                pos = at + len(tok)
        tokens.append(row)
        spans.append((row[0][0] if row else begin, pos))
    # что и когда звучит: (время, сегмент, номер слова в сегменте, номер предложения)
    said = []
    for w in words:
        k = next((k for k, (a, b) in enumerate(spans) if a <= w.get("pos", -1) < b), None)
        if k is not None:
            j = max((j for j, (at, _) in enumerate(tokens[k]) if at <= w["pos"]), default=0)
            said.append((w["start"], k, j, tokens[k][j][1]))
    # когда появляется каждый сегмент: чуть раньше своего первого слова и строго по порядку
    shown = [0.0]
    for k in range(1, len(segs)):
        first = next((t for t, seg, *_ in said if seg == k), total)
        shown.append(max(first - 0.05, shown[-1] + 0.05))

    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    src = s.get("source", {})
    q_header = [(src.get("label") or f"r/{src.get('subreddit', 'AskReddit')}", TEXT, True)]
    if src.get("author"):
        q_header.append((f"  •  u/{src['author']}", GREY, False))
    q_footer, q_score = "Комментарии    Поделиться    Сохранить", _score(src.get("score")).replace(" тыс.", "k")
    q_sents = segs[0][:n_title]
    question = _Card(probe, q_header, q_sents, q_footer, 50, True, q_score)

    # над вопросом — полоса для подписи «Часть 1» / «Часть 2» (слева) и ведущего (справа)
    badge = f"Часть {s['part']}" if s.get("part") else ""
    band = max(BADGE_H + GAP if badge else 0, config.HOST_SIZE - HOST_OVERLAP if config.HOST else 0)
    # между вопросом и ответом — место для того, кто этот ответ читает (девочка или мужчина)
    gap = max(GAP, config.ANSWER_SIZE - HOST_OVERLAP + 8 if config.HOST else 0)
    speakers = [g for _, g in voice_segments(s)][1:len(comments) + 1]

    cards = []
    for n, (c, seg) in enumerate(zip(comments, segs[1:]), 1):
        meta = f"  {_score(c['score'])} очков  ·  " if c.get("score") is not None else "  ·  "
        header = [(c.get("author") or "reddit_user", BLUE, True),
                  (meta + f"{random.randint(2, 23)} ч. назад", GREY, False)]
        counter = f"{n} из {len(comments)}" if config.COUNTER else ""
        for size in (46, 42, 38, 34, 30):   # длинный комментарий — уменьшаем шрифт, пока не влезет
            card = _Card(probe, header, seg, "Ответить    Поделиться    Пожаловаться", size, False, counter=counter)
            if TOP + band + question.height + gap + card.height <= BOTTOM:
                break
        cards.append(card)
    if s.get("outro"):   # концовка идёт последним сегментом, после всех комментариев
        cards.append(_Outro(probe, segs[-1]))

    # блок из вопроса и самого высокого комментария ставим чуть выше центра безопасной зоны
    free = BOTTOM - TOP - band - question.height - gap - max(c.height for c in cards)
    top = TOP + int(max(free, 0) * 0.4)

    # первый кадр: пока звучит вопрос, он показан крупно по центру экрана — так ролик цепляет с первой секунды
    hook, hook_top, teaser = None, top, None
    if config.HOOK:
        if len(segs[0]) > n_title:   # затравка под вопросом: «Последний ответ — огонь»
            teaser = _Outro(probe, segs[0][n_title:], 44)
        for size in (84, 76, 68, 60):   # длинный вопрос — шрифт поменьше
            hook = _Card(probe, q_header, q_sents, q_footer, size, True, q_score)
            if band + hook.height + (GAP + teaser.height if teaser else 0) <= 1000:
                break
        hook_top = TOP + (BOTTOM - TOP - band - hook.height - (GAP + teaser.height if teaser else 0)) // 2

    # персонажи: ведущий говорит вопрос, затравку и концовку, отвечающие — свои ответы.
    # На экране всегда только один отвечающий, так что им хватает общей шкалы.
    states, replies = [(0.0, "closed")], [(0.0, "closed")]
    if config.HOST:
        mine = [spans[0]] + ([spans[-1]] if s.get("outro") else [])
        states = host.timeline([w for w in words if any(a <= w.get("pos", -1) < b for a, b in mine)], total)
        theirs = spans[1:len(comments) + 1]
        replies = host.timeline([w for w in words if any(a <= w.get("pos", -1) < b for a, b in theirs)], total)
        # реакции ведущего на ответы: с начала последнего предложения ответа (или с его середины) и до следующей карточки
        reactions = []
        for k, c in enumerate(comments, 1):
            if c.get("reaction") in host.REACTIONS:
                mine_said = [t for t, seg, _, sent in said if seg == k and sent == len(segs[k]) - 1]
                end = shown[k + 1] if k + 1 < len(shown) else total
                start = mine_said[0] if len(segs[k]) > 1 and mine_said else shown[k] + (end - shown[k]) * 0.6
                reactions.append((start, end, c["reaction"]))
        states = host.react(states, reactions)

    loop_at = total - 0.25 if config.LOOP and hook else None   # последний кадр повторяет первый — ролик зацикливается
    pop_steps = [(0.0, 0.80), (0.045, 0.94), (0.09, 1.04), (0.135, 1.0)] if config.POP_IN else [(0.0, 1.0)]

    def view(t: float) -> tuple:
        """Что на экране в момент t: (сегмент, сколько предложений видно, какое слово звучит, масштаб карточки)."""
        if loop_at is not None and t >= loop_at:
            return 0, 99, -1, 1.0
        k = max(i for i, at in enumerate(shown) if at <= t)
        now = [(j, sent) for at, seg, j, sent in said if seg == k and at <= t]
        hi, upto = now[-1] if now else (-1, 0)
        scale = [sc for dt, sc in pop_steps if shown[k] + dt <= t][-1] if 0 < k <= len(comments) else 1.0
        return k, upto, hi, scale

    def compose(k: int, upto: int, hi: int, scale: float, state: str, reply: str) -> Image.Image:
        img = Image.new("RGBA", (config.W, config.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        big = k == 0 and hook is not None            # вопрос ещё звучит — он крупно по центру
        q, q_top = (hook, hook_top + band) if big else (question, top + band)
        if badge:
            f = _font(40, True)
            w, y = d.textlength(badge, font=f) + 64, q_top - GAP - BADGE_H
            d.rounded_rectangle([X, y, X + w, y + BADGE_H], radius=BADGE_H // 2, fill=ORANGE)
            d.text((X + w / 2, y + BADGE_H / 2), badge, font=f, fill=WHITE, anchor="mm")
        in_title = k == 0 and hi < len([1 for _, sent in tokens[0] if sent < n_title])
        q.draw(d, q_top, 99, hi if in_title else -1)
        if big and teaser and not in_title:
            teaser.draw(d, q_top + q.height + GAP)
        if config.HOST:   # каждый сидит на правом верхнем углу своей карточки, руки лежат на её краю
            img.alpha_composite(host.sprite("host", state), (X + CARD_W - config.HOST_SIZE - 36,
                                                             q_top - config.HOST_SIZE + HOST_OVERLAP - host.lift(state)))
        if k == 0:
            return img
        card = cards[k - 1]
        if isinstance(card, _Outro):   # концовку читает ведущий, поэтому она вплотную к вопросу
            card.draw(d, q_top + q.height + GAP)
            return img
        c_top = q_top + q.height + gap
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        card.draw(ImageDraw.Draw(layer), c_top, upto, hi)
        if config.HOST:
            layer.alpha_composite(host.sprite(speakers[k - 1], reply),
                                  (X + CARD_W - config.ANSWER_SIZE - 36,
                                   c_top - config.ANSWER_SIZE + HOST_OVERLAP - host.lift(reply)))
        if scale != 1.0:   # карточка «выпрыгивает»: вырезаем её вместе с персонажем и масштабируем от центра
            box = (0, c_top - config.ANSWER_SIZE, config.W, min(c_top + card.height + 12, config.H))
            part = layer.crop(box)
            part = part.resize((round(part.width * scale), round(part.height * scale)), Image.LANCZOS)
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            layer.paste(part, ((config.W - part.width) // 2,
                               (box[1] + box[3]) // 2 - part.height // 2))
        img.alpha_composite(layer)
        return img

    out_dir = job / "cards"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    times = {0.0} | {t for t, *_ in said} | {t for t, _ in states + replies}
    times |= {shown[k] + dt for k in range(1, len(comments) + 1) for dt, _ in pop_steps} | set(shown)
    times |= {loop_at} if loop_at is not None else set()
    frames, saved = [], {}   # (время показа, файл); уже нарисованные сочетания
    for t in sorted(x for x in times if x < total):
        k, upto, hi, scale = view(t)
        looping = loop_at is not None and t >= loop_at
        state = "closed" if looping else [st for at, st in states if at <= t][-1]
        reply = [st for at, st in replies if at <= t][-1] if 0 < k <= len(comments) else ""
        key = (k, upto, hi, scale, state, reply)
        if key not in saved:
            saved[key] = f"cards/{len(saved):04d}.png"
            compose(*key).save(job / saved[key], compress_level=1)
        if frames and t - frames[-1][0] < 1 / config.FPS:   # короче одного кадра видео всё равно не покажется
            frames[-1] = (frames[-1][0], saved[key])
        else:
            frames.append((t, saved[key]))

    lines = []
    for (t, name), (t_next, _) in zip(frames, frames[1:] + [(total + 1, "")]):
        lines += [f"file '{name}'", f"duration {t_next - t:.3f}"]
    lines.append(f"file '{frames[-1][1]}'")   # concat требует повторить последний файл
    (job / "cards.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return shown[1:]
