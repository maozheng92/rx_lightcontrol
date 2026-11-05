"""Constants for the 睿信灯控 Home Assistant integration."""

from __future__ import annotations

DOMAIN = "rx_lightcontrol"
PLATFORMS_DELI = ("light", "button", "select", "switch")
PLATFORMS_MESH = ("light", "select", "switch")
POWER_ENTITY_BLE = "ble"

def signal_light_state(entry_id: str) -> str:
    return f"{DOMAIN}_state_{entry_id}"

PROTOCOL_DELI = "deli"
PROTOCOL_MESH = "mesh"

CONF_PROTOCOL = "protocol"
CONF_ROLLING = "rolling"
CONF_DEST_ID = "dest_id"
CONF_DEV_ID = "dev_id"
CONF_TRANSMIT = "transmit"
CONF_ADAPTER = "adapter"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_REPEAT = "repeat"
CONF_INTERVAL_MS = "interval_ms"
CONF_MIN_KELVIN = "min_kelvin"
CONF_MAX_KELVIN = "max_kelvin"
CONF_PAIR_NOW = "pair_now"
CONF_UNPAIR_NOW = "unpair_now"
CONF_POWER_ENTITY = "power_entity"
CONF_POWER_BLE_FOLLOW = "power_ble_follow"
CONF_SETUP_MODE = "setup_mode"

SETUP_PAIR_NEW = "pair_new"
SETUP_ALREADY_PAIRED = "already_paired"

TRANSMIT_LOCAL = "local"
TRANSMIT_MQTT = "mqtt"

SERVICE_PAIR = "pair"
SERVICE_UNPAIR = "unpair"

DEFAULT_REPEAT = 5
DEFAULT_INTERVAL_MS = 100
DEFAULT_MQTT_TOPIC = "rx_lightcontrol/advertise"
DEFAULT_MIN_KELVIN = 2700
DEFAULT_MAX_KELVIN = 6500
DEFAULT_DEST_ID = 1
POWER_SETTLE_SECONDS = 0.6
DEFAULT_POWER_BLE_FOLLOW = True

MANUFACTURER = "睿信"
MODEL_DELI = "照明灯"
MODEL_MESH = "家庭 Mesh 灯"

ATTR_COMMAND = "command"
ATTR_P0 = "p0"
ATTR_P1 = "p1"
ATTR_P2 = "p2"
ATTR_HOURS = "hours"
ATTR_MINUTES = "minutes"
ATTR_SECONDS = "seconds"
ATTR_GROUP = "group"
ATTR_REMOVE_ENTRY = "remove_entry"

SERVICE_SEND_COMMAND = "send_command"
SERVICE_TIMER = "timer"
SERVICE_GROUP = "group"

# App scene / preset names shown as light effects.
EFFECT_OFF = "off"
DELI_EFFECTS = {
    "温馨": ("scene_code", (0xA1, 0, 0)),
    "阅读": ("scene_code", (0xA3, 0, 0)),
    "会客": ("scene_code", (0xA4, 0, 0)),
    "日光": ("scene_code", (0xC1, 0, 0)),
    "月光": ("scene_code", (0xC2, 0, 0)),
    "娱乐": ("scene_code", (0xC4, 0, 0)),
    "小夜灯": ("night", (25, 25, 0)),
    "分段1": ("scene_code", (1, 0, 0)),
    "分段2": ("scene_code", (2, 0, 0)),
    "分段3": ("scene_code", (3, 0, 0)),
}

# Presets: (percent, use_slider_x10) for brightness then CCT, 500 ms apart.
DELI_PRESETS = {
    "全亮": ((100, False), (100, False)),
    "办公": ((100, False), (70, False)),
    "护眼": ((90, True), (35, True)),
    "儿童": ((50, True), (35, True)),
}
