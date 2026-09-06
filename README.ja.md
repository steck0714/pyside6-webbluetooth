# pyside6-webbluetooth

🇯🇵 [日本語](README.ja.md) | 🇺🇸 [English](README.en.md) | 🇨🇳 [简体中文](README.zh.md)

⚠️ **v0.0.0 (初版 / Pre-Alpha)**

PySide6 / QtWebEngineベースのアプリに **Web Bluetooth API** (`navigator.bluetooth`) を追加するライブラリです。実際のBLE通信は [bleak](https://github.com/hbldh/bleak) が担当し、本パッケージはブラウザ側のAPI仕様、権限モデル、セキュリティ境界を実装します。

[steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) の「既存の実APIや標準仕様を調査・検証し、互換性を維持しながら独自の実装・拡張を行う」というコンセプトのもと、[Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) の姉妹プロジェクトとして、**mock-webbluetooth** 枠組みの最初の実装として作られています。

> ⚠️ **初版です。** AIの支援を受けて開発されています。本番のセキュリティ境界やハードウェア制御用途として利用する前に、必ずコードを確認してください。

## 主な機能

- Web Bluetooth API互換の `navigator.bluetooth`
- BLEデバイスの実機通信
- ネイティブデバイス選択ダイアログ
- ライブスキャンによるデバイス一覧更新
- Originごとの永続的なデバイス権限
- フレーム単位のOrigin検証
- GATTサービス / Characteristic / Descriptorへのアクセス
- GATTブロックリスト
- `requestDevice()` の主要なフィルタ処理
- `BluetoothUUID` 相当のUUID名前解決
- Qt UIをブロックしない非同期BLEワーカー
- QtWebEngine / QWebChannelブリッジ

## インストール

```bash
pip install pyside6-webbluetooth
```

依存関係:

- `PySide6>=6.6`
- `bleak>=0.21`

## Quick Start

```python
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from pyside6_webbluetooth import install

app = QApplication([])
view = QWebEngineView()

bridge = install(view.page())

view.load("https://googlechrome.github.io/samples/web-bluetooth/")
view.show()
app.exec()

bridge.shutdown()
```

ページ側では、Web Bluetooth対応ブラウザと同様に `navigator.bluetooth` を利用できます。

```javascript
const device = await navigator.bluetooth.requestDevice({
  filters: [{ services: ['battery_service'] }],
});

const server = await device.gatt.connect();
const service = await server.getPrimaryService('battery_service');
const characteristic = await service.getCharacteristic('battery_level');
const value = await characteristic.readValue();

console.log('battery:', value.getUint8(0), '%');
```

## アーキテクチャ

```text
Webページ
    │
    │ navigator.bluetooth
    ▼
JavaScript Web Bluetooth Polyfill
    │
    │ QWebChannel
    ▼
BluetoothBridge
    │
    ├── Origin / frame検証
    ├── 権限管理
    ├── デバイス選択
    ├── GATTブロックリスト
    └── API / 引数検証
    │
    ▼
BLE Worker / asyncio
    │
    │ bleak
    ▼
BLEデバイス
```

## なぜ pyside6-webusb と設計が違うのか

[pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) と同じくWeb API互換を目指していますが、Bluetooth LEの性質上、内部設計は同一ではありません。

USBの多くの操作は比較的短時間で完了します。一方、BLEではスキャン、接続、GATT探索などが数百ms〜数秒かかる場合があります。また、`bleak` は `asyncio` ベースです。

そのため本プロジェクトでは:

- **軽い操作** (`getDevices()`、`forget()`など): 同期処理
- **デバイス選択** (`requestDevice()`): モーダルダイアログを維持しつつ、専用asyncioスレッドでライブスキャン
- **BLE操作** (`connect()`、GATT探索、read/write/notify): 検証後すぐ `requestId` を返し、完了結果をQt Signal経由で非同期配送

という構成を採用しています。

これにより、BLE無線操作の待ち時間でQtWebEngineのUIスレッドを長時間ブロックしない設計になっています。

## セキュリティ設計

### Originごとの権限

`requestDevice()` で選択されたデバイスと許可されたサービスUUIDをOrigin単位で管理し、`QSettings` に永続化します。

許可されていないサービスへのアクセスは `SecurityError` として拒否されます。

### フレーム単位のOrigin検証

QWebChannelのブリッジはページ内の複数フレームから参照できるため、JavaScriptが自己申告するOriginをそのまま信用しません。

各フレームへ個別の推測困難なトークンを配布し、Python側でトークンと実際のOriginの対応を追跡します。

iframeを含む実際の `QWebEnginePage` を使ったテストでも、この境界を検証しています。

### GATTブロックリスト

WebBluetoothCGのGATT blocklistを実装し、保護対象のサービス・Characteristic・Descriptorへのアクセスを拒否します。

HID、ファームウェア更新系、FIDO関連などの保護対象を含みます。

## Chromeとの互換性

Chrome / ChromiumのWeb Bluetooth挙動を互換性の参考にしていますが、このプロジェクトは **Chromeの内部実装をそのまま移植したものではありません**。

> **Web Bluetooth互換 ≠ Chromeクローン**

APIの互換性を重視しつつ、PySide6 / QtWebEngine / Python / bleakという実行環境に合わせた独立実装になっています。

## 開発・テスト

```bash
pip install -e ".[dev]"
pytest
```

テストでは、実際のPySide6 / QtWebEngine環境、オフスクリーンの `QWebEnginePage`、iframe、JavaScript実行、そしてモックした `bleak.BleakClient` を組み合わせて検証しています。

ヘッドレス環境では `tests/conftest.py` が必要に応じて `QT_QPA_PLATFORM=offscreen` にフォールバックします。

## 既知の制限

v0.0.0では以下が未実装・制限事項です。

- `getIncludedService()` / `getIncludedServices()` は `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` は未実装
- Manufacturer Dataの補助的なブロックリストは未実装
- 通知配送はGATT handleではなく、(device, service)内のCharacteristic UUIDで照合
- pyside6-webusbにあるRust/C++ネイティブ高速化は未対応

詳細は [`CHANGELOG.md`](CHANGELOG.md) を参照してください。

## 関連プロジェクト

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License. [`LICENSE`](LICENSE) を参照してください。
