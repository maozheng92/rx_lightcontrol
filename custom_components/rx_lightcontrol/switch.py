"""Per-device switch: advertise BLE on after the linked power entity turns on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL_DELI, MODEL_MESH
from .coordinator import RxLightClient


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: RxLightClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PowerBleFollowSwitch(client)])


class PowerBleFollowSwitch(SwitchEntity):
    """This lamp only: after its power entity turns on, also send BLE on."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "power_ble_follow"

    def __init__(self, client: RxLightClient) -> None:
        self._client = client
        uid = client.entry.unique_id or client.entry.entry_id
        self._attr_unique_id = f"{uid}_power_ble_follow"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            manufacturer=MANUFACTURER,
            model=MODEL_DELI if client.is_deli else MODEL_MESH,
            name=client.entry.title,
        )

    @property
    def is_on(self) -> bool:
        return self._client.power_ble_follow

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._client.async_set_power_ble_follow(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._client.async_set_power_ble_follow(False)
        self.async_write_ha_state()
