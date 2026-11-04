#!/usr/bin/env python3
"""Unit tests for tools/ble_light_encode.py (no BLE hardware required)."""

from __future__ import annotations

import unittest

import ble_light_encode as enc


class SboxTests(unittest.TestCase):
    def test_permutation(self) -> None:
        self.assertEqual(len(enc.MESH_SBOX), 256)
        self.assertEqual(len(set(enc.MESH_SBOX)), 256)
        self.assertEqual(sorted(enc.MESH_SBOX), list(range(256)))

    def test_inverse(self) -> None:
        for i, value in enumerate(enc.MESH_SBOX):
            self.assertEqual(enc.MESH_INV_SBOX[value], i)


class DeliTests(unittest.TestCase):
    def test_sendutil_sample_structure(self) -> None:
        # SendUtil.a(): mode=0, rolling=12 34 56 78, dest=255, pid=1,
        # cmd=17, payload=22 33 44, timestamp=4660.
        tx = enc.deli_pack_encode(
            0, [0x12, 0x34, 0x56, 0x78], 255, 1, 17, [0x22, 0x33, 0x44], 4660
        )
        self.assertEqual(len(tx), 31)
        self.assertEqual(tx[0:5], bytes([0x02, 0x01, 0x01, 0x1B, 0x03]))
        self.assertEqual(tx[5:9], b"HFKJ")
        self.assertEqual(tx[25:31], bytes([0x10, 0x11, 0x12, 0x13, 0x14, 0x15]))
        adv = enc.deli_advertise_payload(tx)
        self.assertEqual(len(adv), 26)
        uuids = enc.deli_service_uuids(adv)
        self.assertEqual(len(uuids), 13)
        # First UUID is little-endian of adv[0], adv[1] = 'H','F' -> 0x4648.
        self.assertEqual(uuids[0], "00004648-0000-1000-8000-00805f9b34fb")

    def test_mode1_header_and_timestamp(self) -> None:
        tx = enc.deli_pack_encode(
            1, [1, 2, 3, 4], 7, 9, enc.DELI_CMD["on"], [0, 0, 0], 0x1234
        )
        self.assertEqual(tx[5:9], b"DLKJ")
        self.assertEqual(tx[25], 0xAA)
        self.assertEqual(tx[26], 0x34)
        self.assertEqual(tx[27], 0x12)
        self.assertEqual(tx[28:31], bytes([0x13, 0x14, 0x15]))

    def test_pair_skips_first_scramble(self) -> None:
        tx_on = enc.deli_pack_encode(0, [1, 2, 3, 4], 1, 1, enc.DELI_CMD["on"], [0, 0, 0])
        tx_pair = enc.deli_pack_encode(
            0, [1, 2, 3, 4], 1, 1, enc.DELI_CMD["pair"], [0xAA, 0x66, 0x55]
        )
        self.assertNotEqual(tx_on, tx_pair)
        self.assertEqual(len(tx_pair), 31)

    def test_on_off_named(self) -> None:
        tx_on, adv_on, _ = enc.encode_deli_command("on", [9, 8, 7, 6], 3, 2)
        tx_off, adv_off, _ = enc.encode_deli_command("off", [9, 8, 7, 6], 3, 2)
        self.assertNotEqual(adv_on, adv_off)
        self.assertEqual(tx_on[5:9], b"HFKJ")
        self.assertEqual(len(adv_on), 26)

    def test_brightness_payload_endian(self) -> None:
        # UI sends percent*10 as 16-bit big-endian in the 3-byte payload.
        _, adv, _ = enc.encode_deli_command(
            "brightness", [1, 2, 3, 4], 1, 1, payload=[0, 0x03, 0xE8]
        )
        self.assertEqual(len(adv), 26)

    def test_pair_default_payload_skips_first_scramble(self) -> None:
        tx, adv, _ = enc.encode_deli_command("pair", [0x12, 0x34, 0x56, 0x78], 1, 1)
        self.assertEqual(len(adv), 26)
        self.assertEqual(tx[5:9], b"HFKJ")
        # cmd 0xB4 skips first scramble; pair uses AA 66 55
        _, unpair, _ = enc.encode_deli_command("unpair", [0x12, 0x34, 0x56, 0x78], 1, 1)
        self.assertNotEqual(adv, unpair)

    def test_both_modes(self) -> None:
        adv0, adv1 = enc.encode_deli_both("on", [9, 8, 7, 6], 3, 2)
        self.assertEqual(len(adv0), 26)
        self.assertEqual(len(adv1), 26)
        self.assertEqual(adv0[:4], b"HFKJ")
        self.assertEqual(adv1[:4], b"DLKJ")

    def test_decode_roundtrip_named_commands(self) -> None:
        for name, payload in (
            ("on", (0, 0, 0)),
            ("off", (0, 0, 0)),
            ("pair", (0xAA, 0x66, 0x55)),
            ("clear", (0, 0, 0)),
            ("brightness", (0, 0x03, 0x20)),
        ):
            _, adv0, uuids0 = enc.encode_deli_command(
                name, [0x6B, 0x77, 0x4C, 0x09], 1, 6, payload, mode=0
            )
            _, adv1, uuids1 = enc.encode_deli_command(
                name, [0x6B, 0x77, 0x4C, 0x09], 1, 6, payload, timestamp=0xF73D, mode=1
            )
            for adv, uuids, mode in ((adv0, uuids0, 0), (adv1, uuids1, 1)):
                decoded = enc.decode_deli_advertise(adv)
                self.assertIsNotNone(decoded, name)
                self.assertEqual(decoded["mode"], mode)
                self.assertEqual(decoded["cmd"], enc.DELI_CMD[name])
                self.assertEqual(decoded["dest_id"], 1)
                self.assertEqual(decoded["pid"], 6)
                self.assertEqual(decoded["rolling"], bytes([0x6B, 0x77, 0x4C, 0x09]))
                self.assertEqual(decoded["payload"], bytes(payload))
                self.assertEqual(enc.decode_deli_uuids([u[4:8] for u in uuids]), decoded)


class DeliCaptureTests(unittest.TestCase):
    """Live scanner capture of 开灯 (nRF Connect style 13 UUID list)."""

    HFKJ_UUIDS = [
        "4648", "4A4B", "87E1", "DC4B", "B674", "B325", "EAD0",
        "BCB9", "17B1", "A4C5", "1110", "1312", "1514",
    ]
    DLKJ_UUIDS = [
        "4C44", "4A4B", "9795", "7C2C", "C000", "DF55", "E636",
        "E22C", "0094", "1DE0", "3DAA", "13F7", "1514",
    ]

    def test_decode_hfkj_on_capture(self) -> None:
        decoded = enc.decode_deli_uuids(self.HFKJ_UUIDS)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["mode"], 0)
        self.assertEqual(decoded["cmd"], 0xB3)
        self.assertEqual(decoded["cmd_name"], "on")
        self.assertEqual(decoded["dest_id"], 1)
        self.assertEqual(decoded["pid"], 6)
        self.assertEqual(decoded["rolling"], bytes([0x6B, 0x77, 0x4C, 0x09]))
        self.assertEqual(decoded["payload"], bytes([0, 0, 0]))
        self.assertFalse(decoded["group"])
        self.assertIsNone(decoded["timestamp"])

    def test_decode_dlkj_on_capture(self) -> None:
        decoded = enc.decode_deli_uuids(self.DLKJ_UUIDS)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["mode"], 1)
        self.assertEqual(decoded["cmd"], 0xB3)
        self.assertEqual(decoded["dest_id"], 1)
        self.assertEqual(decoded["pid"], 6)
        self.assertEqual(decoded["rolling"], bytes([0x6B, 0x77, 0x4C, 0x09]))
        self.assertEqual(decoded["payload"], bytes([0, 0, 0]))
        self.assertEqual(decoded["timestamp"], 0xF73D)

    def test_reencode_matches_sniffed_uuids(self) -> None:
        adv0, adv1 = enc.encode_deli_both(
            "on", [0x6B, 0x77, 0x4C, 0x09], 1, 6, timestamp=0xF73D
        )
        self.assertEqual(enc.uuids_to_payload(self.HFKJ_UUIDS), adv0)
        self.assertEqual(enc.uuids_to_payload(self.DLKJ_UUIDS), adv1)

    def test_cli_decode_accepts_scanner_paste(self) -> None:
        pasted = (
            "Services: 4C44, 4A4B, 9795, 7C2C, C000, DF55, E636, "
            "E22C, 0094, 1DE0, 3DAA, 13F7, and 1514"
        )
        rc = enc.main(["deli-decode", pasted])
        self.assertEqual(rc, 0)


class MeshTests(unittest.TestCase):
    def test_light_on_frame(self) -> None:
        frame = enc.encode_mesh_light("AABBCC", pid=1, power=1, brightness=80, temperature=50)
        self.assertEqual(len(frame), 26)
        self.assertEqual(frame[0], 0x65)
        self.assertEqual(frame[1], 0x88)
        self.assertEqual(frame[2:5], bytes.fromhex("AABBCC"))
        self.assertEqual(frame[5], 1)
        crc_low = enc.crc16_ccitt_false(frame[:6] + bytes([0]) + frame[7:]) & 0xFF
        self.assertEqual(frame[6], crc_low)

    def test_power_changes_ciphertext(self) -> None:
        on = enc.encode_mesh_light("010203", pid=7, power=1)
        off = enc.encode_mesh_light("010203", pid=7, power=0)
        self.assertNotEqual(on, off)
        self.assertEqual(on[:6], off[:6])  # header + dest + pid match; crc at [6] differs

    def test_lfsr_is_involutive_xor(self) -> None:
        original = bytearray(range(19))
        work = bytearray(original)
        enc.mesh_stream_cipher(work)
        self.assertNotEqual(bytes(work), bytes(original))
        enc.mesh_stream_cipher(work)
        self.assertEqual(bytes(work), bytes(original))

    def test_mesh_decode_roundtrip(self) -> None:
        frame = enc.encode_mesh_light(
            "AABBCC", pid=9, power=1, brightness=80, temperature=40, red=10, green=20, blue=30
        )
        decoded = enc.decode_mesh_frame(frame)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["dev_id"], "AABBCC")
        self.assertEqual(decoded["pid"], 9)
        self.assertEqual(decoded["power"], 1)
        self.assertEqual(decoded["brightness"], 80)
        self.assertEqual(decoded["temperature"], 40)
        self.assertEqual((decoded["red"], decoded["green"], decoded["blue"]), (10, 20, 30))

    def test_decode_ble_advertisement_mesh_and_deli(self) -> None:
        mesh = enc.encode_mesh_light("010203", pid=2, power=0, brightness=50, temperature=10)
        air = enc.mesh_air_frame(mesh)
        decoded = enc.decode_ble_advertisement(raw=air)
        self.assertEqual(decoded["family"], "mesh")
        self.assertEqual(decoded["dev_id"], "010203")
        self.assertEqual(decoded["power"], 0)

        tx0, _ = enc.deli_air_frames("on", [0x6B, 0x77, 0x4C, 0x09], 1, 6, timestamp=0xF73D)
        decoded = enc.decode_ble_advertisement(raw=tx0)
        self.assertEqual(decoded["family"], "deli")
        self.assertEqual(decoded["cmd"], 0xB3)
        self.assertEqual(decoded["dest_id"], 1)

        uuids = enc.deli_service_uuids(tx0[5:])
        decoded = enc.decode_ble_advertisement(service_uuids=uuids)
        self.assertEqual(decoded["cmd"], 0xB3)

    def test_slider_payload_to_percent(self) -> None:
        self.assertEqual(enc.slider_payload_to_percent((0, 0x03, 0x20)), 80)
        self.assertEqual(enc.slider_payload_to_percent((0, 0, 100)), 100)
        self.assertEqual(enc.slider_payload_to_percent((0, 0, 1)), 1)

    def test_dps_layout(self) -> None:
        dps = enc.build_light_dps(
            power=1, brightness=80, temperature=40, auxiliary=1, red=10, green=20, blue=30
        )
        self.assertEqual(dps[0], 0x77)
        self.assertEqual(list(dps[1:9]), [1, 1, 80, 40, 1, 10, 20, 30])
        self.assertEqual(len(dps), 19)


class HaMappingTests(unittest.TestCase):
    def test_rolling_parse_and_format(self) -> None:
        raw = enc.parse_rolling("6B:77:4C:09")
        self.assertEqual(raw, bytes([0x6B, 0x77, 0x4C, 0x09]))
        self.assertEqual(enc.parse_rolling("6b774c09"), raw)
        self.assertEqual(enc.format_rolling(raw), "6B:77:4C:09")
        generated = enc.generate_rolling()
        self.assertEqual(len(generated), 4)

    def test_brightness_and_kelvin(self) -> None:
        self.assertEqual(enc.ha_brightness_to_percent(255), 100)
        self.assertEqual(enc.ha_brightness_to_percent(128), 50)
        self.assertEqual(enc.brightness_slider_payload(80), (0, 0x03, 0x20))
        self.assertEqual(enc.brightness_slider_payload(1), (0, 0, 1))
        self.assertEqual(enc.cct_slider_payload(35), (0, 0x01, 0x5E))
        self.assertEqual(enc.kelvin_to_percent(2700), 0)
        self.assertEqual(enc.kelvin_to_percent(6500), 100)
        self.assertEqual(enc.percent_to_kelvin(0), 2700)

    def test_air_frames_match_capture(self) -> None:
        tx0, tx1 = enc.deli_air_frames(
            "on", [0x6B, 0x77, 0x4C, 0x09], 1, 6, timestamp=0xF73D
        )
        self.assertEqual(tx0[:5], bytes([0x02, 0x01, 0x01, 0x1B, 0x03]))
        self.assertEqual(
            tx0[5:],
            enc.uuids_to_payload(DeliCaptureTests.HFKJ_UUIDS),
        )
        self.assertEqual(
            tx1[5:],
            enc.uuids_to_payload(DeliCaptureTests.DLKJ_UUIDS),
        )

    def test_mesh_air_frame_fits_legacy_adv(self) -> None:
        frame = enc.encode_mesh_light("AABBCC", pid=1, power=1)
        air = enc.mesh_air_frame(frame)
        self.assertEqual(air[0], 0x1D)
        self.assertEqual(air[1], 0xFF)
        self.assertEqual(air[2:4], bytes([0x71, 0x0F]))
        self.assertEqual(len(air), 30)
        self.assertEqual(air[4:], frame)


class PairingTests(unittest.TestCase):
    def test_pair_frames_are_b4_aa6655(self) -> None:
        tx0, tx1 = enc.deli_air_frames("pair", [0x6B, 0x77, 0x4C, 0x09], 2, 1)
        for frame in (tx0, tx1):
            decoded = enc.decode_deli_advertise(frame[5:])
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded["cmd"], 0xB4)
            self.assertEqual(decoded["payload"], bytes([0xAA, 0x66, 0x55]))
            self.assertEqual(decoded["dest_id"], 2)
            self.assertEqual(decoded["rolling"], bytes([0x6B, 0x77, 0x4C, 0x09]))

    def test_clear_frames_are_b0(self) -> None:
        for name in ("clear", "delete"):
            tx0, tx1 = enc.deli_air_frames(name, [0x6B, 0x77, 0x4C, 0x09], 2, 1)
            for frame in (tx0, tx1):
                decoded = enc.decode_deli_advertise(frame[5:])
                self.assertIsNotNone(decoded)
                self.assertEqual(decoded["cmd"], 0xB0)
                self.assertEqual(decoded["payload"], bytes([0, 0, 0]))
                self.assertEqual(decoded["dest_id"], 2)

    def test_next_dest_id_cycles_1_to_15(self) -> None:
        from pathlib import Path
        import sys

        cc = str(Path(__file__).resolve().parents[1] / "custom_components" / "rx_lightcontrol")
        if cc not in sys.path:
            sys.path.insert(0, cc)
        from pairing import next_dest_id, rolling_from_entries, used_dest_ids

        self.assertEqual(next_dest_id([]), 1)
        self.assertEqual(next_dest_id([1, 2, 3]), 4)
        self.assertEqual(next_dest_id(range(1, 16)), 1)
        entries = [
            {"protocol": "deli", "rolling": "6B:77:4C:09", "dest_id": 1},
            {"protocol": "deli", "rolling": "6B774C09", "dest_id": 2},
            {"protocol": "mesh", "rolling": "6B:77:4C:09", "dest_id": 3},
        ]
        self.assertEqual(rolling_from_entries(entries), "6B:77:4C:09")
        self.assertEqual(sorted(used_dest_ids(entries, "6b774c09")), [1, 2])
        self.assertEqual(next_dest_id(used_dest_ids(entries, "6B:77:4C:09")), 3)

    def test_pair_burst_records_two_modes(self) -> None:
        import asyncio
        from pathlib import Path
        import sys

        cc = str(Path(__file__).resolve().parents[1] / "custom_components" / "rx_lightcontrol")
        if cc not in sys.path:
            sys.path.insert(0, cc)
        from advertiser import RecordingAdvertiser
        from pairing import (
            assert_clear_frames,
            assert_pair_frames,
            async_send_clear_burst,
            async_send_pair_burst,
        )

        recorder = RecordingAdvertiser()
        rolling = bytes([0x6B, 0x77, 0x4C, 0x09])
        frames = asyncio.run(
            async_send_pair_burst(recorder, rolling, 1, 7, settle_s=0)
        )
        self.assertEqual(len(frames), 2)
        assert_pair_frames(frames, 1, rolling)
        self.assertEqual(len(recorder.bursts), 1)
        self.assertEqual(len(recorder.bursts[0]["frames"]), 2)

        recorder = RecordingAdvertiser()
        frames = asyncio.run(
            async_send_clear_burst(recorder, rolling, 2, 8, settle_s=0)
        )
        assert_clear_frames(frames, 2, rolling)
        decoded = enc.decode_deli_advertise(frames[0][5:])
        self.assertEqual(decoded["cmd"], 0xB0)
        self.assertEqual(decoded["payload"], bytes([0, 0, 0]))


class AdvertiserJsonTests(unittest.TestCase):
    def test_burst_json(self) -> None:
        sys_path = __import__("sys").path
        from pathlib import Path

        cc = str(Path(__file__).resolve().parents[1] / "custom_components" / "rx_lightcontrol")
        if cc not in sys_path:
            sys_path.insert(0, cc)
        from advertiser import RecordingAdvertiser, burst_json
        import asyncio

        payload = burst_json([b"\x02\x01\x01", b"\x1d\xff"], 100, 5)
        self.assertIn('"v":1', payload)
        self.assertIn("020101", payload)
        recorder = RecordingAdvertiser()
        asyncio.run(recorder.async_burst([b"\x01\x02"], 100, 5, "hci0"))
        self.assertEqual(len(recorder.bursts), 1)
        self.assertEqual(recorder.bursts[0]["repeat"], 5)


if __name__ == "__main__":
    unittest.main()
