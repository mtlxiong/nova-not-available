"""World (meters, z-up) -> Three.js render (y-up) transform.

Mirrors ``frontend/src/lib/alignment.ts``; both are covered by the same test vectors.
World ground point (x, y, z) maps to base render (x, z, -y), then yaw about +Y, uniform scale, translation.
"""

from __future__ import annotations

import math

from echo.schemas import Alignment


def world_to_render(alignment: Alignment, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
    bx, by, bz = x, z, -y
    theta = math.radians(alignment.yaw_deg)
    c, s = math.cos(theta), math.sin(theta)
    rx = c * bx + s * bz
    rz = -s * bx + c * bz
    k = alignment.scale
    tx, ty, tz = alignment.translation
    return (k * rx + tx, k * by + ty, k * rz + tz)


def render_to_world(alignment: Alignment, rx: float, ry: float, rz: float) -> tuple[float, float, float]:
    tx, ty, tz = alignment.translation
    k = alignment.scale
    px, py, pz = (rx - tx) / k, (ry - ty) / k, (rz - tz) / k
    theta = math.radians(alignment.yaw_deg)
    c, s = math.cos(theta), math.sin(theta)
    bx = c * px - s * pz
    bz = s * px + c * pz
    return (bx, -bz, py)
