#!/usr/bin/env python3
"""Subscribe to HA burst JSON and put the frames on air via raw HCI.

Install next to the lamp (Raspberry Pi with a USB Bluetooth adapter):

    pip install paho-mqtt
    python3 tools/mqtt_ble_advertiser.py --host homeassistant.local --adapter hci0

Home Assistant → MQTT topic (default rx_lightcontrol/advertise) → this process.

Needs CAP_NET_RAW / root on many systems, and BlueZ must allow HCI while
powered. If HCI fails, stop bluetoothd scanning or use a dedicated adapter.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_CC = Path(__file__).resolve().parents[1] / "custom_components" / "rx_lightcontrol"
if str(_CC) not in sys.path:
    sys.path.insert(0, str(_CC))

from advertiser import hci_burst  # noqa: E402

_LOGGER = logging.getLogger("mqtt_ble_advertiser")


def _handle(payload: bytes, adapter: str) -> None:
    data = json.loads(payload.decode("utf-8"))
    frames = [bytes.fromhex(item) for item in data["frames"]]
    interval = int(data.get("interval_ms", 100))
    repeat = int(data.get("repeat", 5))
    _LOGGER.info("Advertising %s frames ×%s (%s ms) on %s", len(frames), repeat, interval, adapter)
    hci_burst(frames, interval, repeat, adapter)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--topic", default="rx_lightcontrol/advertise")
    parser.add_argument("--adapter", default="hci0")
    parser.add_argument("--client-id", default="rx-lightcontrol-advertiser")
    args = parser.parse_args(argv)

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("pip install paho-mqtt", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    def on_connect(client, _userdata, _flags, reason_code, _properties=None):
        rc = reason_code if isinstance(reason_code, int) else getattr(reason_code, "value", reason_code)
        _LOGGER.info("MQTT connected rc=%s, subscribe %s", rc, args.topic)
        client.subscribe(args.topic)

    def on_message(_client, _userdata, msg):
        try:
            _handle(msg.payload, args.adapter)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to advertise MQTT payload")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.client_id)
    except AttributeError:
        client = mqtt.Client(client_id=args.client_id)
    if args.username:
        client.username_pw_set(args.username, args.password)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, 60)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
