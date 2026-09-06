# -*- coding: utf-8 -*-
"""requestDevice()のオプション検証・フィルタ照合、GATTブロックリストの適用。

姉妹プロジェクト pyside6-webusb の hardening.py (保護対象インターフェース
クラス・セキュリティキーのブロックリスト・フィルタ照合アルゴリズム)に相当する、
Web Bluetooth版のセキュリティ境界。USBとBluetoothではモデルが大きく異なる
("インターフェースクラス"という概念がBluetoothには無く、代わりにGATT
サービス/特性/記述子のUUID単位でブロックリストが定義される)ため、
中身は一から仕様に基づいて構築している(Mock-APIsのコンセプト通り、
単純な移植ではなく対象環境に合わせた実装)。

## GATT ブロックリスト

https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt
(取得日時点のmasterブランチ)をそのまま転記した値。このファイルを直接
生成スクリプトに通したわけではない(3セクション・コメント付きの構造が
gatt_assigned_*.txtほど機械的ではないため)が、値・コメントとも原本の
記述を保っている。

各エントリは以下のいずれか:
  - "exclude-all"    : そのUUIDの存在自体を隠す(getPrimaryService/
                        getCharacteristic/getDescriptorがNotFoundError、
                        一覧系はそのUUIDを含めない)。requestDevice()の
                        filters.services / optionalServicesに指定しようと
                        した場合もSecurityErrorで即座に拒否する。
  - "exclude-reads"   : 存在は見せるが、readValue()はSecurityError。
  - "exclude-writes"  : 存在は見せるが、writeValue*()はSecurityError。
                        なお startNotifications()/stopNotifications() は
                        CCCD(0x2902)への直接writeValueを経由しない別経路
                        (bleakのstart_notify/stop_notify、実ブラウザでは
                        プラットフォームのネイティブGATTクライアントAPI)で
                        実装するため、このブロックの影響を受けない
                        -- 実際のChromeの挙動と同じ。
"""
from __future__ import annotations

from typing import Any, Optional

from .gatt_registry import GATT_SERVICE_NAMES, canonical_uuid, resolve_uuid

BlocklistMode = str  # "exclude-all" | "exclude-reads" | "exclude-writes"

GATT_BLOCKLIST: dict[str, BlocklistMode] = {
    # --- Services ---
    # org.bluetooth.service.human_interface_device: HIDへの直接アクセスを
    # 許すとWebページがキーロガー化できてしまう。
    "00001812-0000-1000-8000-00805f9b34fb": "exclude-all",
    # NordicのレガシーDevice Firmware Update service。署名検証なしの
    # ファームウェア書き換えを許すと、デバイスの永続的な乗っ取りに繋がる。
    "00001530-1212-efde-1523-785feabcd123": "exclude-all",
    # TIのOver-the-Air Download service(同上の理由)。
    "f000ffc0-0451-4000-b000-000000000000": "exclude-all",
    # Cypressのブートローダサービス(同上の理由)。
    "00060000-0000-1000-8000-00805f9b34fb": "exclude-all",
    # FIDO Bluetooth Specification: Webページが生のGATTコマンドで別サイトの
    # フリをしてFIDOデバイスに成りすませてしまう。
    "0000fffd-0000-1000-8000-00805f9b34fb": "exclude-all",
    "0000fff9-0000-1000-8000-00805f9b34fb": "exclude-all",  # 同じくFIDO用
    "0000fde2-0000-1000-8000-00805f9b34fb": "exclude-all",  # Google製ペアリング不要FIDO
    # --- Characteristics ---
    # gap.peripheral_privacy_flag: Webページにプライバシーモードを
    # 勝手に解除させない。
    "00002a02-0000-1000-8000-00805f9b34fb": "exclude-writes",
    # gap.reconnection_address: 接続パラメータの改ざんを許さない。
    "00002a03-0000-1000-8000-00805f9b34fb": "exclude-all",
    # serial_number_string: 標準化された一意識別子はプライバシー上の理由で
    # 遮断する。
    "00002a25-0000-1000-8000-00805f9b34fb": "exclude-all",
    # --- Descriptors ---
    # gatt.client_characteristic_configuration (CCCD): 直接書き込みを許すと
    # 他ページの通知/インジケーションを妨害できてしまう
    # (startNotifications/stopNotificationsは別経路を使うため影響を受けない)。
    "00002902-0000-1000-8000-00805f9b34fb": "exclude-writes",
    # gatt.server_characteristic_configuration (SCCD): 同上、ブロードキャスト
    # サービスへの妨害を防ぐ。
    "00002903-0000-1000-8000-00805f9b34fb": "exclude-writes",
}


class BlocklistedUUIDError(ValueError):
    """requestDevice()のfilters/optionalServicesにブロックリスト対象の
    UUIDが指定された場合に送出する(呼び出し側でSecurityErrorとして
    JSに投げ返すことを想定)。"""

    def __init__(self, uuid: str):
        super().__init__(f"UUID is blocklisted: {uuid}")
        self.uuid = uuid


class InvalidFilterError(ValueError):
    """requestDevice()のoptions/filterの形式が不正な場合に送出する
    (呼び出し側でTypeErrorとしてJSに投げ返すことを想定)。"""


def is_blocked_entirely(uuid: str) -> bool:
    return GATT_BLOCKLIST.get(uuid.lower()) == "exclude-all"


def is_blocked_for_read(uuid: str) -> bool:
    return GATT_BLOCKLIST.get(uuid.lower()) in ("exclude-all", "exclude-reads")


def is_blocked_for_write(uuid: str) -> bool:
    return GATT_BLOCKLIST.get(uuid.lower()) in ("exclude-all", "exclude-writes")


def _resolve_service_uuid_or_raise(value: Any) -> str:
    try:
        uuid = resolve_uuid(value, GATT_SERVICE_NAMES)
    except ValueError as exc:
        raise InvalidFilterError(str(exc)) from exc
    if is_blocked_entirely(uuid):
        raise BlocklistedUUIDError(uuid)
    return uuid


def _as_bytes(value: Any, field: str) -> bytes:
    """dataPrefix/mask等、JSON経由で届く「バイト列」表現を受け取る。
    polyfill.js側はUint8ArrayをJSON化できないため、16進文字列
    (例: "a1b2c3")または整数配列として送ってくる想定。"""
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("0x") or s.startswith("0X"):
            s = s[2:]
        try:
            return bytes.fromhex(s)
        except ValueError as exc:
            raise InvalidFilterError(f"invalid hex string for {field}: {value!r}") from exc
    if isinstance(value, (list, tuple)):
        try:
            return bytes(int(b) & 0xFF for b in value)
        except (TypeError, ValueError) as exc:
            raise InvalidFilterError(f"invalid byte array for {field}: {value!r}") from exc
    raise InvalidFilterError(f"invalid byte representation for {field}: {value!r}")


def canonicalize_filter(filter_obj: dict) -> dict:
    """requestDevice()のfilters配列の1要素を検証・正規化する。

    仕様(https://webbluetoothcg.github.io/web-bluetooth/#dom-bluetoothlescanfilterinit)
    の「a filter must restrict the devices in some way」に基づき、
    services / name / namePrefix / manufacturerData / serviceData の
    いずれも指定されていないフィルタは不正として拒否する。"""
    if not isinstance(filter_obj, dict):
        raise InvalidFilterError("each filter must be an object")

    services = filter_obj.get("services")
    name = filter_obj.get("name")
    name_prefix = filter_obj.get("namePrefix")
    manufacturer_data = filter_obj.get("manufacturerData")
    service_data = filter_obj.get("serviceData")

    if not any([services, name is not None, name_prefix, manufacturer_data, service_data]):
        raise InvalidFilterError("a filter must restrict the devices in some way")

    result: dict[str, Any] = {}

    if services:
        if not isinstance(services, (list, tuple)) or len(services) == 0:
            raise InvalidFilterError("filter.services must be a non-empty array")
        result["services"] = [_resolve_service_uuid_or_raise(s) for s in services]

    if name is not None:
        if not isinstance(name, str):
            raise InvalidFilterError("filter.name must be a string")
        result["name"] = name

    if name_prefix:
        if not isinstance(name_prefix, str):
            raise InvalidFilterError("filter.namePrefix must be a string")
        result["namePrefix"] = name_prefix

    if manufacturer_data:
        entries = []
        for entry in manufacturer_data:
            if "companyIdentifier" not in entry:
                raise InvalidFilterError("manufacturerData entry requires companyIdentifier")
            company_id = int(entry["companyIdentifier"])
            parsed: dict[str, Any] = {"companyIdentifier": company_id}
            if "dataPrefix" in entry and entry["dataPrefix"] is not None:
                prefix = _as_bytes(entry["dataPrefix"], "dataPrefix")
                mask = (
                    _as_bytes(entry["mask"], "mask")
                    if entry.get("mask") is not None
                    else bytes([0xFF]) * len(prefix)
                )
                if len(mask) != len(prefix):
                    raise InvalidFilterError("mask must be the same length as dataPrefix")
                parsed["dataPrefix"] = prefix
                parsed["mask"] = mask
            entries.append(parsed)
        result["manufacturerData"] = entries

    if service_data:
        entries = []
        for entry in service_data:
            if "service" not in entry:
                raise InvalidFilterError("serviceData entry requires 'service'")
            svc_uuid = _resolve_service_uuid_or_raise(entry["service"])
            parsed = {"service": svc_uuid}
            if "dataPrefix" in entry and entry["dataPrefix"] is not None:
                prefix = _as_bytes(entry["dataPrefix"], "dataPrefix")
                mask = (
                    _as_bytes(entry["mask"], "mask")
                    if entry.get("mask") is not None
                    else bytes([0xFF]) * len(prefix)
                )
                if len(mask) != len(prefix):
                    raise InvalidFilterError("mask must be the same length as dataPrefix")
                parsed["dataPrefix"] = prefix
                parsed["mask"] = mask
            entries.append(parsed)
        result["serviceData"] = entries

    return result


def validate_request_options(options: dict) -> dict:
    """navigator.bluetooth.requestDevice()のoptionsを検証・正規化する。

    戻り値のfilters内のUUID・manufacturerData/serviceDataのバイト列は
    すべて正規化済み(小文字128-bit UUID、bytes型)になる。"""
    if not isinstance(options, dict):
        raise InvalidFilterError("options must be an object")

    accept_all = bool(options.get("acceptAllDevices", False))
    filters = options.get("filters")

    if accept_all and filters:
        raise InvalidFilterError("acceptAllDevices and filters are mutually exclusive")
    if not accept_all and not filters:
        raise InvalidFilterError("either filters or acceptAllDevices must be specified")

    canon_filters = [canonicalize_filter(f) for f in (filters or [])]

    optional_services = []
    for s in options.get("optionalServices") or []:
        optional_services.append(_resolve_service_uuid_or_raise(s))

    optional_manufacturer_data = [int(x) for x in options.get("optionalManufacturerData") or []]

    return {
        "acceptAllDevices": accept_all,
        "filters": canon_filters,
        "optionalServices": optional_services,
        "optionalManufacturerData": optional_manufacturer_data,
    }


def allowed_services_for_grant(options: dict) -> set:
    """デバイス選択が成功した際に、そのオリジンがgetPrimaryService()等で
    アクセスしてよいサービスUUIDの集合を計算する
    (= すべてのfilterのservicesの和集合 + optionalServices)。

    どのfilterが実際にマッチしたかに関わらず全filterのservicesを許可する
    のは、実際のChromium実装(WebBluetoothServiceImpl)に合わせた仕様解釈。"""
    allowed = set()
    for f in options.get("filters", []):
        allowed.update(f.get("services", []))
    allowed.update(options.get("optionalServices", []))
    return allowed


def _bytes_match(data: bytes, prefix: bytes, mask: bytes) -> bool:
    if len(data) < len(prefix):
        return False
    for d, p, m in zip(data, prefix, mask):
        if (d & m) != (p & m):
            return False
    return True


def device_matches_filter(
    *,
    local_name: Optional[str],
    service_uuids: list,
    manufacturer_data: dict,
    service_data: dict,
    filt: dict,
) -> bool:
    """1つの正規化済みfilterに、スキャンで得た1件の広告データが一致するかを
    判定する(仕様の「matches a filter」アルゴリズムに相当)。"""
    if "services" in filt:
        device_services = {canonical_uuid(u) for u in service_uuids}
        if not set(filt["services"]).issubset(device_services):
            return False

    if "name" in filt:
        if (local_name or "") != filt["name"]:
            return False

    if "namePrefix" in filt:
        if not (local_name or "").startswith(filt["namePrefix"]):
            return False

    if "manufacturerData" in filt:
        for entry in filt["manufacturerData"]:
            data = manufacturer_data.get(entry["companyIdentifier"])
            if data is None:
                return False
            if "dataPrefix" in entry:
                if not _bytes_match(bytes(data), entry["dataPrefix"], entry["mask"]):
                    return False

    if "serviceData" in filt:
        norm_service_data = {canonical_uuid(k): v for k, v in service_data.items()}
        for entry in filt["serviceData"]:
            data = norm_service_data.get(entry["service"])
            if data is None:
                return False
            if "dataPrefix" in entry:
                if not _bytes_match(bytes(data), entry["dataPrefix"], entry["mask"]):
                    return False

    return True


def device_matches_options(
    *,
    local_name: Optional[str],
    service_uuids: list,
    manufacturer_data: dict,
    service_data: dict,
    options: dict,
) -> bool:
    """正規化済みoptions全体(acceptAllDevices または filters)に、
    スキャンで得た1件の広告データが一致するかを判定する。"""
    if options.get("acceptAllDevices"):
        return True
    for filt in options.get("filters", []):
        if device_matches_filter(
            local_name=local_name,
            service_uuids=service_uuids,
            manufacturer_data=manufacturer_data,
            service_data=service_data,
            filt=filt,
        ):
            return True
    return False


# --- GATT特性プロパティのマッピング (bleak文字列 -> Web Bluetooth camelCase) ---
# bleak(bleak/assigned_numbers.py CharacteristicPropertyName)がGATTの
# Characteristic Propertiesビットフィールドから生成する文字列を、
# BluetoothCharacteristicProperties辞書のキー名へ変換する。
# 実際にimportして検証した値(bleak 3.0.2時点)に基づく。
_PROPERTY_NAME_MAP: dict[str, str] = {
    "broadcast": "broadcast",
    "read": "read",
    "write-without-response": "writeWithoutResponse",
    "write": "write",
    "notify": "notify",
    "indicate": "indicate",
    "authenticated-signed-writes": "authenticatedSignedWrites",
    "reliable-write": "reliableWrite",
    "writable-auxiliaries": "writableAuxiliaries",
    # "extended-properties" はCharacteristic Extended Properties記述子
    # (0x2900)の存在を示すビットであり、Web Bluetoothの
    # BluetoothCharacteristicProperties自体には対応するフィールドが無い
    # (reliable-write/writable-auxiliariesとして間接的に表現される)ため
    # ここでは読み捨てる。
    # "encrypt-read"/"encrypt-write"等はBlueZ拡張でコアGATT仕様外のため
    # 同様に読み捨てる。
}

_ALL_WEB_BLUETOOTH_PROPERTY_KEYS = (
    "broadcast",
    "read",
    "writeWithoutResponse",
    "write",
    "notify",
    "indicate",
    "authenticatedSignedWrites",
    "reliableWrite",
    "writableAuxiliaries",
)


def characteristic_properties_dict(bleak_properties: list) -> dict:
    """bleakのcharacteristic.propertiesのリストから、Web Bluetoothの
    BluetoothCharacteristicProperties相当のdict(全キーがbool)を作る。"""
    result = {key: False for key in _ALL_WEB_BLUETOOTH_PROPERTY_KEYS}
    for prop in bleak_properties:
        mapped = _PROPERTY_NAME_MAP.get(prop)
        if mapped:
            result[mapped] = True
    return result
