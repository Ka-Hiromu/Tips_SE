"""戯曲PDFビューア + 注釈(スタンプ/テキスト)の自動検証（オフスクリーン）。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPdfWriter, QPainter, QPageSize
from PySide6.QtWidgets import QApplication

from engine import AudioEngine
from main_app import MainWindow
from models import Preset

HERE = Path(__file__).resolve().parent


def make_pdf(path: Path, pages: int = 2):
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.A4))
    p = QPainter(w)
    for i in range(pages):
        if i > 0:
            w.newPage()
        p.drawText(800, 1200, f"戯曲テストページ {i + 1}")
    p.end()


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication([])  # QPdfWriter/QPainter より先に必要
    pdf = HERE.parent / "presets" / "_test_script.pdf"
    make_pdf(pdf, 2)

    eng = AudioEngine(prefer_wasapi=False)
    eng.start()
    win = MainWindow(eng)
    win.show()
    pump(app)

    # PDF読み込み
    ok = win.script.load_pdf(str(pdf))
    win.preset.pdf_path = str(pdf)  # 実アプリでは open_pdf がこれを行う
    pump(app)
    assert ok and win.script.has_pdf(), "PDF読み込み失敗"
    assert win.script.page_count() == 2, f"ページ数異常 {win.script.page_count()}"
    print("PDF読込 OK / pages =", win.script.page_count())

    # 編集モード + スタンプ配置（1ページ目, pad=2 → 表示番号3）
    win.edit_btn.setChecked(True)
    win.tool_stamp.setChecked(True)
    win.script.set_tool("stamp")
    win.script.set_stamp_pad(2)
    win.script._place_at(QPoint(120, 140))
    # スタンプは連続配置できる（ツール維持）
    assert win.script.tool == "stamp", "スタンプツールが解除されてしまった"
    # テキスト配置 → 1個置いたら配置モード解除（誤爆防止）
    win.tool_text.setChecked(True)
    win.script.set_tool("text")
    win.script._place_at(QPoint(200, 260))
    assert win.script.tool is None, "テキスト配置後にツールが解除されていない"
    assert not win.tool_text.isChecked(), "テキストボタンのチェックが外れていない"
    assert len(win.script._anns) == 2, f"注釈数 {len(win.script._anns)}"
    stamp = [a for a in win.script._anns if a.kind == "stamp"][0]
    assert stamp.pad == 2 and stamp.page == 0
    print("スタンプ連続/テキスト単発配置 OK")

    # 連続スクロール: 2ページ目へジャンプ → current page が 1 になる
    win.script.set_page(1)
    pump(app)
    assert win.script.page == 1, f"ページ移動できない page={win.script.page}"
    win.script.set_page(0)
    pump(app)
    assert win.script.page == 0, f"先頭に戻れない page={win.script.page}"
    # 注釈は全ページぶん常にシーンに存在する（連続表示）
    assert len(win.script._items) == 2, "注釈が保持されていない"
    print("連続スクロール/ページ移動 OK")

    # 本番モードでスタンプクリック → 対象パッド再生（音源を割当）
    import numpy as np, soundfile as sf
    snd = HERE.parent / "sounds" / "_p.wav"
    sr = 48000; import numpy as np
    t = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
    sf.write(snd, (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32"), sr)
    win.preset.pads[2].path = str(snd)
    win._rebuild_grid()
    # ロード完了待ち
    for _ in range(200):
        app.processEvents()
        if eng.get_clip("pad2") is not None:
            break
        time.sleep(0.01)
    win.edit_btn.setChecked(False)
    win._on_stamp_clicked(2)
    assert eng.is_playing("pad2"), "スタンプから再生されない"
    print("スタンプ→音再生 OK")
    eng.stop_all()

    # プリセット保存/読込（PDF + 注釈の往復）
    pp = HERE.parent / "presets" / "_test_script.json"
    win.preset_path = str(pp)
    win._do_save(str(pp))
    loaded = Preset.load(str(pp))
    assert loaded.pdf_path == str(pdf)
    assert len(loaded.annotations) == 2
    kinds = sorted(a.kind for a in loaded.annotations)
    assert kinds == ["stamp", "text"], kinds
    print("プリセット往復（PDF+注釈）OK")

    win.script.clear()  # PDFを閉じてからでないと削除できない
    eng.close()
    win.loader.shutdown()
    pump(app)
    for f in (pdf, pp, snd):
        try:
            f.unlink(missing_ok=True)
        except PermissionError:
            pass
    print("ALL PDF/ANNOTATION TESTS PASSED")


if __name__ == "__main__":
    main()
