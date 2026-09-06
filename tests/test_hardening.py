# -*- coding: utf-8 -*-
"""hardening.pyのテスト。

GATTブロックリストの適用、requestDevice()オプションの検証、
「matches a filter」アルゴリズム、bleak特性プロパティのマッピングを検証する。
Qt/実BLEハードウェアに依存しない純粋なロジックのみを対象とする。"""
import pytest

from pyside6_webbluetooth import hardening as h


class TestBlocklist:
    def test_hid_service_fully_blocked(self):
        uuid = "00001812-0000-1000-8000-00805f9b34fb"
        assert h.is_blocked_entirely(uuid)
        assert h.is_blocked_for_read(uuid)
        assert h.is_blocked_for_write(uuid)

    def test_battery_service_not_blocked(self):
        uuid = "0000180f-0000-1000-8000-00805f9b34fb"
        assert not h.is_blocked_entirely(uuid)
        assert not h.is_blocked_for_read(uuid)
        assert not h.is_blocked_for_write(uuid)

    def test_peripheral_privacy_flag_exclude_writes_only(self):
        uuid = "00002a02-0000-1000-8000-00805f9b34fb"
        assert not h.is_blocked_entirely(uuid)
        assert not h.is_blocked_for_read(uuid)
        assert h.is_blocked_for_write(uuid)

    def test_serial_number_string_exclude_all(self):
        uuid = "00002a25-0000-1000-8000-00805f9b34fb"
        assert h.is_blocked_entirely(uuid)
        assert h.is_blocked_for_read(uuid)
        assert h.is_blocked_for_write(uuid)

    def test_cccd_exclude_writes_only(self):
        uuid = "00002902-0000-1000-8000-00805f9b34fb"
        assert not h.is_blocked_for_read(uuid)
        assert h.is_blocked_for_write(uuid)

    def test_blocklist_is_case_insensitive(self):
        assert h.is_blocked_entirely("00001812-0000-1000-8000-00805F9B34FB".lower())


class TestCanonicalizeFilter:
    def test_empty_filter_rejected(self):
        with pytest.raises(h.InvalidFilterError):
            h.canonicalize_filter({})

    def test_services_only(self):
        f = h.canonicalize_filter({"services": ["battery_service"]})
        assert f["services"] == ["0000180f-0000-1000-8000-00805f9b34fb"]

    def test_name_prefix_only(self):
        f = h.canonicalize_filter({"namePrefix": "MyDevice"})
        assert f["namePrefix"] == "MyDevice"

    def test_blocklisted_service_rejected(self):
        with pytest.raises(h.BlocklistedUUIDError):
            h.canonicalize_filter({"services": ["human_interface_device"]})

    def test_empty_services_array_rejected(self):
        with pytest.raises(h.InvalidFilterError):
            h.canonicalize_filter({"services": []})

    def test_manufacturer_data_prefix_and_mask_parsed_as_bytes(self):
        f = h.canonicalize_filter(
            {"manufacturerData": [{"companyIdentifier": 1, "dataPrefix": "a1b2", "mask": "ffff"}]}
        )
        assert f["manufacturerData"][0]["dataPrefix"] == bytes.fromhex("a1b2")
        assert f["manufacturerData"][0]["mask"] == bytes.fromhex("ffff")

    def test_manufacturer_data_mismatched_mask_length_rejected(self):
        with pytest.raises(h.InvalidFilterError):
            h.canonicalize_filter(
                {"manufacturerData": [{"companyIdentifier": 1, "dataPrefix": "a1b2", "mask": "ff"}]}
            )


class TestValidateRequestOptions:
    def test_accept_all_and_filters_mutually_exclusive(self):
        with pytest.raises(h.InvalidFilterError):
            h.validate_request_options({"acceptAllDevices": True, "filters": [{"namePrefix": "x"}]})

    def test_neither_specified_rejected(self):
        with pytest.raises(h.InvalidFilterError):
            h.validate_request_options({})

    def test_valid_filters_and_optional_services(self):
        opts = h.validate_request_options(
            {"filters": [{"services": ["battery_service"]}], "optionalServices": ["heart_rate"]}
        )
        assert opts["filters"][0]["services"] == ["0000180f-0000-1000-8000-00805f9b34fb"]
        assert opts["optionalServices"] == ["0000180d-0000-1000-8000-00805f9b34fb"]

    def test_accept_all_devices(self):
        opts = h.validate_request_options({"acceptAllDevices": True})
        assert opts["acceptAllDevices"] is True
        assert opts["filters"] == []


class TestAllowedServicesForGrant:
    def test_union_of_filters_and_optional_services(self):
        opts = h.validate_request_options(
            {"filters": [{"services": ["battery_service"]}], "optionalServices": ["heart_rate"]}
        )
        allowed = h.allowed_services_for_grant(opts)
        assert allowed == {
            "0000180f-0000-1000-8000-00805f9b34fb",
            "0000180d-0000-1000-8000-00805f9b34fb",
        }


class TestDeviceMatchesFilter:
    def test_services_filter_requires_all_listed(self):
        f = h.canonicalize_filter({"services": ["battery_service", "heart_rate"]})
        assert h.device_matches_filter(
            local_name=None,
            service_uuids=[
                "0000180f-0000-1000-8000-00805f9b34fb",
                "0000180d-0000-1000-8000-00805f9b34fb",
                "0000180a-0000-1000-8000-00805f9b34fb",
            ],
            manufacturer_data={},
            service_data={},
            filt=f,
        )

    def test_services_filter_fails_when_one_missing(self):
        f = h.canonicalize_filter({"services": ["battery_service", "heart_rate"]})
        assert not h.device_matches_filter(
            local_name=None,
            service_uuids=["0000180f-0000-1000-8000-00805f9b34fb"],
            manufacturer_data={},
            service_data={},
            filt=f,
        )

    def test_name_exact_match(self):
        f = h.canonicalize_filter({"name": "Widget-42"})
        assert h.device_matches_filter(
            local_name="Widget-42", service_uuids=[], manufacturer_data={}, service_data={}, filt=f
        )
        assert not h.device_matches_filter(
            local_name="Widget-43", service_uuids=[], manufacturer_data={}, service_data={}, filt=f
        )

    def test_name_prefix_match(self):
        f = h.canonicalize_filter({"namePrefix": "Widget-"})
        assert h.device_matches_filter(
            local_name="Widget-42", service_uuids=[], manufacturer_data={}, service_data={}, filt=f
        )
        assert not h.device_matches_filter(
            local_name="Gadget-42", service_uuids=[], manufacturer_data={}, service_data={}, filt=f
        )

    def test_manufacturer_data_prefix_and_mask(self):
        f = h.canonicalize_filter(
            {"manufacturerData": [{"companyIdentifier": 0x0499, "dataPrefix": "a1b2", "mask": "ffff"}]}
        )
        assert h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={0x0499: bytes.fromhex("a1b2c3d4")},
            service_data={},
            filt=f,
        )
        assert not h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={0x0499: bytes.fromhex("a1b3c3d4")},
            service_data={},
            filt=f,
        )
        assert not h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={0x0500: bytes.fromhex("a1b2c3d4")},
            service_data={},
            filt=f,
        )

    def test_manufacturer_data_partial_mask(self):
        f = h.canonicalize_filter(
            {"manufacturerData": [{"companyIdentifier": 1, "dataPrefix": "a1b0", "mask": "fff0"}]}
        )
        # low nibble of 2nd byte differs but is masked out -> still matches
        assert h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={1: bytes.fromhex("a1bf0000")},
            service_data={},
            filt=f,
        )
        assert not h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={1: bytes.fromhex("a2bf0000")},
            service_data={},
            filt=f,
        )

    def test_manufacturer_data_presence_only(self):
        f = h.canonicalize_filter({"manufacturerData": [{"companyIdentifier": 0x1234}]})
        assert h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={0x1234: b""},
            service_data={},
            filt=f,
        )
        assert not h.device_matches_filter(
            local_name=None, service_uuids=[], manufacturer_data={}, service_data={}, filt=f
        )

    def test_service_data_prefix(self):
        f = h.canonicalize_filter({"serviceData": [{"service": "battery_service", "dataPrefix": "64"}]})
        assert h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={},
            service_data={"0000180f-0000-1000-8000-00805f9b34fb": bytes.fromhex("6401")},
            filt=f,
        )
        assert not h.device_matches_filter(
            local_name=None,
            service_uuids=[],
            manufacturer_data={},
            service_data={"0000180f-0000-1000-8000-00805f9b34fb": bytes.fromhex("6301")},
            filt=f,
        )


class TestDeviceMatchesOptions:
    def test_accept_all_devices_matches_anything(self):
        opts = h.validate_request_options({"acceptAllDevices": True})
        assert h.device_matches_options(
            local_name="anything", service_uuids=[], manufacturer_data={}, service_data={}, options=opts
        )

    def test_matches_if_any_filter_matches(self):
        opts = h.validate_request_options({"filters": [{"name": "A"}, {"name": "B"}]})
        assert h.device_matches_options(
            local_name="A", service_uuids=[], manufacturer_data={}, service_data={}, options=opts
        )
        assert h.device_matches_options(
            local_name="B", service_uuids=[], manufacturer_data={}, service_data={}, options=opts
        )
        assert not h.device_matches_options(
            local_name="C", service_uuids=[], manufacturer_data={}, service_data={}, options=opts
        )


class TestCharacteristicPropertiesDict:
    def test_mapping(self):
        props = h.characteristic_properties_dict(["read", "notify", "write-without-response"])
        assert props == {
            "broadcast": False,
            "read": True,
            "writeWithoutResponse": True,
            "write": False,
            "notify": True,
            "indicate": False,
            "authenticatedSignedWrites": False,
            "reliableWrite": False,
            "writableAuxiliaries": False,
        }

    def test_all_properties(self):
        all_props = [
            "broadcast",
            "read",
            "write-without-response",
            "write",
            "notify",
            "indicate",
            "authenticated-signed-writes",
            "reliable-write",
            "writable-auxiliaries",
        ]
        props = h.characteristic_properties_dict(all_props)
        assert all(props.values())

    def test_unknown_property_ignored(self):
        # BlueZ拡張(encrypt-read等)やextended-propertiesは無視されるだけで
        # 例外にはならない。
        props = h.characteristic_properties_dict(["read", "encrypt-read", "extended-properties"])
        assert props["read"] is True
        assert sum(props.values()) == 1
