"""ArUco fiducial detection for uniquely labeled objects (order bags, delivery containers).

Marker IDs are a separate namespace from tracker IDs. The ``MarkerRegistry`` is the explicit
association layer from a printed marker ID to an internal object ID. A marker says *which
container* this is; it does not say which order is inside unless an order is linked to that
object (see ``echo.memory.orders``).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from echo.schemas import BBox, EntitySource, ImagePoint, Track
from echo.video.sources import Frame

ARUCO_DICTIONARIES = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
}


@dataclass(frozen=True)
class RegisteredObject:
    marker_id: int
    object_id: str
    class_name: str
    label: str


class MarkerRegistry:
    def __init__(self, objects: list[RegisteredObject] | None = None):
        self._by_marker: dict[int, RegisteredObject] = {}
        for obj in objects or []:
            self.register(obj)

    def register(self, obj: RegisteredObject) -> None:
        existing = next((o for o in self._by_marker.values() if o.object_id == obj.object_id and o.marker_id != obj.marker_id), None)
        if existing:
            raise ValueError(f"object {obj.object_id} is already bound to marker {existing.marker_id}")
        self._by_marker[obj.marker_id] = obj

    def lookup(self, marker_id: int) -> RegisteredObject | None:
        return self._by_marker.get(marker_id)

    def all(self) -> list[RegisteredObject]:
        return sorted(self._by_marker.values(), key=lambda o: o.marker_id)


def default_marker_registry() -> MarkerRegistry:
    return MarkerRegistry(
        [
            RegisteredObject(7, "order-bag-A", "order_bag", "Order bag A"),
            RegisteredObject(8, "order-bag-B", "order_bag", "Order bag B"),
            RegisteredObject(12, "hot-box-1", "delivery_container", "Insulated delivery box 1"),
        ]
    )


class ArucoMarkerDetector:
    def __init__(self, dictionary: str = "DICT_4X4_50"):
        if dictionary not in ARUCO_DICTIONARIES:
            raise ValueError(f"unknown ArUco dictionary {dictionary}")
        params = cv2.aruco.DetectorParameters()
        self._detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICTIONARIES[dictionary]), params)

    def detect(self, frame: Frame) -> list[Track]:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY) if frame.image.ndim == 3 else frame.image
        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        tracks = []
        for quad, marker_id in zip(corners, ids.flatten()):
            pts = np.asarray(quad, dtype=np.float64).reshape(4, 2)
            center = pts.mean(axis=0)
            tracks.append(
                Track(
                    source=EntitySource.ARUCO,
                    track_id=str(int(marker_id)),
                    bbox=BBox(x1=float(pts[:, 0].min()), y1=float(pts[:, 1].min()), x2=float(pts[:, 0].max()), y2=float(pts[:, 1].max())),
                    confidence=1.0,
                    class_name="marker",
                    reference_point=ImagePoint(u=float(center[0]), v=float(center[1])),
                    marker_id=int(marker_id),
                )
            )
        return tracks
