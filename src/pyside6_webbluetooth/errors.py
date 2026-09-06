# -*- coding: utf-8 -*-
"""DOMException名の一元管理。

bridge.py内で例外を文字列化するたびに `{"error": "SecurityError", "message": ...}`
のようなdictを手書きすると、名前の綴り間違い(例: "SecurtyError")が起きても
気づきにくい。ここで定義したビルダー関数だけを経由させることで、
polyfill.js側の `throwFromResult()` が必ず正しい名前のDOMExceptionを
投げられるようにする。

Web Bluetooth仕様(https://webbluetoothcg.github.io/web-bluetooth/)の
各アルゴリズムが実際に投げるエラー名に合わせている:
  - SecurityError    : ブロックリスト対象UUIDへのアクセス、オリジンが
                        filters/optionalServicesで宣言していないUUIDへの
                        アクセス、非セキュアコンテキスト、Permissions Policy
                        で無効化されている場合など。
  - NetworkError      : GATTサーバーへの接続確立に失敗した場合、または
                        接続済みのGATTサーバーが操作の途中で切断された場合。
  - NotFoundError     : requestDevice()に一致するデバイスが無かった/
                        ユーザーがチューザーをキャンセルした場合、
                        getPrimaryService()等が対象を発見できなかった場合。
  - InvalidStateError : GATTサーバーが未接続の状態で特性/記述子の操作を
                        行おうとした場合。
  - NotSupportedError : 対象のGATT特性が要求された操作(read/write/notify)
                        に対応するプロパティを持たない場合。
  - AbortError         : watchAdvertisements()等がAbortSignalで中断された場合。
  - TypeError          : requestDevice()に渡されたoptionsの形式が不正な場合
                        (DOMExceptionではなくJS組み込みのTypeError)。

DOMExceptionのレガシーcode値はWebIDL仕様の「error names table」に基づく
(現代のJSではほぼ使われないが、互換性のため付与する)。名前に対応する
codeが無いものは0。
"""
from __future__ import annotations

from typing import Any, Optional

_LEGACY_CODES: dict[str, int] = {
    "IndexSizeError": 1,
    "HierarchyRequestError": 3,
    "WrongDocumentError": 4,
    "InvalidCharacterError": 5,
    "NoModificationAllowedError": 7,
    "NotFoundError": 8,
    "NotSupportedError": 9,
    "InUseAttributeError": 10,
    "InvalidStateError": 11,
    "SyntaxError": 12,
    "InvalidModificationError": 13,
    "NamespaceError": 14,
    "InvalidAccessError": 15,
    "SecurityError": 18,
    "NetworkError": 19,
    "AbortError": 20,
    "URLMismatchError": 21,
    "QuotaExceededError": 22,
    "TimeoutError": 23,
    "InvalidNodeTypeError": 24,
    "DataCloneError": 25,
}

# JSON応答のトップレベルに必ず入れるキー。bridge.py側の全@Slotは
# 成功時 {"ok": True, "result": ...}、失敗時 {"ok": False, "error": {...}}
# の形で統一されたJSON文字列を返す。polyfill.js側はこの2値のどちらかだけを
# 想定していればよく、片方だけ・キー名違いといった不整合が起きない。
ErrorPayload = dict[str, Any]


def make_error(name: str, message: str, *, extra: Optional[dict[str, Any]] = None) -> ErrorPayload:
    """DOMException(またはTypeError)としてJS側に投げ返すエラーの本体を作る。"""
    payload: ErrorPayload = {
        "name": name,
        "message": message,
        "code": _LEGACY_CODES.get(name, 0),
    }
    if extra:
        payload.update(extra)
    return payload


def ok(result: Any = None) -> ErrorPayload:
    """成功応答の共通ラッパー。"""
    return {"ok": True, "result": result}


def fail(name: str, message: str, *, extra: Optional[dict[str, Any]] = None) -> ErrorPayload:
    """失敗応答の共通ラッパー。"""
    return {"ok": False, "error": make_error(name, message, extra=extra)}


# --- 個別のショートハンド。bridge.py / hardening.py から呼びやすいように ---

def security_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("SecurityError", message, extra=extra or None)


def network_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("NetworkError", message, extra=extra or None)


def not_found_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("NotFoundError", message, extra=extra or None)


def invalid_state_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("InvalidStateError", message, extra=extra or None)


def not_supported_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("NotSupportedError", message, extra=extra or None)


def abort_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("AbortError", message, extra=extra or None)


def type_error(message: str, **extra: Any) -> ErrorPayload:
    return fail("TypeError", message, extra=extra or None)


def unknown_error(message: str, **extra: Any) -> ErrorPayload:
    """想定外の例外(pythonの一般Exception)を捕まえたときに使う。
    実機のブラウザではここは"OperationError"や素のErrorになることが
    多いが、原因不明のバグを握りつぶさずJS側コンソールに必ず出す。"""
    return fail("OperationError", message, extra=extra or None)
