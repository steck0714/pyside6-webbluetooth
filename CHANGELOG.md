# Changelog

All notable changes to this project will be documented in this file.

## [0.0.0a1] - Verified against Python 3.14.7; investigated newer language/runtime features

Requested follow-up: check whether anything new in Python 3.14.7 or PySide6
6.11.2 could be put to use, then actually build that environment and
confirm.

### Environment actually built and verified

Neither `apt` nor a PPA mirror was reachable for a prebuilt Python 3.14 in
this project's build environment, so Python 3.14.7 was compiled from source
(from the official `python/cpython` `v3.14.7` tag) and installed to
`/opt/python3.14.7`, including the new `compression.zstd` module (PEP 784),
which requires `libzstd-dev` at build time and is easy to end up silently
without. A fresh virtualenv on this interpreter installed `PySide6==6.11.2`
and `bleak==3.0.2` without issue, and **all 117 tests passed unmodified** —
this package needed no source changes to run correctly on 3.14.7.

### Findings on Python 3.14's headline features, and what was (and wasn't) adopted

- **PEP 649 (deferred evaluation of annotations)** — already effectively
  compatible: every module doing type-annotated work already used
  `from __future__ import annotations`. No change needed, and this import is
  kept rather than removed, since the package still supports 3.10+, not just
  3.14+.
- **PEP 734 (`concurrent.interpreters`, subinterpreters in the stdlib)** —
  investigated as a possible alternative to `ble_worker.py`'s background-OS-
  thread `AsyncioThread` design. Tested directly rather than assumed: running
  `bleak`'s scanner inside a subinterpreter on this Python 3.14.7 build fails
  immediately with `ImportError: module dbus_fast.message does not support
  loading in subinterpreters` — `dbus-fast` (bleak's Linux/BlueZ dependency)
  does not support subinterpreters yet. **Not adopted**; `AsyncioThread`'s
  existing plain-OS-thread design is kept.
- **PEP 779 (free-threaded Python officially supported)** — not pursued.
  Qt for Python's own 6.10 release announcement states free-threaded support
  "is something that is still being researched, and has been very
  challenging due to the fact that a GIL-less Python requires \[Qt] to
  implement \[its] own locking mechanism to work well with the Qt event
  loop." Nothing in the 6.11 release notes indicates this has since been
  resolved. Building and testing against `python3.14t` was skipped on this
  basis rather than attempting an unsupported combination.
- **PEP 750 (t-string literals)** — a genuinely appealing fit was found:
  `chooser_dialog.py`'s manual `_escape()`-then-interpolate pattern for the
  origin label is exactly the kind of thing t-strings (with a custom
  auto-escaping processing function) are designed to make impossible to get
  wrong. **Not adopted**, because `t"..."` is a syntax-level feature — a
  single file using it would raise `SyntaxError` on import for every
  currently-supported Python below 3.14, and PySide6 6.11 itself still
  supports 3.10+. Revisiting this is reasonable once 3.14+ becomes a
  realistic floor for this package's users, not before.
- **PEP 758 (`except`/`except*` without parentheses)** — same syntax-level
  compatibility concern as t-strings applies here (this package uses
  `except (TypeError, ValueError):`-style multi-exception clauses in several
  places); **not adopted** for the same reason.
- **`compression.zstd` (PEP 784)** — confirmed available; not applicable,
  this package has no compression needs.

### Other corrections found while doing this

- `pyproject.toml` declared `requires-python = ">=3.9"`, which was already
  wrong independent of the 3.14 work: PySide6 6.11's own release notes
  record that "the minimum supported Python version has been raised to
  3.10" (PYSIDE-2786). Corrected to `>=3.10`, and Python 3.14 was added to
  the trove classifiers now that it's verified.
- A latent race condition in the test suite itself (not in the package):
  `TestFullGattFlowWithMockedClient::test_full_flow` asserted
  `not worker.is_connected(...)` immediately after calling
  `disconnectGatt()`, without accounting for `disconnectGatt()`'s
  intentionally fire-and-forget design (`BluetoothRemoteGATTServer.disconnect()`
  returns no Promise per spec, so the actual disconnect happens
  asynchronously on the worker thread after the synchronous call already
  returned). This had apparently always been racy but reliably "won" the
  race in earlier runs; it surfaced as a genuine, reproducible failure once
  system load increased (from building Python 3.14.7 from source in the
  same environment). Fixed by polling for the post-condition with a bounded
  timeout instead of asserting immediately.

## [0.0.0a] - Bug fixes, spec re-verification, and an experimental QtBluetooth backend

Requested follow-up to v0.0.0: re-check the implementation for bugs, re-verify
against the official Web Bluetooth specification, and investigate whether
`PySide6.QtBluetooth` (Qt's own Bluetooth LE module, bundled with PySide6)
could be used instead of/alongside `bleak`.

### Bugs found via code review and fixed

- **Resource leak on duplicate `connect()`.** Calling `device.gatt.connect()`
  while already connected created a brand-new `BleakClient` without
  disconnecting the old one, leaking the previous connection. `connectGatt()`
  now short-circuits to an immediate success when already connected, matching
  the spec (`connect()` on an already-connected server just resolves).
- **Malformed advertisement data from one nearby device could break the
  chooser's scan loop.** `service_data` keys are assumed to be well-formed
  UUIDs; a real (possibly malfunctioning or non-standard) device sending
  something else would raise inside the periodic scan-poll timer. Filter
  matching for each discovered device is now individually wrapped, so one
  bad device is skipped rather than disrupting the whole dialog.
- **`_notifyTargets` was never cleaned up on disconnect (JS side).** GATT
  handles can be reassigned on reconnect; without cleanup, stale
  characteristic objects from a previous connection stayed registered
  indefinitely, leaking memory and risking duplicate-delivery once new
  objects with the same UUID registered after a reconnect.
- Removed dead code: an unused `self._runtime` field and an unused
  `new_client_id` import in `bridge.py`.
- Hardened `frame_origin.py`'s per-frame rescan against `QWebEngineFrame`
  objects becoming invalid between the validity check and use (Qt's own
  docs note frames "may be created and deleted spontaneously"); a failure
  visiting one frame, or the page itself being mid-teardown, no longer
  breaks the periodic rescan timer for every other frame.

### Re-verified against the official specification; one gap found and fixed

Re-read the relevant sections of
[the Web Bluetooth spec](https://webbluetoothcg.github.io/web-bluetooth/)
against the implementation rather than against memory of the earlier
research pass.

- **Confirmed correct:** the "merge newly-granted allowed services into the
  stored list" behavior in `requestDeviceChooser()`; `getPrimaryService()`'s
  SecurityError-for-disallowed-service behavior; `bleak`'s `disconnected_callback`
  firing only for *unsolicited* disconnects (confirmed by reading bleak's own
  source), which means `disconnectGatt()`/`forget()` do not spuriously
  trigger a `gattserverdisconnected` event — no change needed there.
- **Gap found and fixed:** the spec requires that operations in progress on a
  `BluetoothRemoteGATTServer` (`getPrimaryService()`, `readValue()`, etc.)
  fail with `NetworkError` if that server disconnects while they're still
  running — regardless of whether the underlying operation would have
  otherwise succeeded, and even if the device reconnects before it would
  have finished ("GATTServer connect-checking wrapper" /
  `[[activeAlgorithms]]` in spec terms). This was not implemented: an
  in-flight operation would simply resolve or reject based on whatever the
  backend eventually returned. Added per-device tracking of in-flight
  request IDs; both a spontaneous disconnect and an explicit
  `disconnect()` now immediately fail every operation still pending for
  that device with `NetworkError`, and the backend's eventual real result
  (if it arrives afterward) is discarded rather than double-delivered.

### Backend architecture refactor

The bridge's read/write/notify flows were originally written as `async def`
coroutines specific to `ble_worker.py`'s asyncio-thread model. Investigating
a QtBluetooth-based backend (see below) surfaced that this coupling would
force a second, parallel copy of every flow. Refactored these into small,
backend-agnostic functions built on a new `future_then()` helper
(`future_utils.py`) that chains plain `concurrent.futures.Future` objects
the way `Promise.prototype.then()` chains promises. Any backend that
produces `Future`s for its primitive operations (`get_services`,
`read_characteristic`, etc.) can now be plugged in without bridge.py caring
how those futures get resolved. All 110 previously-passing tests continued
to pass unchanged after this refactor, and it also happens to close a
latent inefficiency: `get_services()` is now only invoked when actually
needed rather than being re-awaited inline within each composite coroutine.

### Investigated: can `PySide6.QtBluetooth` be used instead of `bleak`?

Yes, for the pieces re-implemented here — with caveats. Findings, in order:

- `PySide6.QtBluetooth` (`QBluetoothDeviceDiscoveryAgent`,
  `QLowEnergyController`, `QLowEnergyService`, `QLowEnergyCharacteristic`,
  `QLowEnergyDescriptor`) ships with PySide6 itself (verified present
  alongside PySide6 6.11.2 in this project's virtual environment) — using it
  would mean one fewer runtime dependency (`bleak` + `dbus-fast` on Linux).
- Unlike `bleak`, it is signal/slot-native. This eliminates the need for
  `ble_worker.py`'s dedicated asyncio thread and the cross-thread
  `Future`-to-`Signal` handoff entirely — a QtBluetooth-backed worker runs
  on the Qt main thread and resolves `Future`s directly from signal
  handlers, sidestepping that whole class of concern.
- `QBluetoothLocalDevice().isValid()` gives a cheap, synchronous
  availability check, unlike `bleak`, which requires attempting an actual
  scan start/stop to find out. Verified directly: in this sandbox (no real
  adapter), it correctly returns `False`, and `QBluetoothDeviceDiscoveryAgent.start()`
  fails cleanly via `errorOccurred` without needing to be wrapped the way
  `bleak`'s lower-level `FileNotFoundError` did.
- Historical, platform-specific bugs exist in Qt's own bug tracker and forum
  history for the central-role GATT client (BlueZ DBus migration-era
  notification issues, reports of `disconnectFromDevice()` not always
  working as expected on some Qt5/early-Qt6 versions). These may or may not
  still apply to this project's Qt 6.11.2 — that could not be confirmed
  either way without real hardware across Windows/macOS/Linux, which this
  sandbox does not have.
- `QLowEnergyCharacteristic`/`QLowEnergyDescriptor` do not expose a numeric
  GATT handle the way `bleak` does, which the rest of this implementation
  relies on to disambiguate same-UUID characteristics/descriptors across
  different services. The new `QtBluetoothWorker` generates its own
  synthetic per-connection handles internally to bridge this gap
  transparently.
- Enabling notifications/indications works by writing specific bit patterns
  directly to the Client Characteristic Configuration Descriptor (matching
  the Web Bluetooth spec's own internal model for `startNotifications()`,
  and confirmed via `QLowEnergyCharacteristic.CCCDEnableNotification` /
  `CCCDEnableIndication` / `CCCDDisable`), rather than a single
  `start_notify()`-style call like `bleak` provides.

Given this, `PySide6.QtBluetooth` support was added as a second, **selectable,
experimental** backend (`install(page, backend="qtbluetooth")`), rather than
replacing `bleak` outright. `bleak` remains the default. The new backend
(`qt_ble_worker.py`) implements the full connect / discover / read / write /
notify surface and is tested with `QLowEnergyController` mocked out the same
way `BleakClient` is mocked for the default backend's tests (including a
regression test confirming that repeated `get_services()` calls return
*stable* synthetic handles for the same underlying characteristic, and that
a connection failure surfaces as `NetworkError`) — but, per the point above,
it has not been exercised against real BLE hardware on any platform. Treat
it as a proof of feasibility, not yet a production-equivalent alternative to
the default backend.

### Known limitations carried over from v0.0.0

See the v0.0.0 entry below; `getIncludedService()`/`getIncludedServices()`,
`watchAdvertisements()`, and the manufacturer-data blocklist remain
unimplemented in this release too.

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
