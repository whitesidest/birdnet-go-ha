"""Config flow for the BirdNET-Go integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BirdNetApiError, BirdNetAuthError, BirdNetClient
from .const import (
    CONF_HOST,
    CONF_MIN_CONFIDENCE,
    CONF_PORT,
    CONF_SSL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_PORT,
    DEFAULT_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_SSL, default=DEFAULT_SSL): bool,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        # Optional: BirdNET-Go allows unauthenticated LAN access when
        # security.allowSubnetBypass is on, which is the usual setup.
        vol.Optional(CONF_TOKEN, default=""): str,
    }
)


class BirdNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BirdNET-Go."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            scheme = "https" if user_input.get(CONF_SSL) else "http"
            base = f"{scheme}://{host}:{port}"

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = BirdNetClient(
                session,
                base,
                token=user_input.get(CONF_TOKEN) or None,
                verify_ssl=user_input.get(CONF_VERIFY_SSL, True),
            )
            try:
                health = await client.async_probe()
            except BirdNetAuthError:
                errors["base"] = "invalid_auth"
            except BirdNetApiError:
                errors["base"] = "cannot_connect"
            else:
                title = "BirdNET-Go"
                try:
                    info = await client.system_info()
                    if hostname := info.get("hostname"):
                        title = f"BirdNET-Go ({hostname})"
                except BirdNetApiError:
                    _LOGGER.debug("system info unavailable during setup")
                data = {**user_input, CONF_HOST: host}
                if not data.get(CONF_TOKEN):
                    data.pop(CONF_TOKEN, None)
                _LOGGER.debug("BirdNET-Go %s reachable", health.get("version"))
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> BirdNetOptionsFlow:
        return BirdNetOptionsFlow()


class BirdNetOptionsFlow(OptionsFlow):
    """Options: currently just the confidence floor for entities and events."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_MIN_CONFIDENCE, default=current): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
