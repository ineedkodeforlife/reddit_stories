"""Персонажи канала. Ведущий (рыжий кот) сидит на карточке с вопросом и читает вопрос и концовку,
на карточке с ответом сидит тот, чьим голосом ответ звучит: девочка или мужчина.

У того, кто говорит, рот меняет форму по буквам произносимого слова (широко на «а», кружком на «о»,
трубочкой на «у», сомкнут на «м», «б», «п»), а голова чуть приподнимается на открытых звуках.
Остальные слушают. Моргают все.

Все персонажи по умолчанию рисуются кодом. Чтобы поставить своих, положи квадратные картинки
с прозрачным фоном closed.png (рот закрыт), open.png (рот открыт) и по желанию blink.png (глаза закрыты):
ведущего — в host/, девочку — в host/f/, мужчину — в host/m/. Низ картинки ляжет на край карточки.
Для живой речи можно добавить картинки по формам рта: a.png, o.png, u.png, e.png, c.png (чуть приоткрыт) —
каких нет, те заменит open.png.
"""
import random
from functools import lru_cache

from PIL import Image, ImageDraw

import config

LINE, WHITE, PINK, BLACK = (70, 35, 20, 255), (255, 255, 255, 255), (255, 150, 160, 255), (30, 20, 20, 255)
FUR, FUR_DARK = (255, 150, 60, 255), (230, 110, 30, 255)
SKIN_F, LIPS = (255, 224, 196, 255), (240, 120, 150, 255)
ROOTS, HAIR_MID, BLOND = (110, 78, 55, 255), (190, 150, 105, 255), (245, 222, 170, 255)
SKIN_M, HAIR_M, HAIR_SIDE = (240, 198, 155, 255), (245, 228, 170, 255), (205, 180, 120, 255)
STUBBLE, BROW, INK = (214, 172, 135, 255), (95, 65, 40, 255), (60, 70, 90, 255)
BLINK = 0.13       # сколько длится моргание
MIN_SHAPE = 0.06   # пауза между словами короче этого рот не закрывает
ONSET = 0.04       # сколько минимум длится смыкание на согласных перед гласной (чуть больше кадра)
LIFT = 5           # на сколько пикселей голова приподнимается на открытых звуках
# форма рта по букве: гласные — открытые формы, губные согласные смыкают губы, остальные чуть приоткрывают рот
SHAPES = {**dict.fromkeys("аяa", "a"), **dict.fromkeys("оёo", "o"), **dict.fromkeys("уюu", "u"),
          **dict.fromkeys("еэиыeiy", "e"), **dict.fromkeys("мбпmbp", "closed")}
# открытый рот: (левый край, правый край, высота) в долях картинки
MOUTHS = {"a": (0.40, 0.60, 0.19), "o": (0.435, 0.565, 0.15), "u": (0.455, 0.545, 0.09),
          "e": (0.38, 0.62, 0.10), "c": (0.43, 0.57, 0.05)}


REACTIONS = ("wow", "lol", "omg")   # реакции ведущего на ответ: удивление, смех, «не могу на это смотреть»


def _base(state: str) -> str:
    """Состояние — форма рта или реакция; тильда на конце — глаза закрыты, крышка — голова приподнята:
    "a", "closed~", "lol^"."""
    return state.rstrip("~^")


def _mouth_of(state: str) -> str:
    return {"open": "a", "blink": "closed", "wow": "o", "lol": "a", "omg": "c"}.get(_base(state), _base(state))


def _blinking(state: str) -> bool:
    return "~" in state or state == "blink"


class _Pen:
    """Рисование в долях от размера картинки. Рисуем вчетверо крупнее и потом уменьшаем — так края гладкие."""

    def __init__(self, size: int):
        self.size, self.n = size, size * 4
        self.img = Image.new("RGBA", (self.n, self.n), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.w = max(self.n // 60, 4)   # толщина контура

    def box(self, x0, y0, x1, y1):
        return [x0 * self.n, y0 * self.n, x1 * self.n, y1 * self.n]

    def pts(self, *points):
        return [(x * self.n, y * self.n) for x, y in points]

    def eyes(self, state: str, y: float, r: float = 0.11) -> None:
        for cx in (0.33, 0.67):
            if _blinking(state):
                self.d.arc(self.box(cx - r, y - 0.08, cx + r, y + 0.08), 20, 160, fill=LINE, width=self.w * 2)
            else:
                self.d.ellipse(self.box(cx - r, y - r - 0.01, cx + r, y + r + 0.01), fill=WHITE, outline=LINE, width=self.w)
                self.d.ellipse(self.box(cx - r * 0.75, y - 0.04, cx + r * 0.3, y + r), fill=BLACK)   # смотрит вниз, на карточку
                self.d.ellipse(self.box(cx - r * 0.5, y - 0.02, cx - r * 0.15, y + 0.02), fill=WHITE)

    def mouth(self, state: str, y: float, lips=None) -> bool:
        """Рисует открытый рот нужной формы. Если рот закрыт, ничего не рисует и возвращает False —
        закрытый рот у каждого персонажа свой."""
        shape = _mouth_of(state)
        if shape not in MOUTHS:
            return False
        x0, x1, h = MOUTHS[shape]
        self.d.ellipse(self.box(x0, y, x1, y + h), fill=LINE, outline=lips or LINE, width=self.w * (2 if lips else 1))
        if shape == "e":   # на «и», «е» видны зубы
            self.d.chord(self.box(x0 + 0.02, y + 0.01, x1 - 0.02, y + h - 0.01), 180, 360, fill=WHITE)
        elif shape == "a":
            self.d.ellipse(self.box(0.45, y + h - 0.085, 0.55, y + h - 0.01), fill=PINK)
        return True

    def hands(self, fill, fingers: bool = True) -> None:
        """Руки или лапы, лежащие на краю карточки."""
        for x0 in (0.14, 0.64):
            self.d.rounded_rectangle(self.box(x0, 0.84, x0 + 0.22, 1.0), radius=self.n // 16, fill=fill,
                                     outline=LINE, width=self.w)
            for k in (1, 2) if fingers else ():
                x = (x0 + 0.22 * k / 3) * self.n
                self.d.line([(x, 0.90 * self.n), (x, 0.98 * self.n)], fill=LINE, width=self.w)

    def done(self) -> Image.Image:
        return self.img.resize((self.size, self.size), Image.LANCZOS)


def _cat(state: str, size: int) -> Image.Image:
    p = _Pen(size)
    d, w = p.d, p.w
    for x0, x1, tip in ((0.10, 0.40, 0.17), (0.90, 0.60, 0.83)):   # уши (правое — зеркально)
        d.polygon(p.pts((x0, 0.36), (tip, 0.04), (x1, 0.24)), fill=FUR, outline=LINE, width=w)
        mid = (x0 + x1) / 2
        d.polygon(p.pts((mid - 0.07, 0.30), (tip * 0.6 + mid * 0.4, 0.13), (mid + 0.07, 0.25)), fill=PINK)
    d.ellipse(p.box(0.06, 0.16, 0.94, 0.98), fill=FUR, outline=LINE, width=w)        # голова
    for x in (0.40, 0.50, 0.60):                                                     # полоски на лбу
        d.line(p.pts((x, 0.19), (x, 0.29)), fill=FUR_DARK, width=w * 2)
    d.ellipse(p.box(0.30, 0.56, 0.70, 0.90), fill=WHITE)                             # светлая мордочка
    mood = _base(state)
    if mood == "wow":                                                                # глаза круглые, зрачки-точки
        for cx in (0.33, 0.67):
            d.ellipse(p.box(cx - 0.13, 0.31, cx + 0.13, 0.59), fill=WHITE, outline=LINE, width=w)
            d.ellipse(p.box(cx - 0.04, 0.41, cx + 0.04, 0.49), fill=BLACK)
    elif mood == "lol":                                                              # глаза-дужки, как при смехе
        for cx in (0.33, 0.67):
            d.arc(p.box(cx - 0.10, 0.40, cx + 0.10, 0.58), 200, 340, fill=LINE, width=w * 2)
    elif mood == "omg":
        p.eyes("blink", 0.46)
    else:
        p.eyes(state, 0.46)
    d.polygon(p.pts((0.46, 0.61), (0.54, 0.61), (0.50, 0.66)), fill=PINK, outline=LINE)   # нос
    if mood == "lol":                                                                # рот до ушей
        d.chord(p.box(0.36, 0.56, 0.64, 0.88), 0, 180, fill=LINE)
        d.ellipse(p.box(0.45, 0.78, 0.55, 0.87), fill=PINK)
    elif not p.mouth(state, 0.67):
        d.arc(p.box(0.40, 0.60, 0.50, 0.74), 0, 180, fill=LINE, width=w)
        d.arc(p.box(0.50, 0.60, 0.60, 0.74), 0, 180, fill=LINE, width=w)
    if mood == "omg":                                                                # закрывает глаза лапами
        for x0 in (0.18, 0.56):
            d.rounded_rectangle(p.box(x0, 0.34, x0 + 0.26, 0.58), radius=p.n // 16, fill=FUR, outline=LINE, width=w)
            for k in (1, 2):
                x = x0 + 0.26 * k / 3
                d.line(p.pts((x, 0.36), (x, 0.45)), fill=LINE, width=w)
    else:
        p.hands(FUR)
    return p.done()


def _girl(state: str, size: int) -> Image.Image:
    """Длинные волнистые волосы с тёмными корнями и светлыми концами, макияж, розовая помада, серьги."""
    p = _Pen(size)
    d, w = p.d, p.w
    for side in (0, 1):                                                              # волнистые пряди по бокам
        for k, y in enumerate((0.40, 0.54, 0.68, 0.82)):
            cx = 0.11 + 0.035 * (k % 2)
            cx = cx if side == 0 else 1 - cx
            d.ellipse(p.box(cx - 0.11, y - 0.11, cx + 0.11, y + 0.11), fill=BLOND, outline=LINE, width=w)
    d.ellipse(p.box(0.07, 0.06, 0.93, 0.90), fill=ROOTS, outline=LINE, width=w)      # волосы сверху, тёмные корни
    d.chord(p.box(0.07, 0.06, 0.93, 0.90), 150, 30, fill=ROOTS)
    for cx in (0.13, 0.87):                                                          # переход в блонд ближе к концам
        d.ellipse(p.box(cx - 0.07, 0.40, cx + 0.07, 0.66), fill=HAIR_MID)
    d.ellipse(p.box(0.17, 0.20, 0.83, 0.98), fill=SKIN_F, outline=LINE, width=w)     # лицо
    # прямой пробор: две пряди расходятся от макушки к вискам
    d.polygon(p.pts((0.50, 0.17), (0.34, 0.20), (0.20, 0.34), (0.16, 0.52), (0.26, 0.40), (0.40, 0.29)), fill=ROOTS)
    d.polygon(p.pts((0.50, 0.17), (0.66, 0.20), (0.80, 0.34), (0.84, 0.52), (0.74, 0.40), (0.60, 0.29)), fill=ROOTS)
    d.line(p.pts((0.50, 0.08), (0.50, 0.19)), fill=LINE, width=w)
    for cx in (0.17, 0.83):                                                          # серьги-гвоздики
        d.ellipse(p.box(cx - 0.025, 0.63, cx + 0.025, 0.68), fill=WHITE, outline=LINE)
    p.eyes(state, 0.53, 0.10)
    for cx, sign in ((0.33, -1), (0.67, 1)):                                         # макияж: стрелки и тонкие брови
        if not _blinking(state):
            d.arc(p.box(cx - 0.11, 0.41, cx + 0.11, 0.65), 195, 345, fill=BLACK, width=w * 2)
        d.line(p.pts((cx + sign * 0.09, 0.50), (cx + sign * 0.15, 0.45)), fill=BLACK, width=w * 2)
        d.arc(p.box(cx - 0.12, 0.35, cx + 0.12, 0.47), 200, 340, fill=ROOTS, width=w * 2)
    for cx in (0.25, 0.75):                                                          # румянец
        d.ellipse(p.box(cx - 0.07, 0.65, cx + 0.07, 0.73), fill=(255, 175, 175, 255))
    if not p.mouth(state, 0.72, LIPS):                                               # губы с розовой помадой
        d.ellipse(p.box(0.40, 0.74, 0.60, 0.84), fill=LIPS, outline=LINE, width=w)
        d.arc(p.box(0.40, 0.70, 0.60, 0.81), 20, 160, fill=LINE, width=w)
    p.hands(SKIN_F)
    return p.done()


def _man(state: str, size: int) -> Image.Image:
    """Блондин с зачёсанной вверх чёлкой, щетиной, серьгой и татуировками на руках."""
    p = _Pen(size)
    d, w = p.d, p.w
    for cx in (0.10, 0.90):                                                          # уши
        d.ellipse(p.box(cx - 0.06, 0.46, cx + 0.06, 0.64), fill=SKIN_M, outline=LINE, width=w)
    d.ellipse(p.box(0.085, 0.60, 0.125, 0.66), fill=(250, 210, 80, 255), outline=LINE)   # серьга
    d.ellipse(p.box(0.10, 0.18, 0.90, 0.98), fill=SKIN_M, outline=LINE, width=w)     # лицо
    d.chord(p.box(0.10, 0.18, 0.90, 0.98), 15, 165, fill=STUBBLE)                    # щетина
    d.ellipse(p.box(0.36, 0.68, 0.64, 0.90), fill=SKIN_M)                            # просвет вокруг рта
    d.ellipse(p.box(0.10, 0.18, 0.90, 0.98), outline=LINE, width=w)
    # светлые волосы: короткие виски и объёмная чёлка, зачёсанная вверх и набок
    d.chord(p.box(0.10, 0.18, 0.90, 0.98), 215, 325, fill=HAIR_SIDE)
    d.ellipse(p.box(0.20, 0.00, 0.80, 0.34), fill=HAIR_M, outline=LINE, width=w)     # основа чёлки
    d.ellipse(p.box(0.42, -0.03, 0.90, 0.27), fill=HAIR_M, outline=LINE, width=w)    # волна набок
    d.ellipse(p.box(0.24, 0.03, 0.72, 0.31), fill=HAIR_M)                            # прячем контур между ними
    for x0, y0, x1, y1 in ((0.30, 0.22, 0.44, 0.06), (0.44, 0.24, 0.60, 0.04), (0.60, 0.22, 0.78, 0.06)):   # пряди
        d.line(p.pts((x0, y0), (x1, y1)), fill=HAIR_SIDE, width=w)
    p.eyes(state, 0.52, 0.09)
    for cx, tilt in ((0.33, -0.015), (0.67, 0.015)):                                 # тёмные брови
        d.line(p.pts((cx - 0.10, 0.385 - tilt), (cx + 0.10, 0.385 + tilt)), fill=BROW, width=w * 3)
    if not p.mouth(state, 0.71):
        d.arc(p.box(0.40, 0.65, 0.60, 0.79), 15, 165, fill=LINE, width=w)
    p.hands(SKIN_M)
    for x0 in (0.14, 0.64):                                                          # татуировки на кистях
        d.line(p.pts((x0 + 0.04, 0.875), (x0 + 0.18, 0.875)), fill=INK, width=w * 2)
        d.ellipse(p.box(x0 + 0.09, 0.855, x0 + 0.13, 0.895), fill=INK)
    return p.done()


ARTISTS = {"host": _cat, "f": _girl, "m": _man}


@lru_cache
def sprite(who: str, state: str) -> Image.Image:
    """Картинка персонажа who ("host" / "f" / "m") в нужном состоянии: своя из папки host/, а если её нет — нарисованная."""
    size = config.HOST_SIZE if who == "host" else config.ANSWER_SIZE
    folder = config.HOST_DIR if who == "host" else config.HOST_DIR / who
    shape = _mouth_of(state)
    names = ["blink"] if _blinking(state) and shape == "closed" else []   # своё моргание есть только с закрытым ртом
    names += [shape] + (["open"] if shape != "closed" else []) + ["closed"]
    if _base(state) in REACTIONS:   # своя картинка реакции (wow.png, lol.png, omg.png), а без неё — обычное лицо
        names = [_base(state), "closed"]
    for name in names:
        path = folder / f"{name}.png"
        if path.exists():
            return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    return ARTISTS[who](state, size)


def lift(state: str) -> int:
    """На сколько пикселей поднять голову: на широко открытых звуках она чуть кивает вверх."""
    if state.endswith("^"):
        return LIFT * 2
    return LIFT if _mouth_of(state) in ("a", "o") and _base(state) != "lol" else 0


def react(events: list[tuple[float, str]], reactions: list[tuple[float, float, str]]) -> list[tuple[float, str]]:
    """Вставляет в шкалу состояний реакции (начало, конец, какая). От смеха голова подпрыгивает."""
    out = [e for e in events if not any(a <= e[0] < b for a, b, _ in reactions)]
    for a, b, kind in reactions:
        if kind == "lol":
            t, up = a, False
            while t < b:
                out.append((t, "lol^" if up else "lol"))
                t, up = t + 0.13, not up
        else:
            out.append((a, kind))
        before = [st for at, st in events if at <= b]
        out.append((b, before[-1] if before else "closed"))   # после реакции — то, что было бы без неё
    out.sort(key=lambda e: e[0])
    return [e for i, e in enumerate(out) if i == 0 or e[1] != out[i - 1][1]]


def _syllables(word: str) -> list[tuple[str, str]]:
    """Слоги слова как пары (смыкание перед гласной, форма гласной). Согласные без гласной после них
    дают последнюю пару с пустой гласной."""
    out, onset = [], ""
    for ch in word.lower():
        if not ch.isalpha() or ch in "ьъ":
            continue
        shape = SHAPES.get(ch, "c")
        if shape in MOUTHS and shape != "c":           # гласная закрывает слог
            out.append((onset, shape))
            onset = ""
        elif onset != "closed":                        # губная согласная важнее обычной: губы смыкаются
            onset = shape
    return out + [(onset, "")] if onset else out


def _mouths(spoken: list[dict]) -> list[tuple[float, str]]:
    """Формы рта по слогам: время слова делится между слогами поровну, в каждом рот сначала прикрывается
    на согласных, потом открывается на гласной — так виден ритм речи."""
    events = []

    def put(t: float, shape: str) -> None:
        if not events or events[-1][1] != shape:
            events.append((t, shape))

    for k, w in enumerate(spoken):
        syllables = _syllables(w["text"])
        if not syllables:
            continue
        step = (w["end"] - w["start"]) / len(syllables)
        for i, (onset, vowel) in enumerate(syllables):
            t = w["start"] + i * step
            if not vowel:                              # хвост из согласных
                put(t, onset)
            elif onset and step >= 2 * ONSET:          # есть время показать и смыкание, и гласную
                put(t, onset)
                put(t + max(step * 0.35, ONSET), vowel)
            else:
                put(t, vowel)
        gap = spoken[k + 1]["start"] - w["end"] if k + 1 < len(spoken) else 1
        if gap > MIN_SHAPE:                            # между словами рот закрывается, если пауза заметна
            put(w["end"], "closed")
    return events


def timeline(spoken: list[dict], total: float) -> list[tuple[float, str]]:
    """Когда какое состояние показывать. spoken — слова, которые персонаж произносит, с таймингами."""
    mouths = [(0.0, "closed")] + _mouths(spoken)
    blinks, t = [], random.uniform(1.5, 3.0)
    while t < total:                                   # моргаем раз в несколько секунд, и во время речи тоже
        blinks.append((t, t + BLINK))
        t += random.uniform(2.5, 4.5)
    events = []
    for t in sorted({t for t, _ in mouths} | {x for pair in blinks for x in pair}):
        shape = [s for at, s in mouths if at <= t][-1]
        events.append((t, shape + ("~" if any(a <= t < b for a, b in blinks) else "")))
    return [e for i, e in enumerate(events) if i == 0 or e[1] != events[i - 1][1]]
