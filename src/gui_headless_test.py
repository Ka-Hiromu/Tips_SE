"""オフスクリーンでGUIを起動し、
   バックグラウンド並列読込（非ブロック）・トグル停止・個別フェーダー・
   プリセット保存/読込・ONJO(.lst)取り込み を検証する。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import dialogs
from dialogs import PadEditDialog
from engine import AudioEngine
from main_window import MainWindow
from models import Pad, Preset
from onjo_headless_test import player as onjo_player

HERE = Path(__file__).resolve().parent
SOUNDS = HERE.parent / "sounds"
SOUNDS.mkdir(exist_ok=True)


def beep(path, freq, dur=0.3, sr=48000):
    # エンジンSRと異なる48kでも作る → リサンプル(soxr)経路を通す
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    sf.write(path, (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr)


def pump_until(app, cond, timeout=20.0):
    """Qtイベントを回しながら cond() が真になるまで待つ。"""
    t0 = time.perf_counter()
    while not cond() and (time.perf_counter() - t0) < timeout:
        app.processEvents()
        time.sleep(0.005)
    return cond()


def main():
    app = QApplication([])
    eng = AudioEngine(blocksize=256, prefer_wasapi=False)
    eng.start()
    win = MainWindow(eng)

    # 16パッドに音源割当（やや長め1本を含めて負荷をかける）
    N = 16
    paths = []
    for i in range(N):
        p = SOUNDS / f"_g{i}.wav"
        beep(p, 400 + i * 25, dur=(3.0 if i == 0 else 0.3))
        paths.append(p)
    win.preset.rows, win.preset.cols = 4, 4
    win.preset.ensure_pads()
    for i in range(N):
        win.preset.pads[i].path = str(paths[i])
    win.preset.pads[0].key = "a"
    win.preset.pads[1].loop = True

    # --- 非ブロック性の検証: _rebuild_grid（→ 並列submit）は即座に返るはず ---
    t0 = time.perf_counter()
    win._rebuild_grid()
    submit_ms = (time.perf_counter() - t0) * 1000
    print(f"_rebuild_grid 呼び出し時間: {submit_ms:.1f} ms（UIブロックしないこと）")
    assert submit_ms < 500, f"UIをブロックしている（{submit_ms:.0f}ms）"
    # この時点ではまだ読み込み中のはず
    print(f"submit直後 pending={win.player.pending_count} / 全{win.player.pending_total}")

    # --- 全完了まで待つ ---
    ok = pump_until(app, lambda: win.player.pending_count == 0, timeout=30)
    assert ok, "読み込みが完了しなかった"
    for i in range(N):
        assert eng.get_clip(f"pad{i}") is not None, f"pad{i} 未ロード"
    print(f"並列バックグラウンド読込 完了 OK（{N}本）")

    # --- トグル停止/再生 ---
    win.trigger_pad(win.preset.pads[0])
    assert eng.is_playing("pad0")
    win.toggle_pad(win.preset.pads[0])
    time.sleep(0.05)
    assert not eng.is_playing("pad0"), "トグル停止が効いていない"
    win.toggle_pad(win.preset.pads[0])
    assert eng.is_playing("pad0"), "トグル再生が効いていない"
    print("トグル停止/再生 OK")

    # --- 個別フェーダー（再生中の即時反映） ---
    win.pad_buttons[0].fader.setValue(50)
    assert abs(win.preset.pads[0].gain - 0.5) < 1e-6
    vg = [v.gain for v in eng._voices if v.clip.clip_id == "pad0"]
    assert vg and abs(vg[0] - 0.5) < 1e-6, "再生中ボイスへ未反映"
    print("個別フェーダー（即時反映）OK")

    eng.stop_all()
    time.sleep(0.1)

    # --- プリセット保存/読込 ---
    pp = HERE.parent / "presets" / "_test.json"
    win.preset_path = str(pp)
    win._do_save(str(pp))
    loaded = Preset.load(str(pp))
    assert loaded.pads[0].path == str(paths[0])
    assert loaded.pads[1].loop is True
    print("プリセット保存/読込 OK")

    # --- 再生範囲（読み込み時に切り出す） ---
    p0 = win.preset.pads[0]       # 3.0秒のビープ
    p0.start, p0.end = 0.5, 1.5
    win.player.reload_pad(p0)
    assert pump_until(app, lambda: win.player.pending_count == 0)
    assert abs(eng.get_clip("pad0").duration - 1.0) < 0.02, eng.get_clip("pad0").duration
    win.trigger_pad(p0)
    assert eng.is_playing("pad0"), "切り出したクリップが鳴らない"
    eng.stop_all()
    time.sleep(0.05)
    # 範囲が逆/行き過ぎでも空クリップにしない（鳴らないパッドを作らない）
    eng.load_clip("_trim_over", str(paths[0]), 10.0, 0.0)
    assert eng.get_clip("_trim_over").frames > 0
    eng.load_clip("_trim_rev", str(paths[0]), 2.0, 1.0)
    assert eng.get_clip("_trim_rev").duration > 0.5
    eng.unload_clip("_trim_over")
    eng.unload_clip("_trim_rev")
    p0.start = p0.end = 0.0
    win.player.reload_pad(p0)
    assert pump_until(app, lambda: win.player.pending_count == 0)
    assert abs(eng.get_clip("pad0").duration - 3.0) < 0.05
    print("再生範囲（切り出し）OK")

    # --- パッド設定ダイアログ: 再生範囲の往復と逆範囲の拒否 ---
    dlg = PadEditDialog(Pad(index=0, path=str(paths[0])), parent=win)
    dlg.start_spin.setValue(1.25)
    dlg.end_spin.setValue(2.5)
    edited = dlg.result_pad()
    assert (edited.start, edited.end) == (1.25, 2.5), (edited.start, edited.end)
    warned = []

    class _StubBox:                      # 警告ダイアログで止まらないよう差し替える
        Yes, No = QMessageBox.Yes, QMessageBox.No

        @staticmethod
        def warning(*a, **k):
            warned.append(a)
            return _StubBox.No

    real_box, dialogs.QMessageBox = dialogs.QMessageBox, _StubBox
    try:
        dlg.end_spin.setValue(1.0)       # 終了 <= 開始
        dlg.accept()
        assert warned, "逆向きの再生範囲が通ってしまう"
        assert dlg.result() != QDialog.Accepted
    finally:
        dialogs.QMessageBox = real_box
    print("パッド設定ダイアログ（再生範囲）OK")

    # --- ONJO(.lst)の取り込み（報告ダイアログは差し替えて止めない） ---
    lst = SOUNDS / "_onjo.lst"
    lst.write_bytes(("".join([
        onjo_player(0, key=2, files=[str(paths[0])], vol=0.5, rep=1,
                    start=5000000, end=15000000),
        onjo_player(1, files=[str(paths[1])], fin=1),
    ])).encode("cp932"))
    reports = []
    win._show_onjo_report = lambda name, rep: reports.append(rep)
    win._load_onjo(str(lst))
    assert win.preset_path is None, ".lst を保存先にしてはいけない"
    assert reports and reports[0].pads == 2, reports
    assert win.preset.pads[0].key == "1" and win.preset.pads[0].loop is True
    assert abs(win.preset.pads[0].gain - 0.5) < 1e-6
    assert (win.preset.pads[0].start, win.preset.pads[0].end) == (0.5, 1.5)
    assert pump_until(app, lambda: win.player.pending_count == 0, timeout=30)
    assert eng.get_clip("pad0") is not None, "取り込んだ音源が常駐していない"
    assert abs(eng.get_clip("pad0").duration - 1.0) < 0.02   # 再生範囲も効いている
    print("ONJO(.lst)取り込み OK")

    eng.close()
    win.player.shutdown()
    # 後始末
    for p in paths:
        p.unlink(missing_ok=True)
    pp.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    print("ALL GUI TESTS PASSED")


if __name__ == "__main__":
    main()
