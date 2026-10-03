"""Генерация ASS-субтитров: фраза из нескольких слов с подсветкой текущего + плашка с заголовком."""
from pathlib import Path

import config


def _ts(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict]) -> list[list[dict]]:
    """Режем слова на фразы: по знакам конца предложения, по числу слов и по длине."""
    chunks, cur = [], []
    for w in words:
        joined = " ".join(x["text"] for x in cur + [w])
        if cur and (len(cur) >= config.WORDS_PER_CHUNK
                    or len(joined) > config.MAX_CHUNK_CHARS
                    or cur[-1]["text"][-1] in ".!?…"):
            chunks.append(cur)
            cur = []
        cur.append(dict(w))
    if cur:
        chunks.append(cur)
    # тянем фразу до следующей, чтобы не мигала в коротких паузах
    for a, b in zip(chunks, chunks[1:]):
        if b[0]["start"] - a[-1]["end"] < 0.5:
            a[-1]["end"] = b[0]["start"]
    return chunks


def _line_break(texts: list[str]) -> int:
    """После какого слова перенести строку, чтобы обе вышли примерно равными (0 — не переносить)."""
    if len(texts) < 2 or len(" ".join(texts)) <= config.LINE_CHARS:
        return 0
    return min(range(1, len(texts)),
               key=lambda k: abs(len(" ".join(texts[:k])) - len(" ".join(texts[k:]))))


def _events(chunk: list[dict]) -> list[str]:
    """По событию на каждое слово: фраза целиком, текущее слово выделено цветом."""
    texts = [_clean(w["text"]).upper() for w in chunk]
    brk = _line_break(texts)
    pop = r"{\fscx80\fscy80\t(0,100,\fscx100\fscy100)}"
    out = []
    for i, w in enumerate(chunk):
        end = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
        parts = []
        for j, t in enumerate(texts):
            if j == i:
                t = f"{{\\c{config.HIGHLIGHT}}}{t}{{\\c&H00FFFFFF&}}"
            parts.append(("\\N" if j == brk and brk else " " if j else "") + t)
        out.append(f"Dialogue: 0,{_ts(w['start'])},{_ts(end)},Word,,0,0,0,,"
                   f"{pop if i == 0 else ''}{''.join(parts)}")
    return out


def write_ass(words: list[dict], title: str, path: Path) -> None:
    f = config.FONT_NAME
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.W}
PlayResY: {config.H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{f},{config.SUB_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,9,5,5,50,50,0,204
Style: Title,{f},60,&H00141414,&H000000FF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,22,0,8,90,90,300,204

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 1,{_ts(0)},{_ts(config.TITLE_SECONDS)},Title,,0,0,0,,{_clean(title)}"]
    for chunk in chunk_words(words):
        lines += _events(chunk)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
