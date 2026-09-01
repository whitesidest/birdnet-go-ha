"""Audio-level accumulation.

The upstream stream fires ~16x/sec, so the accumulate-then-flush design is the
only thing keeping this off the recorder's back. These tests pin the peak-hold
and clipping-latch semantics that make the summary meaningful.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

from birdnet_go.audio_stream import BirdNetAudioLevelStream  # noqa: E402
from birdnet_go.coordinator import BirdNetCoordinator  # noqa: E402


def _coord():
    c = BirdNetCoordinator.__new__(BirdNetCoordinator)
    c.audio_levels = {}
    c._audio_accum = {}
    c.audio_stream_connected = True
    return c


def test_real_message_maps_to_source_keys(audio_level):
    c = _coord()
    c.record_audio_levels(audio_level["levels"])
    c.flush_audio_levels()
    assert set(c.audio_levels) == {"deck", "front_yard", "guest_gate"}


def test_peak_is_held_across_the_window():
    """A transient must survive; sampling the last value would lose it."""
    c = _coord()
    for lvl in (3, 61, 4, 0, 2):
        c.record_audio_levels({"s": {"name": "Deck", "level": lvl, "clipping": False}})
    c.flush_audio_levels()
    assert c.audio_levels["deck"]["level"] == 61


def test_clipping_latches_for_the_window():
    c = _coord()
    c.record_audio_levels({"s": {"name": "Deck", "level": 10, "clipping": False}})
    c.record_audio_levels({"s": {"name": "Deck", "level": 12, "clipping": True}})
    c.record_audio_levels({"s": {"name": "Deck", "level": 11, "clipping": False}})
    c.flush_audio_levels()
    assert c.audio_levels["deck"]["clipping"] is True


def test_accumulator_resets_between_windows():
    c = _coord()
    c.record_audio_levels({"s": {"name": "Deck", "level": 90, "clipping": True}})
    c.flush_audio_levels()
    c.record_audio_levels({"s": {"name": "Deck", "level": 5, "clipping": False}})
    c.flush_audio_levels()
    assert c.audio_levels["deck"] == {"level": 5, "clipping": False}


def test_flush_reports_only_changed_sources():
    c = _coord()
    c.record_audio_levels({"s": {"name": "Deck", "level": 7, "clipping": False}})
    assert c.flush_audio_levels() == {"deck"}
    c.record_audio_levels({"s": {"name": "Deck", "level": 7, "clipping": False}})
    assert c.flush_audio_levels() == set(), "unchanged source should not write state"


def test_garbage_entries_are_skipped():
    c = _coord()
    c.record_audio_levels({"a": None, "b": {"level": 5}, "c": {"name": "", "level": 5}})
    c.flush_audio_levels()
    assert c.audio_levels == {}


class _Harness(BirdNetAudioLevelStream):
    def __init__(self):
        self.seen = []
        self._on_levels = self.seen.append


def test_stream_accepts_real_payload(audio_level):
    h = _Harness()
    h.handle(None, audio_level)
    assert len(h.seen) == 1
    assert set(h.seen[0]) == set(audio_level["levels"])


def test_stream_ignores_foreign_payloads():
    h = _Harness()
    h.handle(None, {"type": "something-else", "levels": {"x": {}}})
    h.handle(None, {"type": "audio-level"})
    h.handle(None, [1, 2, 3])
    assert h.seen == []
