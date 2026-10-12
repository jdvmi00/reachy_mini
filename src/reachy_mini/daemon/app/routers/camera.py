"""Camera-related API routes.

Exposes camera specifications detected by the daemon so that clients
(SDK, REST, web UIs) can use the correct intrinsic matrix, distortion
coefficients, and available resolutions without hardcoding them.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from reachy_mini.media.camera_constants import get_camera_specs_by_name

from ...daemon import Daemon
from ..dependencies import get_daemon

router = APIRouter(prefix="/camera")


class ResolutionInfo(BaseModel):
    """A single camera resolution entry."""

    name: str
    width: int
    height: int
    fps: int
    crop_factor: float


class CameraSpecsResponse(BaseModel):
    """Full camera specifications as detected by the daemon."""

    name: str
    available_resolutions: list[ResolutionInfo]
    default_resolution: ResolutionInfo
    K: list[list[float]]
    D: list[float]


def _resolution_info(res) -> ResolutionInfo:  # type: ignore[no-untyped-def]
    """Convert a ``CameraResolution`` enum member to a ``ResolutionInfo``."""
    w, h, fps, crop = res.value
    return ResolutionInfo(
        name=res.name,
        width=w,
        height=h,
        fps=fps,
        crop_factor=crop,
    )


@router.get("/specs")
async def get_camera_specs(
    daemon: Daemon = Depends(get_daemon),
) -> CameraSpecsResponse:
    """Get the detected camera specifications.

    Returns the camera name, available resolutions, default resolution,
    intrinsic matrix (K) and distortion coefficients (D) for the camera
    that the daemon detected at startup.
    """
    specs = get_camera_specs_by_name(daemon.status().camera_specs_name)
    return CameraSpecsResponse(
        name=specs.name,
        available_resolutions=[
            _resolution_info(r) for r in specs.available_resolutions
        ],
        default_resolution=_resolution_info(specs.default_resolution),
        K=specs.K.tolist(),
        D=specs.D.tolist(),
    )


class ZoomRequest(BaseModel):
    """Zoom request: factor >= 1 and crop centre in normalized FOV coords."""

    factor: float = 1.0
    center: tuple[float, float] = (0.5, 0.5)


def _media_server(daemon: Daemon) -> Any:
    """Return the live media server, or 503 when media is released."""
    ms = getattr(daemon, "_media_server", None)
    if ms is None or getattr(daemon, "_media_released", False):
        raise HTTPException(status_code=503, detail="Media server not available.")
    return ms


def _zoom_state(value: Any) -> dict[str, Any]:
    """Narrow a media-server zoom dict for the response."""
    return cast(dict[str, Any], value)


@router.get("/zoom")
async def get_zoom(daemon: Daemon = Depends(get_daemon)) -> dict[str, Any]:
    """Get the current camera zoom (scaler-crop) state."""
    return _zoom_state(_media_server(daemon).get_zoom())


@router.post("/zoom")
async def set_zoom(
    req: ZoomRequest, daemon: Daemon = Depends(get_daemon)
) -> dict[str, Any]:
    """Zoom the camera stream at runtime (applies to every consumer)."""
    try:
        return _zoom_state(_media_server(daemon).set_zoom(req.factor, req.center))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/zoom/reset")
async def reset_zoom(daemon: Daemon = Depends(get_daemon)) -> dict[str, Any]:
    """Reset the camera to the full field of view."""
    return _zoom_state(_media_server(daemon).reset_zoom())
