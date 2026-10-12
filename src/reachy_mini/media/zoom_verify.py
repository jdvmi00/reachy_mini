"""Camera zoom rectangles, and a cheap check that a ScalerCrop change took effect.

``crop_maximum`` and ``compute_zoom_rect`` turn a requested factor into a
sensor rectangle.

The check compares a tiny grayscale thumbnail taken before the change with
one taken after it. libcamerasrc does not expose per-request metadata (the
applied ScalerCrop) on buffers, and reading its ``scaler-crop`` property
races the camera thread, so the check uses two hypotheses:

* applied: the new frame shows the new crop, so pixels at the same point of
  the full field of view should match once mapped through each crop;
* not applied: the new frame still shows the old crop, so it should match
  the old thumbnail pixel for pixel.

A thumbnail is ~128x72, so a check costs well under a millisecond of numpy.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, cast

import numpy as np
import numpy.typing as npt

Rect = tuple[int, int, int, int]
FloatImage = npt.NDArray[np.float32]


def crop_maximum() -> Rect:
    """Return the full-FOV ScalerCrop rectangle for the active sensor mode.

    Native sensor pixels, relative to the active area. Override with
    ``REACHY_CAMERA_CROP_MAX="x,y,w,h"``. Defaults assume an imx708
    (4608x2592 active area): full FOV for the 2304x1296 and 4608x2592
    modes, the centre 3072x1728 for the default 1536x864 mode.
    """
    env = os.environ.get("REACHY_CAMERA_CROP_MAX", "").strip()
    if env:
        x, y, w, h = (int(v) for v in env.split(","))
        return (x, y, w, h)
    mode = os.environ.get("REACHY_CAMERA_SENSOR_MODE", "").strip().lower()
    if mode in ("2304x1296", "4608x2592"):
        return (0, 0, 4608, 2592)
    return (768, 432, 3072, 1728)


def compute_zoom_rect(
    crop_max: Rect,
    factor: float,
    center: tuple[float, float],
    nudge: int = 8,
) -> tuple[Rect, Rect]:
    """Return ``(rect, nudged)`` ScalerCrop rectangles for one zoom request.

    Width and height stay even and the crop stays inside ``crop_max``,
    keeping that rectangle's aspect ratio. ``factor`` below 1 is treated
    as 1 (the full crop). ``nudged`` shifts x by ``nudge`` pixels, or the
    other way when that would leave the maximum, so a retry is not identical
    to the crop libcamera last programmed.
    """
    mx, my, mw, mh = crop_max
    factor = max(1.0, float(factor))
    w = int(mw / factor) // 2 * 2
    h = int(mh / factor) // 2 * 2
    cx = mx + float(center[0]) * mw
    cy = my + float(center[1]) * mh
    x = int(min(max(cx - w / 2, mx), mx + mw - w)) // 2 * 2
    y = int(min(max(cy - h / 2, my), my + mh - h)) // 2 * 2
    rect = (x, y, w, h)
    nx = x + nudge if x + nudge + w <= mx + mw else x - nudge
    nudged = (nx, y, w, h)
    return rect, nudged


THUMB_W = 128
APPLIED = "applied"
NOT_APPLIED = "not_applied"
UNCERTAIN = "uncertain"


def thumbnail_yuy2(data: bytes, width: int, height: int) -> FloatImage:
    """Return a blurred ~THUMB_W wide float32 luma thumbnail of a YUY2 frame."""
    stride = len(data) // height
    step = max(1, width // THUMB_W)
    rows = np.frombuffer(data, dtype=np.uint8, count=stride * height).reshape(height, stride)
    # Luma is every other byte in YUY2; take every `step`-th pixel.
    y = rows[::step, 0 : width * 2 : 2 * step].astype(np.float32)
    # 3x3 box blur to tame noise and sub-pixel misalignment.
    p = np.pad(y, 1, mode="edge")
    blurred = (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
        + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
        + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    ) / 9.0
    return cast(FloatImage, blurred)


def _ncc(a: FloatImage, b: FloatImage) -> Optional[float]:
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if d < 1e-6 * a.size or a.std() < 2.0 or b.std() < 2.0:
        return None  # flat / textureless: no evidence either way
    return float((a * b).sum() / d)


def _sample(
    img: FloatImage,
    rect: Rect,
    px: npt.NDArray[np.float64],
    py: npt.NDArray[np.float64],
) -> FloatImage:
    h, w = img.shape
    u = np.clip(np.rint((px - rect[0]) / rect[2] * (w - 1)), 0, w - 1).astype(int)
    v = np.clip(np.rint((py - rect[1]) / rect[3] * (h - 1)), 0, h - 1).astype(int)
    return cast(FloatImage, img[v, u])


def rects_close(a: Rect, b: Rect, tol: float = 0.02) -> bool:
    """Return whether two crops differ by less than ``tol`` of the larger width."""
    lim = tol * max(a[2], b[2])
    return max(abs(x - y) for x, y in zip(a, b)) <= lim


def check(
    before: FloatImage,
    after: FloatImage,
    old_rects: Sequence[Rect],
    new_rect: Rect,
    margin: float = 0.1,
) -> tuple[str, Optional[float], Optional[float]]:
    """Classify `after` as showing `new_rect` (applied) or still an old crop.

    `old_rects` are the candidates for what `before` showed (usually one).
    Returns (verdict, applied_score, not_applied_score).
    """
    if before.shape != after.shape:
        return UNCERTAIN, None, None
    s_id = _ncc(before.ravel(), after.ravel())
    s_app: Optional[float] = None
    for old in old_rects:
        ix0, iy0 = max(old[0], new_rect[0]), max(old[1], new_rect[1])
        ix1 = min(old[0] + old[2], new_rect[0] + new_rect[2])
        iy1 = min(old[1] + old[3], new_rect[1] + new_rect[3])
        iw, ih = ix1 - ix0, iy1 - iy0
        if iw <= 0 or ih <= 0 or iw * ih < 0.15 * new_rect[2] * new_rect[3]:
            continue
        mx, my = 0.08 * iw, 0.08 * ih
        gx = np.linspace(ix0 + mx, ix1 - mx, 32)
        gy = np.linspace(iy0 + my, iy1 - my, 18)
        px, py = np.meshgrid(gx, gy)
        s = _ncc(_sample(before, old, px, py), _sample(after, new_rect, px, py))
        if s is not None and (s_app is None or s > s_app):
            s_app = s
    a = -1.0 if s_app is None else s_app
    n = -1.0 if s_id is None else s_id
    if s_app is not None and a >= 0.5 and a - n >= margin:
        return APPLIED, s_app, s_id
    if s_id is not None and n >= 0.5 and n - a >= margin:
        return NOT_APPLIED, s_app, s_id
    return UNCERTAIN, s_app, s_id
