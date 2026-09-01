"""Async REST client for the BirdNET-Go v2 API."""
from __future__ import annotations

from typing import Any

import aiohttp


class BirdNetApiError(Exception):
    """Raised on non-2xx responses or transport errors."""


class BirdNetAuthError(BirdNetApiError):
    """Raised on 401/403 — a token is required or the one supplied is wrong."""


class BirdNetClient:
    """Thin wrapper over the endpoints this integration needs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    @property
    def verify_ssl(self) -> bool:
        return self._verify_ssl

    async def _get(self, path: str, **params: Any) -> Any:
        url = f"{self._base}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers,
                params={k: v for k, v in params.items() if v is not None} or None,
                ssl=None if self._verify_ssl else False,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status in (401, 403):
                    raise BirdNetAuthError(f"{resp.status} on {path}")
                if resp.status >= 400:
                    text = await resp.text()
                    raise BirdNetApiError(f"{resp.status} on {path}: {text[:200]}")
                return await resp.json()
        except aiohttp.ClientError as err:
            raise BirdNetApiError(f"{type(err).__name__} on {path}: {err}") from err
        except TimeoutError as err:
            raise BirdNetApiError(f"timeout on {path}") from err

    # --- endpoints -------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Server status, uptime and host resource usage."""
        return await self._get("/api/v2/health")

    async def system_info(self) -> dict[str, Any]:
        """Hostname, platform and CPU details — used for device metadata."""
        return await self._get("/api/v2/system/info")

    async def realtime_settings(self) -> dict[str, Any]:
        """Full realtime config; we read ``rtsp.streams`` for the source list."""
        return await self._get("/api/v2/settings/realtime")

    async def recent_detections(self) -> list[dict[str, Any]]:
        """The newest detections, richest representation available."""
        data = await self._get("/api/v2/detections/recent")
        return data if isinstance(data, list) else []

    async def species_daily(self) -> list[dict[str, Any]]:
        """Per-species counts for today, including 24-slot hourly histograms."""
        data = await self._get("/api/v2/analytics/species/daily")
        return data if isinstance(data, list) else []

    async def species_summary(self) -> list[dict[str, Any]]:
        """All-time per-species totals with first/last heard timestamps."""
        data = await self._get("/api/v2/analytics/species/summary")
        return data if isinstance(data, list) else []

    async def async_probe(self) -> dict[str, Any]:
        """Validate connectivity during the config flow."""
        return await self.health()
