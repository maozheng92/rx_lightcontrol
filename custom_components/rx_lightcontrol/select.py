"""Per-device power-entity picker (config category)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED

from .const import (
    DOMAIN,
    MANUFACTURER,
    MODEL_DELI,
    MODEL_MESH,
    POWER_ENTITY_BLE,
)
from .coordinator import RxLightClient


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: RxLightClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PowerEntitySelect(client)])


def _candidates(hass: HomeAssistant) -> list[str]:
    registry = er.async_get(hass)
    found: list[str] = []
    for state in hass.states.async_all(["light", "switch"]):
        item = registry.async_get(state.entity_id)
        if item is not None and item.platform == DOMAIN:
            continue
        found.append(state.entity_id)
    return sorted(found)


class PowerEntitySelect(SelectEntity):
    """Choose BLE on/off or a wall light/switch for this lamp only."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "power_entity"

    def __init__(self, client: RxLightClient) -> None:
        self._client = client
        uid = client.entry.unique_id or client.entry.entry_id
        self._attr_unique_id = f"{uid}_power_entity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            manufacturer=MANUFACTURER,
            model=MODEL_DELI if client.is_deli else MODEL_MESH,
            name=client.entry.title,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, self._async_registry)
        )

    @callback
    def _async_registry(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        choices = [POWER_ENTITY_BLE]
        current = self._client.power_entity
        if current:
            choices.append(current)
        for entity_id in _candidates(self.hass):
            if entity_id not in choices:
                choices.append(entity_id)
        return choices

    @property
    def current_option(self) -> str:
        return self._client.power_entity or POWER_ENTITY_BLE

    async def async_select_option(self, option: str) -> None:
        entity_id = None if option == POWER_ENTITY_BLE else option
        self._client.async_set_power_entity(entity_id)
