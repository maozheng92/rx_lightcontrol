"""睿信灯控 — BLE advertise light control (no GATT)."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_COMMAND,
    ATTR_GROUP,
    ATTR_HOURS,
    ATTR_MINUTES,
    ATTR_P0,
    ATTR_P1,
    ATTR_P2,
    ATTR_REMOVE_ENTRY,
    ATTR_SECONDS,
    ATTR_TURN_ON,
    CONF_PROTOCOL,
    DOMAIN,
    PLATFORMS_DELI,
    PLATFORMS_MESH,
    PROTOCOL_DELI,
    SERVICE_GROUP,
    SERVICE_PAIR,
    SERVICE_SEND_COMMAND,
    SERVICE_TIMER,
    SERVICE_UNPAIR,
)
from .coordinator import RxLightClient
from .scanner import async_maybe_stop_scanner, async_setup_scanner


def _platforms(entry: ConfigEntry) -> tuple[str, ...]:
    if entry.data.get(CONF_PROTOCOL) == PROTOCOL_DELI:
        return PLATFORMS_DELI
    return PLATFORMS_MESH


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    client = RxLightClient(hass, entry)
    await client.async_setup()
    hass.data[DOMAIN][entry.entry_id] = client
    await async_setup_scanner(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(entry))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, _platforms(entry))
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        await async_maybe_stop_scanner(hass)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _clients(hass: HomeAssistant) -> list[RxLightClient]:
    data = hass.data.get(DOMAIN, {})
    return [value for value in data.values() if isinstance(value, RxLightClient)]


def _client_for_device(hass: HomeAssistant, device_id: str | None, entry_id: str | None) -> RxLightClient | None:
    domain = hass.data.get(DOMAIN, {})
    if entry_id and entry_id in domain and isinstance(domain[entry_id], RxLightClient):
        return domain[entry_id]
    if not device_id:
        return None
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return None
    for entry_id in device.config_entries:
        client = domain.get(entry_id)
        if isinstance(client, RxLightClient):
            return client
    return None


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        return

    async def _send_command(call: ServiceCall) -> None:
        client = _client_for_device(
            hass, call.data.get("device_id"), call.data.get("entry_id")
        )
        if client is None:
            targets = _clients(hass)
            if len(targets) == 1:
                client = targets[0]
        if client is None:
            raise ValueError("Specify a 睿信灯控 device")
        command = call.data[ATTR_COMMAND]
        payload = (
            int(call.data.get(ATTR_P0, 0)),
            int(call.data.get(ATTR_P1, 0)),
            int(call.data.get(ATTR_P2, 0)),
        )
        group = bool(call.data.get(ATTR_GROUP, False))
        if client.is_deli:
            await client.async_send_deli(command, payload, group=group)
        elif command in ("on", "off"):
            await client.async_send_mesh(power=1 if command == "on" else 0)
        else:
            raise ValueError(f"Mesh lights do not support command {command}")

    async def _timer(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data.get("device_id"), None) or (
            _clients(hass)[0] if len(_clients(hass)) == 1 else None
        )
        if client is None or not client.is_deli:
            raise ValueError("Timer is only available on 照明灯")
        hours = int(call.data.get(ATTR_HOURS, 0))
        minutes = int(call.data.get(ATTR_MINUTES, 0))
        seconds = int(call.data.get(ATTR_SECONDS, 0))
        await client.async_send_deli("timer", (hours, minutes, seconds))

    async def _group(call: ServiceCall) -> None:
        if ATTR_TURN_ON in call.data:
            on = bool(call.data[ATTR_TURN_ON])
        elif "on" in call.data:
            on = bool(call.data["on"])
        else:
            on = True
        seen: set[bytes] = set()
        for client in _clients(hass):
            if not client.is_deli:
                continue
            key = bytes(client.rolling)
            if key in seen:
                continue
            seen.add(key)
            await client.async_send_deli("on" if on else "off", group=True)
        if not seen:
            raise ValueError("No 照明灯 configured for group control")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        _send_command,
        schema=vol.Schema(
            {
                vol.Required(ATTR_COMMAND): cv.string,
                vol.Optional(ATTR_P0, default=0): vol.Coerce(int),
                vol.Optional(ATTR_P1, default=0): vol.Coerce(int),
                vol.Optional(ATTR_P2, default=0): vol.Coerce(int),
                vol.Optional(ATTR_GROUP, default=False): cv.boolean,
                vol.Optional("device_id"): cv.string,
                vol.Optional("entry_id"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TIMER,
        _timer,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_HOURS, default=0): vol.All(vol.Coerce(int), vol.Range(0, 23)),
                vol.Optional(ATTR_MINUTES, default=0): vol.All(vol.Coerce(int), vol.Range(0, 59)),
                vol.Optional(ATTR_SECONDS, default=0): vol.All(vol.Coerce(int), vol.Range(0, 59)),
                vol.Optional("device_id"): cv.string,
            }
        ),
    )
    async def _pair(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data.get("device_id"), call.data.get("entry_id"))
        if client is None:
            targets = [item for item in _clients(hass) if item.is_deli]
            if len(targets) == 1:
                client = targets[0]
        if client is None or not client.is_deli:
            raise ValueError("Specify a 照明灯 device to pair")
        await client.async_send_pair()

    async def _unpair(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data.get("device_id"), call.data.get("entry_id"))
        if client is None:
            targets = [item for item in _clients(hass) if item.is_deli]
            if len(targets) == 1:
                client = targets[0]
        if client is None or not client.is_deli:
            raise ValueError("Specify a 照明灯 device to unpair")
        await client.async_send_unpair(remove_entry=bool(call.data.get(ATTR_REMOVE_ENTRY, True)))

    hass.services.async_register(
        DOMAIN,
        SERVICE_GROUP,
        _group,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_TURN_ON, default=True): cv.boolean,
                vol.Optional("on"): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_PAIR,
        _pair,
        schema=vol.Schema(
            {
                vol.Optional("device_id"): cv.string,
                vol.Optional("entry_id"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNPAIR,
        _unpair,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_REMOVE_ENTRY, default=True): cv.boolean,
                vol.Optional("device_id"): cv.string,
                vol.Optional("entry_id"): cv.string,
            }
        ),
    )
