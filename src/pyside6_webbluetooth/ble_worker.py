# -*- coding: utf-8 -*-
"""BLE操作の非同期実行基盤。

## なぜpyside6-webusbとここが一番違うのか

pyside6-webusbのbridge.pyは、pyusb(libusbの薄いラッパー)への呼び出しを
Qtのメインスレッド上で同期的に(ブロッキングで)行っていた。USBのバルク転送は
通常ミリ秒単位で終わるため、これでも実用上は問題にならない。

しかしBluetooth LEでは:
  - requestDevice()のスキャンは数百ms〜数秒
  - gatt.connect()も数百ms〜数秒(デバイスによってはもっと長い)
  - サービス探索も接続直後は追加の往復が発生することがある

これらをQtのメインスレッドで同期的に(ブロッキングで)行うと、その間
ブラウザのUIそのものが固まってしまう。加えて、BLE操作の標準的なPython実装
である bleak は asyncio ベースであり、Qtのイベントループとは別物である
(PySide6にはQtのイベントループ上でasyncioコルーチンを直接動かす標準機構が
無い)。

そこで本モジュールは、bleakの操作を専用のバックグラウンドスレッド上で
独自のasyncioイベントループとして走らせ、Qtのメインスレッド(bridge.py)
からは `concurrent.futures.Future` 経由で結果を受け取る形にする:

  1. `AsyncioThread` がバックグラウンドスレッドでasyncioループを起動する。
  2. `BleWorker` がbleakの実際の操作(スキャン/接続/GATT読み書き等)を
     コルーチンとして実装し、`AsyncioThread.submit()` 経由でそのループに
     投入する。
  3. 返ってきた `Future` に `add_done_callback()` を付け、完了時に
     Qt Signal(`bridge.py`側で定義)をemitする。

     実機で検証済み: `Future.add_done_callback()`のコールバックは
     バックグラウンドスレッド上で実行されるが、そこからQt Signalを
     emitしても(Signalの所有元QObjectがメインスレッド上に存在する限り)
     Qtの自動キュー接続によって安全にメインスレッドへ配送される。
     これによりbridge.py側は「別スレッドから呼ばれるかもしれない」
     ことを気にせず、通常通りSignal/Slotだけで完結したコードを書ける。

## エラーハンドリングについて

BlueZ(Linux)がまったく利用できない環境(D-Busシステムバスすら存在しない
コンテナ等)では、bleakは`BleakError`ではなく`FileNotFoundError`のような
OSレベルの例外を送出することを実機で確認している。そのため本モジュールは
特定の例外型に依存せず、bleak呼び出し全体を広く`Exception`で捕捉し、
「Bluetoothが利用できない」という状態として扱う。
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import uuid as uuid_module
from concurrent.futures import Future
from typing import Any, Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError


class AsyncioThread:
    """バックグラウンドスレッドで専用のasyncioイベントループを走らせる。"""

    def __init__(self, name: str = "pyside6-webbluetooth-asyncio") -> None:
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def submit(self, coro) -> Future:
        if self._loop is None:
            raise RuntimeError("AsyncioThread has not been started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self, timeout: float = 5.0) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._loop = None
        self._thread = None


class BleOperationError(RuntimeError):
    """BLE操作(スキャン/接続/読み書き等)が失敗したことを表す。

    `web_bluetooth_error_name` に、呼び出し側(bridge.py)がJSへ投げ返す
    べきDOMException名のヒントを保持する(未指定ならNetworkErrorとして
    扱われることを想定)。"""

    def __init__(self, message: str, *, web_bluetooth_error_name: str = "NetworkError"):
        super().__init__(message)
        self.web_bluetooth_error_name = web_bluetooth_error_name


class BleWorker:
    """bleakによる実際のBLE操作をバックグラウンドの`AsyncioThread`上で行う。

    このクラス自体のメソッドはすべてQtメインスレッドから呼ばれる想定で、
    重い処理を一切含まない(コルーチンを組み立てて`submit()`するだけ)。
    実際の待ち時間が発生する処理はすべて`_asyncio_thread`上で実行される。
    """

    def __init__(self) -> None:
        self._asyncio_thread = AsyncioThread()
        self._scanner: Optional[BleakScanner] = None
        self._scan_results: dict[str, tuple[BLEDevice, AdvertisementData]] = {}
        self._scan_lock = threading.Lock()
        self._clients: dict[str, BleakClient] = {}
        self._clients_lock = threading.Lock()

    # --- ライフサイクル ---

    def start(self) -> None:
        self._asyncio_thread.start()

    def stop(self) -> None:
        with self._clients_lock:
            client_ids = list(self._clients.keys())
        for client_id in client_ids:
            with contextlib.suppress(Exception):
                self.disconnect(client_id).result(timeout=5.0)
        if self._scanner is not None:
            with contextlib.suppress(Exception):
                self.stop_scan().result(timeout=5.0)
        self._asyncio_thread.stop()

    def submit(self, coro) -> Future:
        return self._asyncio_thread.submit(coro)

    # --- 可用性 ---

    async def _check_availability_async(self) -> bool:
        """アダプタの存在確認。実際に短いスキャンを試み、例外なく開始できれば
        利用可能とみなす(bleakには`getAvailability()`に直接対応する軽量APIが
        無いため)。"""
        try:
            scanner = BleakScanner()
            await scanner.start()
            await scanner.stop()
            return True
        except Exception:
            return False

    def check_availability(self) -> Future:
        return self.submit(self._check_availability_async())

    # --- スキャン(requestDevice()のチューザー用) ---

    async def _start_scan_async(self, service_uuids: Optional[list] = None) -> None:
        def on_detect(device: BLEDevice, adv: AdvertisementData) -> None:
            with self._scan_lock:
                self._scan_results[device.address] = (device, adv)

        if self._scanner is not None:
            with contextlib.suppress(Exception):
                await self._scanner.stop()
        with self._scan_lock:
            self._scan_results.clear()
        try:
            self._scanner = BleakScanner(
                detection_callback=on_detect,
                service_uuids=service_uuids or None,
            )
            await self._scanner.start()
        except Exception as exc:
            self._scanner = None
            raise BleOperationError(
                f"failed to start BLE scan: {exc}", web_bluetooth_error_name="NotFoundError"
            ) from exc

    async def _stop_scan_async(self) -> None:
        if self._scanner is not None:
            with contextlib.suppress(Exception):
                await self._scanner.stop()
            self._scanner = None

    def start_scan(self, service_uuids: Optional[list] = None) -> Future:
        return self.submit(self._start_scan_async(service_uuids))

    def stop_scan(self) -> Future:
        return self.submit(self._stop_scan_async())

    def snapshot_scan_results(self) -> dict[str, tuple[BLEDevice, AdvertisementData]]:
        """スキャン中の発見済みデバイスのスナップショットを返す
        (チューザーダイアログのポーリング用。呼び出し元スレッドを問わず
        安全 -- ロックで保護された辞書のコピーを返すだけ)。"""
        with self._scan_lock:
            return dict(self._scan_results)

    # --- 接続 ---

    async def _connect_async(
        self,
        client_id: str,
        address: str,
        on_disconnected: Optional[Callable[[str], None]] = None,
    ) -> None:
        def _disconnected_callback(_client: BleakClient) -> None:
            with self._clients_lock:
                self._clients.pop(client_id, None)
            if on_disconnected is not None:
                on_disconnected(client_id)

        try:
            client = BleakClient(address, disconnected_callback=_disconnected_callback)
            await client.connect()
        except Exception as exc:
            raise BleOperationError(
                f"failed to connect to {address}: {exc}", web_bluetooth_error_name="NetworkError"
            ) from exc
        with self._clients_lock:
            self._clients[client_id] = client

    def connect(
        self, client_id: str, address: str, on_disconnected: Optional[Callable[[str], None]] = None
    ) -> Future:
        return self.submit(self._connect_async(client_id, address, on_disconnected))

    async def _disconnect_async(self, client_id: str) -> None:
        with self._clients_lock:
            client = self._clients.pop(client_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    def disconnect(self, client_id: str) -> Future:
        return self.submit(self._disconnect_async(client_id))

    def is_connected(self, client_id: str) -> bool:
        with self._clients_lock:
            client = self._clients.get(client_id)
        return client is not None and client.is_connected

    def _get_client_or_raise(self, client_id: str) -> BleakClient:
        with self._clients_lock:
            client = self._clients.get(client_id)
        if client is None:
            raise BleOperationError(
                f"no active GATT connection for {client_id}", web_bluetooth_error_name="NetworkError"
            )
        return client

    # --- GATTツリーの取得(サービス/特性/記述子の列挙) ---

    async def _get_services_async(self, client_id: str) -> list:
        client = self._get_client_or_raise(client_id)
        if not client.is_connected:
            raise BleOperationError("GATT server is disconnected", web_bluetooth_error_name="NetworkError")
        collection = client.services
        result = []
        for service in collection:
            chars = []
            for char in service.characteristics:
                chars.append(
                    {
                        "uuid": char.uuid,
                        "handle": char.handle,
                        "properties": list(char.properties),
                        "descriptors": [
                            {"uuid": d.uuid, "handle": d.handle} for d in char.descriptors
                        ],
                    }
                )
            result.append({"uuid": service.uuid, "handle": service.handle, "characteristics": chars})
        return result

    def get_services(self, client_id: str) -> Future:
        return self.submit(self._get_services_async(client_id))

    # --- 特性/記述子の読み書き ---

    async def _read_characteristic_async(self, client_id: str, char_uuid: str) -> bytes:
        client = self._get_client_or_raise(client_id)
        try:
            return bytes(await client.read_gatt_char(char_uuid))
        except BleakError as exc:
            raise BleOperationError(str(exc), web_bluetooth_error_name="NetworkError") from exc

    def read_characteristic(self, client_id: str, char_uuid: str) -> Future:
        return self.submit(self._read_characteristic_async(client_id, char_uuid))

    async def _write_characteristic_async(
        self, client_id: str, char_uuid: str, data: bytes, with_response: bool
    ) -> None:
        client = self._get_client_or_raise(client_id)
        try:
            await client.write_gatt_char(char_uuid, data, response=with_response)
        except BleakError as exc:
            raise BleOperationError(str(exc), web_bluetooth_error_name="NetworkError") from exc

    def write_characteristic(
        self, client_id: str, char_uuid: str, data: bytes, with_response: bool
    ) -> Future:
        return self.submit(
            self._write_characteristic_async(client_id, char_uuid, data, with_response)
        )

    async def _read_descriptor_async(self, client_id: str, handle: int) -> bytes:
        client = self._get_client_or_raise(client_id)
        try:
            return bytes(await client.read_gatt_descriptor(handle))
        except BleakError as exc:
            raise BleOperationError(str(exc), web_bluetooth_error_name="NetworkError") from exc

    def read_descriptor(self, client_id: str, handle: int) -> Future:
        return self.submit(self._read_descriptor_async(client_id, handle))

    async def _write_descriptor_async(self, client_id: str, handle: int, data: bytes) -> None:
        client = self._get_client_or_raise(client_id)
        try:
            await client.write_gatt_descriptor(handle, data)
        except BleakError as exc:
            raise BleOperationError(str(exc), web_bluetooth_error_name="NetworkError") from exc

    def write_descriptor(self, client_id: str, handle: int, data: bytes) -> Future:
        return self.submit(self._write_descriptor_async(client_id, handle, data))

    # --- 通知(notify/indicate) ---

    async def _start_notify_async(
        self, client_id: str, char_uuid: str, callback: Callable[[str, bytes], None]
    ) -> None:
        client = self._get_client_or_raise(client_id)

        def _on_notify(sender, data: bytearray) -> None:
            char_uuid_str = sender.uuid if hasattr(sender, "uuid") else str(sender)
            callback(char_uuid_str, bytes(data))

        try:
            await client.start_notify(char_uuid, _on_notify)
        except BleakError as exc:
            raise BleOperationError(str(exc), web_bluetooth_error_name="NotSupportedError") from exc

    def start_notify(self, client_id: str, char_uuid: str, callback: Callable[[str, bytes], None]) -> Future:
        return self.submit(self._start_notify_async(client_id, char_uuid, callback))

    async def _stop_notify_async(self, client_id: str, char_uuid: str) -> None:
        client = self._get_client_or_raise(client_id)
        with contextlib.suppress(BleakError):
            await client.stop_notify(char_uuid)

    def stop_notify(self, client_id: str, char_uuid: str) -> Future:
        return self.submit(self._stop_notify_async(client_id, char_uuid))


def new_client_id() -> str:
    return uuid_module.uuid4().hex
