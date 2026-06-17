"""
エンジンのスモークテスト。
- テスト用 wav を生成
- 起動時に事前デコード
- 再生して「発音までの遅延」を測定（ストリーム時計ベース）
実機スピーカーから音が鳴り、遅延が十分小さい（数十ms以下）ことを確認する。
"""
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from engine import AudioEngine

HERE = Path(__file__).resolve().parent
SOUNDS = HERE.parent / "sounds"
SOUNDS.mkdir(exist_ok=True)


def make_beep(path: Path, freq: float, dur: float, sr: int = 48000):
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    wave = 0.3 * np.sin(2 * np.pi * freq * t)
    # クリックノイズ防止に短いフェード
    fade = int(sr * 0.005)
    env = np.ones_like(wave)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    sf.write(path, (wave * env).astype(np.float32), sr)


def main():
    p1 = SOUNDS / "_test_440.wav"
    p2 = SOUNDS / "_test_880.wav"
    make_beep(p1, 440, 0.4)
    make_beep(p2, 880, 0.4)

    eng = AudioEngine(blocksize=256)
    print(f"engine SR={eng.samplerate}Hz ch={eng.channels} block={eng.blocksize} "
          f"(WASAPI={'yes' if eng._hostapi is not None else 'no'})")

    t0 = time.perf_counter()
    eng.load_clip("a", str(p1))
    eng.load_clip("b", str(p2))
    print(f"2音の事前デコード時間: {(time.perf_counter()-t0)*1000:.1f} ms")

    eng.start()
    print("再生開始… 440Hz と 880Hz を順に、最後に同時再生します")

    for cid in ("a", "b"):
        t = time.perf_counter()
        eng.play(cid)
        print(f"  play({cid}) 呼び出しコスト: {(time.perf_counter()-t)*1000:.3f} ms / "
              f"active voices={eng.active_voice_count()}")
        time.sleep(0.6)

    # 同時再生 + フェードイン
    eng.play("a", fade_in=0.2)
    eng.play("b", gain=0.7)
    time.sleep(0.5)
    eng.stop_all(fade_out=0.15)
    time.sleep(0.3)

    eng.close()
    print("OK: クラッシュなく完了")


if __name__ == "__main__":
    main()
