# BirdNET-Go for Home Assistant

A HACS custom integration for [BirdNET-Go](https://github.com/tphakala/birdnet-go).
Every microphone becomes its own Home Assistant device, detections arrive over a
live push stream, and each detection lands on the event bus so you can build
automations against it.

## Why not just use BirdNET-Go's MQTT output?

BirdNET-Go's built-in MQTT discovery publishes **every detection to a single flat
`birdnet` topic**, then auto-generates one HA sensor per source whose value
template reads roughly:

```jinja
{{ value_json.CommonName if value_json.sourceId == 'rtsp_e0f5dcab' else None }}
```

Because all sources subscribe to that same topic, **every detection updates every
sensor**. The one matching source gets the species; the rest render `None`, which
Home Assistant stores as `unknown`. With three microphones you never see more than
one populated at a time, and it looks like two of them are broken.

This integration reads the REST/SSE API instead, where each detection carries its
own `source.displayName`. Sources become separate devices, so they cannot
overwrite each other, and values persist until that source hears something new.

## Features

- **Live push** via `GET /api/v2/detections/stream` (SSE) — `iot_class: local_push`,
  with automatic reconnect and exponential backoff.
- **One device per audio source**, discovered from `rtsp.streams` and keyed on the
  stream name (not BirdNET-Go's URL-derived `rtsp_<hash>`, so re-addressing a
  camera doesn't orphan your entities).
- **Rarity data** surfaced from the API: `days_since_first_seen`, `days_this_year`,
  `days_this_season`, `current_season`.
- **Events** on the Home Assistant bus for automations.
- **Server diagnostics**: CPU, memory, disk, uptime, version, database status.

## Entities

### Per source (Deck, Front Yard, Guest Gate, …)

| Entity | Description |
|---|---|
| `sensor.<source>_last_species` | Last species heard. Attributes carry scientific name, confidence, rarity, and ready-to-use `thumbnail_url` / `spectrogram_url` / `clip_url`. |
| `sensor.<source>_last_confidence` | Confidence of that detection, as a percentage. |
| `sensor.<source>_last_heard` | Timestamp of that detection. |
| `binary_sensor.<source>_recently_active` | Detection within the last 30 minutes. |

### Server

| Entity | Description |
|---|---|
| `sensor.birdnet_go_species_today` | Distinct species today. |
| `sensor.birdnet_go_species_this_week` | Distinct species in the last 7 days. |
| `sensor.birdnet_go_species_all_time` | Distinct species ever recorded. |
| `sensor.birdnet_go_detections_today` | Total detections today. |
| `sensor.birdnet_go_new_species_today` | Species heard for the first time ever today; `species` attribute lists them. |
| `sensor.birdnet_go_last_detection` | Most recent detection across all sources. |
| `binary_sensor.birdnet_go_online` | Server reports healthy. |
| `binary_sensor.birdnet_go_detection_stream` | SSE stream is connected (diagnostic). |

Plus diagnostic sensors for CPU, memory, disk free, uptime, version and database status.

## Events

`birdnet_go_detection` fires on every confirmed detection above the configured
confidence floor. `birdnet_go_new_species` fires additionally when
`days_since_first_seen` is 0 — a species the server has never recorded before.

Both carry:

```yaml
source: Deck
source_key: deck
common_name: Swinhoe's White-eye
scientific_name: Zosterops simplex
species_code: swiwhe1
confidence: 0.82
detection_id: 42117
timestamp: "2026-09-01T11:01:18-07:00"
days_since_first_seen: 10
days_this_year: 10
days_this_season: 10
current_season: summer
```

### Example: announce a first-ever species

```yaml
automation:
  - alias: "New bird species"
    trigger:
      - platform: event
        event_type: birdnet_go_new_species
    action:
      - service: notify.mobile_app
        data:
          title: "New species: {{ trigger.event.data.common_name }}"
          message: >-
            Heard on {{ trigger.event.data.source }} at
            {{ (trigger.event.data.confidence | float * 100) | round(0) }}% confidence.
```

### Example: only react to high-confidence detections on one microphone

```yaml
automation:
  - alias: "Hawk on the deck"
    trigger:
      - platform: event
        event_type: birdnet_go_detection
    condition:
      - "{{ trigger.event.data.source_key == 'deck' }}"
      - "{{ trigger.event.data.confidence > 0.8 }}"
      - "{{ 'Hawk' in trigger.event.data.common_name }}"
    action:
      - service: light.turn_on
        target: { entity_id: light.deck }
```

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/whitesidest/birdnet-go-ha`, category **Integration**
3. Install **BirdNET-Go**, restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → *BirdNET-Go*

### Manual

Copy `custom_components/birdnet_go/` into your Home Assistant `config/custom_components/`
directory and restart.

## Configuration

Enter the host and port of the BirdNET-Go server (default `8080`). The API token
is **optional**: BirdNET-Go permits unauthenticated access from the local subnet
when `security.allowSubnetBypass` is enabled, which is the common self-hosted
setup. Supply a token only if Home Assistant reaches the server from outside that
subnet.

The options flow exposes a **minimum confidence** floor (0.0–1.0) applied to both
entity state and events.

## Notes

- `pending` SSE events are deliberately ignored. They represent in-progress
  candidates that churn between `active` and `rejected` as the model accumulates
  hits, and acting on them would make entities flap before anything is confirmed.
- If you previously used BirdNET-Go's MQTT discovery, turn it off in the server's
  settings and clear the retained `homeassistant/sensor/BirdNET-Go/...` topics,
  or the old broken sensors will linger alongside these.

## License

MIT
