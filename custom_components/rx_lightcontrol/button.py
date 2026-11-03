"""Pair / night-light buttons for DELI 照明灯."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL_DELI
from .coordinator import RxLightClient


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: RxLightClient = hass.data[DOMAIN][entry.entry_id]
    if not client.is_deli:
        return
    uid = client.entry.unique_id or client.entry.entry_id
    device = DeviceInfo(
        identifiers={(DOMAIN, uid)},
        manufacturer=MANUFACTURER,
        model=MODEL_DELI,
        name=client.entry.title,
    )
    async_add_entities(
        [
            RxCommandButton(client, device, uid, "pair", "pair", "pair"),
            RxCommandButton(client, device, uid, "night", "night", "night"),
        ]
    )


class RxCommandButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        client: RxLightClient,
        device: DeviceInfo,
        uid: str,
        key: str,
        command: str,
        translation_key: str,
    ) -> None:
        self._client = client
        self._command = command
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_translation_key = translation_key
        self._attr_device_info = device

    async def async_press(self) -> None:
        await self._client.async_send_deli(self._command)
