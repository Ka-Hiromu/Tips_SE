"""
Tips_SE オーディオエンジン
==========================
ONJO の弱点（2秒のラグ／取りこぼし／長時間で落ちる）を構造的に潰すための低遅延ミキサー。

設計の核:
  1. 全音源を起動時に float32 PCM として RAM へ事前デコード（再生時デコードをしない）
  2. WASAPI の出力ストリームを開きっぱなしにし、コールバックでサンプルを流し込む
     （再生のたびにデバイスやオブジェクトを生成/破棄しない → 取りこぼし・リークを防ぐ）
  3. 再生は「アクティブなボイス」リストへ追加するだけ → キー押下から発音までほぼゼロ遅延
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf


def _find_wasapi() -> int | None:
    """WASAPI の hostapi index を返す（なければ None）。"""
    for i, ha in enumerate(sd.query_hostapis()):
        if "WASAPI" in ha["name"]:
            return i
    return None


def _resample(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """高品質・高速リサンプル。soxr があれば使い、無ければ線形補間にフォールバック。

    data: (frames, channels) の float32
    """
    if src_sr == dst_sr:
        return data
    try:
        import soxr
        out = soxr.resample(data, src_sr, dst_sr, quality="HQ")
        return np.ascontiguousarray(out, dtype=np.float32)
    except Exception:
        return _resample_linear(data, src_sr, dst_sr)


def _resample_linear(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """線形補間によるリサンプル。効果音用途には十分な品質。

    data: (frames, channels) の float32
    """
    if src_sr == dst_sr:
        return data
    src_n = data.shape[0]
    dst_n = int(round(src_n * dst_sr / src_sr))
    if dst_n <= 0:
        return np.zeros((0, data.shape[1]), dtype=np.float32)
    # 元の各サンプル位置 0..src_n-1 を、出力 dst_n 点へ写像
    src_idx = np.linspace(0.0, src_n - 1, dst_n)
    out = np.empty((dst_n, data.shape[1]), dtype=np.float32)
    xp = np.arange(src_n)
    for ch in range(data.shape[1]):
        out[:, ch] = np.interp(src_idx, xp, data[:, ch])
    return out


@dataclass
class Clip:
    """事前デコード済みの音源。PCM は常にエンジンのサンプルレート/チャンネル数に統一済み。"""

    clip_id: str
    path: str
    pcm: np.ndarray  # (frames, channels) float32
    samplerate: int

    @property
    def frames(self) -> int:
        return self.pcm.shape[0]

    @property
    def duration(self) -> float:
        return self.frames / self.samplerate if self.samplerate else 0.0


@dataclass
class Voice:
    """再生中の1音。コールバック内でのみ位置を進める。"""

    clip: Clip
    pos: int = 0
    gain: float = 1.0
    loop: bool = False
    # フェード: 残りフレーム数と方向（+1=フェードイン, -1=フェードアウト, 0=なし）
    fade_remaining: int = 0
    fade_total: int = 0
    fade_dir: int = 0
    fade_start_gain: float = 0.0
    fade_target_gain: float = 1.0
    finished: bool = False
    voice_id: int = 0


class AudioEngine:
    """低遅延ソフトミキサー。"""

    def __init__(self, samplerate: int | None = None, channels: int = 2,
                 blocksize: int = 256, prefer_wasapi: bool = True):
        self.channels = channels
        self.blocksize = blocksize  # 小さいほど低遅延（256@48k ≒ 約5ms）

        self._hostapi = _find_wasapi() if prefer_wasapi else None
        # WASAPI を使う場合は、その hostapi のデフォルト出力デバイスを明示指定する
        # （デフォルトデバイスが別 hostapi(MME 等)だと WasapiSettings と衝突するため）
        self._device = None
        if self._hostapi is not None:
            ha = sd.query_hostapis(self._hostapi)
            self._device = ha.get("default_output_device", -1)
            if self._device is None or self._device < 0:
                self._device = None
                self._hostapi = None
        dev_info = sd.query_devices(self._device, kind="output")
        self.samplerate = int(samplerate or dev_info["default_samplerate"])

        self._lock = threading.Lock()
        self._voices: list[Voice] = []
        self._clips: dict[str, Clip] = {}
        self._next_voice_id = 1
        self.master_gain = 1.0
        # 出力レベルメーター用: 直近ブロックのチャンネル別ピーク（0.0-）
        self._meter = np.zeros(self.channels, dtype=np.float32)

        extra = None
        if self._hostapi is not None:
            try:
                extra = sd.WasapiSettings(exclusive=False)
            except Exception:
                extra = None

        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            blocksize=self.blocksize,
            dtype="float32",
            device=self._device,
            callback=self._callback,
            extra_settings=extra,
        )

    # ---- ライフサイクル ----
    def start(self) -> None:
        self._stream.start()

    def close(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()

    # ---- 音源管理 ----
    def load_clip(self, clip_id: str, path: str) -> Clip:
        """音源を読み込み、エンジンのSR/チャンネルへ統一して RAM 常駐させる。"""
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        # チャンネル数を統一（モノ→ステレオ複製 / 多ch→先頭chへ縮約は単純平均）
        if data.shape[1] == 1 and self.channels > 1:
            data = np.repeat(data, self.channels, axis=1)
        elif data.shape[1] != self.channels:
            if data.shape[1] > self.channels:
                data = data[:, : self.channels]
            else:
                pad = np.repeat(data[:, -1:], self.channels - data.shape[1], axis=1)
                data = np.concatenate([data, pad], axis=1)
        data = _resample(data, sr, self.samplerate)
        clip = Clip(clip_id=clip_id, path=str(path), pcm=np.ascontiguousarray(data),
                    samplerate=self.samplerate)
        with self._lock:
            self._clips[clip_id] = clip
        return clip

    def unload_clip(self, clip_id: str) -> None:
        with self._lock:
            self._clips.pop(clip_id, None)
            self._voices = [v for v in self._voices if v.clip.clip_id != clip_id]

    def get_clip(self, clip_id: str) -> Clip | None:
        return self._clips.get(clip_id)

    # ---- 再生制御 ----
    def play(self, clip_id: str, gain: float = 1.0, loop: bool = False,
             fade_in: float = 0.0, exclusive: bool = False,
             retrigger: bool = True) -> int | None:
        """音源を再生。即座にボイスを追加するだけなので発音遅延はほぼゼロ。

        exclusive: True なら再生中の全ボイスを止めてから鳴らす（排他再生）
        retrigger: True なら同一音源が鳴っていても重ねて鳴らす
        """
        clip = self._clips.get(clip_id)
        if clip is None:
            return None
        with self._lock:
            if exclusive:
                for v in self._voices:
                    self._begin_fadeout(v, 0.02)
            elif not retrigger:
                for v in self._voices:
                    if v.clip.clip_id == clip_id and not v.finished:
                        return v.voice_id
            vid = self._next_voice_id
            self._next_voice_id += 1
            voice = Voice(clip=clip, gain=gain, loop=loop, voice_id=vid)
            if fade_in > 0:
                n = int(fade_in * self.samplerate)
                voice.fade_remaining = n
                voice.fade_total = n
                voice.fade_dir = 1
                voice.fade_start_gain = 0.0
                voice.fade_target_gain = gain
            self._voices.append(voice)
        return vid

    def stop(self, clip_id: str, fade_out: float = 0.0) -> None:
        with self._lock:
            for v in self._voices:
                if v.clip.clip_id == clip_id:
                    if fade_out > 0:
                        self._begin_fadeout(v, fade_out)
                    else:
                        v.finished = True

    def stop_all(self, fade_out: float = 0.0) -> None:
        with self._lock:
            for v in self._voices:
                if fade_out > 0:
                    self._begin_fadeout(v, fade_out)
                else:
                    v.finished = True

    def set_clip_gain(self, clip_id: str, gain: float) -> None:
        """再生中の該当ボイスの音量を即時変更する（フェーダー用）。

        フェード中のボイスはフェードの目標値を更新し、それ以外は即反映する。
        """
        with self._lock:
            for v in self._voices:
                if v.clip.clip_id != clip_id or v.finished:
                    continue
                if v.fade_dir != 0 and v.fade_total > 0:
                    v.fade_target_gain = gain
                else:
                    v.gain = gain

    def is_playing(self, clip_id: str) -> bool:
        with self._lock:
            return any(v.clip.clip_id == clip_id and not v.finished
                       for v in self._voices)

    def active_voice_count(self) -> int:
        with self._lock:
            return len(self._voices)

    def get_meter(self) -> np.ndarray:
        """直近の出力ピークレベル（チャンネル別, 0.0-1.0）のコピーを返す。"""
        m = self._meter  # 参照を取得（callbackが差し替えても安全）
        return np.array(m, dtype=np.float32, copy=True)

    def _begin_fadeout(self, v: Voice, seconds: float) -> None:
        n = max(1, int(seconds * self.samplerate))
        # 現在の実効ゲインから 0 へ
        cur = v.gain
        if v.fade_dir != 0 and v.fade_total > 0:
            prog = 1.0 - v.fade_remaining / v.fade_total
            cur = v.fade_start_gain + (v.fade_target_gain - v.fade_start_gain) * prog
        v.fade_remaining = n
        v.fade_total = n
        v.fade_dir = -1
        v.fade_start_gain = cur
        v.fade_target_gain = 0.0

    # ---- オーディオコールバック（リアルタイムスレッド）----
    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        outdata.fill(0.0)
        with self._lock:
            voices = self._voices
            survivors: list[Voice] = []
            for v in voices:
                if v.finished:
                    continue
                written = self._mix_voice(v, outdata, frames)
                if not v.finished and written:
                    survivors.append(v)
            self._voices = survivors
            mg = self.master_gain
        if mg != 1.0:
            outdata *= mg
        # クリップ防止（ソフトリミット）
        np.clip(outdata, -1.0, 1.0, out=outdata)
        # 出力レベル（チャンネル別ピーク）を記録 ─ メーター表示用
        # 参照の差し替えのみなので GUI スレッドからのロックなし読み取りで安全
        self._meter = np.max(np.abs(outdata), axis=0)

    def _mix_voice(self, v: Voice, outdata: np.ndarray, frames: int) -> bool:
        """1ボイスを outdata に加算。まだ生存していれば True。"""
        clip = v.clip
        filled = 0
        out_pos = 0
        while filled < frames:
            remain_clip = clip.frames - v.pos
            if remain_clip <= 0:
                if v.loop:
                    v.pos = 0
                    continue
                v.finished = True
                break
            n = min(frames - filled, remain_clip)
            seg = clip.pcm[v.pos:v.pos + n]
            # ゲイン/フェード適用
            if v.fade_dir != 0 and v.fade_total > 0:
                # このセグメント分のフェード係数を生成
                start_prog = 1.0 - v.fade_remaining / v.fade_total
                step = 1.0 / v.fade_total
                progs = start_prog + step * np.arange(n)
                np.clip(progs, 0.0, 1.0, out=progs)
                env = (v.fade_start_gain
                       + (v.fade_target_gain - v.fade_start_gain) * progs)
                env = env.astype(np.float32)[:, None]
                outdata[out_pos:out_pos + n] += seg * env
                v.fade_remaining -= n
                if v.fade_remaining <= 0:
                    v.gain = v.fade_target_gain
                    v.fade_dir = 0
                    v.fade_total = 0
                    if v.gain <= 0.0:
                        v.finished = True
            else:
                if v.gain != 1.0:
                    outdata[out_pos:out_pos + n] += seg * v.gain
                else:
                    outdata[out_pos:out_pos + n] += seg
            v.pos += n
            filled += n
            out_pos += n
            if v.finished:
                break
        return not v.finished
