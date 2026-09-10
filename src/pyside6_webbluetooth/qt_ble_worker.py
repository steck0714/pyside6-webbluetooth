# -*- coding: utf-8 -*-
"""PySide6.QtBluetooth(QLowEnergyController等)を使った、bleakの代わりの
選択可能なバックエンド(実験的)。

## これは何か

ble_worker.py の `BleWorker` と同じ公開インターフェース
(start/stop/check_availability/start_scan/stop_scan/
snapshot_scan_results/connect/disconnect/is_connected/get_services/
read_characteristic/write_characteristic/read_descriptor/
write_descriptor/start_notify/stop_notify、いずれも
`concurrent.futures.Future`を返す)を実装する、もう1つのBLEバックエンド。

「PySide6にはBluetoothを扱えるQtBluetoothモジュールがあるのだから
それを使えないか」という検討に基づき、実際にPySide6.QtBluetoothの
API(QLowEnergyController/QLowEnergyService/
QBluetoothDeviceDiscoveryAgent)を調査・実機確認した上で作成した。

## bleakバックエンドとの決定的な違い

QtBluetoothはもともとQtのシグナル/スロットで動く。asyncioを一切使わない
ため、ble_worker.py(BleWorker)にあった「専用のasyncioスレッドを別に
立てる」仕組みが丸ごと不要になる。すべてQtのメインスレッド上で完結し、
`concurrent.futures.Future`は対応するQtシグナルのハンドラから直接
`set_result()`/`set_exception()`される(スレッドをまたがないため、
ble_worker.pyのために実機検証した「別スレッドからのSignal emitを
Qtが安全にメインスレッドへキューイングする」という性質にも依存しない)。

## 実機で確認したこと・していないこと

- `QBluetoothLocalDevice().isValid()` でアダプタの有無を同期的に
  判定できること、`QBluetoothDeviceDiscoveryAgent.start()` が
  アダプタ無し環境で`errorOccurred`シグナル経由できちんと失敗を
  報告すること(`isActive()`もFalseのままになること)は、実際に
  アダプタの無いサンドボックス環境で動かして確認済み。
- 一方、実BLEデバイスへの接続・GATT探索・read/write/notifyの成功パスは、
  このサンドボックスに実BLEアダプタが無いため実機のハードウェアに対しては
  検証できていない。`QLowEnergyController`をテスト用にモックへ差し替えた
  上で、本モジュール自身の配線ロジック(シグナル→Future解決、
  合成handleの管理等)は検証しているが、bleakバックエンドほど長期間
  使われてきた実績は無い。Qtのフォーラムには過去(Qt5〜Qt6初期)に
  central役でのnotify不達やdisconnect関連の報告もあったため、
  本番導入前に対象プラットフォームで実機確認することを推奨する。
- Linux(BlueZ)では同一デバイスに対し`QLowEnergyController`を2つ同時に
  使えない制限がある。本モジュールは1接続につき1つのControllerしか
  保持しないため通常は問題にならないが、同じアドレスに対し複数の
  device_idが割り当たるような使い方(通常は起こらない)では注意が必要。

## GATT特性/記述子の識別方法(合成handle)

bleakと異なり、`QLowEnergyCharacteristic`/`QLowEnergyDescriptor`は
PySide6バインディング上で数値のhandleを直接公開していない。
bridge.py側は(同一サービス内でのUUID重複に対応するため)整数handleで
特性/記述子を指し示す設計になっているため、本モジュール内部で
1から始まる連番の「合成handle」を発行し、
`{client_id: {handle: QLowEnergyCharacteristicまたはQLowEnergyDescriptor}}`
として保持することでbleakバックエンドと同じ外部インターフェースを保つ。
"""
from __future__ import annotations

import itertools
from concurrent.futures import Future
from typing import Callable, Optional

from PySide6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothLocalDevice,
    QLowEnergyCharacteristic,
    QLowEnergyController,
    QLowEnergyService,
)
from PySide6.QtCore import QObject

from .ble_worker import BleOperationError


class _ScannedDevice:
    """bleak.backends.device.BLEDeviceと同じ形(address, name)で
    QBluetoothDeviceInfoをラップする(chooser_dialog.py/bridge.pyが
    bleakのBLEDeviceを想定しているため、同じ属性名を提供する)。"""

    def __init__(self, info) -> None:
        self.address = info.address().toString()
        self.name = info.name()
        self._info = info


class _ScannedAdvertisement:
    """bleak.backends.scanner.AdvertisementDataと同じ形
    (local_name, service_uuids, manufacturer_data, service_data, rssi)で
    QBluetoothDeviceInfoをラップする。"""

    def __init__(self, info) -> None:
        self.local_name = info.name() or None
        self.service_uuids = [u.toString(3).strip("{}").lower() for u in info.serviceUuids()]
        self.manufacturer_data = {
            mid: bytes(info.manufacturerData(mid)) for mid in info.manufacturerIds()
        }
        self.service_data = {
            sid.toString(3).strip("{}").lower(): bytes(info.serviceData(sid)) for sid in info.serviceIds()
        }
        self.rssi = info.rssi()


class _Connection:
    """1つのGATT接続(1 device_id分)が保持する状態一式。"""

    def __init__(self, controller: QLowEnergyController) -> None:
        self.controller = controller
        self.services: dict[str, QLowEnergyService] = {}
        # 合成handle -> (kind, obj, service_uuid, characteristic_handle_if_descriptor)
        self.handles: dict[int, tuple] = {}
        self.notify_callbacks: dict[int, Callable[[str, bytes], None]] = {}
        self.on_disconnected: Optional[Callable[[], None]] = None
        self.pending_connect: Optional[Future] = None


def _uuid_str(qtuuid) -> str:
    # QBluetoothUuid.toString()は"{xxxxxxxx-....}"形式で返るため、
    # bleak側の"小文字・波括弧無し128-bit"表記に正規化する。
    return qtuuid.toString(3).strip("{}").lower() if hasattr(qtuuid, "toString") else str(qtuuid).lower()


class QtBluetoothWorker(QObject):
    """PySide6.QtBluetoothバックエンド。BleWorkerと同じ公開インターフェース。
    QObjectを継承しているのは、Qtのシグナル/スロット接続(特に
    `connect(..., Qt.ConnectionType...)`まわりの寿命管理)を素直に
    扱うため。"""

    def __init__(self) -> None:
        super().__init__()
        self._scan_agent: Optional[QBluetoothDeviceDiscoveryAgent] = None
        self._scan_results: dict[str, tuple] = {}
        self._connections: dict[str, _Connection] = {}
        self._handle_counter = itertools.count(1)

    # --- ライフサイクル ---
    # QtBluetoothはQtのメインスレッド上で完結するため、bleakバックエンドの
    # ような専用スレッドの起動/停止は不要。

    def start(self) -> None:
        pass

    def stop(self) -> None:
        for client_id in list(self._connections.keys()):
            self.disconnect(client_id)
        if self._scan_agent is not None:
            self._scan_agent.stop()
            self._scan_agent = None

    # --- 可用性 ---

    def check_availability(self) -> Future:
        future: Future = Future()
        try:
            future.set_result(QBluetoothLocalDevice().isValid())
        except Exception as exc:  # noqa: BLE001
            future.set_result(False)
        return future

    # --- スキャン ---

    def start_scan(self, service_uuids: Optional[list] = None) -> Future:
        future: Future = Future()
        if self._scan_agent is not None:
            self._scan_agent.stop()
        self._scan_results.clear()
        agent = QBluetoothDeviceDiscoveryAgent(self)
        self._scan_agent = agent

        def on_device(info) -> None:
            addr = info.address().toString()
            self._scan_results[addr] = (_ScannedDevice(info), _ScannedAdvertisement(info))

        def on_error(_err) -> None:
            if not future.done():
                future.set_exception(
                    BleOperationError(
                        f"failed to start BLE scan: {agent.errorString()}",
                        web_bluetooth_error_name="NotFoundError",
                    )
                )

        agent.deviceDiscovered.connect(on_device)
        agent.errorOccurred.connect(on_error)
        agent.start(QBluetoothDeviceDiscoveryAgent.DiscoveryMethod.LowEnergyMethod)
        if not future.done():
            future.set_result(None)
        return future

    def stop_scan(self) -> Future:
        future: Future = Future()
        if self._scan_agent is not None:
            self._scan_agent.stop()
            self._scan_agent = None
        future.set_result(None)
        return future

    def snapshot_scan_results(self) -> dict:
        return dict(self._scan_results)

    # --- 接続 ---

    def connect(self, client_id: str, address: str, on_disconnected: Optional[Callable[[str], None]] = None) -> Future:
        from PySide6.QtBluetooth import QBluetoothAddress, QBluetoothDeviceInfo

        future: Future = Future()
        info = QBluetoothDeviceInfo(QBluetoothAddress(address), "", 0)
        controller = QLowEnergyController.createCentral(info, self)
        conn = _Connection(controller)
        conn.pending_connect = future
        self._connections[client_id] = conn

        def cleanup_signal_connections() -> None:
            for sig, slot in list(_temp_conns):
                try:
                    sig.disconnect(slot)
                except Exception:  # noqa: BLE001
                    pass

        _temp_conns = []

        def on_connected() -> None:
            controller.discoverServices()

        def on_discovery_finished() -> None:
            uuids = [_uuid_str(u) for u in controller.services()]
            self._begin_service_detail_discovery(client_id, uuids, future)

        def on_error(_err) -> None:
            if conn.pending_connect is not None and not conn.pending_connect.done():
                conn.pending_connect.set_exception(
                    BleOperationError(
                        f"failed to connect to {address}: {controller.errorString()}",
                        web_bluetooth_error_name="NetworkError",
                    )
                )
            self._connections.pop(client_id, None)
            cleanup_signal_connections()

        def on_disconnected_signal() -> None:
            self._connections.pop(client_id, None)
            if on_disconnected is not None:
                on_disconnected(client_id)
            cleanup_signal_connections()

        controller.connected.connect(on_connected)
        controller.discoveryFinished.connect(on_discovery_finished)
        controller.errorOccurred.connect(on_error)
        controller.disconnected.connect(on_disconnected_signal)
        _temp_conns.extend(
            [
                (controller.connected, on_connected),
                (controller.discoveryFinished, on_discovery_finished),
                (controller.errorOccurred, on_error),
            ]
        )
        conn.on_disconnected = lambda: on_disconnected(client_id) if on_disconnected else None

        controller.connectToDevice()
        return future

    def _begin_service_detail_discovery(self, client_id: str, service_uuids: list, connect_future: Future) -> None:
        conn = self._connections.get(client_id)
        if conn is None:
            return
        if not service_uuids:
            if not connect_future.done():
                connect_future.set_result(None)
            return

        remaining = set(service_uuids)

        for uuid in service_uuids:
            service = conn.controller.createServiceObject(_qt_uuid_from_str(uuid), self)
            if service is None:
                remaining.discard(uuid)
                continue
            conn.services[uuid] = service

            def make_on_state_changed(u=uuid, svc=service):
                def on_state_changed(state) -> None:
                    if state == QLowEnergyService.ServiceState.RemoteServiceDiscovered:
                        remaining.discard(u)
                        if not remaining and not connect_future.done():
                            connect_future.set_result(None)
                    elif state == QLowEnergyService.ServiceState.InvalidService:
                        remaining.discard(u)
                        if not remaining and not connect_future.done():
                            connect_future.set_result(None)

                return on_state_changed

            service.stateChanged.connect(make_on_state_changed())
            service.discoverDetails()

        if not remaining and not connect_future.done():
            connect_future.set_result(None)

    def disconnect(self, client_id: str) -> Future:
        future: Future = Future()
        conn = self._connections.pop(client_id, None)
        if conn is not None:
            conn.controller.disconnectFromDevice()
        future.set_result(None)
        return future

    def is_connected(self, client_id: str) -> bool:
        conn = self._connections.get(client_id)
        if conn is None:
            return False
        return conn.controller.state() in (
            QLowEnergyController.ControllerState.ConnectedState,
            QLowEnergyController.ControllerState.DiscoveringState,
            QLowEnergyController.ControllerState.DiscoveredState,
        )

    def _get_connection_or_raise(self, client_id: str) -> _Connection:
        conn = self._connections.get(client_id)
        if conn is None:
            raise BleOperationError(
                f"no active GATT connection for {client_id}", web_bluetooth_error_name="NetworkError"
            )
        return conn

    # --- GATTツリーの取得 ---

    def get_services(self, client_id: str) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future

        result = []
        for service_uuid, service in conn.services.items():
            chars = []
            for qchar in service.characteristics():
                char_handle = self._register_handle(conn, "characteristic", qchar, service_uuid)
                props = _qt_properties_to_bleak_style(qchar.properties())
                descs = []
                for qdesc in qchar.descriptors():
                    desc_handle = self._register_handle(conn, "descriptor", qdesc, service_uuid, char_handle)
                    descs.append({"uuid": _uuid_str(qdesc.uuid()), "handle": desc_handle})
                chars.append(
                    {"uuid": _uuid_str(qchar.uuid()), "handle": char_handle, "properties": props, "descriptors": descs}
                )
            result.append({"uuid": service_uuid, "handle": self._register_handle(conn, "service", service, service_uuid), "characteristics": chars})
        future.set_result(result)
        return future

    def _register_handle(self, conn: _Connection, kind: str, obj, service_uuid: str, parent_handle: Optional[int] = None) -> int:
        # 同じオブジェクトに何度も新しいhandleを割り当てないよう、
        # 既存登録を先に探す(get_services()は呼ばれるたびに
        # 特性/記述子を再列挙するため)。
        for h, entry in conn.handles.items():
            if entry[1] is obj:
                return h
        handle = next(self._handle_counter)
        conn.handles[handle] = (kind, obj, service_uuid, parent_handle)
        return handle

    # --- 読み書き ---

    def _connect_until_future_done(self, signal, future: Future, handler) -> None:
        """`handler`を`signal`に接続し、`future`が解決した時点で自動的に
        切断する。切断し忘れると、同じserviceへの複数回のread/write
        呼び出しのたびにハンドラが積み上がっていってしまう
        (コードレビューで気づいた: 個々の呼び出し自体はUUID一致判定と
        `future.done()`チェックのおかげで誤動作こそしないが、
        メモリ・ハンドラ数が単調に増え続けるのは健全ではない)。"""
        conn_ref = signal.connect(handler)

        def cleanup(_fut: Future) -> None:
            try:
                signal.disconnect(conn_ref)
            except (RuntimeError, TypeError):
                pass

        future.add_done_callback(cleanup)

    def read_characteristic(self, client_id: str, handle: int) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        kind, qchar, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]

        def on_read(characteristic, value) -> None:
            if _uuid_str(characteristic.uuid()) == _uuid_str(qchar.uuid()) and not future.done():
                future.set_result(bytes(value))

        def on_error(_err) -> None:
            if not future.done():
                future.set_exception(BleOperationError("characteristic read failed", web_bluetooth_error_name="NetworkError"))

        self._connect_until_future_done(service.characteristicRead, future, on_read)
        self._connect_until_future_done(service.errorOccurred, future, on_error)
        service.readCharacteristic(qchar)
        return future

    def write_characteristic(self, client_id: str, handle: int, data: bytes, with_response: bool) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        _kind, qchar, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]
        mode = (
            QLowEnergyService.WriteMode.WriteWithResponse
            if with_response
            else QLowEnergyService.WriteMode.WriteWithoutResponse
        )

        def on_written(characteristic, _value) -> None:
            if _uuid_str(characteristic.uuid()) == _uuid_str(qchar.uuid()) and not future.done():
                future.set_result(None)

        def on_error(_err) -> None:
            if not future.done():
                future.set_exception(BleOperationError("characteristic write failed", web_bluetooth_error_name="NetworkError"))

        if with_response:
            self._connect_until_future_done(service.characteristicWritten, future, on_written)
            self._connect_until_future_done(service.errorOccurred, future, on_error)
        service.writeCharacteristic(qchar, bytearray(data), mode)
        if not with_response and not future.done():
            # Write Without Responseはcharacteristic側のACKが来ない
            # (仕様通り)ため、送信要求を出した時点で成功とする。
            future.set_result(None)
        return future

    def read_descriptor(self, client_id: str, handle: int) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        _kind, qdesc, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]

        def on_read(descriptor, value) -> None:
            if _uuid_str(descriptor.uuid()) == _uuid_str(qdesc.uuid()) and not future.done():
                future.set_result(bytes(value))

        self._connect_until_future_done(service.descriptorRead, future, on_read)
        service.readDescriptor(qdesc)
        return future

    def write_descriptor(self, client_id: str, handle: int, data: bytes) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        _kind, qdesc, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]

        def on_written(descriptor, _value) -> None:
            if _uuid_str(descriptor.uuid()) == _uuid_str(qdesc.uuid()) and not future.done():
                future.set_result(None)

        self._connect_until_future_done(service.descriptorWritten, future, on_written)
        service.writeDescriptor(qdesc, bytearray(data))
        return future

    def start_notify(self, client_id: str, handle: int, callback: Callable[[str, bytes], None]) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        _kind, qchar, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]
        cccd = qchar.clientCharacteristicConfiguration()
        if not cccd.isValid():
            future.set_exception(
                BleOperationError("characteristic has no CCCD (cannot notify)", web_bluetooth_error_name="NotSupportedError")
            )
            return future

        char_uuid = _uuid_str(qchar.uuid())
        conn.notify_callbacks[handle] = callback

        def on_value_changed(characteristic, value) -> None:
            if _uuid_str(characteristic.uuid()) == char_uuid:
                callback(char_uuid, bytes(value))

        service.characteristicChanged.connect(on_value_changed)
        # notify優先、無ければindicateを有効にする(仕様の内部実装
        # ノートと同じくCCCDへ直接書き込む)。
        enable_value = (
            QLowEnergyCharacteristic.CCCDEnableNotification
            if "notify" in _qt_properties_to_bleak_style(qchar.properties())
            else QLowEnergyCharacteristic.CCCDEnableIndication
        )
        service.writeDescriptor(cccd, enable_value)
        future.set_result(None)
        return future

    def stop_notify(self, client_id: str, handle: int) -> Future:
        future: Future = Future()
        try:
            conn = self._get_connection_or_raise(client_id)
        except BleOperationError as exc:
            future.set_exception(exc)
            return future
        _kind, qchar, service_uuid, _parent = conn.handles[handle]
        service = conn.services[service_uuid]
        cccd = qchar.clientCharacteristicConfiguration()
        conn.notify_callbacks.pop(handle, None)
        if cccd.isValid():
            service.writeDescriptor(cccd, QLowEnergyCharacteristic.CCCDDisable)
        future.set_result(None)
        return future


def _qt_uuid_from_str(uuid_str: str):
    from PySide6.QtBluetooth import QBluetoothUuid
    from PySide6.QtCore import QUuid

    return QBluetoothUuid(QUuid("{" + uuid_str + "}"))


def _qt_properties_to_bleak_style(qt_properties) -> list:
    """QLowEnergyCharacteristic.PropertyType(フラグ)を、hardening.pyが
    期待するbleak形式の文字列リストへ変換する。"""
    from PySide6.QtBluetooth import QLowEnergyCharacteristic as _Char

    mapping = [
        (_Char.PropertyType.Broadcasting, "broadcast"),
        (_Char.PropertyType.Read, "read"),
        (_Char.PropertyType.WriteNoResponse, "write-without-response"),
        (_Char.PropertyType.Write, "write"),
        (_Char.PropertyType.Notify, "notify"),
        (_Char.PropertyType.Indicate, "indicate"),
        (_Char.PropertyType.WriteSigned, "authenticated-signed-writes"),
        (_Char.PropertyType.ExtendedProperty, "extended-properties"),
    ]
    return [name for flag, name in mapping if qt_properties & flag]



