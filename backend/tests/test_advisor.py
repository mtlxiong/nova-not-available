import json

from echo.advisor import advise_session, extract_output_text, handoffs, parse_advice_text, process_times


def _metrics():
    return {
        "session_id": "s1",
        "simulated": True,
        "duration_ms": 20_000,
        "data_quality": {"calibrated": True, "calibration_rmse_m": 0.0, "positioned_observations": 8, "notes": []},
        "zones": [
            {"zone_id": "prep", "name": "Prep", "kind": "workstation", "visits": 2, "mean_dwell_s": 4.0, "total_dwell_s": 8.0, "max_occupancy": 1, "mean_occupancy": 0.4},
            {"zone_id": "packing", "name": "Delivery Packing", "kind": "workstation", "visits": 3, "mean_dwell_s": 9.5, "total_dwell_s": 28.5, "max_occupancy": 2, "mean_occupancy": 1.1},
            {"zone_id": "pickup", "name": "Courier Pickup", "kind": "pickup", "visits": 0, "mean_dwell_s": None, "total_dwell_s": 0, "max_occupancy": 0, "mean_occupancy": 0},
        ],
        "zone_visits": [
            {"entity_id": "t1", "zone_id": "prep", "start_ms": 0, "end_ms": 4000},
            {"entity_id": "t1", "zone_id": "packing", "start_ms": 6000, "end_ms": 11000},
        ],
        "orders": {"available": False, "reason": "no orders linked"},
        "queues": {"available": False, "reason": "no queue zones"},
    }


def test_process_times_use_mean_dwell_and_skip_empty_zones():
    rows = process_times(_metrics())
    assert [r["zone_id"] for r in rows] == ["packing", "prep"]
    assert rows[0]["process_time_s"] == 9.5


def test_handoffs_measure_the_gap_between_stations():
    gaps = handoffs(_metrics())
    assert gaps == [
        {
            "from_zone": "prep",
            "from_name": "Prep",
            "to_zone": "packing",
            "to_name": "Delivery Packing",
            "count": 1,
            "mean_gap_s": 2.0,
        }
    ]


def test_advice_without_a_key_still_returns_measured_times(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    body = advise_session(_metrics(), api_key="")
    assert body["status"] == "unavailable"
    assert body["process_times"][0]["zone_id"] == "packing"
    assert body["suggestions"] == []


def test_advice_parses_model_json_and_does_not_send_track_ids():
    seen = {}

    def transport(url, headers, body):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        seen["input"] = body["input"]
        assert body["model"] == "~openai/gpt-sol-latest"
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "summary": "Packing visits run longer than prep.",
                                    "suggestions": ["Stage bags before the packer arrives."],
                                }
                            ),
                        }
                    ],
                }
            ],
        }

    body = advise_session(_metrics(), api_key="test-key", transport=transport)
    assert seen["url"].endswith("/api/v1/responses")
    assert seen["auth"] == "Bearer test-key"
    assert "t1" not in seen["input"]
    assert body["status"] == "ok"
    assert body["summary"].startswith("Packing")
    assert body["suggestions"] == ["Stage bags before the packer arrives."]
    assert body["simulated"] is True


def test_extracts_fenced_json():
    text = extract_output_text({"output_text": '```json\n{"summary":"ok","suggestions":["a"]}\n```'})
    summary, suggestions = parse_advice_text(text)
    assert summary == "ok" and suggestions == ["a"]
