"""オフスクリーンでGUIを起動し、
   バックグラウンド並列読込（非ブロック）・トグル停止・個別フェーダー・
   プリセット保存/読込 を検証する。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

from engine import AudioEngine
from main_window import MainWindow
from models import Preset

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

    eng.close()
    win.player.shutdown()
    # 後始末
    for p in paths:
        p.unlink(missing_ok=True)
    pp.unlink(missing_ok=True)
    print("ALL GUI TESTS PASSED")


if __name__ == "__main__":
    main()
