"""
Tips_SE — 演劇用ポン出しアプリ（PySide6 GUI）
ONJO の弱点（遅延/取りこぼし/長時間クラッシュ）を潰した低遅延ミキサーを核にする。
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF, QEvent
from PySide6.QtGui import (
    QAction, QActionGroup, QKeySequence, QColor, QPalette, QPainter,
    QLinearGradient, QFont, QFontDatabase, QPen, QPixmap, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QFileDialog, QMessageBox, QMenu, QDialog,
    QFormLayout, QLineEdit, QCheckBox, QDoubleSpinBox, QDialogButtonBox,
    QSpinBox, QSizePolicy, QStatusBar, QSplitter, QSplashScreen,
)

from engine import AudioEngine
from models import Preset, Pad, Sound
from pdfview import ScriptView
from remote import RemoteServer
from theme import (t, set_theme, THEMES, DEFAULT_THEME,
                   app_qss, fader_qss, palette_of)


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load_app_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_app_config(cfg: dict):
    try:
        config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception:
        pass

APP_NAME = "Tips_SE ポン出し"
SOUND_EXTS = {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif", ".w64"}

log = logging.getLogger("tips_se")


def run_elevated(ps_command: str) -> bool:
    """管理者権限で PowerShell コマンドを実行（UACが表示される）。
    起動できたら True（UACキャンセル/失敗で False）。"""
    import ctypes
    params = f'-NoProfile -WindowStyle Hidden -Command "{ps_command}"'
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                                             params, None, 0)
    return int(rc) > 32


def make_qr_pixmap(text: str, px: int = 260) -> QPixmap:
    """文字列のQRコードを QPixmap に描画（PIL不要・qrcodeのmatrixをQPainterで描く）。"""
    import qrcode
    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    pm = QPixmap(px, px)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("black"))
    cell = px / n
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                p.drawRect(int(c * cell), int(r * cell), int(cell + 1), int(cell + 1))
    p.end()
    return pm


def resource_path(rel: str) -> Path:
    """同梱リソースのパス（開発時/PyInstaller frozen時の両対応）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / rel


def app_icon() -> QIcon:
    """アプリ／タスクバーのアイコン（ロゴの「音」を抜き出したもの）。"""
    p = resource_path("assets/icon.ico")
    return QIcon(str(p)) if p.exists() else QIcon()


def set_app_user_model_id():
    """タスクバーで自前アイコンが使われるようにする（開発時の pythonw 対策）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TipsSE.PonDashi")
    except Exception:
        pass


SPLASH_FONT_FAMILY = ""  # 同梱フォント読み込み後に main() が設定する


# DwmSetWindowAttribute の属性ID（Windows 11 以降で有効）
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF


def _colorref(hex_color: str) -> int:
    """#rrggbb → COLORREF(0x00bbggrr)。"""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def set_caption_colors(win: QWidget, caption: str | None, text: str | None,
                       dark: bool):
    """タイトルバーの色を指定する。None ならシステム既定に戻す。"""
    try:
        import ctypes
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(win.winId()))

        def put(attr: int, value: int):
            v = ctypes.c_uint(value)
            dwm.DwmSetWindowAttribute(hwnd, ctypes.c_int(attr),
                                      ctypes.byref(v), ctypes.sizeof(v))

        put(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        cap = _colorref(caption) if caption else DWMWA_COLOR_DEFAULT
        put(DWMWA_CAPTION_COLOR, cap)
        put(DWMWA_BORDER_COLOR, cap)
        put(DWMWA_TEXT_COLOR, _colorref(text) if text else DWMWA_COLOR_DEFAULT)
    except Exception:
        pass  # Windows 10 以前や DWM 無効時は何もしない


class DialogCaptionStyler(QObject):
    """あとから開くダイアログのタイトルバーにもテーマ色を当てるイベントフィルタ。"""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QDialog):
            th = t()
            set_caption_colors(obj, th.caption_active, th.caption_text_active,
                               dark=True)
        return False


class SplashScreen(QSplashScreen):
    """起動待ちに出すロゴのスプラッシュ。"""

    MIN_VISIBLE_SEC = 1.2

    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._text = ""
        self._shown_at = time.monotonic()

    def status(self, text: str):
        self._text = text
        self.repaint()
        QApplication.processEvents()

    def drawContents(self, painter: QPainter):
        f = QFont(SPLASH_FONT_FAMILY or painter.font().family(), 10)
        painter.setFont(f)
        painter.setPen(QColor(t().text_dim))
        painter.drawText(self.rect().adjusted(0, 0, 0, -22),
                         Qt.AlignBottom | Qt.AlignHCenter, self._text)

    def close_after_min(self, win: QWidget | None = None):
        """短すぎる点滅を避けるため、最低表示時間まで待ってから閉じる。"""
        while time.monotonic() - self._shown_at < self.MIN_VISIBLE_SEC:
            QApplication.processEvents()
            time.sleep(0.02)
        if win is not None:
            self.finish(win)
        else:
            self.close()


def make_splash() -> SplashScreen | None:
    p = resource_path("assets/logo.png")
    if not p.exists():
        return None
    logo = QPixmap(str(p)).scaled(400, 400, Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
    canvas = QPixmap(logo.width(), logo.height() + 64)
    canvas.fill(QColor("#000000"))
    pt = QPainter(canvas)
    pt.drawPixmap(0, 0, logo)
    pt.setPen(QPen(QColor(t().border), 1))
    pt.drawRect(0, 0, canvas.width() - 1, canvas.height() - 1)
    pt.end()
    return SplashScreen(canvas)


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

    FILE_FILTER = "音声ファイル (*.wav *.flac *.mp3 *.ogg *.aiff *.aif *.w64);;すべて (*.*)"

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
        path, _ = QFileDialog.getOpenFileName(self, "音源を選択", "", self.FILE_FILTER)
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


class PadEditDialog(QDialog):
    """1パッドの設定編集ダイアログ。"""

    def __init__(self, pad: Pad, parent=None):
        super().__init__(parent)
        self.pad = pad
        self.ext_random = bool(getattr(parent, "ext_random", False))
        self.ext_continuous = bool(getattr(parent, "ext_continuous", False))
        self.setWindowTitle(f"パッド {pad.index + 1} の設定")
        form = QFormLayout(self)

        # 単一音源ファイル
        self.path_edit = QLineEdit(pad.path)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        self.file_label = QLabel("音源ファイル")
        self.file_field = self._wrap(path_row)
        form.addRow(self.file_label, self.file_field)

        # 確率モード（ランダム再生拡張が有効なときのみ）
        self.random_chk = None
        self.sources_editor = None
        if self.ext_random:
            self.random_chk = QCheckBox("確率（複数音源をランダム再生）")
            self.random_chk.setChecked(pad.random_enabled)
            self.random_chk.toggled.connect(self._on_random_toggled)
            form.addRow("", self.random_chk)
            self.sources_label = QLabel("ランダム音源")
            self.sources_editor = SourcesEditor(self)
            self.sources_editor.set_sources(pad.sources)
            form.addRow(self.sources_label, self.sources_editor)
            self.setMinimumWidth(560)   # 円グラフを右に置くぶん横に広げる

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

        self.retrigger_chk = None
        if self.ext_continuous:
            self.retrigger_chk = QCheckBox("連続再生（連打したぶん重ねて鳴らす）")
            self.retrigger_chk.setChecked(pad.retrigger)
            form.addRow("", self.retrigger_chk)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        if self.random_chk is not None:
            self._on_random_toggled(self.random_chk.isChecked())

    def _on_random_toggled(self, on: bool):
        # 確率モード時は単一ファイル行を隠し、音源エディタを表示
        self.file_label.setVisible(not on)
        self.file_field.setVisible(not on)
        self.sources_label.setVisible(on)
        self.sources_editor.setVisible(on)

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
        if self.random_chk is not None and self.random_chk.isChecked():
            self.pad.random_enabled = True
            self.pad.sources = self.sources_editor.get_sources()
            self.pad.path = ""   # 確率モードでは単一pathは使わない
        else:
            if self.random_chk is not None:
                self.pad.random_enabled = False
            self.pad.path = self.path_edit.text().strip()
        self.pad.label = self.label_edit.text().strip()
        self.pad.key = self.key_edit.text().strip().lower()
        self.pad.gain = self.gain_spin.value()
        self.pad.fade_in = self.fade_in_spin.value()
        self.pad.fade_out = self.fade_out_spin.value()
        self.pad.loop = self.loop_chk.isChecked()
        self.pad.exclusive = self.excl_chk.isChecked()
        if self.retrigger_chk is not None:
            self.pad.retrigger = self.retrigger_chk.isChecked()
        return self.pad


class PadCell(QWidget):
    """1パッド = 再生ボタン ＋ 個別音量フェーダーの複合ウィジェット。"""

    def __init__(self, pad: Pad, parent_window: "MainWindow"):
        super().__init__()
        self.pad = pad
        self.win = parent_window
        self.is_loading = False
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
        self._last_paint_key = None  # 末尾の _set_playing(False) を確実に再適用させる
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
            self.mode_lbl.setText(" ".join(tags))
            self.fader_widget.setVisible(True)
            self.opt_bar.setVisible(True)
        else:
            self.text_lbl.setText("＋")
            self.key_lbl.setText("")
            self.mode_lbl.setText("")
            self.fader_widget.setVisible(False)
            self.opt_bar.setVisible(False)
        # フェーダー位置を pad.gain に同期（シグナル抑制）
        self.fader.blockSignals(True)
        self.fader.setValue(int(p.gain * 100))
        self.fader.blockSignals(False)
        self.vol_lbl.setText(f"{int(p.gain * 100)}%")
        self._fade_dir_shown = None
        self._update_fade_label(0)
        self._set_playing(False)

    def _update_fade_label(self, fade_dir: int):
        """フェード表示を更新。fade_dir(+1/-1) の側だけ明るくして進行中を示す。"""
        if self._fade_dir_shown == fade_dir:
            return
        self._fade_dir_shown = fade_dir
        p = self.pad
        if not p.assigned or (p.fade_in <= 0 and p.fade_out <= 0):
            self.fade_lbl.setText("")
            return
        show_sec = getattr(self.win, "show_fade_sec", True)
        parts = []
        for mark, sec, active in (("◢", p.fade_in, fade_dir > 0),
                                  ("◣", p.fade_out, fade_dir < 0)):
            if sec <= 0:
                continue
            color = t().text if active else t().text_dim
            body = f"{mark}&nbsp;{sec:g}s" if show_sec else mark
            parts.append(f'<span style="color:{color};">{body}</span>')
        self.fade_lbl.setText("&nbsp;&nbsp;".join(parts))

    @staticmethod
    def _lerp_hex(c1: str, c2: str, t: float) -> str:
        """2つの #rrggbb を t(0-1) で線形補間する。"""
        t = max(0.0, min(1.0, t))
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3))

    def _set_playing(self, playing: bool, progress: float = 0.0,
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
        if playing:
            # 再生位置を左→右に「濃い緑」で塗るプログレスバー表現。
            # フェード中はその強度(0-1)で緑の明度を変え、薄→濃(イン)/濃→薄(アウト)を可視化する。
            p2 = min(1.0, p + 0.001)
            th = t()
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
            th = t()
            self.button.setStyleSheet(
                f"QPushButton{{background:{th.pad_bg};border:1px solid {th.pad_border};"
                "border-radius:8px;}"
                f"QPushButton:hover{{background:{th.pad_hover};}}")
            self.text_lbl.setStyleSheet(
                f"background:transparent;color:{th.pad_text};font-size:13px;")
        else:
            th = t()
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
        # 再生中なら即時反映
        self.win._pad_set_gain(self.pad, gain)
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
        self.remote: RemoteServer | None = None
        # 拡張機能の有効/無効（コンフィグメニュー、アプリ全体で永続）
        self.app_config = load_app_config()
        self.ext_random = bool(self.app_config.get("random_playback", False))
        self.ext_continuous = bool(self.app_config.get("continuous_playback", False))
        # 戯曲PDFビュー（既定ON）。OFFにすると右側が空き、PowerPoint等を並べられる。
        self.ext_pdf = bool(self.app_config.get("pdf_view", True))
        # PDF編集モード / スマホ操作は本番中はまず使わないので既定OFF（必要な人だけ出す）
        self.ext_pdf_edit = bool(self.app_config.get("pdf_edit", False))
        self.ext_remote = bool(self.app_config.get("remote_control", False))
        # パッドのフェード表示に秒数を併記するか
        self.show_fade_sec = bool(self.app_config.get("fade_seconds", True))

        self.setWindowTitle(APP_NAME)
        # パッド + 戯曲PDF + メーター。PDF非表示時はパッド+メーターに収まる幅で起動。
        self.resize(1500 if self.ext_pdf else 760, 840)
        self._build_ui()
        self._rebuild_grid()
        self._restore_output_device()  # 前回選んだスピーカーを復元（音源ロード前）

        # 再生状態の視覚反映（軽いポーリング）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_playing_state)
        self._timer.start(80)

        # 出力デバイス表示の追従（既定デバイスの切替や機器の抜き差しを拾う）
        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._update_device_label)
        self._device_timer.start(3000)

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

        # 右: 戯曲PDFパネル（コンフィグで表示/非表示を切替）
        self.pdf_panel = self._build_pdf_panel()
        self.splitter.addWidget(self.pdf_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 4)
        if not self.ext_pdf:
            self.pdf_panel.hide()
        body.addWidget(self.splitter, 1)

        # 右端: 出力レベルメーター（L+R統合）
        self.meter = LevelMeter(self.engine, self)
        body.addWidget(self.meter)
        root.addLayout(body, 1)

        self.setStatusBar(QStatusBar())
        # 出力先は本番中いつでも確認したいので右端に常設（showMessage では消えない）
        self.device_lbl = QLabel()
        self.device_lbl.setStyleSheet(
            f"color:{t().device_text};font-size:12px;padding:0 8px;")
        self.statusBar().addPermanentWidget(self.device_lbl)
        self.statusBar().showMessage(
            f"エンジン: {engine_desc(self.engine)} ｜ 未保存プリセット")

        self._build_menu()
        self.apply_theme()
        self._update_edit_ui()
        self._update_optional_ui()
        self._update_device_label()

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.stop_btn = QPushButton("■ 全停止 (Space)")
        self.stop_btn.clicked.connect(self.stop_all)
        bar.addWidget(self.stop_btn)

        bar.addSpacing(12)
        bar.addWidget(QLabel("マスター音量"))
        self.master_slider = QSlider(Qt.Horizontal)
        self.master_slider.setRange(0, 150)
        self.master_slider.setValue(100)
        self.master_slider.setFixedWidth(160)
        self.master_slider.setStyleSheet(fader_qss(t()))
        self.master_slider.setMinimumHeight(22)
        self.master_slider.valueChanged.connect(self._on_master_changed)
        bar.addWidget(self.master_slider)
        self.master_lbl = QLabel("100%")
        bar.addWidget(self.master_lbl)

        bar.addSpacing(16)
        # 編集モード切替
        self.edit_btn = QPushButton("✎ 編集モード")
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self._on_edit_toggled)
        bar.addWidget(self.edit_btn)

        self.remote_btn = QPushButton("📱 スマホ操作")
        self.remote_btn.setCheckable(True)
        self.remote_btn.toggled.connect(self._on_remote_toggled)
        bar.addWidget(self.remote_btn)

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
        open_btn = QPushButton("PDFを開く")
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
        self.pdf_hint = QLabel("（PDF上をクリックで配置 / ドラッグで移動 / 選択してDeleteで削除）")
        self.pdf_hint.setStyleSheet(f"color:{t().text_dim};font-size:11px;")
        et.addWidget(self.pdf_hint)
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

    def apply_theme(self, name: str | None = None, save: bool = False):
        """配色を適用する。name 指定でテーマを切り替え、save で設定に残す。"""
        if name:
            set_theme(name)
            if save:
                self.app_config["theme"] = name
                save_app_config(self.app_config)
        th = t()
        app = QApplication.instance()
        app.setPalette(palette_of(th))
        app.setStyleSheet(app_qss(th))

        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{th.stop_bg};color:{th.stop_text};"
            f"font-weight:bold;border:1px solid {th.stop_bg};"
            "padding:8px 16px;border-radius:6px;}"
            f"QPushButton:hover{{background:{th.stop_hover};}}")
        for btn, on_bg in ((self.edit_btn, th.edit_on_bg),
                           (self.remote_btn, th.remote_on_bg)):
            btn.setStyleSheet(
                f"QPushButton{{padding:6px 14px;border-radius:6px;"
                f"background:{th.toggle_bg};border:1px solid {th.border};}}"
                f"QPushButton:hover{{background:{th.toggle_hover};}}"
                f"QPushButton:checked{{background:{on_bg};"
                f"color:{th.toggle_on_text};font-weight:bold;}}")
        self.master_slider.setStyleSheet(fader_qss(th))
        self.device_lbl.setStyleSheet(
            f"color:{th.device_text};font-size:12px;padding:0 8px;")
        self.pdf_hint.setStyleSheet(f"color:{th.text_dim};font-size:11px;")
        self.meter.setStyleSheet(f"background:{th.meter_panel};")
        self.meter.update()
        self.script.apply_theme()
        for cell in self.pad_buttons:
            cell.apply_theme()
        self._update_caption()

    def _update_caption(self):
        """アクティブ／非アクティブでタイトルバーの色を切り替える。"""
        th = t()
        active = self.isActiveWindow()
        set_caption_colors(
            self,
            th.caption_active if active else th.caption_inactive,
            th.caption_text_active if active else th.caption_text_inactive,
            dark=active)

    def changeEvent(self, ev):
        if ev.type() == QEvent.ActivationChange:
            self._update_caption()
        super().changeEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._update_caption()  # winId が有効になってから適用する

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
        a_pdf = QAction("PDFを開く", self); a_pdf.triggered.connect(self.open_pdf)
        sm.addAction(a_pdf)
        sm.addSeparator()

        # 戯曲PDF まわりの表示切替はこのメニューにまとめる
        self.act_pdf = QAction("PDFビューを表示", self, checkable=True)
        self.act_pdf.setChecked(self.ext_pdf)
        self.act_pdf.toggled.connect(self._on_ext_pdf_toggled)
        sm.addAction(self.act_pdf)

        self.act_pdf_edit = QAction("編集モードを使う", self, checkable=True)
        self.act_pdf_edit.setChecked(self.ext_pdf_edit)
        self.act_pdf_edit.toggled.connect(self._on_ext_pdf_edit_toggled)
        sm.addAction(self.act_pdf_edit)

        cm = mb.addMenu("コンフィグ")
        self.act_random = QAction("ランダム再生", self, checkable=True)
        self.act_random.setChecked(self.ext_random)
        self.act_random.toggled.connect(self._on_ext_random_toggled)
        cm.addAction(self.act_random)

        self.act_continuous = QAction("連続再生", self, checkable=True)
        self.act_continuous.setChecked(self.ext_continuous)
        self.act_continuous.toggled.connect(self._on_ext_continuous_toggled)
        cm.addAction(self.act_continuous)

        self.act_remote = QAction("スマホ操作", self, checkable=True)
        self.act_remote.setChecked(self.ext_remote)
        self.act_remote.toggled.connect(self._on_ext_remote_toggled)
        cm.addAction(self.act_remote)

        self.act_fade_sec = QAction("フェード秒数を表示", self, checkable=True)
        self.act_fade_sec.setChecked(self.show_fade_sec)
        self.act_fade_sec.toggled.connect(self._on_fade_sec_toggled)
        cm.addAction(self.act_fade_sec)

        cm.addSeparator()
        # 出力スピーカー選択（開くたびに最新デバイスへ再構築＝接続直後も反映）
        theme_menu = cm.addMenu("配色")
        tgroup = QActionGroup(self)
        tgroup.setExclusive(True)
        for tname, th in THEMES.items():
            act = QAction(th.label, self, checkable=True)
            act.setChecked(tname == t().name)
            act.triggered.connect(
                lambda _checked=False, n=tname: self.apply_theme(n, save=True))
            tgroup.addAction(act)
            theme_menu.addAction(act)

        self.output_menu = cm.addMenu("出力デバイス")
        self.output_menu.aboutToShow.connect(self._rebuild_output_menu)

    def _on_ext_random_toggled(self, on: bool):
        self.ext_random = on
        self.app_config["random_playback"] = on
        save_app_config(self.app_config)
        self.statusBar().showMessage(
            "ランダム再生拡張: 有効（パッド設定に「確率」が出ます）" if on
            else "ランダム再生拡張: 無効")

    def _on_ext_continuous_toggled(self, on: bool):
        self.ext_continuous = on
        self.app_config["continuous_playback"] = on
        save_app_config(self.app_config)
        self.statusBar().showMessage(
            "連続再生拡張: 有効（パッド設定に「連続再生」が出ます）" if on
            else "連続再生拡張: 無効")

    def _on_ext_pdf_edit_toggled(self, on: bool):
        self.ext_pdf_edit = on
        self.app_config["pdf_edit"] = on
        save_app_config(self.app_config)
        if not on and self.edit_btn.isChecked():
            self.edit_btn.setChecked(False)   # 隠す前に本番モードへ戻す
        self._update_optional_ui()
        self.statusBar().showMessage(
            "戯曲PDFの編集モード: 使う（ツールバーに「✎ 編集モード」が出ます）" if on
            else "戯曲PDFの編集モード: 使わない")

    def _on_ext_remote_toggled(self, on: bool):
        self.ext_remote = on
        self.app_config["remote_control"] = on
        save_app_config(self.app_config)
        if not on and self.remote_btn.isChecked():
            self.remote_btn.setChecked(False)  # 隠す前に待受を止める
        self._update_optional_ui()
        self.statusBar().showMessage(
            "スマホ操作: 使う（ツールバーに「📱 スマホ操作」が出ます）" if on
            else "スマホ操作: 使わない")

    def _on_fade_sec_toggled(self, on: bool):
        self.show_fade_sec = on
        self.app_config["fade_seconds"] = on
        save_app_config(self.app_config)
        for cell in self.pad_buttons:
            cell._fade_dir_shown = None
            cell._update_fade_label(0)
        self.statusBar().showMessage(
            "フェード表示: アイコン＋秒数" if on else "フェード表示: アイコンのみ")

    def _update_optional_ui(self):
        """オプション扱いの機能ボタンをツールバーに出す/隠す。"""
        self.edit_btn.setVisible(self.ext_pdf_edit and self.ext_pdf)
        self.remote_btn.setVisible(self.ext_remote)

    def _on_ext_pdf_toggled(self, on: bool):
        self.ext_pdf = on
        self.app_config["pdf_view"] = on
        save_app_config(self.app_config)
        if not on and self.edit_btn.isChecked():
            self.edit_btn.setChecked(False)
        self._update_optional_ui()
        if on:
            self.pdf_panel.show()
            # 非表示直前の幅へ戻す（無ければ既定幅）
            self.resize(getattr(self, "_width_with_pdf", 1500), self.height())
            self.statusBar().showMessage("戯曲PDFビュー: 表示")
        else:
            # 復元用に現在の幅を控え、パネル幅ぶんウィンドウを縮めて右側を空ける
            self._width_with_pdf = self.width()
            sizes = self.splitter.sizes()
            panel_w = sizes[1] if len(sizes) > 1 else 0
            self.pdf_panel.hide()
            if panel_w > 0:
                self.resize(max(600, self.width() - panel_w), self.height())
            self.statusBar().showMessage(
                "戯曲PDFビュー: 非表示（右側が空くのでPowerPoint等を並べられます）")

    # ---- 出力デバイス（スピーカー）選択 ----
    def _update_device_label(self):
        """ステータスバー右端の出力先表示を最新化する。"""
        try:
            name = self.engine.current_device_name()
        except Exception:
            name = "(不明)"
        text = f"🔊 {name}"
        if text != self.device_lbl.text():
            self.device_lbl.setText(text)
            self.device_lbl.setToolTip(f"再生中の出力デバイス: {name}")

    def _rebuild_output_menu(self):
        """メニューを開くたびに現在のデバイス一覧で作り直す（新規接続を反映）。"""
        self.output_menu.clear()
        try:
            devices = self.engine.list_output_devices()
        except Exception as e:
            act = self.output_menu.addAction(f"(取得失敗: {e})")
            act.setEnabled(False)
            return
        if not devices:
            self.output_menu.addAction("(利用可能なデバイスがありません)").setEnabled(False)
            return
        cur = self.engine.current_device_name()
        group = QActionGroup(self.output_menu)
        group.setExclusive(True)
        for d in devices:
            act = self.output_menu.addAction(f"{d['name']}  ({d['samplerate']}Hz)")
            act.setCheckable(True)
            act.setChecked(d["name"] == cur)
            group.addAction(act)
            act.triggered.connect(
                lambda checked=False, idx=d["index"], name=d["name"]:
                    self._select_output_device(idx, name))

    def _select_output_device(self, index: int, name: str):
        try:
            self.engine.set_output_device(index)
        except Exception as e:
            log.warning("出力デバイス切替失敗 %s: %s", name, e)
            QMessageBox.critical(
                self, "出力デバイス",
                f"このデバイスを開けませんでした:\n{name}\n\n{e}\n\n既定のデバイスに戻しました。")
            self.statusBar().showMessage("出力デバイス切替に失敗（既定に戻しました）")
            self._update_device_label()
            return
        self.app_config["output_device"] = name
        save_app_config(self.app_config)
        self._update_device_label()
        self.statusBar().showMessage(f"出力デバイス: {name} ｜ {engine_desc(self.engine)}")

    def _restore_output_device(self):
        """前回選んだ出力デバイスを名前で復元（見つからなければ既定のまま）。"""
        name = self.app_config.get("output_device")
        if not name:
            return
        try:
            for d in self.engine.list_output_devices():
                if d["name"] == name:
                    if d["index"] != self.engine._device:
                        self.engine.set_output_device(d["index"])
                    self._update_device_label()
                    self.statusBar().showMessage(f"出力デバイス: {name}")
                    return
        except Exception as e:
            log.warning("出力デバイス復元失敗: %s", e)

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

    # ---- スマホ・リモート操作 ----
    def _on_remote_toggled(self, on: bool):
        if on:
            if self.remote is None:
                self.remote = RemoteServer(self)
            try:
                url = self.remote.start()
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"リモートを開始できません:\n{e}")
                self.remote_btn.setChecked(False)
                return
            self._show_remote_dialog(url)
            self.statusBar().showMessage(f"スマホ操作 待受中: {url}")
        else:
            if self.remote:
                self.remote.stop()
            self.statusBar().showMessage("スマホ操作を停止しました")

    def _show_remote_dialog(self, url: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("スマホ操作")
        v = QVBoxLayout(dlg)
        head = QLabel("同じWi-Fiにつないだスマホで、このQRコードを読み取ってください")
        v.addWidget(head)
        try:
            qr = QLabel()
            qr.setPixmap(make_qr_pixmap(url))
            qr.setAlignment(Qt.AlignCenter)
            v.addWidget(qr)
        except Exception as e:
            log.warning("QR生成失敗: %s", e)
        url_lbl = QLabel(url)
        url_lbl.setAlignment(Qt.AlignCenter)
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_lbl.setStyleSheet(
            f"font-size:15px;font-weight:bold;color:{t().url_text};")
        v.addWidget(url_lbl)
        hint = QLabel("（再生中の音だけが縦フェーダーで並び、指で同時に音量調整できます）")
        hint.setStyleSheet(f"color:{t().text_dim};font-size:12px;")
        v.addWidget(hint)
        btn = QPushButton("閉じる")
        btn.clicked.connect(dlg.accept)
        v.addWidget(btn)
        dlg.exec()

    def set_pad_gain_remote(self, idx: int, v: float):
        """スマホからの音量変更を反映（パッドのフェーダー表示も同期）。"""
        p = self.preset.pads[idx]
        p.gain = max(0.0, min(1.5, v))
        self._pad_set_gain(p, p.gain)
        cell = self.pad_buttons[idx]
        cell.fader.blockSignals(True)
        cell.fader.setValue(int(p.gain * 100))
        cell.fader.blockSignals(False)
        cell.vol_lbl.setText(f"{int(p.gain * 100)}%")
        self._mark_dirty()

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
                w.hide()  # deleteLater が処理されるまで残骸が見えるのを防ぐ
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
        s = clip_id[3:]  # 'pad' を除去
        if "_" in s:     # "pad{i}_s{j}" → i
            s = s.split("_", 1)[0]
        return int(s)

    # ---- パッド⇔クリップ（単一音源 / 確率モード両対応） ----
    def _pad_clip_specs(self, pad: Pad) -> list[tuple[str, str]]:
        """(clip_id, path) のリスト。確率モードなら全音源、通常なら単一。"""
        if pad.random_active:
            return [(f"pad{pad.index}_s{j}", s.path)
                    for j, s in enumerate(pad.sources) if s.path]
        if pad.path:
            return [(f"pad{pad.index}", pad.path)]
        return []

    def _pad_clip_ids(self, pad: Pad) -> list[str]:
        return [cid for cid, _ in self._pad_clip_specs(pad)]

    def _pad_pick_clip(self, pad: Pad) -> str | None:
        """再生するクリップを選ぶ。確率モードは重み付きランダム。"""
        specs = self._pad_clip_specs(pad)
        if not specs:
            return None
        if pad.random_active and len(specs) > 1:
            weights = [max(0.0, s.prob) for s in pad.sources if s.path]
            if sum(weights) <= 0:
                weights = [1.0] * len(specs)
            return random.choices([cid for cid, _ in specs], weights=weights, k=1)[0]
        return specs[0][0]

    def _pad_is_playing(self, pad: Pad) -> bool:
        return any(self.engine.is_playing(c) for c in self._pad_clip_ids(pad))

    def _pad_progress(self, pad: Pad) -> float | None:
        best = None
        for c in self._pad_clip_ids(pad):
            p = self.engine.get_progress(c)
            if p is not None and (best is None or p > best):
                best = p
        return best

    def _pad_fade_state(self, pad: Pad) -> tuple[int, float] | None:
        """パッドのフェード状態。フェード中のクリップを優先して返す。"""
        result = None
        for c in self._pad_clip_ids(pad):
            st = self.engine.get_fade_state(c)
            if st is None:
                continue
            if result is None or (st[0] != 0 and result[0] == 0):
                result = st
        return result

    def _pad_stop(self, pad: Pad, fade_out: float = 0.0):
        for c in self._pad_clip_ids(pad):
            self.engine.stop(c, fade_out)

    def _pad_set_gain(self, pad: Pad, gain: float):
        for c in self._pad_clip_ids(pad):
            self.engine.set_clip_gain(c, gain)

    def _unload_pad(self, pad: Pad):
        pre = f"pad{pad.index}"
        for cid in self.engine.loaded_clip_ids():
            if cid == pre or cid.startswith(pre + "_"):
                self.engine.unload_clip(cid)

    def _reload_engine_clips(self):
        """全クリップをバックグラウンドで（再）読み込みする。UIはブロックしない。"""
        # 世代を進め、進行中の古い完了通知を無効化
        self._load_generation += 1
        gen = self._load_generation
        self._pending.clear()
        self._load_errors.clear()

        # 既存クリップを一旦全アンロード
        for cid in self.engine.loaded_clip_ids():
            self.engine.unload_clip(cid)

        to_load = []
        for p in self.preset.pads:
            if not p.assigned:
                continue
            for cid, path in self._pad_clip_specs(p):
                if Path(path).exists():
                    to_load.append((cid, path))
                else:
                    self._load_errors.append(
                        f"パッド{p.index+1}: ファイルが見つかりません {path}")

        self._pending = {cid for cid, _ in to_load}
        self._pending_total = len(to_load)
        for p in self.preset.pads:
            if p.assigned:
                self.pad_buttons[p.index].set_loading(True)
        for cid, path in to_load:
            self.loader.submit(cid, path, gen)
        self._update_load_status()

    def _on_clip_loaded(self, clip_id: str, ok: bool, err: str, generation: int):
        # 古い世代の完了は破棄（再読込が重なった場合の取り違え防止）
        if generation != self._load_generation:
            return
        idx = self._pad_index_from_clip(clip_id)
        if not ok:
            self._load_errors.append(f"パッド{idx+1}: 読み込み失敗 ({err})")
        self._pending.discard(clip_id)
        # 同じパッドの残り音源がもう無ければ「読み込み中」を解除
        if 0 <= idx < len(self.pad_buttons):
            if not any(self._pad_index_from_clip(c) == idx for c in self._pending):
                self.pad_buttons[idx].set_loading(False)
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
        cid = self._pad_pick_clip(pad)   # 確率モードならランダムに1音選択
        if cid is None:
            return
        if self.engine.get_clip(cid) is None:
            self.statusBar().showMessage("読み込み中… 完了までお待ちください")
            return
        self.engine.play(
            cid, gain=pad.gain, loop=pad.loop,
            fade_in=pad.fade_in, exclusive=pad.exclusive, retrigger=True)

    def toggle_pad(self, pad: Pad):
        """クリック時の挙動: 再生中なら停止、停止中なら再生（トグル）。

        連続再生が有効なパッドは停止せず、押すたびに重ねて発音する。
        """
        if not pad.assigned:
            return
        if pad.retrigger:
            self.trigger_pad(pad)
        elif self._pad_is_playing(pad):
            self.stop_pad(pad)
        else:
            self.trigger_pad(pad)

    def stop_pad(self, pad: Pad):
        self._pad_stop(pad, fade_out=pad.fade_out)

    def stop_all(self):
        self.engine.stop_all(fade_out=self.preset.default_fade_out)

    def edit_pad(self, pad: Pad):
        dlg = PadEditDialog(pad, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.result_pad()
            # 該当パッドのクリップをバックグラウンドで再ロード（複数音源対応）
            self._unload_pad(pad)
            cell = self._button_for(pad)
            specs = self._pad_clip_specs(pad)
            real = [(cid, path) for cid, path in specs if Path(path).exists()]
            missing = [path for cid, path in specs if not Path(path).exists()]
            if real:
                cell.set_loading(True)
                for cid, path in real:
                    self._pending.add(cid)
                    self.loader.submit(cid, path, self._load_generation)
                self._pending_total = max(self._pending_total, len(self._pending))
                self._update_load_status()
            else:
                cell.refresh()
            if missing:
                QMessageBox.warning(self, "読み込み警告",
                                    "ファイルが見つかりません:\n" + "\n".join(missing))
            self._rebuild_key_map()
            self._mark_dirty()

    def clear_pad(self, pad: Pad):
        self._unload_pad(pad)
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

    def _preset_dir(self) -> str:
        """プリセットを開く/保存するときの初期フォルダ。
        前回使った場所 → アプリ同梱の presets → 指定なし の順で決める。"""
        last = self.app_config.get("preset_dir", "")
        if last and Path(last).is_dir():
            return last
        base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)
        d = base / "presets"
        return str(d) if d.is_dir() else ""

    def _remember_preset_dir(self, path: str):
        self.app_config["preset_dir"] = str(Path(path).parent)
        save_app_config(self.app_config)

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "プリセットを開く",
                                              self._preset_dir(),
                                              "プリセット (*.json)")
        if not path:
            return
        try:
            self.preset = Preset.load(path)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"読み込めません:\n{e}")
            return
        self.preset_path = path
        self._remember_preset_dir(path)
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
        path, _ = QFileDialog.getSaveFileName(
            self, "名前を付けて保存",
            str(Path(self._preset_dir()) / "preset.json"),
            "プリセット (*.json)")
        if path:
            self.preset_path = path
            self._remember_preset_dir(path)
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
            # 同じキーをもう一度押すと停止（トグル）
            self.toggle_pad(pad)
            return
        super().keyPressEvent(ev)

    # ---- 状態反映 ----
    def _refresh_playing_state(self):
        # 全ボイス状態を1回のロックでまとめて取得（パッドごとのロック競合を避ける）
        snap = self.engine.voice_snapshot()
        for btn in self.pad_buttons:
            pad = btn.pad
            if not pad.assigned or btn.is_loading:
                continue
            prog = None
            fade = None
            for c in self._pad_clip_ids(pad):
                st = snap.get(c)
                if st is None:
                    continue
                if prog is None or st[0] > prog:
                    prog = st[0]
                if st[1] != 0 and (fade is None or fade[0] == 0):
                    fade = (st[1], st[2])
            btn._set_playing(prog is not None, prog or 0.0, fade)
        self.voices_lbl.setText(f"再生中: {self.engine.active_voice_count()}")

    def _mark_dirty(self):
        self._update_title(dirty=True)

    def _update_title(self, dirty: bool = False):
        name = Path(self.preset_path).name if self.preset_path else "未保存"
        mark = " *" if dirty else ""
        self.setWindowTitle(f"{APP_NAME} — {name}{mark}")

    def closeEvent(self, ev):
        try:
            log.info("終了 underflow=%d", self.engine.underflow_count)
        except Exception:
            pass
        try:
            if self.remote:
                self.remote.stop()
        except Exception:
            pass
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
    app.setWindowIcon(app_icon())
    set_app_user_model_id()
    app.setStyle("Fusion")
    # Noto Sans JP を読み込む（割当キーの大きな表示などに使用）
    _fp = resource_path("assets/fonts/NotoSansJP.ttf")
    if _fp.exists():
        _fid = QFontDatabase.addApplicationFont(str(_fp))
        _fams = QFontDatabase.applicationFontFamilies(_fid)
        if _fams:
            global SPLASH_FONT_FAMILY
            SPLASH_FONT_FAMILY = _fams[0]
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
        log.info("オーディオ初期化: %s", engine_desc(engine))
    except Exception as e:
        log.exception("オーディオ初期化失敗")
        if splash:
            splash.close()
        QMessageBox.critical(None, "オーディオ初期化エラー",
                             f"オーディオデバイスを開けませんでした:\n{e}\n\n{traceback.format_exc()}")
        return 1

    if splash:
        splash.status("画面を準備中…")
    win = MainWindow(engine)
    win.show()
    if splash:
        splash.close_after_min(win)

    # ビルド検証用: TIPS_SE_SELFTEST=1 のとき、起動確認後に自動終了する
    if os.environ.get("TIPS_SE_SELFTEST"):
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

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
