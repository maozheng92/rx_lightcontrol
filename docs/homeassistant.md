# Home Assistant 集成：睿信灯控

自定义集成目录：[`custom_components/rx_lightcontrol`](../custom_components/rx_lightcontrol)。

灯只听 **BLE 广播**，没有状态回读。实体是 `assumed_state`：HA 显示的是上次发出去的命令。

这和 [ha-ble-adv](https://github.com/NicoIIT/ha-ble-adv) 里的「RuiXin」编码不是同一套协议。那边是 `RXK` 短包；本仓库对应 App `com.deep.smartmeicontrol` 的 **HFKJ/DLKJ（DELI）** 和家庭 Mesh `0x0F71`。

---

## 1. 安装

把整个 `custom_components/rx_lightcontrol` 目录复制到 Home Assistant 的 `config/custom_components/rx_lightcontrol/`，重启，然后：

**设置 → 设备与服务 → 添加集成 → 睿信灯控**

也可以用 HACS 以自定义仓库方式加入本 GitHub 仓库。

最低：Home Assistant 2024.8。主机需要：

- **本机蓝牙适配器**（HAOS 上的 USB/内置蓝牙，不是 VM 里没透传的那个），或
- **MQTT + 灯旁边的广播器**（[`tools/mqtt_ble_advertiser.py`](../tools/mqtt_ble_advertiser.py) 或 [`esphome/rx_light_advertiser.yaml`](../esphome/rx_light_advertiser.yaml)）

普通 ESPHome `bluetooth_proxy` **只能扫描，不能发广播**，控不了这些灯。

---

## 2. 添加照明灯（DELI）

有两种身份来源，不要混用：

| 方式 | 做法 | 结果 |
| --- | --- | --- |
| **沿用手机 App** | 滚码填扫描器解出来的值（例如开灯捕获的 `6B:77:4C:09`），destId 填 1 | 手机和 HA 共用同一组滚码，都能控 |
| **HA 独立对码** | 滚码留空（按 App 规则新生成），灯打到学习态，勾选「现在发送对码」 | 灯记住 HA 的滚码；手机 App 旧滚码会失效，需重新对码 |

`destId` 一般是 1–15。255 是 App 首页「全开/全关」。

发送方式：

1. **本机蓝牙适配器**：选 `hci0` 等。集成先走 raw HCI（31 字节原样，和手机一致），失败再退到 BlueZ 的 13 个 Service UUID。
2. **MQTT**：HA 往主题（默认 `rx_lightcontrol/advertise`）发 JSON，由旁边的 Linux/ESP32 真正广播。

广播节奏与 App 相同：HFKJ + DLKJ，各重复 5 次，每帧约 100 ms。

---

## 3. 实体

| 实体 | 作用 |
| --- | --- |
| `light.*` | 开/关（`0xB3` / `0xB2`）、亮度（`0xB5`，滑条百分数 ×10）、色温（`0xB7`，百分数 ×10） |
| 效果 | 温馨/阅读/会客/日光/月光/娱乐、小夜灯、分段 1–3、全亮/办公/护眼/儿童 |
| `button.*_pair` | 再发对码 `0xB4 AA 66 55` |
| `button.*_night` | 小夜灯 `0xA1 19 19 00`（灯端自翻） |

家庭 Mesh（类型 2001）只有灯实体：开关、亮度 1–100、色温、RGB。整包 DPS 重发，公司 ID `0x0F71`。

服务：

- `rx_lightcontrol.send_command` — 任意 DELI 命令名 + 三字节 payload
- `rx_lightcontrol.timer` — `0xA5` 时/分/秒，全 0 取消
- `rx_lightcontrol.group` — destId 255 全开/全关

---

## 4. MQTT 广播器（推荐给没有主机蓝牙的 HA）

HA 发布：

```json
{"v":1,"interval_ms":100,"repeat":5,"frames":["0201011b03…31字节hex","…"]}
```

灯旁边的树莓派：

```bash
pip install paho-mqtt
python3 tools/mqtt_ble_advertiser.py --host homeassistant.local --adapter hci0 \
  --username mqtt --password secret
```

需要能发 HCI 命令（常见要 root / `CAP_NET_RAW`）。ESP32 则刷 `esphome/rx_light_advertiser.yaml`。

---

## 5. 用扫描器填滚码

手机 App 开一次灯，在 nRF Connect / Scanner 里复制 13 个 UUID，在本仓库解码：

```bash
python3 tools/ble_light_encode.py deli-decode \
  4648,4A4B,87E1,DC4B,B674,B325,EAD0,BCB9,17B1,A4C5,1110,1312,1514
```

把输出的 `rolling` 和 `dest_id` 填进集成。不要勾选「现在发送对码」，否则会覆盖灯里已有的滚码。

---

## 6. 限制

- 没有状态回读；墙开、遥控器、手机 App 改灯后，HA 不会自动变。
- 本机 HCI 和 BlueZ 扫描可能抢适配器。若打不开灯，改用 MQTT 专用适配器，或暂时关掉其它占用。
- Mesh 厂家数据加 flags 会超过 31 字节，因此 mesh 帧不带 flags，只发 Manufacturer Specific。
- 色温 Kelvin 范围默认 2700–6500，只是 UI 映射；DELI 协议本身是 0–100。
