"""Unit tests for runtime camera zoom (no robot, no GStreamer pipeline)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini.daemon.app.routers import camera
from reachy_mini.media.media_manager import MediaBackend, MediaManager
from reachy_mini.media.zoom_verify import (
    APPLIED,
    NOT_APPLIED,
    UNCERTAIN,
    check,
    compute_zoom_rect,
    crop_maximum,
    rects_close,
    thumbnail_yuy2,
)

# imx708 full active area, the rectangle used on the robot in 2304x1296 mode.
_FULL: tuple[int, int, int, int] = (0, 0, 4608, 2592)


def _clear_zoom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REACHY_CAMERA_CROP_MAX", raising=False)
    monkeypatch.delenv("REACHY_CAMERA_SENSOR_MODE", raising=False)


def test_compute_zoom_rect_matches_hardware_crops() -> None:
    """Rects measured on an imx708 in 2304x1296 mode (T1)."""
    assert compute_zoom_rect(_FULL, 1.0, (0.5, 0.5))[0] == (0, 0, 4608, 2592)
    assert compute_zoom_rect(_FULL, 4 / 3, (0.5, 0.5))[0] == (576, 324, 3456, 1944)
    assert compute_zoom_rect(_FULL, 1.8, (0.5, 0.5))[0] == (1024, 576, 2560, 1440)
    assert compute_zoom_rect(_FULL, 1.8, (0.25, 0.35))[0] == (0, 186, 2560, 1440)


def test_compute_zoom_rect_clamps_factor_and_stays_even() -> None:
    """A factor below 1 is the full frame, and width/height stay even."""
    assert compute_zoom_rect(_FULL, 0.2, (0.2, 0.8))[0] == _FULL
    rect, _nudged = compute_zoom_rect(_FULL, 1.5, (0.5, 0.5))
    assert rect == (768, 432, 3072, 1728)
    assert rect[2] % 2 == 0 and rect[3] % 2 == 0


def test_compute_zoom_rect_nudge_stays_inside_or_flips() -> None:
    """Retry nudge moves +8 px, or -8 px when +8 would leave the maximum."""
    _rect, nudged = compute_zoom_rect(_FULL, 1.8, (0.25, 0.35))
    assert nudged == (8, 186, 2560, 1440)
    _rect, nudged = compute_zoom_rect(_FULL, 1.8, (1.0, 0.5))
    assert nudged == (2040, 576, 2560, 1440)


def test_crop_maximum_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default is the 1536x864 centre crop; full-FOV modes and the env override."""
    _clear_zoom_env(monkeypatch)
    assert crop_maximum() == (768, 432, 3072, 1728)

    monkeypatch.setenv("REACHY_CAMERA_SENSOR_MODE", "2304x1296")
    assert crop_maximum() == _FULL
    monkeypatch.setenv("REACHY_CAMERA_SENSOR_MODE", "4608x2592")
    assert crop_maximum() == _FULL
    monkeypatch.setenv("REACHY_CAMERA_SENSOR_MODE", "not-a-mode")
    assert crop_maximum() == (768, 432, 3072, 1728)

    monkeypatch.setenv("REACHY_CAMERA_CROP_MAX", "10,20,100,80")
    assert crop_maximum() == (10, 20, 100, 80)


def test_rects_close_uses_fraction_of_width() -> None:
    """Two crops within 2% of the larger width count as the same view."""
    assert rects_close((0, 0, 1000, 500), (20, 0, 1000, 500))
    assert not rects_close((0, 0, 1000, 500), (21, 0, 1000, 500))


def _render(rect: tuple[int, int, int, int], height: int = 72, width: int = 128) -> np.ndarray:
    """Thumbnail of ``rect`` sampled from a stable sensor-space pattern.

    The pattern varies slowly compared with one thumbnail pixel, so points
    that land on neighbouring pixels still match. A faster pattern aliases
    and the checker reports ``uncertain``.
    """
    uu, vv = np.meshgrid(np.arange(width), np.arange(height))
    sx = rect[0] + uu / (width - 1) * rect[2]
    sy = rect[1] + vv / (height - 1) * rect[3]
    return (
        np.sin(sx / 400.0) * 80
        + np.cos(sy / 280.0) * 60
        + np.sin((sx - 2 * sy) / 520.0) * 40
    ).astype(np.float32)


def test_check_applied_when_thumbnails_match_the_new_crop() -> None:
    """A frame rendered from the new rectangle scores as applied."""
    old = _FULL
    new = (1024, 576, 2560, 1440)
    verdict, applied, unchanged = check(_render(old), _render(new), [old], new)
    assert verdict == APPLIED
    assert applied is not None and applied >= 0.5
    assert unchanged is not None and applied - unchanged >= 0.1


def test_check_not_applied_when_frame_is_unchanged() -> None:
    """The same thumbnail still showing the old crop scores as not applied."""
    rng = np.random.default_rng(0)
    frame = rng.normal(size=(36, 64)).astype(np.float32) * 30 + 80
    verdict, _applied, unchanged = check(
        frame, frame, [_FULL], (1024, 576, 2560, 1440)
    )
    assert verdict == NOT_APPLIED
    assert unchanged is not None and unchanged >= 0.5


def test_check_uncertain_on_flat_or_mismatched_frames() -> None:
    """Textureless frames and different shapes are not evidence either way."""
    flat = np.full((18, 32), 40.0, dtype=np.float32)
    verdict, applied, unchanged = check(flat, flat, [_FULL], (0, 0, 2000, 1000))
    assert verdict == UNCERTAIN
    assert applied is None and unchanged is None

    other = np.zeros((19, 32), dtype=np.float32)
    assert check(flat, other, [_FULL], _FULL)[0] == UNCERTAIN


def test_thumbnail_yuy2_extracts_luma() -> None:
    """YUY2 luma (every other byte) survives the box blur."""
    width, height = 16, 8
    row = bytes([200, 128] * width)
    thumb = thumbnail_yuy2(row * height, width, height)
    assert thumb.shape == (height, width)
    assert thumb.dtype == np.float32
    assert np.allclose(thumb, 200.0)


class _Daemon:
    """Just enough of Daemon for the camera router."""

    def __init__(self, server: Any, released: bool = False) -> None:
        self._media_server = server
        self._media_released = released


def test_get_zoom_returns_media_server_state(router_app: Any) -> None:
    """GET /camera/zoom returns whatever the media server reports."""
    server = MagicMock()
    server.get_zoom.return_value = {"factor": 1.0, "status": "idle"}
    client = router_app(camera.router, daemon=_Daemon(server))

    resp = client.get("/camera/zoom")

    assert resp.status_code == 200
    assert resp.json() == {"factor": 1.0, "status": "idle"}
    server.get_zoom.assert_called_once_with()


def test_set_zoom_forwards_factor_and_center(router_app: Any) -> None:
    """POST /camera/zoom calls set_zoom with the parsed body."""
    server = MagicMock()
    server.set_zoom.return_value = {"factor": 1.8, "rect": [1024, 576, 2560, 1440]}
    client = router_app(camera.router, daemon=_Daemon(server))

    resp = client.post("/camera/zoom", json={"factor": 1.8, "center": [0.25, 0.35]})

    assert resp.status_code == 200
    assert resp.json()["rect"] == [1024, 576, 2560, 1440]
    server.set_zoom.assert_called_once_with(1.8, (0.25, 0.35))


def test_set_zoom_conflict_and_unavailable(router_app: Any) -> None:
    """A missing scaler-crop is 409; released or absent media is 503."""
    server = MagicMock()
    server.set_zoom.side_effect = RuntimeError("Runtime zoom needs a libcamerasrc with scaler-crop.")
    client = router_app(camera.router, daemon=_Daemon(server))
    resp = client.post("/camera/zoom", json={"factor": 2.0})
    assert resp.status_code == 409
    assert "scaler-crop" in resp.json()["detail"]

    client = router_app(camera.router, daemon=_Daemon(server, released=True))
    assert client.get("/camera/zoom").status_code == 503

    client = router_app(camera.router, daemon=_Daemon(None))
    assert client.post("/camera/zoom/reset").status_code == 503


def test_reset_zoom_calls_media_server(router_app: Any) -> None:
    """POST /camera/zoom/reset delegates to reset_zoom."""
    server = MagicMock()
    server.reset_zoom.return_value = {"factor": 1.0, "status": "pending"}
    client = router_app(camera.router, daemon=_Daemon(server))

    resp = client.post("/camera/zoom/reset")

    assert resp.status_code == 200
    assert resp.json()["factor"] == 1.0
    server.reset_zoom.assert_called_once_with()


def test_media_manager_set_and_reset_zoom(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK methods POST the daemon zoom routes, including the URL fallback."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    def _post(url: str, json: dict[str, Any], timeout: float) -> _Resp:
        calls.append((url, json))
        assert timeout == 5
        return _Resp({"factor": json["factor"]})

    monkeypatch.setattr(
        "reachy_mini.media.media_manager.requests.post", _post
    )
    media = MediaManager(backend=MediaBackend.NO_MEDIA, daemon_url="http://robot:8000")

    assert media.set_zoom(1.8, (0.25, 0.35)) == {"factor": 1.8}
    assert media.reset_zoom() == {"factor": 1.0}
    assert calls == [
        ("http://robot:8000/api/camera/zoom", {"factor": 1.8, "center": [0.25, 0.35]}),
        ("http://robot:8000/api/camera/zoom", {"factor": 1.0, "center": [0.5, 0.5]}),
    ]

    calls.clear()
    local = MediaManager(backend=MediaBackend.NO_MEDIA)
    local.camera = SimpleNamespace(daemon_url="http://cam:8000")  # type: ignore[assignment]
    local.set_zoom()
    assert calls[0][0] == "http://cam:8000/api/camera/zoom"

    calls.clear()
    local.camera = None
    local._daemon_url = ""
    local.set_zoom()
    assert calls[0][0] == "http://localhost:8000/api/camera/zoom"
