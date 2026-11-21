"""パッドとオーディオエンジンの仲介。

GUI を一切持たないので、ここだけを見れば「どのパッドがどの音を鳴らすか」が分かる。
clip_id の命名規約（"pad{i}" / 確率モードは "pad{i}_s{j}"）を知るのはこのモジュールだけ。
"""
from __future__ import annotations

import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from engine import AudioEngine
from models import Pad

log = logging.getLogger("tips_se")

_CLIP_PREFIX = "pad"


class TriggerResult(Enum):
    """trigger() の結果。UI 側が「なぜ鳴らなかったか」を伝えるために使う。"""
    PLAYED = "played"
    UNASSIGNED = "unassigned"   # 未割当（音源なし）
    LOADING = "loading"         # まだ読み込みが終わっていない


@dataclass
class PadState:
    """1パッドの再生状況のスナップショット。"""
    progress: float                     # 0.0-1.0
    fade: tuple[int, float] | None      # (方向 +1/-1, エンベロープ強度) / 無ければ None


class ClipLoader(QObject):
    """音源デコードをUIスレッドから外し、複数ファイルを並列読み込みする。

    soxr/soundfile は重い処理中に GIL を解放するため、スレッドプールで実効的に
    高速化できる。完了は Qt シグナルで主スレッドへ通知される（スレッド安全）。
    """

    finished = Signal(str, bool, str, int)  # clip_id, ok, error, generation

    def __init__(self, engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        workers = min(8, (os.cpu_count() or 4))
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="clip-loader")

    def submit(self, clip_id: str, path: str, generation: int,
               start: float = 0.0, end: float = 0.0) -> None:
        self._pool.submit(self._work, clip_id, path, generation, start, end)

    def _work(self, clip_id: str, path: str, generation: int,
              start: float, end: float) -> None:
        try:
            self.engine.load_clip(clip_id, path, start, end)
            self.finished.emit(clip_id, True, "", generation)
        except Exception as e:  # noqa: BLE001
            self.finished.emit(clip_id, False, str(e), generation)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class PadPlayer(QObject):
    """パッド操作をエンジンのクリップ操作へ翻訳し、読み込み状態を管理する。"""

    loadingChanged = Signal(int, bool)   # pad_index, 読み込み中か
    progressChanged = Signal(int, int)   # 完了数, 総数（総数0なら読み込み無し）
    loadFailed = Signal(list)            # 人間向けエラーメッセージのリスト

    def __init__(self, engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._loader = ClipLoader(engine, self)
        self._loader.finished.connect(self._on_clip_loaded)
        self._generation = 0
        self._pending: set[str] = set()
        self._pending_total = 0
        self._errors: list[str] = []

    @property
    def pending_count(self) -> int:
        """まだ読み込みが終わっていないクリップ数。"""
        return len(self._pending)

    @property
    def pending_total(self) -> int:
        """いま進行中の読み込みバッチの総数（完了後は0）。"""
        return self._pending_total

    # ---- clip_id の規約 ----
    @staticmethod
    def clip_id(pad: Pad) -> str:
        """パッドの代表 clip_id（単一音源のとき実際に使われるID）。"""
        return f"{_CLIP_PREFIX}{pad.index}"

    @staticmethod
    def pad_index_of(clip_id: str) -> int:
        """clip_id からパッド番号を復元する。解釈できなければ -1。"""
        if not clip_id.startswith(_CLIP_PREFIX):
            return -1
        s = clip_id[len(_CLIP_PREFIX):].split("_", 1)[0]
        return int(s) if s.isdigit() else -1

    @staticmethod
    def clip_specs(pad: Pad) -> list[tuple[str, str]]:
        """(clip_id, path) のリスト。確率モードなら全音源、通常なら単一。"""
        if pad.random_active:
            return [(f"{_CLIP_PREFIX}{pad.index}_s{j}", s.path)
                    for j, s in enumerate(pad.sources) if s.path]
        if pad.path:
            return [(f"{_CLIP_PREFIX}{pad.index}", pad.path)]
        return []

    @classmethod
    def clip_ids(cls, pad: Pad) -> list[str]:
        return [cid for cid, _ in cls.clip_specs(pad)]

    # ---- 再生 ----
    def pick_clip(self, pad: Pad) -> str | None:
        """再生するクリップを選ぶ。確率モードは重み付きランダム。"""
        specs = self.clip_specs(pad)
        if not specs:
            return None
        if pad.random_active and len(specs) > 1:
            weights = [max(0.0, s.prob) for s in pad.sources if s.path]
            if sum(weights) <= 0:
                weights = [1.0] * len(specs)
            return random.choices([cid for cid, _ in specs], weights=weights, k=1)[0]
        return specs[0][0]

    def trigger(self, pad: Pad) -> TriggerResult:
        """パッドを発音する。鳴らせなかった場合は理由を戻り値で返す。"""
        if not pad.assigned:
            return TriggerResult.UNASSIGNED
        cid = self.pick_clip(pad)
        if cid is None:
            return TriggerResult.UNASSIGNED
        if self.engine.get_clip(cid) is None:
            return TriggerResult.LOADING
        self.engine.play(cid, gain=pad.gain, loop=pad.loop,
                         fade_in=pad.fade_in, exclusive=pad.exclusive,
                         retrigger=True)
        return TriggerResult.PLAYED

    def stop(self, pad: Pad, fade_out: float | None = None) -> None:
        sec = pad.fade_out if fade_out is None else fade_out
        for c in self.clip_ids(pad):
            self.engine.stop(c, sec)

    def stop_all(self, fade_out: float = 0.0) -> None:
        self.engine.stop_all(fade_out=fade_out)

    def set_gain(self, pad: Pad, gain: float) -> None:
        for c in self.clip_ids(pad):
            self.engine.set_clip_gain(c, gain)

    def is_playing(self, pad: Pad) -> bool:
        return any(self.engine.is_playing(c) for c in self.clip_ids(pad))

    def is_ready(self, pad: Pad) -> bool:
        """このパッドの音源がすべて常駐済みか（音源が1つも無ければ False）。"""
        ids = self.clip_ids(pad)
        return bool(ids) and all(self.engine.get_clip(c) is not None for c in ids)

    def snapshot(self, pads: list[Pad]) -> dict[int, PadState]:
        """再生中パッドの状態をまとめて取得する（エンジンのロックは1回だけ）。"""
        snap = self.engine.voice_snapshot()
        out: dict[int, PadState] = {}
        for pad in pads:
            prog: float | None = None
            fade: tuple[int, float] | None = None
            for c in self.clip_ids(pad):
                st = snap.get(c)
                if st is None:
                    continue
                if prog is None or st[0] > prog:
                    prog = st[0]
                # フェード中(dir!=0)のボイスを優先して見せる
                if st[1] != 0 and (fade is None or fade[0] == 0):
                    fade = (st[1], st[2])
            if prog is not None:
                out[pad.index] = PadState(progress=prog, fade=fade)
        return out

    # ---- 読み込み ----
    def unload(self, pad: Pad) -> None:
        pre = f"{_CLIP_PREFIX}{pad.index}"
        for cid in self.engine.loaded_clip_ids():
            if cid == pre or cid.startswith(pre + "_"):
                self.engine.unload_clip(cid)

    def reload_all(self, pads: list[Pad]) -> None:
        """全クリップをバックグラウンドで（再）読み込みする。UIはブロックしない。"""
        # 世代を進め、進行中の古い完了通知を無効化する
        self._generation += 1
        self._pending.clear()
        self._errors.clear()

        for cid in self.engine.loaded_clip_ids():
            self.engine.unload_clip(cid)

        # 再生範囲はパッドごとなので Pad も一緒に持ち回す
        to_load: list[tuple[str, str, Pad]] = []
        for p in pads:
            if not p.assigned:
                continue
            for cid, path in self.clip_specs(p):
                if Path(path).exists():
                    to_load.append((cid, path, p))
                else:
                    self._errors.append(
                        f"パッド{p.index + 1}: ファイルが見つかりません {path}")

        self._pending = {cid for cid, _, _ in to_load}
        self._pending_total = len(to_load)
        for p in pads:
            if p.assigned:
                self.loadingChanged.emit(p.index, True)
        for cid, path, p in to_load:
            self._loader.submit(cid, path, self._generation, p.start, p.end)
        self._emit_progress()

    def reload_pad(self, pad: Pad) -> list[str]:
        """1パッドだけ読み直す。見つからなかったパスのリストを返す。"""
        self.unload(pad)
        specs = self.clip_specs(pad)
        real = [(cid, path) for cid, path in specs if Path(path).exists()]
        missing = [path for _, path in specs if not Path(path).exists()]
        if not real:
            self.loadingChanged.emit(pad.index, False)
            return missing
        self.loadingChanged.emit(pad.index, True)
        self._pending.update(cid for cid, _ in real)
        self._pending_total += len(real)
        for cid, path in real:
            self._loader.submit(cid, path, self._generation, pad.start, pad.end)
        self._emit_progress()
        return missing

    def _on_clip_loaded(self, clip_id: str, ok: bool, err: str,
                        generation: int) -> None:
        # 古い世代の完了は破棄（再読込が重なった場合の取り違え防止）
        if generation != self._generation:
            return
        idx = self.pad_index_of(clip_id)
        if not ok:
            self._errors.append(f"パッド{idx + 1}: 読み込み失敗 ({err})")
        self._pending.discard(clip_id)
        # 同じパッドの残り音源がもう無ければ「読み込み中」を解除
        if idx >= 0 and not any(self.pad_index_of(c) == idx for c in self._pending):
            self.loadingChanged.emit(idx, False)
        self._emit_progress()

    def _emit_progress(self) -> None:
        remaining = len(self._pending)
        if remaining > 0:
            self.progressChanged.emit(self._pending_total - remaining,
                                      self._pending_total)
            return
        # 全部終わったのでカウンタを畳む（次の部分読み込みが 0/N から始まるように）
        self._pending_total = 0
        self.progressChanged.emit(0, 0)
        if self._errors:
            errs = self._errors[:]
            self._errors.clear()
            self.loadFailed.emit(errs)

    def shutdown(self) -> None:
        self._loader.shutdown()
