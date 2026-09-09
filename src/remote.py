"""
スマホ・リモート操作サーバ。
同一Wi-Fi上のスマホからブラウザでアクセスし、再生中の音源だけを
縦フェーダーで同時操作（クロスフェード）・停止できるようにする。

構成（新規依存なし）:
  - 静的ページ配信: 標準ライブラリ http.server をデーモンスレッドで実行（remote_web/）。
  - リアルタイム制御: PySide6 QWebSocketServer（Qtイベントループ＝メインスレッドで動作）。
    受信ハンドラからエンジン/ウィンドウを直接操作してよい（同一スレッド）。
"""
from __future__ import annotations

import functools
import http.server
import json
import socket
import sys
import threading
from pathlib import Path


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """アクセスログを出さない静的ハンドラ。

    windowedビルド(コンソール無し)では sys.stderr が None になり、
    既定の log_message が sys.stderr へ書き込もうとして例外→接続が切れる。
    それを防ぐためログを無効化する。
    """
    def log_message(self, *args):
        pass


def _web_dir() -> Path:
    """配信ページ(remote_web)の場所。開発時とPyInstaller frozen時の両対応。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "remote_web"
    return Path(__file__).resolve().parent.parent / "remote_web"

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer


def lan_ip() -> str:
    """デフォルト経路のローカルIP（スマホから繋ぐ先）を返す。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class RemoteServer(QObject):
    started = Signal(str)   # 接続URL
    stopped = Signal()
    clientsChanged = Signal(int)

    def __init__(self, window, http_port: int = 8770, ws_port: int = 8771, parent=None):
        super().__init__(parent)
        self.window = window
        self.engine = window.engine
        self.http_port = http_port
        self.ws_port = ws_port
        self._clients: list = []
        self._http = None
        self._http_thread = None
        self._ws = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._broadcast)
        self.running = False

    # ---- ライフサイクル ----
    def start(self) -> str:
        if self.running:
            return self.url()
        handler = functools.partial(_QuietHandler, directory=str(_web_dir()))
        self._http = http.server.ThreadingHTTPServer(("0.0.0.0", self.http_port), handler)
        self._http_thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._http_thread.start()

        self._ws = QWebSocketServer("Tips_SE Remote", QWebSocketServer.NonSecureMode, self)
        self._ws.newConnection.connect(self._on_new_connection)
        if not self._ws.listen(QHostAddress.Any, self.ws_port):
            self.stop()
            raise RuntimeError(f"WebSocketポート{self.ws_port}を開けません")

        self._timer.start(100)
        self.running = True
        url = self.url()
        self.started.emit(url)
        return url

    def stop(self):
        self._timer.stop()
        for s in list(self._clients):
            try: s.close()
            except Exception: pass
        self._clients.clear()
        if self._ws:
            self._ws.close(); self._ws = None
        if self._http:
            self._http.shutdown(); self._http.server_close(); self._http = None
        self.running = False
        self.stopped.emit()

    def url(self) -> str:
        return f"http://{lan_ip()}:{self.http_port}/"

    # ---- WebSocket ----
    def _on_new_connection(self):
        sock = self._ws.nextPendingConnection()
        sock.textMessageReceived.connect(
            lambda msg, s=sock: self._on_message(msg, s))
        sock.disconnected.connect(lambda s=sock: self._remove_client(s))
        self._clients.append(sock)
        self._send_state(sock)
        self.clientsChanged.emit(len(self._clients))

    def _remove_client(self, sock):
        if sock in self._clients:
            self._clients.remove(sock)
            self.clientsChanged.emit(len(self._clients))

    def _on_message(self, msg: str, sock):
        try:
            d = json.loads(msg)
        except Exception:
            return
        t = d.get("type")
        pads = self.window.preset.pads
        if t == "gain":
            idx = int(d.get("pad", -1))
            if 0 <= idx < len(pads):
                self.window.set_pad_gain_remote(idx, float(d.get("value", 1.0)))
        elif t == "stop":
            idx = int(d.get("pad", -1))
            if 0 <= idx < len(pads):
                self.window.stop_pad(pads[idx])
        elif t == "stopAll":
            self.window.stop_all()

    # ---- 状態配信（再生中の音源のみ） ----
    def _payload(self) -> str:
        items = []
        for p in self.window.preset.pads:
            if p.assigned and self.window._pad_is_playing(p):
                items.append({"pad": p.index, "num": p.index + 1,
                              "name": p.display_name(), "gain": round(p.gain, 3)})
        return json.dumps({"type": "state", "playing": items}, ensure_ascii=False)

    def _send_state(self, sock):
        if sock and sock.isValid():
            sock.sendTextMessage(self._payload())

    def _broadcast(self):
        if not self._clients:
            return
        msg = self._payload()
        for s in list(self._clients):
            if s.isValid():
                s.sendTextMessage(msg)
