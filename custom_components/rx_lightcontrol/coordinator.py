"""Per-config-entry client that encodes commands and bursts BLE advertisements."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Sequence

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .advertiser import AdvertiseError, BleAdvertiser
from .const import (
    CONF_ADAPTER,
    CONF_DEST_ID,
    CONF_DEV_ID,
    CONF_INTERVAL_MS,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_MQTT_TOPIC,
    CONF_PROTOCOL,
    CONF_REPEAT,
    CONF_ROLLING,
    CONF_TRANSMIT,
    DEFAULT_INTERVAL_MS,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_REPEAT,
    DELI_PRESETS,
    DOMAIN,
    PROTOCOL_DELI,
    PROTOCOL_MESH,
    TRANSMIT_MQTT,
)
from .protocol import (
    brightness_slider_payload,
    deli_air_frames,
    encode_mesh_light,
    mesh_air_frame,
    parse_rolling,
)

_PID_STORE_DELAY = 2.0


class PidCounter:
    """Phone-wide pid 0..255, shared by every light on this HA instance."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, 1, f"{DOMAIN}_pid")
        self._pid = 0
        self._lock = asyncio.Lock()
        self._hass = hass
        self._save_handle: Optional[asyncio.TimerHandle] = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._pid = int(data.get("pid", 0)) & 0xFF
        self._loaded = True

    async def async_next(self) -> int:
        async with self._lock:
            self._pid = (self._pid + 1) & 0xFF
            self._schedule_save()
            return self._pid

    def _schedule_save(self) -> None:
        if self._save_handle:
            self._save_handle.cancel()
        loop = self._hass.loop
        self._save_handle = loop.call_later(
            _PID_STORE_DELAY, lambda: self._hass.async_create_task(self._store.async_save({"pid": self._pid}))
        )


def get_pid_counter(hass: HomeAssistant) -> PidCounter:
    domain = hass.data.setdefault(DOMAIN, {})
    counter = domain.get("pid")
    if counter is None:
        counter = PidCounter(hass)
        domain["pid"] = counter
    return counter


class RxLightClient:
    """Encode + advertise for one config entry."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.advertiser: Optional[BleAdvertiser] = None
        self._mesh_power = 0
        self._mesh_brightness = 100
        self._mesh_temperature = 100
        self._mesh_aux = 0
        self._mesh_rgb = (52, 138, 255)

    @property
    def protocol(self) -> str:
        return self.entry.data[CONF_PROTOCOL]

    @property
    def is_deli(self) -> bool:
        return self.protocol == PROTOCOL_DELI

    @property
    def rolling(self) -> bytes:
        return parse_rolling(self.entry.data.get(CONF_ROLLING, "00000000"))

    @property
    def dest_id(self) -> int:
        return int(self._cfg(CONF_DEST_ID, 1)) & 0xFF

    @property
    def dev_id(self) -> str:
        return str(self.entry.data.get(CONF_DEV_ID, "000000"))

    @property
    def min_kelvin(self) -> int:
        return int(self.entry.options.get(CONF_MIN_KELVIN, self.entry.data.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN)))

    @property
    def max_kelvin(self) -> int:
        return int(self.entry.options.get(CONF_MAX_KELVIN, self.entry.data.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN)))

    @property
    def repeat(self) -> int:
        return int(self.entry.options.get(CONF_REPEAT, self.entry.data.get(CONF_REPEAT, DEFAULT_REPEAT)))

    @property
    def interval_ms(self) -> int:
        return int(self.entry.options.get(CONF_INTERVAL_MS, self.entry.data.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS)))

    def _cfg(self, key: str, default: Any = None) -> Any:
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    async def async_setup(self) -> None:
        counter = get_pid_counter(self.hass)
        await counter.async_load()

        transmit = self._cfg(CONF_TRANSMIT, "local")
        mqtt_publish = None
        if transmit == TRANSMIT_MQTT:
            async def _publish(topic: str, payload: str) -> None:
                from homeassistant.components import mqtt

                await mqtt.async_publish(self.hass, topic, payload, 0, False)

            mqtt_publish = _publish

        self.advertiser = BleAdvertiser(
            transmit=transmit,
            adapter=str(self._cfg(CONF_ADAPTER, "hci0") or "hci0"),
            mqtt_topic=str(self._cfg(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC) or DEFAULT_MQTT_TOPIC),
            mqtt_publish=mqtt_publish,
        )

    async def async_send_deli(
        self,
        name: str,
        payload: Sequence[int] = (0, 0, 0),
        *,
        group: bool = False,
    ) -> None:
        if not self.is_deli:
            raise HomeAssistantError("This device is not a DELI 照明灯")
        pid = await get_pid_counter(self.hass).async_next()
        timestamp = int(time.time() * 1000) & 0xFFFF
        frames = deli_air_frames(
            name,
            self.rolling,
            self.dest_id,
            pid,
            payload,
            timestamp=timestamp,
            group=group,
        )
        await self._async_burst(frames)

    async def async_send_preset(self, preset: str) -> None:
        bright, cct = DELI_PRESETS[preset]
        await self.async_send_deli("brightness", _preset_level_payload(*bright))
        await asyncio.sleep(0.5)
        await self.async_send_deli("cct", _preset_level_payload(*cct))

    async def async_send_mesh(self, **dps_kwargs: Any) -> None:
        if self.protocol != PROTOCOL_MESH:
            raise HomeAssistantError("This device is not a mesh light")
        if "power" in dps_kwargs:
            self._mesh_power = int(dps_kwargs["power"])
        if "brightness" in dps_kwargs:
            self._mesh_brightness = int(dps_kwargs["brightness"])
        if "temperature" in dps_kwargs:
            self._mesh_temperature = int(dps_kwargs["temperature"])
        if "auxiliary" in dps_kwargs:
            self._mesh_aux = int(dps_kwargs["auxiliary"])
        if "red" in dps_kwargs:
            self._mesh_rgb = (
                int(dps_kwargs["red"]),
                int(dps_kwargs.get("green", self._mesh_rgb[1])),
                int(dps_kwargs.get("blue", self._mesh_rgb[2])),
            )
        pid = await get_pid_counter(self.hass).async_next()
        frame26 = encode_mesh_light(
            self.dev_id,
            pid=pid,
            power=self._mesh_power,
            brightness=self._mesh_brightness,
            temperature=self._mesh_temperature,
            auxiliary=self._mesh_aux,
            red=self._mesh_rgb[0],
            green=self._mesh_rgb[1],
            blue=self._mesh_rgb[2],
        )
        await self._async_burst((mesh_air_frame(frame26),))

    async def _async_burst(self, frames: Sequence[bytes]) -> None:
        if self.advertiser is None:
            raise HomeAssistantError("Advertiser is not set up")
        try:
            await self.advertiser.async_burst(list(frames), self.interval_ms, self.repeat)
        except AdvertiseError as err:
            raise HomeAssistantError(str(err)) from err


def _preset_level_payload(percent: int, use_slider: bool) -> tuple[int, int, int]:
    if use_slider:
        return brightness_slider_payload(percent)
    return (0, 0, percent & 0xFF)
