"""Light entities for DELI 照明灯 and mesh type-2001 lamps."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    DELI_EFFECTS,
    DELI_PRESETS,
    DOMAIN,
    EFFECT_OFF,
    MANUFACTURER,
    MODEL_DELI,
    MODEL_MESH,
    POWER_SETTLE_SECONDS,
    signal_light_state,
)
from .coordinator import RxLightClient
from .protocol import (
    brightness_slider_payload,
    cct_slider_payload,
    ha_brightness_to_percent,
    kelvin_to_percent,
    percent_to_ha_brightness,
    percent_to_kelvin,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    client: RxLightClient = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RxLight(client)])


class RxLight(RestoreEntity, LightEntity):
    """BLE advertise light. State is restored, then updated from sniffed ads."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_translation_key = "light"

    def __init__(self, client: RxLightClient) -> None:
        self._client = client
        self._attr_unique_id = client.entry.unique_id or client.entry.entry_id
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_color_temp_kelvin = client.max_kelvin
        self._attr_min_color_temp_kelvin = client.min_kelvin
        self._attr_max_color_temp_kelvin = client.max_kelvin
        self._attr_effect = EFFECT_OFF
        self._state_source = "unknown"
        self._last_seen: datetime | None = None
        self._syncing_power = False
        if client.is_deli:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [EFFECT_OFF, *DELI_EFFECTS, *DELI_PRESETS]
        else:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_rgb_color = (52, 138, 255)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            manufacturer=MANUFACTURER,
            model=MODEL_DELI if client.is_deli else MODEL_MESH,
            name=client.entry.title,
        )

    @property
    def assumed_state(self) -> bool:
        if self._client.power_entity:
            return False
        return self._state_source != "ble"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
            brightness = last.attributes.get("brightness")
            if brightness is not None:
                self._attr_brightness = int(brightness)
            kelvin = last.attributes.get("color_temp_kelvin")
            if kelvin is not None:
                self._attr_color_temp_kelvin = int(kelvin)
            rgb = last.attributes.get("rgb_color")
            if rgb is not None:
                self._attr_rgb_color = tuple(int(v) for v in rgb)  # type: ignore[assignment]
            effect = last.attributes.get("effect")
            if effect:
                self._attr_effect = effect
            self._state_source = "restored"
        power_entity = self._client.power_entity
        if power_entity:
            self._apply_power_state(self.hass.states.get(power_entity))
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [power_entity], self._async_power_entity_changed
                )
            )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_light_state(self._client.entry.entry_id),
                self._async_apply_remote,
            )
        )

    @callback
    def _async_power_entity_changed(self, event: Event) -> None:
        if self._syncing_power:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        self._apply_power_state(new_state)
        self.async_write_ha_state()
        if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        was_on = old_state.state == STATE_ON
        now_on = bool(self._attr_is_on)
        if now_on and not was_on and self._client.power_ble_follow:
            self.hass.async_create_task(self._async_ble_switch(True))

    def _apply_power_state(self, state) -> None:
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        self._attr_is_on = state.state == STATE_ON
        self._state_source = "power_entity"
        if not self._attr_is_on:
            self._attr_effect = EFFECT_OFF

    def _power_target_ok(self, entity_id: str) -> bool:
        if entity_id == self.entity_id:
            return False
        registry = er.async_get(self.hass)
        item = registry.async_get(entity_id)
        if item is not None and item.platform == DOMAIN:
            return False
        return True

    async def _async_set_power_entity(self, on: bool) -> bool:
        """Drive the linked light/switch. Return True if it actually changed."""
        entity_id = self._client.power_entity
        if not entity_id or not self._power_target_ok(entity_id):
            return False
        current = self.hass.states.get(entity_id)
        currently_on = current is not None and current.state == STATE_ON
        if currently_on == on:
            return False
        domain = entity_id.split(".", 1)[0]
        self._syncing_power = True
        try:
            await self.hass.services.async_call(
                domain,
                SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
        finally:
            self._syncing_power = False
        return True

    async def _async_ble_switch(self, on: bool) -> None:
        if self._client.is_deli:
            await self._client.async_send_deli("on" if on else "off")
        else:
            await self._client.async_send_mesh(power=1 if on else 0)

    async def _async_ensure_powered(self) -> None:
        changed = await self._async_set_power_entity(True)
        if changed:
            await asyncio.sleep(POWER_SETTLE_SECONDS)

    @callback
    def _async_apply_remote(self, state: dict[str, Any]) -> None:
        if self._client.power_entity:
            state = {key: value for key, value in state.items() if key != "is_on"}
            if not any(
                key in state
                for key in ("brightness_percent", "cct_percent", "rgb_color", "effect")
            ):
                return
        if "is_on" in state:
            self._attr_is_on = bool(state["is_on"])
        if "brightness_percent" in state:
            self._attr_brightness = percent_to_ha_brightness(int(state["brightness_percent"]))
        if "cct_percent" in state:
            self._attr_color_temp_kelvin = percent_to_kelvin(
                int(state["cct_percent"]),
                self.min_color_temp_kelvin,
                self.max_color_temp_kelvin,
            )
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if "rgb_color" in state:
            self._attr_rgb_color = tuple(int(v) for v in state["rgb_color"])  # type: ignore[assignment]
            if self._attr_supported_color_modes and ColorMode.RGB in self._attr_supported_color_modes:
                self._attr_color_mode = ColorMode.RGB
        if "effect" in state:
            self._attr_effect = state["effect"]
        elif "is_on" in state:
            self._attr_effect = EFFECT_OFF
        self._state_source = str(state.get("source") or "ble")
        self._last_seen = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        effect = kwargs.get(ATTR_EFFECT)
        was_off = not self._attr_is_on

        if self._client.power_entity:
            await self._async_ensure_powered()

        if self._client.is_deli:
            if effect and effect != EFFECT_OFF:
                await self._async_apply_effect(effect)
                self._attr_is_on = True
                self._attr_effect = effect
                self._state_source = "power_entity" if self._client.power_entity else "command"
                self.async_write_ha_state()
                return
            send_ble_on = was_off or (brightness is None and kelvin is None)
            if send_ble_on and not self._client.power_entity:
                await self._client.async_send_deli("on")
            elif send_ble_on and self._client.power_ble_follow:
                await self._async_ble_switch(True)
            if brightness is not None:
                percent = ha_brightness_to_percent(brightness)
                await self._client.async_send_deli("brightness", brightness_slider_payload(percent))
                self._attr_brightness = percent_to_ha_brightness(percent)
            if kelvin is not None:
                percent = kelvin_to_percent(kelvin, self.min_color_temp_kelvin, self.max_color_temp_kelvin)
                await self._client.async_send_deli("cct", cct_slider_payload(percent))
                self._attr_color_temp_kelvin = percent_to_kelvin(
                    percent, self.min_color_temp_kelvin, self.max_color_temp_kelvin
                )
            self._attr_is_on = True
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_effect = EFFECT_OFF
            self._state_source = "power_entity" if self._client.power_entity else "command"
            self.async_write_ha_state()
            return

        if rgb is not None:
            self._attr_rgb_color = tuple(int(v) for v in rgb)  # type: ignore[assignment]
            self._attr_color_mode = ColorMode.RGB
        if kelvin is not None:
            percent = kelvin_to_percent(kelvin, self.min_color_temp_kelvin, self.max_color_temp_kelvin)
            self._attr_color_temp_kelvin = percent_to_kelvin(
                percent, self.min_color_temp_kelvin, self.max_color_temp_kelvin
            )
            self._client._mesh_temperature = percent
            self._attr_color_mode = ColorMode.COLOR_TEMP
        if brightness is not None:
            percent = ha_brightness_to_percent(brightness)
            self._attr_brightness = percent_to_ha_brightness(percent)
            self._client._mesh_brightness = percent
        kwargs_mesh: dict[str, Any] = {}
        if not self._client.power_entity or self._client.power_ble_follow:
            kwargs_mesh["power"] = 1
        if rgb is not None:
            kwargs_mesh.update(red=rgb[0], green=rgb[1], blue=rgb[2])
        await self._client.async_send_mesh(**kwargs_mesh)
        self._attr_is_on = True
        self._state_source = "power_entity" if self._client.power_entity else "command"
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._client.power_entity:
            await self._async_set_power_entity(False)
        elif self._client.is_deli:
            await self._client.async_send_deli("off")
        else:
            await self._client.async_send_mesh(power=0)
        self._attr_is_on = False
        self._attr_effect = EFFECT_OFF
        self._state_source = "power_entity" if self._client.power_entity else "command"
        self.async_write_ha_state()

    async def _async_apply_effect(self, effect: str) -> None:
        if effect in DELI_PRESETS:
            await self._client.async_send_preset(effect)
            percent_br, _ = DELI_PRESETS[effect][0]
            percent_ct, _ = DELI_PRESETS[effect][1]
            self._attr_brightness = percent_to_ha_brightness(percent_br)
            self._attr_color_temp_kelvin = percent_to_kelvin(
                percent_ct, self.min_color_temp_kelvin, self.max_color_temp_kelvin
            )
            return
        cmd, payload = DELI_EFFECTS[effect]
        await self._client.async_send_deli(cmd, payload)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "protocol": self._client.protocol,
            "state_source": self._state_source,
        }
        if self._client.power_entity:
            attrs["power_entity"] = self._client.power_entity
        if self._last_seen is not None:
            attrs["last_seen"] = self._last_seen.isoformat()
        if self._client.is_deli:
            attrs["rolling"] = self._client.entry.data.get("rolling")
            attrs["dest_id"] = self._client.dest_id
        else:
            attrs["dev_id"] = self._client.dev_id
        return attrs
