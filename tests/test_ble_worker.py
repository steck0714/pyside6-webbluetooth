# -*- coding: utf-8 -*-
"""ble_worker.pyのテスト。

このサンドボックスには実BLEアダプタが無いため、2段構えで検証する:

  1. `AsyncioThread` / `BleWorker.check_availability()` は本物のbleakを
     使って実行し、「アダプタが無い環境では例外を投げずFalseを返す」という
     フェイルセーフな挙動そのものを検証する(これは実機・モック両方が
     混ざったテストになるが、この宣言的な挙動こそが実際にサンドボックスで
     確認したかったことなので、モックに置き換えない)。
  2. connect/read/write/notify等、bleak.BleakClientとのやり取りの
     "配線"(client_idの管理、BleakErrorのBleOperationErrorへの変換、
     disconnected_callbackでのクリーンアップ)は、BleakClientをモックに
     差し替えて検証する。これはbleak自体の正しさではなく、
     BleWorker側のオーケストレーションロジックの正しさを検証するもの。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError

from pyside6_webbluetooth.ble_worker import AsyncioThread, BleOperationError, BleWorker, new_client_id


class TestAsyncioThread:
    def test_start_submit_stop(self):
        at = AsyncioThread()
        at.start()
        try:
            assert at.loop is not None

            async def add(a, b):
                await asyncio.sleep(0.01)
                return a + b

            fut = at.submit(add(2, 3))
            assert fut.result(timeout=2) == 5
        finally:
            at.stop()
        assert at.loop is None

    def test_submit_before_start_raises(self):
        at = AsyncioThread()

        async def noop():
            return None

        coro = noop()
        try:
            with pytest.raises(RuntimeError):
                at.submit(coro)
        finally:
            coro.close()

    def test_exception_in_coroutine_propagates_via_future(self):
        at = AsyncioThread()
        at.start()
        try:

            async def boom():
                raise ValueError("kaboom")

            fut = at.submit(boom())
            with pytest.raises(ValueError, match="kaboom"):
                fut.result(timeout=2)
        finally:
            at.stop()


class TestCheckAvailabilityNoAdapter:
    """このサンドボックス環境(実BLEアダプタ・D-Bus無し)でのフェイルセーフ動作。"""

    def test_returns_false_without_raising(self):
        worker = BleWorker()
        worker.start()
        try:
            available = worker.check_availability().result(timeout=5)
            assert available is False
        finally:
            worker.stop()


@pytest.fixture
def worker():
    w = BleWorker()
    w.start()
    yield w
    w.stop()


class TestConnectDisconnectWithMockedClient:
    def test_connect_registers_client(self, worker):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            client_id = new_client_id()
            worker.connect(client_id, "AA:BB:CC:DD:EE:FF").result(timeout=2)

        assert worker.is_connected(client_id)
        fake_client.connect.assert_awaited_once()

    def test_connect_failure_wraps_as_ble_operation_error(self, worker):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(side_effect=BleakError("device unreachable"))

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            client_id = new_client_id()
            with pytest.raises(BleOperationError) as excinfo:
                worker.connect(client_id, "AA:BB:CC:DD:EE:FF").result(timeout=2)
        assert excinfo.value.web_bluetooth_error_name == "NetworkError"
        assert not worker.is_connected(client_id)

    def test_disconnect_removes_client(self, worker):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.disconnect = AsyncMock(return_value=None)
        fake_client.is_connected = True

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            client_id = new_client_id()
            worker.connect(client_id, "AA:BB:CC:DD:EE:FF").result(timeout=2)
            worker.disconnect(client_id).result(timeout=2)

        fake_client.disconnect.assert_awaited_once()
        assert not worker.is_connected(client_id)

    def test_spontaneous_disconnect_callback_cleans_up(self, worker):
        """デバイス側からの切断(disconnected_callback)でも_clientsから
        取り除かれ、bridge.py側へ通知するためのon_disconnectedが呼ばれること。"""
        captured_callback = {}

        def fake_bleak_client_factory(address, disconnected_callback=None, **kwargs):
            fake = MagicMock()
            fake.connect = AsyncMock(return_value=None)
            fake.is_connected = True
            captured_callback["cb"] = disconnected_callback
            return fake

        on_disconnected_calls = []

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", side_effect=fake_bleak_client_factory):
            client_id = new_client_id()
            worker.connect(
                client_id, "AA:BB:CC:DD:EE:FF", on_disconnected=on_disconnected_calls.append
            ).result(timeout=2)
            assert worker.is_connected(client_id)

            # デバイス側が切断した状況をシミュレート: bleakが
            # disconnected_callback(client)を呼ぶ。
            captured_callback["cb"](MagicMock())

        assert not worker.is_connected(client_id)
        assert on_disconnected_calls == [client_id]


class TestReadWriteWithMockedClient:
    def _connected_worker(self, worker, fake_client):
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True
        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            client_id = new_client_id()
            worker.connect(client_id, "AA:BB:CC:DD:EE:FF").result(timeout=2)
        return client_id

    def test_read_characteristic_returns_bytes(self, worker):
        fake_client = MagicMock()
        fake_client.read_gatt_char = AsyncMock(return_value=bytearray(b"\x01\x02"))
        client_id = self._connected_worker(worker, fake_client)

        result = worker.read_characteristic(client_id, "0000180f-0000-1000-8000-00805f9b34fb").result(
            timeout=2
        )
        assert result == b"\x01\x02"
        assert isinstance(result, bytes)

    def test_read_characteristic_error_wrapped(self, worker):
        fake_client = MagicMock()
        fake_client.read_gatt_char = AsyncMock(side_effect=BleakError("not connected"))
        client_id = self._connected_worker(worker, fake_client)

        with pytest.raises(BleOperationError):
            worker.read_characteristic(client_id, "0000180f-0000-1000-8000-00805f9b34fb").result(
                timeout=2
            )

    def test_write_characteristic_passes_response_flag(self, worker):
        fake_client = MagicMock()
        fake_client.write_gatt_char = AsyncMock(return_value=None)
        client_id = self._connected_worker(worker, fake_client)

        worker.write_characteristic(
            client_id, "0000180f-0000-1000-8000-00805f9b34fb", b"\x05", True
        ).result(timeout=2)
        fake_client.write_gatt_char.assert_awaited_once_with(
            "0000180f-0000-1000-8000-00805f9b34fb", b"\x05", response=True
        )

    def test_operation_without_connection_raises(self, worker):
        with pytest.raises(BleOperationError):
            worker.read_characteristic("nonexistent-client-id", "0000180f-0000-1000-8000-00805f9b34fb").result(
                timeout=2
            )


class TestNotifyWithMockedClient:
    def test_start_notify_delivers_uuid_and_bytes(self, worker):
        fake_client = MagicMock()
        fake_client.connect = AsyncMock(return_value=None)
        fake_client.is_connected = True
        captured = {}

        async def fake_start_notify(char_specifier, callback):
            captured["callback"] = callback

        fake_client.start_notify = AsyncMock(side_effect=fake_start_notify)

        with patch("pyside6_webbluetooth.ble_worker.BleakClient", return_value=fake_client):
            client_id = new_client_id()
            worker.connect(client_id, "AA:BB:CC:DD:EE:FF").result(timeout=2)

        received = []
        worker.start_notify(
            client_id, "00002a37-0000-1000-8000-00805f9b34fb", lambda uuid, data: received.append((uuid, data))
        ).result(timeout=2)

        # bleakが通知を受け取った状況をシミュレートする
        fake_sender = MagicMock()
        fake_sender.uuid = "00002a37-0000-1000-8000-00805f9b34fb"
        captured["callback"](fake_sender, bytearray(b"\x64"))

        assert received == [("00002a37-0000-1000-8000-00805f9b34fb", b"\x64")]
