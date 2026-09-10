# -*- coding: utf-8 -*-
"""フレーム単位のオリジン追跡(iframeなりすまし対策)。

## 問題

QWebChannelでPythonのブリッジオブジェクトをページに公開すると、その
ページ内の *すべて* のフレーム(トップページ自身と、その中に埋め込まれた
すべてのiframe)から同じブリッジオブジェクトが見える。もしiframeが
「自分のオリジンはこうだ」とJS側で自己申告した文字列をそのまま信用すると、
悪意のある(あるいは単に脆弱性のある)サイトに埋め込まれた第三者製iframeが、
トップページのオリジンを名乗って requestDevice() を呼び出したり、
トップページがすでに許可を得たデバイスに(自分は許可されていないのに)
アクセスしたりできてしまう。

## 対策

「JSが自己申告するオリジン文字列」を一切信用しない。代わりに:

  1. Qt WebEngine自身が把握している、各フレームの実際のURL
     (`QWebEngineFrame.url()`)からオリジンを導出する。これは
     JSページ側のコードからは書き換えられない、Python(Qtエンジン)側の
     一次情報である。
  2. 各フレームに対して、そのフレーム *だけ* に向けてJavaScriptを実行できる
     `QWebEngineFrame.runJavaScript(script, worldId)` を使い、推測不可能な
     ランダムトークンをそのフレームの`window`オブジェクトへ個別に注入する。
     これは実機で検証済み: 一方のフレームに注入した値が別のフレームの
     `window`から見えないことを確認している(フレーム間のJS実行コンテキストは
     Chromiumのプロセス/コンテキスト分離によって隔てられている)。
  3. JS側(polyfill.py)は、自分の`window`に置かれたトークンをそのまま
     ブリッジ呼び出しに含める。オリジン文字列そのものは送らない。
  4. Python側は「トークン→そのトークンを発行した時点で確認した実オリジン」の
     対応表だけを信頼し、ブリッジ呼び出しごとにこの表を引いて検証する。
     知らないトークンやウィンドウ生成直後でまだトークンが届いていない
     フレームからの呼び出しは、フェイルクローズ(SecurityError)で拒否する。

## 制約(正直に書いておく)

- `about:blank` / `about:srcdoc` / `data:` / 空URL など、Web上の「不透明
  オリジン(opaque origin)」に該当するフレームには、そもそもトークンを
  発行しない(常に拒否)。これは実際のブラウザがこの種のコンテキストに
  対してWeb Bluetoothへのアクセスを許可しないのと同じ方針。
- 動的に挿入されたiframe(`document.body.appendChild(iframe)`等)は、
  次回の再スキャンまでトークンを受け取れない。`loadFinished`シグナルに
  加えて短い周期のタイマーで再スキャンすることで、この遅延を実用上
  問題にならない範�囲(既定0.75秒)に抑えている。挙動は実機で検証済み。
- ページ読み込み直後、Python側の注入が完了する前にフレームがブリッジを
  呼び出そうとした場合、そのフレームの`window`にはまだトークンが無いため
  `undefined`が送られる。Python側はこれを「未検証」として拒否する
  (安全側に倒す=fail closed。開いてしまう=fail openにはしない)。
"""
from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - 型チェック専用
    from PySide6.QtWebEngineCore import QWebEngineFrame, QWebEnginePage

# 各フレームの window に注入するグローバル変数名。
# polyfill.py側もこの名前を直接参照する(WINDOW_TOKEN_PROPERTY を経由)。
WINDOW_TOKEN_PROPERTY = "__pyside6WebBluetoothFrameToken"

# https/httpのデフォルトポート。明示的に書かれていても省略時と同一オリジンと
# みなすための正規化に使う(WHATWG URL living standardのデフォルトポート表の
# うちWeb Bluetoothが実務上出会うものに限定)。
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}

# 発行したトークンをどれだけ保持し続けるか(秒)。この間は「1つ前の
# フレーム世代」のトークンでの呼び出しも許容する(再スキャンとブリッジ呼び出し
# が競合しても失敗しないようにするための猶予)。
_TOKEN_TTL_SECONDS = 30.0

# 不透明オリジン(opaque origin)を表す内部マーカー。Web標準でのシリアライズに
# 合わせて文字列 "null" を使うが、これがトークン解決の結果として返ることは
# ない(不透明オリジンのフレームにはそもそもトークンを発行しないため)。
OPAQUE_ORIGIN = "null"


def derive_origin(url) -> Optional[str]:
    """QUrlから "scheme://host[:port]" 形式のオリジン文字列を導出する。

    about:/data:/空URLなど、hostを持たない(=不透明オリジンの)URLに対しては
    Noneを返す。呼び出し側はNoneを「この種のフレームにはアクセスを許可しない」
    という意味で扱うこと。"""
    try:
        scheme = url.scheme().lower()
        host = url.host().lower()
    except AttributeError:
        return None
    if not scheme or not host:
        return None
    port = url.port(-1)
    if port != -1 and _DEFAULT_PORTS.get(scheme) == port:
        port = -1
    if port == -1:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


class FrameOriginTracker:
    """1つのQWebEnginePageに紐づく、フレーム→検証済みオリジンの追跡器。

    使い方::

        tracker = FrameOriginTracker(page)
        tracker.start()
        ...
        origin = tracker.resolve_origin(token_from_js)
        if origin is None:
            # 未検証。SecurityErrorとして拒否する。
    """

    def __init__(self, page: "QWebEnginePage", *, rescan_interval_ms: int = 750) -> None:
        self._page = page
        self._rescan_interval_ms = rescan_interval_ms
        self._tokens: dict[str, tuple[str, float]] = {}
        self._timer = None
        self._started = False

    def start(self) -> None:
        """再スキャンを開始する。loadFinishedシグナル + 周期タイマーの両方で
        フレーム構成の変化(ナビゲーション、動的iframe挿入)を検出する。"""
        if self._started:
            return
        self._started = True
        from PySide6.QtCore import QTimer

        self._page.loadFinished.connect(lambda _ok: self.rescan())
        self._timer = QTimer(self._page)
        self._timer.setInterval(self._rescan_interval_ms)
        self._timer.timeout.connect(self.rescan)
        self._timer.start()
        # ページがすでに読み込み済みで呼ばれた場合に備え、即座に一度実行する。
        self.rescan()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._started = False

    def rescan(self) -> None:
        """現在のフレーム木を歩き、各フレームへ新しいトークンを配布する。"""
        try:
            main_frame = self._page.mainFrame()
        except Exception:
            # アプリ終了処理中などでpage自体が破棄されかけている場合に
            # 備える。QTimerは明示的にstop()するまで発火し続けるため、
            # ここで例外を投げるとタイマーのコールバック連鎖を壊しうる。
            return
        if main_frame is None:
            return
        self._prune_expired()
        self._visit(main_frame)

    def _visit(self, frame: "QWebEngineFrame") -> None:
        try:
            if frame is None or not frame.isValid():
                return
            origin = derive_origin(frame.url())
            children = list(frame.children())
        except Exception:
            # Qtのドキュメントにある通り、フレームは自発的に生成・破棄され
            # 得る("may be created and deleted spontaneously")。
            # isValid()の直後であっても、その後の.url()/.children()呼び出しが
            # 完全に安全である保証は無いため、ここで打ち切っても
            # rescan()全体やQTimerの周期処理を壊さないようにする
            # (次の周期でやり直せば済む話であり、フェイルクローズの
            # 原則にも反しない)。
            return

        if origin is not None:
            token = secrets.token_urlsafe(32)
            self._tokens[token] = (origin, time.monotonic())
            script = (
                f"window.{WINDOW_TOKEN_PROPERTY} = {token!r};"
            )
            try:
                frame.runJavaScript(script, 0)
            except Exception:
                # フレームがスキャンとinject呼び出しの間に破棄されるレースは
                # 起こり得る。トークンは登録済みだが誰にも渡らないだけなので
                # 安全側(単にそのフレームは今回トークンを受け取れない)。
                pass
        # 不透明オリジンのフレーム(about:/data:等)には何も注入しない。
        # window.__pyside6WebBluetoothFrameToken は未定義のままとなり、
        # そのフレームからのブリッジ呼び出しはPython側で必ず拒否される。
        for child in children:
            self._visit(child)

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [
            tok for tok, (_origin, issued_at) in self._tokens.items()
            if now - issued_at > _TOKEN_TTL_SECONDS
        ]
        for tok in expired:
            del self._tokens[tok]

    def resolve_origin(self, token: Optional[str]) -> Optional[str]:
        """トークンから検証済みオリジンを引く。未知/期限切れならNone。"""
        if not token or not isinstance(token, str):
            return None
        entry = self._tokens.get(token)
        if entry is None:
            return None
        origin, issued_at = entry
        if time.monotonic() - issued_at > _TOKEN_TTL_SECONDS:
            del self._tokens[token]
            return None
        return origin
