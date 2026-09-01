"""Test fixtures for the BirdNET-Go integration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def detections_recent():
    return load("detections_recent.json")


@pytest.fixture
def health():
    return load("health.json")


@pytest.fixture
def species_daily():
    return load("species_daily.json")


@pytest.fixture
def species_summary():
    return load("species_summary.json")


@pytest.fixture
def realtime():
    return load("realtime.json")
