# -*- coding: utf-8 -*-
"""requestDevice()のためのネイティブなデバイス選択ダイアログ。

姉妹プロジェクトpyside6-webusbのchooser_dialog.pyと同じ設計方針を踏襲する:

  - 要求元オリジンを必ず明示する(なりすまし防止。「このサイトが
    デバイスへのアクセスを求めています」を常にユーザーに見せる)。
  - 自動選択は絶対にしない。ユーザーが明示的に1台選んでOKを押すまで
    requestDevice()のPromiseは解決しない。
  - リストはライブ更新される。

WebUSBとの違いは、リストの母集団が「すでに挿さっている既知デバイス」の
列挙ではなく、「今まさに実行中のBLEスキャンで見つかったデバイス」である点。
そのためダイアログを開いている間は`BleWorker`でスキャンを実行し続け、
一定間隔でポーリングしてリストを更新する。"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from . import hardening
from .ble_worker import BleWorker


class BluetoothDeviceChooserDialog(QDialog):
    """requestDevice()呼び出し1回につき1つ生成される、モーダルな
    デバイス選択ダイアログ。`exec()`で表示し、戻り値がQDialog.Acceptedなら
    `selected_address`にユーザーが選んだデバイスのアドレスが入っている。"""

    def __init__(
        self,
        *,
        origin: str,
        options: dict,
        worker: BleWorker,
        poll_interval_ms: int = 400,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._options = options
        self._worker = worker
        self.selected_address: Optional[str] = None
        self._addresses_in_list: dict[str, int] = {}  # address -> QListWidgetItem row

        self.setWindowTitle("Bluetoothデバイスの選択 / Choose a Bluetooth device")
        self.setMinimumSize(480, 360)
        self.setModal(True)

        layout = QVBoxLayout(self)

        origin_label = QLabel(
            f"<b>{self._escape(origin)}</b> がBluetoothデバイスへのアクセスを求めています。"
        )
        origin_label.setWordWrap(True)
        layout.addWidget(origin_label)

        self._status_label = QLabel("周辺のデバイスをスキャンしています…")
        layout.addWidget(self._status_label)

        self._list_widget = QListWidget()
        self._list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list_widget)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        self._ok_button = button_box.button(QDialogButtonBox.Ok)
        self._ok_button.setEnabled(False)
        layout.addWidget(button_box)

        self._list_widget.itemSelectionChanged.connect(
            lambda: self._ok_button.setEnabled(self._list_widget.currentItem() is not None)
        )

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_interval_ms)
        self._poll_timer.timeout.connect(self._poll_scan_results)

        service_uuids = self._service_uuid_hint()
        self._worker.start_scan(service_uuids=service_uuids)
        self._poll_timer.start()

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _service_uuid_hint(self) -> Optional[list]:
        """スキャナに渡す軽量なサービスUUIDフィルタのヒント。
        (OS側のスキャンフィルタで絞れると発見が速くなる場合があるが、
        必須ではないため、フィルタ条件が単純な場合のみ使う。
        acceptAllDevicesの場合や複雑なフィルタの場合はNoneとし、
        最終的なマッチ判定はhardening.device_matches_options()に委ねる。)"""
        if self._options.get("acceptAllDevices"):
            return None
        uuids: set[str] = set()
        for filt in self._options.get("filters", []):
            svc = filt.get("services")
            if not svc:
                return None  # servicesを指定しないfilterがあるため絞り込めない
            uuids.update(svc)
        return list(uuids) if uuids else None

    def _poll_scan_results(self) -> None:
        results = self._worker.snapshot_scan_results()
        matched = []
        for address, (device, adv) in results.items():
            local_name = adv.local_name or device.name
            if hardening.device_matches_options(
                local_name=local_name,
                service_uuids=list(adv.service_uuids or []),
                manufacturer_data=dict(adv.manufacturer_data or {}),
                service_data=dict(adv.service_data or {}),
                options=self._options,
            ):
                matched.append((address, local_name, adv.rssi))

        # 信号強度が強い順(=近い/繋がりやすい順)に並べる。RSSI未取得はNoneでは
        # なく十分小さい値として扱い、末尾に回す。
        matched.sort(key=lambda item: item[2] if item[2] is not None else -999, reverse=True)

        self._status_label.setText(
            f"見つかったデバイス: {len(matched)}件(スキャン中…)"
            if matched
            else "周辺のデバイスをスキャンしています…(まだ見つかっていません)"
        )

        self._rebuild_list(matched)

    def _rebuild_list(self, matched: list) -> None:
        previously_selected = self._current_selected_address()

        self._list_widget.clear()
        self._addresses_in_list.clear()
        for row, (address, local_name, rssi) in enumerate(matched):
            display_name = local_name or "(名前不明のデバイス)"
            rssi_text = f"  [{rssi} dBm]" if rssi is not None else ""
            item = QListWidgetItem(f"{display_name}{rssi_text}\n{address}")
            item.setData(Qt.UserRole, address)
            self._list_widget.addItem(item)
            self._addresses_in_list[address] = row
            if address == previously_selected:
                self._list_widget.setCurrentItem(item)

    def _current_selected_address(self) -> Optional[str]:
        item = self._list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _on_item_double_clicked(self, _item: QListWidgetItem) -> None:
        self._on_accept()

    def _on_accept(self) -> None:
        address = self._current_selected_address()
        if address is None:
            return
        self.selected_address = address
        self.accept()

    def done(self, result: int) -> None:  # noqa: D102 - Qt override
        self._poll_timer.stop()
        self._worker.stop_scan()
        super().done(result)
