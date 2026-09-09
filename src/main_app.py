"""
Tips_SE — 演劇用ポン出しアプリ（起動エントリポイント）。
ONJO の弱点（遅延/取りこぼし/長時間クラッシュ）を潰した低遅延ミキサーを核にする。

画面の中身は main_window.py、音は engine.py / padplayer.py にある。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from appconfig import app_data_dir, load_app_config, setup_logging
from engine import AudioEngine
from main_window import MainWindow
from remote import RemoteServer
from theme import DEFAULT_THEME, app_qss, palette_of, set_theme, t
from winui import (DialogCaptionStyler, app_icon, load_bundled_font, make_splash,
                   set_app_user_model_id)

log = logging.getLogger("tips_se")


def _install_excepthook():
    """未捕捉例外をログに記録し、可能ならダイアログ表示（windowedでも追える）。"""
    def hook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("未捕捉例外:\n%s", msg)
        try:
            QMessageBox.critical(
                None, "予期しないエラー",
                f"エラーが発生しました。\n\nログ: {app_data_dir() / 'tips_se.log'}\n\n{exc}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


def _start_selftest(app: QApplication, win: MainWindow) -> None:
    """ビルド検証用: TIPS_SE_SELFTEST=1 のとき、起動確認後に自動終了する。"""
    log.info("SELFTEST モード: 起動確認後に自動終了します")
    delay = 1800
    if os.environ.get("TIPS_SE_REMOTE"):
        # リモートサーバの配信確認用（ダイアログを出さず直接起動）
        try:
            win.remote = RemoteServer(win)
            win.remote.start()
            log.info("SELFTEST: remote started %s", win.remote.url())
            delay = 4000
        except Exception:
            log.exception("SELFTEST remote 起動失敗")
    QTimer.singleShot(delay, app.quit)


def main() -> int:
    setup_logging()
    _install_excepthook()
    log.info("==== Tips_SE 起動 ==== (frozen=%s)", getattr(sys, "frozen", False))

    app = QApplication(sys.argv)
    app.setApplicationName("Tips_SE")
    app.setWindowIcon(app_icon())
    set_app_user_model_id()
    app.setStyle("Fusion")
    load_bundled_font()   # 割当キーの大きな表示などに使う Noto Sans JP

    # 配色テーマ（前回の選択を復元）
    set_theme(load_app_config().get("theme", DEFAULT_THEME))
    app.setPalette(palette_of(t()))
    app.setStyleSheet(app_qss(t()))
    caption_styler = DialogCaptionStyler(app)   # app が参照を保持する
    app.installEventFilter(caption_styler)

    splash = make_splash()
    if splash:
        splash.show()
        splash.status("オーディオデバイスを準備中…")

    try:
        engine = AudioEngine(blocksize=1024)
        engine.start()
        log.info("オーディオ初期化: %s", engine.describe())
    except Exception as e:
        log.exception("オーディオ初期化失敗")
        if splash:
            splash.close()
        QMessageBox.critical(
            None, "オーディオ初期化エラー",
            f"オーディオデバイスを開けませんでした:\n{e}\n\n{traceback.format_exc()}")
        return 1

    if splash:
        splash.status("画面を準備中…")
    win = MainWindow(engine)
    win.show()
    if splash:
        splash.close_after_min(win)

    if os.environ.get("TIPS_SE_SELFTEST"):
        _start_selftest(app, win)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
