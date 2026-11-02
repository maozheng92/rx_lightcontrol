# 睿信灯控 APK 蓝牙命令

APK：`rx_lightcontrol_v2.5_20251021.apk`  
包名：`com.deep.smartmeicontrol`  
版本：v2.5（2025-10-21）

应用**不用 GATT 写特征值**控制灯。两条产品线都是手机做 BLE **广播**，灯扫描广播后执行命令。

| 产品线 | UI | 空中接口 | 明文命令 |
| --- | --- | --- | --- |
| 家庭 Mesh 灯（设备类型 `2001`） | `LightScreen` | Manufacturer Specific Data，公司 ID **0x0F71**（3953） | 19 字节 DPS，头 `0x77` |
| 智能「照明灯」（风扇灯 / 吸顶灯） | `SmartDeviceZMFragment` 等 | 13 个 16-bit Service UUID | DELI 指令字节 `0xB2`/`0xB3`/… |

编码实现：[`tools/ble_light_encode.py`](../tools/ble_light_encode.py)。

```bash
python3 tools/ble_light_encode.py mesh --dev-id AABBCC --power 1 --brightness 80
python3 tools/ble_light_encode.py deli --cmd on --rolling 12:34:56:78 --dest 1 --pid 1
```

---

## 1. 家庭 Mesh 灯（类型 2001）

入口：`LightScreen` → `BaseOperationScreen.h()` → `DeviceComponent.b()` → `startDataSendDiyNoFan()`。

`parameter["communication"]`：

- `0`：只走 HTTP，不发蓝牙
- `1` / `2`，或家庭模式 `HomeBeanNumberMode == 1`：发下面的广播

广播参数（`f.i.c.d`）：不可连接、TxPower HIGH、timeout 0。

### 1.1 空中帧（26 字节）

AD 结构：`Manufacturer Specific Data`，公司 ID **3953 = 0x0F71**（小端 `71 0F`），后面 26 字节：

| 偏移 | 长度 | 含义 |
| --- | --- | --- |
| 0 | 1 | 固定 `0x65`（101） |
| 1 | 1 | 固定 `0x88`（136） |
| 2..4 | 3 | 目标设备 ID 前 6 个十六进制字符 |
| 5 | 1 | 滚动包序号 `pid`，0..255 循环 |
| 6 | 1 | CRC-16/CCITT-FALSE 的低 8 位（计算时本字节当 0） |
| 7..25 | 19 | 加密后的 DPS |

设备 ID 不够 6 个 hex 字符时，分享设备会退回 `000000`。

### 1.2 明文 DPS（类型 2001，19 字节）

`BaseOperationScreen.f()`，头字节 `0x77`：

| 偏移 | 字段 | 取值 |
| --- | --- | --- |
| 0 | 命令头 | `0x77` |
| 1 | 标志 | `1` |
| 2 | `power` | `0` 关 / `1` 开 |
| 3 | `brightness` | `1`..`100`（滑条 0 会被写成 1） |
| 4 | `temperature` | `0`..`100`，界面色温 K = `value * 33 + 2700` |
| 5 | `auxiliaryLight` | `0` / `1` 夜灯 |
| 6 | `red` | `0`..`255`（色盘 0..100 × 2.55） |
| 7 | `green` | 同上 |
| 8 | `blue` | 同上 |
| 9 | `shutdownTimeState` | `0` 关定时 |
| 10 | 定时 时 | 剩余秒数换算 |
| 11 | 定时 分 | |
| 12 | 定时 秒 | |
| 13..18 | 填充 | `0` |

类型 `2002`（艺术灯 / `ArticleLampScreen`）同一条发送路径，明文里多 `cmd` / `param`；JADX 对中间字节有寄存器复用，不要当精确布局。

灯的每一次操作（开关、亮度、色温、RGB、夜灯）都是**整包重发当前 DPS**，不是单字节 opcode。

### 1.3 加密（必须按顺序）

源码：`f.c.b.c.b` + `f.c.b.i.a`（`CRC16Util`）。

1. 19 字节明文每个字节 `+= pid`（8 位溢出）
2. 用密钥 `37` 初始化 7 bit LFSR（`b[0]` 强制为 1，其余为 key 的 bit5..bit0）
3. 每个字节从 LSB 开始：`out_bit = in_bit XOR lfsr()`
4. LFSR 步进：`t = s[3] XOR s[6]`，然后  
   `s[3]=s[2], s[2]=s[1], s[1]=s[0], s[0]=s[6], s[6]=s[5], s[5]=s[4], s[4]=t`，输出新的 `s[0]`
5. 对每个密文字节做**逆 S 盒**：在 256 元表里找下标（表见 `tools/ble_light_encode.py` 的 `MESH_SBOX`）
6. 整帧 26 字节（偏移 6 仍为 0）做 CRC-16/CCITT-FALSE（poly `0x1021`，初值 `0xFFFF`），低 8 位写入偏移 6

S 盒不是 AES S-box，是 APK 里一张自定义置换表。

### 1.4 开灯 / 关灯示例（编码前）

设备 `AABBCC`，`pid=1`，亮度 80，色温 40，RGB 默认：

```
77 01 01 50 28 00 34 8A FF 00 00 00 00 00 00 00 00 00 00   # 开
77 01 00 50 28 00 34 8A FF 00 00 00 00 00 00 00 00 00 00   # 关
```

上空气用 `python3 tools/ble_light_encode.py mesh ...` 生成。

---

## 2. 智能照明灯（DELI 滚码广播）

入口：`BleManager`（`f.c.c.b.b`）→ `MsgGenerator` → native `DELI_Pack_Encode`（`libmsglibrary.so`）→ `AdvertiseManager`。

每一条命令打 **两帧**（mode 0 `HFKJ` + mode 1 `DLKJ`），各重复 5 次，每帧广播约 100 ms。  
广播：可连接、LOW_LATENCY、TxPower HIGH、timeout 180 s。  
26 字节按小端拆成 13 个 16-bit Service UUID：

```
UUID = 0000{b[i+1]:02x}{b[i]:02x}-0000-1000-8000-00805f9b34fb
```

### 2.1 Native 入参

```c
void DELI_Pack_Encode(
    int mode,          // 0=HFKJ, 1=DLKJ
    uint8_t rolling[4],// 设备滚码 addr1..addr4（addr5 未进包）
    int destId,        // 设备号 0..255；群控开/关时改为 255
    int pid,           // 滚动序号 0..255
    int cmd,           // 见下表
    uint8_t payload[3],
    int timestamp,     // 毫秒时间戳，mode 1 写入包尾
    uint8_t out[31]
);
```

Java 丢掉 `out[0..4]`，广播 `out[5..30]`。

### 2.2 31 字节 Tx_Payload（加扰前骨架）

| 偏移 | mode 0 | mode 1 |
| --- | --- | --- |
| 0..4 | `02 01 01 1B 03` | 同左 |
| 5..8 | `HFKJ` | `DLKJ` |
| 9 | `FF` | `FF` |
| 10 | `pid+1` | `pid+1` |
| 11 | `pid` | `pid` |
| 12..15 | 滚码 4 字节 | 同左 |
| 16 | `cmd` | `cmd` |
| 17 | `destId` | `destId` |
| 18..22 | 见加扰 | 见加扰 |
| 23..24 | CRC16 | CRC16 |
| 25..30 | `10 11 12 13 14 15` | `AA` + timestamp 小端 16 bit + `13 14 15` |

密钥（`libmsglibrary.so` rodata）：

```
SELFKEY1 (mode 0): 1d 04 11 20 98 75 28 46 0b af 43 ac d6 be 89 8e
SELFKEY2 (mode 1): 08 d1 11 02 57 89 82 64 b0 fa 34 ca 6d ed 98 e8
```

`nibble(x) = (x & 0x0F) ^ ((x & 0xF0) >> 4)`

`cmd != 0xB4` 时的第一轮（`k = KEY[nibble(pid)]`）：

| 字节 | mode 0 | mode 1 |
| --- | --- | --- |
| 18 | `0x42 - k` | `0x33 - k` |
| 19 | `(k ^ pid) + 0xAC` | `(k ^ pid) + 0xCA` |
| 20 | `(p0 ^ k) + 0xD6` | `(p0 ^ k) + 0x6D` |
| 21 | `(p1 ^ k) + 0xBE` | `(p1 ^ k) + 0xED` |
| 22 | `(p2 ^ k) + 0x89` | `(p2 ^ k) + 0x98` |

`cmd == 0xB4`（配对）跳过这一轮，18..22 保持 `FF pid p0 p1 p2`。

CRC：CRC-16/XMODEM（poly `0x1021`，初值 0）对 `[0xFF] + buf[10:22]`，小端写入偏移 23。

第二轮，`k2 = KEY[nibble(pid+1)]`：

- mode 0：`i = 0..15`，`buf[9+i] = (buf[9+i] ^ k2) + KEY[(i+6) & 0xF]`
- mode 1：`i = 9..24`，`buf[i] = (buf[i] ^ k2) + KEY[i & 0xF]`

### 2.3 灯相关指令字节（加扰前）

来源：`f.c.c.a.a`（`DLKeyCode`）+ `BleManager` + `SmartDeviceZMFragment`。

| 功能 | `cmd` | payload `p0 p1 p2` | 调用 |
| --- | --- | --- | --- |
| 开灯 | `0xB3` | `00 00 00` | `s()` |
| 关灯 | `0xB2` | `00 00 00` | `f()` |
| 群开 | `0xB3` | dest=255，payload 被置 0 | `a(true)` |
| 群关 | `0xB2` | dest=255 | `a(false)` |
| 亮度 | `0xB5` | `00 hi lo`，16-bit 大端。界面百分数 `>1` 时发 `percent*10`，`≤1` 发原值 | `d(v)` |
| 色温 | `0xB7` | `00 hi lo`，发 `percent*10` | `A(v)` |
| RGB | `0xCA` | `R G B`（界面常用 0..250） | `v(r,g,b)` |
| RGB 开 | `0xC1` | `00 00 00` | `w(1)` |
| RGB 关 | `0xC0` | `00 00 00` | `w(其他)` |
| 夜灯亮度 | `0xA1` | `19 19 00`（固定 25,25） | `r()` |
| 夜灯关 | `0xD1` | `00 00 00` | `i(false)` |
| 场景 1..6 | `0xD3` | `n 00 00` | `h(n)` |
| 夜灯开（场景 3） | `0xD3` | `03 00 00` | `i(true)` |
| 辅灯开 / 关 | `0xA6` | `01 00 00` / `02 00 00` | `m(true/false)` |
| 风扇灯亮度档 | `0xA6` | `(fLight+1) 00 01` | `c(n)` |
| 定时 | `0xA5` | `H M S` | `o(h,m,s)` |
| 取消定时 | `0xA5` | `00 00 00` | `z()` |
| 查询 / 同步 | `0xB0` | `00 00 00` | `e()` |
| 配对 | `0xB4` | `AA 66 55` | `t()` |
| 解配 | `0xB4` | `AA EE 55` | `u()` |
| 风扇档 | `0xA7` | `n 00 00`（1..4 或 161..164、193..196 场景风） | `B(n)` |
| 动态模式 | `0xCB` | `n 00 00` | `p(n)` |
| 动态速度 | `0xC9` | `00 00 n` | `n(n)` |
| 停动态 | `0xDB` | `00 00 00` | `D()` |
| 音乐关 / 开 | `0xD9` / `0xDA` | `00 00 00` | `k(0/1)` |

预设场景（先亮度再色温，有的会再改 RGB）：

| 场景 | 亮度参数 | 色温参数 | 备注 |
| --- | --- | --- | --- |
| 阅读 / 明亮 | `d(100)` | `A(70)` | |
| 夜灯 / 暖 | `d(90)` 或 `d(50)` | `A(35)` | |
| 会客 | `d(100)` | `A(100)` | |
| 影视（HC） | `d(500)` 或 `d(1000)` | | `FileSizeUnit.ACCURATE_KB = 1000` |

色盘快捷色（`v()`）：红 `250,0,0`，绿 `0,250,0`，蓝 `0,0,250`，黄 `250,250,0`，青 `0,250,250`，紫 `250,0,250`，橙 `255,128,0`，粉 `255,192,203`。

### 2.4 开灯明文骨架（加扰前，mode 0）

滚码 `12 34 56 78`，`dest=1`，`pid=1`，`cmd=0xB3`：

```
02 01 01 1B 03 48 46 4B 4A FF 02 01 12 34 56 78 B3 01 ... CRC ... 10 11 12 13 14 15
           ^^^^^ HFKJ
```

完整密文用编码器生成；不要只广播上面这行。

---

## 3. 源码对照

| 内容 | 混淆类 / 符号 |
| --- | --- |
| Mesh 组帧 | `f.c.b.c.b`（`DeviceComponent`） |
| Mesh 加密 / CRC / S 盒 | `f.c.b.i.a` |
| Mesh 广播 | `f.i.c.a.startDataSendDiyNoFan` → company id 3953 |
| 2001 DPS | `com.deep.smartruixin.screen.base.BaseOperationScreen.f()` |
| DELI 指令表 | `f.c.c.a.a`（`DLKeyCode`） |
| DELI 发送 | `f.c.c.b.b`（`BleManager`） |
| DELI 打包 | `Java_com_deep_smartruixin_ble_SendUtil_DELIPackEncode` |
| DELI 密钥 | `libmsglibrary.so` `SELFKEY1` / `SELFKEY2` |
| DELI 广播 | `f.c.c.b.a`（`AdvertiseManager`）把 26 字节变成 Service UUID |

JNI 自测向量（`SendUtil.a()`，只打 mode 0，不发送）：

```
mode=0, rolling={0x12,0x34,0x56,0x78}, dest=255, pid=1, cmd=17,
payload={0x22,0x33,0x44}, timestamp=4660
```

---

## 4. 怎么发到灯上

灯听的是 **BLE advertise**，不是连接后写 GATT。

- Mesh：Manufacturer Data，公司 ID `0x0F71`，26 字节 payload  
- DELI：同一 26 字节拆成 13 个 16-bit UUID，mode 0 和 mode 1 都要发  

手机侧需要 `BLUETOOTH_ADVERTISE`（以及旧系统的 `BLUETOOTH_ADMIN`）。同一条命令 App 会连发多次；灯按滚码 / 设备 ID / `pid` 去重。
