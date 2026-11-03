# 照明灯：对码过程与控制码

对象：智能模式里的 **照明灯**（`type = 0`，界面 `SmartDeviceZMFragment`）。  
不是家庭 Mesh 类型 `2001` 的 `LightScreen`。

空中接口：手机 BLE **广播** 13 个 16-bit Service UUID，灯扫描学习。没有 GATT，也没有对码应答。

---

## 1. 对码在做什么

对码 = 让灯记住本机的 **4 字节滚码 + 设备号 destId**。之后同一滚码、同一 destId 的控制帧才会被这盏灯执行。

App **不扫描灯、不读回 ACK**。流程是：

1. 用户把灯打到学习态（说明书：上电 / 墙开连续开关等；APK 里没有这段文案，只有「对码中请稍等」）。
2. 手机广播对码帧约 1 秒。
3. App 等 1.5～1.8 秒后把设备写进本地列表，默认当作对码成功。

```
用户点「添加设备」选照明灯
        │
        ▼
┌─────────────────────────────────────┐
│ 若本机还没有滚码 (addrRand==0)      │
│   随机生成 addr1..addr5 并持久化    │
│ 本机所有智能灯共用同一组滚码        │
└─────────────────────────────────────┘
        │
        ▼
  destId = 本机设备序号 1..15 循环
  绑定到 SmartDeviceBean
        │
        ▼
  BleManager.t()
  cmd=0xB4  payload=AA 66 55
  同时打 mode0(HFKJ) + mode1(DLKJ)
  各重复 5 次，每帧约 100 ms
        │
        ▼
  1.5 s 关闭「对码中请稍等」
  1.8 s 把灯写入房间列表
```

入口：

| 操作 | 代码 | 发出的明文 |
| --- | --- | --- |
| 添加照明灯 | `AddDeviceFragment` → `t()` | `0xB4` + `AA 66 55` |
| 菜单再次对码 | `SmartDeviceZMFragment` / `DeviceManagerFragment` → `t()` | 同上 |
| 删除灯 | `e()` 然后本地删记录 | `0xB0` + `00 00 00` |
| 天境灯添加（对照，不是照明灯） | `u()` | `0xB4` + `AA EE 55` |

---

## 2. 身份字段

对码帧和控制帧用同一套身份，native 只吃滚码前 4 字节。

| 字段 | 来源 | 进包位置 |
| --- | --- | --- |
| 滚码 `addr1..addr4` | 本机第一次添加设备时 `127 - Random.nextInt(256)`，之后所有灯共用 | Tx_Payload 偏移 12..15 |
| `addr5` | 同样随机并保存 | **不进 BLE 包** |
| `destId` | `NumData.deviceId`，1→15 再回到 1 | Tx_Payload 偏移 17 |
| `pid` | `BleManager` 内存计数 0..255 | 偏移 10/11，并参与加扰 |

群开/群关例外：`destId` 改成 **255**，payload 清零（首页「全开/全关」）。

同一手机下多盏照明灯：滚码相同，靠 destId 区分。换手机或清数据会重新随机滚码，旧灯必须重新对码。

---

## 3. 对码帧（明文骨架）

`DELI_Pack_Encode` 入参（照明灯添加）：

```
mode      = 0 和 1 各打一次
rolling   = addr1 addr2 addr3 addr4
destId    = 1..15
pid       = 当前计数
cmd       = 0xB4
payload   = AA 66 55
timestamp = System.currentTimeMillis()
```

`cmd == 0xB4` 时**跳过第一轮加扰**，第二轮密钥加扰和 CRC 仍要做。  
Java 丢掉 31 字节里的前 5 字节，把 26 字节按小端拆成 UUID 广播。

编码（会同时给出 HFKJ / DLKJ）：

```bash
python3 tools/ble_light_encode.py deli --cmd pair --rolling 12:34:56:78 --dest 1 --pid 1 --both
```

删除灯发的不是 `AA EE 55`，而是：

```
cmd=0xB0  payload=00 00 00
```

`AA EE 55` 只给「天境灯」添加使用。

广播节奏（`AdvertiseManager`）：

- 队列 = `[mode0, mode1]` × 5 = 10 帧
- 每帧 LOW_LATENCY、TxPower HIGH、可连接、timeout 180 s，实际只挂约 100 ms 就切下一帧
- 「对码中请稍等」对话框 1500 ms 自动关；添加流程再等 300 ms 才写入列表

---

## 4. 照明灯控制码

对码成功后，所有控制都走同一套 DELI 打包，只改 `cmd` + 3 字节 payload。  
界面：`SmartDeviceZMFragment`（type 0）。

### 4.1 基本控制

| 界面 | `cmd` | payload | `BleManager` |
| --- | --- | --- | --- |
| 开灯 `kadeBt` | `0xB3` | `00 00 00` | `s()` |
| 关灯 `gudeBt` | `0xB2` | `00 00 00` | `f()` |
| 全开（首页第 0 项） | `0xB3` | dest=255 | `a(true)` |
| 全关（首页第 1 项） | `0xB2` | dest=255 | `a(false)` |
| 亮度滑条 | `0xB5` | `00 hi lo` 大端。百分数 `≤1` 发原值，`>1` 发 `n×10`（80% → 800 = `03 20`） | `d(v)` |
| 色温滑条 | `0xB7` | `00 hi lo`，发 `n×10` | `A(v)` |
| 定时开 | `0xA5` | `时 分 秒` | `o(h,m,s)` |
| 定时关 | `0xA5` | `00 00 00` | `z()` |
| 小夜灯 `yedeBt` | `0xA1` | 固定 `19 19 00`（开关发同一包，灯端自翻） | `r()` |
| 分段 `feduBt` | `0xA7` | `n 00 00`，n 在 1/2/3 循环 | `B(n)` |
| 辅光 `fuguBt` | `0xA6` | `(0/1)+1, 00, 01` → `01 00 01` 或 `02 00 01` | `c(n)` |
| 节能半亮 `jieneBt` | `0xB5` | 开：`01 F4`（500）；关：`03 E8`（1000） | `d(500/1000)` |
| 删除 | `0xB0` | `00 00 00` | `e()` |

### 4.2 场景码（仍是 `cmd=0xA7`）

和「分段 1/2/3」共用指令字节，靠 payload 第一字节区分。

| 按钮（布局 id） | 名称 | payload |
| --- | --- | --- |
| `wexiBt` | 温馨 | `A1 00 00`（161） |
| `yiyuBt` | （布局 yiyu） | `A2 00 00`（162） |
| `yuduBt` | 阅读 | `A3 00 00`（163） |
| `hukeBt` | 会客 | `A4 00 00`（164） |
| `riguBt` | 日光 | `C1 00 00`（193） |
| `yuguBt` | 月光 | `C2 00 00`（194） |
| `xixiBt` | （布局 xixi） | `C3 00 00`（195） |
| `yuleBt` | 娱乐 | `C4 00 00`（196） |

### 4.3 亮度+色温组合（先发亮度，500 ms 后再发色温）

| 按钮 | 名称 | 第一包 `0xB5` | 第二包 `0xB7` |
| --- | --- | --- | --- |
| `dinaBt` | 全亮 | `d(100)` → `00 64` | `A(100)` → `00 64` |
| `bagoBt` | 办公 | `d(100)` → `00 64` | `A(70)` → `00 46` |
| `huyaBt` | 护眼 | `d(90)` → `03 84`（90×10） | `A(35)` → `01 5E`（35×10） |
| `ertoBt` | 儿童 | `d(50)` → `01 F4`（50×10） | `A(35)` → `01 5E` |

注意：`d(100)` 的 100>1，按滑条规则应是 ×10，但预设场景里办公/全亮写的是 `d(100)` 不是 `d(100*10)`，和滑条 100%（发 1000）不一致。以源码为准。

### 4.4 生成控制帧

```bash
# 开 / 关
python3 tools/ble_light_encode.py deli --cmd on  --rolling 12:34:56:78 --dest 1 --pid 2 --both
python3 tools/ble_light_encode.py deli --cmd off --rolling 12:34:56:78 --dest 1 --pid 3 --both

# 亮度 80%、色温 40%
python3 tools/ble_light_encode.py deli --rolling 12:34:56:78 --dest 1 --pid 4 --brightness 80
python3 tools/ble_light_encode.py deli --rolling 12:34:56:78 --dest 1 --pid 5 --cct 40

# 阅读场景
python3 tools/ble_light_encode.py deli --cmd scene_code --rolling 12:34:56:78 --dest 1 --pid 6 --p0 163
```

加扰、密钥、CRC 见 [ble-light-protocol.md](ble-light-protocol.md) 第 2 节。

---

## 5. 源码对照

| 步骤 | 位置 |
| --- | --- |
| 选「照明灯」type=0 | `AddDeviceFragment.mainInit` |
| 生成滚码 / destId | 同上 `c.onClick` |
| 打开照明灯页 | `SmartRoomChildFragment` `type==0` → `SmartDeviceZMFragment` |
| 对码发送 | `f.c.c.b.b.t()` |
| 删除发送 | `f.c.c.b.b.e()` |
| 指令表 | `f.c.c.a.a`（`DLKeyCode`） |
| 双模式打包 | `f.c.c.i.a`（`MsgGenerator`） |
| 加扰 | `libmsglibrary.so` `DELI_Pack_Encode` |
| UUID 广播 | `f.c.c.b.a`（`AdvertiseManager`） |
| 滚码随机 | `f.d.a.a.a.a.b()` = `(byte)(127 - nextInt(256))` |

---

## 6. 实采：开灯动作（nRF / Scanner 13 UUID）

扫描器里每条记录的「Services: WXYZ, …」不是 GATT 服务，而是 App 把 26 字节广播拆成的 13 个 16-bit UUID。转换：

```
扫描器显示 WXYZ  →  payload 两个字节 [YZ, WX]
```

所以 `4648,4A4B` = `48 46 4B 4A` = ASCII `HFKJ`（mode 0）；`4C44,4A4B` = `44 4C 4B 4A` = `DLKJ`（mode 1）。  
彩色 Bluetooth 图标、设备名 `N/A`、`Connect` 按钮都和灯控无关：本机没有 Local Name，控制也不走连接。

同一条「开灯」会交替打出两套 UUID（`MsgGenerator` 同时编 mode0+mode1，队列 ×5）。一次捕获里出现的两组是：

| 扫描器 UUID（13 个） | 头 | 26 字节 payload |
| --- | --- | --- |
| `4648,4A4B,87E1,DC4B,B674,B325,EAD0,BCB9,17B1,A4C5,1110,1312,1514` | HFKJ | `48 46 4B 4A E1 87 4B DC 74 B6 25 B3 D0 EA B9 BC B1 17 C5 A4 10 11 12 13 14 15` |
| `4C44,4A4B,9795,7C2C,C000,DF55,E636,E22C,0094,1DE0,3DAA,13F7,1514` | DLKJ | `44 4C 4B 4A 95 97 2C 7C 00 C0 55 DF 36 E6 2C E2 94 00 E0 1D AA 3D F7 13 14 15` |

解开加扰后两组明文相同：

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `cmd` | `0xB3` | 开灯（`BleManager.s()` / `kadeBt`） |
| `payload` | `00 00 00` | 开灯固定空参 |
| `destId` | `1` | 本机第 1 盏照明灯，不是群控 255 |
| 滚码 | `6B 77 4C 09` | 本机 `addr1..addr4` |
| `pid` | `6` | 当时的包序号 |
| mode1 时间戳 | `0xF73D`（63293） | 只写在 DLKJ 包尾 `AA 3D F7 13 14 15` |

用扫描器粘贴直接解：

```bash
python3 tools/ble_light_encode.py deli-decode \
  4648,4A4B,87E1,DC4B,B674,B325,EAD0,BCB9,17B1,A4C5,1110,1312,1514
```

重放同一条开灯（滚码/dest/pid 必须和灯里记下的一致）：

```bash
python3 tools/ble_light_encode.py deli --cmd on \
  --rolling 6B:77:4C:09 --dest 1 --pid 6 --timestamp 0xF73D --both
```
