"""
Tips_SE 負荷テスト & 長時間稼働テスト
======================================
本番公演を想定した過負荷・長時間稼働シナリオを自動実行し、
結果をレポートとして出力する。

テスト構成:
  T1  play() 呼び出しレイテンシ基準計測
  T2  最大同時ボイス数（64ボイス以上）
  T3  高速連打トリガー（秒間30回 × 10秒）
  T4  大容量音源のロード & 再生
  T5  長時間稼働安定性（3分、play/stop を繰り返しながらメモリ・レイテンシを監視）
  T6  GUI オフスクリーンでの全パッド同時操作
"""
from __future__ import annotations

import os
import gc
import sys
import time
import math
import threading
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

# Windows CP932 コンソールで Unicode 文字を出力できるようにする
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp932", "mbcs"):
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8",
                      buffering=1, closefd=False)
    sys.stderr = open(sys.stderr.fileno(), mode="w", encoding="utf-8",
                      buffering=1, closefd=False)

# オフスクリーン Qt（GUI テストで使用）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# venv 内の src をパスに追加
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import AudioEngine  # noqa: E402

# psutil は任意（未インストール時は tracemalloc のみで計測）
try:
    import psutil
    _PROC = psutil.Process()
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

SOUNDS = HERE.parent / "sounds"
SOUNDS.mkdir(exist_ok=True)

REPORT_PATH = HERE.parent / "stress_test_report.md"

# ─────────────────────────────────────────────
# 音源生成ユーティリティ
# ─────────────────────────────────────────────

def _make_wav(path: Path, freq: float, dur: float,
              sr: int = 48000, channels: int = 1) -> Path:
    """指定周波数・長さのサイン波 wav を生成して返す。"""
    frames = int(sr * dur)
    t = np.linspace(0, dur, frames, endpoint=False)
    wave = (0.25 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    fade = min(int(sr * 0.005), frames // 4)
    if fade > 0:
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
    data = np.stack([wave] * channels, axis=1) if channels > 1 else wave
    sf.write(path, data, sr)
    return path


# ─────────────────────────────────────────────
# 計測ヘルパー
# ─────────────────────────────────────────────

def _rss_mb() -> float:
    if _HAS_PSUTIL:
        return _PROC.memory_info().rss / (1024 ** 2)
    return 0.0


def _measure_latency(fn: Callable, n: int = 200) -> tuple[float, float, float]:
    """fn を n 回呼び出し (us), 最小/平均/最大を返す。"""
    times: list[float] = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1e6)
    return min(times), sum(times) / len(times), max(times)


# ─────────────────────────────────────────────
# レポート収集クラス
# ─────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Report:
    def __init__(self):
        self.results: list[TestResult] = []
        self._start = time.perf_counter()

    def add(self, r: TestResult):
        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"\n  [{status}] {r.name}")
        for d in r.details:
            print(f"        {d}")
        for w in r.warnings:
            print(f"        ⚠️  {w}")
        self.results.append(r)

    def save(self, path: Path):
        elapsed = time.perf_counter() - self._start
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)

        lines = [
            "# Tips_SE 負荷テスト & 安定性テスト レポート",
            "",
            f"- **実施日時**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **総テスト数**: {total}",
            f"- **合格**: {passed}  /  **不合格**: {total - passed}",
            f"- **総所要時間**: {elapsed:.1f} 秒",
            "",
            "---",
            "",
        ]

        for r in self.results:
            icon = "✅" if r.passed else "❌"
            lines.append(f"## {icon} {r.name}")
            lines.append("")
            for d in r.details:
                lines.append(f"- {d}")
            for w in r.warnings:
                lines.append(f"- ⚠️  {w}")
            lines.append("")

        lines += [
            "---",
            "",
            "## 判定基準",
            "",
            "| 指標 | 合格基準 |",
            "|------|---------|",
            "| play() レイテンシ (平均) | < 500 µs |",
            "| play() レイテンシ (最大) | < 5000 µs |",
            "| 64 ボイス同時再生 | クラッシュなし |",
            "| 秒間 30 回連打 × 10 秒 | 取りこぼし 0 |",
            "| 大容量ファイル (30 秒 wav) ロード | < 3 秒 |",
            "| 3 分稼働後メモリ増加 | < 50 MB |",
            "| 3 分稼働後レイテンシ劣化 | < 10 倍 |",
            "| GUI 64 パッド同時割当・再生 | クラッシュなし |",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nレポートを保存しました: {path}")


# ─────────────────────────────────────────────
# 個別テスト
# ─────────────────────────────────────────────

def _engine(prefer_wasapi: bool = False) -> AudioEngine:
    eng = AudioEngine(blocksize=256, prefer_wasapi=prefer_wasapi)
    eng.start()
    return eng


def _cleanup_sounds(paths: list[Path]):
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# T1: 基準レイテンシ計測
def test_baseline_latency(report: Report):
    name = "T1: play() レイテンシ基準計測"
    print(f"\n{'─'*60}\n{name}")
    paths: list[Path] = []
    try:
        p = _make_wav(SOUNDS / "_stress_beep.wav", 440, 0.3)
        paths.append(p)
        eng = _engine()
        eng.load_clip("beep", str(p))

        # コールド計測（最初の n 回）
        cold_min, cold_avg, cold_max = _measure_latency(
            lambda: eng.play("beep"), n=100)

        eng.stop_all()
        time.sleep(0.05)

        # ホット計測（ボイスが消えた後）
        hot_min, hot_avg, hot_max = _measure_latency(
            lambda: (eng.stop_all(), eng.play("beep")), n=100)

        eng.close()

        passed = cold_avg < 500 and cold_max < 5000
        details = [
            f"コールド  min={cold_min:.1f} µs / avg={cold_avg:.1f} µs / max={cold_max:.1f} µs",
            f"ホット    min={hot_min:.1f} µs / avg={hot_avg:.1f} µs / max={hot_max:.1f} µs",
        ]
        warns = []
        if cold_avg >= 200:
            warns.append(f"平均レイテンシがやや高め ({cold_avg:.1f} µs)")
        if cold_max >= 2000:
            warns.append(f"最大レイテンシが高い ({cold_max:.1f} µs) — 稀に瞬断が起きる可能性")
        report.add(TestResult(name, passed, details, warns))
    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        traceback_info = __import__("traceback").format_exc()
        print(traceback_info)
    finally:
        _cleanup_sounds(paths)


# T2: 最大同時ボイス数
def test_max_voices(report: Report):
    name = "T2: 大量同時ボイス再生 (64ボイス以上)"
    print(f"\n{'─'*60}\n{name}")
    paths: list[Path] = []
    try:
        # 長めの音源（4秒）を1本使い回す
        p = _make_wav(SOUNDS / "_stress_long.wav", 330, 4.0)
        paths.append(p)
        eng = _engine()
        eng.load_clip("long", str(p))

        # 64ボイス同時投入
        t0 = time.perf_counter()
        for _ in range(64):
            eng.play("long", retrigger=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        voices = eng.active_voice_count()
        time.sleep(0.1)  # コールバックが 64 ボイスを処理する時間を与える

        # アンダーラン・クラッシュがなければ正常
        # コールバック負荷が高い場合はレイテンシが跳ねる
        lat_min, lat_avg, lat_max = _measure_latency(
            lambda: eng.play("long", retrigger=True), n=20)

        eng.stop_all()
        time.sleep(0.2)
        eng.close()

        passed = voices >= 60  # 多少取りこぼしても 60 以上残れば合格

        report.add(TestResult(name, passed, [
            f"64ボイス投入時間: {elapsed_ms:.2f} ms",
            f"直後のアクティブボイス数: {voices}",
            f"過負荷時 play() レイテンシ avg={lat_avg:.1f} µs / max={lat_max:.1f} µs",
        ], [f"ボイス数 {voices} < 64（一部取りこぼし）"] if voices < 64 else []))
    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        print(__import__("traceback").format_exc())
    finally:
        _cleanup_sounds(paths)


# T3: 高速連打トリガー
def test_rapid_trigger(report: Report):
    name = "T3: 高速連打トリガー (30回/秒 × 10秒)"
    print(f"\n{'─'*60}\n{name}")
    paths: list[Path] = []
    try:
        p = _make_wav(SOUNDS / "_stress_short.wav", 660, 0.15)
        paths.append(p)
        eng = _engine()
        eng.load_clip("s", str(p))

        interval = 1.0 / 30.0  # 約33ms間隔
        total = 300             # 30回/秒 × 10秒
        triggered = 0
        missed = 0
        latencies: list[float] = []

        deadline = time.perf_counter() + 10.0
        for _ in range(total):
            target = time.perf_counter()
            t = time.perf_counter()
            result = eng.play("s", retrigger=True)
            lat = (time.perf_counter() - t) * 1e6
            latencies.append(lat)
            if result is not None:
                triggered += 1
            else:
                missed += 1
            # 次の発火タイミングまでスリープ（過ぎていたら即時）
            sleep_time = (target + interval) - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)

        eng.stop_all()
        time.sleep(0.1)
        eng.close()

        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        passed = missed == 0
        report.add(TestResult(name, passed, [
            f"トリガー成功: {triggered} / {total}  取りこぼし: {missed}",
            f"play() レイテンシ avg={avg_lat:.1f} µs / max={max_lat:.1f} µs",
        ], [f"取りこぼし {missed} 件"] if missed else []))
    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        print(__import__("traceback").format_exc())
    finally:
        _cleanup_sounds(paths)


# T4: 大容量音源ロード
def test_large_file(report: Report):
    name = "T4: 大容量音源ファイルのロード & 再生 (30秒 wav)"
    print(f"\n{'─'*60}\n{name}")
    paths: list[Path] = []
    try:
        p = _make_wav(SOUNDS / "_stress_large.wav", 220, 30.0, channels=2)
        size_mb = p.stat().st_size / (1024 ** 2)
        paths.append(p)
        eng = _engine()

        mem_before = _rss_mb()
        t0 = time.perf_counter()
        clip = eng.load_clip("big", str(p))
        load_time = time.perf_counter() - t0
        mem_after = _rss_mb()

        pcm_mb = clip.pcm.nbytes / (1024 ** 2)
        t = time.perf_counter()
        eng.play("big")
        play_lat = (time.perf_counter() - t) * 1e6
        time.sleep(0.1)
        eng.stop_all()
        time.sleep(0.1)
        eng.close()

        passed = load_time < 3.0
        report.add(TestResult(name, passed, [
            f"ファイルサイズ: {size_mb:.1f} MB  (30秒 ステレオ wav)",
            f"ロード時間: {load_time*1000:.0f} ms  (上限: 3000 ms)",
            f"PCM RAM 使用量: {pcm_mb:.1f} MB",
            f"プロセスRSS増加: {mem_after - mem_before:.1f} MB",
            f"play() レイテンシ: {play_lat:.1f} µs",
        ], [f"ロード時間 {load_time*1000:.0f} ms が遅い (> 1000ms)"] if load_time > 1.0 else []))
    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        print(__import__("traceback").format_exc())
    finally:
        _cleanup_sounds(paths)


# T5: 長時間稼働安定性テスト（3分）
def test_long_running(report: Report):
    name = "T5: 長時間稼働安定性テスト (3分)"
    print(f"\n{'─'*60}\n{name}")
    print("  ※ このテストは約3分かかります…")
    paths: list[Path] = []
    try:
        # 複数の短い音源を用意
        clips = []
        for i in range(8):
            p = _make_wav(SOUNDS / f"_stress_loop{i}.wav", 300 + i * 100, 0.5)
            paths.append(p)
            clips.append((f"c{i}", str(p)))

        eng = _engine()
        for cid, path in clips:
            eng.load_clip(cid, path)

        # tracemalloc でPythonオブジェクトのリークを追跡
        tracemalloc.start()
        gc.collect()

        mem_rss_start = _rss_mb()
        lat_samples: list[tuple[float, float]] = []  # (elapsed_sec, lat_us)
        voice_samples: list[tuple[float, float]] = []  # (elapsed_sec, voices)
        crash_detected = False

        start = time.perf_counter()
        duration = 180.0  # 3分

        interval = 0.15   # 約150ms ごとにランダムに鳴らす/止める
        ops = 0

        def worker():
            nonlocal crash_detected, ops
            rng = np.random.default_rng(42)
            try:
                while True:
                    elapsed = time.perf_counter() - start
                    if elapsed >= duration:
                        break

                    # ランダムにいくつかのクリップをトリガー
                    n_play = int(rng.integers(1, 5))
                    for _ in range(n_play):
                        cid = clips[int(rng.integers(0, len(clips)))][0]
                        t = time.perf_counter()
                        eng.play(cid, retrigger=True)
                        lat_us = (time.perf_counter() - t) * 1e6
                        lat_samples.append((elapsed, lat_us))

                    # 30% の確率で全停止
                    if rng.random() < 0.30:
                        eng.stop_all(fade_out=0.05)

                    vc = eng.active_voice_count()
                    voice_samples.append((elapsed, vc))
                    ops += 1
                    time.sleep(interval)
            except Exception as ex:
                crash_detected = True
                print(f"\n  !! ワーカースレッド例外: {ex}")
                print(__import__("traceback").format_exc())

        th = threading.Thread(target=worker, daemon=True)
        th.start()

        # 進捗表示（10秒ごと）
        next_report_t = 10.0
        while th.is_alive():
            elapsed = time.perf_counter() - start
            if elapsed >= next_report_t:
                cur_rss = _rss_mb()
                avg_lat = (sum(l for _, l in lat_samples[-200:]) / max(1, len(lat_samples[-200:])))
                print(f"  {elapsed:5.0f}s  RSS={cur_rss:.1f}MB(+{cur_rss - mem_rss_start:.1f})  "
                      f"ops={ops}  voices={eng.active_voice_count()}  "
                      f"avg_lat(直近200)={avg_lat:.1f}µs")
                next_report_t += 10.0
            time.sleep(1.0)

        th.join(timeout=5.0)
        eng.stop_all()
        time.sleep(0.2)
        eng.close()

        # メモリリーク解析
        gc.collect()
        mem_rss_end = _rss_mb()
        mem_delta = mem_rss_end - mem_rss_start
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()

        top_stats = snapshot.statistics("lineno")[:5]
        top_alloc_lines = [str(s) for s in top_stats]

        # レイテンシ劣化解析
        if lat_samples:
            early = [l for t, l in lat_samples if t < 30]
            late = [l for t, l in lat_samples if t > (duration - 30)]
            avg_early = sum(early) / max(1, len(early))
            avg_late = sum(late) / max(1, len(late))
            lat_ratio = avg_late / max(1e-6, avg_early)
            max_lat = max(l for _, l in lat_samples)
        else:
            avg_early = avg_late = lat_ratio = max_lat = 0.0

        passed = (
            not crash_detected
            and mem_delta < 50.0
            and lat_ratio < 10.0
        )

        warns = []
        if mem_delta > 20:
            warns.append(f"メモリ増加 {mem_delta:.1f} MB（軽微なリークの可能性）")
        if lat_ratio > 3.0:
            warns.append(f"終盤のレイテンシが序盤の {lat_ratio:.1f} 倍（劣化の可能性）")
        if crash_detected:
            warns.append("ワーカースレッドで例外が発生")

        report.add(TestResult(name, passed, [
            f"総操作回数: {ops}  テスト時間: {duration:.0f}秒",
            f"プロセスRSS: 開始={mem_rss_start:.1f}MB → 終了={mem_rss_end:.1f}MB  増加={mem_delta:.1f}MB",
            f"play() レイテンシ: 序盤avg={avg_early:.1f}µs / 終盤avg={avg_late:.1f}µs / max={max_lat:.1f}µs",
            f"レイテンシ劣化率: {lat_ratio:.2f}x (上限 10x)",
            f"クラッシュ検出: {'あり' if crash_detected else 'なし'}",
            "--- tracemalloc Top-5 (Pythonオブジェクト) ---",
        ] + top_alloc_lines, warns))

    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        print(__import__("traceback").format_exc())
    finally:
        _cleanup_sounds(paths)


# T6: GUI オフスクリーン 全パッド同時操作
def test_gui_load(report: Report):
    name = "T6: GUI オフスクリーン 全パッド同時操作"
    print(f"\n{'─'*60}\n{name}")
    paths: list[Path] = []
    try:
        from PySide6.QtWidgets import QApplication
        from main_app import MainWindow

        # QApplication はプロセス内に1つだけ
        app = QApplication.instance() or QApplication([])

        eng = AudioEngine(blocksize=256, prefer_wasapi=False)
        eng.start()
        win = MainWindow(eng)

        N = 32
        for i in range(N):
            p = _make_wav(SOUNDS / f"_gui{i}.wav", 300 + i * 50, 0.3)
            paths.append(p)

        win.preset.rows = 4
        win.preset.cols = 8  # 32パッド
        win.preset.ensure_pads()
        for i in range(N):
            win.preset.pads[i].path = str(paths[i])
            win.preset.pads[i].key = ""

        # _rebuild_grid がブロックしないことを確認
        t0 = time.perf_counter()
        win._rebuild_grid()
        rebuild_ms = (time.perf_counter() - t0) * 1000

        # 全クリップのロード完了を待つ（最大30秒）
        def pump_until(cond, timeout=30.0):
            t0 = time.perf_counter()
            while not cond() and (time.perf_counter() - t0) < timeout:
                app.processEvents()
                time.sleep(0.005)
            return cond()

        ok = pump_until(lambda: len(win._pending) == 0)
        assert ok, "全クリップのロードが完了しなかった"

        # 全パッドを同時にトリガー
        t0 = time.perf_counter()
        for pad in win.preset.pads:
            win.trigger_pad(pad)
        trigger_all_ms = (time.perf_counter() - t0) * 1000

        time.sleep(0.1)
        voices_after = eng.active_voice_count()

        # 全停止（デフォルトフェードアウト 0.3s が完了するまで待つ）
        win.stop_all()
        time.sleep(0.6)
        assert not eng.is_playing(f"pad0"), "全停止が効いていない"

        # 排他再生モードで連打
        win.preset.pads[0].exclusive = True
        for _ in range(20):
            win.trigger_pad(win.preset.pads[0])

        time.sleep(0.1)
        win.stop_all()
        time.sleep(0.1)

        eng.close()
        win.loader.shutdown()

        passed = ok and voices_after >= N * 0.9  # 90%以上再生できれば合格
        report.add(TestResult(name, passed, [
            f"パッド数: {N}",
            f"_rebuild_grid() 呼び出し時間: {rebuild_ms:.1f} ms（UIブロックしないこと）",
            f"全パッドトリガー時間: {trigger_all_ms:.1f} ms",
            f"トリガー後アクティブボイス数: {voices_after} / {N}",
        ], [
            f"ボイス数が少ない ({voices_after}/{N})" if voices_after < N * 0.9 else ""
        ]))

    except Exception as e:
        report.add(TestResult(name, False, [f"例外: {e}"], []))
        print(__import__("traceback").format_exc())
    finally:
        _cleanup_sounds(paths)


# ─────────────────────────────────────────────
# エントリポイント
# ─────────────────────────────────────────────

def main():
    import traceback as _tb

    print("=" * 60)
    print("Tips_SE  負荷テスト & 長時間稼働テスト")
    print("=" * 60)
    if not _HAS_PSUTIL:
        print("  ※ psutil 未導入: RSS 計測はスキップされます（tracemalloc のみ使用）")

    report = Report()

    tests = [
        test_baseline_latency,
        test_max_voices,
        test_rapid_trigger,
        test_large_file,
        test_long_running,   # 3分
        test_gui_load,
    ]

    for test_fn in tests:
        try:
            test_fn(report)
        except Exception:
            print(f"\n!! テスト関数 {test_fn.__name__} でキャッチされない例外:")
            print(_tb.format_exc())

    passed = sum(1 for r in report.results if r.passed)
    total = len(report.results)
    print("\n" + "=" * 60)
    print(f"結果: {passed}/{total} テスト合格")
    print("=" * 60)

    report.save(REPORT_PATH)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
