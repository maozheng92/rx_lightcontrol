"""UI config flow: pick protocol, identity, and how to put advertisements on air."""

from __future__ import annotations

from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .advertiser import AdvertiseError, BleAdvertiser, list_hci_adapters
from .const import (
    CONF_ADAPTER,
    CONF_DEST_ID,
    CONF_DEV_ID,
    CONF_INTERVAL_MS,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_MQTT_TOPIC,
    CONF_PAIR_NOW,
    CONF_PROTOCOL,
    CONF_REPEAT,
    CONF_ROLLING,
    CONF_TRANSMIT,
    DEFAULT_DEST_ID,
    DEFAULT_INTERVAL_MS,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_REPEAT,
    DOMAIN,
    PROTOCOL_DELI,
    PROTOCOL_MESH,
    TRANSMIT_LOCAL,
    TRANSMIT_MQTT,
)
from .protocol import deli_air_frames, format_rolling, generate_rolling, parse_rolling

_PROTOCOL_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=PROTOCOL_DELI, label="照明灯 (DELI / 13 UUID)"),
            selector.SelectOptionDict(value=PROTOCOL_MESH, label="家庭 Mesh (company 0x0F71)"),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)
_TRANSMIT_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=TRANSMIT_LOCAL, label="本机蓝牙适配器"),
            selector.SelectOptionDict(value=TRANSMIT_MQTT, label="MQTT → 远程广播器"),
        ],
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


def _adapter_selector() -> selector.SelectSelector:
    options = [
        selector.SelectOptionDict(value=name, label=f"{name} ({addr})" if addr else name)
        for name, addr in list_hci_adapters()
    ]
    if not options:
        options = [selector.SelectOptionDict(value="hci0", label="hci0")]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
    )


async def _async_mqtt_publish(hass: HomeAssistant, topic: str, payload: str) -> None:
    from homeassistant.components import mqtt

    await mqtt.async_publish(hass, topic, payload, 0, False)


async def _async_pair_now(hass: HomeAssistant, data: dict[str, Any]) -> None:
    rolling = parse_rolling(data[CONF_ROLLING])
    dest = int(data[CONF_DEST_ID])
    frames = deli_air_frames("pair", rolling, dest, pid=1, timestamp=0)
    transmit = data.get(CONF_TRANSMIT, TRANSMIT_LOCAL)
    mqtt_publish = None
    if transmit == TRANSMIT_MQTT:
        mqtt_publish = lambda topic, payload: _async_mqtt_publish(hass, topic, payload)  # noqa: E731
    advertiser = BleAdvertiser(
        transmit=transmit,
        adapter=str(data.get(CONF_ADAPTER) or "hci0"),
        mqtt_topic=str(data.get(CONF_MQTT_TOPIC) or DEFAULT_MQTT_TOPIC),
        mqtt_publish=mqtt_publish,
    )
    await advertiser.async_burst(
        list(frames),
        int(data.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS)),
        int(data.get(CONF_REPEAT, DEFAULT_REPEAT)),
    )


class RxLightControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Add a 睿信 lamp by rolling code / mesh id."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlowHandler:
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_PROTOCOL] == PROTOCOL_DELI:
                return await self.async_step_deli()
            return await self.async_step_mesh()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="照明灯"): str,
                    vol.Required(CONF_PROTOCOL, default=PROTOCOL_DELI): _PROTOCOL_SELECTOR,
                    vol.Required(CONF_TRANSMIT, default=TRANSMIT_LOCAL): _TRANSMIT_SELECTOR,
                }
            ),
        )

    async def async_step_deli(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            rolling_text = str(user_input.get(CONF_ROLLING) or "").strip()
            try:
                rolling = parse_rolling(rolling_text) if rolling_text else generate_rolling()
            except ValueError:
                errors[CONF_ROLLING] = "invalid_rolling"
            else:
                self._data[CONF_ROLLING] = format_rolling(rolling)
                self._data[CONF_DEST_ID] = int(user_input[CONF_DEST_ID])
                self._data[CONF_PAIR_NOW] = bool(user_input.get(CONF_PAIR_NOW, True))
                return await self._async_next_after_identity()
        return self.async_show_form(
            step_id="deli",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ROLLING, default=self._data.get(CONF_ROLLING, "")): str,
                    vol.Required(CONF_DEST_ID, default=DEFAULT_DEST_ID): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=255)
                    ),
                    vol.Optional(CONF_PAIR_NOW, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_mesh(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            devid = "".join(ch for ch in str(user_input[CONF_DEV_ID]) if ch.isalnum())
            if len(devid) < 6:
                errors[CONF_DEV_ID] = "invalid_dev_id"
            else:
                self._data[CONF_DEV_ID] = devid[:6].upper()
                return await self._async_next_after_identity()
        return self.async_show_form(
            step_id="mesh",
            data_schema=vol.Schema({vol.Required(CONF_DEV_ID): str}),
            errors=errors,
        )

    async def _async_next_after_identity(self) -> FlowResult:
        if self._data.get(CONF_TRANSMIT) == TRANSMIT_MQTT:
            return await self.async_step_mqtt()
        return await self.async_step_adapter()

    async def async_step_adapter(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._data[CONF_ADAPTER] = user_input[CONF_ADAPTER]
            return await self._async_finish()
        return self.async_show_form(
            step_id="adapter",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADAPTER, default="hci0"): _adapter_selector()}
            ),
        )

    async def async_step_mqtt(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._data[CONF_MQTT_TOPIC] = user_input[CONF_MQTT_TOPIC]
            return await self._async_finish()
        return self.async_show_form(
            step_id="mqtt",
            data_schema=vol.Schema(
                {vol.Required(CONF_MQTT_TOPIC, default=DEFAULT_MQTT_TOPIC): str}
            ),
        )

    async def _async_finish(self) -> FlowResult:
        protocol = self._data[CONF_PROTOCOL]
        if protocol == PROTOCOL_DELI:
            unique = f"deli-{self._data[CONF_ROLLING]}-{self._data[CONF_DEST_ID]}"
        else:
            unique = f"mesh-{self._data[CONF_DEV_ID]}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if protocol == PROTOCOL_DELI and self._data.get(CONF_PAIR_NOW):
            try:
                await _async_pair_now(self.hass, self._data)
            except AdvertiseError:
                errors["base"] = "advertise_failed"
            except Exception:  # noqa: BLE001
                errors["base"] = "advertise_failed"
            if errors:
                # Stay on a confirmation step so the user can skip pairing.
                return await self.async_step_pair_retry(errors=errors)

        return self._create()

    async def async_step_pair_retry(
        self, user_input: Optional[dict[str, Any]] = None, errors: Optional[dict[str, str]] = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input.get("retry"):
                try:
                    await _async_pair_now(self.hass, self._data)
                except Exception:  # noqa: BLE001
                    return self.async_show_form(
                        step_id="pair_retry",
                        data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
                        errors={"base": "advertise_failed"},
                    )
            return self._create()
        return self.async_show_form(
            step_id="pair_retry",
            data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            errors=errors or {},
        )

    def _create(self) -> FlowResult:
        data = {
            CONF_PROTOCOL: self._data[CONF_PROTOCOL],
            CONF_TRANSMIT: self._data.get(CONF_TRANSMIT, TRANSMIT_LOCAL),
            CONF_ADAPTER: self._data.get(CONF_ADAPTER, "hci0"),
            CONF_MQTT_TOPIC: self._data.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC),
            CONF_REPEAT: DEFAULT_REPEAT,
            CONF_INTERVAL_MS: DEFAULT_INTERVAL_MS,
            CONF_MIN_KELVIN: DEFAULT_MIN_KELVIN,
            CONF_MAX_KELVIN: DEFAULT_MAX_KELVIN,
        }
        if data[CONF_PROTOCOL] == PROTOCOL_DELI:
            data[CONF_ROLLING] = self._data[CONF_ROLLING]
            data[CONF_DEST_ID] = self._data[CONF_DEST_ID]
        else:
            data[CONF_DEV_ID] = self._data[CONF_DEV_ID]
        return self.async_create_entry(title=self._data[CONF_NAME], data=data)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self._entry.data, **self._entry.options}
        schema: dict[Any, Any] = {
            vol.Required(CONF_TRANSMIT, default=current.get(CONF_TRANSMIT, TRANSMIT_LOCAL)): _TRANSMIT_SELECTOR,
            vol.Optional(CONF_ADAPTER, default=current.get(CONF_ADAPTER, "hci0")): str,
            vol.Optional(CONF_MQTT_TOPIC, default=current.get(CONF_MQTT_TOPIC, DEFAULT_MQTT_TOPIC)): str,
            vol.Required(CONF_REPEAT, default=int(current.get(CONF_REPEAT, DEFAULT_REPEAT))): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=20)
            ),
            vol.Required(
                CONF_INTERVAL_MS, default=int(current.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS))
            ): vol.All(vol.Coerce(int), vol.Range(min=20, max=1000)),
            vol.Required(
                CONF_MIN_KELVIN, default=int(current.get(CONF_MIN_KELVIN, DEFAULT_MIN_KELVIN))
            ): vol.All(vol.Coerce(int), vol.Range(min=1800, max=4000)),
            vol.Required(
                CONF_MAX_KELVIN, default=int(current.get(CONF_MAX_KELVIN, DEFAULT_MAX_KELVIN))
            ): vol.All(vol.Coerce(int), vol.Range(min=4000, max=7000)),
        }
        if current.get(CONF_PROTOCOL) == PROTOCOL_DELI:
            schema[vol.Required(CONF_DEST_ID, default=int(current.get(CONF_DEST_ID, 1)))] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=255)
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
