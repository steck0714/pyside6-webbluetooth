# -*- coding: utf-8 -*-
"""pytestに'tests/'を発見させた際、pip install不要でsrc/レイアウトのパッケージを
importできるようにするための設定。`pip install -e .`済みなら本来不要だが、
素のクローン直後でも `pytest` や `python tests/test_*.py` が動くようにしておく。

★ ヘッドレス環境(CI、Dockerコンテナ、ディスプレイの無いサンドボックス等)では、
QApplication([]) の生成がPythonの例外ではなく Fatal Python error: Aborted という
捕捉不可能なプロセスクラッシュになることがある(Qtのxcbプラットフォームプラグインが
ディスプレイに接続できず異常終了するため)。DISPLAYが設定されておらず、かつ
QT_QPA_PLATFORMをユーザーが明示していない場合に限り、自動的に'offscreen'へ
フォールバックする。実ディスプレイがある開発機ではこの分岐は発火せず、
通常どおり実プラットフォームで(GUIを見ながら)動く。

QtWebEngine(Chromium)はroot権限で--no-sandboxなしに起動できないため、
サンドボックス/コンテナ環境かつ未設定の場合はQTWEBENGINE_CHROMIUM_FLAGSに
--no-sandboxを補う。実開発機でroot以外のユーザーとして動かす場合はこの分岐は
発火しない。

姉妹プロジェクト pyside6-webusb と同じ構成。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

if sys.platform.startswith("linux") and os.geteuid() == 0 and "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-software-rasterizer"

import pytest  # noqa: E402


class _FreeLoop:
    """pytest-qtを追加依存にせず、コールバックベースの非同期QtコードをPython
    テストの中で待ち合わせるための最小限のヘルパー。`later()`でQTimerを
    仕込み、`run()`でネストしたQEventLoopに入って`stop()`が呼ばれる
    (またはtimeout)まで待つだけの薄いラッパー。"""

    def __init__(self):
        from PySide6.QtCore import QEventLoop

        self._loop = QEventLoop()

    def later(self, ms, callback):
        from PySide6.QtCore import QTimer

        QTimer.singleShot(ms, callback)

    def stop(self):
        self._loop.quit()

    def run(self, timeout_ms=5000):
        from PySide6.QtCore import QTimer

        QTimer.singleShot(timeout_ms, self._loop.quit)
        self._loop.exec()


@pytest.fixture
def qtbot_free_loop():
    return _FreeLoop()
