# -*- coding: utf-8 -*-
"""pyside6-webbluetooth: PySide6/QtWebEngineアプリ向けのWeb Bluetooth API実装。

使い方::

    from PySide6.QtWebEngineWidgets import QWebEngineView
    from pyside6_webbluetooth import install

    view = QWebEngineView()
    bridge = install(view.page())
    view.load(QUrl("https://example.com"))
    ...
    # アプリ終了時
    bridge.shutdown()

姉妹プロジェクトである pyside6-webusb (https://github.com/steck0714/Pyside6-webusb)
と同じ位置づけの、mock-webbluetooth枠組み(https://github.com/steck0714/Mock-APIs)
におけるPySide6実装。詳細はREADME.ja.md / README.en.mdを参照。"""
from ._version import __version__
from .bridge import BluetoothBridge
from .errors import make_error
from .hardening import BlocklistedUUIDError, InvalidFilterError
from .polyfill import install

__all__ = [
    "__version__",
    "install",
    "BluetoothBridge",
    "BlocklistedUUIDError",
    "InvalidFilterError",
    "make_error",
]
