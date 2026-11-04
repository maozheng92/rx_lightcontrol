"""Diagnostics dump for a 睿信灯控 config entry."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import RxLightClient


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    client: RxLightClient | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "protocol": entry.data.get("protocol"),
        "repeat": getattr(client, "repeat", None),
        "interval_ms": getattr(client, "interval_ms", None),
        "transmit": getattr(getattr(client, "advertiser", None), "transmit", None),
        "adapter": getattr(getattr(client, "advertiser", None), "adapter", None),
        "scanner": "scanner" in hass.data.get(DOMAIN, {}),
    }
