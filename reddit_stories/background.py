"""Анимированные фоны на случай, когда в backgrounds/ нет своих видео.

Кадры считаются в низком разрешении (BG_W x BG_H) и растягиваются ffmpeg'ом до 1080x1920:
так быстро, а мягкость после растяжения этим стилям только на пользу.
"""
import random

import numpy as np

STYLES = ["aurora", "bokeh", "warp"]
BG_W, BG_H = 270, 480


def _palette(v: np.ndarray, phase: float) -> np.ndarray:
    """Косинусная палитра: число -> плавно меняющийся цвет."""
    shift = np.array([0.0, 0.33, 0.67], dtype=np.float32)
    return 0.5 + 0.5 * np.cos(6.2832 * (v[..., None] + phase + shift))


def _aurora(n: int, fps: int):
    """Переливающиеся цветные волны."""
    y, x = np.mgrid[0:BG_H, 0:BG_W].astype(np.float32)
    x, y = x / BG_W, y / BG_W
    phase = random.random()
    for i in range(n):
        t = i / fps
        v = (np.sin(x * 3.1 + t * 0.9) + np.sin(y * 2.3 - t * 0.7)
             + np.sin((x + y) * 2.7 + t * 0.5)
             + np.sin(np.hypot(x - 0.5 + 0.4 * np.sin(t * 0.31), y - 0.9 + 0.5 * np.cos(t * 0.23)) * 7 - t * 1.3))
        yield _palette(v * 0.055 + t * 0.012, phase) * (0.42 + 0.1 * np.sin(v * 2)[..., None])


def _bokeh(n: int, fps: int):
    """Светящиеся круги, плывущие вверх по тёмному градиенту."""
    phase = random.random()
    ramp = np.linspace(0, 1, BG_H, dtype=np.float32)[:, None]
    base = (_palette(ramp * 0.25 + np.zeros((1, BG_W), np.float32), phase) * (0.10 + 0.22 * ramp[..., None]))
    blobs = [{"x": random.uniform(0, BG_W), "y": random.uniform(0, BG_H), "r": random.uniform(10, 38),
              "v": random.uniform(12, 45), "sway": random.uniform(0.3, 1.2), "ph": random.uniform(0, 6.28),
              "c": _palette(np.float32(random.random()), phase) * random.uniform(0.25, 0.6)}
             for _ in range(26)]
    for i in range(n):
        t = i / fps
        frame = base.copy()
        for b in blobs:
            r = b["r"]
            cy = (b["y"] - b["v"] * t) % (BG_H + 4 * r) - 2 * r
            cx = b["x"] + 14 * np.sin(t * b["sway"] + b["ph"])
            x0, x1 = max(int(cx - 2 * r), 0), min(int(cx + 2 * r) + 1, BG_W)
            y0, y1 = max(int(cy - 2 * r), 0), min(int(cy + 2 * r) + 1, BG_H)
            if x0 >= x1 or y0 >= y1:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            d = np.hypot(xx - cx, yy - cy) / r
            glow = np.clip(1.15 - d, 0, 1) ** 0.6 * (d < 1.15)   # диск с мягким краем
            frame[y0:y1, x0:x1] += glow[..., None] * b["c"]
        yield frame


def _warp(n: int, fps: int):
    """Полёт сквозь звёзды со шлейфами."""
    phase = random.random()
    y, x = np.mgrid[0:BG_H, 0:BG_W].astype(np.float32)
    dist = np.hypot(x - BG_W / 2, y - BG_H / 2) / BG_H
    base = _palette(dist * 0.35, phase) * (0.05 + 0.3 * dist[..., None])
    k = 500
    sx, sy = np.random.uniform(-1, 1, k), np.random.uniform(-1, 1, k)
    sz = np.random.uniform(0.05, 1, k)
    tint = _palette(np.random.uniform(0, 0.3, k).astype(np.float32), phase) * 0.5 + 0.5
    trail = np.zeros((BG_H, BG_W, 3), np.float32)
    for i in range(n):
        sz -= 0.35 / fps
        dead = sz < 0.03
        m = int(dead.sum())
        sx[dead], sy[dead], sz[dead] = np.random.uniform(-1, 1, m), np.random.uniform(-1, 1, m), 1.0
        px = (BG_W / 2 + sx / sz * BG_W * 0.35).astype(int)
        py = (BG_H / 2 + sy / sz * BG_W * 0.35).astype(int)
        ok = (px >= 0) & (px < BG_W - 1) & (py >= 0) & (py < BG_H - 1)
        trail *= 0.86
        glow = (tint * ((1 - sz) ** 2)[:, None] * 0.9)[ok]
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            np.add.at(trail, (py[ok] + dy, px[ok] + dx), glow)
        yield base + trail


def frames(style: str, n: int, fps: int):
    """n кадров фона в формате rgb24 (bytes), размер BG_W x BG_H."""
    gen = {"aurora": _aurora, "bokeh": _bokeh, "warp": _warp}[style]
    for f in gen(n, fps):
        yield (np.clip(f, 0, 1) * 255).astype(np.uint8).tobytes()
