# -*- coding: utf-8 -*-
"""qt_ble_worker.py(実験的なQtBluetoothバックエンド)のテスト。

2段構え(ble_worker.pyのテストと同じ方針):

  1. 可用性チェックとスキャンは、このサンドボックスに実BLEアダプタが
     無い環境で、本物のQBluetoothLocalDevice/QBluetoothDeviceDiscoveryAgent
     を使って「例外を投げずグレースフルに失敗する」ことを検証する。
  2. connect〜GATT操作〜disconnectの一連の配線ロジックは、
     `QLowEnergyController.createCentral`をモックに差し替え、
     フェイクのシグナルオブジェクトで発火を手動制御することで検証する
     (bleakのBleakClientをモックしたのと同じ考え方。ただし
     QLowEnergyController/QLowEnergyServiceはドキュメントに書かれている
     通りに振る舞うと仮定したモックであり、実BLEハードウェアに対する
     検証ではないことに注意)。
"""
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtBluetooth import QLowEnergyCharacteristic, QLowEnergyController, QLowEnergyService

from pyside6_webbluetooth.ble_worker import BleOperationError
from pyside6_webbluetooth.qt_ble_worker import QtBluetoothWorker

BATTERY_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class _FakeSignal:
    """PySide6のSignalの代わりに使う、テスト用の最小限の実装。
    `.connect()`/`.disconnect()`/`.emit()`だけを持つ。"""

    def __init__(self):
        self._handlers = []

    def connect(self, fn):
        self._handlers.append(fn)
        return fn

    def disconnect(self, fn):
        if fn in self._handlers:
            self._handlers.remove(fn)

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


def _make_fake_controller_and_service():
    """connect() -> discoverServices() -> 1サービス1特性(read+notify)の
    discoverDetails完了、までを模擬するモックの組を作る。"""
    ctrl = MagicMock()
    ctrl.connected = _FakeSignal()
    ctrl.discoveryFinished = _FakeSignal()
    ctrl.errorOccurred = _FakeSignal()
    ctrl.disconnected = _FakeSignal()
    ctrl.state.return_value = QLowEnergyController.ControllerState.ConnectedState
    ctrl.errorString.return_value = "mock error"

    fake_char = MagicMock()
    fake_char.uuid.return_value.toString.return_value = "{" + BATTERY_LEVEL_UUID + "}"
    fake_char.properties.return_value = (
        QLowEnergyCharacteristic.PropertyType.Read | QLowEnergyCharacteristic.PropertyType.Notify
    )
    fake_char.descriptors.return_value = []
    cccd = MagicMock()
    cccd.isValid.return_value = True
    fake_char.clientCharacteristicConfiguration.return_value = cccd

    fake_service = MagicMock()
    fake_service.characteristics.return_value = [fake_char]
    fake_service.stateChanged = _FakeSignal()
    fake_service.characteristicRead = _FakeSignal()
    fake_service.characteristicWritten = _FakeSignal()
    fake_service.errorOccurred = _FakeSignal()

    def fake_discover_details():
        fake_service.stateChanged.emit(QLowEnergyService.ServiceState.RemoteServiceDiscovered)

    fake_service.discoverDetails.side_effect = fake_discover_details

    def fake_read_characteristic(ch):
        fake_service.characteristicRead.emit(ch, bytearray([88]))

    fake_service.readCharacteristic.side_effect = fake_read_characteristic

    def fake_write_characteristic(ch, data, mode):
        fake_service.characteristicWritten.emit(ch, data)

    fake_service.writeCharacteristic.side_effect = fake_write_characteristic

    ctrl.createServiceObject.return_value = fake_service
    ctrl.services.return_value = [MagicMock(toString=lambda *a: "{" + BATTERY_UUID + "}")]

    def fake_connect_to_device():
        ctrl.connected.emit()

    ctrl.connectToDevice.side_effect = fake_connect_to_device

    def fake_discover_services():
        ctrl.discoveryFinished.emit()

    ctrl.discoverServices.side_effect = fake_discover_services

    return ctrl, fake_service, fake_char


class TestAvailabilityAndScanWithoutRealAdapter:
    """このサンドボックス環境(実BLEアダプタ無し)での、本物の
    QBluetoothLocalDevice/QBluetoothDeviceDiscoveryAgentに対する
    フェイルセーフ動作。"""

    def test_check_availability_returns_false(self, qapp):
        worker = QtBluetoothWorker()
        worker.start()
        try:
            assert worker.check_availability().result(timeout=2) is False
        finally:
            worker.stop()

    def test_start_scan_fails_gracefully_with_not_found_error(self, qapp):
        worker = QtBluetoothWorker()
        worker.start()
        try:
            with pytest.raises(BleOperationError) as excinfo:
                worker.start_scan().result(timeout=3)
            assert excinfo.value.web_bluetooth_error_name == "NotFoundError"
        finally:
            worker.stop()


@pytest.fixture
def worker(qapp):
    w = QtBluetoothWorker()
    w.start()
    yield w
    w.stop()


class TestFullGattFlowWithMockedController:
    def test_connect_discover_read_write_disconnect(self, worker):
        ctrl, fake_service, fake_char = _make_fake_controller_and_service()

        with patch(
            "pyside6_webbluetooth.qt_ble_worker.QLowEnergyController.createCentral", return_value=ctrl
        ):
            worker.connect("dev1", "AA:AA:AA:AA:AA:AA").result(timeout=2)
            assert worker.is_connected("dev1")

            services = worker.get_services("dev1").result(timeout=2)
            assert len(services) == 1
            assert services[0]["uuid"] == BATTERY_UUID
            chars = services[0]["characteristics"]
            assert len(chars) == 1
            assert chars[0]["uuid"] == BATTERY_LEVEL_UUID
            assert chars[0]["properties"] == ["read", "notify"]
            handle = chars[0]["handle"]

            data = worker.read_characteristic("dev1", handle).result(timeout=2)
            assert data == bytes([88])

            # write_gatt_charと同型のインターフェース: 例外を出さず完了する
            worker.write_characteristic("dev1", handle, b"\x01", True).result(timeout=2)

            worker.disconnect("dev1").result(timeout=2)
        assert not worker.is_connected("dev1")

    def test_get_services_handles_are_stable_across_calls(self, worker):
        """同じ特性オブジェクトに対し、get_services()を複数回呼んでも
        同じ合成handleが返ること(呼び出しごとに新しいhandleを発行すると、
        以前に取得したhandleでのread/writeが壊れてしまう)。"""
        ctrl, fake_service, fake_char = _make_fake_controller_and_service()
        with patch(
            "pyside6_webbluetooth.qt_ble_worker.QLowEnergyController.createCentral", return_value=ctrl
        ):
            worker.connect("dev1", "AA:AA:AA:AA:AA:AA").result(timeout=2)
            services1 = worker.get_services("dev1").result(timeout=2)
            services2 = worker.get_services("dev1").result(timeout=2)
            handle1 = services1[0]["characteristics"][0]["handle"]
            handle2 = services2[0]["characteristics"][0]["handle"]
            assert handle1 == handle2
            worker.disconnect("dev1").result(timeout=2)

    def test_connect_failure_reports_network_error(self, worker):
        ctrl, _fake_service, _fake_char = _make_fake_controller_and_service()

        def failing_connect():
            ctrl.errorOccurred.emit(QLowEnergyController.Error.ConnectionError)

        ctrl.connectToDevice.side_effect = failing_connect

        with patch(
            "pyside6_webbluetooth.qt_ble_worker.QLowEnergyController.createCentral", return_value=ctrl
        ):
            with pytest.raises(BleOperationError) as excinfo:
                worker.connect("dev1", "AA:AA:AA:AA:AA:AA").result(timeout=2)
            assert excinfo.value.web_bluetooth_error_name == "NetworkError"
        assert not worker.is_connected("dev1")

    def test_write_without_response_does_not_wait_for_ack(self, worker):
        """Write Without Responseはcharacteristic側のACKが来ない仕様のため、
        送信要求を出した時点でFutureが解決すること。"""
        ctrl, fake_service, fake_char = _make_fake_controller_and_service()
        # writeCharacteristicが呼ばれても、あえてcharacteristicWrittenを
        # emitしない(ACK無しであることを模擬)。
        fake_service.writeCharacteristic.side_effect = None

        with patch(
            "pyside6_webbluetooth.qt_ble_worker.QLowEnergyController.createCentral", return_value=ctrl
        ):
            worker.connect("dev1", "AA:AA:AA:AA:AA:AA").result(timeout=2)
            services = worker.get_services("dev1").result(timeout=2)
            handle = services[0]["characteristics"][0]["handle"]
            # with_response=Falseなので、ACKが来なくてもタイムアウトしない
            worker.write_characteristic("dev1", handle, b"\x01", False).result(timeout=2)
            worker.disconnect("dev1").result(timeout=2)
