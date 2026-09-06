# -*- coding: utf-8 -*-
"""pyside6-webbluetooth の最小サンプル。

navigator.bluetooth を有効化した、URLバー付きの単純なブラウザウィンドウ。
F12(またはCtrl+Shift+I)でDevToolsを開けるので、実際のWebサイトの
navigator.bluetooth呼び出しをコンソールで試したり、
https://googlechrome.github.io/samples/web-bluetooth/ のような
公式サンプルを動かして確認するのに使える。

実行方法::

    pip install pyside6-webbluetooth
    python minimal_browser.py [URL]

（BLEアダプタが無い/OSの権限が無い環境では getAvailability() が
false を返すだけで、アプリ自体はクラッシュしない。)
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMainWindow,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from pyside6_webbluetooth import __version__, install


class BrowserWindow(QMainWindow):
    def __init__(self, start_url: str) -> None:
        super().__init__()
        self.setWindowTitle(f"pyside6-webbluetooth デモブラウザ (v{__version__})")
        self.resize(1200, 800)

        self.view = QWebEngineView()
        self.bridge = install(self.view.page())

        self.devtools_view: QWebEngineView | None = None

        toolbar = QToolBar("navigation")
        self.addToolBar(toolbar)

        self.address_bar = QLineEdit()
        self.address_bar.returnPressed.connect(self._navigate_to_address_bar)
        toolbar.addWidget(self.address_bar)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.setCentralWidget(central)

        self.view.urlChanged.connect(lambda url: self.address_bar.setText(url.toString()))
        self.view.loadFinished.connect(lambda _ok: self.setWindowTitle(self.view.title() or "pyside6-webbluetooth デモブラウザ"))

        QShortcut(QKeySequence("F12"), self, activated=self._toggle_devtools)
        QShortcut(QKeySequence("Ctrl+Shift+I"), self, activated=self._toggle_devtools)

        self.view.setUrl(QUrl(start_url))

    def _navigate_to_address_bar(self) -> None:
        text = self.address_bar.text().strip()
        if not text:
            return
        if "://" not in text:
            text = "https://" + text
        self.view.setUrl(QUrl(text))

    def _toggle_devtools(self) -> None:
        if self.devtools_view is not None:
            self.devtools_view.close()
            self.devtools_view = None
            return
        self.devtools_view = QWebEngineView()
        self.devtools_view.setWindowTitle("DevTools")
        self.devtools_view.resize(1000, 700)
        self.view.page().setDevToolsPage(self.devtools_view.page())
        self.devtools_view.show()

    def closeEvent(self, event) -> None:  # noqa: D102 - Qt override
        self.bridge.shutdown()
        super().closeEvent(event)


def main() -> None:
    start_url = sys.argv[1] if len(sys.argv) > 1 else "https://googlechrome.github.io/samples/web-bluetooth/"
    app = QApplication(sys.argv)
    window = BrowserWindow(start_url)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
