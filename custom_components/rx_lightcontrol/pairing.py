"""照明灯 pairing helpers.

The APK never gets an ACK.

Pairing: put the lamp in learn mode, advertise cmd 0xB4 payload AA 66 55
(HFKJ + DLKJ × 5 × ~100 ms), wait ~1.5 s, then treat it as success.

Delete pairing: advertise cmd 0xB0 payload 00 00 00 (BleManager.e()), then
drop the local record. 0xB4 AA EE 55 is 天境灯 add, not 照明灯 unpair.

Rolling code is phone-wide; destId cycles 1–15.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    from .advertiser import AdvertiseError, BleAdvertiser
    from .const import (
        CONF_ADAPTER,
        CONF_DEST_ID,
        CONF_INTERVAL_MS,
        CONF_MQTT_TOPIC,
        CONF_PROTOCOL,
        CONF_REPEAT,
        CONF_ROLLING,
        CONF_TRANSMIT,
        DEFAULT_INTERVAL_MS,
        DEFAULT_MQTT_TOPIC,
        DEFAULT_REPEAT,
        DOMAIN,
        PROTOCOL_DELI,
        TRANSMIT_LOCAL,
        TRANSMIT_MQTT,
    )
    from .protocol import (
        decode_deli_advertise,
        deli_air_frames,
        format_rolling,
        generate_rolling,
        parse_rolling,
    )
except ImportError:  # tools/ unit tests
    from advertiser import AdvertiseError, BleAdvertiser
    from const import (
        CONF_ADAPTER,
        CONF_DEST_ID,
        CONF_INTERVAL_MS,
        CONF_MQTT_TOPIC,
        CONF_PROTOCOL,
        CONF_REPEAT,
        CONF_ROLLING,
        CONF_TRANSMIT,
        DEFAULT_INTERVAL_MS,
        DEFAULT_MQTT_TOPIC,
        DEFAULT_REPEAT,
        DOMAIN,
        PROTOCOL_DELI,
        TRANSMIT_LOCAL,
        TRANSMIT_MQTT,
    )
    from protocol import (
        decode_deli_advertise,
        deli_air_frames,
        format_rolling,
        generate_rolling,
        parse_rolling,
    )

PAIR_CMD = "pair"
PAIR_PAYLOAD = (0xAA, 0x66, 0x55)
CLEAR_CMD = "clear"
CLEAR_PAYLOAD = (0, 0, 0)
PAIR_SETTLE_SECONDS = 1.5
DEST_ID_MIN = 1
DEST_ID_MAX = 15


def next_dest_id(used: Iterable[int]) -> int:
    """First free destId in 1..15, wrapping to 1 like the phone app."""
    taken = {int(value) for value in used if DEST_ID_MIN <= int(value) <= DEST_ID_MAX}
    for dest in range(DEST_ID_MIN, DEST_ID_MAX + 1):
        if dest not in taken:
            return dest
    return DEST_ID_MIN


def _rolling_key(text: str | None) -> str:
    if not text:
        return ""
    try:
        return format_rolling(parse_rolling(str(text)))
    except ValueError:
        return str(text).strip().upper()


def rolling_from_entries(entries: Iterable[Mapping[str, Any]]) -> Optional[str]:
    for item in entries:
        if item.get(CONF_PROTOCOL) != PROTOCOL_DELI:
            continue
        raw = item.get(CONF_ROLLING)
        if raw:
            return _rolling_key(str(raw))
    return None


def used_dest_ids(entries: Iterable[Mapping[str, Any]], rolling: str) -> list[int]:
    want = _rolling_key(rolling)
    used: list[int] = []
    for item in entries:
        if item.get(CONF_PROTOCOL) != PROTOCOL_DELI:
            continue
        if _rolling_key(str(item.get(CONF_ROLLING) or "")) != want:
            continue
        try:
            used.append(int(item.get(CONF_DEST_ID, 0)))
        except (TypeError, ValueError):
            continue
    return used


def entry_payloads(hass) -> list[dict[str, Any]]:
    return [dict(entry.data) for entry in hass.config_entries.async_entries(DOMAIN)]


async def async_shared_rolling(hass) -> str:
    """Reuse the HA-wide rolling code, matching one phone's addr1..addr4."""
    found = rolling_from_entries(entry_payloads(hass))
    if found:
        return found
    from homeassistant.helpers.storage import Store

    store = Store(hass, 1, f"{DOMAIN}_identity")
    data = await store.async_load()
    if isinstance(data, dict) and data.get("rolling"):
        try:
            return format_rolling(parse_rolling(str(data["rolling"])))
        except ValueError:
            pass
    rolling = format_rolling(generate_rolling())
    await store.async_save({"rolling": rolling})
    return rolling


async def async_remember_rolling(hass, rolling: str) -> None:
    from homeassistant.helpers.storage import Store

    store = Store(hass, 1, f"{DOMAIN}_identity")
    await store.async_save({"rolling": _rolling_key(rolling)})


def next_dest_id_for_hass(hass, rolling: str) -> int:
    return next_dest_id(used_dest_ids(entry_payloads(hass), rolling))


def advertiser_from_data(hass, data: Mapping[str, Any]) -> BleAdvertiser:
    transmit = data.get(CONF_TRANSMIT, TRANSMIT_LOCAL)
    mqtt_publish = None
    if transmit == TRANSMIT_MQTT:
        async def _publish(topic: str, payload: str) -> None:
            from homeassistant.components import mqtt

            await mqtt.async_publish(hass, topic, payload, 0, False)

        mqtt_publish = _publish
    return BleAdvertiser(
        transmit=str(transmit),
        adapter=str(data.get(CONF_ADAPTER) or "hci0"),
        mqtt_topic=str(data.get(CONF_MQTT_TOPIC) or DEFAULT_MQTT_TOPIC),
        mqtt_publish=mqtt_publish,
    )


async def async_send_named_burst(
    advertiser: BleAdvertiser,
    name: str,
    payload: Sequence[int],
    rolling: Sequence[int] | bytes,
    dest_id: int,
    pid: int,
    *,
    timestamp: int = 0,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    repeat: int = DEFAULT_REPEAT,
    settle_s: float = PAIR_SETTLE_SECONDS,
) -> tuple[bytes, bytes]:
    frames = deli_air_frames(name, rolling, dest_id, pid, payload, timestamp=timestamp)
    await advertiser.async_burst(list(frames), interval_ms, repeat)
    if settle_s > 0:
        await asyncio.sleep(settle_s)
    return frames


async def async_send_pair_burst(
    advertiser: BleAdvertiser,
    rolling: Sequence[int] | bytes,
    dest_id: int,
    pid: int,
    **kwargs: Any,
) -> tuple[bytes, bytes]:
    """Advertise pairing frames and wait like AddDeviceFragment (1.5 s)."""
    return await async_send_named_burst(
        advertiser, PAIR_CMD, PAIR_PAYLOAD, rolling, dest_id, pid, **kwargs
    )


async def async_send_clear_burst(
    advertiser: BleAdvertiser,
    rolling: Sequence[int] | bytes,
    dest_id: int,
    pid: int,
    **kwargs: Any,
) -> tuple[bytes, bytes]:
    """Advertise delete-pairing frames: cmd 0xB0 payload 00 00 00 (BleManager.e())."""
    return await async_send_named_burst(
        advertiser, CLEAR_CMD, CLEAR_PAYLOAD, rolling, dest_id, pid, **kwargs
    )


def assert_cmd_frames(
    frames: tuple[bytes, bytes],
    cmd: int,
    payload: Sequence[int],
    dest_id: int,
    rolling: bytes,
) -> None:
    expect = bytes(payload)
    for frame in frames:
        decoded = decode_deli_advertise(frame[5:] if len(frame) == 31 else frame)
        if decoded is None:
            raise AdvertiseError("frame did not decode")
        if decoded["cmd"] != cmd or decoded["payload"] != expect:
            raise AdvertiseError(f"frame is not cmd=0x{cmd:02X} payload={expect.hex()}")
        if decoded["dest_id"] != dest_id:
            raise AdvertiseError("destId mismatch")
        if bytes(decoded["rolling"]) != bytes(rolling):
            raise AdvertiseError("rolling mismatch")


def assert_pair_frames(frames: tuple[bytes, bytes], dest_id: int, rolling: bytes) -> None:
    assert_cmd_frames(frames, 0xB4, PAIR_PAYLOAD, dest_id, rolling)


def assert_clear_frames(frames: tuple[bytes, bytes], dest_id: int, rolling: bytes) -> None:
    assert_cmd_frames(frames, 0xB0, CLEAR_PAYLOAD, dest_id, rolling)


async def _async_advertise_named(hass, data: Mapping[str, Any], name: str, payload: Sequence[int]):
    try:
        from .coordinator import get_pid_counter
    except ImportError:
        from coordinator import get_pid_counter

    rolling = parse_rolling(str(data[CONF_ROLLING]))
    dest_id = int(data[CONF_DEST_ID])
    counter = get_pid_counter(hass)
    await counter.async_load()
    pid = await counter.async_next()
    timestamp = int(time.time() * 1000) & 0xFFFF
    advertiser = advertiser_from_data(hass, data)
    frames = await async_send_named_burst(
        advertiser,
        name,
        payload,
        rolling,
        dest_id,
        pid,
        timestamp=timestamp,
        interval_ms=int(data.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS)),
        repeat=int(data.get(CONF_REPEAT, DEFAULT_REPEAT)),
    )
    return frames, rolling, dest_id


async def async_advertise_pair(hass, data: Mapping[str, Any]) -> None:
    """Config-flow / options-flow pairing: increment pid, air both modes, settle."""
    frames, rolling, dest_id = await _async_advertise_named(hass, data, PAIR_CMD, PAIR_PAYLOAD)
    assert_pair_frames(frames, dest_id, rolling)
    await async_remember_rolling(hass, format_rolling(rolling))


async def async_advertise_unpair(hass, data: Mapping[str, Any]) -> None:
    """Send 照明灯 delete (0xB0 00 00 00), matching BleManager.e()."""
    frames, rolling, dest_id = await _async_advertise_named(hass, data, CLEAR_CMD, CLEAR_PAYLOAD)
    assert_clear_frames(frames, dest_id, rolling)
