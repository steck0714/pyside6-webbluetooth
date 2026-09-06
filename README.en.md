# pyside6-webbluetooth

🇯🇵 [日本語](README.ja.md) | 🇺🇸 [English](README.en.md) | 🇨🇳 [简体中文](README.zh.md)

⚠️ **v0.0.0 (Initial Release / Pre-Alpha)**

A Web Bluetooth API implementation for **PySide6 / QtWebEngine** applications. Actual BLE communication is handled by [bleak](https://github.com/hbldh/bleak), while this package implements the browser-side API surface, permission model, and security boundaries.

Built under the [steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) concept of researching and verifying real APIs and standard specifications while maintaining compatibility through independent implementations and extensions. It is a sibling project of [Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) and the first implementation in the **mock-webbluetooth** framework.

> ⚠️ **Initial release.** Developed with AI assistance. Review the source code before relying on it as a production security boundary or for hardware control.

## Features

- Web Bluetooth-compatible `navigator.bluetooth`
- Real BLE device communication
- Native device chooser dialog
- Live-updating scan results
- Per-origin persistent device permissions
- Per-frame origin verification
- GATT service / characteristic / descriptor access
- GATT blocklist enforcement
- Major `requestDevice()` filter handling
- `BluetoothUUID`-style UUID name resolution
- Asynchronous BLE worker that avoids blocking the Qt UI thread
- QtWebEngine / QWebChannel bridge

## Installation

```bash
pip install pyside6-webbluetooth
```

Requirements:

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

Page-side JavaScript can use `navigator.bluetooth` in the same general way as a Web Bluetooth-capable browser:

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

## Architecture

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
    ├── Origin / frame verification
    ├── Permission management
    ├── Device chooser
    ├── GATT blocklist
    └── API / argument validation
    │
    ▼
BLE Worker / asyncio
    │
    │ bleak
    ▼
BLE device
```

## Why the design differs from pyside6-webusb

Like [pyside6-webusb](https://github.com/steck0714/Pyside6-webusb), this project aims for Web API compatibility, but its internal architecture is intentionally different because Bluetooth LE has different timing and concurrency characteristics.

Many USB operations complete relatively quickly. BLE scanning, connecting, and GATT discovery can take hundreds of milliseconds or several seconds, and `bleak` is asyncio-based.

Therefore this project uses:

- **Cheap operations** (`getDevices()`, `forget()`, etc.): synchronous
- **Device chooser** (`requestDevice()`): a modal chooser with a live scan running on a dedicated asyncio thread
- **BLE operations** (`connect()`, GATT discovery, read/write/notify): validate synchronously, return a `requestId`, and deliver the result asynchronously through Qt Signals

This prevents long-running BLE radio operations from blocking the QtWebEngine UI thread.

## Security Design

### Per-origin permissions

Devices selected through `requestDevice()` and their allowed service UUIDs are stored per origin using `QSettings`.

Access to a service that was not granted to the origin is rejected with `SecurityError`.

### Per-frame origin verification

Because a QWebChannel bridge can be visible from multiple frames, JavaScript-reported origins are not trusted directly.

Each frame receives its own unpredictable token, and Python tracks the mapping between the token and the real origin.

This boundary has been tested with an actual `QWebEnginePage` containing iframes.

### GATT blocklist

The WebBluetoothCG GATT blocklist is enforced to prevent access to protected services, characteristics, and descriptors, including protected HID, firmware-update, and FIDO-related resources.

## Chrome Compatibility

Chrome / Chromium Web Bluetooth behavior is used as a compatibility reference, but this project is **not a direct port of Chrome's internal implementation**.

> **Web Bluetooth compatibility ≠ Chrome clone**

The API surface is designed for compatibility while the implementation remains independent and adapted to PySide6 / QtWebEngine / Python / bleak.

## Development & Testing

```bash
pip install -e ".[dev]"
pytest
```

Testing combines real PySide6 / QtWebEngine environments, offscreen `QWebEnginePage` instances, iframes, JavaScript execution, and a mocked `bleak.BleakClient`.

In headless environments, `tests/conftest.py` can fall back to `QT_QPA_PLATFORM=offscreen`.

## Known Limitations

The v0.0.0 release has the following known limitations:

- `getIncludedService()` / `getIncludedServices()` return `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` are not implemented
- The secondary manufacturer-data blocklist is not implemented
- Notification delivery matches characteristic UUIDs within a `(device, service)` pair rather than GATT handles
- The Rust/C++ native acceleration used by pyside6-webusb is not implemented

See [`CHANGELOG.md`](CHANGELOG.md) for details.

## Related Projects

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License. See [`LICENSE`](LICENSE).
