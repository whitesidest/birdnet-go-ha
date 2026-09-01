"""The SSE parser is the riskiest part of this integration: it has to match
BirdNET-Go's own frontend, which dispatches on an in-band ``eventType`` field
and falls back to a duck-type check. These tests pin that behaviour.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from birdnet_go.stream import BirdNetStream, _looks_like_detection  # noqa: E402


class _Harness(BirdNetStream):
    """Bypass __init__ so we can exercise _dispatch without a session."""

    def __init__(self):  # noqa: D107
        self.seen: list[dict] = []
        self._on_detection = self.seen.append
        self._on_state = lambda _v: None


@pytest.fixture
def harness():
    return _Harness()


def test_heartbeat_and_connected_are_ignored(harness):
    harness._dispatch("heartbeat", json.dumps({"clients": 2, "timestamp": 1}))
    harness._dispatch("connected", json.dumps({"clientId": "abc"}))
    assert harness.seen == []


def test_pending_is_ignored(harness):
    """Pending candidates churn active/rejected; acting on them makes entities flap."""
    pending = [
        {
            "species": "Swinhoe's White-eye",
            "sourceID": "rtsp_adf898c6",
            "source": "Guest Gate",
            "status": "active",
        }
    ]
    harness._dispatch("pending", json.dumps(pending))
    assert harness.seen == []


def test_named_detection_event(harness, detections_recent):
    det = detections_recent[0]
    harness._dispatch("detection", json.dumps(det))
    assert len(harness.seen) == 1
    assert harness.seen[0]["commonName"] == det["commonName"]


def test_eventtype_field_wins_over_event_name(harness, detections_recent):
    """The official frontend trusts the in-band eventType field."""
    payload = dict(detections_recent[0])
    payload["eventType"] = "detection"
    harness._dispatch(None, json.dumps(payload))
    assert len(harness.seen) == 1


def test_unlabelled_payload_uses_duck_typing(harness, detections_recent):
    """Frontend fallback: id + commonName means it's a detection."""
    harness._dispatch(None, json.dumps(detections_recent[0]))
    assert len(harness.seen) == 1


def test_detection_wrapped_in_data_key(harness, detections_recent):
    harness._dispatch(
        "detection", json.dumps({"eventType": "detection", "data": detections_recent[0]})
    )
    assert len(harness.seen) == 1
    assert harness.seen[0]["id"] == detections_recent[0]["id"]


def test_malformed_json_is_swallowed(harness):
    harness._dispatch("detection", "{not json")
    assert harness.seen == []


def test_looks_like_detection_rejects_partials():
    assert not _looks_like_detection({"id": 1})
    assert not _looks_like_detection({"commonName": "Crow"})
    assert not _looks_like_detection(None)
    assert _looks_like_detection({"id": 1, "commonName": "Crow"})
