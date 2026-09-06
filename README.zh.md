# pyside6-webbluetooth

🇯🇵 [日本語](README.ja.md) | 🇺🇸 [English](README.en.md) | 🇨🇳 [简体中文](README.zh.md)

⚠️ **v0.0.0（初版 / Pre-Alpha）**

这是一个面向 **PySide6 / QtWebEngine** 应用的 Web Bluetooth API 实现。实际 BLE 通信由 [bleak](https://github.com/hbldh/bleak) 负责，而本项目负责实现浏览器侧 API、权限模型以及安全边界。

本项目基于 [steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) 的理念开发：研究和验证现有真实 API 与标准规范，在保持兼容性的同时进行独立实现与扩展。它是 [Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) 的姐妹项目，也是 **mock-webbluetooth** 框架中的首个实现。

> ⚠️ **这是初版。** 项目在 AI 辅助下开发。将其作为生产环境安全边界或硬件控制组件使用前，请务必自行审查源代码。

## 主要功能

- 兼容 Web Bluetooth 的 `navigator.bluetooth`
- 实际 BLE 设备通信
- 原生设备选择对话框
- 实时更新的扫描结果
- 按 Origin 持久化设备权限
- 按 Frame 进行 Origin 验证
- GATT Service / Characteristic / Descriptor 访问
- GATT Blocklist 强制检查
- `requestDevice()` 的主要过滤器处理
- 类似 `BluetoothUUID` 的 UUID 名称解析
- 不阻塞 Qt UI 线程的异步 BLE Worker
- QtWebEngine / QWebChannel Bridge

## 安装

```bash
pip install pyside6-webbluetooth
```

依赖：

- `PySide6>=6.6`
- `bleak>=0.21`

## Quick Start

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

网页端 JavaScript 可以按照支持 Web Bluetooth 的浏览器所使用的方式调用 `navigator.bluetooth`：

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

## 架构

```text
网页
    │
    │ navigator.bluetooth
    ▼
JavaScript Web Bluetooth Polyfill
    │
    │ QWebChannel
    ▼
BluetoothBridge
    │
    ├── Origin / Frame 验证
    ├── 权限管理
    ├── 设备选择
    ├── GATT Blocklist
    └── API / 参数验证
    │
    ▼
BLE Worker / asyncio
    │
    │ bleak
    ▼
BLE 设备
```

## 为什么设计不同于 pyside6-webusb

与 [pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) 一样，本项目以 Web API 兼容性为目标，但由于 Bluetooth LE 在时序和并发方面与 USB 不同，内部架构并没有简单照搬。

许多 USB 操作可以在较短时间内完成，而 BLE 扫描、连接和 GATT 探索可能需要数百毫秒甚至数秒。同时，`bleak` 基于 asyncio。

因此本项目采用：

- **轻量操作**（`getDevices()`、`forget()` 等）：同步处理
- **设备选择**（`requestDevice()`）：保持模态选择器，同时在独立 asyncio 线程中运行实时扫描
- **BLE 操作**（`connect()`、GATT 探索、read/write/notify）：同步完成验证并立即返回 `requestId`，实际结果通过 Qt Signal 异步传递

这样可以避免长时间的 BLE 无线操作阻塞 QtWebEngine UI 线程。

## 安全设计

### 按 Origin 管理权限

通过 `requestDevice()` 选择的设备及其允许访问的 Service UUID 会使用 `QSettings` 按 Origin 持久化保存。

如果 Origin 尝试访问没有被授予的 Service，则会以 `SecurityError` 拒绝。

### 按 Frame 验证 Origin

由于 QWebChannel Bridge 可能从页面中的多个 Frame 被访问，因此不会直接信任 JavaScript 自行报告的 Origin。

每个 Frame 都会获得一个独立且难以猜测的 Token，Python 端维护 Token 与真实 Origin 之间的映射。

这一边界已经通过包含 iframe 的实际 `QWebEnginePage` 进行了测试。

### GATT Blocklist

项目强制执行 WebBluetoothCG 的 GATT Blocklist，以阻止访问受保护的 Service、Characteristic 和 Descriptor，包括受保护的 HID、固件更新以及 FIDO 相关资源。

## Chrome 兼容性

Chrome / Chromium 的 Web Bluetooth 行为被作为兼容性参考，但本项目**不是 Chrome 内部实现的直接移植**。

> **Web Bluetooth 兼容 ≠ Chrome 克隆**

本项目以 API 兼容性为目标，同时针对 PySide6 / QtWebEngine / Python / bleak 的运行环境进行独立实现。

## 开发与测试

```bash
pip install -e ".[dev]"
pytest
```

测试结合了真实的 PySide6 / QtWebEngine 环境、离屏 `QWebEnginePage`、iframe、JavaScript 执行，以及通过 `unittest.mock` 模拟的 `bleak.BleakClient`。

在无头环境中，`tests/conftest.py` 可以自动回退到 `QT_QPA_PLATFORM=offscreen`。

## 已知限制

v0.0.0 当前存在以下限制：

- `getIncludedService()` / `getIncludedServices()` 返回 `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` 尚未实现
- 辅助性的 Manufacturer Data Blocklist 尚未实现
- 通知传递通过 `(device, service)` 内的 Characteristic UUID 匹配，而不是 GATT handle
- pyside6-webusb 中使用的 Rust/C++ 原生加速尚未实现

详细信息请参阅 [`CHANGELOG.md`](CHANGELOG.md)。

## 相关项目

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License。请参阅 [`LICENSE`](LICENSE)。
