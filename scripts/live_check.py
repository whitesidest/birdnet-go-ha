#!/usr/bin/env python3
"""Smoke-test this integration's API and SSE layers against a real server.

Not part of the pytest suite (it needs a live BirdNET-Go). Run manually:

    python scripts/live_check.py birdnet.local 8080 [seconds]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))

import aiohttp  # noqa: E402

from birdnet_go.api import BirdNetClient  # noqa: E402
from birdnet_go.coordinator import parse_uptime, slugify_source  # noqa: E402
from birdnet_go.stream import BirdNetStream  # noqa: E402


async def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "birdnet.local"
    port = sys.argv[2] if len(sys.argv) > 2 else "8080"
    listen_s = int(sys.argv[3]) if len(sys.argv) > 3 else 45
    base = f"http://{host}:{port}"

    async with aiohttp.ClientSession() as session:
        client = BirdNetClient(session, base)

        print(f"== REST against {base} ==")
        health = await client.health()
        print(f"  health.status      : {health.get('status')}")
        print(f"  version            : {health.get('version')}")
        print(f"  uptime parsed      : {parse_uptime(health.get('uptime_seconds')):.0f}s")

        realtime = await client.realtime_settings()
        streams = (realtime.get("rtsp") or {}).get("streams") or []
        print(f"  sources discovered : {[s['name'] for s in streams]}")
        print(f"  source keys        : {[slugify_source(s['name']) for s in streams]}")

        recent = await client.recent_detections()
        print(f"  recent detections  : {len(recent)}")
        if recent:
            d = recent[0]
            src = d.get("source") or {}
            print(
                f"  newest             : {d.get('commonName')!r} on "
                f"{src.get('displayName')!r} @ {float(d.get('confidence', 0)) * 100:.0f}%"
            )

        daily = await client.species_daily()
        print(f"  species today      : {len([s for s in daily if s.get('count')])}")
        print(f"  detections today   : {sum(int(s.get('count') or 0) for s in daily)}")

        print(f"\n== SSE for {listen_s}s ==")
        got: list[dict] = []
        states: list[bool] = []
        stream = BirdNetStream(
            session, base, {"Accept": "application/json"},
            on_detection=got.append, on_state=states.append,
        )
        stream.start()
        await asyncio.sleep(listen_s)
        await stream.stop()

        print(f"  connect events     : {states}")
        print(f"  detections received: {len(got)}")
        for d in got:
            src = d.get("source")
            name = src.get("displayName") if isinstance(src, dict) else src
            print(f"    - {d.get('commonName')} on {name}")
        if not got:
            print("    (none in window — normal; birds are sporadic)")

        ok = health.get("status") == "healthy" and bool(streams) and states[:1] == [True]
        print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
