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

    def test_dps_layout(self) -> None:
        dps = enc.build_light_dps(
            power=1, brightness=80, temperature=40, auxiliary=1, red=10, green=20, blue=30
        )
        self.assertEqual(dps[0], 0x77)
        self.assertEqual(list(dps[1:9]), [1, 1, 80, 40, 1, 10, 20, 30])
        self.assertEqual(len(dps), 19)


if __name__ == "__main__":
    unittest.main()
