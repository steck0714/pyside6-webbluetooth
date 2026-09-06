# -*- coding: utf-8 -*-
"""navigator.bluetooth のJSポリフィル本体と、ページへの導入(install)。

姉妹プロジェクトpyside6-webusbのpolyfill.pyと同じ役割: QWebChannel経由で
bridge.py(BluetoothBridge)と通信するJavaScriptコードを組み立て、
`QWebEngineScript`としてページの全フレームに(ドキュメント生成時点で)
注入する。使う側のアプリケーションは

    from pyside6_webbluetooth import install
    bridge = install(page)

の1行を呼ぶだけで、そのpageで読み込まれるすべてのページに
`navigator.bluetooth` が生えるようになる。

## qwebchannel.jsの扱い

QWebChannelのJS側クライアントライブラリ(qwebchannel.js)は自前で
同梱・転記していない。`PySide6.QtWebChannel` をimportすると
Qtのリソースシステムに `:/qtwebchannel/qwebchannel.js` として登録される
ことを実機で確認済みであり、実行時にそこから読み出して注入スクリプトの
先頭に連結する。これにより、Qtのバージョンが上がってqwebchannel.js自体が
更新された場合でも自動的に追従する。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QFile, QIODevice


def _read_qwebchannel_js() -> str:
    # このimport自体が、qwebchannel.jsをQtのリソースシステムに登録する
    # 副作用を持つ(実機で確認済み: import前は:/qtwebchannel/以下が
    # 空に見える)。
    import PySide6.QtWebChannel  # noqa: F401

    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.ReadOnly | QIODevice.Text):
        raise RuntimeError(
            "failed to read qwebchannel.js from Qt resources "
            "(:/qtwebchannel/qwebchannel.js) -- is PySide6.QtWebChannel available?"
        )
    try:
        return bytes(f.readAll()).decode("utf-8")
    finally:
        f.close()



_POLYFILL_JS_TEMPLATE = r'''
(function () {
  'use strict';

  try {
    if (typeof qt === 'undefined' || !qt.webChannelTransport) {
      console.warn('[pyside6-webbluetooth] qt.webChannelTransport is not available; navigator.bluetooth will not be installed on this page.');
      return;
    }

    var TOKEN_PROPERTY = '__pyside6WebBluetoothFrameToken';

    function frameToken() {
      return window[TOKEN_PROPERTY];
    }

    // --- base64 <-> byte helpers -------------------------------------------------

    function base64ToUint8Array(b64) {
      var binary = atob(b64);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return bytes;
    }

    function bufferSourceToBase64(bufferSource) {
      var bytes;
      if (bufferSource instanceof ArrayBuffer) {
        bytes = new Uint8Array(bufferSource);
      } else if (ArrayBuffer.isView(bufferSource)) {
        bytes = new Uint8Array(bufferSource.buffer, bufferSource.byteOffset, bufferSource.byteLength);
      } else {
        throw new TypeError('value must be a BufferSource (ArrayBuffer or a typed array/DataView)');
      }
      var binary = '';
      for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      return btoa(binary);
    }

    function toDataViewCopy(bufferSource) {
      var bytes;
      if (bufferSource instanceof ArrayBuffer) {
        bytes = new Uint8Array(bufferSource);
      } else {
        bytes = new Uint8Array(bufferSource.buffer, bufferSource.byteOffset, bufferSource.byteLength);
      }
      var copy = new Uint8Array(bytes.length);
      copy.set(bytes);
      return new DataView(copy.buffer);
    }

    // --- event-handler-IDL-attribute helper (on<x> single-slot property) --------
    //
    // EventTarget自体は"on<x>"形式のプロパティを自動では持たない
    // (addEventListener/removeEventListener/dispatchEventのみ)。
    // Web IDLの「イベントハンドラ属性」(ongattserverdisconnected等)を
    // 単一スロットのプロパティとして再現する。

    function defineOnEventProperty(target, eventName) {
      var current = null;
      Object.defineProperty(target, 'on' + eventName, {
        configurable: true,
        enumerable: true,
        get: function () {
          return current;
        },
        set: function (fn) {
          if (current) target.removeEventListener(eventName, current);
          current = typeof fn === 'function' ? fn : null;
          if (current) target.addEventListener(eventName, current);
        },
      });
    }

    // --- QWebChannel bridge plumbing ---------------------------------------------

    var _bridge = null;
    var _bridgeReady = new Promise(function (resolveReady) {
      new QWebChannel(qt.webChannelTransport, function (channel) {
        _bridge = channel.objects.__OBJECT_NAME__;
        if (!_bridge) {
          console.error('[pyside6-webbluetooth] bridge object "__OBJECT_NAME__" was not found on the QWebChannel.');
          resolveReady();
          return;
        }
        _bridge.bleOperationResult.connect(onBleOperationResult);
        _bridge.characteristicValueChanged.connect(onCharacteristicValueChanged);
        _bridge.gattServerDisconnected.connect(onGattServerDisconnected);
        resolveReady();
      });
    });

    var _pendingRequests = new Map(); // requestId -> {resolve, reject}
    var _orphanedResults = new Map(); // requestId -> payload (下記の競合対策)
    var _deviceRegistry = new Map(); // deviceId -> BluetoothDevice
    var _notifyTargets = new Map(); // "deviceId|serviceUuid" -> [BluetoothRemoteGATTCharacteristic,...]

    function domException(errorObj) {
      return new DOMException(errorObj.message, errorObj.name);
    }

    function rememberOrphanedResult(requestId, payload) {
      // 万一どのcallBridgeAsync()にも回収されないrequestIdが混じっても
      // 無限に溜め続けないよう、上限を設けて古いものから捨てる。
      if (_orphanedResults.size > 256) {
        var oldestKey = _orphanedResults.keys().next().value;
        _orphanedResults.delete(oldestKey);
      }
      _orphanedResults.set(requestId, payload);
    }

    function onBleOperationResult(payloadJson) {
      var payload = JSON.parse(payloadJson);
      var pending = _pendingRequests.get(payload.requestId);
      if (!pending) {
        // 実機テストで判明した競合: bleak側の処理(モック等では特に)が
        // 極めて高速に完了すると、Python側がbleOperationResultをemitする
        // タイミングが、JS側がcallBridge()の同期応答(requestId)を受け取って
        // _pendingRequestsに登録するより先になり得る。その場合はここに
        // まだ何も無いので、後から回収できるよう一時的に保存しておく。
        rememberOrphanedResult(payload.requestId, payload);
        return;
      }
      _pendingRequests.delete(payload.requestId);
      if (payload.ok) {
        pending.resolve(payload.result);
      } else {
        pending.reject(domException(payload.error));
      }
    }

    function onCharacteristicValueChanged(payloadJson) {
      var payload = JSON.parse(payloadJson);
      var list = _notifyTargets.get(payload.deviceId + '|' + payload.serviceUuid) || [];
      var bytes = base64ToUint8Array(payload.value);
      list.forEach(function (ch) {
        // 同一デバイス・サービス内でnotify対象特性のUUIDが重複することは
        // 稀だが、handleではなくUUIDで突き合わせている(v0.0.0の既知の
        // 簡略化)。該当するUUIDを持つものすべてに配送する。
        if (ch.uuid !== payload.characteristicUuid) return;
        ch.value = new DataView(bytes.buffer.slice(0));
        ch.dispatchEvent(new Event('characteristicvaluechanged'));
      });
    }

    function onGattServerDisconnected(payloadJson) {
      var payload = JSON.parse(payloadJson);
      var device = _deviceRegistry.get(payload.deviceId);
      if (!device) return;
      device.gatt.connected = false;
      device.dispatchEvent(new Event('gattserverdisconnected'));
    }

    function callBridge(methodName, args) {
      return _bridgeReady.then(function () {
        return new Promise(function (resolve, reject) {
          if (!_bridge) {
            reject(new DOMException('Bluetooth bridge is not available on this page.', 'NotSupportedError'));
            return;
          }
          var method = _bridge[methodName];
          if (typeof method !== 'function') {
            reject(new DOMException('Unknown bridge method: ' + methodName, 'NotSupportedError'));
            return;
          }
          method.apply(_bridge, args.concat([function (resultJson) {
            var result = JSON.parse(resultJson);
            if (result.ok) resolve(result.result);
            else reject(domException(result.error));
          }]));
        });
      });
    }

    // BLE無線を伴う操作: 同期呼び出しでrequestIdを受け取り、
    // bleOperationResultシグナルが届くまで待つPromiseを返す。
    //
    // 実機テストで踏んだ競合への対策: 素朴には「callBridgeの結果
    // (requestId)を受け取った直後に_pendingRequestsへ登録すれば、
    // シグナルが同期呼び出しの往復より先に届くことは無い」と考えたくなるが、
    // これは誤りだった。bleak側の処理が(モックなどで)ほぼ即座に完了する
    // 場合、Python側はrequestIdを含む同期応答を返した直後に非同期処理も
    // 完了させてbleOperationResultをemitできてしまう。QWebChannelの
    // 往復には(同期応答であっても)メッセージング層を経由する遅延が
    // あるため、「Pythonがシグナルをemitする」方が「JSがcallBridgeの
    // 同期応答を受け取ってthen()を実行する」より早く起こり得る。
    // そのため、まず_orphanedResultsに結果がすでに届いていないかを
    // 確認し、届いていればその場で解決/棄却する。
    function callBridgeAsync(methodName, args) {
      return callBridge(methodName, args).then(function (syncResult) {
        var requestId = syncResult.requestId;
        var already = _orphanedResults.get(requestId);
        if (already) {
          _orphanedResults.delete(requestId);
          if (already.ok) return already.result;
          throw domException(already.error);
        }
        return new Promise(function (resolve, reject) {
          _pendingRequests.set(requestId, { resolve: resolve, reject: reject });
        });
      });
    }

    // --- BluetoothRemoteGATTDescriptor -------------------------------------------
    // (仕様上EventTargetを継承しない)

    function BluetoothRemoteGATTDescriptor(characteristic, uuid, handle) {
      this.characteristic = characteristic;
      this.uuid = uuid;
      this.value = null;
      this._handle = handle;
    }

    BluetoothRemoteGATTDescriptor.prototype.readValue = function () {
      var self = this;
      var ch = this.characteristic;
      return callBridgeAsync('readDescriptorValue', [
        ch.service.device.id,
        ch.service.uuid,
        JSON.stringify(ch._handle),
        JSON.stringify(this._handle),
        frameToken(),
      ]).then(function (result) {
        var bytes = base64ToUint8Array(result.value);
        self.value = new DataView(bytes.buffer);
        return self.value;
      });
    };

    BluetoothRemoteGATTDescriptor.prototype.writeValue = function (value) {
      var self = this;
      var ch = this.characteristic;
      var b64 = bufferSourceToBase64(value);
      return callBridgeAsync('writeDescriptorValue', [
        ch.service.device.id,
        ch.service.uuid,
        JSON.stringify(ch._handle),
        JSON.stringify(this._handle),
        b64,
        frameToken(),
      ]).then(function () {
        self.value = toDataViewCopy(value);
        return undefined;
      });
    };

    // --- BluetoothRemoteGATTCharacteristic ---------------------------------------
    // (仕様上EventTargetを継承する。ネイティブのEventTargetは
    // `EventTarget.call(this)`のような旧式の疑似継承を受け付けず
    // "Please use the 'new' operator" 例外になるため、ES6の
    // `class ... extends EventTarget`を使う -- 実機で確認済みの制約。)

    class BluetoothRemoteGATTCharacteristic extends EventTarget {
      constructor(service, uuid, handle, properties) {
        super();
        this.service = service;
        this.uuid = uuid;
        this.properties = Object.freeze(properties);
        this.value = null;
        this._handle = handle;
        defineOnEventProperty(this, 'characteristicvaluechanged');
      }

      _fetchDescriptors(descriptorFilter) {
        var self = this;
        return callBridgeAsync('getDescriptors', [
          this.service.device.id,
          this.service.uuid,
          JSON.stringify(this._handle),
          JSON.stringify(descriptorFilter === undefined ? null : descriptorFilter),
          frameToken(),
        ]).then(function (list) {
          return list.map(function (d) {
            return new BluetoothRemoteGATTDescriptor(self, d.uuid, d.handle);
          });
        });
      }

      getDescriptor(descriptor) {
        if (descriptor === undefined) {
          return Promise.reject(new TypeError('getDescriptor() requires a descriptor UUID or name'));
        }
        return this._fetchDescriptors(descriptor).then(function (list) {
          return list[0];
        });
      }

      getDescriptors(descriptor) {
        return this._fetchDescriptors(descriptor);
      }

      readValue() {
        var self = this;
        return callBridgeAsync('readCharacteristicValue', [
          this.service.device.id,
          this.service.uuid,
          JSON.stringify(this._handle),
          frameToken(),
        ]).then(function (result) {
          var bytes = base64ToUint8Array(result.value);
          self.value = new DataView(bytes.buffer);
          return self.value;
        });
      }

      _writeValue(value, withResponse) {
        var self = this;
        var b64 = bufferSourceToBase64(value);
        return callBridgeAsync('writeCharacteristicValue', [
          this.service.device.id,
          this.service.uuid,
          JSON.stringify(this._handle),
          b64,
          !!withResponse,
          frameToken(),
        ]).then(function () {
          self.value = toDataViewCopy(value);
          return undefined;
        });
      }

      writeValueWithResponse(value) {
        return this._writeValue(value, true);
      }

      writeValueWithoutResponse(value) {
        return this._writeValue(value, false);
      }

      writeValue(value) {
        // レガシーAPI。'write'があればwith-response、
        // 無ければ'write-without-response'を試す。
        if (this.properties.write) return this.writeValueWithResponse(value);
        if (this.properties.writeWithoutResponse) return this.writeValueWithoutResponse(value);
        return Promise.reject(new DOMException('Characteristic does not support writes.', 'NotSupportedError'));
      }

      startNotifications() {
        var self = this;
        return callBridgeAsync('startNotifications', [
          this.service.device.id,
          this.service.uuid,
          JSON.stringify(this._handle),
          frameToken(),
        ]).then(function () {
          var key = self.service.device.id + '|' + self.service.uuid;
          var list = _notifyTargets.get(key);
          if (!list) {
            list = [];
            _notifyTargets.set(key, list);
          }
          if (list.indexOf(self) === -1) list.push(self);
          return self;
        });
      }

      stopNotifications() {
        var self = this;
        return callBridgeAsync('stopNotifications', [
          this.service.device.id,
          this.service.uuid,
          JSON.stringify(this._handle),
          frameToken(),
        ]).then(function () {
          var key = self.service.device.id + '|' + self.service.uuid;
          var list = _notifyTargets.get(key);
          if (list) {
            var idx = list.indexOf(self);
            if (idx !== -1) list.splice(idx, 1);
          }
          return self;
        });
      }
    }

    // --- BluetoothRemoteGATTService ----------------------------------------------

    class BluetoothRemoteGATTService extends EventTarget {
      constructor(device, uuid, isPrimary) {
        super();
        this.device = device;
        this.uuid = uuid;
        this.isPrimary = !!isPrimary;
        defineOnEventProperty(this, 'characteristicvaluechanged');
        defineOnEventProperty(this, 'serviceadded');
        defineOnEventProperty(this, 'servicechanged');
        defineOnEventProperty(this, 'serviceremoved');
      }

      _fetchCharacteristics(characteristicFilter) {
        var self = this;
        return callBridgeAsync('getCharacteristics', [
          this.device.id,
          this.uuid,
          JSON.stringify(characteristicFilter === undefined ? null : characteristicFilter),
          frameToken(),
        ]).then(function (list) {
          return list.map(function (c) {
            return new BluetoothRemoteGATTCharacteristic(self, c.uuid, c.handle, c.properties);
          });
        });
      }

      getCharacteristic(characteristic) {
        if (characteristic === undefined) {
          return Promise.reject(new TypeError('getCharacteristic() requires a characteristic UUID or name'));
        }
        return this._fetchCharacteristics(characteristic).then(function (list) {
          return list[0];
        });
      }

      getCharacteristics(characteristic) {
        return this._fetchCharacteristics(characteristic);
      }

      getIncludedService() {
        // v0.0.0の既知の制限: includedサービス(セカンダリサービス)は
        // 未対応。「動くふり」をさせず明示的にNotSupportedErrorとする。
        return Promise.reject(
          new DOMException('getIncludedService() is not supported by pyside6-webbluetooth v0.0.0', 'NotSupportedError')
        );
      }

      getIncludedServices() {
        return Promise.reject(
          new DOMException('getIncludedServices() is not supported by pyside6-webbluetooth v0.0.0', 'NotSupportedError')
        );
      }
    }

    // --- BluetoothRemoteGATTServer -----------------------------------------------
    // (仕様上EventTargetを継承しない)

    function BluetoothRemoteGATTServer(device) {
      this.device = device;
      this.connected = false;
    }

    BluetoothRemoteGATTServer.prototype.connect = function () {
      var self = this;
      return callBridgeAsync('connectGatt', [this.device.id, frameToken()]).then(function () {
        self.connected = true;
        return self;
      });
    };

    BluetoothRemoteGATTServer.prototype.disconnect = function () {
      // 仕様上Promiseを返さない(undefined)。切断完了を待たずに返す。
      callBridge('disconnectGatt', [this.device.id, frameToken()]).catch(function (e) {
        console.warn('[pyside6-webbluetooth] disconnect() failed:', e);
      });
      this.connected = false;
    };

    BluetoothRemoteGATTServer.prototype._fetchServices = function (serviceFilter) {
      var device = this.device;
      return callBridgeAsync('getPrimaryServices', [
        device.id,
        JSON.stringify(serviceFilter === undefined ? null : serviceFilter),
        frameToken(),
      ]).then(function (list) {
        return list.map(function (s) {
          return new BluetoothRemoteGATTService(device, s.uuid, s.isPrimary);
        });
      });
    };

    BluetoothRemoteGATTServer.prototype.getPrimaryService = function (service) {
      if (service === undefined) {
        return Promise.reject(new TypeError('getPrimaryService() requires a service UUID or name'));
      }
      return this._fetchServices(service).then(function (list) {
        return list[0];
      });
    };

    BluetoothRemoteGATTServer.prototype.getPrimaryServices = function (service) {
      return this._fetchServices(service);
    };

    // --- BluetoothDevice ----------------------------------------------------------

    class BluetoothDevice extends EventTarget {
      constructor(id, name) {
        super();
        this.id = id;
        this.name = name || null;
        this.gatt = new BluetoothRemoteGATTServer(this);
        this.watchingAdvertisements = false;
        defineOnEventProperty(this, 'gattserverdisconnected');
        defineOnEventProperty(this, 'advertisementreceived');
        defineOnEventProperty(this, 'characteristicvaluechanged');
        defineOnEventProperty(this, 'serviceadded');
        defineOnEventProperty(this, 'servicechanged');
        defineOnEventProperty(this, 'serviceremoved');
      }

      watchAdvertisements() {
        // v0.0.0の既知の制限: 広告の継続監視は未対応
        // (requestDevice()時点の1回限りのスキャンのみ)。
        return Promise.reject(
          new DOMException('watchAdvertisements() is not supported by pyside6-webbluetooth v0.0.0', 'NotSupportedError')
        );
      }

      unwatchAdvertisements() {
        this.watchingAdvertisements = false;
      }

      forget() {
        var self = this;
        return callBridge('forgetDevice', [this.id, frameToken()]).then(function () {
          _deviceRegistry.delete(self.id);
          return undefined;
        });
      }
    }

    function getOrCreateDevice(id, name) {
      var existing = _deviceRegistry.get(id);
      if (existing) {
        if (name) existing.name = name;
        return existing;
      }
      var device = new BluetoothDevice(id, name);
      _deviceRegistry.set(id, device);
      return device;
    }

    // --- Bluetooth (navigator.bluetooth) ------------------------------------------

    class Bluetooth extends EventTarget {
      constructor() {
        super();
        defineOnEventProperty(this, 'availabilitychanged');
      }

      getAvailability() {
        return callBridgeAsync('getAvailability', [frameToken()]);
      }

      getDevices() {
        return callBridge('getDevices', [frameToken()]).then(function (list) {
          return list.map(function (d) {
            return getOrCreateDevice(d.id, d.name);
          });
        });
      }

      requestDevice(options) {
        options = options || {};
        return callBridge('requestDeviceChooser', [JSON.stringify(options), frameToken()]).then(function (result) {
          return getOrCreateDevice(result.id, result.name);
        });
      }
    }

    Object.defineProperty(navigator, 'bluetooth', {
      value: new Bluetooth(),
      writable: false,
      configurable: true,
      enumerable: true,
    });
  } catch (installError) {
    console.error('[pyside6-webbluetooth] failed to install navigator.bluetooth polyfill:', installError);
  }
})();
'''

def _build_polyfill_js(object_name: str) -> str:
    if "__OBJECT_NAME__" not in _POLYFILL_JS_TEMPLATE:
        raise RuntimeError("polyfill template is missing its __OBJECT_NAME__ placeholder")
    return _POLYFILL_JS_TEMPLATE.replace("__OBJECT_NAME__", object_name)


def install(page, *, object_name: str = "pyBluetoothBridge"):
    """指定したQWebEnginePageに navigator.bluetooth を導入する。

    ページで読み込まれるすべてのフレーム(トップページ本体、および
    その中のiframe)のドキュメント生成時点で、QWebChannelのセットアップと
    navigator.bluetoothの定義が自動的に走るようになる。

    戻り値は生成された`BluetoothBridge`(bridge.py)。呼び出し側は
    アプリケーション終了時に`bridge.shutdown()`を呼ぶこと
    (BLEスキャン/接続をきちんと停止するため)。"""
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript

    from .bridge import BluetoothBridge

    bridge = BluetoothBridge(page, parent=page)
    channel = QWebChannel(page)
    channel.registerObject(object_name, bridge)
    page.setWebChannel(channel)

    script = QWebEngineScript()
    script.setName("pyside6-webbluetooth-polyfill")
    script.setSourceCode(_read_qwebchannel_js() + "\n" + _build_polyfill_js(object_name))
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    script.setRunsOnSubFrames(True)
    page.scripts().insert(script)

    return bridge
