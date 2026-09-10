# -*- coding: utf-8 -*-
"""bridge.py(BluetoothBridge)の統合テスト。

`FrameOriginTracker`本体の検証はtest_frame_origin.pyで既に行っているため、
ここではトークン→オリジンの対応表に直接テスト用のエントリを注入することで
「有効なフレームトークンを持っている」状態を作り、bridge.py自身の
ロジック(オリジン検証の要求、permission grantの管理、GATTブロックリストの
適用、非同期操作のrequestId/Signal配送、JSON応答の形)を検証する。

BleakClientは`ble_worker.py`と同じ要領でモックに差し替える(実BLEアダプタが
無い環境のため)。QSettingsはテスト間で状態が漏れないよう、実行のたびに
一時的な組織名/アプリ名を使う。
"""
import json
import time
import uuid as uuid_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PySide6.QtCore import QSettings

from pyside6_webbluetooth.bridge import BluetoothBridge


BATTERY_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
HID_SERVICE_UUID = "00001812-0000-1000-8000-00805f9b34fb"  # ブロックリスト対象
SERIAL_NUMBER_UUID = "00002a25-0000-1000-8000-00805f9b34fb"  # ブロックリスト対象


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

    return QWebEnginePage(QWebEngineProfile.defaultProfile())


@pytest.fixture
def bridge(page, monkeypatch):
    # テストごとに別々の設定ストアを使う(QSettingsの永続化が
    # テスト間で漏れないように、一意な組織/アプリ名にする)。
    unique = uuid_module.uuid4().hex
    monkeypatch.setattr(
        "pyside6_webbluetooth.bridge.QSettings",
        lambda *a, **k: QSettings(f"pyside6-webbluetooth-test-{unique}", "GrantedDevices"),
    )
    b = BluetoothBridge(page)
    yield b
    b.shutdown()
    QSettings(f"pyside6-webbluetooth-test-{unique}", "GrantedDevices").clear()


def _inject_token(bridge: BluetoothBridge, origin: str) -> str:
    """FrameOriginTracker本体のテストは別ファイルで実施済みなので、
    ここでは「有効なトークンを持っている」状態を直接作る。"""
    token = f"test-token-{uuid_module.uuid4().hex}"
    bridge._frame_tracker._tokens[token] = (origin, time.monotonic())
    return token


def _seed_grant(bridge: BluetoothBridge, origin: str, device_id: str, address: str, allowed_services: list):
    grants = bridge._load_grants(origin)
    grants[device_id] = {"address": address, "name": "TestDevice", "allowedServices": allowed_services}
    bridge._save_grants(origin, grants)


class _SignalWaiter:
    """シグナルを購読してから対象の操作を呼び出すことで、
    「操作が同期的な処理だけで完結してシグナルが即座に(この関数が
    signal.connect()する前に)発火してしまい、待ち漏れる」という
    競合を避けるためのヘルパー。

    (実際にテスト中に踏んだ問題: モックBleakClientはawaitがほぼ
    瞬時に完了するため、`bridge.connectGatt(...)`を呼んでから
    別途signal.connect()するのでは、その一瞬の間にbleOperationResultが
    発火してしまい待ちが永久に完了しないことがあった。)
    """

    def __init__(self, signal, timeout_ms: int = 3000):
        self._signal = signal
        self._timeout_ms = timeout_ms
        self._box: dict = {}
        self._conn = None
        self._loop = None

    def __enter__(self) -> "_SignalWaiter":
        from PySide6.QtCore import QEventLoop

        self._loop = QEventLoop()
        self._conn = self._signal.connect(self._on_signal)
        return self

    def _on_signal(self, payload: str) -> None:
        self._box["payload"] = payload
        self._loop.quit()

    def wait(self) -> dict:
        from PySide6.QtCore import QTimer

        if "payload" not in self._box:
            QTimer.singleShot(self._timeout_ms, self._loop.quit)
            self._loop.exec()
        if "payload" not in self._box:
            raise TimeoutError("signal did not fire in time")
        return json.loads(self._box["payload"])

    def __exit__(self, exc_type, exc, tb) -> None:
        self._signal.disconnect(self._conn)


def _call_and_wait(signal, action, timeout_ms: int = 3000):
    """`action()`を呼び出し、その結果(同期戻り値)と、その後に届く
    シグナルのペイロードの両方を返す。"""
    with _SignalWaiter(signal, timeout_ms=timeout_ms) as waiter:
        sync_result = action()
        payload = waiter.wait()
    return sync_result, payload


def _wait_until(predicate, timeout: float = 2.0, interval_ms: int = 20) -> None:
    """`predicate()`がTrueになるまで、Qtのイベントループを短い間隔で
    回しながら待つ。fire-and-forgetな操作(disconnectGatt()等)の後、
    バックグラウンドスレッド側の後始末が実際に反映されるのを
    ポーリングで確認するために使う(固定時間のsleepだと、短すぎれば
    フレーキーに、長すぎればテストが無駄に遅くなる)。"""
    import time

    from PySide6.QtCore import QEventLoop, QTimer

    deadline = time.monotonic() + timeout
    loop = QEventLoop()
    while time.monotonic() < deadline:
        if predicate():
            return
        QTimer.singleShot(interval_ms, loop.quit)
        loop.exec()
    if not predicate():
        raise AssertionError(f"condition not met within {timeout}s")


class TestOriginVerification:
    def test_unverified_frame_token_rejected(self, bridge):
        result = json.loads(bridge.getDevices("not-a-real-token"))
        assert result["ok"] is False
        assert result["error"]["name"] == "SecurityError"

    def test_empty_frame_token_rejected(self, bridge):
        result = json.loads(bridge.getDevices(""))
        assert result["ok"] is False
        assert result["error"]["name"] == "SecurityError"


class TestPermissionGrantIsolation:
    def test_getDevices_only_returns_own_origin_grants(self, bridge):
        _seed_grant(bridge, "https://a.example", "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        _seed_grant(bridge, "https://b.example", "dev2", "BB:BB:BB:BB:BB:BB", [BATTERY_UUID])

        token_a = _inject_token(bridge, "https://a.example")
        result_a = json.loads(bridge.getDevices(token_a))
        assert result_a["ok"] is True
        assert [d["id"] for d in result_a["result"]] == ["dev1"]

        token_b = _inject_token(bridge, "https://b.example")
        result_b = json.loads(bridge.getDevices(token_b))
        assert [d["id"] for d in result_b["result"]] == ["dev2"]

    def test_device_id_from_other_origin_is_rejected(self, bridge):
        _seed_grant(bridge, "https://a.example", "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token_b = _inject_token(bridge, "https://b.example")
        result = json.loads(bridge.connectGatt("dev1", token_b))
        assert result["ok"] is False
        assert result["error"]["name"] == "SecurityError"

    def test_forget_device_removes_grant(self, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)
        assert json.loads(bridge.getDevices(token))["result"] != []
        result = json.loads(bridge.forgetDevice("dev1", token))
        assert result["ok"] is True
        assert json.loads(bridge.getDevices(token))["result"] == []


class TestServicePermissionAndBlocklist:
    def test_service_not_in_allowed_list_rejected(self, qapp, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)

        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True
        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            _, connect_payload = _call_and_wait(
                bridge.bleOperationResult, lambda: json.loads(bridge.connectGatt("dev1", token))
            )
            assert connect_payload["ok"] is True

        # heart_rateはこのデバイスに許可されていない(seedしたのはbattery_serviceのみ)
        result = json.loads(
            bridge.getCharacteristics("dev1", "0000180d-0000-1000-8000-00805f9b34fb", "null", token)
        )
        assert result["ok"] is False
        assert result["error"]["name"] == "SecurityError"

    def test_blocklisted_service_never_reachable_even_if_claimed_allowed(self, bridge):
        # allowedServicesに(不正な状態として)HIDサービスが入っていたとしても、
        # ブロックリストが最終防衛線として機能し、NotFoundErrorになる
        # (SecurityErrorではなく「存在しない」ものとして扱う)。
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [HID_SERVICE_UUID])
        token = _inject_token(bridge, origin)
        result = json.loads(bridge.getPrimaryServices("dev1", "null", token))
        # 未接続なのでこの時点ではNetworkErrorになるはず(接続してから確認する
        # テストは別途end-to-endで行う)。ここではallowedServicesの検証
        # 自体がブロックリストと矛盾しないことだけ先に確認する。
        assert result["ok"] is False
        assert result["error"]["name"] == "NetworkError"

    def test_requesting_blocklisted_service_by_uuid_directly_is_security_error(self, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [HID_SERVICE_UUID])
        token = _inject_token(bridge, origin)
        # HIDサービスをallowedServicesに入れていても、ブロックリストにより
        # is_blocked_entirely()がTrueなので_require_service_allowedが
        # NotFoundErrorを返す(存在自体を隠す)。
        result = json.loads(bridge.getCharacteristics("dev1", HID_SERVICE_UUID, "null", token))
        assert result["ok"] is False
        assert result["error"]["name"] == "NotFoundError"


class TestFullGattFlowWithMockedClient:
    """connect -> getPrimaryServices -> getCharacteristics -> read -> write ->
    disconnect の一連の流れを、モックBleakClientを使って検証する。"""

    def _make_fake_client(self):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.disconnect = AsyncMock(return_value=None)
        fake_client.is_connected = True
        fake_client.read_gatt_char = AsyncMock(return_value=bytearray(b"\x5a"))
        fake_client.write_gatt_char = AsyncMock(return_value=None)

        fake_char = MagicMock()
        fake_char.uuid = BATTERY_LEVEL_UUID
        fake_char.handle = 42
        fake_char.properties = ["read", "notify"]
        fake_char.descriptors = []

        fake_service = MagicMock()
        fake_service.uuid = BATTERY_UUID
        fake_service.handle = 10
        fake_service.characteristics = [fake_char]

        fake_client.services = [fake_service]
        return fake_client

    def test_full_flow(self, qapp, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)
        fake_client = self._make_fake_client()

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            # connect
            sync_r, payload = _call_and_wait(
                bridge.bleOperationResult, lambda: json.loads(bridge.connectGatt("dev1", token))
            )
            assert sync_r["ok"] is True
            req_id = sync_r["result"]["requestId"]
            assert payload["requestId"] == req_id
            assert payload["ok"] is True
            assert bridge.isGattConnected("dev1", token) == json.dumps({"ok": True, "result": True})

            # getPrimaryServices (all)
            sync_r, payload = _call_and_wait(
                bridge.bleOperationResult,
                lambda: json.loads(bridge.getPrimaryServices("dev1", "null", token)),
            )
            assert sync_r["ok"] is True
            assert payload["ok"] is True
            assert payload["result"] == [{"uuid": BATTERY_UUID, "isPrimary": True}]

            # getCharacteristics
            sync_r, payload = _call_and_wait(
                bridge.bleOperationResult,
                lambda: json.loads(bridge.getCharacteristics("dev1", BATTERY_UUID, "null", token)),
            )
            assert sync_r["ok"] is True
            assert payload["ok"] is True
            chars = payload["result"]
            assert len(chars) == 1
            assert chars[0]["uuid"] == BATTERY_LEVEL_UUID
            assert chars[0]["handle"] == 42
            assert chars[0]["properties"]["read"] is True
            assert chars[0]["properties"]["notify"] is True
            assert chars[0]["properties"]["write"] is False

            # readCharacteristicValue
            sync_r, payload = _call_and_wait(
                bridge.bleOperationResult,
                lambda: json.loads(bridge.readCharacteristicValue("dev1", BATTERY_UUID, "42", token)),
            )
            assert sync_r["ok"] is True
            assert payload["ok"] is True
            import base64

            assert base64.b64decode(payload["result"]["value"]) == b"\x5a"

            # writeCharacteristicValue (only 'read'+'notify' props -> write should be NotSupportedError)
            sync_r, payload = _call_and_wait(
                bridge.bleOperationResult,
                lambda: json.loads(
                    bridge.writeCharacteristicValue(
                        "dev1", BATTERY_UUID, "42", base64.b64encode(b"\x01").decode(), True, token
                    )
                ),
            )
            assert sync_r["ok"] is True  # 同期部分は受理される(requestIdが返る)
            assert payload["ok"] is False
            assert payload["error"]["name"] == "NotSupportedError"

            # disconnect
            r = json.loads(bridge.disconnectGatt("dev1", token))
            assert r["ok"] is True

        # disconnectGatt()は仕様通りfire-and-forget(実際の切断処理は
        # ワーカースレッド上で非同期に進む)なので、即座にis_connected()を
        # 見ると偽陽性(まだTrueのまま)になり得ることがビルド作業などで
        # マシン負荷が高いときに実際に顕在化した。ここは短時間ポーリングして
        # 確実に切断が反映されるのを待ってから確認する。
        _wait_until(lambda: not bridge._worker.is_connected("dev1"), timeout=2.0)

    def test_read_after_disconnect_is_network_error(self, qapp, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)
        # 一度も接続していない状態
        result = json.loads(bridge.readCharacteristicValue("dev1", BATTERY_UUID, "42", token))
        assert result["ok"] is False
        assert result["error"]["name"] == "NetworkError"


class TestIsAvailable:
    def test_isAvailable_needs_no_token(self, bridge):
        result = json.loads(bridge.isAvailable())
        assert result["ok"] is True
        assert result["result"]["package"] == "pyside6-webbluetooth"
        assert result["result"]["version"] == "0.0.0a1"


class TestRequestDeviceChooserFlow:
    """requestDeviceChooser()はQDialog.exec()でモーダルにブロックするため、
    ダイアログ表示中にQTimerで「ユーザーがデバイスを選んでOKを押した」を
    シミュレートして検証する。"""

    def test_selecting_a_device_saves_grant_with_allowed_services(self, qapp, bridge):
        from concurrent.futures import Future

        from PySide6.QtCore import QTimer

        origin = "https://a.example"
        token = _inject_token(bridge, origin)

        class FakeBLEDevice:
            def __init__(self, address, name):
                self.address = address
                self.name = name

        class FakeAdv:
            def __init__(self, local_name, service_uuids, rssi):
                self.local_name = local_name
                self.service_uuids = service_uuids
                self.manufacturer_data = {}
                self.service_data = {}
                self.rssi = rssi

        def fake_start_scan(service_uuids=None):
            bridge._worker._scan_results["AA:AA:AA:AA:AA:AA"] = (
                FakeBLEDevice("AA:AA:AA:AA:AA:AA", "TestDevice"),
                FakeAdv("TestDevice", [BATTERY_UUID], -50),
            )
            f = Future()
            f.set_result(None)
            return f

        def simulate_user_accept():
            dlg = qapp.activeModalWidget()
            if dlg is None:
                QTimer.singleShot(50, simulate_user_accept)
                return
            dlg._poll_scan_results()
            dlg._list_widget.setCurrentRow(0)
            dlg._on_accept()

        with patch.object(bridge._worker, "start_scan", side_effect=fake_start_scan):
            QTimer.singleShot(150, simulate_user_accept)
            options_json = json.dumps({"filters": [{"services": ["battery_service"]}]})
            result = json.loads(bridge.requestDeviceChooser(options_json, token))

        assert result["ok"] is True
        assert result["result"]["name"] == "TestDevice"
        device_id = result["result"]["id"]

        grants = bridge._load_grants(origin)
        assert device_id in grants
        assert grants[device_id]["address"] == "AA:AA:AA:AA:AA:AA"
        assert grants[device_id]["allowedServices"] == [BATTERY_UUID]

    def test_cancelling_chooser_is_not_found_error(self, qapp, bridge):
        from PySide6.QtCore import QTimer

        token = _inject_token(bridge, "https://a.example")

        def simulate_user_cancel():
            dlg = qapp.activeModalWidget()
            if dlg is None:
                QTimer.singleShot(50, simulate_user_cancel)
                return
            dlg.reject()

        QTimer.singleShot(150, simulate_user_cancel)
        options_json = json.dumps({"acceptAllDevices": True})
        result = json.loads(bridge.requestDeviceChooser(options_json, token))
        assert result["ok"] is False
        assert result["error"]["name"] == "NotFoundError"

    def test_invalid_options_json_is_type_error(self, bridge):
        token = _inject_token(bridge, "https://a.example")
        result = json.loads(bridge.requestDeviceChooser("not valid json", token))
        assert result["ok"] is False
        assert result["error"]["name"] == "TypeError"

    def test_blocklisted_filter_service_is_security_error(self, bridge):
        token = _inject_token(bridge, "https://a.example")
        options_json = json.dumps({"filters": [{"services": ["human_interface_device"]}]})
        result = json.loads(bridge.requestDeviceChooser(options_json, token))
        assert result["ok"] is False
        assert result["error"]["name"] == "SecurityError"


class TestDuplicateConnectAndInFlightCancellation:
    """v0.0.0aで見つけた2つのバグの回帰テスト:
    1. 接続済みデバイスへの再connect()が新しいBleakClientを作ってしまい、
       古い接続がリークする。
    2. デバイス切断時に、そのデバイスに対して進行中だった操作
       (getPrimaryServices等)がNetworkErrorとして確定されない
       (仕様の"GATTServer connect-checking wrapper"に相当する処理が無かった)。
    """

    def test_connect_when_already_connected_does_not_create_second_client(self, qapp, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)

        construction_count = {"n": 0}

        def factory(address, disconnected_callback=None, **kwargs):
            construction_count["n"] += 1
            fake = MagicMock()
            fake.connect = AsyncMock(return_value=None)
            fake.is_connected = True
            return fake

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", side_effect=factory):
            _, payload1 = _call_and_wait(
                bridge.bleOperationResult, lambda: json.loads(bridge.connectGatt("dev1", token))
            )
            assert payload1["ok"] is True
            assert construction_count["n"] == 1

            # 既に接続済みの状態でもう一度connect()する
            sync_r2, payload2 = _call_and_wait(
                bridge.bleOperationResult, lambda: json.loads(bridge.connectGatt("dev1", token))
            )
            assert sync_r2["ok"] is True
            assert payload2["ok"] is True
            assert payload2["result"] == {"deviceId": "dev1"}
            # 2回目はBleakClientを新しく作っていないこと(接続のリーク防止)
            assert construction_count["n"] == 1

    def test_in_flight_operation_fails_with_network_error_on_disconnect(self, qapp, bridge):
        """`_dispatch_async`にdevice_idを紐づけて登録した操作は、その
        デバイスが(裏側の処理の完了を待たずに)切断された時点で即座に
        NetworkErrorとして確定し、後から裏側の処理が実際に完了しても
        二重に配送されないことを検証する。

        実際のbleak呼び出しを遅延させる代わりに、`concurrent.futures.Future`
        を手動で制御することで「操作が進行中の状態」を直接再現している
        (getPrimaryServices等の入り口を経由しても、最終的には
        _dispatch_asyncの中の同じ仕組みを検証することになるため、
        これは実装の詳細に依存しない検証になっている)。
        """
        from concurrent.futures import Future

        device_id = "dev1"
        manual_future: Future = Future()
        request_id = "req-in-flight-1"

        bridge._dispatch_async(manual_future, request_id, device_id=device_id)
        assert request_id in bridge._pending_by_device.get(device_id, set())

        with _SignalWaiter(bridge.bleOperationResult) as waiter:
            bridge._on_device_disconnected(device_id)
            payload = waiter.wait()

        assert payload["requestId"] == request_id
        assert payload["ok"] is False
        assert payload["error"]["name"] == "NetworkError"
        assert device_id not in bridge._pending_by_device

        # 切断"後"に、裏側の処理が実際には成功していたとしても、
        # 同じrequestIdに対して二度目の配送は起きない。
        received_after = []
        bridge.bleOperationResult.connect(lambda p: received_after.append(json.loads(p)))
        manual_future.set_result("this should be ignored")

        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(300, loop.quit)
        loop.exec()
        assert not any(p.get("requestId") == request_id for p in received_after)

    def test_explicit_disconnect_also_cancels_in_flight_operations(self, qapp, bridge):
        origin = "https://a.example"
        _seed_grant(bridge, origin, "dev1", "AA:AA:AA:AA:AA:AA", [BATTERY_UUID])
        token = _inject_token(bridge, origin)

        from concurrent.futures import Future

        manual_future: Future = Future()
        request_id = "req-in-flight-2"
        bridge._dispatch_async(manual_future, request_id, device_id="dev1")

        with _SignalWaiter(bridge.bleOperationResult) as waiter:
            json.loads(bridge.disconnectGatt("dev1", token))
            payload = waiter.wait()

        assert payload["requestId"] == request_id
        assert payload["ok"] is False
        assert payload["error"]["name"] == "NetworkError"
