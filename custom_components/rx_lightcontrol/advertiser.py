"""Send exact BLE advertising packets (DELI 31-byte Tx_Payload / mesh manufacturer).

The lamp protocol is one-way advertise, not GATT. Home Assistant has no public
TX API, so this module:

1. MQTT — publish a burst JSON for a Pi/ESP32 advertiser
2. Raw HCI — write LE Set Advertising Data (exact bytes the phone sends)
3. BlueZ D-Bus — 13 service UUIDs (DELI) when HCI is unavailable
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

try:
    from .protocol import deli_service_uuids
except ImportError:  # tools/mqtt_ble_advertiser.py
    from protocol import deli_service_uuids

_LOGGER = logging.getLogger(__name__)

HCI_COMMAND_PKT = 0x01
OGF_LE_CTL = 0x08
OCF_LE_SET_ADV_PARAMETERS = 0x0006
OCF_LE_SET_ADV_DATA = 0x0008
OCF_LE_SET_ADV_ENABLE = 0x000A

# ADV_IND, 100 ms interval (units of 0.625 ms).
_ADV_INTERVAL = 0x00A0


class AdvertiseError(RuntimeError):
    """Raised when no advertiser could put a frame on air."""


def list_hci_adapters() -> List[tuple[str, str]]:
    """Return [(hciN, bdaddr), ...] from sysfs."""
    root = Path("/sys/class/bluetooth")
    if not root.exists():
        return []
    adapters: List[tuple[str, str]] = []
    for path in sorted(root.iterdir()):
        if not path.name.startswith("hci"):
            continue
        addr_file = path / "address"
        addr = addr_file.read_text().strip() if addr_file.exists() else ""
        adapters.append((path.name, addr))
    return adapters


def _dev_id(adapter: str) -> int:
    name = adapter.strip() or "hci0"
    if name.startswith("hci"):
        try:
            return int(name[3:] or "0")
        except ValueError:
            return 0
    return 0


def _hci_opcode(ogf: int, ocf: int) -> int:
    return ocf | (ogf << 10)


def _hci_cmd(sock: socket.socket, ogf: int, ocf: int, params: bytes) -> None:
    opcode = _hci_opcode(ogf, ocf)
    packet = bytes([HCI_COMMAND_PKT, opcode & 0xFF, (opcode >> 8) & 0xFF, len(params)]) + params
    sock.send(packet)


def _open_hci(dev_id: int) -> socket.socket:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)  # type: ignore[attr-defined]
    sock.bind((dev_id,))
    sock.settimeout(0.5)
    return sock


def _hci_set_params(sock: socket.socket) -> None:
    params = struct.pack(
        "<HHBBB6sBB",
        _ADV_INTERVAL,
        _ADV_INTERVAL,
        0x00,  # ADV_IND (connectable, matching the APK)
        0x00,  # own address public
        0x00,
        bytes(6),
        0x07,
        0x00,
    )
    _hci_cmd(sock, OGF_LE_CTL, OCF_LE_SET_ADV_PARAMETERS, params)


def _hci_set_data(sock: socket.socket, frame: bytes) -> None:
    if len(frame) > 31:
        raise AdvertiseError(f"advertising data too long: {len(frame)}")
    payload = bytes([len(frame)]) + frame + bytes(31 - len(frame))
    _hci_cmd(sock, OGF_LE_CTL, OCF_LE_SET_ADV_DATA, payload)


def _hci_enable(sock: socket.socket, enabled: bool) -> None:
    _hci_cmd(sock, OGF_LE_CTL, OCF_LE_SET_ADV_ENABLE, bytes([1 if enabled else 0]))


def hci_burst(frames: Sequence[bytes], interval_ms: int, repeat: int, adapter: str) -> None:
    """Blocking: air each frame for interval_ms, `repeat` times. Matches the phone queue."""
    sock = _open_hci(_dev_id(adapter))
    try:
        _hci_set_params(sock)
        interval = max(0.02, interval_ms / 1000.0)
        for _ in range(max(1, repeat)):
            for frame in frames:
                try:
                    _hci_enable(sock, False)
                except OSError:
                    pass
                _hci_set_data(sock, frame)
                _hci_enable(sock, True)
                time.sleep(interval)
        try:
            _hci_enable(sock, False)
        except OSError:
            pass
    finally:
        sock.close()


def burst_json(frames: Sequence[bytes], interval_ms: int, repeat: int) -> str:
    return json.dumps(
        {
            "v": 1,
            "interval_ms": int(interval_ms),
            "repeat": int(repeat),
            "frames": [bytes(frame).hex() for frame in frames],
        },
        separators=(",", ":"),
    )


class RecordingAdvertiser:
    """Test double that records on-air frames."""

    def __init__(self) -> None:
        self.bursts: list[dict] = []

    async def async_burst(
        self, frames: Sequence[bytes], interval_ms: int, repeat: int, adapter: str = ""
    ) -> None:
        self.bursts.append(
            {
                "frames": [bytes(f) for f in frames],
                "interval_ms": interval_ms,
                "repeat": repeat,
                "adapter": adapter,
            }
        )


async def async_bluez_burst(frames: Sequence[bytes], interval_ms: int, repeat: int, adapter: str) -> None:
    """Advertise DELI frames as 13×16-bit service UUIDs via BlueZ."""
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, dbus_property, method

    class _Advertisement(ServiceInterface):
        def __init__(self, path: str, uuids: list[str]) -> None:
            super().__init__("org.bluez.LEAdvertisement1")
            self.path = path
            self._uuids = uuids

        @dbus_property()
        def Type(self) -> "s":  # type: ignore[name-defined]
            return "peripheral"

        @Type.setter
        def Type(self, value: "s") -> None:  # type: ignore[name-defined]
            return

        @dbus_property()
        def ServiceUUIDs(self) -> "as":  # type: ignore[name-defined]
            return self._uuids

        @ServiceUUIDs.setter
        def ServiceUUIDs(self, value: "as") -> None:  # type: ignore[name-defined]
            self._uuids = list(value)

        @dbus_property()
        def Includes(self) -> "as":  # type: ignore[name-defined]
            return []

        @Includes.setter
        def Includes(self, value: "as") -> None:  # type: ignore[name-defined]
            return

        @method()
        def Release(self) -> None:
            return

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    adapter_path = f"/org/bluez/{adapter or 'hci0'}"
    interval = max(0.05, interval_ms / 1000.0)
    counter = 0
    try:
        for _ in range(max(1, repeat)):
            for frame in frames:
                if len(frame) != 31 or frame[4] != 0x03:
                    raise AdvertiseError("BlueZ UUID path only supports DELI 31-byte frames")
                uuids = deli_service_uuids(frame[5:])
                counter += 1
                path = f"/com/deep/rx_lightcontrol/ad{counter}"
                adv = _Advertisement(path, uuids)
                bus.export(path, adv)
                reply = await bus.call(
                    Message(
                        destination="org.bluez",
                        path=adapter_path,
                        interface="org.bluez.LEAdvertisingManager1",
                        member="RegisterAdvertisement",
                        signature="oa{sv}",
                        body=[path, {}],
                    )
                )
                if reply is None or reply.message_type == MessageType.ERROR:
                    err = reply.body if reply else "no reply"
                    raise AdvertiseError(f"BlueZ RegisterAdvertisement failed: {err}")
                await asyncio.sleep(interval)
                await bus.call(
                    Message(
                        destination="org.bluez",
                        path=adapter_path,
                        interface="org.bluez.LEAdvertisingManager1",
                        member="UnregisterAdvertisement",
                        signature="o",
                        body=[path],
                    )
                )
                bus.unexport(path)
    finally:
        bus.disconnect()


class BleAdvertiser:
    """Pick MQTT, HCI, or BlueZ and serialize bursts so UUID lists never interleave."""

    def __init__(
        self,
        *,
        transmit: str,
        adapter: str = "",
        mqtt_topic: str = "",
        mqtt_publish: Optional[Callable] = None,
    ) -> None:
        self.transmit = transmit
        self.adapter = adapter or "hci0"
        self.mqtt_topic = mqtt_topic
        self.mqtt_publish = mqtt_publish
        self._lock = asyncio.Lock()

    async def async_burst(self, frames: Sequence[bytes], interval_ms: int, repeat: int) -> None:
        if not frames:
            return
        async with self._lock:
            await self._async_burst_unlocked(frames, interval_ms, repeat)

    async def _async_burst_unlocked(
        self, frames: Sequence[bytes], interval_ms: int, repeat: int
    ) -> None:
        if self.transmit == "mqtt":
            if not self.mqtt_publish or not self.mqtt_topic:
                raise AdvertiseError("MQTT advertiser is not configured")
            payload = burst_json(frames, interval_ms, repeat)
            await self.mqtt_publish(self.mqtt_topic, payload)
            _LOGGER.debug("Published %s frames to %s", len(frames), self.mqtt_topic)
            return

        errors: list[str] = []
        try:
            await asyncio.to_thread(hci_burst, list(frames), interval_ms, repeat, self.adapter)
            _LOGGER.debug("HCI advertised %s frames x%s on %s", len(frames), repeat, self.adapter)
            return
        except Exception as err:  # noqa: BLE001 — try BlueZ next
            errors.append(f"HCI: {err}")
            _LOGGER.debug("HCI advertise failed (%s), trying BlueZ", err)

        try:
            await async_bluez_burst(frames, interval_ms, repeat, self.adapter)
            _LOGGER.debug("BlueZ advertised %s frames x%s on %s", len(frames), repeat, self.adapter)
            return
        except Exception as err:  # noqa: BLE001
            errors.append(f"BlueZ: {err}")

        raise AdvertiseError(
            "Could not advertise over Bluetooth ("
            + "; ".join(errors)
            + "). Use a USB adapter on the HA host, or MQTT + tools/mqtt_ble_advertiser.py"
        )
