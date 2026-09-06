# -*- coding: utf-8 -*-
"""GATT標準サービス/特性/記述子の名前解決(BluetoothUUID相当)。

Web Bluetooth仕様のBluetoothUUID.getService() / getCharacteristic() /
getDescriptor()、およびrequestDevice()のfilters内で
'battery_service'のような人間可読な名前を128-bit UUIDへ変換する処理に対応する。

以下の3つの辞書は手打ちではなく、一次情報である
https://github.com/WebBluetoothCG/registries の
gatt_assigned_services.txt / gatt_assigned_characteristics.txt /
gatt_assigned_descriptors.txt (取得時点のmasterブランチ)から機械的に
生成したものである(生成スクリプトはリポジトリのdevツールには含めていないが、
値は取得したテキストファイルをそのまま `name uuid` の1行1エントリで
パースしただけで、手動転記による誤りが入り込む余地がないようにしている)。
Chromiumの実装(third_party/blink/renderer/modules/bluetooth/bluetooth_uuid.cc)
に基づく値である旨が原本にも明記されている。
"""
from __future__ import annotations

import re
from typing import Optional, Union

# Bluetooth Base UUID (Bluetooth Core Specification Vol 3, Part B, Section 2.5.1)。
# 16-bit / 32-bit のショートフォームUUIDは、この定数のうち0-31bit目を
# 対象の値で置き換えることで128-bit UUIDへ変換できる。
_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"

_UUID128_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_HEX32_RE = re.compile(r"^(0x)?[0-9a-f]{1,8}$", re.IGNORECASE)


def canonical_uuid(alias: Union[int, str]) -> str:
    """16-bit/32-bitのショートUUID(int、または"0x180f"等の文字列)を
    Bluetooth Base UUIDに基づく128-bit UUID文字列へ変換する。
    すでに128-bit形式の文字列が渡された場合は、小文字に正規化して返す。

    不正な形式の場合は ValueError を送出する(呼び出し側でTypeErrorとして
    JSに投げ返すことを想定)。"""
    if isinstance(alias, int):
        if not (0 <= alias <= 0xFFFFFFFF):
            raise ValueError(f"UUID alias out of range: {alias!r}")
        return f"{alias:08x}{_BASE_UUID_SUFFIX}"

    if not isinstance(alias, str):
        raise ValueError(f"invalid UUID alias type: {type(alias)!r}")

    s = alias.strip().lower()
    if _UUID128_RE.match(s):
        return s

    hex_part = s[2:] if s.startswith("0x") else s
    if _HEX32_RE.match(hex_part) and 1 <= len(hex_part) <= 8:
        try:
            value = int(hex_part, 16)
        except ValueError as exc:
            raise ValueError(f"invalid UUID alias: {alias!r}") from exc
        return f"{value:08x}{_BASE_UUID_SUFFIX}"

    raise ValueError(f"invalid UUID alias: {alias!r}")


# --- GATT標準サービス名 -> UUID (gatt_assigned_services.txt より) ---
GATT_SERVICE_NAMES: dict[str, str] = {
    'generic_access': '00001800-0000-1000-8000-00805f9b34fb',
    'generic_attribute': '00001801-0000-1000-8000-00805f9b34fb',
    'immediate_alert': '00001802-0000-1000-8000-00805f9b34fb',
    'link_loss': '00001803-0000-1000-8000-00805f9b34fb',
    'tx_power': '00001804-0000-1000-8000-00805f9b34fb',
    'current_time': '00001805-0000-1000-8000-00805f9b34fb',
    'reference_time_update': '00001806-0000-1000-8000-00805f9b34fb',
    'next_dst_change': '00001807-0000-1000-8000-00805f9b34fb',
    'glucose': '00001808-0000-1000-8000-00805f9b34fb',
    'health_thermometer': '00001809-0000-1000-8000-00805f9b34fb',
    'device_information': '0000180a-0000-1000-8000-00805f9b34fb',
    'heart_rate': '0000180d-0000-1000-8000-00805f9b34fb',
    'phone_alert_status': '0000180e-0000-1000-8000-00805f9b34fb',
    'battery_service': '0000180f-0000-1000-8000-00805f9b34fb',
    'blood_pressure': '00001810-0000-1000-8000-00805f9b34fb',
    'alert_notification': '00001811-0000-1000-8000-00805f9b34fb',
    'human_interface_device': '00001812-0000-1000-8000-00805f9b34fb',
    'scan_parameters': '00001813-0000-1000-8000-00805f9b34fb',
    'running_speed_and_cadence': '00001814-0000-1000-8000-00805f9b34fb',
    'automation_io': '00001815-0000-1000-8000-00805f9b34fb',
    'cycling_speed_and_cadence': '00001816-0000-1000-8000-00805f9b34fb',
    'cycling_power': '00001818-0000-1000-8000-00805f9b34fb',
    'location_and_navigation': '00001819-0000-1000-8000-00805f9b34fb',
    'environmental_sensing': '0000181a-0000-1000-8000-00805f9b34fb',
    'body_composition': '0000181b-0000-1000-8000-00805f9b34fb',
    'user_data': '0000181c-0000-1000-8000-00805f9b34fb',
    'weight_scale': '0000181d-0000-1000-8000-00805f9b34fb',
    'bond_management': '0000181e-0000-1000-8000-00805f9b34fb',
    'continuous_glucose_monitoring': '0000181f-0000-1000-8000-00805f9b34fb',
    'internet_protocol_support': '00001820-0000-1000-8000-00805f9b34fb',
    'indoor_positioning': '00001821-0000-1000-8000-00805f9b34fb',
    'pulse_oximeter': '00001822-0000-1000-8000-00805f9b34fb',
    'http_proxy': '00001823-0000-1000-8000-00805f9b34fb',
    'transport_discovery': '00001824-0000-1000-8000-00805f9b34fb',
    'object_transfer': '00001825-0000-1000-8000-00805f9b34fb',
    'fitness_machine': '00001826-0000-1000-8000-00805f9b34fb',
    'mesh_provisioning': '00001827-0000-1000-8000-00805f9b34fb',
    'mesh_proxy': '00001828-0000-1000-8000-00805f9b34fb',
    'reconnection_configuration': '00001829-0000-1000-8000-00805f9b34fb',}

# --- GATT標準特性名 -> UUID (gatt_assigned_characteristics.txt より) ---
GATT_CHARACTERISTIC_NAMES: dict[str, str] = {
    'gap.device_name': '00002a00-0000-1000-8000-00805f9b34fb',
    'gap.appearance': '00002a01-0000-1000-8000-00805f9b34fb',
    'gap.peripheral_privacy_flag': '00002a02-0000-1000-8000-00805f9b34fb',
    'gap.reconnection_address': '00002a03-0000-1000-8000-00805f9b34fb',
    'gap.peripheral_preferred_connection_parameters': '00002a04-0000-1000-8000-00805f9b34fb',
    'gatt.service_changed': '00002a05-0000-1000-8000-00805f9b34fb',
    'alert_level': '00002a06-0000-1000-8000-00805f9b34fb',
    'tx_power_level': '00002a07-0000-1000-8000-00805f9b34fb',
    'date_time': '00002a08-0000-1000-8000-00805f9b34fb',
    'day_of_week': '00002a09-0000-1000-8000-00805f9b34fb',
    'day_date_time': '00002a0a-0000-1000-8000-00805f9b34fb',
    'exact_time_100': '00002a0b-0000-1000-8000-00805f9b34fb',
    'exact_time_256': '00002a0c-0000-1000-8000-00805f9b34fb',
    'dst_offset': '00002a0d-0000-1000-8000-00805f9b34fb',
    'time_zone': '00002a0e-0000-1000-8000-00805f9b34fb',
    'local_time_information': '00002a0f-0000-1000-8000-00805f9b34fb',
    'secondary_time_zone': '00002a10-0000-1000-8000-00805f9b34fb',
    'time_with_dst': '00002a11-0000-1000-8000-00805f9b34fb',
    'time_accuracy': '00002a12-0000-1000-8000-00805f9b34fb',
    'time_source': '00002a13-0000-1000-8000-00805f9b34fb',
    'reference_time_information': '00002a14-0000-1000-8000-00805f9b34fb',
    'time_broadcast': '00002a15-0000-1000-8000-00805f9b34fb',
    'time_update_control_point': '00002a16-0000-1000-8000-00805f9b34fb',
    'time_update_state': '00002a17-0000-1000-8000-00805f9b34fb',
    'glucose_measurement': '00002a18-0000-1000-8000-00805f9b34fb',
    'battery_level': '00002a19-0000-1000-8000-00805f9b34fb',
    'battery_power_state': '00002a1a-0000-1000-8000-00805f9b34fb',
    'battery_level_state': '00002a1b-0000-1000-8000-00805f9b34fb',
    'temperature_measurement': '00002a1c-0000-1000-8000-00805f9b34fb',
    'temperature_type': '00002a1d-0000-1000-8000-00805f9b34fb',
    'intermediate_temperature': '00002a1e-0000-1000-8000-00805f9b34fb',
    'temperature_celsius': '00002a1f-0000-1000-8000-00805f9b34fb',
    'temperature_fahrenheit': '00002a20-0000-1000-8000-00805f9b34fb',
    'measurement_interval': '00002a21-0000-1000-8000-00805f9b34fb',
    'boot_keyboard_input_report': '00002a22-0000-1000-8000-00805f9b34fb',
    'system_id': '00002a23-0000-1000-8000-00805f9b34fb',
    'model_number_string': '00002a24-0000-1000-8000-00805f9b34fb',
    'serial_number_string': '00002a25-0000-1000-8000-00805f9b34fb',
    'firmware_revision_string': '00002a26-0000-1000-8000-00805f9b34fb',
    'hardware_revision_string': '00002a27-0000-1000-8000-00805f9b34fb',
    'software_revision_string': '00002a28-0000-1000-8000-00805f9b34fb',
    'manufacturer_name_string': '00002a29-0000-1000-8000-00805f9b34fb',
    'ieee_11073-20601_regulatory_certification_data_list': '00002a2a-0000-1000-8000-00805f9b34fb',
    'current_time': '00002a2b-0000-1000-8000-00805f9b34fb',
    'magnetic_declination': '00002a2c-0000-1000-8000-00805f9b34fb',
    'position_2d': '00002a2f-0000-1000-8000-00805f9b34fb',
    'position_3d': '00002a30-0000-1000-8000-00805f9b34fb',
    'scan_refresh': '00002a31-0000-1000-8000-00805f9b34fb',
    'boot_keyboard_output_report': '00002a32-0000-1000-8000-00805f9b34fb',
    'boot_mouse_input_report': '00002a33-0000-1000-8000-00805f9b34fb',
    'glucose_measurement_context': '00002a34-0000-1000-8000-00805f9b34fb',
    'blood_pressure_measurement': '00002a35-0000-1000-8000-00805f9b34fb',
    'intermediate_cuff_pressure': '00002a36-0000-1000-8000-00805f9b34fb',
    'heart_rate_measurement': '00002a37-0000-1000-8000-00805f9b34fb',
    'body_sensor_location': '00002a38-0000-1000-8000-00805f9b34fb',
    'heart_rate_control_point': '00002a39-0000-1000-8000-00805f9b34fb',
    'removable': '00002a3a-0000-1000-8000-00805f9b34fb',
    'service_required': '00002a3b-0000-1000-8000-00805f9b34fb',
    'scientific_temperature_celsius': '00002a3c-0000-1000-8000-00805f9b34fb',
    'string': '00002a3d-0000-1000-8000-00805f9b34fb',
    'network_availability': '00002a3e-0000-1000-8000-00805f9b34fb',
    'alert_status': '00002a3f-0000-1000-8000-00805f9b34fb',
    'ringer_control_point': '00002a40-0000-1000-8000-00805f9b34fb',
    'ringer_setting': '00002a41-0000-1000-8000-00805f9b34fb',
    'alert_category_id_bit_mask': '00002a42-0000-1000-8000-00805f9b34fb',
    'alert_category_id': '00002a43-0000-1000-8000-00805f9b34fb',
    'alert_notification_control_point': '00002a44-0000-1000-8000-00805f9b34fb',
    'unread_alert_status': '00002a45-0000-1000-8000-00805f9b34fb',
    'new_alert': '00002a46-0000-1000-8000-00805f9b34fb',
    'supported_new_alert_category': '00002a47-0000-1000-8000-00805f9b34fb',
    'supported_unread_alert_category': '00002a48-0000-1000-8000-00805f9b34fb',
    'blood_pressure_feature': '00002a49-0000-1000-8000-00805f9b34fb',
    'hid_information': '00002a4a-0000-1000-8000-00805f9b34fb',
    'report_map': '00002a4b-0000-1000-8000-00805f9b34fb',
    'hid_control_point': '00002a4c-0000-1000-8000-00805f9b34fb',
    'report': '00002a4d-0000-1000-8000-00805f9b34fb',
    'protocol_mode': '00002a4e-0000-1000-8000-00805f9b34fb',
    'scan_interval_window': '00002a4f-0000-1000-8000-00805f9b34fb',
    'pnp_id': '00002a50-0000-1000-8000-00805f9b34fb',
    'glucose_feature': '00002a51-0000-1000-8000-00805f9b34fb',
    'record_access_control_point': '00002a52-0000-1000-8000-00805f9b34fb',
    'rsc_measurement': '00002a53-0000-1000-8000-00805f9b34fb',
    'rsc_feature': '00002a54-0000-1000-8000-00805f9b34fb',
    'sc_control_point': '00002a55-0000-1000-8000-00805f9b34fb',
    'digital': '00002a56-0000-1000-8000-00805f9b34fb',
    'digital_output': '00002a57-0000-1000-8000-00805f9b34fb',
    'analog': '00002a58-0000-1000-8000-00805f9b34fb',
    'analog_output': '00002a59-0000-1000-8000-00805f9b34fb',
    'aggregate': '00002a5a-0000-1000-8000-00805f9b34fb',
    'csc_measurement': '00002a5b-0000-1000-8000-00805f9b34fb',
    'csc_feature': '00002a5c-0000-1000-8000-00805f9b34fb',
    'sensor_location': '00002a5d-0000-1000-8000-00805f9b34fb',
    'plx_spot_check_measurement': '00002a5e-0000-1000-8000-00805f9b34fb',
    'plx_continuous_measurement': '00002a5f-0000-1000-8000-00805f9b34fb',
    'plx_features': '00002a60-0000-1000-8000-00805f9b34fb',
    'pulse_oximetry_control_point': '00002a62-0000-1000-8000-00805f9b34fb',
    'cycling_power_measurement': '00002a63-0000-1000-8000-00805f9b34fb',
    'cycling_power_vector': '00002a64-0000-1000-8000-00805f9b34fb',
    'cycling_power_feature': '00002a65-0000-1000-8000-00805f9b34fb',
    'cycling_power_control_point': '00002a66-0000-1000-8000-00805f9b34fb',
    'location_and_speed': '00002a67-0000-1000-8000-00805f9b34fb',
    'navigation': '00002a68-0000-1000-8000-00805f9b34fb',
    'position_quality': '00002a69-0000-1000-8000-00805f9b34fb',
    'ln_feature': '00002a6a-0000-1000-8000-00805f9b34fb',
    'ln_control_point': '00002a6b-0000-1000-8000-00805f9b34fb',
    'elevation': '00002a6c-0000-1000-8000-00805f9b34fb',
    'pressure': '00002a6d-0000-1000-8000-00805f9b34fb',
    'temperature': '00002a6e-0000-1000-8000-00805f9b34fb',
    'humidity': '00002a6f-0000-1000-8000-00805f9b34fb',
    'true_wind_speed': '00002a70-0000-1000-8000-00805f9b34fb',
    'true_wind_direction': '00002a71-0000-1000-8000-00805f9b34fb',
    'apparent_wind_speed': '00002a72-0000-1000-8000-00805f9b34fb',
    'apparent_wind_direction': '00002a73-0000-1000-8000-00805f9b34fb',
    'gust_factor': '00002a74-0000-1000-8000-00805f9b34fb',
    'pollen_concentration': '00002a75-0000-1000-8000-00805f9b34fb',
    'uv_index': '00002a76-0000-1000-8000-00805f9b34fb',
    'irradiance': '00002a77-0000-1000-8000-00805f9b34fb',
    'rainfall': '00002a78-0000-1000-8000-00805f9b34fb',
    'wind_chill': '00002a79-0000-1000-8000-00805f9b34fb',
    'heat_index': '00002a7a-0000-1000-8000-00805f9b34fb',
    'dew_point': '00002a7b-0000-1000-8000-00805f9b34fb',
    'descriptor_value_changed': '00002a7d-0000-1000-8000-00805f9b34fb',
    'aerobic_heart_rate_lower_limit': '00002a7e-0000-1000-8000-00805f9b34fb',
    'aerobic_threshold': '00002a7f-0000-1000-8000-00805f9b34fb',
    'age': '00002a80-0000-1000-8000-00805f9b34fb',
    'anaerobic_heart_rate_lower_limit': '00002a81-0000-1000-8000-00805f9b34fb',
    'anaerobic_heart_rate_upper_limit': '00002a82-0000-1000-8000-00805f9b34fb',
    'anaerobic_threshold': '00002a83-0000-1000-8000-00805f9b34fb',
    'aerobic_heart_rate_upper_limit': '00002a84-0000-1000-8000-00805f9b34fb',
    'date_of_birth': '00002a85-0000-1000-8000-00805f9b34fb',
    'date_of_threshold_assessment': '00002a86-0000-1000-8000-00805f9b34fb',
    'email_address': '00002a87-0000-1000-8000-00805f9b34fb',
    'fat_burn_heart_rate_lower_limit': '00002a88-0000-1000-8000-00805f9b34fb',
    'fat_burn_heart_rate_upper_limit': '00002a89-0000-1000-8000-00805f9b34fb',
    'first_name': '00002a8a-0000-1000-8000-00805f9b34fb',
    'five_zone_heart_rate_limits': '00002a8b-0000-1000-8000-00805f9b34fb',
    'gender': '00002a8c-0000-1000-8000-00805f9b34fb',
    'heart_rate_max': '00002a8d-0000-1000-8000-00805f9b34fb',
    'height': '00002a8e-0000-1000-8000-00805f9b34fb',
    'hip_circumference': '00002a8f-0000-1000-8000-00805f9b34fb',
    'last_name': '00002a90-0000-1000-8000-00805f9b34fb',
    'maximum_recommended_heart_rate': '00002a91-0000-1000-8000-00805f9b34fb',
    'resting_heart_rate': '00002a92-0000-1000-8000-00805f9b34fb',
    'sport_type_for_aerobic_and_anaerobic_thresholds': '00002a93-0000-1000-8000-00805f9b34fb',
    'three_zone_heart_rate_limits': '00002a94-0000-1000-8000-00805f9b34fb',
    'two_zone_heart_rate_limit': '00002a95-0000-1000-8000-00805f9b34fb',
    'vo2_max': '00002a96-0000-1000-8000-00805f9b34fb',
    'waist_circumference': '00002a97-0000-1000-8000-00805f9b34fb',
    'weight': '00002a98-0000-1000-8000-00805f9b34fb',
    'database_change_increment': '00002a99-0000-1000-8000-00805f9b34fb',
    'user_index': '00002a9a-0000-1000-8000-00805f9b34fb',
    'body_composition_feature': '00002a9b-0000-1000-8000-00805f9b34fb',
    'body_composition_measurement': '00002a9c-0000-1000-8000-00805f9b34fb',
    'weight_measurement': '00002a9d-0000-1000-8000-00805f9b34fb',
    'weight_scale_feature': '00002a9e-0000-1000-8000-00805f9b34fb',
    'user_control_point': '00002a9f-0000-1000-8000-00805f9b34fb',
    'magnetic_flux_density_2D': '00002aa0-0000-1000-8000-00805f9b34fb',
    'magnetic_flux_density_3D': '00002aa1-0000-1000-8000-00805f9b34fb',
    'language': '00002aa2-0000-1000-8000-00805f9b34fb',
    'barometric_pressure_trend': '00002aa3-0000-1000-8000-00805f9b34fb',
    'bond_management_control_point': '00002aa4-0000-1000-8000-00805f9b34fb',
    'bond_management_feature': '00002aa5-0000-1000-8000-00805f9b34fb',
    'gap.central_address_resolution_support': '00002aa6-0000-1000-8000-00805f9b34fb',
    'cgm_measurement': '00002aa7-0000-1000-8000-00805f9b34fb',
    'cgm_feature': '00002aa8-0000-1000-8000-00805f9b34fb',
    'cgm_status': '00002aa9-0000-1000-8000-00805f9b34fb',
    'cgm_session_start_time': '00002aaa-0000-1000-8000-00805f9b34fb',
    'cgm_session_run_time': '00002aab-0000-1000-8000-00805f9b34fb',
    'cgm_specific_ops_control_point': '00002aac-0000-1000-8000-00805f9b34fb',
    'indoor_positioning_configuration': '00002aad-0000-1000-8000-00805f9b34fb',
    'latitude': '00002aae-0000-1000-8000-00805f9b34fb',
    'longitude': '00002aaf-0000-1000-8000-00805f9b34fb',
    'local_north_coordinate': '00002ab0-0000-1000-8000-00805f9b34fb',
    'local_east_coordinate.xml': '00002ab1-0000-1000-8000-00805f9b34fb',
    'floor_number': '00002ab2-0000-1000-8000-00805f9b34fb',
    'altitude': '00002ab3-0000-1000-8000-00805f9b34fb',
    'uncertainty': '00002ab4-0000-1000-8000-00805f9b34fb',
    'location_name': '00002ab5-0000-1000-8000-00805f9b34fb',
    'uri': '00002ab6-0000-1000-8000-00805f9b34fb',
    'http_headers': '00002ab7-0000-1000-8000-00805f9b34fb',
    'http_status_code': '00002ab8-0000-1000-8000-00805f9b34fb',
    'http_entity_body': '00002ab9-0000-1000-8000-00805f9b34fb',
    'http_control_point': '00002aba-0000-1000-8000-00805f9b34fb',
    'https_security': '00002abb-0000-1000-8000-00805f9b34fb',
    'tds_control_point': '00002abc-0000-1000-8000-00805f9b34fb',
    'ots_feature': '00002abd-0000-1000-8000-00805f9b34fb',
    'object_name': '00002abe-0000-1000-8000-00805f9b34fb',
    'object_type': '00002abf-0000-1000-8000-00805f9b34fb',
    'object_size': '00002ac0-0000-1000-8000-00805f9b34fb',
    'object_first_created': '00002ac1-0000-1000-8000-00805f9b34fb',
    'object_last_modified': '00002ac2-0000-1000-8000-00805f9b34fb',
    'object_id': '00002ac3-0000-1000-8000-00805f9b34fb',
    'object_properties': '00002ac4-0000-1000-8000-00805f9b34fb',
    'object_action_control_point': '00002ac5-0000-1000-8000-00805f9b34fb',
    'object_list_control_point': '00002ac6-0000-1000-8000-00805f9b34fb',
    'object_list_filter': '00002ac7-0000-1000-8000-00805f9b34fb',
    'object_changed': '00002ac8-0000-1000-8000-00805f9b34fb',
    'resolvable_private_address_only': '00002ac9-0000-1000-8000-00805f9b34fb',
    'fitness_machine_feature': '00002acc-0000-1000-8000-00805f9b34fb',
    'treadmill_data': '00002acd-0000-1000-8000-00805f9b34fb',
    'cross_trainer_data': '00002ace-0000-1000-8000-00805f9b34fb',
    'step_climber_data': '00002acf-0000-1000-8000-00805f9b34fb',
    'stair_climber_data': '00002ad0-0000-1000-8000-00805f9b34fb',
    'rower_data': '00002ad1-0000-1000-8000-00805f9b34fb',
    'indoor_bike_data': '00002ad2-0000-1000-8000-00805f9b34fb',
    'training_status': '00002ad3-0000-1000-8000-00805f9b34fb',
    'supported_speed_range': '00002ad4-0000-1000-8000-00805f9b34fb',
    'supported_inclination_range': '00002ad5-0000-1000-8000-00805f9b34fb',
    'supported_resistance_level_range': '00002ad6-0000-1000-8000-00805f9b34fb',
    'supported_heart_rate_range': '00002ad7-0000-1000-8000-00805f9b34fb',
    'supported_power_range': '00002ad8-0000-1000-8000-00805f9b34fb',
    'fitness_machine_control_point': '00002ad9-0000-1000-8000-00805f9b34fb',
    'fitness_machine_status': '00002ada-0000-1000-8000-00805f9b34fb',
    'date_utc': '00002aed-0000-1000-8000-00805f9b34fb',}

# --- GATT標準記述子名 -> UUID (gatt_assigned_descriptors.txt より) ---
GATT_DESCRIPTOR_NAMES: dict[str, str] = {
    'gatt.characteristic_extended_properties': '00002900-0000-1000-8000-00805f9b34fb',
    'gatt.characteristic_user_description': '00002901-0000-1000-8000-00805f9b34fb',
    'gatt.client_characteristic_configuration': '00002902-0000-1000-8000-00805f9b34fb',
    'gatt.server_characteristic_configuration': '00002903-0000-1000-8000-00805f9b34fb',
    'gatt.characteristic_presentation_format': '00002904-0000-1000-8000-00805f9b34fb',
    'gatt.characteristic_aggregate_format': '00002905-0000-1000-8000-00805f9b34fb',
    'valid_range': '00002906-0000-1000-8000-00805f9b34fb',
    'external_report_reference': '00002907-0000-1000-8000-00805f9b34fb',
    'report_reference': '00002908-0000-1000-8000-00805f9b34fb',
    'number_of_digitals': '00002909-0000-1000-8000-00805f9b34fb',
    'value_trigger_setting': '0000290a-0000-1000-8000-00805f9b34fb',
    'es_configuration': '0000290b-0000-1000-8000-00805f9b34fb',
    'es_measurement': '0000290c-0000-1000-8000-00805f9b34fb',
    'es_trigger_setting': '0000290d-0000-1000-8000-00805f9b34fb',
    'time_trigger_setting': '0000290e-0000-1000-8000-00805f9b34fb',
}


# 表示用の逆引き(UUID -> 代表的な名前)。チューザーダイアログやF12デバッグ表示で
# 「このUUIDは何か」を人間可読に示すために使う。複数の名前が同じUUIDに
# 割り当たることは無い(各レジストリ内で名前もUUIDも一意)ので単純な逆引きでよい。
_ALL_KNOWN_NAMES: dict[str, str] = {
    **GATT_SERVICE_NAMES,
    **GATT_CHARACTERISTIC_NAMES,
    **GATT_DESCRIPTOR_NAMES,
}
UUID_TO_NAME: dict[str, str] = {uuid: name for name, uuid in _ALL_KNOWN_NAMES.items()}


def resolve_uuid(value: Union[int, str], registry: dict[str, str]) -> str:
    """BluetoothUUID.getService() / getCharacteristic() / getDescriptor() 相当。

    `value` が指定されたregistry(GATT_SERVICE_NAMES等)に含まれる既知の
    名前(例: 'battery_service')であればそのUUIDを返す。名前として見つからない
    場合は、UUIDのショートフォーム/フルフォームとしてcanonical_uuid()に
    委譲する。

    どちらにも該当しない場合は ValueError を送出する。"""
    if isinstance(value, str):
        key = value.strip().lower()
        if key in registry:
            return registry[key]
    return canonical_uuid(value)


def friendly_name(uuid: str) -> Optional[str]:
    """既知のUUIDに対応する人間可読な名前を返す(無ければNone)。"""
    return UUID_TO_NAME.get(uuid.strip().lower())
