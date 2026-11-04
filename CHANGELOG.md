# Changelog

## 1.1.0 — 2026-11-04

首个正式 GitHub Release。

### 图标

- 仓库根目录增加 `icon.png`（512×512），供 HACS 和 README 使用。

### Home Assistant

- 自定义集成 `custom_components/rx_lightcontrol`
- 照明灯（DELI / HFKJ+DLKJ）对码向导、删除配对、开/关/亮度/色温/场景
- 家庭 Mesh 灯（厂家数据 `0x0F71`）
- 本机蓝牙广播，或 MQTT 远程广播器

### 协议

- 照明灯对码：`0xB4 AA 66 55`
- 照明灯删除配对：`0xB0 00 00 00`
- 开/关：`0xB3` / `0xB2`
- 编码器与解码 CLI：`tools/ble_light_encode.py`
