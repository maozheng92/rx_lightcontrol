<p align="center">
  <img src="icon.png" width="128" height="128" alt="睿信灯控">
</p>

<h1 align="center">睿信灯控</h1>

<p align="center">
  Home Assistant 自定义集成 · BLE 广播控灯 · HACS
</p>

睿信灯控 App `rx_lightcontrol_v2.5_20251021.apk`（包名 `com.deep.smartmeicontrol`）。

灯控走 BLE **广播**，不写 GATT 特征值。协议说明和命令表：

- [照明灯对码过程与控制码](docs/zm-light-pairing-and-control.md)
- [两套 BLE 广播协议总表](docs/ble-light-protocol.md)
- [Home Assistant 安装与对码](docs/homeassistant.md)

当前正式版：**v1.1.1**

生成空中 payload：

```bash
python3 tools/test_ble_light_encode.py
python3 tools/ble_light_encode.py deli --cmd pair --rolling 12:34:56:78 --dest 1 --pid 1 --both
python3 tools/ble_light_encode.py deli --cmd clear --rolling 12:34:56:78 --dest 1 --pid 1 --both
python3 tools/ble_light_encode.py deli --cmd on --rolling 12:34:56:78 --dest 1 --pid 2 --both
python3 tools/ble_light_encode.py deli-decode 4648,4A4B,87E1,DC4B,B674,B325,EAD0,BCB9,17B1,A4C5,1110,1312,1514
python3 tools/ble_light_encode.py mesh --dev-id AABBCC --power 1 --brightness 80
```

## Home Assistant

自定义集成：[`custom_components/rx_lightcontrol`](custom_components/rx_lightcontrol)

### HACS

1. HACS → 自定义仓库 → 添加 `https://github.com/maozheng92/rx_lightcontrol`，类型选 **Integration**
2. 搜索 **睿信灯控** 并下载
3. 重启 Home Assistant
4. **设置 → 设备与服务 → 添加集成 → 睿信灯控**

也可以手动复制 `custom_components/rx_lightcontrol` 到 `config/custom_components/rx_lightcontrol/` 后重启。

灯控仍然是 BLE 广播：HA 主机要有蓝牙适配器，或用 MQTT 把帧交给 [`tools/mqtt_ble_advertiser.py`](tools/mqtt_ble_advertiser.py) / [`esphome/rx_light_advertiser.yaml`](esphome/rx_light_advertiser.yaml)。普通 ESPHome `bluetooth_proxy` 只能扫描，不能发广播。
