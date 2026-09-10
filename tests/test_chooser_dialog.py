# -*- coding: utf-8 -*-
"""chooser_dialog.pyのテスト。

実際のBLEスキャンは行わず、`BleWorker`をモックに差し替えて
「フィルタに一致しないデバイスは表示しない」「RSSI降順に並ぶ」
「accept/reject どちらの経路でもスキャンが確実に(かつ1回だけ)
停止される」ことを検証する。"""
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt

from pyside6_webbluetooth import hardening
from pyside6_webbluetooth.chooser_dialog import BluetoothDeviceChooserDialog


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class _FakeBLEDevice:
    def __init__(self, address, name):
        self.address = address
        self.name = name


class _FakeAdv:
    def __init__(self, local_name, service_uuids, rssi):
        self.local_name = local_name
        self.service_uuids = service_uuids
        self.manufacturer_data = {}
        self.service_data = {}
        self.rssi = rssi


BATTERY_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
HEART_RATE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"


@pytest.fixture
def battery_filter_options():
    return hardening.validate_request_options({"filters": [{"services": ["battery_service"]}]})


class TestChooserDialogFiltersAndSorts(object):
    def test_only_matching_devices_shown_sorted_by_rssi(self, qapp, battery_filter_options):
        fake_worker = MagicMock()
        fake_worker.snapshot_scan_results.return_value = {
            "AA:AA:AA:AA:AA:AA": (
                _FakeBLEDevice("AA:AA:AA:AA:AA:AA", "WeakMatch"),
                _FakeAdv("WeakMatch", [BATTERY_UUID], -80),
            ),
            "BB:BB:BB:BB:BB:BB": (
                _FakeBLEDevice("BB:BB:BB:BB:BB:BB", "StrongMatch"),
                _FakeAdv("StrongMatch", [BATTERY_UUID], -40),
            ),
            "CC:CC:CC:CC:CC:CC": (
                _FakeBLEDevice("CC:CC:CC:CC:CC:CC", "NoMatch"),
                _FakeAdv("NoMatch", [HEART_RATE_UUID], -30),
            ),
        }
        dlg = BluetoothDeviceChooserDialog(
            origin="https://example.com", options=battery_filter_options, worker=fake_worker
        )
        try:
            fake_worker.start_scan.assert_called_once()
            dlg._poll_scan_results()
            assert dlg._list_widget.count() == 2
            assert dlg._list_widget.item(0).data(Qt.UserRole) == "BB:BB:BB:BB:BB:BB"
            assert dlg._list_widget.item(1).data(Qt.UserRole) == "AA:AA:AA:AA:AA:AA"
        finally:
            dlg.reject()

    def test_no_devices_ok_button_disabled(self, qapp, battery_filter_options):
        fake_worker = MagicMock()
        fake_worker.snapshot_scan_results.return_value = {}
        dlg = BluetoothDeviceChooserDialog(
            origin="https://example.com", options=battery_filter_options, worker=fake_worker
        )
        try:
            dlg._poll_scan_results()
            assert dlg._list_widget.count() == 0
            assert not dlg._ok_button.isEnabled()
        finally:
            dlg.reject()


class TestChooserDialogAcceptReject:
    def test_accept_sets_selected_address_and_stops_scan_once(self, qapp, battery_filter_options):
        fake_worker = MagicMock()
        fake_worker.snapshot_scan_results.return_value = {
            "AA:AA:AA:AA:AA:AA": (
                _FakeBLEDevice("AA:AA:AA:AA:AA:AA", "Device"),
                _FakeAdv("Device", [BATTERY_UUID], -50),
            ),
        }
        dlg = BluetoothDeviceChooserDialog(
            origin="https://example.com", options=battery_filter_options, worker=fake_worker
        )
        dlg._poll_scan_results()
        dlg._list_widget.setCurrentRow(0)
        assert dlg._ok_button.isEnabled()
        dlg._on_accept()
        assert dlg.selected_address == "AA:AA:AA:AA:AA:AA"
        fake_worker.stop_scan.assert_called_once()

    def test_reject_stops_scan_once_and_leaves_selection_none(self, qapp, battery_filter_options):
        fake_worker = MagicMock()
        fake_worker.snapshot_scan_results.return_value = {}
        dlg = BluetoothDeviceChooserDialog(
            origin="https://example.com", options=battery_filter_options, worker=fake_worker
        )
        dlg.reject()
        fake_worker.stop_scan.assert_called_once()
        assert dlg.selected_address is None


class TestChooserDialogOriginDisplay:
    def test_origin_is_html_escaped(self, qapp, battery_filter_options):
        dlg = BluetoothDeviceChooserDialog(
            origin="<script>evil</script>", options=battery_filter_options, worker=MagicMock()
        )
        try:
            labels = [
                dlg.layout().itemAt(i).widget()
                for i in range(dlg.layout().count())
                if hasattr(dlg.layout().itemAt(i).widget(), "text")
            ]
            texts = [w.text() for w in labels]
            assert any("&lt;script&gt;" in t for t in texts)
            assert not any("<script>evil</script>" in t for t in texts)
        finally:
            dlg.reject()


class TestChooserDialogRobustness:
    def test_malformed_advertisement_from_one_device_does_not_break_others(self, qapp, battery_filter_options):
        """近くの実デバイスが規格外の広告データ(serviceDataのキーが
        UUIDとして解釈できない等)を送ってきても、他の(正しくマッチする)
        デバイスの一覧表示やダイアログ自体を壊さないことを確認する。"""

        fake_worker = MagicMock()
        good_adv = _FakeAdv("GoodDevice", [BATTERY_UUID], -50)
        good_adv.service_data = {BATTERY_UUID: b"\x64"}
        bad_adv = _FakeAdv("BadDevice", [BATTERY_UUID], -40)
        bad_adv.service_data = {"not-a-valid-uuid": b"\x01"}  # canonical_uuid()がValueErrorを送出する
        fake_worker.snapshot_scan_results.return_value = {
            "AA:AA:AA:AA:AA:AA": (_FakeBLEDevice("AA:AA:AA:AA:AA:AA", "GoodDevice"), good_adv),
            "BB:BB:BB:BB:BB:BB": (_FakeBLEDevice("BB:BB:BB:BB:BB:BB", "BadDevice"), bad_adv),
        }
        # serviceDataでのフィルタでなければcanonical_uuid()に到達しないため、
        # filterにserviceDataを含めて確実に例外経路を通す
        from pyside6_webbluetooth import hardening

        options = hardening.validate_request_options(
            {"filters": [{"services": ["battery_service"], "serviceData": [{"service": "battery_service"}]}]}
        )
        dlg = BluetoothDeviceChooserDialog(origin="https://example.com", options=options, worker=fake_worker)
        try:
            dlg._poll_scan_results()  # 例外を送出せず完了すること
            addresses = [dlg._list_widget.item(i).data(Qt.UserRole) for i in range(dlg._list_widget.count())]
            assert "AA:AA:AA:AA:AA:AA" in addresses
            assert "BB:BB:BB:BB:BB:BB" not in addresses
        finally:
            dlg.reject()
