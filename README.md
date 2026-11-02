# rx_lightcontrol

睿信灯控 App `rx_lightcontrol_v2.5_20251021.apk`（包名 `com.deep.smartmeicontrol`）。

灯控走 BLE **广播**，不写 GATT 特征值。协议说明和命令表：

- [docs/ble-light-protocol.md](docs/ble-light-protocol.md)

生成空中 payload：

```bash
python3 tools/test_ble_light_encode.py
python3 tools/ble_light_encode.py mesh --dev-id AABBCC --power 1 --brightness 80
python3 tools/ble_light_encode.py deli --cmd on --rolling 12:34:56:78 --dest 1 --pid 1
```
