"""Сборка финального ролика через ffmpeg."""
import os
import random
import re
import shutil
import subprocess
from pathlib import Path

import background
import config

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}


def _ffmpeg() -> str:
    """Системный ffmpeg, а если его нет — тот, что идёт с пакетом imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration(path: Path) -> float:
    # ffprobe в imageio-ffmpeg не входит, поэтому длительность читаем из вывода ffmpeg
    out = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr)
    if not m:
        raise RuntimeError(f"Не удалось прочитать длительность: {path}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


TAIL = 0.4   # пауза после последнего слова


def speed_up(audio: Path, factor: float) -> None:
    """Ускоряет озвучку без изменения высоты голоса."""
    tmp = audio.with_name("voice_fast.mp3")
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(audio),
                    "-filter:a", f"atempo={factor:.4f}", str(tmp)], check=True)
    tmp.replace(audio)


def trim_pauses(audio: Path, words: list[dict]) -> list[dict]:
    """Ужимает паузы длиннее MAX_PAUSE и сдвигает тайминги слов под новую дорожку."""
    log = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(audio), "-af",
                          f"silencedetect=noise={config.SILENCE_DB}dB:d={config.MAX_PAUSE + 0.05:.2f}",
                          "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    silences = zip(re.findall(r"silence_start: (-?[\d.]+)", log), re.findall(r"silence_end: ([\d.]+)", log))
    # от каждой паузы оставляем по половине MAX_PAUSE с краёв, середину вырезаем
    half = config.MAX_PAUSE / 2
    cuts = [(max(float(a), 0) + half, float(b) - half) for a, b in silences]
    cuts = [(a, b) for a, b in cuts if b - a > 0.02]
    if not cuts:
        return words

    keep = [(a, b) for a, b in zip([0.0] + [b for _, b in cuts], [a for a, _ in cuts] + [None])]
    graph = "".join(f"[0:a]atrim=start={a:.3f}{'' if b is None else f':end={b:.3f}'},asetpts=PTS-STARTPTS[s{i}];"
                    for i, (a, b) in enumerate(keep))
    graph += "".join(f"[s{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=0:a=1[out]"
    tmp = audio.with_name("voice_trim.mp3")
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(audio),
                    "-filter_complex", graph, "-map", "[out]", str(tmp)], check=True)
    tmp.replace(audio)

    def shift(t: float) -> float:
        return t - sum(min(t, b) - a for a, b in cuts if t > a)

    return [{**w, "start": shift(w["start"]), "end": shift(w["end"])} for w in words]


def render(job: Path, cards: bool = False) -> Path:
    """cards=True — вместо субтитров накладываем карточки из cards.txt (обсуждения)."""
    audio = job / "voice.mp3"
    dur = min(duration(audio) + TAIL, config.MAX_SECONDS)

    # относительные пути — чтобы не экранировать «C:» на Windows внутри фильтра
    fontsdir = os.path.relpath(config.FONTS_DIR, job).replace("\\", "/")
    vf = (f"scale={config.W}:{config.H}:force_original_aspect_ratio=increase,"
          f"crop={config.W}:{config.H},fps={config.FPS},setsar=1")
    if cards:
        video = ["-f", "concat", "-safe", "0", "-i", "cards.txt", "-i", "voice.mp3",
                 "-filter_complex", f"[0:v]{vf}[bg];[1:v]format=rgba[c];[bg][c]overlay=0:0:eof_action=repeat[v]",
                 "-map", "[v]", "-map", "2:a:0"]
    else:
        video = ["-i", "voice.mp3", "-map", "0:v:0", "-map", "1:a:0",
                 "-vf", f"{vf},subtitles=subs.ass:fontsdir={fontsdir}"]

    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    bgs = [p for p in config.BACKGROUNDS_DIR.iterdir() if p.suffix.lower() in VIDEO_EXT]
    style = None
    if bgs:
        bg = random.choice(bgs)
        bg_dur = duration(bg)
        if bg_dur < dur:
            cmd += ["-stream_loop", "-1", "-i", str(bg.resolve())]
        else:
            cmd += ["-ss", f"{random.uniform(0, bg_dur - dur):.2f}", "-i", str(bg.resolve())]
    else:
        # своих видео нет — рисуем анимированный фон сами и отдаём кадры ffmpeg'у через stdin
        style = config.BACKGROUND_STYLE
        if style not in background.STYLES:
            style = random.choice(background.STYLES)
        print(f"  в backgrounds/ пусто — рисую фон «{style}»")
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{background.BG_W}x{background.BG_H}",
                "-r", str(config.FPS), "-i", "pipe:0"]
    cmd += video + ["-t", f"{dur:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "video.mp4"]
    if not style:
        subprocess.run(cmd, cwd=job, check=True)
        return job / "video.mp4"

    proc = subprocess.Popen(cmd, cwd=job, stdin=subprocess.PIPE)
    try:
        for frame in background.frames(style, int(dur * config.FPS) + 2, config.FPS):
            proc.stdin.write(frame)
    except (BrokenPipeError, OSError):
        pass   # ffmpeg набрал нужную длительность и закрыл stdin
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg завершился с ошибкой")
    return job / "video.mp4"
