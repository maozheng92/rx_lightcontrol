#!/usr/bin/env python3
"""Encode BLE advertise payloads used by rx_lightcontrol to control lights.

The APK never writes GATT characteristics for lamp control. Both product
lines broadcast a packed frame:

  * Home / type-2001 lights: manufacturer data company id 0x0F71 (3953)
  * Smart / 照明灯 (DELI): 13 little-endian 16-bit service UUIDs

See docs/ble-light-protocol.md for the full field map.

The codec lives in custom_components/rx_lightcontrol/protocol.py so the
Home Assistant integration stays self-contained.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_CC = Path(__file__).resolve().parents[1] / "custom_components" / "rx_lightcontrol"
if str(_CC) not in sys.path:
    sys.path.insert(0, str(_CC))

from protocol import (  # noqa: E402
    DELI_CMD,
    DELI_CMD_NAME,
    DELI_DEFAULT_PAYLOAD,
    DELI_KEY1,
    DELI_KEY2,
    MESH_COMPANY_ID,
    MESH_HEADER,
    MESH_INV_SBOX,
    MESH_LFSR_KEY,
    MESH_SBOX,
    _int_auto,
    _u8,
    brightness_slider_payload,
    build_light_dps,
    cct_slider_payload,
    crc16_ccitt_false,
    crc16_ccitt_xmodem,
    decode_deli_advertise,
    decode_deli_uuids,
    deli_advertise_payload,
    deli_air_frames,
    deli_pack_encode,
    deli_service_uuids,
    encode_deli_both,
    encode_deli_command,
    encode_mesh_frame,
    encode_mesh_light,
    format_rolling,
    generate_rolling,
    ha_brightness_to_percent,
    hexdump,
    kelvin_to_percent,
    mesh_air_frame,
    mesh_stream_cipher,
    nibble_mix,
    parse_dev_id,
    parse_rolling,
    parse_uuid_tokens,
    percent_to_ha_brightness,
    percent_to_kelvin,
    uuids_to_payload,
)


def _print_deli(tx: bytes, adv: bytes, uuids: Iterable[str]) -> None:
    print("Tx_Payload 31B:", hexdump(tx))
    print("Advertise  26B:", hexdump(adv))
    print("Service UUIDs:")
    for uuid in uuids:
        print(" ", uuid)


def _print_decoded(decoded: Dict) -> None:
    header = "HFKJ" if decoded["mode"] == 0 else "DLKJ"
    print(f"mode      : {decoded['mode']} {header}")
    print(f"pid       : {decoded['pid']}")
    print(f"cmd       : 0x{decoded['cmd']:02X} ({decoded['cmd_name']})")
    dest = decoded["dest_id"]
    print(f"dest_id   : {dest}" + (" (group)" if decoded.get("group") else ""))
    print("rolling   : " + ":".join(f"{b:02X}" for b in decoded["rolling"]))
    print(f"payload   : {hexdump(decoded['payload'])}")
    ts = decoded.get("timestamp")
    if ts is None:
        print("timestamp : n/a")
    else:
        print(f"timestamp : {ts} (0x{ts:04X})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="proto", required=True)

    mesh = sub.add_parser("mesh", help="Home type-2001 light (company id 0x0F71)")
    mesh.add_argument("--dev-id", required=True, help="6+ hex chars, e.g. AABBCC")
    mesh.add_argument("--pid", type=int, default=1)
    mesh.add_argument("--power", type=int, default=1)
    mesh.add_argument("--brightness", type=int, default=100)
    mesh.add_argument("--temperature", type=int, default=100)
    mesh.add_argument("--auxiliary", type=int, default=0)
    mesh.add_argument("--red", type=int, default=52)
    mesh.add_argument("--green", type=int, default=138)
    mesh.add_argument("--blue", type=int, default=255)

    deli = sub.add_parser("deli", help="Smart 照明灯 DELI rolling-code frames")
    deli.add_argument("--cmd", default="on", choices=sorted(DELI_CMD))
    deli.add_argument("--rolling", default="12:34:56:78", help="4 bytes aa:bb:cc:dd")
    deli.add_argument("--dest", type=int, default=1, help="device destId 0-255, 255=group")
    deli.add_argument("--pid", type=int, default=1)
    deli.add_argument("--mode", type=int, default=0, choices=(0, 1), help="0=HFKJ, 1=DLKJ")
    deli.add_argument("--both", action="store_true", help="print mode 0 and mode 1 like the app")
    deli.add_argument("--group", action="store_true")
    deli.add_argument("--p0", type=int, default=0)
    deli.add_argument("--p1", type=int, default=0)
    deli.add_argument("--p2", type=int, default=0)
    deli.add_argument("--timestamp", type=_int_auto, default=0, help="mode1 tail; decimal or 0x hex")
    deli.add_argument("--brightness", type=int, help="percent; sends cmd brightness, value*10 if >1")
    deli.add_argument("--cct", type=int, help="percent; sends cmd cct, value*10")
    deli.add_argument("--rgb", help="R,G,B 0-255, sends cmd rgb")

    decode = sub.add_parser(
        "deli-decode",
        help="Decode sniffed 13×16-bit service UUIDs from a scanner capture",
    )
    decode.add_argument(
        "uuids",
        nargs="+",
        help="13 short UUIDs, e.g. 4648 4A4B ... 1514 (commas/and allowed)",
    )

    args = parser.parse_args(argv)

    if args.proto == "mesh":
        frame = encode_mesh_light(
            args.dev_id,
            pid=args.pid,
            power=args.power,
            brightness=args.brightness,
            temperature=args.temperature,
            auxiliary=args.auxiliary,
            red=args.red,
            green=args.green,
            blue=args.blue,
        )
        print("Company ID : 0x%04X (%d)" % (MESH_COMPANY_ID, MESH_COMPANY_ID))
        print("Payload 26B:", hexdump(frame))
        return 0

    if args.proto == "deli-decode":
        decoded = decode_deli_uuids(parse_uuid_tokens(args.uuids))
        if not decoded:
            print("decode failed: not a HFKJ/DLKJ DELI frame, or pid/CRC mismatch")
            return 1
        _print_decoded(decoded)
        return 0

    rolling = [int(x, 16) for x in args.rolling.replace(",", ":").split(":")]
    cmd = args.cmd
    payload = [args.p0, args.p1, args.p2]
    if args.brightness is not None:
        cmd = "brightness"
        value = args.brightness if args.brightness <= 1 else args.brightness * 10
        payload = [0, (value >> 8) & 0xFF, value & 0xFF]
    if args.cct is not None:
        cmd = "cct"
        value = args.cct * 10
        payload = [0, (value >> 8) & 0xFF, value & 0xFF]
    if args.rgb:
        cmd = "rgb"
        parts = [int(x) for x in args.rgb.replace(":", ",").split(",")]
        payload = (parts + [0, 0, 0])[:3]
    if args.both:
        adv0, adv1 = encode_deli_both(
            cmd, rolling, args.dest, args.pid, payload, args.timestamp, args.group
        )
        print("mode0 HFKJ 26B:", hexdump(adv0))
        print("mode1 DLKJ 26B:", hexdump(adv1))
        return 0
    tx, adv, uuids = encode_deli_command(
        cmd,
        rolling,
        args.dest,
        args.pid,
        payload,
        timestamp=args.timestamp,
        mode=args.mode,
        group=args.group,
    )
    _print_deli(tx, adv, uuids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
