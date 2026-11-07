import cv2
import numpy as np
import pytest

from echo.schemas import CalibrationPoint, CalibrationRequest
from echo.spatial.calibration import CalibrationError, GroundPlaneProjector, compute_calibration, project

WORLD = [(0, 0), (9, 0), (9, 6), (0, 6), (3.5, 2.6), (5.5, 2.6)]
IMAGE_QUAD = np.array([[80, 700], [1200, 700], [970, 216], [310, 216]], dtype=np.float32)
H_WORLD_TO_IMAGE = cv2.getPerspectiveTransform(np.array(WORLD[:4], dtype=np.float32), IMAGE_QUAD).astype(np.float64)


def _request(world=WORLD, noise=0.0, **kw):
    img, _ = project(H_WORLD_TO_IMAGE, np.array(world, dtype=np.float64))
    rng = np.random.default_rng(0)
    img = img + rng.normal(0, noise, img.shape) if noise else img
    return CalibrationRequest(
        image_width=1280,
        image_height=720,
        points=[CalibrationPoint(image=(float(u), float(v)), world=w) for (u, v), w in zip(img, world)],
        **kw,
    )


def test_recovers_known_homography():
    result = compute_calibration(_request())
    assert result.reprojection_rmse_m < 1e-6
    projector = GroundPlaneProjector(result)
    for x, y in [(1.0, 1.0), (4.5, 3.0), (8.2, 5.5)]:
        pts, _ = project(H_WORLD_TO_IMAGE, np.array([[x, y]]))
        pos = projector.image_to_world(float(pts[0, 0]), float(pts[0, 1]))
        assert pos is not None
        assert pos.x == pytest.approx(x, abs=1e-6) and pos.y == pytest.approx(y, abs=1e-6)


def test_noisy_points_report_reprojection_error():
    result = compute_calibration(_request(noise=1.5))
    assert 0 < result.reprojection_rmse_m < 0.1
    assert len(result.point_errors_m) == len(WORLD)


def test_four_points_warn_about_no_redundancy():
    result = compute_calibration(_request(world=WORLD[:4]))
    assert any("exactly 4" in w for w in result.warnings)


@pytest.mark.parametrize(
    "world,match",
    [
        (WORLD[:3], "at least 4"),
        ([(0, 0), (1, 0), (2, 0), (3, 0)], "collinear"),
        ([(0, 0), (0, 0), (9, 6), (0, 6)], "duplicate"),
    ],
)
def test_rejects_degenerate_input(world, match):
    with pytest.raises(CalibrationError, match=match):
        compute_calibration(_request(world=world))


def test_rejects_swapped_correspondences():
    req = _request()
    pts = list(req.points)
    pts[0], pts[2] = CalibrationPoint(image=pts[0].image, world=pts[2].world), CalibrationPoint(image=pts[2].image, world=pts[0].world)
    with pytest.raises(CalibrationError):
        compute_calibration(req.model_copy(update={"points": pts}))


def test_rejects_points_outside_image():
    req = _request()
    pts = list(req.points)
    pts[0] = CalibrationPoint(image=(-5.0, 10.0), world=pts[0].world)
    with pytest.raises(CalibrationError, match="inside the image"):
        compute_calibration(req.model_copy(update={"points": pts}))


def test_projector_refuses_points_outside_frame_and_beyond_horizon():
    projector = GroundPlaneProjector(compute_calibration(_request()))
    assert projector.image_to_world(-1, 100) is None
    assert projector.image_to_world(640, 2000) is None


def test_projector_refuses_points_beyond_horizon():
    # Steep perspective: floor side edges converge at v ~= 259 px, inside the frame.
    quad = np.array([[0, 700], [1280, 700], [700, 300], [580, 300]], dtype=np.float32)
    h = cv2.getPerspectiveTransform(np.array(WORLD[:4], dtype=np.float32), quad).astype(np.float64)
    img, _ = project(h, np.array(WORLD, dtype=np.float64))
    req = CalibrationRequest(
        image_width=1280,
        image_height=720,
        points=[CalibrationPoint(image=(float(u), float(v)), world=w) for (u, v), w in zip(img, WORLD)],
    )
    projector = GroundPlaneProjector(compute_calibration(req))
    assert projector.image_to_world(640, 400) is not None
    assert projector.image_to_world(640, 100) is None


def test_projector_rescales_for_different_frame_resolution():
    result = compute_calibration(_request())
    full = GroundPlaneProjector(result)
    half = GroundPlaneProjector(result, frame_width=640, frame_height=360)
    a = full.image_to_world(640, 500)
    b = half.image_to_world(320, 250)
    assert a is not None and b is not None
    assert a.x == pytest.approx(b.x, abs=1e-9) and a.y == pytest.approx(b.y, abs=1e-9)
