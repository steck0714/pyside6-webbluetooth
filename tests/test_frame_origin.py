# -*- coding: utf-8 -*-
"""frame_origin.pyのテスト。

derive_origin()は純粋関数なのでQt WebEngineなしで検証できる。
FrameOriginTrackerの本体(フレームごとのトークン注入と分離)は実際の
QWebEnginePage + iframeを使って検証する -- これは「本当にフレームが
分離されているか」というセキュリティ上の主張そのものであり、モックでは
検証する意味がないため。"""
import pytest
from PySide6.QtCore import QUrl

from pyside6_webbluetooth.frame_origin import (
    FrameOriginTracker,
    WINDOW_TOKEN_PROPERTY,
    derive_origin,
)


class TestDeriveOrigin:
    def test_basic_https(self):
        assert derive_origin(QUrl("https://example.com/path")) == "https://example.com"

    def test_explicit_nondefault_port_kept(self):
        assert derive_origin(QUrl("https://example.com:8443/x")) == "https://example.com:8443"

    def test_explicit_default_https_port_normalized_away(self):
        assert derive_origin(QUrl("https://example.com:443/x")) == "https://example.com"

    def test_explicit_default_http_port_normalized_away(self):
        assert derive_origin(QUrl("http://example.com:80/x")) == "http://example.com"

    def test_nondefault_http_port_kept(self):
        assert derive_origin(QUrl("http://example.com:8080/x")) == "http://example.com:8080"

    def test_host_case_insensitive(self):
        assert derive_origin(QUrl("https://Example.COM/x")) == "https://example.com"

    @pytest.mark.parametrize(
        "url",
        [
            "about:blank",
            "about:srcdoc",
            "data:text/html,hi",
            "",
            "file:///tmp/x.html",
        ],
    )
    def test_opaque_origins_return_none(self, url):
        assert derive_origin(QUrl(url)) is None

    def test_different_hosts_are_different_origins(self):
        a = derive_origin(QUrl("https://a.example/"))
        b = derive_origin(QUrl("https://b.example/"))
        assert a != b

    def test_different_schemes_are_different_origins(self):
        a = derive_origin(QUrl("https://example.com/"))
        b = derive_origin(QUrl("http://example.com/"))
        assert a != b


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class TestFrameOriginTrackerLive:
    """実際のQWebEnginePageと(動的に挿入した)iframeを使い、フレーム間で
    トークンが漏れない/なりすませないことを検証する。"""

    def test_frames_get_distinct_unforgeable_tokens(self, qapp, qtbot_free_loop):
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

        page = QWebEnginePage(QWebEngineProfile.defaultProfile())
        tracker = FrameOriginTracker(page, rescan_interval_ms=200)
        results: dict = {}

        def on_loaded(_ok):
            tracker.start()

            def insert_child():
                js = (
                    "var f = document.createElement('iframe');"
                    "f.src = 'https://child-embed.example/frame.html';"
                    "document.body.appendChild(f);"
                )
                page.runJavaScript(js, 0, lambda _v: None)
                qtbot_free_loop.later(600, after_scan)

            qtbot_free_loop.later(300, insert_child)

        def after_scan():
            mf = page.mainFrame()
            kids = mf.children()
            assert len(kids) == 1
            child = kids[0]

            def got_top(tok):
                results["top"] = tok

                def got_child(tok2):
                    results["child"] = tok2
                    qtbot_free_loop.stop()

                child.runJavaScript(f"window.{WINDOW_TOKEN_PROPERTY}", 0, got_child)

            mf.runJavaScript(f"window.{WINDOW_TOKEN_PROPERTY}", 0, got_top)

        page.loadFinished.connect(on_loaded)
        page.setUrl(QUrl("https://parent.example/"))
        qtbot_free_loop.run(timeout_ms=8000)

        top_tok = results.get("top")
        child_tok = results.get("child")
        assert top_tok and child_tok
        assert top_tok != child_tok

        assert tracker.resolve_origin(top_tok) == "https://parent.example"
        assert tracker.resolve_origin(child_tok) == "https://child-embed.example"

        # 未知/偽造/欠落トークンは必ずNone(フェイルクローズ)
        assert tracker.resolve_origin("forged-token") is None
        assert tracker.resolve_origin(None) is None
        assert tracker.resolve_origin("") is None
