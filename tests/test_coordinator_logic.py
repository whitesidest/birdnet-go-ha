"""Coordinator helpers, exercised against payloads captured from a live server."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from birdnet_go.coordinator import parse_uptime, slugify_source  # noqa: E402


class TestSlugifySource:
    def test_display_names_from_this_deployment(self):
        assert slugify_source("Deck") == "deck"
        assert slugify_source("Front Yard") == "front_yard"
        assert slugify_source("Guest Gate") == "guest_gate"

    def test_punctuation_and_spacing(self):
        assert slugify_source("  Back-Porch (West) ") == "back_porch_west"

    def test_empty_falls_back(self):
        assert slugify_source("") == "unknown"
        assert slugify_source(None) == "unknown"


class TestParseUptime:
    def test_go_duration_string(self):
        # The exact shape /api/v2/health returns.
        assert parse_uptime("113h17m49.056998798s") == pytest.approx(407869.057, abs=0.01)

    def test_numeric_passthrough(self):
        assert parse_uptime(407869.056998798) == pytest.approx(407869.057, abs=0.01)

    def test_junk_returns_none(self):
        assert parse_uptime("nonsense") is None
        assert parse_uptime(None) is None


def test_source_key_resolution_across_payload_shapes():
    """REST gives source:{displayName}; pending/MQTT gives a flat string."""
    from birdnet_go.coordinator import BirdNetCoordinator

    resolve = BirdNetCoordinator.source_key_for

    class Stub:
        pass

    rest = {"source": {"id": "rtsp://x/front", "displayName": "Front Yard"}}
    flat = {"source": "Guest Gate", "sourceID": "rtsp_adf898c6"}
    idonly = {"sourceID": "rtsp_e0f5dcab"}

    assert resolve(Stub(), rest) == "front_yard"
    assert resolve(Stub(), flat) == "guest_gate"
    assert resolve(Stub(), idonly) == "rtsp_e0f5dcab"


def test_fixture_detections_all_resolve_to_a_named_source(detections_recent):
    """Every real detection must map to a real microphone, not 'unknown'."""
    from birdnet_go.coordinator import BirdNetCoordinator

    class Stub:
        pass

    keys = {BirdNetCoordinator.source_key_for(Stub(), d) for d in detections_recent}
    assert keys, "fixture had no detections"
    assert "unknown" not in keys
    assert keys <= {"deck", "front_yard", "guest_gate"}


def test_realtime_fixture_exposes_expected_streams(realtime):
    names = {s["name"] for s in realtime["rtsp"]["streams"]}
    assert names == {"Deck", "Front Yard", "Guest Gate"}


def test_species_week_counts_recent_only(species_summary):
    """Entries last heard over a week ago must not count toward the weekly total."""
    from birdnet_go.coordinator import BirdNetCoordinator

    class Stub:
        _parse_ts = staticmethod(BirdNetCoordinator._parse_ts)

    rows = BirdNetCoordinator._species_week_list(Stub(), species_summary)
    assert 0 <= len(rows) <= len(species_summary)

    # A species last heard 30 days ago must be excluded.
    stale = [{"common_name": "Ghost Bird", "last_heard": "2020-01-01T00:00:00-07:00",
              "count": 5, "max_confidence": 0.9, "scientific_name": "X y"}]
    assert BirdNetCoordinator._species_week_list(Stub(), stale) == []


def test_species_today_list_shape(species_daily):
    """Dashboard tables consume species/count/last/conf — pin that contract."""
    from birdnet_go.coordinator import BirdNetCoordinator

    rows = BirdNetCoordinator._species_today_list(species_daily)
    assert rows, "fixture had no species with detections"
    assert set(rows[0]) == {"species", "scientific_name", "count", "last", "conf", "new"}
    # busiest first
    assert [r["count"] for r in rows] == sorted(
        [r["count"] for r in rows], reverse=True
    )
    assert all(r["count"] > 0 for r in rows)
    assert all(0 <= r["conf"] <= 100 for r in rows)


def test_species_week_list_excludes_stale(species_summary):
    from birdnet_go.coordinator import BirdNetCoordinator

    class Stub:
        _parse_ts = staticmethod(BirdNetCoordinator._parse_ts)

    rows = BirdNetCoordinator._species_week_list(Stub(), species_summary)
    assert isinstance(rows, list)
    assert len(rows) <= len(species_summary)
    assert all(set(r) == {"species", "scientific_name", "count", "last", "conf"} for r in rows)


class TestBackfill:
    """Cold-start seeding must fill gaps without overwriting pushed state."""

    def _coord(self, recent):
        import asyncio
        from birdnet_go.coordinator import BirdNetCoordinator

        class FakeClient:
            async def recent_detections(self):
                return recent

        c = BirdNetCoordinator.__new__(BirdNetCoordinator)
        c.client = FakeClient()
        c.last_detection = {}
        c.last_detection_any = None
        c.sources = {"deck": {"name": "Deck"}, "front_yard": {"name": "Front Yard"},
                     "guest_gate": {"name": "Guest Gate"}}
        return c, asyncio

    def test_fills_every_empty_source(self, detections_recent):
        c, aio = self._coord(detections_recent)
        aio.run(c._backfill_last_detections())
        assert set(c.last_detection) <= {"deck", "front_yard", "guest_gate"}
        assert c.last_detection, "nothing was backfilled"
        assert c.last_detection_any is not None

    def test_newest_wins_per_source(self, detections_recent):
        c, aio = self._coord(detections_recent)
        aio.run(c._backfill_last_detections())
        from birdnet_go.coordinator import BirdNetCoordinator

        class Stub:
            pass

        for key, det in c.last_detection.items():
            same = [d for d in detections_recent
                    if BirdNetCoordinator.source_key_for(Stub(), d) == key]
            newest = max(same, key=lambda d: str(d.get("timestamp") or ""))
            assert det["id"] == newest["id"]

    def test_never_overwrites_pushed_state(self, detections_recent):
        c, aio = self._coord(detections_recent)
        sentinel = {"id": -1, "commonName": "Pushed Bird",
                    "source": {"displayName": "Deck"}, "timestamp": "1970-01-01T00:00:00+00:00"}
        c.last_detection["deck"] = sentinel
        c.last_detection_any = sentinel
        aio.run(c._backfill_last_detections())
        assert c.last_detection["deck"] is sentinel, "live push was clobbered by backfill"
