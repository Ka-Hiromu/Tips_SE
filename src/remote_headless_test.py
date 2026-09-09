"""スマホ・リモートの自動検証（オフスクリーン）。
QWebSocketクライアントで実際に接続し、state受信 → gain/stop 送信 → エンジン反映を確認する。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebSockets import QWebSocket

from engine import AudioEngine
from main_app import MainWindow

HERE = Path(__file__).resolve().parent


def pump(app, cond, timeout=5.0):
    t0 = time.perf_counter()
    while not cond() and time.perf_counter() - t0 < timeout:
        app.processEvents(); time.sleep(0.01)
    return cond()


def main():
    app = QApplication([])
    eng = AudioEngine(prefer_wasapi=False); eng.start()
    win = MainWindow(eng)

    # 音源を割当して再生
    snd = HERE.parent / "sounds" / "_rem.wav"
    sr = 48000; t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    sf.write(snd, (0.5 * np.sin(2 * np.pi * 330 * t)).astype("float32"), sr)
    p0 = win.preset.pads[0]; p0.path = str(snd); p0.label = "テスト音"
    win._rebuild_grid()
    pump(app, lambda: eng.get_clip(win._clip_id(p0)) is not None, 10)
    win.trigger_pad(p0)
    assert eng.is_playing(win._clip_id(p0)), "再生できていない"

    # リモート開始
    win.remote = win.remote or None
    from remote import RemoteServer
    win.remote = RemoteServer(win, http_port=8780, ws_port=8781)
    win.remote.start()
    print("remote url:", win.remote.url())

    # WSクライアントで接続
    received = []
    ws = QWebSocket()
    ws.textMessageReceived.connect(lambda m: received.append(m))
    connected = {"v": False}
    ws.connected.connect(lambda: connected.__setitem__("v", True))
    ws.open(QUrl("ws://127.0.0.1:8781"))
    assert pump(app, lambda: connected["v"], 5), "WS接続失敗"
    print("WS接続 OK")

    # 初期state受信 → 再生中の音が含まれる
    assert pump(app, lambda: len(received) > 0, 3), "state未受信"
    state = json.loads(received[-1])
    assert state["type"] == "state"
    assert any(it["pad"] == 0 and it["name"] == "テスト音" for it in state["playing"]), \
        f"再生中リストにpad0が無い: {state}"
    print("state受信・再生中リスト OK")

    # gain 送信 → pad.gain 反映
    ws.sendTextMessage(json.dumps({"type": "gain", "pad": 0, "value": 0.4}))
    assert pump(app, lambda: abs(win.preset.pads[0].gain - 0.4) < 1e-3, 3), \
        f"gain未反映 gain={win.preset.pads[0].gain}"
    print("リモートgain反映 OK / pad.gain =", round(win.preset.pads[0].gain, 3))

    # stop 送信 → 停止
    ws.sendTextMessage(json.dumps({"type": "stop", "pad": 0}))
    assert pump(app, lambda: not eng.is_playing(win._clip_id(p0)), 3), "stop未反映"
    print("リモートstop反映 OK")

    # 停止後の state は空（再生中のみ表示）
    received.clear()
    assert pump(app, lambda: len(received) > 0, 3)
    state2 = json.loads(received[-1])
    assert state2["playing"] == [], f"停止後も再生中扱い: {state2}"
    print("停止後 再生中リスト空 OK")

    ws.close()
    win.remote.stop()
    eng.close(); win.loader.shutdown()
    snd.unlink(missing_ok=True)
    print("ALL REMOTE TESTS PASSED")


if __name__ == "__main__":
    main()
