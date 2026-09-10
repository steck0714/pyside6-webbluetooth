# pyside6-webbluetooth

🇯🇵 [日本語](README.md) | 🇺🇸 English | 🇨🇳 [简体中文](README.zh.md)

⚠️ **v0.0.0a1 (Pre-Alpha / Experimental)**

A Web Bluetooth API implementation for **PySide6 / QtWebEngine** applications. It exposes `navigator.bluetooth` to pages while using [bleak](https://github.com/hbldh/bleak) for actual BLE communication. The package implements the browser-side API surface, permission model, GATT access control, and security boundaries.

Built under the [steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) concept of researching and verifying real APIs and standard specifications while maintaining compatibility through independent implementations and extensions. It is a sibling project of [Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) and the first implementation in the **mock-webbluetooth** framework.

> ⚠️ **Pre-Alpha release.** Developed with AI assistance. Review the source code and verify behavior on your target platform before relying on it as a production security boundary or for hardware control.

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
- `bleak` backend
- Experimental `PySide6.QtBluetooth` / `QLowEnergyController` backend

## Installation

```bash
pip install pyside6-webbluetooth
```

Requirements:

- `Python>=3.10`
- `PySide6>=6.6`
- `bleak>=0.21`

The package has also been built and verified on Python 3.14.7 + PySide6 6.11.2 + bleak 3.0.2, with **all 117 tests passing**.

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

## Backend

The default backend is `bleak`:

```python
bridge = install(view.page())
```

An experimental backend based on PySide6's bundled `PySide6.QtBluetooth` / `QLowEnergyController` is also available:

```python
bridge = install(view.page(), backend="qtbluetooth")
```

The QtBluetooth backend's connect/discover/read/write/notify wiring has been tested with mocks, but **has not yet been verified against real BLE hardware**. Test it on your target platform before production use.

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
    ├── API / argument validation
    ├── Origin / frame verification
    ├── Permission management
    ├── Device chooser
    └── GATT blocklist
    │
    ▼
BLE Worker / asyncio
    │
    ├── bleak backend
    │      or
    └── QtBluetooth backend
    │
    ▼
BLE device
```

## Why the design differs from pyside6-webusb

The [Mock-APIs](https://github.com/steck0714/Mock-APIs) concept favors independent implementations adapted to the target environment rather than simple ports.

`pyside6-webusb` can use synchronous operations, but BLE is different:

- `requestDevice()` scanning can take hundreds of milliseconds to seconds
- `gatt.connect()` can also take hundreds of milliseconds to seconds
- `bleak` is asyncio-based and has a different event-loop model from Qt

Therefore this package uses:

- **Cheap operations** (`getDevices()`, `forget()`, etc.): synchronous
- **`requestDevice()`**: a modal native chooser with a live scan running on a dedicated asyncio thread
- **BLE operations** (`connect()`, GATT discovery, read/write/notify): synchronous validation followed by a `requestId`, with the final result delivered asynchronously through Qt Signals

This keeps long-running BLE radio operations away from the QtWebEngine UI thread.

## Security Design

### Per-origin permissions

Devices selected through `requestDevice()` and their allowed service UUIDs are persisted per origin using `QSettings`.

Access to a service that was not granted to the origin is rejected with `SecurityError`.

### Per-frame origin verification

A QWebChannel bridge can be visible from multiple frames, so JavaScript-reported origins are not trusted directly.

Each frame receives an unpredictable token, and Python tracks the mapping between the token and the real origin. This has been tested with actual `QWebEnginePage` instances containing iframes.

### GATT blocklist

The package implements the GATT blocklist from [WebBluetoothCG/registries](https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt).

Protected resources include HID, firmware-update, and FIDO-related GATT resources, as well as selected privacy/configuration characteristics and descriptors.

## Chrome Compatibility

Chrome / Chromium Web Bluetooth behavior and the Web Bluetooth specification are used as compatibility references, but this project is **not a direct port of Chrome's internal implementation**.

> **Web Bluetooth compatibility ≠ Chrome clone**

The API surface aims for Web Bluetooth compatibility while the internal implementation remains independent and adapted to PySide6 / QtWebEngine / Python / the selected BLE backend.

## Development & Testing

```bash
pip install -e ".[dev]"
pytest
```

Testing combines real PySide6 / QtWebEngine environments, offscreen `QWebEnginePage` instances, iframes, JavaScript execution, and mocked `bleak.BleakClient` / `QLowEnergyController` backends.

The v0.0.0a1 test suite has been verified with **117 tests passing**.

For CI/container environments without a BLE adapter, `tests/conftest.py` can fall back to `QT_QPA_PLATFORM=offscreen`.

## Known Limitations

Known limitations in v0.0.0a1 include:

- `getIncludedService()` / `getIncludedServices()` return `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` are not implemented
- The secondary Manufacturer Data blocklist is not implemented
- Notification/indication delivery is matched by characteristic UUID within a `(device, service)` pair rather than by the GATT handle itself
- Rust/C++ native acceleration is not implemented
- The `backend="qtbluetooth"` implementation has not been verified with real BLE hardware

Web Bluetooth behavior can also vary across browsers, operating systems, and BLE stacks. Hardware-control applications should be tested on the actual target OS and BLE adapter.

See [`CHANGELOG.md`](CHANGELOG.md) for detailed changes, bug fixes, specification re-verification, and the Python 3.14 / PySide6 6.11 verification work.

## Related Projects

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License. See [`LICENSE`](LICENSE).
