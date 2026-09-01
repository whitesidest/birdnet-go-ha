# Fixtures

Captured from a live BirdNET-Go server (build `20260716`) so the tests exercise
real payload shapes rather than hand-written guesses.

**Redacted for publication:** geographic coordinates are zeroed and LAN
addresses are rewritten to the RFC 5737 `192.0.2.0/24` documentation range.
Nothing in the test suite asserts on either, so the substitution is inert.

| File | Source |
|---|---|
| `detections_recent.json` | `GET /api/v2/detections/recent` |
| `health.json` | `GET /api/v2/health` |
| `species_daily.json` | `GET /api/v2/analytics/species/daily` |
| `species_summary.json` | `GET /api/v2/analytics/species/summary` (truncated) |
| `realtime.json` | `GET /api/v2/settings/realtime`, `rtsp` block only |
| `sse_detection.json` | a `detection` event from `GET /api/v2/detections/stream` |
| `sse_pending.json` | a `pending` event from the same stream |
