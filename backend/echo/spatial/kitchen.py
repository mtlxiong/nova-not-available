"""Kitchen *template*: a 9 m x 6 m restaurant kitchen with a delivery packing flow.

This is one example environment, used by the labeled simulation and available as a template.
Real cameras get their own spatial model from scene analysis (see ``echo.analysis``) or manual
editing; nothing in the live pipeline assumes this layout.

World frame: origin at the south-west floor corner, +x east, +y north, meters. Zones are the floor
areas where people stand to work a station, not the counter tops themselves.
"""

from __future__ import annotations

from echo.schemas import Fixture, ReferencePoint, SpatialModel, Zone
from echo.spatial.geometry import rect

KITCHEN_WIDTH_M = 9.0
KITCHEN_DEPTH_M = 6.0
KITCHEN_TEMPLATE_ID = "kitchen-template"


def default_kitchen() -> SpatialModel:
    z = dict(provenance="template")
    zones = [
        Zone(zone_id="prep", name="Prep", polygon=rect(0.5, 4.3, 3.0, 5.3), color="#22c55e", **z),
        Zone(zone_id="grill", name="Grill", polygon=rect(3.5, 4.3, 5.5, 5.3), color="#ef4444", **z),
        Zone(zone_id="fry", name="Fry", polygon=rect(6.0, 4.3, 8.0, 5.3), color="#f97316", **z),
        Zone(zone_id="expo", name="Expo / Plating", polygon=rect(3.5, 1.8, 5.5, 2.6), color="#eab308", **z),
        Zone(zone_id="packing", name="Delivery Packing", polygon=rect(0.7, 1.0, 1.7, 3.0), color="#a855f7", **z),
        Zone(zone_id="pickup", name="Courier Pickup", kind="pickup", polygon=rect(7.3, 0.5, 8.3, 2.5), color="#06b6d4", **z),
        Zone(zone_id="dish", name="Dish", polygon=rect(1.8, 0.6, 3.6, 1.4), color="#64748b", **z),
    ]
    fixtures = [
        Fixture(fixture_id="prep-counter", label="Prep counter", center=(1.75, 5.65), size=(2.5, 0.7, 0.9), zone_id="prep", **z),
        Fixture(fixture_id="grill-line", kind="appliance", label="Grill line", center=(4.5, 5.65), size=(2.0, 0.7, 0.9), zone_id="grill", **z),
        Fixture(fixture_id="fryers", kind="appliance", label="Fryers", center=(7.0, 5.65), size=(2.0, 0.7, 0.9), zone_id="fry", **z),
        Fixture(fixture_id="expo-island", label="Expo island", center=(4.5, 3.0), size=(2.0, 0.8, 0.9), zone_id="expo", **z),
        Fixture(fixture_id="packing-counter", label="Packing counter", center=(0.35, 2.0), size=(0.7, 2.0, 0.9), zone_id="packing", **z),
        Fixture(fixture_id="pickup-shelf", kind="shelf", label="Pickup shelf", center=(8.65, 1.5), size=(0.7, 2.0, 1.6), zone_id="pickup", **z),
        Fixture(fixture_id="dish-sink", kind="appliance", label="Dish sink", center=(2.7, 0.3), size=(1.8, 0.6, 0.9), zone_id="dish", **z),
    ]
    reference_points = [
        ReferencePoint(point_id="floor-sw", label="Floor SW corner", x=0.0, y=0.0),
        ReferencePoint(point_id="floor-se", label="Floor SE corner", x=KITCHEN_WIDTH_M, y=0.0),
        ReferencePoint(point_id="floor-ne", label="Floor NE corner", x=KITCHEN_WIDTH_M, y=KITCHEN_DEPTH_M),
        ReferencePoint(point_id="floor-nw", label="Floor NW corner", x=0.0, y=KITCHEN_DEPTH_M),
        ReferencePoint(point_id="island-sw", label="Expo island SW foot", x=3.5, y=2.6),
        ReferencePoint(point_id="island-se", label="Expo island SE foot", x=5.5, y=2.6),
    ]
    floor = rect(0.0, 0.0, KITCHEN_WIDTH_M, KITCHEN_DEPTH_M)
    return SpatialModel(
        model_id=KITCHEN_TEMPLATE_ID,
        name="Template: restaurant kitchen (9 m x 6 m)",
        width_m=KITCHEN_WIDTH_M,
        depth_m=KITCHEN_DEPTH_M,
        floor_polygon=floor,
        navigable_regions=[rect(0.7, 0.6, 8.3, 5.3)],
        zones=zones,
        fixtures=fixtures,
        reference_points=reference_points,
        source="template",
        notes=["Example layout used by the simulation. Not derived from any camera."],
    )
