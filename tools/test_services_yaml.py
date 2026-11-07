#!/usr/bin/env python3
"""services.yaml field names must be strings (YAML on/off/yes/no are booleans)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "custom_components/rx_lightcontrol/services.yaml"


class ServicesYamlTests(unittest.TestCase):
    def test_field_names_are_strings(self) -> None:
        data = yaml.safe_load(SERVICES.read_text())
        self.assertIsInstance(data, dict)
        for service, spec in data.items():
            self.assertIsInstance(service, str, f"service name {service!r}")
            fields = (spec or {}).get("fields") or {}
            for key in fields:
                self.assertIsInstance(
                    key, str, f"expected str at '{service}.fields[{key!r}]'"
                )
        self.assertIn("turn_on", data["group"]["fields"])
        self.assertNotIn(True, data["group"]["fields"])
        self.assertNotIn("on", data["group"]["fields"])

    def test_command_example_is_string(self) -> None:
        data = yaml.safe_load(SERVICES.read_text())
        example = data["send_command"]["fields"]["command"]["example"]
        self.assertIsInstance(example, str)
        self.assertEqual(example, "on")


if __name__ == "__main__":
    unittest.main()
