# -*- coding: utf-8 -*-
"""navigator.bluetooth のQWebChannelブリッジ本体。

姉妹プロジェクトpyside6-webusbのbridge.py(WebUSBBridge)と同じ役割を
Web Bluetoothに対して果たすが、アーキテクチャは大きく異なる。

## 同期呼び出しと非同期呼び出しの使い分け

pyside6-webusbは全操作を「呼ばれたら結果が出るまでブロックして`str`を返す」
同期スタイルで実装していた(pyusbのブロッキング呼び出しはミリ秒単位で
終わるため実用上問題にならない)。Bluetoothではスキャン・接続・GATT探索が
数百ms〜数秒かかるため、同じ方式だとアプリ全体のUIが固まってしまう。

そこで本モジュールでは操作を2種類に分ける:

  - **軽い操作**(getDevices/forgetDevice/isAvailable等): 権限ストア
    (QSettings)を読むだけで完結するため、pyside6-webusbと同じ同期
    @Slot(...)->strパターンのまま。
  - **BLE無線を伴う操作**(connectGatt/getPrimaryServices/read*/write*/
    startNotifications等): @Slotは検証(オリジン確認・権限確認・
    ブロックリスト確認)だけを同期的に行い、リクエストIDを即座に返す。
    実際のbleak呼び出しは`BleWorker`(ble_worker.py)の別スレッド上の
    asyncioループへ投入し、完了時に`bleOperationResult`というQt Signalで
    結果を配送する。ble_worker.py内で実機確認済みの通り、この
    Signal発火はbleakスレッドから行われてもQtが自動的にメインスレッドへ
    キューイングするため安全。

  requestDeviceChooser()だけは例外で、pyside6-webusbのチューザーと同じく
  `QDialog.exec()`によるネストしたイベントループでブロックする
  (モーダルダイアログである以上、そもそもブロックすることが意図された
  UXであるため)。ダイアログ内部のライブスキャンはBleWorker任せなので
  UIがフリーズすることはない。

## JSON応答の統一フォーマット

すべての@Slotはerrors.ok()/errors.fail()由来の
`{"ok": true, "result": ...}` または `{"ok": false, "error": {...}}`
の形の文字列を返す。BLE無線を伴う操作の場合、即座に返るのは
`{"ok": true, "result": {"requestId": "..."}}` であり、本当の結果は
後から`bleOperationResult`シグナルで同じrequestIdと共に届く。
"""
from __future__ import annotations

import base64
import json
import secrets
import urllib.parse
import uuid as uuid_module
from typing import Any, Optional

from PySide6.QtCore import QObject, QSettings, Signal, Slot
from PySide6.QtWidgets import QDialog

from . import errors, hardening
from ._version import __version__
from .ble_worker import BleOperationError, BleWorker
from .chooser_dialog import BluetoothDeviceChooserDialog
from .frame_origin import FrameOriginTracker
from .future_utils import future_then
from .gatt_registry import GATT_CHARACTERISTIC_NAMES, GATT_DESCRIPTOR_NAMES, GATT_SERVICE_NAMES, resolve_uuid


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


class BluetoothBridge(QObject):
    """1つのQWebEnginePageにつき1つ生成し、QWebChannelで
    'pyBluetoothBridge' 等の名前でJSに公開するオブジェクト。"""

    # BLE無線を伴う操作の非同期完了を通知する。ペイロードは
    # {"requestId": str, "ok": bool, "result"|"error": ...} のJSON文字列。
    bleOperationResult = Signal(str)

    # notify/indicateで特性値が変化したときに発火する。ペイロードは
    # {"deviceId", "serviceUuid", "characteristicUuid", "value": base64} のJSON。
    characteristicValueChanged = Signal(str)

    # デバイス側から予期せず切断されたときに発火する。ペイロードは
    # {"deviceId": str} のJSON。
    gattServerDisconnected = Signal(str)

    def __init__(self, page, parent: Optional[QObject] = None, *, backend: str = "bleak") -> None:
        super().__init__(parent)
        self._page = page
        self._frame_tracker = FrameOriginTracker(page)
        self._frame_tracker.start()
        self._worker = self._create_worker(backend)
        self._worker.start()
        self._settings = QSettings("pyside6-webbluetooth", "GrantedDevices")
        # device_id -> そのデバイスに対して現在実行中(未解決)のrequestIdの集合。
        # 仕様のGATTServer connect-checking wrapper
        # (https://webbluetoothcg.github.io/web-bluetooth/ 、
        # 「gattServer@[[activeAlgorithms]]」)に相当する: あるデバイスが
        # 切断された時点で、そのデバイスに対して実行中だった操作は
        # (たとえ裏側のbleak呼び出し自体が後から成功したとしても)
        # NetworkErrorとして確定させなければならない。実装時のコード
        # レビューで、この仕組みが無いことに気づいて追加した。
        self._pending_by_device: dict[str, set[str]] = {}

    @staticmethod
    def _create_worker(backend: str):
        """BLEバックエンドを選ぶ。

        - "bleak"(既定): 十分にテストされた、cross-platformに実績のある
          BleWorker(ble_worker.py)。
        - "qtbluetooth": PySide6.QtBluetooth(QLowEnergyController)を使う
          実験的なQtBluetoothWorker(qt_ble_worker.py)。追加の依存
          (bleak, dbus-fast等)を増やしたくない場合や、Qtへより密に
          統合したい場合の選択肢として用意した。モックによる配線検証は
          行っているが、実BLEハードウェアに対する検証はできていない
          (README.md/CHANGELOG.mdの既知の制限を参照)。
        """
        if backend == "bleak":
            return BleWorker()
        if backend == "qtbluetooth":
            from .qt_ble_worker import QtBluetoothWorker

            return QtBluetoothWorker()
        raise ValueError(f"unknown backend: {backend!r} (expected 'bleak' or 'qtbluetooth')")

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _verify_origin(self, frame_token: str) -> Optional[str]:
        return self._frame_tracker.resolve_origin(frame_token)

    def _origin_key(self, origin: str) -> str:
        # QSettingsは'/'をキーの階層区切りとして扱うため、オリジン文字列を
        # そのまま埋め込むと"https://a.example"の"//"が単一の"/"に
        # 畳み込まれてしまう(実機で確認済み)。実際に別々の有効なオリジンが
        # この畳み込みで衝突するケースは組み立てられないが、曖昧さを
        # 完全に無くすためパーセントエンコードしてから使う。
        return "granted_devices/" + urllib.parse.quote(origin, safe="")

    def _load_grants(self, origin: str) -> dict[str, dict[str, Any]]:
        raw = self._settings.value(self._origin_key(origin), "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}

    def _save_grants(self, origin: str, grants: dict[str, dict[str, Any]]) -> None:
        self._settings.setValue(self._origin_key(origin), json.dumps(grants))
        self._settings.sync()

    def _get_grant(self, origin: str, device_id: str) -> Optional[dict[str, Any]]:
        return self._load_grants(origin).get(device_id)

    def _require_service_allowed(self, grant: dict[str, Any], service_uuid: str) -> Optional[str]:
        """アクセス不可なら理由(エラーJSON文字列)を返す。問題無ければNone。"""
        if hardening.is_blocked_entirely(service_uuid):
            return json.dumps(errors.not_found_error(f"Service {service_uuid} is not accessible"))
        if service_uuid not in grant.get("allowedServices", []):
            return json.dumps(
                errors.security_error(
                    f"Origin is not allowed to access service {service_uuid}. "
                    "It must be listed in requestDevice()'s filters or optionalServices."
                )
            )
        return None

    def _dispatch_async(self, future, request_id: str, serialize=lambda x: x, device_id: Optional[str] = None) -> None:
        if device_id is not None:
            self._pending_by_device.setdefault(device_id, set()).add(request_id)

        def on_done(fut) -> None:
            # このデバイス切断時にすでにキャンセル済み(_fail_pending_for_deviceが
            # 代わりにNetworkErrorを配送済み)なら、裏側のbleak呼び出しが後から
            # 何を返してきても二重に配送しない。
            if device_id is not None:
                pending = self._pending_by_device.get(device_id)
                if pending is None or request_id not in pending:
                    return
                pending.discard(request_id)
            try:
                result = fut.result()
                payload = {"requestId": request_id, "ok": True, "result": serialize(result)}
            except BleOperationError as exc:
                payload = {
                    "requestId": request_id,
                    "ok": False,
                    "error": errors.make_error(exc.web_bluetooth_error_name, str(exc)),
                }
            except Exception as exc:  # noqa: BLE001 - bleak/OS由来の未分類例外を確実に拾う
                payload = {
                    "requestId": request_id,
                    "ok": False,
                    "error": errors.make_error("OperationError", str(exc)),
                }
            self.bleOperationResult.emit(json.dumps(payload))

        future.add_done_callback(on_done)

    def _fail_pending_for_device(self, device_id: str, message: str) -> None:
        """仕様の「GATTServer connect-checking wrapper」(gattServerが実行中に
        切断された場合、そのgattServerに紐づく進行中のアルゴリズムはすべて
        NetworkErrorとして確定させる)に相当する処理。切断がユーザーの
        明示的なdisconnect()によるものか、デバイス側からの予期しない
        切断かを問わず、切断時点で未解決のrequestIdはすべてここで
        確定させる。"""
        pending = self._pending_by_device.pop(device_id, None)
        if not pending:
            return
        for request_id in pending:
            payload = {
                "requestId": request_id,
                "ok": False,
                "error": errors.make_error("NetworkError", message),
            }
            self.bleOperationResult.emit(json.dumps(payload))

    @staticmethod
    def _new_request_id() -> str:
        return uuid_module.uuid4().hex

    def _on_device_disconnected(self, device_id: str) -> None:
        self._fail_pending_for_device(
            device_id, "GATT Server was disconnected while this operation was in progress."
        )
        self.gattServerDisconnected.emit(json.dumps({"deviceId": device_id}))

    def _on_characteristic_notify(
        self, device_id: str, service_uuid: str, char_uuid: str, _sender_uuid: str, data: bytes
    ) -> None:
        self.characteristicValueChanged.emit(
            json.dumps(
                {
                    "deviceId": device_id,
                    "serviceUuid": service_uuid,
                    "characteristicUuid": char_uuid,
                    "value": _b64encode(data),
                }
            )
        )

    def shutdown(self) -> None:
        """アプリ終了時に呼ぶ。すべてのGATT接続とスキャンを止める。"""
        self._frame_tracker.stop()
        self._worker.stop()

    # ------------------------------------------------------------------
    # 軽い操作(権限ストアの読み書きのみ。同期)
    # ------------------------------------------------------------------

    @Slot(result=str)
    def isAvailable(self) -> str:
        """F12コンソールでの疎通確認用。オリジン検証は不要
        (デバイス固有情報を一切含まないため)。"""
        return json.dumps(
            errors.ok(
                {
                    "package": "pyside6-webbluetooth",
                    "version": __version__,
                    "backend": "bleak",
                }
            )
        )

    @Slot(str, result=str)
    def getAvailability(self, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        request_id = self._new_request_id()
        future = self._worker.check_availability()
        self._dispatch_async(future, request_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, result=str)
    def getDevices(self, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grants = self._load_grants(origin)
        devices = [
            {"id": device_id, "name": grant.get("name")} for device_id, grant in grants.items()
        ]
        return json.dumps(errors.ok(devices))

    @Slot(str, str, result=str)
    def forgetDevice(self, device_id: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grants = self._load_grants(origin)
        if device_id in grants:
            del grants[device_id]
            self._save_grants(origin, grants)
        if self._worker.is_connected(device_id):
            self._worker.disconnect(device_id)
        return json.dumps(errors.ok(None))

    # ------------------------------------------------------------------
    # requestDevice() チューザー(モーダル。ネストしたイベントループでブロック)
    # ------------------------------------------------------------------

    @Slot(str, str, result=str)
    def requestDeviceChooser(self, options_json: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))

        try:
            raw_options = json.loads(options_json)
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("options must be valid JSON"))

        try:
            options = hardening.validate_request_options(raw_options)
        except hardening.BlocklistedUUIDError as exc:
            return json.dumps(errors.security_error(f"UUID is blocklisted: {exc.uuid}"))
        except hardening.InvalidFilterError as exc:
            return json.dumps(errors.type_error(str(exc)))

        dialog = BluetoothDeviceChooserDialog(
            origin=origin, options=options, worker=self._worker, parent=self._page.view() if hasattr(self._page, "view") else None
        )
        result = dialog.exec()
        address = dialog.selected_address
        if result != QDialog.Accepted or address is None:
            return json.dumps(errors.not_found_error("User cancelled the requestDevice() chooser."))

        scan_results = self._worker.snapshot_scan_results()
        device_entry = scan_results.get(address)
        local_name = None
        if device_entry is not None:
            device, adv = device_entry
            local_name = adv.local_name or device.name

        allowed_services = sorted(hardening.allowed_services_for_grant(options))

        grants = self._load_grants(origin)
        # 同じアドレスに対する既存の許可があれば device_id を使い回し、
        # allowedServicesは和集合で拡張する(再度requestDevice()した際に
        # 以前より狭い権限に後退しないようにする)。
        existing_id = None
        for did, g in grants.items():
            if g.get("address") == address:
                existing_id = did
                break
        device_id = existing_id or secrets.token_urlsafe(16)
        merged_services = sorted(set(grants.get(device_id, {}).get("allowedServices", [])) | set(allowed_services))
        grants[device_id] = {"address": address, "name": local_name, "allowedServices": merged_services}
        self._save_grants(origin, grants)

        return json.dumps(errors.ok({"id": device_id, "name": local_name}))

    # ------------------------------------------------------------------
    # GATT接続
    # ------------------------------------------------------------------

    @Slot(str, str, result=str)
    def connectGatt(self, device_id: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))

        if self._worker.is_connected(device_id):
            # すでに接続済みのGATTサーバーへ再度connect()した場合、仕様上は
            # 同じサーバーで即座に成功すればよい。ここを素通りしてbleak
            # (BleakClient)へ新しい接続を張り直すと、古いBleakClient
            # インスタンスをdisconnect()せずに上書きしてしまい、接続が
            # リークする(コードレビューで発見。JS側で`connect()`を
            # 防御的に複数回呼ぶコードは珍しくないため、実害があり得る)。
            request_id = self._new_request_id()
            self.bleOperationResult.emit(
                json.dumps({"requestId": request_id, "ok": True, "result": {"deviceId": device_id}})
            )
            return json.dumps(errors.ok({"requestId": request_id}))

        request_id = self._new_request_id()
        future = self._worker.connect(
            device_id, grant["address"], on_disconnected=self._on_device_disconnected
        )
        self._dispatch_async(future, request_id, serialize=lambda _: {"deviceId": device_id}, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, result=str)
    def disconnectGatt(self, device_id: str, frame_token: str) -> str:
        # 仕様上 BluetoothRemoteGATTServer.disconnect() はPromiseを返さない
        # (JS側で待つ必要が無い)ため、これは同期Slotのままでよい。実際の
        # 切断処理はBleWorker任せで完了を待たない(fire-and-forget)。
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        # 切断時点で進行中だった操作は、後から裏側の処理がどう転んでも
        # NetworkErrorとして確定させる(仕様のGATTServer
        # connect-checking wrapperに相当。上のコメント・
        # _fail_pending_for_deviceの説明を参照)。
        self._fail_pending_for_device(device_id, "GATT Server disconnected by disconnect().")
        self._worker.disconnect(device_id)
        return json.dumps(errors.ok(None))

    @Slot(str, str, result=str)
    def isGattConnected(self, device_id: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        return json.dumps(errors.ok(self._worker.is_connected(device_id)))

    # ------------------------------------------------------------------
    # GATTツリーの探索 (getPrimaryService(s) / getCharacteristic(s) / getDescriptor(s))
    # ------------------------------------------------------------------
    #
    # サービスはUUID単位で許可判定する(仕様上、許可情報自体がUUID単位で
    # 保持されるため)。特性・記述子はUUIDが同一デバイス内で重複しうる
    # (例: 複数のnotify対応特性それぞれに同じUUID 0x2902のCCCDが存在する)
    # ため、一度取得したhandleで一意に指し示す。JSに渡す各要素には
    # 内部用の"handle"フィールドを含めておき、polyfill.js側はそれを
    # 後続のread/write呼び出しにそのまま渡す(スペック上JSに見える
    # プロパティではなく、あくまで内部実装用の紐付けキー)。

    @Slot(str, str, str, result=str)
    def getPrimaryServices(self, device_id: str, service_uuid_json: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot retrieve services."))

        try:
            requested = json.loads(service_uuid_json)
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid service uuid"))

        target_uuid = None
        if requested is not None:
            try:
                target_uuid = resolve_uuid(requested, GATT_SERVICE_NAMES)
            except ValueError as exc:
                return json.dumps(errors.type_error(str(exc)))
            denial = self._require_service_allowed(grant, target_uuid)
            if denial is not None:
                return denial

        allowed = set(grant.get("allowedServices", []))

        def serialize(raw_services):
            out = []
            for svc in raw_services:
                uuid = svc["uuid"]
                if hardening.is_blocked_entirely(uuid) or uuid not in allowed:
                    continue
                if target_uuid is not None and uuid != target_uuid:
                    continue
                out.append({"uuid": uuid, "isPrimary": True})
            if target_uuid is not None and not out:
                raise BleOperationError(
                    f"No Services matching UUID {target_uuid} found in Device.",
                    web_bluetooth_error_name="NotFoundError",
                )
            return out

        request_id = self._new_request_id()
        future = self._worker.get_services(device_id)
        self._dispatch_async(future, request_id, serialize=serialize, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, result=str)
    def getCharacteristics(
        self, device_id: str, service_uuid: str, characteristic_uuid_json: str, frame_token: str
    ) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot retrieve characteristics."))

        try:
            requested = json.loads(characteristic_uuid_json)
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid characteristic uuid"))

        target_char_uuid = None
        if requested is not None:
            try:
                target_char_uuid = resolve_uuid(requested, GATT_CHARACTERISTIC_NAMES)
            except ValueError as exc:
                return json.dumps(errors.type_error(str(exc)))

        def serialize(raw_services):
            svc = next((s for s in raw_services if s["uuid"] == service_uuid), None)
            if svc is None:
                raise BleOperationError(
                    f"Service {service_uuid} no longer exists on this device.",
                    web_bluetooth_error_name="InvalidStateError",
                )
            out = []
            for ch in svc["characteristics"]:
                uuid = ch["uuid"]
                if hardening.is_blocked_entirely(uuid):
                    continue
                if target_char_uuid is not None and uuid != target_char_uuid:
                    continue
                out.append(
                    {
                        "uuid": uuid,
                        "handle": ch["handle"],
                        "properties": hardening.characteristic_properties_dict(ch["properties"]),
                    }
                )
            if target_char_uuid is not None and not out:
                raise BleOperationError(
                    f"No Characteristic matching UUID {target_char_uuid} found in Service.",
                    web_bluetooth_error_name="NotFoundError",
                )
            return out

        request_id = self._new_request_id()
        future = self._worker.get_services(device_id)
        self._dispatch_async(future, request_id, serialize=serialize, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, str, result=str)
    def getDescriptors(
        self,
        device_id: str,
        service_uuid: str,
        characteristic_handle_json: str,
        descriptor_uuid_json: str,
        frame_token: str,
    ) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot retrieve descriptors."))

        try:
            characteristic_handle = int(json.loads(characteristic_handle_json))
            requested = json.loads(descriptor_uuid_json)
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid characteristic handle or descriptor uuid"))

        target_desc_uuid = None
        if requested is not None:
            try:
                target_desc_uuid = resolve_uuid(requested, GATT_DESCRIPTOR_NAMES)
            except ValueError as exc:
                return json.dumps(errors.type_error(str(exc)))

        def serialize(raw_services):
            svc = next((s for s in raw_services if s["uuid"] == service_uuid), None)
            if svc is None:
                raise BleOperationError(
                    f"Service {service_uuid} no longer exists on this device.",
                    web_bluetooth_error_name="InvalidStateError",
                )
            ch = next((c for c in svc["characteristics"] if c["handle"] == characteristic_handle), None)
            if ch is None:
                raise BleOperationError(
                    "Characteristic no longer exists on this device.",
                    web_bluetooth_error_name="InvalidStateError",
                )
            out = []
            for d in ch["descriptors"]:
                uuid = d["uuid"]
                if hardening.is_blocked_entirely(uuid):
                    continue
                if target_desc_uuid is not None and uuid != target_desc_uuid:
                    continue
                out.append({"uuid": uuid, "handle": d["handle"]})
            if target_desc_uuid is not None and not out:
                raise BleOperationError(
                    f"No Descriptor matching UUID {target_desc_uuid} found in Characteristic.",
                    web_bluetooth_error_name="NotFoundError",
                )
            return out

        request_id = self._new_request_id()
        future = self._worker.get_services(device_id)
        self._dispatch_async(future, request_id, serialize=serialize, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    # ------------------------------------------------------------------
    # 特性・記述子の読み書き / 通知
    # ------------------------------------------------------------------
    #
    # サービス単位の許可(allowedServices)はここで同期的に検証してから
    # 非同期処理を投入する。特性/記述子がJSの申告通り本当にそのサービスの
    # 配下にあるかどうかは、実際に操作を行うコルーチンの中でGATTツリーを
    # 再取得して構造的に検証する(handleがJS側で偽装されても、存在しない
    # 組み合わせならInvalidStateErrorになるだけで、他サービスの特性へは
    # アクセスできない)。
    #
    # `self._worker._get_services_async`等のアンダースコア付きメソッドを
    # 直接awaitしているのは、bridge.py側のコルーチンがすでに
    # BleWorkerと同じバックグラウンドasyncioループ上で実行されている
    # (=`self._worker.submit()`によってそこへ投入された)ためで、
    # 二重にスレッドをまたぐ必要が無いことを利用している。ble_worker.pyと
    # bridge.pyは同一パッケージ内で密結合している前提の設計。

    def _resolve_characteristic_or_raise(self, services: list, service_uuid: str, handle: int) -> dict:
        svc = next((s for s in services if s["uuid"] == service_uuid), None)
        if svc is None:
            raise BleOperationError(
                f"Service {service_uuid} no longer exists on this device.",
                web_bluetooth_error_name="InvalidStateError",
            )
        ch = next((c for c in svc["characteristics"] if c["handle"] == handle), None)
        if ch is None:
            raise BleOperationError(
                "Characteristic no longer exists on this device.", web_bluetooth_error_name="InvalidStateError"
            )
        return ch

    def _resolve_descriptor_or_raise(
        self, services: list, service_uuid: str, characteristic_handle: int, handle: int
    ) -> dict:
        ch = self._resolve_characteristic_or_raise(services, service_uuid, characteristic_handle)
        d = next((dd for dd in ch["descriptors"] if dd["handle"] == handle), None)
        if d is None:
            raise BleOperationError(
                "Descriptor no longer exists on this device.", web_bluetooth_error_name="InvalidStateError"
            )
        return d

    def _read_characteristic_flow(self, device_id: str, service_uuid: str, handle: int):
        def after_services(services):
            ch = self._resolve_characteristic_or_raise(services, service_uuid, handle)
            if hardening.is_blocked_for_read(ch["uuid"]):
                raise BleOperationError(
                    f"Reading characteristic {ch['uuid']} is not allowed.", web_bluetooth_error_name="SecurityError"
                )
            if "read" not in ch["properties"]:
                raise BleOperationError(
                    "Characteristic does not support reads.", web_bluetooth_error_name="NotSupportedError"
                )
            return future_then(self._worker.read_characteristic(device_id, handle), lambda data: (ch["uuid"], data))

        return future_then(self._worker.get_services(device_id), after_services)

    def _write_characteristic_flow(
        self, device_id: str, service_uuid: str, handle: int, data: bytes, with_response: bool
    ):
        def after_services(services):
            ch = self._resolve_characteristic_or_raise(services, service_uuid, handle)
            if hardening.is_blocked_for_write(ch["uuid"]):
                raise BleOperationError(
                    f"Writing characteristic {ch['uuid']} is not allowed.", web_bluetooth_error_name="SecurityError"
                )
            required_prop = "write" if with_response else "write-without-response"
            if required_prop not in ch["properties"]:
                raise BleOperationError(
                    f"Characteristic does not support {required_prop}.", web_bluetooth_error_name="NotSupportedError"
                )
            return future_then(
                self._worker.write_characteristic(device_id, handle, data, with_response), lambda _: ch["uuid"]
            )

        return future_then(self._worker.get_services(device_id), after_services)

    def _read_descriptor_flow(self, device_id: str, service_uuid: str, characteristic_handle: int, handle: int):
        def after_services(services):
            d = self._resolve_descriptor_or_raise(services, service_uuid, characteristic_handle, handle)
            if hardening.is_blocked_for_read(d["uuid"]):
                raise BleOperationError(
                    f"Reading descriptor {d['uuid']} is not allowed.", web_bluetooth_error_name="SecurityError"
                )
            return future_then(self._worker.read_descriptor(device_id, handle), lambda data: (d["uuid"], data))

        return future_then(self._worker.get_services(device_id), after_services)

    def _write_descriptor_flow(
        self, device_id: str, service_uuid: str, characteristic_handle: int, handle: int, data: bytes
    ):
        def after_services(services):
            d = self._resolve_descriptor_or_raise(services, service_uuid, characteristic_handle, handle)
            if hardening.is_blocked_for_write(d["uuid"]):
                raise BleOperationError(
                    f"Writing descriptor {d['uuid']} is not allowed.", web_bluetooth_error_name="SecurityError"
                )
            return future_then(self._worker.write_descriptor(device_id, handle, data), lambda _: d["uuid"])

        return future_then(self._worker.get_services(device_id), after_services)

    def _start_notify_flow(self, device_id: str, service_uuid: str, handle: int):
        def after_services(services):
            ch = self._resolve_characteristic_or_raise(services, service_uuid, handle)
            if "notify" not in ch["properties"] and "indicate" not in ch["properties"]:
                raise BleOperationError(
                    "Characteristic does not support notifications.", web_bluetooth_error_name="NotSupportedError"
                )
            char_uuid = ch["uuid"]

            def on_value(_sender_uuid: str, data: bytes) -> None:
                self._on_characteristic_notify(device_id, service_uuid, char_uuid, _sender_uuid, data)

            return future_then(self._worker.start_notify(device_id, handle, on_value), lambda _: char_uuid)

        return future_then(self._worker.get_services(device_id), after_services)

    @Slot(str, str, str, str, result=str)
    def readCharacteristicValue(self, device_id: str, service_uuid: str, handle_json: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot read characteristic."))
        try:
            handle = int(json.loads(handle_json))
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid characteristic handle"))

        request_id = self._new_request_id()
        future = self._read_characteristic_flow(device_id, service_uuid, handle)
        self._dispatch_async(future, request_id, serialize=lambda pair: {"value": _b64encode(pair[1])}, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, bool, str, result=str)
    def writeCharacteristicValue(
        self,
        device_id: str,
        service_uuid: str,
        handle_json: str,
        data_b64: str,
        with_response: bool,
        frame_token: str,
    ) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot write characteristic."))
        try:
            handle = int(json.loads(handle_json))
            data = _b64decode(data_b64)
        except (TypeError, ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            return json.dumps(errors.type_error("invalid characteristic handle or data"))

        request_id = self._new_request_id()
        future = self._write_characteristic_flow(device_id, service_uuid, handle, data, with_response)
        self._dispatch_async(future, request_id, serialize=lambda _uuid: None, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, str, result=str)
    def readDescriptorValue(
        self, device_id: str, service_uuid: str, characteristic_handle_json: str, handle_json: str, frame_token: str
    ) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot read descriptor."))
        try:
            characteristic_handle = int(json.loads(characteristic_handle_json))
            handle = int(json.loads(handle_json))
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid handle"))

        request_id = self._new_request_id()
        future = self._read_descriptor_flow(device_id, service_uuid, characteristic_handle, handle)
        self._dispatch_async(future, request_id, serialize=lambda pair: {"value": _b64encode(pair[1])}, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, str, str, result=str)
    def writeDescriptorValue(
        self,
        device_id: str,
        service_uuid: str,
        characteristic_handle_json: str,
        handle_json: str,
        data_b64: str,
        frame_token: str,
    ) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot write descriptor."))
        try:
            characteristic_handle = int(json.loads(characteristic_handle_json))
            handle = int(json.loads(handle_json))
            data = _b64decode(data_b64)
        except (TypeError, ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            return json.dumps(errors.type_error("invalid handle or data"))

        request_id = self._new_request_id()
        future = self._write_descriptor_flow(device_id, service_uuid, characteristic_handle, handle, data)
        self._dispatch_async(future, request_id, serialize=lambda _uuid: None, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, result=str)
    def startNotifications(self, device_id: str, service_uuid: str, handle_json: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        denial = self._require_service_allowed(grant, service_uuid)
        if denial is not None:
            return denial
        if not self._worker.is_connected(device_id):
            return json.dumps(errors.network_error("GATT Server is disconnected. Cannot start notifications."))
        try:
            handle = int(json.loads(handle_json))
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid characteristic handle"))

        request_id = self._new_request_id()
        future = self._start_notify_flow(device_id, service_uuid, handle)
        self._dispatch_async(future, request_id, serialize=lambda _uuid: None, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))

    @Slot(str, str, str, str, result=str)
    def stopNotifications(self, device_id: str, service_uuid: str, handle_json: str, frame_token: str) -> str:
        origin = self._verify_origin(frame_token)
        if origin is None:
            return json.dumps(errors.security_error("unable to verify calling frame's origin"))
        grant = self._get_grant(origin, device_id)
        if grant is None:
            return json.dumps(errors.security_error(f"No known device with id {device_id} for this origin"))
        try:
            handle = int(json.loads(handle_json))
        except (TypeError, ValueError):
            return json.dumps(errors.type_error("invalid characteristic handle"))

        request_id = self._new_request_id()
        future = self._worker.stop_notify(device_id, handle)
        self._dispatch_async(future, request_id, serialize=lambda _: None, device_id=device_id)
        return json.dumps(errors.ok({"requestId": request_id}))
