# -*- coding: utf-8 -*-
"""`concurrent.futures.Future` をPromiseの`.then()`のように連結するための
小さなユーティリティ。

bridge.py の「サービス/特性ツリーを取得 → 見つけたオブジェクトを検証 →
実際の読み書きを行う」という複数段の非同期フローは、元々
`ble_worker.py`(bleak/asyncio)の"privateなコルーチンを直接await"という
実装依存の方法で書かれていた。v0.0.0aで2つ目のバックエンド候補
(PySide6.QtBluetooth、asyncioを一切使わずQtのシグナル/スロットだけで
動く)を検討した際、この書き方だとバックエンドごとに全く別のフロー実装が
必要になってしまうことに気づいた。

`future_then()`はこの問題を解消する: バックエンドがasyncioスレッド経由で
Futureを解決しようが、Qtのシグナルハンドラから直接`set_result()`しようが、
呼び出し側(bridge.py)は「Futureを受け取り、次のFutureを返す」という
形だけを意識すればよくなる。"""
from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable, TypeVar, Union

T = TypeVar("T")
U = TypeVar("U")


def future_then(future: "Future[T]", on_success: Callable[[T], Union[U, "Future[U]"]]) -> "Future[U]":
    """`future`が成功したら`on_success(result)`を呼ぶ。

    `on_success`の戻り値がさらに`Future`であれば、それも解決されるまで
    自動的に連結する(Promiseの`.then()`と同じ「自動フラット化」)。
    `on_success`が例外を送出した場合、返り値のFutureはその例外で失敗する。
    `future`自体が失敗した場合は、`on_success`を呼ばずにその例外を
    そのまま伝播する。"""
    out: "Future[U]" = Future()

    def on_input_done(fut: "Future[T]") -> None:
        try:
            result = fut.result()
        except Exception as exc:  # noqa: BLE001 - 元の例外をそのまま伝播する
            out.set_exception(exc)
            return
        try:
            next_value: Any = on_success(result)
        except Exception as exc:  # noqa: BLE001
            out.set_exception(exc)
            return
        if isinstance(next_value, Future):

            def on_next_done(next_fut: "Future[U]") -> None:
                try:
                    out.set_result(next_fut.result())
                except Exception as exc:  # noqa: BLE001
                    out.set_exception(exc)

            next_value.add_done_callback(on_next_done)
        else:
            out.set_result(next_value)

    future.add_done_callback(on_input_done)
    return out
