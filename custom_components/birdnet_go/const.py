"""Constants for the BirdNET-Go integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "birdnet_go"
MANUFACTURER = "BirdNET-Go"

# Config keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SSL = "ssl"
CONF_TOKEN = "token"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_PORT = 8080
DEFAULT_SSL = False
DEFAULT_VERIFY_SSL = True

# BirdNET-Go's REST API is unauthenticated for LAN clients when
# security.allowSubnetBypass is enabled (the common self-hosted setup). The
# token is therefore OPTIONAL — it is only needed when the server is reached
# across a subnet boundary or has privateMode locked down.
CONF_MIN_CONFIDENCE = "min_confidence"
DEFAULT_MIN_CONFIDENCE = 0.0

# Analytics/health polling. Detections themselves arrive over SSE, so this
# interval only governs the aggregate counters and diagnostics — it can be
# lazy without making the dashboard feel stale.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)

# Dispatcher signals
SIGNAL_DETECTION = f"{DOMAIN}_detection"
SIGNAL_STREAM_STATE = f"{DOMAIN}_stream_state"

# Home Assistant event-bus events
EVENT_DETECTION = f"{DOMAIN}_detection"
EVENT_NEW_SPECIES = f"{DOMAIN}_new_species"

# A source is considered "active" if it produced a detection within this
# window. Birds are not continuous, so this is deliberately generous — it is a
# liveness hint, not a silence detector.
SOURCE_ACTIVE_WINDOW = timedelta(minutes=30)

# SSE reconnect backoff
STREAM_BACKOFF_MIN = 5
STREAM_BACKOFF_MAX = 300
