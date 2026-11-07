# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is someone responsible for a real space. They open ECHO to map that space from a camera, watch how people and objects move through it, replay what was recorded, and read zone metrics.

## Product Purpose

ECHO turns a camera feed into a spatial twin of the space it sees, so the person responsible for that space can watch it live and keep a record they can replay and measure. A session succeeds when both are true: the live view is how the space is watched, and the mapping plus replay are the record.

## Positioning

The floor calibration is the measurement. People and objects are tracked anonymously on that floor, and a generated 3D picture of the room, when one exists, is only a backdrop. A viewer that redraws the camera image, or a tracker that identifies people, is a different product.

## Operating Context

The operator points a webcam or a video file at a physical space, or starts from the labeled simulation. Analysis builds a workspace from that footage: floor extent, object labels, activity zones, and a calibration. A live session draws anonymous tracks on that floor. A stored session can be opened later, scrubbed, and measured. The app runs locally: the API on port 8000 and the interface on port 5180.

## Capabilities and Constraints

- Tracks are anonymous session IDs. No face recognition, and no ranking of people.
- Distances, zones, and metrics come from the calibrated floor model. A Gaussian splat never supplies a measurement.
- World Labs image-to-world generation stays optional and paid. The room works with no API key, using the reconstructed floor and stock materials.
- Detector labels and zones stay editable. A proposed zone is a proposal, not a fact. The operator can rename, re-kind, delete, or draw zones.
- Auto-calibration from assumed person height is approximate. Measured floor points are required when the numbers have to be real.
- Object sizes the camera cannot see are category estimates and are marked as such.
- The current analysis builds a workspace from the footage rather than from a chosen room type. That behavior was not confirmed as a constraint later work must keep.
- YOLO-World weights are AGPL-3.0. A COCO-only detector is the fallback when that license is unacceptable.

## Brand Commitments

The product name is ECHO.

## Evidence on Hand

- Product description, setup, and stated limits: `README.md`.
- Single-machine timing notes in that README are observations, not benchmarks.
- Sample clip used in development: `data/samples/vtest.avi`, when present.
- No customer quotes, case studies, pricing, or licensing claims beyond the YOLO-World note above. Do not invent them.

## Product Principles

- The person in the frame stays anonymous.
- The floor is the measurement. A rendered world never becomes the measurement.
- The room is usable without a paid world-generation key.
- What the detector proposes, the operator can correct.
- A session is watched live and kept as a record.
