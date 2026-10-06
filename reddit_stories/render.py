"""Сборка финального ролика через ffmpeg."""
import os
import random
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

import background
import config
import music
import pixabay

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
SR = 44100   # частота, в которой склеивается озвучка и пишутся щелчки


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


def join_voice(parts: list[Path], out: Path) -> list[float]:
    """Склеивает куски озвучки (у каждого может быть свой голос) в одну дорожку.

    Возвращает точную длительность каждого куска — по ней сдвигаются тайминги слов.
    """
    wavs, durs = [], []
    for p in parts:
        wav = p.with_suffix(".wav")
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(p),
                        "-ar", str(SR), "-ac", "1", str(wav)], check=True)
        with wave.open(str(wav)) as w:
            durs.append(w.getnframes() / SR)
        wavs.append(wav)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    for wav in wavs:
        cmd += ["-i", str(wav)]
    subprocess.run(cmd + ["-filter_complex", f"concat=n={len(wavs)}:v=0:a=1", str(out)], check=True)
    for f in parts + wavs:
        f.unlink()
    return durs


def write_pops(times: list[float], total: float, path: Path) -> None:
    """Дорожка с короткими щелчками в моменты появления карточек. Звук синтезируется, файлы не нужны."""
    t = np.arange(int(0.09 * SR)) / SR
    freq = 260 + 520 * np.exp(-t * 40)                         # тон быстро падает — выходит мягкий «поп»
    pop = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 38) * np.minimum(t / 0.003, 1)
    track = np.zeros(int(total * SR) + len(pop))
    for x in times:
        i = int(max(x, 0) * SR)
        track[i:i + len(pop)] += pop
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes())


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


def stock_background(job: Path, dur: float) -> Path | None:
    """Склеивает фон bg.mp4 из нескольких клипов Pixabay. None — ключа нет или что-то не вышло."""
    try:
        clips = pixabay.clips(dur)
        if not clips:
            return None
        cmd, graph = [_ffmpeg(), "-y", "-loglevel", "error"], ""
        for i, clip in enumerate(clips):   # от каждого клипа берём начало, чтобы картинка чаще менялась
            cmd += ["-t", str(config.STOCK_CLIP_SECONDS), "-i", str(clip.resolve())]
            graph += (f"[{i}:v]scale={config.W}:{config.H}:force_original_aspect_ratio=increase,"
                      f"crop={config.W}:{config.H},fps={config.FPS},setsar=1[v{i}];")
        graph += "".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0,lutyuv=y=val*{config.STOCK_DIM}[v]"
        out = job / "bg.mp4"
        subprocess.run(cmd + ["-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264",
                              "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)], check=True)
        return out
    except Exception as e:
        print(f"  фон с Pixabay не получился ({e}), рисую сам")
        return None


def render(job: Path, pops: list[float] | None = None) -> Path:
    """pops задан — это обсуждение: вместо субтитров накладываем карточки из cards.txt,
    а в моменты pops (появление карточек) ставим щелчки."""
    audio = job / "voice.mp3"
    dur = min(duration(audio) + TAIL, config.MAX_SECONDS)

    # относительные пути — чтобы не экранировать «C:» на Windows внутри фильтра
    fontsdir = os.path.relpath(config.FONTS_DIR, job).replace("\\", "/")
    vf = (f"scale={config.W}:{config.H}:force_original_aspect_ratio=increase,"
          f"crop={config.W}:{config.H},fps={config.FPS},setsar=1")
    if pops is not None:
        video = ["-f", "concat", "-safe", "0", "-i", "cards.txt"]
        graph = f"[0:v]{vf}[bg];[1:v]format=rgba[c];[bg][c]overlay=0:0:eof_action=repeat[v]"
    else:
        video = []
        graph = f"[0:v]{vf},subtitles=subs.ass:fontsdir={fontsdir}[v]"

    # звук: голос, поверх него щелчки и тихая музыка
    n = 1 if pops is None else 2               # номер входа ffmpeg с голосом (0 — фон, 1 — карточки)
    video += ["-i", "voice.mp3"]
    mix = [f"[{n}:a]volume=1[a0]"]
    if pops and config.POP_VOLUME:
        write_pops(pops, dur, job / "pops.wav")
        video += ["-i", "pops.wav"]
        mix.append(f"[{n + len(mix)}:a]volume={config.POP_VOLUME}[a{len(mix)}]")
    track = music.pick() if config.MUSIC_VOLUME else None
    if track:
        m_dur = duration(track)
        video += (["-ss", f"{random.uniform(0, m_dur - dur - 1):.2f}"] if m_dur > dur + 5
                  else ["-stream_loop", "-1"]) + ["-i", str(track.resolve())]
        # треки записаны с разной громкостью — сначала выравниваем, потом приглушаем под голос
        mix.append(f"[{n + len(mix)}:a]loudnorm=I=-16:TP=-2:LRA=11,volume={config.MUSIC_VOLUME}[a{len(mix)}]")
    graph += (";" + ";".join(mix) + ";" + "".join(f"[a{i}]" for i in range(len(mix)))
              + f"amix=inputs={len(mix)}:normalize=0:duration=first[a]")
    video += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]

    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    bgs = [p for p in config.BACKGROUNDS_DIR.iterdir() if p.suffix.lower() in VIDEO_EXT]
    style = None
    # свои видео из backgrounds/ → клипы с Pixabay → нарисованный фон
    bg = random.choice(bgs) if bgs else stock_background(job, dur)
    if bg:
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
