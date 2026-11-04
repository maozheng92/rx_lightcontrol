"""Listen for BLE advertisements and push decoded lamp state into entities.

DELI 照明灯 do not transmit status; we sniff the same HFKJ/DLKJ frames the
phone or a remote puts on air. Mesh lamps (0x0F71) often re-advertise DPS,
so those packets are treated as live state.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, PROTOCOL_MESH, signal_light_state
from .protocol import (
    DELI_DLKJ_UUID,
    DELI_HFKJ_UUID,
    MESH_COMPANY_ID,
    decode_ble_advertisement,
    format_rolling,
    slider_payload_to_percent,
)

_LOGGER = logging.getLogger(__name__)

SCANNER_KEY = "scanner"


def get_scanner(hass: HomeAssistant) -> Optional["AdvertisementScanner"]:
    return hass.data.get(DOMAIN, {}).get(SCANNER_KEY)


class AdvertisementScanner:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._unsub: list = []
        self._last_key: Optional[tuple] = None

    async def async_start(self) -> None:
        if self._unsub:
            return
        try:
            from homeassistant.components import bluetooth
        except ImportError:
            _LOGGER.debug("Bluetooth component not present")
            return
        if "bluetooth" not in self.hass.config.components:
            _LOGGER.info("Bluetooth is not set up; lamp state is restored from HA only")
            return
        matchers = (
            {"manufacturer_id": MESH_COMPANY_ID, "connectable": False},
            {"service_uuid": DELI_HFKJ_UUID, "connectable": False},
            {"service_uuid": DELI_DLKJ_UUID, "connectable": False},
        )
        for matcher in matchers:
            try:
                self._unsub.append(
                    bluetooth.async_register_callback(
                        self.hass,
                        self._async_on_ble,
                        matcher,
                        bluetooth.BluetoothScanningMode.ACTIVE,
                    )
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not register BLE matcher %s", matcher, exc_info=True)
        if self._unsub:
            _LOGGER.debug("Listening for 睿信 BLE advertisements (%s matchers)", len(self._unsub))

    async def async_stop(self) -> None:
        while self._unsub:
            unsub = self._unsub.pop()
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass

    @callback
    def _async_on_ble(self, service_info, change) -> None:  # noqa: ARG002
        raw = getattr(service_info, "raw", None) or getattr(
            getattr(service_info, "advertisement", None), "raw", None
        )
        manufacturer = dict(getattr(service_info, "manufacturer_data", None) or {})
        uuids = list(getattr(service_info, "service_uuids", None) or [])
        try:
            decoded = decode_ble_advertisement(raw, uuids, manufacturer)
        except Exception:  # noqa: BLE001
            return
        if not decoded:
            return
        family = decoded.get("family")
        if family == "deli":
            key = ("deli", decoded["rolling"], decoded["dest_id"], decoded["pid"], decoded["cmd"])
            if key == self._last_key:
                return
            self._last_key = key
            self._dispatch_deli(decoded)
            return
        if family == "mesh":
            key = ("mesh", decoded["dev_id"], decoded["pid"], decoded["power"], decoded["brightness"])
            if key == self._last_key:
                return
            self._last_key = key
            self._dispatch_mesh(decoded)

    def _clients(self) -> list:
        data = self.hass.data.get(DOMAIN, {})
        from .coordinator import RxLightClient

        return [value for value in data.values() if isinstance(value, RxLightClient)]

    def _dispatch_deli(self, decoded: dict[str, Any]) -> None:
        cmd = decoded["cmd"]
        if cmd in (0xB0, 0xB4):
            return
        rolling = bytes(decoded["rolling"])
        dest = int(decoded["dest_id"])
        payload = decoded["payload"]
        state: dict[str, Any] = {"source": "ble"}
        if cmd == 0xB3:
            state["is_on"] = True
        elif cmd == 0xB2:
            state["is_on"] = False
        elif cmd == 0xB5:
            state["is_on"] = True
            state["brightness_percent"] = slider_payload_to_percent(payload)
        elif cmd == 0xB7:
            state["is_on"] = True
            state["cct_percent"] = slider_payload_to_percent(payload)
        elif cmd == 0xA1:
            state["is_on"] = True
            state["effect"] = "小夜灯"
        elif cmd == 0xA7 and payload:
            scene = {0xA1: "温馨", 0xA3: "阅读", 0xA4: "会客", 0xC1: "日光", 0xC2: "月光", 0xC4: "娱乐"}
            state["is_on"] = True
            if payload[0] in scene:
                state["effect"] = scene[payload[0]]
            elif payload[0] in (1, 2, 3):
                state["effect"] = f"分段{payload[0]}"
        else:
            return
        for client in self._clients():
            if not client.is_deli:
                continue
            if bytes(client.rolling) != rolling:
                continue
            if dest not in (client.dest_id, 255):
                continue
            async_dispatcher_send(self.hass, signal_light_state(client.entry.entry_id), state)
            _LOGGER.debug(
                "DELI state from BLE cmd=0x%02X dest=%s rolling=%s -> %s",
                cmd,
                dest,
                format_rolling(rolling),
                client.entry.entry_id,
            )

    def _dispatch_mesh(self, decoded: dict[str, Any]) -> None:
        want = decoded["dev_id"]
        state = {
            "source": "ble",
            "is_on": bool(decoded["power"]),
            "brightness_percent": int(decoded["brightness"]),
            "cct_percent": int(decoded["temperature"]),
            "rgb_color": (int(decoded["red"]), int(decoded["green"]), int(decoded["blue"])),
        }
        for client in self._clients():
            if client.protocol != PROTOCOL_MESH:
                continue
            if client.dev_id.upper()[:6] != want[:6]:
                continue
            async_dispatcher_send(self.hass, signal_light_state(client.entry.entry_id), state)


async def async_setup_scanner(hass: HomeAssistant) -> AdvertisementScanner:
    domain = hass.data.setdefault(DOMAIN, {})
    scanner = domain.get(SCANNER_KEY)
    if scanner is None:
        scanner = AdvertisementScanner(hass)
        domain[SCANNER_KEY] = scanner
        await scanner.async_start()
    return scanner


async def async_maybe_stop_scanner(hass: HomeAssistant) -> None:
    from .coordinator import RxLightClient

    domain = hass.data.get(DOMAIN, {})
    if any(isinstance(value, RxLightClient) for value in domain.values()):
        return
    scanner = domain.pop(SCANNER_KEY, None)
    if scanner is not None:
        await scanner.async_stop()
