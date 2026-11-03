"""BLE advertise codecs for 睿信 / SmartMei lights (DELI + mesh).

Copied into the Home Assistant custom component so the integration is
self-contained. The CLI in tools/ble_light_encode.py re-exports this module.
"""

from __future__ import annotations

import random
import re
import struct
from typing import Dict, List, Optional, Sequence, Tuple

# Native libmsglibrary.so rodata tables (SELFKEY1 / SELFKEY2).
DELI_KEY1 = bytes.fromhex("1d041120987528460baf43acd6be898e")
DELI_KEY2 = bytes.fromhex("08d1110257898264b0fa34ca6ded98e8")

# f.c.b.i.a.a — 256-byte permutation used as an inverse S-box.
MESH_SBOX = [
    162, 191, 255, 13, 142, 213, 78, 38, 240, 20, 31, 252, 206, 171, 169, 23,
    145, 77, 214, 211, 220, 150, 242, 61, 91, 195, 42, 159, 215, 54, 101, 127,
    239, 254, 157, 228, 176, 71, 181, 46, 172, 202, 16, 223, 232, 85, 246, 222,
    9, 207, 170, 125, 201, 89, 105, 72, 32, 233, 175, 51, 180, 28, 45, 140,
    103, 245, 212, 238, 235, 244, 92, 153, 21, 158, 76, 87, 208, 144, 41, 224,
    36, 143, 229, 192, 126, 70, 98, 122, 236, 177, 173, 251, 237, 138, 88, 210,
    133, 135, 14, 58, 130, 129, 114, 194, 5, 141, 136, 209, 37, 96, 60, 227,
    253, 216, 166, 115, 48, 205, 124, 149, 34, 118, 0, 147, 250, 234, 230, 163,
    25, 116, 7, 221, 146, 17, 108, 26, 75, 121, 100, 187, 148, 219, 112, 53,
    225, 139, 3, 40, 73, 137, 161, 10, 193, 188, 186, 178, 200, 95, 107, 167,
    99, 197, 83, 155, 80, 94, 6, 243, 111, 249, 168, 248, 57, 154, 152, 241,
    174, 30, 33, 247, 1, 82, 203, 160, 8, 49, 12, 199, 52, 190, 231, 185,
    39, 113, 22, 11, 64, 102, 184, 67, 110, 204, 196, 55, 66, 44, 131, 68,
    128, 74, 93, 69, 18, 183, 59, 2, 15, 117, 151, 43, 226, 62, 65, 179,
    156, 81, 63, 90, 106, 119, 217, 164, 19, 120, 134, 198, 132, 182, 79, 84,
    97, 165, 4, 29, 123, 56, 47, 109, 218, 189, 86, 27, 35, 50, 24, 104,
]
MESH_INV_SBOX = [0] * 256
for _i, _v in enumerate(MESH_SBOX):
    MESH_INV_SBOX[_v] = _i

MESH_COMPANY_ID = 0x0F71  # 3953 decimal, little-endian in advertise AD
MESH_LFSR_KEY = 37
MESH_HEADER = (0x65, 0x88)  # 101, 136

# DELI command bytes from f.c.c.a.a (DLKeyCode).
DELI_CMD = {
    "pair": 0xB4,
    "unpair": 0xB4,
    "clear": 0xB0,
    "query": 0xB0,
    "off": 0xB2,
    "on": 0xB3,
    "brightness": 0xB5,
    "cct": 0xB7,
    "rgb": 0xCA,
    "rgb_off": 0xC0,
    "rgb_on": 0xC1,
    "timer": 0xA5,
    "scene_code": 0xA7,
    "fan_speed": 0xA7,
    "aux": 0xA6,
    "night": 0xA1,
    "night_off": 0xD1,
    "scene": 0xD3,
    "dynamic": 0xCB,
    "dynamic_speed": 0xC9,
    "dynamic_stop": 0xDB,
    "music_off": 0xD9,
    "music_on": 0xDA,
}

# Default 3-byte payloads when the caller does not pass one.
DELI_DEFAULT_PAYLOAD = {
    "pair": (0xAA, 0x66, 0x55),
    "unpair": (0xAA, 0xEE, 0x55),
    "night": (25, 25, 0),
}

DELI_CMD_NAME = {
    0xB4: "pair/unpair",
    0xB0: "clear",
    0xB2: "off",
    0xB3: "on",
    0xB5: "brightness",
    0xB7: "cct",
    0xCA: "rgb",
    0xC0: "rgb_off",
    0xC1: "rgb_on",
    0xA5: "timer",
    0xA7: "scene_code",
    0xA6: "aux",
    0xA1: "night",
    0xD1: "night_off",
    0xD3: "scene",
    0xCB: "dynamic",
    0xC9: "dynamic_speed",
    0xDB: "dynamic_stop",
    0xD9: "music_off",
    0xDA: "music_on",
}


def _u8(value: int) -> int:
    return value & 0xFF


def _int_auto(value: str) -> int:
    """Accept decimal or 0x-prefixed hex (argparse `type=int` is base 10 only)."""
    return int(value, 0)


def crc16_ccitt_xmodem(data: bytes, init: int = 0) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0) matching libmsglibrary.so."""
    crc = init & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) matching f.c.b.i.a."""
    crc = 0xFFFF
    for byte in data:
        for bit in range(8):
            data_bit = (byte >> (7 - bit)) & 1
            msb = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if data_bit ^ msb:
                crc ^= 0x1021
    return crc


def nibble_mix(value: int) -> int:
    value &= 0xFF
    return (value & 0x0F) ^ ((value & 0xF0) >> 4)


def deli_pack_encode(
    mode: int,
    rolling: Sequence[int],
    dest_id: int,
    pid: int,
    cmd: int,
    payload: Sequence[int],
    timestamp: int = 0,
) -> bytes:
    """Reimplementation of native DELI_Pack_Encode.

    Returns the 31-byte Tx_Payload. The app advertises bytes [5:31] (26 bytes)
    as 13 little-endian 16-bit service UUIDs.
    """
    if len(rolling) < 4:
        raise ValueError("rolling code needs 4 bytes")
    if len(payload) < 3:
        raise ValueError("payload needs 3 bytes")

    mode &= 0xFF
    dest_id &= 0xFF
    pid &= 0xFF
    cmd &= 0xFF
    p0, p1, p2 = (_u8(payload[0]), _u8(payload[1]), _u8(payload[2]))
    key = DELI_KEY1 if mode == 0 else DELI_KEY2
    buf = bytearray(31)

    buf[0:5] = bytes([0x02, 0x01, 0x01, 0x1B, 0x03])
    if mode == 0:
        buf[5:9] = b"HFKJ"
    else:
        buf[5:9] = b"DLKJ"
    buf[9] = 0xFF
    buf[10] = _u8(pid + 1)
    buf[11] = pid
    buf[12:16] = bytes(_u8(b) for b in rolling[:4])
    buf[16] = cmd
    buf[17] = dest_id
    buf[18] = 0xFF
    buf[19] = pid
    buf[20] = p0
    buf[21] = p1
    buf[22] = p2

    # cmd 0xB4 (pair) skips the first scramble of bytes 18-22.
    if cmd != 0xB4:
        k = key[nibble_mix(pid)]
        if mode == 0:
            buf[18] = _u8(0x42 - k)
            buf[19] = _u8((k ^ pid) + 0xAC)
            buf[20] = _u8((p0 ^ k) + 0xD6)
            buf[21] = _u8((p1 ^ k) + 0xBE)
            buf[22] = _u8((p2 ^ k) + 0x89)
        else:
            buf[18] = _u8(0x33 - k)
            buf[19] = _u8((k ^ pid) + 0xCA)
            buf[20] = _u8((p0 ^ k) + 0x6D)
            buf[21] = _u8((p1 ^ k) + 0xED)
            buf[22] = _u8((p2 ^ k) + 0x98)

    # CRC-16/XMODEM of 0xFF + buf[10:22], stored LE at offset 23.
    crc = crc16_ccitt_xmodem(bytes([0xFF]) + bytes(buf[10:22]))
    struct.pack_into("<H", buf, 23, crc)

    k2 = key[nibble_mix(pid + 1)]
    if mode == 0:
        for i in range(16):
            idx = 9 + i
            buf[idx] = _u8((buf[idx] ^ k2) + key[(i + 6) & 0x0F])
        buf[25] = 0x10
        buf[26] = 0x11
        buf[27] = 0x12
    else:
        for i in range(9, 25):
            buf[i] = _u8((buf[i] ^ k2) + key[i & 0x0F])
        buf[25] = 0xAA
        buf[26] = _u8(timestamp)
        buf[27] = _u8(timestamp >> 8)
    buf[28] = 0x13
    buf[29] = 0x14
    buf[30] = 0x15
    return bytes(buf)


def deli_advertise_payload(tx31: bytes) -> bytes:
    """26-byte slice the app actually puts on air (drops 5-byte radio header)."""
    if len(tx31) != 31:
        raise ValueError("Tx_Payload must be 31 bytes")
    return tx31[5:]


def deli_service_uuids(payload26: bytes) -> List[str]:
    """Build the 16-bit service UUIDs AdvertiseManager.addServiceUuid uses."""
    uuids = []
    for i in range(0, len(payload26) - 1, 2):
        lo, hi = payload26[i], payload26[i + 1]
        uuids.append(f"0000{hi:02x}{lo:02x}-0000-1000-8000-00805f9b34fb")
    return uuids


def encode_deli_command(
    name: str,
    rolling: Sequence[int],
    dest_id: int,
    pid: int,
    payload: Sequence[int] = (0, 0, 0),
    timestamp: int = 0,
    mode: int = 0,
    group: bool = False,
) -> Tuple[bytes, bytes, List[str]]:
    """Return (tx31, advertise26, uuid_list) for a named DELI light command."""
    cmd = DELI_CMD[name]
    use_dest = 255 if group else dest_id
    body = list(payload)
    if body == [0, 0, 0] and name in DELI_DEFAULT_PAYLOAD:
        body = list(DELI_DEFAULT_PAYLOAD[name])
    if group and name in ("on", "off"):
        body = [0, 0, 0]
    tx = deli_pack_encode(mode, rolling, use_dest, pid, cmd, body, timestamp)
    adv = deli_advertise_payload(tx)
    return tx, adv, deli_service_uuids(adv)


def encode_deli_both(
    name: str,
    rolling: Sequence[int],
    dest_id: int,
    pid: int,
    payload: Sequence[int] = (0, 0, 0),
    timestamp: int = 0,
    group: bool = False,
) -> Tuple[bytes, bytes]:
    """Mode 0 + mode 1 advertise payloads, matching MsgGenerator.b()."""
    _, adv0, _ = encode_deli_command(
        name, rolling, dest_id, pid, payload, timestamp, mode=0, group=group
    )
    _, adv1, _ = encode_deli_command(
        name, rolling, dest_id, pid, payload, timestamp, mode=1, group=group
    )
    return adv0, adv1


def uuids_to_payload(uuids: Sequence[str]) -> bytes:
    """Convert scanner 16-bit UUID list (e.g. 4648,4A4B,...) back to 26 bytes."""
    out = bytearray()
    for item in uuids:
        token = item.strip().lower().replace("0x", "")
        if len(token) > 4:
            token = token[4:8] if token.startswith("0000") else token[-4:]
        if len(token) != 4:
            raise ValueError(f"bad uuid short id: {item}")
        value = int(token, 16)
        out.append(value & 0xFF)
        out.append((value >> 8) & 0xFF)
    if len(out) != 26:
        raise ValueError(f"expected 13 UUIDs / 26 bytes, got {len(out)}")
    return bytes(out)


def _unscramble_second(buf: bytearray, mode: int, pid: int) -> None:
    key = DELI_KEY1 if mode == 0 else DELI_KEY2
    k2 = key[nibble_mix(pid + 1)]
    if mode == 0:
        for i in range(16):
            idx = 9 + i
            buf[idx] = _u8((buf[idx] - key[(i + 6) & 0x0F]) ^ k2)
    else:
        for i in range(9, 25):
            buf[i] = _u8((buf[i] - key[i & 0x0F]) ^ k2)


def decode_deli_advertise(adv: bytes) -> Optional[Dict]:
    """Undo DELI packing. `adv` is the 26-byte UUID payload (Tx_Payload[5:31])."""
    if len(adv) != 26:
        raise ValueError("advertise payload must be 26 bytes")
    if adv[:4] == b"HFKJ":
        mode = 0
    elif adv[:4] == b"DLKJ":
        mode = 1
    else:
        return None
    raw = bytes([0x02, 0x01, 0x01, 0x1B, 0x03]) + adv
    key = DELI_KEY1 if mode == 0 else DELI_KEY2
    for pid in range(256):
        buf = bytearray(raw)
        _unscramble_second(buf, mode, pid)
        if buf[9] != 0xFF or buf[11] != pid or buf[10] != _u8(pid + 1):
            continue
        crc = crc16_ccitt_xmodem(bytes([0xFF]) + bytes(buf[10:22]))
        if crc != (buf[23] | (buf[24] << 8)):
            continue
        cmd = buf[16]
        dest = buf[17]
        rolling = bytes(buf[12:16])
        k = key[nibble_mix(pid)]
        if cmd == 0xB4:
            payload = bytes(buf[20:23])
        elif mode == 0:
            if buf[18] != _u8(0x42 - k) or buf[19] != _u8((k ^ pid) + 0xAC):
                continue
            payload = bytes(
                [
                    _u8((buf[20] - 0xD6) ^ k),
                    _u8((buf[21] - 0xBE) ^ k),
                    _u8((buf[22] - 0x89) ^ k),
                ]
            )
        else:
            if buf[18] != _u8(0x33 - k) or buf[19] != _u8((k ^ pid) + 0xCA):
                continue
            payload = bytes(
                [
                    _u8((buf[20] - 0x6D) ^ k),
                    _u8((buf[21] - 0xED) ^ k),
                    _u8((buf[22] - 0x98) ^ k),
                ]
            )
        timestamp = None
        if mode == 1:
            timestamp = adv[21] | (adv[22] << 8)
        return {
            "mode": mode,
            "pid": pid,
            "cmd": cmd,
            "cmd_name": DELI_CMD_NAME.get(cmd, f"0x{cmd:02X}"),
            "dest_id": dest,
            "rolling": rolling,
            "payload": payload,
            "timestamp": timestamp,
            "group": dest == 255 and cmd in (0xB2, 0xB3),
        }
    return None


def decode_deli_uuids(uuids: Sequence[str]) -> Optional[Dict]:
    return decode_deli_advertise(uuids_to_payload(uuids))


def parse_uuid_tokens(tokens: Sequence[str]) -> List[str]:
    """Accept scanner paste: '4648,4A4B,... and 1514' or thirteen separate args."""
    text = " ".join(tokens)
    for prefix in ("services:", "service:", "uuids:", "uuid:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    for sep in (",", ";", "|", "and"):
        text = text.replace(sep, " ")
    return [item for item in text.split() if item]


class _Lfsr7:
    """7-bit LFSR used by f.c.b.i.a.e / f.c.b.i.a.f (key loaded as 7 bits)."""

    def __init__(self, key: int) -> None:
        self.state = [0] * 7
        self.state[0] = 1
        for i in range(1, 7):
            self.state[i] = (key >> (6 - i)) & 1

    def bit(self) -> int:
        t = self.state[3] ^ self.state[6]
        nxt = [
            self.state[6],
            self.state[0],
            self.state[1],
            self.state[2],
            t,
            self.state[4],
            self.state[5],
        ]
        self.state = nxt
        return self.state[0]


def mesh_stream_cipher(data: bytearray, key: int = MESH_LFSR_KEY) -> None:
    """In-place bit-wise LFSR XOR, LSB first, matching f.c.b.i.a.d."""
    lfsr = _Lfsr7(key)
    for i, byte in enumerate(data):
        out = 0
        for bit in range(8):
            out |= ((byte >> bit) & 1 ^ lfsr.bit()) << bit
        data[i] = _u8(out)


def parse_dev_id(dev_id: str) -> bytes:
    """First 3 bytes of the 6-hex-char mesh device id."""
    hexid = "".join(ch for ch in dev_id if ch.isalnum())
    if len(hexid) < 6:
        raise ValueError("dev_id needs at least 6 hex characters")
    return bytes.fromhex(hexid[:6])


def build_light_dps(
    power: int = 1,
    brightness: int = 100,
    temperature: int = 100,
    auxiliary: int = 0,
    red: int = 52,
    green: int = 138,
    blue: int = 255,
    timer_state: int = 0,
    timer_h: int = 0,
    timer_m: int = 0,
    timer_s: int = 0,
) -> bytes:
    """19-byte plaintext DPS block for device type 2001 (LightScreen)."""
    buf = bytearray(19)
    buf[0] = 0x77
    buf[1] = 1
    buf[2] = _u8(power)
    buf[3] = _u8(max(brightness, 0))
    buf[4] = _u8(temperature)
    buf[5] = _u8(auxiliary)
    buf[6] = _u8(red)
    buf[7] = _u8(green)
    buf[8] = _u8(blue)
    buf[9] = _u8(timer_state)
    buf[10] = _u8(timer_h)
    buf[11] = _u8(timer_m)
    buf[12] = _u8(timer_s)
    return bytes(buf)


def encode_mesh_frame(dev_id: str, dps: bytes, pid: int) -> bytes:
    """26-byte manufacturer payload DeviceComponent.b() puts on air."""
    if len(dps) != 19:
        raise ValueError("dps must be 19 bytes")
    pid &= 0xFF
    dest = parse_dev_id(dev_id)
    frame = bytearray(26)
    frame[0] = MESH_HEADER[0]
    frame[1] = MESH_HEADER[1]
    frame[2:5] = dest
    frame[5] = pid
    frame[6] = 0

    body = bytearray(_u8(b + pid) for b in dps)
    mesh_stream_cipher(body, MESH_LFSR_KEY)
    for i, byte in enumerate(body):
        frame[7 + i] = MESH_INV_SBOX[byte]

    frame[6] = _u8(crc16_ccitt_false(bytes(frame)))
    return bytes(frame)


def encode_mesh_light(dev_id: str, pid: int = 1, **dps_kwargs) -> bytes:
    return encode_mesh_frame(dev_id, build_light_dps(**dps_kwargs), pid)


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def generate_rolling() -> bytes:
    """Match AddDeviceFragment: `(byte)(127 - Random.nextInt(256))` four times."""
    return bytes((127 - random.randint(0, 255)) & 0xFF for _ in range(4))


def parse_rolling(text: str) -> bytes:
    """Accept 6B:77:4C:09, 6B774C09, or 6B 77 4C 09."""
    hexid = re.sub(r"[^0-9A-Fa-f]", "", text)
    if len(hexid) != 8:
        raise ValueError("rolling code must be 4 bytes (8 hex characters)")
    return bytes.fromhex(hexid)


def format_rolling(rolling: Sequence[int] | bytes) -> str:
    return ":".join(f"{_u8(b):02X}" for b in rolling[:4])


def brightness_slider_payload(percent: int) -> Tuple[int, int, int]:
    """Match BleManager.d(v): ≤1 as-is, >1 as n×10 big-endian in the last two bytes."""
    percent = int(percent)
    value = percent if percent <= 1 else percent * 10
    value = max(0, min(0xFFFF, value))
    return (0, (value >> 8) & 0xFF, value & 0xFF)


def cct_slider_payload(percent: int) -> Tuple[int, int, int]:
    """Match BleManager.A(v): always n×10."""
    percent = max(0, min(100, int(percent)))
    value = percent * 10
    return (0, (value >> 8) & 0xFF, value & 0xFF)


def ha_brightness_to_percent(brightness: int) -> int:
    """Map Home Assistant 1..255 onto the app slider 1..100."""
    return max(1, min(100, int(round(int(brightness) * 100 / 255))))


def percent_to_ha_brightness(percent: int) -> int:
    return max(1, min(255, int(round(int(percent) * 255 / 100))))


def kelvin_to_percent(kelvin: int, min_k: int = 2700, max_k: int = 6500) -> int:
    if max_k <= min_k:
        return 100
    pct = round((int(kelvin) - min_k) * 100 / (max_k - min_k))
    return max(0, min(100, int(pct)))


def percent_to_kelvin(percent: int, min_k: int = 2700, max_k: int = 6500) -> int:
    return int(round(min_k + int(percent) * (max_k - min_k) / 100))


def deli_air_frames(
    name: str,
    rolling: Sequence[int],
    dest_id: int,
    pid: int,
    payload: Sequence[int] = (0, 0, 0),
    timestamp: int = 0,
    group: bool = False,
) -> Tuple[bytes, bytes]:
    """Exact 31-byte on-air packets (mode 0 HFKJ, mode 1 DLKJ)."""
    tx0, _, _ = encode_deli_command(
        name, rolling, dest_id, pid, payload, timestamp, mode=0, group=group
    )
    tx1, _, _ = encode_deli_command(
        name, rolling, dest_id, pid, payload, timestamp, mode=1, group=group
    )
    return tx0, tx1


def mesh_air_frame(frame26: bytes) -> bytes:
    """Legacy advertising data: Manufacturer Specific only (fits 31 bytes)."""
    if len(frame26) != 26:
        raise ValueError("mesh payload must be 26 bytes")
    return bytes(
        [0x1D, 0xFF, MESH_COMPANY_ID & 0xFF, (MESH_COMPANY_ID >> 8) & 0xFF]
    ) + frame26


