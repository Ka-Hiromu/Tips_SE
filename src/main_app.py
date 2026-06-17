"""
Tips_SE — 演劇用ポン出しアプリ（PySide6 GUI）
ONJO の弱点（遅延/取りこぼし/長時間クラッシュ）を潰した低遅延ミキサーを核にする。
"""
from __future__ import annotations

import logging
import math
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QPalette, QPainter, QLinearGradient,
    QFont, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QMessageBox, QMenu, QDialog,
    QFormLayout, QLineEdit, QCheckBox, QDoubleSpinBox, QDialogButtonBox,
    QSpinBox, QSizePolicy, QStatusBar, QSplitter,
)

from engine import AudioEngine
from models import Preset, Pad
from pdfview import ScriptView

APP_NAME = "Tips_SE ポン出し"
SOUND_EXTS = {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif", ".w64"}

log = logging.getLogger("tips_se")


def app_data_dir() -> Path:
    """設定・ログの保存先（%LOCALAPPDATA%\\Tips_SE、無ければホーム）。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "Tips_SE"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path.home()
    return d


def setup_logging() -> Path:
    """ファイルへのロギングを設定（windowedビルドでもエラーを追える）。"""
    log_path = app_data_dir() / "tips_se.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # コンソールがあれば併せて出力
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root.addHandler(sh)
    return log_path


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

    def submit(self, clip_id: str, path: str, generation: int) -> None:
        self._pool.submit(self._work, clip_id, path, generation)

    def _work(self, clip_id: str, path: str, generation: int) -> None:
        try:
            self.engine.load_clip(clip_id, path)
            self.finished.emit(clip_id, True, "", generation)
        except Exception as e:  # noqa: BLE001
            self.finished.emit(clip_id, False, str(e), generation)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class PadEditDialog(QDialog):
    """1パッドの設定編集ダイアログ。"""

    def __init__(self, pad: Pad, parent=None):
        super().__init__(parent)
        self.pad = pad
        self.setWindowTitle(f"パッド {pad.index + 1} の設定")
        form = QFormLayout(self)

        self.path_edit = QLineEdit(pad.path)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        form.addRow("音源ファイル", self._wrap(path_row))

        self.label_edit = QLineEdit(pad.label)
        self.label_edit.setPlaceholderText("空ならファイル名を表示")
        form.addRow("表示名", self.label_edit)

        self.key_edit = QLineEdit(pad.key)
        self.key_edit.setMaxLength(1)
        self.key_edit.setPlaceholderText("例: a / 1 （空で無効）")
        form.addRow("割当キー", self.key_edit)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0.0, 1.5)
        self.gain_spin.setSingleStep(0.05)
        self.gain_spin.setValue(pad.gain)
        form.addRow("音量(0.0-1.5)", self.gain_spin)

        self.fade_in_spin = QDoubleSpinBox()
        self.fade_in_spin.setRange(0.0, 30.0)
        self.fade_in_spin.setSingleStep(0.1)
        self.fade_in_spin.setSuffix(" 秒")
        self.fade_in_spin.setValue(pad.fade_in)
        form.addRow("フェードイン", self.fade_in_spin)

        self.fade_out_spin = QDoubleSpinBox()
        self.fade_out_spin.setRange(0.0, 30.0)
        self.fade_out_spin.setSingleStep(0.1)
        self.fade_out_spin.setSuffix(" 秒")
        self.fade_out_spin.setValue(pad.fade_out)
        form.addRow("フェードアウト", self.fade_out_spin)

        self.loop_chk = QCheckBox("ループ再生")
        self.loop_chk.setChecked(pad.loop)
        form.addRow("", self.loop_chk)

        self.excl_chk = QCheckBox("排他再生（他の音を止めて鳴らす）")
        self.excl_chk.setChecked(pad.exclusive)
        form.addRow("", self.excl_chk)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        return w

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "音源を選択", "",
            "音声ファイル (*.wav *.flac *.mp3 *.ogg *.aiff *.aif *.w64);;すべて (*.*)")
        if path:
            self.path_edit.setText(path)

    def result_pad(self) -> Pad:
        self.pad.path = self.path_edit.text().strip()
        self.pad.label = self.label_edit.text().strip()
        self.pad.key = self.key_edit.text().strip().lower()
        self.pad.gain = self.gain_spin.value()
        self.pad.fade_in = self.fade_in_spin.value()
        self.pad.fade_out = self.fade_out_spin.value()
        self.pad.loop = self.loop_chk.isChecked()
        self.pad.exclusive = self.excl_chk.isChecked()
        return self.pad


class PadCell(QWidget):
    """1パッド = 再生ボタン ＋ 個別音量フェーダーの複合ウィジェット。"""

    def __init__(self, pad: Pad, parent_window: "MainWindow"):
        super().__init__()
        self.pad = pad
        self.win = parent_window
        self.is_loading = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(120, 104)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.button = QPushButton()
        self.button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.button.customContextMenuRequested.connect(
            lambda pos: self._menu(self.button.mapTo(self, pos)))
        self.button.clicked.connect(self._on_click)
        lay.addWidget(self.button, 1)

        # 個別音量フェーダー
        fader_row = QHBoxLayout()
        fader_row.setContentsMargins(4, 0, 4, 0)
        fader_row.setSpacing(4)
        self.fader = QSlider(Qt.Horizontal)
        self.fader.setRange(0, 150)
        self.fader.setValue(int(pad.gain * 100))
        self.fader.setToolTip("このパッドの音量")
        self.fader.valueChanged.connect(self._on_fader)
        self.vol_lbl = QLabel(f"{int(pad.gain * 100)}%")
        self.vol_lbl.setMinimumWidth(38)
        self.vol_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.vol_lbl.setStyleSheet("font-size:11px;color:#90a4ae;")
        fader_row.addWidget(self.fader, 1)
        fader_row.addWidget(self.vol_lbl)
        self.fader_widget = QWidget()
        self.fader_widget.setLayout(fader_row)
        lay.addWidget(self.fader_widget)

        self.refresh()

    def set_loading(self, loading: bool):
        """読み込み中表示の切替。"""
        self.is_loading = loading
        if loading:
            self.button.setText(f"⏳ 読み込み中…\n{self.pad.display_name()}")
            self.fader_widget.setVisible(False)
            self.button.setStyleSheet(
                "QPushButton{background:#5d4037;color:#ffe0b2;border:1px solid #795548;"
                "border-radius:8px;font-size:12px;}")
        else:
            self.refresh()

    def refresh(self):
        p = self.pad
        if self.is_loading:
            return
        num = p.index + 1
        if p.assigned:
            name = p.display_name()
            key = f"[{p.key.upper()}] " if p.key else ""
            tags = []
            if p.loop:
                tags.append("⟳")
            if p.exclusive:
                tags.append("EXCL")
            tag = ("  " + " ".join(tags)) if tags else ""
            self.button.setText(f"{num}.  {key}{name}{tag}")
            self.fader_widget.setVisible(True)
        else:
            self.button.setText(f"{num}\n＋ (クリックで割当)")
            self.fader_widget.setVisible(False)
        # フェーダー位置を pad.gain に同期（シグナル抑制）
        self.fader.blockSignals(True)
        self.fader.setValue(int(p.gain * 100))
        self.fader.blockSignals(False)
        self.vol_lbl.setText(f"{int(p.gain * 100)}%")
        self._set_playing(False)

    def _set_playing(self, playing: bool):
        if self.is_loading:
            return
        if playing:
            self.button.setStyleSheet(
                "QPushButton{background:#2e7d32;color:white;border:2px solid #1b5e20;"
                "border-radius:8px;font-size:13px;font-weight:bold;}")
        elif self.pad.assigned:
            self.button.setStyleSheet(
                "QPushButton{background:#37474f;color:#eceff1;border:1px solid #546e7a;"
                "border-radius:8px;font-size:13px;}"
                "QPushButton:hover{background:#455a64;}")
        else:
            self.button.setStyleSheet(
                "QPushButton{background:#263238;color:#78909c;border:1px dashed #455a64;"
                "border-radius:8px;font-size:12px;}"
                "QPushButton:hover{background:#2c393f;}")

    def _on_fader(self, value: int):
        gain = value / 100.0
        self.pad.gain = gain
        self.vol_lbl.setText(f"{value}%")
        # 再生中なら即時反映
        self.win.engine.set_clip_gain(self.win._clip_id(self.pad), gain)
        self.win._mark_dirty()

    def _on_click(self):
        if self.pad.assigned:
            self.win.toggle_pad(self.pad)
        else:
            self.win.edit_pad(self.pad)

    def _menu(self, pos):
        m = QMenu(self)
        if self.pad.assigned:
            m.addAction("▶ 再生", lambda: self.win.trigger_pad(self.pad))
            m.addAction("■ この音を停止", lambda: self.win.stop_pad(self.pad))
            m.addSeparator()
        m.addAction("設定…", lambda: self.win.edit_pad(self.pad))
        if self.pad.assigned:
            m.addAction("クリア", lambda: self.win.clear_pad(self.pad))
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

        self.setFixedWidth(78)
        self.setMinimumHeight(300)
        self.setStyleSheet("background:#11171a;")

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

        p.setPen(QColor("#90a4ae"))
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
            p.setPen(QColor("#5b6b73"))
            p.drawText(0, y - 7, scale_w - 3, 14,
                       Qt.AlignRight | Qt.AlignVCenter, f"{db}")
            p.setPen(QColor("#222d33"))
            p.drawLine(scale_w - 2, y, W - 4, y)

        # トラック背景
        p.fillRect(x, meter_top, bar_w, usable, QColor("#0a0e10"))

        grad = QLinearGradient(0, meter_bottom, 0, meter_top)
        grad.setColorAt(0.0, QColor("#1faa45"))
        grad.setColorAt(0.62, QColor("#3fd968"))
        grad.setColorAt(0.78, QColor("#e6d23a"))
        grad.setColorAt(0.90, QColor("#ef8a2b"))
        grad.setColorAt(1.0, QColor("#e53935"))

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
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(x, py, x + bar_w, py)

        p.setPen(QColor("#2b383f"))
        p.drawRect(x, meter_top, bar_w, usable)

        # クリップLED
        led = QColor("#ff1744") if self._clip else QColor("#3a1212")
        p.fillRect(x, top, bar_w, clip_h, led)
        p.setPen(QColor("#2b383f")); p.drawRect(x, top, bar_w, clip_h)

        # ラベル
        p.setPen(QColor("#b0bec5"))
        p.setFont(f2)
        p.drawText(x, meter_bottom + 1, bar_w, bottom - 1,
                   Qt.AlignHCenter | Qt.AlignVCenter, "L+R")
        p.end()


class MainWindow(QMainWindow):
    def __init__(self, engine: AudioEngine):
        super().__init__()
        self.engine = engine
        self.preset = Preset()
        self.preset.ensure_pads()
        self.preset_path: str | None = None
        self.pad_buttons: list[PadCell] = []
        self._key_map: dict[str, Pad] = {}

        # バックグラウンド・ローダー
        self.loader = ClipLoader(engine, self)
        self.loader.finished.connect(self._on_clip_loaded)
        self._load_generation = 0
        self._pending: set[str] = set()
        self._pending_total = 0
        self._load_errors: list[str] = []

        self.setWindowTitle(APP_NAME)
        self.resize(1500, 840)  # パッド + 戯曲PDF + メーター
        self._build_ui()
        self._rebuild_grid()

        # 再生状態の視覚反映（軽いポーリング）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_playing_state)
        self._timer.start(80)

    # ---- UI構築 ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addLayout(self._build_topbar())

        # 本体: [ パッド | 戯曲PDF ] スプリッタ ＋ 右端の細メーター
        body = QHBoxLayout()
        self.splitter = QSplitter(Qt.Horizontal)

        # 左: パッドグリッド
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(8)
        self.splitter.addWidget(self.grid_host)

        # 右: 戯曲PDFパネル
        self.splitter.addWidget(self._build_pdf_panel())
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 4)
        body.addWidget(self.splitter, 1)

        # 右端: 出力レベルメーター（L+R統合）
        self.meter = LevelMeter(self.engine, self)
        body.addWidget(self.meter)
        root.addLayout(body, 1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            f"エンジン: {engine_desc(self.engine)} ｜ 未保存プリセット")

        self._build_menu()
        self._update_edit_ui()

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        stop_btn = QPushButton("■ 全停止 (Space)")
        stop_btn.setStyleSheet(
            "QPushButton{background:#c62828;color:white;font-weight:bold;"
            "padding:8px 16px;border-radius:6px;}QPushButton:hover{background:#d32f2f;}")
        stop_btn.clicked.connect(self.stop_all)
        bar.addWidget(stop_btn)

        bar.addSpacing(12)
        bar.addWidget(QLabel("マスター音量"))
        self.master_slider = QSlider(Qt.Horizontal)
        self.master_slider.setRange(0, 150)
        self.master_slider.setValue(100)
        self.master_slider.setFixedWidth(140)
        self.master_slider.valueChanged.connect(self._on_master_changed)
        bar.addWidget(self.master_slider)
        self.master_lbl = QLabel("100%")
        bar.addWidget(self.master_lbl)

        bar.addSpacing(16)
        # 編集モード切替
        self.edit_btn = QPushButton("✎ 編集モード")
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self._on_edit_toggled)
        self.edit_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;border-radius:6px;background:#37474f;}"
            "QPushButton:checked{background:#1565c0;color:white;font-weight:bold;}")
        bar.addWidget(self.edit_btn)

        bar.addStretch(1)
        self.voices_lbl = QLabel("再生中: 0")
        bar.addWidget(self.voices_lbl)
        return bar

    def _build_pdf_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # PDF操作バー
        ctrl = QHBoxLayout()
        open_btn = QPushButton("戯曲PDFを開く…")
        open_btn.clicked.connect(self.open_pdf)
        ctrl.addWidget(open_btn)
        prev_btn = QPushButton("◀")
        prev_btn.setFixedWidth(32)
        prev_btn.clicked.connect(lambda: self.script.prev_page())
        ctrl.addWidget(prev_btn)
        self.page_lbl = QLabel("- / -")
        self.page_lbl.setMinimumWidth(60)
        self.page_lbl.setAlignment(Qt.AlignCenter)
        ctrl.addWidget(self.page_lbl)
        next_btn = QPushButton("▶")
        next_btn.setFixedWidth(32)
        next_btn.clicked.connect(lambda: self.script.next_page())
        ctrl.addWidget(next_btn)
        zout = QPushButton("－"); zout.setFixedWidth(28)
        zout.clicked.connect(lambda: self.script.zoom_out())
        zin = QPushButton("＋"); zin.setFixedWidth(28)
        zin.clicked.connect(lambda: self.script.zoom_in())
        ctrl.addWidget(zout); ctrl.addWidget(zin)
        ctrl.addStretch(1)
        v.addLayout(ctrl)

        # 編集ツールバー（編集モード時のみ表示）
        self.edit_tools = QWidget()
        et = QHBoxLayout(self.edit_tools)
        et.setContentsMargins(0, 0, 0, 0)
        self.tool_stamp = QPushButton("● スタンプ")
        self.tool_stamp.setCheckable(True)
        self.tool_text = QPushButton("▭ テキスト")
        self.tool_text.setCheckable(True)
        self.tool_stamp.clicked.connect(lambda: self._select_tool("stamp"))
        self.tool_text.clicked.connect(lambda: self._select_tool("text"))
        et.addWidget(self.tool_stamp)
        et.addWidget(self.tool_text)
        et.addSpacing(10)
        et.addWidget(QLabel("スタンプ番号"))
        self.stamp_num = QSpinBox()
        self.stamp_num.setRange(1, self.preset.count)
        self.stamp_num.valueChanged.connect(
            lambda v: self.script.set_stamp_pad(v - 1))
        et.addWidget(self.stamp_num)
        hint = QLabel("（PDF上をクリックで配置 / ドラッグで移動 / 選択してDeleteで削除）")
        hint.setStyleSheet("color:#90a4ae;font-size:11px;")
        et.addWidget(hint)
        et.addStretch(1)
        v.addWidget(self.edit_tools)

        # PDFビュー
        self.script = ScriptView(panel)
        self.script.stampClicked.connect(self._on_stamp_clicked)
        self.script.changed.connect(self._mark_dirty)
        self.script.pageChanged.connect(self._on_page_changed)
        self.script.toolCleared.connect(self._on_tool_cleared)
        v.addWidget(self.script, 1)
        return panel

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("ファイル")
        a_new = QAction("新規プリセット", self); a_new.triggered.connect(self.new_preset)
        a_open = QAction("プリセットを開く…", self); a_open.triggered.connect(self.open_preset)
        a_save = QAction("プリセットを保存", self); a_save.setShortcut(QKeySequence.Save)
        a_save.triggered.connect(self.save_preset)
        a_saveas = QAction("名前を付けて保存…", self); a_saveas.triggered.connect(self.save_preset_as)
        a_folder = QAction("フォルダから一括割当…", self)
        a_folder.triggered.connect(self.assign_from_folder)
        for a in (a_new, a_open, a_save, a_saveas):
            fm.addAction(a)
        fm.addSeparator(); fm.addAction(a_folder)

        gm = mb.addMenu("グリッド")
        a_grid = QAction("行・列数を変更…", self); a_grid.triggered.connect(self.change_grid)
        gm.addAction(a_grid)

        sm = mb.addMenu("戯曲")
        a_pdf = QAction("戯曲PDFを開く…", self); a_pdf.triggered.connect(self.open_pdf)
        sm.addAction(a_pdf)

    # ---- 編集モード / 戯曲PDF ----
    def _on_edit_toggled(self, on: bool):
        self.script.set_edit_mode(on)
        if not on:
            self.tool_stamp.setChecked(False)
            self.tool_text.setChecked(False)
        self._update_edit_ui()
        self.statusBar().showMessage(
            "編集モード: PDF上にスタンプ/テキストを配置できます" if on
            else "本番モード: スタンプのクリックで音を再生します")

    def _update_edit_ui(self):
        on = self.edit_btn.isChecked()
        self.edit_tools.setVisible(on)

    def _select_tool(self, tool: str):
        # 片方を選ぶと他方は解除（トグル式）
        if tool == "stamp":
            self.tool_text.setChecked(False)
            active = self.tool_stamp.isChecked()
        else:
            self.tool_stamp.setChecked(False)
            active = self.tool_text.isChecked()
        self.script.set_tool(tool if active else None)

    def _on_stamp_clicked(self, pad_index: int):
        if 0 <= pad_index < len(self.preset.pads):
            self.trigger_pad(self.preset.pads[pad_index])

    def _on_tool_cleared(self):
        # テキスト配置後など、ツールが自動解除されたらボタンのチェックも外す
        self.tool_stamp.setChecked(False)
        self.tool_text.setChecked(False)
        self.statusBar().showMessage("テキストを配置しました（配置モードを解除）")

    def _on_page_changed(self, cur: int, total: int):
        self.page_lbl.setText(f"{cur + 1} / {total}" if total else "- / -")
        self.preset.pdf_page = cur

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "戯曲PDFを開く", "",
                                              "PDF (*.pdf)")
        if not path:
            return
        if self.script.load_pdf(path):
            self.preset.pdf_path = path
            self._mark_dirty()
            self.statusBar().showMessage(f"戯曲PDF: {Path(path).name}")
        else:
            QMessageBox.critical(self, "エラー", f"PDFを開けません:\n{path}")

    def _rebuild_grid(self):
        # 既存ボタンを除去
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.pad_buttons.clear()
        self.preset.ensure_pads()
        for p in self.preset.pads:
            r, c = divmod(p.index, self.preset.cols)
            btn = PadCell(p, self)
            self.grid.addWidget(btn, r, c)
            self.pad_buttons.append(btn)
        self._reload_engine_clips()
        self._rebuild_key_map()
        # 戯曲スタンプの番号範囲をパッド数に合わせる
        if hasattr(self, "script"):
            self.script.pad_count = self.preset.count
            self.stamp_num.setMaximum(self.preset.count)

    def _rebuild_key_map(self):
        self._key_map = {p.key: p for p in self.preset.pads if p.key and p.assigned}

    # ---- 音源ロード ----
    def _clip_id(self, pad: Pad) -> str:
        return f"pad{pad.index}"

    def _pad_index_from_clip(self, clip_id: str) -> int:
        return int(clip_id[3:])  # "padN" -> N

    def _reload_engine_clips(self):
        """全クリップをバックグラウンドで（再）読み込みする。UIはブロックしない。"""
        # 世代を進め、進行中の古い完了通知を無効化
        self._load_generation += 1
        gen = self._load_generation
        self._pending.clear()
        self._load_errors.clear()

        # 既存クリップを一旦全アンロード
        for p in self.preset.pads:
            self.engine.unload_clip(self._clip_id(p))

        to_load = []
        for p in self.preset.pads:
            if not p.assigned:
                continue
            cid = self._clip_id(p)
            if Path(p.path).exists():
                to_load.append((cid, p.path))
            else:
                self._load_errors.append(
                    f"パッド{p.index+1}: ファイルが見つかりません {p.path}")

        self._pending = {cid for cid, _ in to_load}
        self._pending_total = len(to_load)
        for cid, path in to_load:
            self.pad_buttons[self._pad_index_from_clip(cid)].set_loading(True)
            self.loader.submit(cid, path, gen)
        self._update_load_status()

    def _on_clip_loaded(self, clip_id: str, ok: bool, err: str, generation: int):
        # 古い世代の完了は破棄（再読込が重なった場合の取り違え防止）
        if generation != self._load_generation:
            return
        idx = self._pad_index_from_clip(clip_id)
        if 0 <= idx < len(self.pad_buttons):
            self.pad_buttons[idx].set_loading(False)
        if not ok:
            self._load_errors.append(f"パッド{idx+1}: 読み込み失敗 ({err})")
        self._pending.discard(clip_id)
        self._update_load_status()

    def _update_load_status(self):
        remaining = len(self._pending)
        if remaining > 0:
            done = self._pending_total - remaining
            self.statusBar().showMessage(
                f"読み込み中… {done}/{self._pending_total}")
        else:
            self.statusBar().showMessage("準備完了")
            if self._load_errors:
                errs = self._load_errors[:]
                self._load_errors.clear()
                QMessageBox.warning(
                    self, "読み込み警告",
                    "一部の音源を読み込めませんでした:\n\n" + "\n".join(errs))

    # ---- パッド操作 ----
    def trigger_pad(self, pad: Pad):
        if not pad.assigned:
            return
        cid = self._clip_id(pad)
        cell = self.pad_buttons[pad.index]
        if self.engine.get_clip(cid) is None:
            # まだ読み込めていない: 同期デコードでUIを固めず、状況だけ知らせる
            if cell.is_loading:
                self.statusBar().showMessage("読み込み中… 完了までお待ちください")
            else:
                # 念のためバックグラウンドで読み込みを開始
                cell.set_loading(True)
                self._pending.add(cid)
                self._pending_total = max(self._pending_total, len(self._pending))
                self.loader.submit(cid, pad.path, self._load_generation)
                self.statusBar().showMessage("音源を読み込み中…")
            return
        self.engine.play(
            cid, gain=pad.gain, loop=pad.loop,
            fade_in=pad.fade_in, exclusive=pad.exclusive, retrigger=True)

    def toggle_pad(self, pad: Pad):
        """クリック時の挙動: 再生中なら停止、停止中なら再生（トグル）。"""
        if not pad.assigned:
            return
        if self.engine.is_playing(self._clip_id(pad)):
            self.stop_pad(pad)
        else:
            self.trigger_pad(pad)

    def stop_pad(self, pad: Pad):
        self.engine.stop(self._clip_id(pad), fade_out=pad.fade_out)

    def stop_all(self):
        self.engine.stop_all(fade_out=self.preset.default_fade_out)

    def edit_pad(self, pad: Pad):
        dlg = PadEditDialog(pad, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.result_pad()
            cid = self._clip_id(pad)
            # 該当クリップをバックグラウンドで再ロード
            self.engine.unload_clip(cid)
            cell = self._button_for(pad)
            if pad.assigned and Path(pad.path).exists():
                cell.set_loading(True)
                self._pending.add(cid)
                self._pending_total = max(self._pending_total, len(self._pending))
                self.loader.submit(cid, pad.path, self._load_generation)
                self._update_load_status()
            else:
                cell.refresh()
                if pad.assigned:
                    QMessageBox.warning(self, "読み込み警告",
                                        f"ファイルが見つかりません:\n{pad.path}")
            self._rebuild_key_map()
            self._mark_dirty()

    def clear_pad(self, pad: Pad):
        self.engine.unload_clip(self._clip_id(pad))
        idx = pad.index
        self.preset.pads[idx] = Pad(index=idx)
        self._button_for(pad).pad = self.preset.pads[idx]
        self._button_for(pad).refresh()
        self._rebuild_key_map()
        self._mark_dirty()

    def _button_for(self, pad: Pad) -> PadCell:
        return self.pad_buttons[pad.index]

    # ---- フォルダ一括割当 ----
    def assign_from_folder(self):
        d = QFileDialog.getExistingDirectory(self, "音源フォルダを選択")
        if not d:
            return
        files = sorted(p for p in Path(d).iterdir()
                       if p.suffix.lower() in SOUND_EXTS)
        if not files:
            QMessageBox.information(self, "情報", "対応音源が見つかりませんでした。")
            return
        for pad, f in zip(self.preset.pads, files):
            pad.path = str(f)
            pad.label = ""
        self._rebuild_grid()
        self._mark_dirty()
        self.statusBar().showMessage(f"{min(len(files), self.preset.count)} 個を割り当てました")

    # ---- プリセット ----
    def new_preset(self):
        self.engine.stop_all()
        self.preset = Preset()
        self.preset.ensure_pads()
        self.preset_path = None
        self.script.clear()
        self._rebuild_grid()
        self.master_slider.setValue(100)
        self._update_title()

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "プリセットを開く", "",
                                              "プリセット (*.json)")
        if not path:
            return
        try:
            self.preset = Preset.load(path)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込めません:\n{e}")
            return
        self.preset_path = path
        self.master_slider.setValue(int(self.preset.master_gain * 100))
        self._rebuild_grid()
        self._load_script_from_preset()
        self._update_title()

    def _load_script_from_preset(self):
        """プリセットの戯曲PDFと注釈をビューアへ反映。"""
        if self.preset.pdf_path and Path(self.preset.pdf_path).exists():
            self.script.load_pdf(self.preset.pdf_path)
            self.script.set_annotations(self.preset.annotations)
            self.script.set_page(self.preset.pdf_page)
        else:
            self.script.clear()
            if self.preset.pdf_path:
                self.statusBar().showMessage(
                    f"戯曲PDFが見つかりません: {self.preset.pdf_path}")

    def save_preset(self):
        if not self.preset_path:
            return self.save_preset_as()
        self._do_save(self.preset_path)

    def save_preset_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", "preset.json",
                                              "プリセット (*.json)")
        if path:
            self.preset_path = path
            self._do_save(path)

    def _do_save(self, path: str):
        self.preset.master_gain = self.master_slider.value() / 100.0
        self.preset.annotations = self.script.export_annotations()
        self.preset.pdf_page = self.script.page
        try:
            self.preset.save(path)
            self._update_title()
            self.statusBar().showMessage(f"保存しました: {path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存できません:\n{e}")

    def change_grid(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("グリッドサイズ")
        form = QFormLayout(dlg)
        rs = QSpinBox(); rs.setRange(1, 8); rs.setValue(self.preset.rows)
        cs = QSpinBox(); cs.setRange(1, 8); cs.setValue(self.preset.cols)
        form.addRow("行数", rs)
        form.addRow("列数", cs)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() == QDialog.Accepted:
            self.preset.rows = rs.value()
            self.preset.cols = cs.value()
            self._rebuild_grid()
            self._mark_dirty()

    # ---- マスター音量 ----
    def _on_master_changed(self, v):
        self.engine.master_gain = v / 100.0
        self.master_lbl.setText(f"{v}%")

    # ---- キーボード ----
    def keyPressEvent(self, ev):
        if ev.isAutoRepeat():
            return
        if ev.key() == Qt.Key_Space or ev.key() == Qt.Key_Escape:
            self.stop_all()
            return
        text = ev.text().lower()
        pad = self._key_map.get(text)
        if pad is not None:
            self.trigger_pad(pad)
            return
        super().keyPressEvent(ev)

    # ---- 状態反映 ----
    def _refresh_playing_state(self):
        for btn in self.pad_buttons:
            if btn.pad.assigned and not btn.is_loading:
                playing = self.engine.is_playing(self._clip_id(btn.pad))
                btn._set_playing(playing)
        self.voices_lbl.setText(f"再生中: {self.engine.active_voice_count()}")

    def _mark_dirty(self):
        self._update_title(dirty=True)

    def _update_title(self, dirty: bool = False):
        name = Path(self.preset_path).name if self.preset_path else "未保存"
        mark = " *" if dirty else ""
        self.setWindowTitle(f"{APP_NAME} — {name}{mark}")

    def closeEvent(self, ev):
        try:
            self.loader.shutdown()
        except Exception:
            pass
        try:
            self.engine.close()
        except Exception:
            pass
        super().closeEvent(ev)


def engine_desc(engine: AudioEngine) -> str:
    api = "WASAPI" if engine._hostapi is not None else "default"
    latency_ms = engine.blocksize / engine.samplerate * 1000
    return f"{api} {engine.samplerate}Hz / block{engine.blocksize}(≈{latency_ms:.1f}ms)"


def _install_excepthook():
    """未捕捉例外をログに記録し、可能ならダイアログ表示（windowedでも追える）。"""
    def hook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("未捕捉例外:\n%s", msg)
        try:
            QMessageBox.critical(None, "予期しないエラー",
                                 f"エラーが発生しました。\n\nログ: {app_data_dir() / 'tips_se.log'}\n\n{exc}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


def main():
    log_path = setup_logging()
    _install_excepthook()
    log.info("==== Tips_SE 起動 ==== (frozen=%s)", getattr(sys, "frozen", False))

    app = QApplication(sys.argv)
    app.setApplicationName("Tips_SE")
    app.setStyle("Fusion")
    # ダークテーマ
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#1c2529"))
    pal.setColor(QPalette.WindowText, QColor("#eceff1"))
    pal.setColor(QPalette.Base, QColor("#263238"))
    pal.setColor(QPalette.Text, QColor("#eceff1"))
    pal.setColor(QPalette.Button, QColor("#37474f"))
    pal.setColor(QPalette.ButtonText, QColor("#eceff1"))
    app.setPalette(pal)

    try:
        engine = AudioEngine(blocksize=256)
        engine.start()
        log.info("オーディオ初期化: %s", engine_desc(engine))
    except Exception as e:
        log.exception("オーディオ初期化失敗")
        QMessageBox.critical(None, "オーディオ初期化エラー",
                             f"オーディオデバイスを開けませんでした:\n{e}\n\n{traceback.format_exc()}")
        return 1

    win = MainWindow(engine)
    win.show()

    # ビルド検証用: TIPS_SE_SELFTEST=1 のとき、起動確認後に自動終了する
    if os.environ.get("TIPS_SE_SELFTEST"):
        log.info("SELFTEST モード: 起動確認後に自動終了します")
        QTimer.singleShot(1800, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
