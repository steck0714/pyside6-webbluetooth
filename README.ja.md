# pyside6-webbluetooth

🇯🇵 日本語 | 🇺🇸 [English](README.en.md) | 🇨🇳 [简体中文](README.zh.md)

⚠️ **v0.0.0a1 (Pre-Alpha / Experimental)**

**PySide6 / QtWebEngine** アプリケーションに **Web Bluetooth API (`navigator.bluetooth`)** を追加するライブラリです。実際のBLE通信は [bleak](https://github.com/hbldh/bleak) が担当し、本パッケージはブラウザ側のAPIサーフェス、権限モデル、GATTアクセス制御、セキュリティ境界を実装します。

[steck0714/Mock-APIs](https://github.com/steck0714/Mock-APIs) の「実在するAPI・標準仕様を調査・検証し、互換性を維持しながら独立実装と拡張を行う」というコンセプトの下で開発されています。[Mock-webusb](https://github.com/steck0714/Mock-webusb) / [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb) の姉妹プロジェクトであり、**mock-webbluetooth** フレームワークにおける最初の実装です。

> ⚠️ **Pre-Alpha版です。** AIの支援を受けて開発されています。本番環境のセキュリティ境界やハードウェア制御を目的として利用する前に、必ずソースコードと対象環境での挙動を確認してください。

## Features

- Web Bluetooth互換の `navigator.bluetooth`
- 実BLEデバイスとの通信
- ネイティブデバイス選択ダイアログ
- ライブ更新されるスキャン結果
- オリジンごとの永続的なデバイス権限
- フレーム単位のオリジン検証
- GATT Service / Characteristic / Descriptorへのアクセス
- GATTブロックリスト
- `requestDevice()` の主要なfilter処理
- `BluetoothUUID` 相当のUUID名解決
- Qt UIスレッドをブロックしない非同期BLE Worker
- QtWebEngine / QWebChannelブリッジ
- `bleak` を使用する標準バックエンド
- 実験的な `PySide6.QtBluetooth` (`QLowEnergyController`) バックエンド

## Installation

```bash
pip install pyside6-webbluetooth
```

Requirements:

- `Python>=3.10`
- `PySide6>=6.6`
- `bleak>=0.21`

Python 3.14.7 + PySide6 6.11.2 + bleak 3.0.2 の環境でも構築・検証され、テストスイート **117件すべてが成功**しています。

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

ページ側のJavaScriptからは、Web Bluetooth対応ブラウザと同じ一般的な書き方で利用できます。

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

## Backend

既定では `bleak` を使用します。

```python
bridge = install(view.page())
```

実験的にPySide6同梱の `PySide6.QtBluetooth` / `QLowEnergyController` を使用することもできます。

```python
bridge = install(view.page(), backend="qtbluetooth")
```

QtBluetoothバックエンドでは、接続・GATT探索・read/write/notifyの配線をモックで検証していますが、**実BLEハードウェアでの検証はまだ行われていません**。対象プラットフォームでの実機確認を推奨します。

## Architecture

```text
Web page
    │
    │ navigator.bluetooth
    ▼
JavaScript Web Bluetooth Polyfill
    │
    │ QWebChannel
    ▼
BluetoothBridge
    │
    ├── API / argument validation
    ├── Origin / frame verification
    ├── Permission management
    ├── Device chooser
    └── GATT blocklist
    │
    ▼
BLE Worker / asyncio
    │
    ├── bleak backend
    │      or
    └── QtBluetooth backend
    │
    ▼
BLE device
```

## Why the design differs from pyside6-webusb

[Mock-APIs](https://github.com/steck0714/Mock-APIs) では、単純な移植ではなく対象環境の制約に合わせた独立実装を重視しています。

`pyside6-webusb` ではUSB操作を同期的に扱えますが、BLEではスキャン・接続・GATT探索などが数百ms〜数秒かかる場合があります。また `bleak` はasyncioベースです。

そのため本パッケージでは、

- **軽量操作** (`getDevices()`、`forget()`など): 同期処理
- **`requestDevice()`**: モーダルなネイティブチューザーを表示しながら、専用asyncioスレッドでライブスキャン
- **BLE操作** (`connect()`、GATT探索、read/write/notify): 同期検証後に `requestId` を返し、完了結果をQt Signalで非同期配送

という構成を採用しています。

これにより、BLE無線処理の待ち時間でQtWebEngineのUIスレッドを長時間ブロックすることを避けます。

## Security Design

### Per-origin permissions

`requestDevice()` で選択したデバイスと、そのオリジンに許可されたサービスUUIDを `QSettings` に保存します。

許可されていないサービスへのアクセスは `SecurityError` として拒否されます。

### Per-frame origin verification

QWebChannelで公開されたブリッジはページ内のiframeからも見えるため、JavaScriptが自己申告するorigin文字列をそのまま信用しません。

各フレームに推測困難なトークンを配布し、Python側で **token → 実際のorigin** の対応を追跡します。iframeを含む実際の `QWebEnginePage` を使ったテストでも検証されています。

### GATT blocklist

[WebBluetoothCG/registries](https://github.com/WebBluetoothCG/registries/blob/master/gatt_blocklist.txt) のGATT blocklistを実装しています。

対象には、HID、ファームウェア更新系、FIDO関連などの保護されたGATTリソースや、特定のプライバシー関連・設定用characteristic/descriptorが含まれます。

## Chrome Compatibility

Chrome / ChromiumのWeb Bluetooth挙動とWeb Bluetooth仕様を互換性の基準として参照していますが、**Chrome内部実装の直接移植ではありません**。

> **Web Bluetooth compatibility ≠ Chrome clone**

APIの使い方は可能な限りWeb Bluetooth互換を目指しつつ、内部実装はPySide6 / QtWebEngine / Python / BLEバックエンドに合わせて独立して構築しています。

## Development & Testing

```bash
pip install -e ".[dev]"
pytest
```

PySide6 / QtWebEngineの実環境、オフスクリーン `QWebEnginePage`、iframe、JavaScript実行、`bleak.BleakClient` / `QLowEnergyController` のモックを組み合わせてテストしています。

v0.0.0a1では **117 tests passed** が確認されています。

実BLEアダプタがないCI・コンテナ環境では、`tests/conftest.py` が `QT_QPA_PLATFORM=offscreen` へフォールバックできます。

## Known Limitations

v0.0.0a1時点の主な制限:

- `getIncludedService()` / `getIncludedServices()` は `NotSupportedError`
- `watchAdvertisements()` / `unwatchAdvertisements()` は未実装
- Manufacturer Dataの補助的なblocklistは未実装
- notify/indicateの配送は `(device, service)` 内のcharacteristic UUIDで対応付けており、GATT handleそのものではありません
- Rust/C++によるネイティブ高速化は未対応
- `backend="qtbluetooth"` は実BLEハードウェアで未検証

また、Web Bluetoothはブラウザ・OS・BLEスタックによる差異が大きいため、実際にハードウェアを制御する用途では対象OS・BLEアダプタでの検証を推奨します。

詳細な変更点、バグ修正、仕様再確認、Python 3.14 / PySide6 6.11での検証結果は [`CHANGELOG.md`](CHANGELOG.md) を参照してください。

## Related Projects

- [Mock-APIs](https://github.com/steck0714/Mock-APIs)
- [Mock-webusb](https://github.com/steck0714/Mock-webusb)
- [Pyside6-webusb](https://github.com/steck0714/Pyside6-webusb)
- [Mock-webbluetooth](https://github.com/steck0714/Mock-webbluetooth)

## License

MIT License. [`LICENSE`](LICENSE) を参照してください。
