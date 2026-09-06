# -*- coding: utf-8 -*-
"""gatt_registry.pyのテスト。

GATT_SERVICE_NAMES等の3辞書はWebBluetoothCG/registriesの一次データから
機械的に生成したものなので、ここでは主に canonical_uuid() /
resolve_uuid() / friendly_name() の変換ロジックと、データそのものの
整合性(重複や不正なUUID形式が無いこと)を検証する。"""
import pytest

from pyside6_webbluetooth import gatt_registry as g


class TestRegistryDataIntegrity:
    """一次情報からの機械生成データそのものの健全性チェック。"""

    @pytest.mark.parametrize(
        "registry_name",
        ["GATT_SERVICE_NAMES", "GATT_CHARACTERISTIC_NAMES", "GATT_DESCRIPTOR_NAMES"],
    )
    def test_all_uuids_well_formed_128bit(self, registry_name):
        registry = getattr(g, registry_name)
        for name, uuid in registry.items():
            assert g._UUID128_RE.match(uuid), f"{registry_name}[{name!r}] = {uuid!r} is not a well-formed 128-bit UUID"

    def test_expected_counts(self):
        # WebBluetoothCG/registries取得時点での件数。レジストリが更新されて
        # 増減した場合はこのテストを更新すること。
        assert len(g.GATT_SERVICE_NAMES) == 39
        assert len(g.GATT_CHARACTERISTIC_NAMES) == 214
        assert len(g.GATT_DESCRIPTOR_NAMES) == 15

    def test_well_known_services_present(self):
        assert g.GATT_SERVICE_NAMES["battery_service"] == "0000180f-0000-1000-8000-00805f9b34fb"
        assert g.GATT_SERVICE_NAMES["heart_rate"] == "0000180d-0000-1000-8000-00805f9b34fb"
        assert g.GATT_SERVICE_NAMES["device_information"] == "0000180a-0000-1000-8000-00805f9b34fb"

    def test_well_known_characteristics_present(self):
        assert g.GATT_CHARACTERISTIC_NAMES["battery_level"] == "00002a19-0000-1000-8000-00805f9b34fb"
        assert g.GATT_CHARACTERISTIC_NAMES["gap.device_name"] == "00002a00-0000-1000-8000-00805f9b34fb"

    def test_well_known_descriptors_present(self):
        assert (
            g.GATT_DESCRIPTOR_NAMES["gatt.client_characteristic_configuration"]
            == "00002902-0000-1000-8000-00805f9b34fb"
        )


class TestCanonicalUuid:
    def test_int_alias(self):
        assert g.canonical_uuid(0x180F) == "0000180f-0000-1000-8000-00805f9b34fb"

    def test_hex_string_with_prefix(self):
        assert g.canonical_uuid("0x180F") == "0000180f-0000-1000-8000-00805f9b34fb"

    def test_hex_string_without_prefix(self):
        assert g.canonical_uuid("180f") == "0000180f-0000-1000-8000-00805f9b34fb"

    def test_already_full_uuid_is_lowercased(self):
        assert (
            g.canonical_uuid("0000180F-0000-1000-8000-00805F9B34FB")
            == "0000180f-0000-1000-8000-00805f9b34fb"
        )

    def test_custom_128bit_uuid_passthrough(self):
        custom = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
        assert g.canonical_uuid(custom) == custom

    def test_32bit_alias(self):
        assert g.canonical_uuid(0x0000180F) == "0000180f-0000-1000-8000-00805f9b34fb"

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            g.canonical_uuid("not-a-uuid")

    def test_out_of_range_int_raises(self):
        with pytest.raises(ValueError):
            g.canonical_uuid(0x1_0000_0000)

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError):
            g.canonical_uuid(3.14)  # type: ignore[arg-type]


class TestResolveUuid:
    def test_known_name(self):
        assert (
            g.resolve_uuid("battery_service", g.GATT_SERVICE_NAMES)
            == "0000180f-0000-1000-8000-00805f9b34fb"
        )

    def test_known_name_case_insensitive(self):
        assert (
            g.resolve_uuid("BATTERY_SERVICE", g.GATT_SERVICE_NAMES)
            == "0000180f-0000-1000-8000-00805f9b34fb"
        )

    def test_falls_back_to_canonical_uuid_for_unknown_name(self):
        custom = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
        assert g.resolve_uuid(custom, g.GATT_SERVICE_NAMES) == custom

    def test_falls_back_to_canonical_uuid_for_int(self):
        assert (
            g.resolve_uuid(0x180F, g.GATT_SERVICE_NAMES)
            == "0000180f-0000-1000-8000-00805f9b34fb"
        )


class TestFriendlyName:
    def test_known_uuid(self):
        assert g.friendly_name("0000180f-0000-1000-8000-00805f9b34fb") == "battery_service"

    def test_unknown_uuid_returns_none(self):
        assert g.friendly_name("6e400001-b5a3-f393-e0a9-e50e24dcca9e") is None
