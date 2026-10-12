"""ScalerCrop rectangles for runtime camera zoom."""

from __future__ import annotations

import os

Rect = tuple[int, int, int, int]


def crop_maximum() -> Rect:
    """Return the full-FOV ScalerCrop rectangle.

    Native sensor pixels, relative to the active area. Override with
    ``REACHY_CAMERA_CROP_MAX="x,y,w,h"``. The default is the centre
    3072x1728 crop of an imx708 (the 1536x864 sensor mode).
    """
    env = os.environ.get("REACHY_CAMERA_CROP_MAX", "").strip()
    if env:
        x, y, w, h = (int(v) for v in env.split(","))
        return (x, y, w, h)
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
