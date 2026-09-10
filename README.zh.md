# pyside6-webbluetooth

🇯🇵 [日本語](README.md) | 🇺🇸 [English](README.en.md) | 🇨🇳 简体中文

⚠️ **v0.0.0a1（Pre-Alpha / 实验性版本）**

一个面向 **PySide6 / QtWebEngine** 应用的 Web Bluetooth API 实现。它向网页提供 `navigator.bluetooth`，实际的 BLE 通信由 [bleak](https://github.com/hbldh/bleak) 负责。本项目实现浏览器侧 API、权限模型、GATT 访问控制以及安全边界。

本项目遵循 [steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) 的理念：研究和验证真实 API 与标准规范，并在保持兼容性的同时进行独立实现和扩展。它是 [Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) 的姊妹项目，也是 **mock-webbluetooth** 框架的第一个实现。

> ⚠️ **Pre-Alpha 版本。** 本项目在 AI 辅助下开发。在将其用于生产环境安全边界或硬件控制之前，请自行审查源代码，并在目标平台上验证实际行为。

## Features

- 兼容 Web Bluetooth 的 `navigator.bluetooth`
- 与真实 BLE 设备通信
- 原生设备选择对话框
- 实时更新的扫描结果
- 按 Origin 持久化设备权限
- 按 Frame 验证 Origin
- GATT Service / Characteristic / Descriptor 访问
- GATT Blocklist
- `requestDevice()` 的主要 Filter 处理
- 类似 `BluetoothUUID` 的 UUID 名称解析
- 不阻塞 Qt UI 线程的异步 BLE Worker
- QtWebEngine / QWebChannel Bridge
- `bleak` 后端
- 实验性的 `PySide6.QtBluetooth` / `QLowEnergyController` 后端

## 安装

```bash
pip install pyside6-webbluetooth
```

要求：

- `Python>=3.10`
- `PySide6>=6.6`
- `bleak>=0.21`

本项目还在 Python 3.14.7 + PySide6 6.11.2 + bleak 3.0.2 环境中完成了构建和验证，**117 项测试全部通过**。

## 快速开始

```python
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from pyside6_webbluetooth import install

app = QApplication([])
view = QWebEngineView()

bridge = install(view.page())

view.load("https://googlechrome.github.io/samples/web-bluetooth/")
view.show()
app.exec()

bridge.shutdown()
```

网页中的 JavaScript 可以按照支持 Web Bluetooth 的浏览器的常见方式使用：

```javascript
const device = await navigator.bluetooth.requestDevice({
  filters: [{ services: ['battery_service'] }],
});

const server = await device.gatt.connect();
const service = await server.getPrimaryService('battery_service');
const characteristic = await service.getCharacteristic('battery_level');
const value = await characteristic.readValue();

console.log('battery:', value.getUint8(0), '%');
```

## 后端

默认使用 `bleak`：

```python
bridge = install(view.page())
```

也可以实验性地使用 PySide6 自带的 `PySide6.QtBluetooth` / `QLowEnergyController`：

```python
bridge = install(view.page(), backend="qtbluetooth")
```

QtBluetooth 后端的连接、GATT Discovery、读写以及通知流程已经通过 Mock 进行测试，但**尚未在真实 BLE 硬件上验证**。正式使用前建议在目标平台进行实际硬件测试。

## 架构

```text
Web page
    │
    │ navigator.bluetooth
    ▼
JavaScript Web Bluetooth Polyfill
    │
    │ QWebChannel
    ▼
BluetoothBridge
    │
    ├── API / 参数验证
    ├── Origin / Frame 验证
    ├── 权限管理
    ├── 设备选择器
    └── GATT Blocklist
    │
    ▼
BLE Worker / asyncio
    │
    ├── bleak 后端
    │      或
    └── QtBluetooth 后端
    │
    ▼
BLE device
```

## 为什么设计不同于 pyside6-webusb

[Mock-APIs](https://github.com/steck0714/Mock-APIs) 强调根据目标环境的限制进行独立实现，而不是简单移植。

USB 与 BLE 的运行特性不同：

- `requestDevice()` 扫描可能需要数百毫秒到数秒
- `gatt.connect()` 同样可能需要较长时间
- `bleak` 基于 asyncio，与 Qt 的事件循环模型不同

因此本项目采用：

- **轻量操作**（`getDevices()`、`forget()` 等）：同步处理
- **`requestDevice()`**：显示模态原生选择器，同时在专用 asyncio 线程中进行实时扫描
- **BLE 操作**（`connect()`、GATT Discovery、read/write/notify）：先同步验证并立即返回 `requestId`，完成结果通过 Qt Signal 异步传递

这样可以避免长时间的 BLE 无线操作阻塞 QtWebEngine UI 线程。

## 安全设计

### 按 Origin 管理权限

通过 `requestDevice()` 选择的设备以及该 Origin 被允许访问的 Service UUID 会使用 `QSettings` 持久化。

如果 Origin 尝试访问未被授权的 Service，将被拒绝并返回 `SecurityError`。

### 按 Frame 验证 Origin

QWebChannel Bridge 可能同时被页面中的多个 Frame 看到，因此不会直接信任 JavaScript 自己报告的 Origin。

每个 Frame 都会获得不可预测的 Token，Python 端维护 **Token → 实际 Origin** 的映射。该机制已经通过包含 iframe 的实际 `QWebEnginePage` 进行了测试。

### GATT Blocklist

本项目实现 [WebBluetoothCG/registries](https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt) 中的 GATT Blocklist。

其中包括 HID、固件更新、FIDO 相关的受保护 GATT 资源，以及部分隐私/配置相关 Characteristic 和 Descriptor。

## Chrome 兼容性

Chrome / Chromium 的 Web Bluetooth 行为以及 Web Bluetooth 规范被作为兼容性参考，但本项目**不是 Chrome 内部实现的直接移植**。

> **Web Bluetooth compatibility ≠ Chrome clone**

API 表面以 Web Bluetooth 兼容性为目标，而内部实现则独立适配 PySide6 / QtWebEngine / Python / BLE 后端。

## 开发与测试

```bash
pip install -e ".[dev]"
pytest
```

测试结合真实 PySide6 / QtWebEngine 环境、无界面 `QWebEnginePage`、iframe、JavaScript 执行，以及 Mock 的 `bleak.BleakClient` / `QLowEnergyController` 后端。

v0.0.0a1 的测试套件已验证 **117 项测试全部通过**。

在没有 BLE 适配器的 CI / 容器环境中，`tests/conftest.py` 可以自动回退到 `QT_QPA_PLATFORM=offscreen`。

## 已知限制

v0.0.0a1 的主要限制：

- `getIncludedService()` / `getIncludedServices()` 返回 `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` 尚未实现
- 独立于 GATT Blocklist 的辅助 Manufacturer Data Blocklist 尚未实现
- notify/indicate 的事件分发使用 `(device, service)` 范围内的 Characteristic UUID，而不是直接使用 GATT Handle
- 尚未实现 Rust/C++ 原生加速
- `backend="qtbluetooth"` 尚未通过真实 BLE 硬件验证

此外，Web Bluetooth 的行为可能因浏览器、操作系统和 BLE Stack 而有所不同。用于实际硬件控制时，请务必在目标操作系统和 BLE 适配器上进行验证。

更多变更、Bug 修复、规范重新验证，以及 Python 3.14 / PySide6 6.11 的验证结果，请参阅 [`CHANGELOG.md`](CHANGELOG.md)。

## 相关项目

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License。请参阅 [`LICENSE`](LICENSE)。
