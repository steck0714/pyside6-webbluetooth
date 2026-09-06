# Changelog

All notable changes to this project will be documented in this file.

## [0.0.0] - Initial release

Initial implementation of `pyside6-webbluetooth`, designed following the
architecture and security lessons of the sibling project
[Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) (part of the
[Mock-APIs](https://github.com/steck0714/Mock-APIs) /
[Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth) framework),
but re-designed where Bluetooth's requirements genuinely differ from USB's —
in particular, BLE radio operations (scanning, connecting, GATT discovery)
are asynchronous and can take seconds, unlike USB's largely synchronous,
millisecond-scale transfers. See README.md for the full architecture
discussion.

### Core functionality

- `navigator.bluetooth.getAvailability()`, `getDevices()`, `requestDevice()`
- `BluetoothDevice`: `id`, `name`, `gatt`, `watchingAdvertisements`,
  `forget()`, `gattserverdisconnected` event
- `BluetoothRemoteGATTServer`: `connect()`, `disconnect()`,
  `getPrimaryService()`, `getPrimaryServices()`
- `BluetoothRemoteGATTService`: `getCharacteristic()`, `getCharacteristics()`
- `BluetoothRemoteGATTCharacteristic`: `properties`, `value`,
  `getDescriptor()`, `getDescriptors()`, `readValue()`,
  `writeValueWithResponse()`, `writeValueWithoutResponse()`, `writeValue()`
  (legacy), `startNotifications()`, `stopNotifications()`,
  `characteristicvaluechanged` event
- `BluetoothRemoteGATTDescriptor`: `readValue()`, `writeValue()`
- A native, non-auto-selecting device chooser dialog with a live-updating
  scan list, sorted by signal strength, always showing the requesting origin
- Per-origin, persisted device permissions (`QSettings`-backed), matching the
  real spec's `getDevices()`/`forget()` semantics
- The real GATT services blocklist from
  [WebBluetoothCG/registries](https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt),
  transcribed directly from source (not hand-typed from memory) and enforced
  on every service/characteristic/descriptor access
- The real `requestDevice()` filter-matching algorithm (`services`, `name`,
  `namePrefix`, `manufacturerData` with prefix/mask, `serviceData` with
  prefix/mask)
- `BluetoothUUID`-equivalent name resolution (`battery_service`,
  `heart_rate`, etc.) backed by the full GATT assigned-numbers registries
  (39 services / 214 characteristics / 15 descriptors), also transcribed
  directly from the WebBluetoothCG registries rather than retyped from memory
- Frame-origin verification: each frame (including iframes) is issued its own
  unguessable token via `QWebEngineFrame`-scoped script injection, so a
  cross-origin iframe embedded on the page cannot spoof the top-level
  origin's permissions — verified against a real `QWebEnginePage` with a
  dynamically-inserted cross-origin iframe, not just asserted
- An asyncio-thread-backed BLE worker (`bleak` as the backend) so that slow
  radio operations never block the Qt UI thread; verified against real
  cross-thread `Future` → Qt `Signal` delivery

### Known limitations (v0.0.0)

- `getIncludedService()`/`getIncludedServices()` (secondary/included GATT
  services) are not implemented; they reject with `NotSupportedError` rather
  than silently misbehaving
- `watchAdvertisements()` / `unwatchAdvertisements()` (continuous
  advertisement monitoring) are not implemented for the same reason
- The manufacturer-data blocklist (a narrower, secondary blocklist in the
  same upstream registry) is not implemented — only the GATT services/
  characteristics/descriptors blocklist is
- Notification delivery matches by characteristic UUID within a
  (device, service) pair, not by GATT handle; devices with two notifying
  characteristics that share the same custom UUID under one service would
  receive each other's notifications. Standard 16-bit UUIDs essentially
  never collide this way in practice
- Native Rust/C++ acceleration (as later added to the sibling WebUSB project)
  is not present in this initial version

### Found and fixed during development (via testing against a real
QtWebEngine + a mocked `bleak.BleakClient`, per the project's "verify
against a real environment, don't just assume" policy)

- The JS classes for `Bluetooth`, `BluetoothDevice`,
  `BluetoothRemoteGATTService`, and `BluetoothRemoteGATTCharacteristic`
  initially used the old `EventTarget.call(this)` pseudo-inheritance
  pattern, which real browsers reject for native constructors
  ("Please use the 'new' operator"). Fixed by switching those four classes
  to genuine ES6 `class ... extends EventTarget`.
- A race condition in the request/response bridge: because BLE operations
  can complete extremely fast (especially under test with a mocked
  backend), Python could emit the `bleOperationResult` completion signal
  before the JS side had finished registering the corresponding pending
  Promise, permanently hanging that call. Fixed with a small buffer for
  "arrived-early" results that a not-yet-registered caller can claim.
- `QDialog.Accepted` is only reliably accessible as a class attribute
  (`QDialog.Accepted`) in this PySide6 version, not as an instance attribute
  (`dialog.Accepted`) on a `QDialog` subclass.
- Origin strings were used verbatim as `QSettings` key path segments;
  `QSettings` collapses `//` in key paths, which — while not exploitable
  through this package's own trusted origin-derivation path — was tightened
  by percent-encoding the origin before using it as a key, removing the
  ambiguity entirely.
