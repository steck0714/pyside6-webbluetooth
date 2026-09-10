# -*- coding: utf-8 -*-
"""polyfill.py(navigator.bluetoothのJS実装)の統合テスト。

実際のQWebEnginePageにinstall()を適用し、本物のJavaScriptエンジン
(QtWebEngine = Chromium)上でnavigator.bluetoothを動かして検証する。
bridge.py側のテストはPythonの@Slotを直接呼んでいたが、ここではJSの
`await navigator.bluetooth.xxx()`から一連の流れを駆動することで、
polyfill.js側のクラス設計・Promise配線・イベント配送を検証する。

`test_race_condition_...`は実際にこのテストを書く過程で踏んだバグの
再発防止テストである: bleak操作がモックなどで極めて高速に完了すると、
Python側のbleOperationResultシグナルが、JS側がcallBridge()の同期応答
(requestId)を受け取ってPromiseを登録するより先に届くことがあった。
"""
import json

import pytest
from PySide6.QtCore import QTimer, QUrl
from unittest.mock import AsyncMock, MagicMock, patch

from pyside6_webbluetooth import install

BATTERY_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class _JsPageHarness:
    """`page.setUrl()`でロードした(実ネットワークは無いため`loadFinished`は
    Falseになるが、`.url()`自体は正しく反映される)ページに対して、
    JS実行とその非同期結果の取得を簡潔に書くためのヘルパー。

    QSettingsはテスト間で状態が漏れないよう、インスタンスごとに一意な
    組織名を使う(実際にこれをせず実行し、他のテストで保存した権限が
    残っていて「権限0件のはず」のテストが失敗する、という問題を
    実際に踏んだことがある)。"""

    def __init__(self, qapp, monkeypatch, url="https://example.com/"):
        import uuid as _uuid

        from PySide6.QtCore import QSettings
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

        self._settings_org = f"pyside6-webbluetooth-test-{_uuid.uuid4().hex}"
        monkeypatch.setattr(
            "pyside6_webbluetooth.bridge.QSettings",
            lambda *a, **k: QSettings(self._settings_org, "GrantedDevices"),
        )

        self.qapp = qapp
        self.page = QWebEnginePage(QWebEngineProfile.defaultProfile())
        self.bridge = install(self.page)
        self._loaded_box = {}
        self.page.loadFinished.connect(lambda ok: self._loaded_box.__setitem__("ok", ok))
        self.page.setUrl(QUrl(url))
        self._pump(1000)  # DocumentCreationスクリプトが確実に注入されるまで待つ

    def _pump(self, ms):
        from PySide6.QtCore import QEventLoop

        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def eval_async(self, js_expression_returning_promise, timeout_ms=4000):
        """`js_expression_returning_promise`はPromiseを返すJS式(文字列)。
        resolve時の値をJSON文字列化してPythonへ返す前提のコードを
        呼び出し側が書く(then内でwindow.__resultに格納する等)。
        ここでは共通の待ち合わせ用スロットを使う簡易ラッパーを提供する。"""
        marker = "__pyside6_test_result__"
        wrapped = (
            "window.%s = undefined;"
            "(%s).then(function(v){ window.%s = JSON.stringify({ok:true, value:v}); })"
            ".catch(function(e){ window.%s = JSON.stringify({ok:false, name:e.name, message:e.message}); });"
            "'started';"
        ) % (marker, js_expression_returning_promise, marker, marker)

        box = {}

        def started(_v):
            box["started"] = True

        self.page.runJavaScript(wrapped, 0, started)
        self._pump(150)

        waited = 0
        step = 150
        while waited < timeout_ms:
            self._pump(step)
            waited += step
            result_box = {}

            def check(v):
                result_box["v"] = v

            self.page.runJavaScript("window.%s || null" % marker, 0, check)
            self._pump(50)
            if result_box.get("v"):
                return json.loads(result_box["v"])
        raise TimeoutError("JS promise did not settle in time: %s" % js_expression_returning_promise)

    def eval_sync(self, js_expression):
        box = {}

        def cb(v):
            box["v"] = v

        self.page.runJavaScript(js_expression, 0, cb)
        self._pump(150)
        return box.get("v")

    def close(self):
        self.bridge.shutdown()
        from PySide6.QtCore import QSettings

        QSettings(self._settings_org, "GrantedDevices").clear()


@pytest.fixture
def harness(qapp, monkeypatch):
    h = _JsPageHarness(qapp, monkeypatch)
    yield h
    h.close()


class TestNavigatorBluetoothShape:
    def test_shape(self, harness):
        result = harness.eval_sync(
            """
            JSON.stringify({
              hasBluetooth: typeof navigator.bluetooth === 'object',
              hasRequestDevice: typeof navigator.bluetooth.requestDevice === 'function',
              hasGetAvailability: typeof navigator.bluetooth.getAvailability === 'function',
              hasGetDevices: typeof navigator.bluetooth.getDevices === 'function',
              isEventTarget: navigator.bluetooth instanceof EventTarget,
              hasOnAvailabilityChanged: 'onavailabilitychanged' in navigator.bluetooth
            })
            """
        )
        shape = json.loads(result)
        assert shape == {
            "hasBluetooth": True,
            "hasRequestDevice": True,
            "hasGetAvailability": True,
            "hasGetDevices": True,
            "isEventTarget": True,
            "hasOnAvailabilityChanged": True,
        }


class TestGetAvailabilityAndGetDevices:
    def test_get_availability_resolves_false_without_adapter(self, harness):
        # このサンドボックスには実BLEアダプタが無いため、bleakは
        # 例外を投げずFalseに落ち着く(ble_worker.pyのフェイルセーフ)。
        result = harness.eval_async("navigator.bluetooth.getAvailability()")
        assert result["ok"] is True
        assert result["value"] is False

    def test_get_devices_empty_when_no_grants(self, harness):
        result = harness.eval_async("navigator.bluetooth.getDevices().then(d => d.length)")
        assert result == {"ok": True, "value": 0}


class TestFullGattFlowThroughRealJs:
    """navigator.bluetooth経由でconnect -> getPrimaryService ->
    getCharacteristic -> readValue -> writeValueWithResponse ->
    startNotifications -> disconnect まで一気通貫で検証する。"""

    def _make_fake_client(self):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True
        fake_client.read_gatt_char = AsyncMock(return_value=bytearray([77]))
        fake_client.write_gatt_char = AsyncMock(return_value=None)
        fake_client.start_notify = AsyncMock(return_value=None)
        fake_client.stop_notify = AsyncMock(return_value=None)

        fake_char = MagicMock()
        fake_char.uuid = BATTERY_LEVEL_UUID
        fake_char.handle = 42
        fake_char.properties = ["read", "write", "notify"]
        fake_char.descriptors = []

        fake_service = MagicMock()
        fake_service.uuid = BATTERY_UUID
        fake_service.handle = 10
        fake_service.characteristics = [fake_char]
        fake_client.services = [fake_service]
        return fake_client

    def test_full_flow(self, harness):
        origin = "https://example.com"
        grants = {
            "dev1": {"address": "AA:AA:AA:AA:AA:AA", "name": "TestDevice", "allowedServices": [BATTERY_UUID]}
        }
        harness.bridge._save_grants(origin, grants)
        fake_client = self._make_fake_client()

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            flow_js = """
            (async function() {
                var devices = await navigator.bluetooth.getDevices();
                var device = devices[0];
                var server = await device.gatt.connect();
                var service = await server.getPrimaryService('battery_service');
                var char = await service.getCharacteristic('battery_level');
                var value = await char.readValue();
                await char.writeValueWithResponse(new Uint8Array([9]).buffer);
                await char.startNotifications();
                var connectedBeforeDisconnect = server.connected;
                device.gatt.disconnect();
                return {
                    deviceId: device.id,
                    deviceName: device.name,
                    connected: connectedBeforeDisconnect,
                    serviceUuid: service.uuid,
                    isPrimary: service.isPrimary,
                    charUuid: char.uuid,
                    properties: char.properties,
                    readByte: value.getUint8(0),
                    connectedAfterDisconnect: server.connected
                };
            })()
            """
            result = harness.eval_async(flow_js, timeout_ms=6000)

        assert result["ok"] is True, result
        v = result["value"]
        assert v["deviceId"] == "dev1"
        assert v["deviceName"] == "TestDevice"
        assert v["connected"] is True
        assert v["serviceUuid"] == BATTERY_UUID
        assert v["isPrimary"] is True
        assert v["charUuid"] == BATTERY_LEVEL_UUID
        assert v["properties"] == {
            "broadcast": False,
            "read": True,
            "writeWithoutResponse": False,
            "write": True,
            "notify": True,
            "indicate": False,
            "authenticatedSignedWrites": False,
            "reliableWrite": False,
            "writableAuxiliaries": False,
        }
        assert v["readByte"] == 77
        assert v["connectedAfterDisconnect"] is False

    def test_race_condition_fast_mock_still_resolves(self, harness):
        """回帰テスト: bleak操作が(モックにより)ほぼ瞬時に完了しても
        connect()のPromiseがハングしないこと。"""
        origin = "https://example.com"
        harness.bridge._save_grants(
            origin,
            {"dev1": {"address": "AA:AA:AA:AA:AA:AA", "name": "TestDevice", "allowedServices": [BATTERY_UUID]}},
        )
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)  # 即座に完了する

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            result = harness.eval_async(
                """
                navigator.bluetooth.getDevices().then(function(devices) {
                    return devices[0].gatt.connect();
                }).then(function(server) {
                    return server.connected;
                })
                """,
                timeout_ms=4000,
            )
        assert result == {"ok": True, "value": True}


class TestNotifyDeliveryThroughRealJs:
    def test_characteristic_value_changed_event_fires(self, harness):
        origin = "https://example.com"
        harness.bridge._save_grants(
            origin,
            {"dev1": {"address": "AA:AA:AA:AA:AA:AA", "name": "TestDevice", "allowedServices": [BATTERY_UUID]}},
        )

        captured = {}
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True

        fake_char = MagicMock()
        fake_char.uuid = HEART_RATE_MEASUREMENT_UUID
        fake_char.handle = 99
        fake_char.properties = ["notify"]
        fake_char.descriptors = []
        fake_service = MagicMock()
        fake_service.uuid = BATTERY_UUID
        fake_service.handle = 10
        fake_service.characteristics = [fake_char]
        fake_client.services = [fake_service]

        async def fake_start_notify(char_specifier, callback):
            captured["cb"] = callback

        fake_client.start_notify = AsyncMock(side_effect=fake_start_notify)

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            setup_js = """
            (async function() {
                window.__notifyLog = [];
                var devices = await navigator.bluetooth.getDevices();
                var device = devices[0];
                await device.gatt.connect();
                var service = await device.gatt.getPrimaryService('battery_service');
                var char = await service.getCharacteristic('heart_rate_measurement');
                char.oncharacteristicvaluechanged = function() {
                    window.__notifyLog.push(char.value.getUint8(0));
                };
                await char.startNotifications();
                return true;
            })()
            """
            setup_result = harness.eval_async(setup_js, timeout_ms=4000)
            assert setup_result == {"ok": True, "value": True}

            assert "cb" in captured, "start_notify callback was never captured"
            fake_sender = MagicMock()
            fake_sender.uuid = HEART_RATE_MEASUREMENT_UUID
            captured["cb"](fake_sender, bytearray([72]))
            harness._pump(500)

            log = harness.eval_sync("JSON.stringify(window.__notifyLog)")
            assert json.loads(log) == [72]


class TestQtBluetoothBackendSelection:
    """install(page, backend="qtbluetooth")が実際にQtBluetoothWorkerを
    使い、navigator.bluetooth経由でも(getAvailability()を通じて)
    最後まで正しく動くことを確認する。GATTフロー自体の検証は
    test_qt_ble_worker.pyで行っているので、ここではバックエンド選択の
    配線そのものだけを対象にする。"""

    def test_qtbluetooth_backend_wired_through_navigator_bluetooth(self, qapp, monkeypatch):
        import uuid as _uuid

        from PySide6.QtCore import QSettings
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

        settings_org = f"pyside6-webbluetooth-test-{_uuid.uuid4().hex}"
        monkeypatch.setattr(
            "pyside6_webbluetooth.bridge.QSettings",
            lambda *a, **k: QSettings(settings_org, "GrantedDevices"),
        )

        page = QWebEnginePage(QWebEngineProfile.defaultProfile())
        bridge = install(page, backend="qtbluetooth")
        try:
            assert type(bridge._worker).__name__ == "QtBluetoothWorker"

            harness = _JsPageHarness.__new__(_JsPageHarness)
            harness.qapp = qapp
            harness.page = page
            harness.bridge = bridge
            harness._settings_org = settings_org
            harness._loaded_box = {}
            page.loadFinished.connect(lambda ok: harness._loaded_box.__setitem__("ok", ok))
            page.setUrl(QUrl("https://example.com/"))
            harness._pump(1000)

            result = harness.eval_async("navigator.bluetooth.getAvailability()")
            assert result == {"ok": True, "value": False}
        finally:
            bridge.shutdown()
            from PySide6.QtCore import QSettings as _QSettings

            _QSettings(settings_org, "GrantedDevices").clear()
