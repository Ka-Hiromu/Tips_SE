"""パッドグリッドとメーターまわりの自作ウィジェット。

いずれも MainWindow を直接呼ばず、操作はシグナルで外へ通知する
（ウィジェット単体でテストでき、画面側の都合が漏れ込まないようにするため）。
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from appconfig import SOUND_FILE_FILTER
from engine import AudioEngine
from models import Pad, Sound
from theme import t, fader_qss


class PieChart(QWidget):
    """確率配分の小さな円グラフ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(124, 124)
        self._values: list[float] = []

    def set_values(self, values: list[float]):
        self._values = list(values)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(8, 8, self.width() - 16, self.height() - 16)
        total = sum(v for v in self._values if v > 0)
        if total <= 0:
            p.setPen(Qt.NoPen); p.setBrush(QColor(t().pie_empty)); p.drawEllipse(rect)
            p.end(); return
        start = 90 * 16  # 上(12時)から時計回り
        for i, v in enumerate(self._values):
            if v <= 0:
                continue
            span = -int(round(360 * 16 * v / total))
            colors = t().pie_colors
            p.setBrush(QColor(colors[i % len(colors)]))
            p.setPen(QColor(t().pie_edge))
            p.drawPie(rect, start, span)
            start += span
        p.end()


class SourcesEditor(QWidget):
    """確率モードの複数音源エディタ（音源行 + 確率 + 円グラフ + 補完）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        # 左に音源リスト＋追加ボタン、右に円グラフ（横並びで縦伸びを防ぐ）
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.rows_host = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_host)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(4)
        left.addWidget(self.rows_host)
        add_btn = QPushButton("＋ 音源を追加")
        add_btn.clicked.connect(lambda: self.add_row())
        left.addWidget(add_btn, 0, Qt.AlignLeft)
        left.addStretch(1)
        main.addLayout(left, 1)

        self.pie = PieChart()
        main.addWidget(self.pie, 0, Qt.AlignTop)

    def set_sources(self, sources: list):
        for r in self.rows:
            r["w"].deleteLater()
        self.rows = []
        if sources:
            for s in sources:
                self.add_row(s.path, s.prob)
        else:
            self.add_row(); self.add_row()
        self._update_pie()

    def add_row(self, path: str = "", prob: float = 0.0):
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        pe = QLineEdit(path); pe.setPlaceholderText("音源ファイル")
        br = QPushButton("…"); br.setFixedWidth(30)
        pr = QLineEdit(); pr.setFixedWidth(52); pr.setPlaceholderText("%")
        pr.setAlignment(Qt.AlignRight)
        if prob and prob > 0:
            pr.setText(f"{prob:g}")
        rm = QPushButton("×"); rm.setFixedWidth(30)
        h.addWidget(pe, 1); h.addWidget(br); h.addWidget(pr)
        h.addWidget(QLabel("%")); h.addWidget(rm)
        row = {"w": w, "path": pe, "prob": pr}
        br.clicked.connect(lambda: self._browse(pe))
        rm.clicked.connect(lambda: self._remove(row))
        pe.textChanged.connect(self._update_pie)
        pr.textChanged.connect(self._update_pie)
        self.rows_lay.addWidget(w)
        self.rows.append(row)
        self._update_pie()

    def _browse(self, pe: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "音源を選択", "", SOUND_FILE_FILTER)
        if path:
            pe.setText(path)

    def _remove(self, row: dict):
        if len(self.rows) <= 1:
            return
        row["w"].deleteLater()
        self.rows.remove(row)
        self._update_pie()

    @staticmethod
    def _parse(txt: str):
        txt = txt.strip()
        if txt == "":
            return None
        try:
            return max(0.0, float(txt))
        except ValueError:
            return None

    def _active_rows(self) -> list[dict]:
        return [r for r in self.rows if r["path"].text().strip()]

    def _update_pie(self):
        vals = []
        for r in self._active_rows():
            v = self._parse(r["prob"].text())
            vals.append(v if v is not None else 0.0)
        if vals and sum(vals) <= 0:   # 全空欄=等確率として可視化
            vals = [1.0] * len(vals)
        self.pie.set_values(vals)

    def get_sources(self) -> list:
        active = self._active_rows()
        probs = [self._parse(r["prob"].text()) for r in active]
        blanks = [i for i, v in enumerate(probs) if v is None]
        if len(blanks) == 1:   # 1つだけ空欄 → 残りで補完
            others = sum(v for v in probs if v is not None)
            probs[blanks[0]] = max(0.0, 100.0 - others)
        probs = [(v if v is not None else 0.0) for v in probs]
        if active and sum(probs) <= 0:   # 全0 → 等確率
            probs = [100.0 / len(active)] * len(active)
        out = []
        for r, pr in zip(active, probs):
            path = r["path"].text().strip()
            out.append(Sound(path=path, label=Path(path).stem, prob=pr))
        return out


class PadCell(QWidget):
    """1パッド = 再生ボタン ＋ 個別音量フェーダーの複合ウィジェット。"""

    toggleRequested = Signal(int)    # ボタンクリック（再生/停止のトグル）
    triggerRequested = Signal(int)   # メニューの「再生」
    stopRequested = Signal(int)
    editRequested = Signal(int)
    clearRequested = Signal(int)
    gainChanged = Signal(int, float)

    def __init__(self, pad: Pad, show_fade_sec: bool = True, parent=None):
        super().__init__(parent)
        self.pad = pad
        self.is_loading = False
        self.show_fade_sec = show_fade_sec
        self._last_paint_key = None  # 直近に適用した再生表示状態（冗長なsetStyleSheet抑制）
        self._fade_dir_shown = None  # 直近に描いたフェード強調の向き
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(120, 112)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.button = QPushButton()
        # サイズを内容(テキスト)に依存させない → 名前が長くてもパッドの形は不変
        self.button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.button.customContextMenuRequested.connect(
            lambda pos: self._menu(self.button.mapTo(self, pos)))
        self.button.clicked.connect(self._on_click)
        # ボタン上にラベルを重ねる（同じセルに2枚スタック）
        blay = QGridLayout(self.button)
        blay.setContentsMargins(8, 8, 8, 8)
        # 割当キーを大きく薄く表示（背景・直感的にキーが分かる）
        self.key_lbl = QLabel()
        self.key_lbl.setAlignment(Qt.AlignCenter)
        self.key_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        kf = QFont("Noto Sans JP"); kf.setPointSize(60); kf.setBold(True)
        self.key_lbl.setFont(kf)
        self.key_lbl.setStyleSheet(
            f"background:transparent;color:{t().pad_key_overlay};")
        blay.addWidget(self.key_lbl, 0, 0)
        # 名前（前景・折り返し対応）
        self.text_lbl = QLabel()
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAlignment(Qt.AlignCenter)
        self.text_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.text_lbl.setStyleSheet("background:transparent;")
        blay.addWidget(self.text_lbl, 0, 0)
        # ボタン下部のオプション表示区域。各機能の記号は位置を固定し、
        # 有効なものだけをそこに出す（左=モード / 右=フェード）。
        self.opt_bar = QWidget()
        self.opt_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.opt_bar.setStyleSheet("background:transparent;")
        ob = QHBoxLayout(self.opt_bar)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(6)
        self.mode_lbl = QLabel()   # ⟳ / EXCL / 連
        self.mode_lbl.setStyleSheet(
            f"background:transparent;font-size:11px;color:{t().text_dim};")
        self.fade_lbl = QLabel()   # ◢ イン / ◣ アウト
        self.fade_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fade_lbl.setStyleSheet("background:transparent;font-size:11px;")
        ob.addWidget(self.mode_lbl)
        ob.addStretch(1)
        ob.addWidget(self.fade_lbl)
        blay.addWidget(self.opt_bar, 0, 0, Qt.AlignBottom)
        lay.addWidget(self.button, 1)

        # 個別音量フェーダー
        fader_row = QHBoxLayout()
        fader_row.setContentsMargins(4, 2, 4, 2)
        fader_row.setSpacing(4)
        self.fader = QSlider(Qt.Horizontal)
        self.fader.setRange(0, 150)
        self.fader.setValue(int(pad.gain * 100))
        self.fader.setStyleSheet(fader_qss(t()))
        self.fader.setMinimumHeight(22)   # 太くしたハンドルが潰れない高さを確保
        self.fader.setToolTip("このパッドの音量")
        self.fader.valueChanged.connect(self._on_fader)
        self.vol_lbl = QLabel(f"{int(pad.gain * 100)}%")
        self.vol_lbl.setMinimumWidth(38)
        self.vol_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.vol_lbl.setStyleSheet(f"font-size:11px;color:{t().text_dim};")
        fader_row.addWidget(self.fader, 1)
        fader_row.addWidget(self.vol_lbl)
        self.fader_widget = QWidget()
        self.fader_widget.setLayout(fader_row)
        lay.addWidget(self.fader_widget)

        self.refresh()

    def apply_theme(self):
        th = t()
        self.key_lbl.setStyleSheet(
            f"background:transparent;color:{th.pad_key_overlay};")
        self.mode_lbl.setStyleSheet(
            f"background:transparent;font-size:11px;color:{th.text_dim};")
        self.vol_lbl.setStyleSheet(f"font-size:11px;color:{th.text_dim};")
        self.fader.setStyleSheet(fader_qss(th))
        self._last_paint_key = None
        self._fade_dir_shown = None
        self.refresh()

    def set_show_fade_sec(self, on: bool):
        self.show_fade_sec = on
        self._fade_dir_shown = None
        self._update_fade_label(0)

    def sync_gain(self):
        """pad.gain の外部変更をフェーダー表示へ反映する（リモート操作など）。"""
        self.fader.blockSignals(True)
        self.fader.setValue(int(self.pad.gain * 100))
        self.fader.blockSignals(False)
        self.vol_lbl.setText(f"{int(self.pad.gain * 100)}%")

    def set_loading(self, loading: bool):
        """読み込み中表示の切替。"""
        self.is_loading = loading
        self._last_paint_key = None
        if loading:
            self.text_lbl.setText(f"⏳ 読み込み中…\n{self.pad.display_name()}")
            self.text_lbl.setStyleSheet(
                f"background:transparent;color:{t().accent_text};")
            self.key_lbl.setText("")
            self.opt_bar.setVisible(False)
            self._fade_dir_shown = None
            self.fader_widget.setVisible(False)
            th = t()
            self.button.setStyleSheet(
                f"QPushButton{{background:{th.pad_load_bg};"
                f"border:1px solid {th.pad_load_border};"
                "border-radius:8px;font-size:12px;}}")
        else:
            self.refresh()

    def refresh(self):
        p = self.pad
        if self.is_loading:
            return
        self._last_paint_key = None  # 末尾の set_playing(False) を確実に再適用させる
        if p.assigned:
            self.text_lbl.setText(p.display_name())
            self.key_lbl.setText(p.key.upper() if p.key else "")
            tags = []
            if p.loop:
                tags.append("⟳")
            if p.exclusive:
                tags.append("EXCL")
            if p.retrigger:
                tags.append("連")
            if p.start > 0 or p.end > 0:
                tags.append("範")
            self.mode_lbl.setText(" ".join(tags))
            self.fader_widget.setVisible(True)
            self.opt_bar.setVisible(True)
        else:
            self.text_lbl.setText("＋")
            self.key_lbl.setText("")
            self.mode_lbl.setText("")
            self.fader_widget.setVisible(False)
            self.opt_bar.setVisible(False)
        self.sync_gain()
        self._fade_dir_shown = None
        self._update_fade_label(0)
        self.set_playing(False)

    def _update_fade_label(self, fade_dir: int):
        """フェード表示を更新。fade_dir(+1/-1) の側だけ明るくして進行中を示す。"""
        if self._fade_dir_shown == fade_dir:
            return
        self._fade_dir_shown = fade_dir
        p = self.pad
        if not p.assigned or (p.fade_in <= 0 and p.fade_out <= 0):
            self.fade_lbl.setText("")
            return
        parts = []
        for mark, sec, active in (("◢", p.fade_in, fade_dir > 0),
                                  ("◣", p.fade_out, fade_dir < 0)):
            if sec <= 0:
                continue
            color = t().text if active else t().text_dim
            body = f"{mark}&nbsp;{sec:g}s" if self.show_fade_sec else mark
            parts.append(f'<span style="color:{color};">{body}</span>')
        self.fade_lbl.setText("&nbsp;&nbsp;".join(parts))

    @staticmethod
    def _lerp_hex(c1: str, c2: str, ratio: float) -> str:
        """2つの #rrggbb を ratio(0-1) で線形補間する。"""
        ratio = max(0.0, min(1.0, ratio))
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(round(a[k] + (b[k] - a[k]) * ratio)
                                       for k in range(3))

    def set_playing(self, playing: bool, progress: float = 0.0,
                    fade: tuple[int, float] | None = None):
        if self.is_loading:
            return
        self._update_fade_label(fade[0] if (playing and fade) else 0)
        # 冗長な setStyleSheet を避ける: 描画状態が前回と同じなら何もしない。
        # （再生中は80msごとに呼ばれ、Qtのスタイル再パース＆確保が常時走るのを防ぐ）
        if playing:
            p = max(0.0, min(1.0, progress))
            level = fade[1] if (fade and fade[0] != 0) else 1.0
            key = (1, round(p, 3), round(level, 2))
        elif self.pad.assigned:
            key = (0,)
        else:
            key = (-1,)
        if key == self._last_paint_key:
            return
        self._last_paint_key = key
        th = t()
        if playing:
            # 再生位置を左→右に「濃い緑」で塗るプログレスバー表現。
            # フェード中はその強度(0-1)で緑の明度を変え、薄→濃(イン)/濃→薄(アウト)を可視化する。
            p2 = min(1.0, p + 0.001)
            played = self._lerp_hex(th.pad_played_lo, th.pad_played_hi, level)
            unplayed = self._lerp_hex(th.pad_unplayed_lo, th.pad_unplayed_hi, level)
            self.button.setStyleSheet(
                "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"stop:0 {unplayed}, stop:{p:.3f} {unplayed},"
                f"stop:{p2:.3f} {played}, stop:1 {played});"
                f"border:2px solid {th.pad_play_border};border-radius:8px;}}")
            self.text_lbl.setStyleSheet(
                f"background:transparent;color:{th.pad_play_text};"
                "font-size:13px;font-weight:bold;")
        elif self.pad.assigned:
            self.button.setStyleSheet(
                f"QPushButton{{background:{th.pad_bg};border:1px solid {th.pad_border};"
                "border-radius:8px;}"
                f"QPushButton:hover{{background:{th.pad_hover};}}")
            self.text_lbl.setStyleSheet(
                f"background:transparent;color:{th.pad_text};font-size:13px;")
        else:
            self.button.setStyleSheet(
                f"QPushButton{{background:{th.pad_empty_bg};"
                f"border:1px dashed {th.pad_empty_border};border-radius:8px;}}"
                f"QPushButton:hover{{background:{th.pad_hover};}}")
            self.text_lbl.setStyleSheet(
                f"background:transparent;color:{th.pad_empty_text};font-size:22px;")

    def _on_fader(self, value: int):
        gain = value / 100.0
        self.pad.gain = gain
        self.vol_lbl.setText(f"{value}%")
        self.gainChanged.emit(self.pad.index, gain)

    def _on_click(self):
        idx = self.pad.index
        if self.pad.assigned:
            self.toggleRequested.emit(idx)
        else:
            self.editRequested.emit(idx)

    def _menu(self, pos):
        idx = self.pad.index
        m = QMenu(self)
        if self.pad.assigned:
            m.addAction("▶ 再生", lambda: self.triggerRequested.emit(idx))
            m.addAction("■ この音を停止", lambda: self.stopRequested.emit(idx))
            m.addSeparator()
        m.addAction("設定…", lambda: self.editRequested.emit(idx))
        if self.pad.assigned:
            m.addAction("クリア", lambda: self.clearRequested.emit(idx))
        m.exec(self.mapToGlobal(pos))


class LevelMeter(QWidget):
    """音響卓風の出力レベルメーター
    （L+R統合の1本表示。dB目盛り・ピークホールド・クリップLED付き）。"""

    MIN_DB = -60.0          # 表示下限
    RELEASE = 0.78          # バーの落下（小さいほど速く落ちる）
    PEAK_HOLD_TICKS = 18    # ピークホールド保持フレーム数
    PEAK_FALL = 0.965       # ピークマーカーの落下係数
    REFRESH_MS = 33         # 約30fps

    # dB目盛り（ラベル表示）
    SCALE_DB = [0, -3, -6, -12, -20, -30, -45, -60]

    def __init__(self, engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._disp = 0.0       # 表示レベル(線形)
        self._peak = 0.0       # ピークホールド(線形)
        self._peak_age = 0
        self._clip = False     # クリップ点灯
        self._clip_age = 0
        # 直近に描画した値（変化時のみ再描画するための比較用）
        self._last_disp = -1.0
        self._last_peak = -1.0
        self._last_clip = None

        self.setFixedWidth(78)
        self.setMinimumHeight(300)
        self.setStyleSheet(f"background:{t().meter_panel};")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.REFRESH_MS)

    # ---- 値の更新（ballistics: 速いアタック・緩い解放） ----
    def _tick(self):
        levels = self.engine.get_meter()
        lv = float(max(levels)) if len(levels) else 0.0  # L+R統合（ピーク）
        self._disp = max(lv, self._disp * self.RELEASE)
        if lv >= self._peak:
            self._peak = lv
            self._peak_age = 0
        else:
            self._peak_age += 1
            if self._peak_age > self.PEAK_HOLD_TICKS:
                self._peak = max(self._disp, self._peak * self.PEAK_FALL)
        if lv >= 0.999:
            self._clip = True
            self._clip_age = 0
        elif self._clip:
            self._clip_age += 1
            if self._clip_age > 60:  # 約2秒で消灯
                self._clip = False
        # 描画値が変化した時だけ再描画（無音・静止時の30fps描画を止める）
        if (abs(self._disp - self._last_disp) > 1e-4
                or abs(self._peak - self._last_peak) > 1e-4
                or self._clip != self._last_clip):
            self._last_disp = self._disp
            self._last_peak = self._peak
            self._last_clip = self._clip
            self.update()

    # ---- 描画 ----
    def _db_to_frac(self, level: float) -> float:
        if level <= 1e-7:
            return 0.0
        db = 20.0 * math.log10(level)
        frac = (db - self.MIN_DB) / (0.0 - self.MIN_DB)
        return max(0.0, min(1.0, frac))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        W, H = self.width(), self.height()

        top = 22
        bottom = 18
        clip_h = 12
        meter_top = top + clip_h + 2
        meter_bottom = H - bottom
        usable = meter_bottom - meter_top

        th = t()
        p.setPen(QColor(th.meter_scale))
        f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(0, 4, W, 16, Qt.AlignHCenter | Qt.AlignVCenter, "OUT")

        scale_w = 26
        x = scale_w
        bar_w = W - scale_w - 6

        # dB目盛り
        f2 = QFont(); f2.setPointSize(7); p.setFont(f2)
        for db in self.SCALE_DB:
            frac = (db - self.MIN_DB) / (0.0 - self.MIN_DB)
            y = int(meter_bottom - frac * usable)
            p.setPen(QColor(th.meter_grid_major))
            p.drawText(0, y - 7, scale_w - 3, 14,
                       Qt.AlignRight | Qt.AlignVCenter, f"{db}")
            p.setPen(QColor(th.meter_grid_minor))
            p.drawLine(scale_w - 2, y, W - 4, y)

        # トラック背景
        p.fillRect(x, meter_top, bar_w, usable, QColor(th.meter_track))

        # バーの色は音量判断そのものなので、テーマを問わず固定
        grad = QLinearGradient(0, meter_bottom, 0, meter_top)
        for stop, col in zip(th.meter.grad_stops, th.meter.grad):
            grad.setColorAt(stop, QColor(col))

        frac = self._db_to_frac(self._disp)
        lv_h = int(frac * usable)
        if lv_h > 0:
            p.fillRect(QRectF(x, meter_bottom - lv_h, bar_w, lv_h), grad)

        # セグメント感
        p.setPen(QColor(0, 0, 0, 90))
        yy = meter_top
        while yy < meter_bottom:
            p.drawLine(x, yy, x + bar_w, yy)
            yy += 4

        # ピークホールド
        pf = self._db_to_frac(self._peak)
        if pf > 0:
            py = int(meter_bottom - pf * usable)
            p.setPen(QPen(QColor(th.meter.peak), 2))
            p.drawLine(x, py, x + bar_w, py)

        p.setPen(QColor(th.meter_frame))
        p.drawRect(x, meter_top, bar_w, usable)

        # クリップLED
        led = QColor(th.meter.clip_on if self._clip else th.meter.clip_off)
        p.fillRect(x, top, bar_w, clip_h, led)
        p.setPen(QColor(th.meter_frame)); p.drawRect(x, top, bar_w, clip_h)

        # ラベル
        p.setPen(QColor(th.meter_scale))
        p.setFont(f2)
        p.drawText(x, meter_bottom + 1, bar_w, bottom - 1,
                   Qt.AlignHCenter | Qt.AlignVCenter, "L+R")
        p.end()
