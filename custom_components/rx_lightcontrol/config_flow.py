"""UI config flow: pick protocol, identity, and how to put advertisements on air."""

from __future__ import annotations

from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er, selector

from .advertiser import AdvertiseError, list_hci_adapters
from .const import (
    CONF_ADAPTER,
    CONF_DEST_ID,
    CONF_DEV_ID,
    CONF_INTERVAL_MS,
    CONF_MAX_KELVIN,
    CONF_MIN_KELVIN,
    CONF_MQTT_TOPIC,
    CONF_PAIR_NOW,
    CONF_POWER_BLE_FOLLOW,
    CONF_POWER_ENTITY,
    CONF_UNPAIR_NOW,
    CONF_PROTOCOL,
    CONF_REPEAT,
    CONF_ROLLING,
    CONF_SETUP_MODE,
    CONF_TRANSMIT,
    DEFAULT_DEST_ID,
    DEFAULT_INTERVAL_MS,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    DEFAULT_MQTT_TOPIC,
    DEFAULT_POWER_BLE_FOLLOW,
    DEFAULT_REPEAT,
    DOMAIN,
    PROTOCOL_DELI,
    PROTOCOL_MESH,
    SETUP_ALREADY_PAIRED,
    SETUP_PAIR_NEW,
    TRANSMIT_LOCAL,
    TRANSMIT_MQTT,
)
from .pairing import (
    async_advertise_pair,
    async_advertise_unpair,
    async_remember_rolling,
    async_shared_rolling,
    next_dest_id_for_hass,
)
from .protocol import format_rolling, parse_rolling

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
_SETUP_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value=SETUP_PAIR_NEW, label="新灯对码（灯进学习态）"),
            selector.SelectOptionDict(
                value=SETUP_ALREADY_PAIRED, label="已经对过码（填写滚码）"
            ),
        ],
        mode=selector.SelectSelectorMode.LIST,
    )
)
_PAIR_RESULT_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            selector.SelectOptionDict(value="success", label="灯有反应，完成添加"),
            selector.SelectOptionDict(value="retry", label="没反应，再发一次对码"),
        ],
        mode=selector.SelectSelectorMode.LIST,
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


class RxLightControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Add a 睿信 lamp by pairing or by pasting a sniffed rolling code."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._pair_task: Any = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlowHandler:
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_PROTOCOL] == PROTOCOL_DELI:
                return await self.async_step_deli_mode()
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

    async def async_step_deli_mode(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            self._data[CONF_SETUP_MODE] = user_input[CONF_SETUP_MODE]
            if user_input[CONF_SETUP_MODE] == SETUP_ALREADY_PAIRED:
                return await self.async_step_deli()
            rolling = await async_shared_rolling(self.hass)
            self._data[CONF_ROLLING] = rolling
            self._data[CONF_DEST_ID] = next_dest_id_for_hass(self.hass, rolling)
            return await self.async_step_deli_pair_info()
        return self.async_show_form(
            step_id="deli_mode",
            data_schema=vol.Schema(
                {vol.Required(CONF_SETUP_MODE, default=SETUP_PAIR_NEW): _SETUP_SELECTOR}
            ),
        )

    async def async_step_deli_pair_info(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                rolling = format_rolling(parse_rolling(str(user_input[CONF_ROLLING])))
            except ValueError:
                errors[CONF_ROLLING] = "invalid_rolling"
            else:
                self._data[CONF_ROLLING] = rolling
                self._data[CONF_DEST_ID] = int(user_input[CONF_DEST_ID])
                await async_remember_rolling(self.hass, rolling)
                return await self._async_next_after_identity()
        return self.async_show_form(
            step_id="deli_pair_info",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROLLING, default=self._data.get(CONF_ROLLING, "")): str,
                    vol.Required(
                        CONF_DEST_ID, default=int(self._data.get(CONF_DEST_ID, DEFAULT_DEST_ID))
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
                }
            ),
            errors=errors,
            description_placeholders={
                "rolling": str(self._data.get(CONF_ROLLING, "")),
                "dest_id": str(self._data.get(CONF_DEST_ID, DEFAULT_DEST_ID)),
            },
        )

    async def async_step_deli(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            rolling_text = str(user_input.get(CONF_ROLLING) or "").strip()
            try:
                rolling = format_rolling(parse_rolling(rolling_text))
            except ValueError:
                errors[CONF_ROLLING] = "invalid_rolling"
            else:
                self._data[CONF_ROLLING] = rolling
                self._data[CONF_DEST_ID] = int(user_input[CONF_DEST_ID])
                await async_remember_rolling(self.hass, rolling)
                return await self._async_next_after_identity()
        suggested = self._data.get(CONF_ROLLING) or await async_shared_rolling(self.hass)
        return self.async_show_form(
            step_id="deli",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROLLING, default=suggested): str,
                    vol.Required(
                        CONF_DEST_ID,
                        default=next_dest_id_for_hass(self.hass, suggested) if suggested else DEFAULT_DEST_ID,
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
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

    async def _async_unique(self) -> None:
        protocol = self._data[CONF_PROTOCOL]
        if protocol == PROTOCOL_DELI:
            unique = f"deli-{self._data[CONF_ROLLING]}-{self._data[CONF_DEST_ID]}"
        else:
            unique = f"mesh-{self._data[CONF_DEV_ID]}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

    async def _async_finish(self) -> FlowResult:
        await self._async_unique()
        if (
            self._data.get(CONF_PROTOCOL) == PROTOCOL_DELI
            and self._data.get(CONF_SETUP_MODE) == SETUP_PAIR_NEW
        ):
            return await self.async_step_pair()
        return self._create()

    async def async_step_pair(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(async_advertise_pair(self.hass, self._data))
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="sending_pair",
                progress_task=self._pair_task,
                description_placeholders={
                    "rolling": str(self._data.get(CONF_ROLLING, "")),
                    "dest_id": str(self._data.get(CONF_DEST_ID, "")),
                },
            )
        error = False
        try:
            await self._pair_task
        except AdvertiseError:
            error = True
        except Exception:  # noqa: BLE001
            error = True
        self._pair_task = None
        if error:
            return self.async_show_progress_done(next_step_id="pair_failed")
        return self.async_show_progress_done(next_step_id="pair_confirm")

    async def async_step_pair_confirm(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input.get("result") == "retry":
                return await self.async_step_pair()
            return self._create()
        return self.async_show_form(
            step_id="pair_confirm",
            data_schema=vol.Schema({vol.Required("result", default="success"): _PAIR_RESULT_SELECTOR}),
            description_placeholders={
                "rolling": str(self._data.get(CONF_ROLLING, "")),
                "dest_id": str(self._data.get(CONF_DEST_ID, "")),
            },
        )

    async def async_step_pair_failed(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input.get("retry"):
                return await self.async_step_pair()
            return self._create()
        return self.async_show_form(
            step_id="pair_failed",
            data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            errors={"base": "advertise_failed"},
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
        current = {**self._entry.data, **self._entry.options}
        if user_input is not None:
            unpair_now = bool(user_input.pop(CONF_UNPAIR_NOW, False))
            pair_now = bool(user_input.pop(CONF_PAIR_NOW, False))
            power = str(user_input.get(CONF_POWER_ENTITY) or "").strip()
            if power:
                user_input[CONF_POWER_ENTITY] = power
            else:
                user_input.pop(CONF_POWER_ENTITY, None)
            payload = {**current, **user_input}
            if unpair_now and current.get(CONF_PROTOCOL) == PROTOCOL_DELI:
                try:
                    await async_advertise_unpair(self.hass, payload)
                except AdvertiseError:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=self._schema(current),
                        errors={"base": "advertise_failed"},
                    )
                self.hass.async_create_task(
                    self.hass.config_entries.async_remove(self._entry.entry_id)
                )
                return self.async_create_entry(title="", data=user_input)
            if pair_now and current.get(CONF_PROTOCOL) == PROTOCOL_DELI:
                try:
                    await async_advertise_pair(self.hass, payload)
                except AdvertiseError:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=self._schema(current),
                        errors={"base": "advertise_failed"},
                    )
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=self._schema(current))

    def _schema(self, current: dict[str, Any]) -> vol.Schema:
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
            (
                vol.Optional(
                    CONF_POWER_ENTITY,
                    description={"suggested_value": current[CONF_POWER_ENTITY]},
                )
                if current.get(CONF_POWER_ENTITY)
                else vol.Optional(CONF_POWER_ENTITY)
            ): self._power_selector(),
            vol.Optional(
                CONF_POWER_BLE_FOLLOW,
                default=bool(current.get(CONF_POWER_BLE_FOLLOW, DEFAULT_POWER_BLE_FOLLOW)),
            ): bool,
        }
        if current.get(CONF_PROTOCOL) == PROTOCOL_DELI:
            schema[vol.Required(CONF_DEST_ID, default=int(current.get(CONF_DEST_ID, 1)))] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=255)
            )
            schema[vol.Optional(CONF_PAIR_NOW, default=False)] = bool
            schema[vol.Optional(CONF_UNPAIR_NOW, default=False)] = bool
        return vol.Schema(schema)

    def _power_selector(self) -> selector.EntitySelector:
        registry = er.async_get(self.hass)
        exclude = [
            item.entity_id
            for item in er.async_entries_for_config_entry(registry, self._entry.entry_id)
        ]
        return selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["light", "switch"],
                exclude_entities=exclude,
                multiple=False,
            )
        )
