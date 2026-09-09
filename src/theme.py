"""配色テーマ。UI で使う色はすべてここを参照する。

MONO（黒地に白）と CLASSIC（従来のブルーグレー）の2種類。
レベルメーターのバー・クリップLEDは本番中の判断材料なので、
どちらのテーマでも色を変えない（MeterColors は共通）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MeterColors:
    """レベルメーターの配色（音響上の意味を持つので原則いじらない）。"""
    grad: tuple[str, ...] = ("#1faa45", "#3fd968", "#e6d23a", "#ef8a2b", "#e53935")
    grad_stops: tuple[float, ...] = (0.0, 0.62, 0.78, 0.90, 1.0)
    peak: str = "#ffffff"
    clip_on: str = "#ff1744"
    clip_off: str = "#3a1212"


@dataclass(frozen=True)
class Theme:
    name: str
    label: str

    # 基本（QPalette に流し込む）
    window: str
    base: str            # 入力欄・リストの背景
    text: str
    text_dim: str        # 補助テキスト
    button: str
    button_hover: str
    border: str
    highlight: str       # 選択色
    highlight_text: str
    dialog_border: str   # ダイアログの外枠（背面と同化させないため）

    # パッド（未再生・割当済み）
    pad_bg: str
    pad_border: str
    pad_hover: str
    pad_text: str
    # パッド（未割当）
    pad_empty_bg: str
    pad_empty_border: str
    pad_empty_text: str
    # パッド（再生中）: played/unplayed は「フェード強度0→1」で補間する2色
    pad_play_border: str
    pad_played_lo: str
    pad_played_hi: str
    pad_unplayed_lo: str
    pad_unplayed_hi: str
    pad_play_text: str
    # パッド（読み込み中）
    pad_load_bg: str
    pad_load_border: str
    pad_load_text: str
    # 背景に薄く出る割当キー文字
    pad_key_overlay: str

    # フェーダー
    fader_groove: str
    fader_fill: str
    fader_handle: str
    fader_handle_border: str
    fader_handle_hover: str
    fader_handle_hover_border: str

    # トップバー
    stop_bg: str
    stop_hover: str
    stop_text: str
    toggle_bg: str
    toggle_hover: str
    edit_on_bg: str
    remote_on_bg: str
    toggle_on_text: str

    # 情報表示
    device_text: str
    url_text: str
    accent_text: str     # 強調テキスト（読み込み中など）

    # メーター周辺（バー本体以外）
    meter_panel: str
    meter_track: str
    meter_frame: str
    meter_scale: str
    meter_grid_major: str
    meter_grid_minor: str

    # 円グラフ（確率配分）
    pie_colors: tuple[str, ...]
    pie_empty: str
    pie_edge: str

    # 戯曲PDFビュー
    pdf_bg: str
    pdf_placeholder_border: str

    # タイトルバー（DWM）。None ならシステム既定に任せる
    caption_active: str | None
    caption_inactive: str | None
    caption_text_active: str | None
    caption_text_inactive: str | None

    meter: MeterColors = field(default_factory=MeterColors)


MONO = Theme(
    name="mono",
    label="モノクロ（黒地に白）",
    window="#000000",
    base="#0d0d0d",
    text="#ffffff",
    text_dim="#8a8a8a",
    button="#161616",
    button_hover="#242424",
    border="#333333",
    highlight="#ffffff",
    highlight_text="#000000",
    dialog_border="#ffffff",
    pad_bg="#141414",
    pad_border="#3d3d3d",
    pad_hover="#1f1f1f",
    pad_text="#ffffff",
    pad_empty_bg="#080808",
    pad_empty_border="#343434",
    pad_empty_text="#6b6b6b",
    pad_play_border="#ffffff",
    pad_played_lo="#242424",
    pad_played_hi="#565656",
    pad_unplayed_lo="#0a0a0a",
    pad_unplayed_hi="#161616",
    pad_play_text="#ffffff",
    pad_load_bg="#1a1a1a",
    pad_load_border="#5a5a5a",
    pad_load_text="#d6d6d6",
    pad_key_overlay="rgba(255,255,255,0.10)",
    fader_groove="#1e1e1e",
    fader_fill="#c8c8c8",
    fader_handle="#ffffff",
    fader_handle_border="#8a8a8a",
    fader_handle_hover="#ffffff",
    fader_handle_hover_border="#ffffff",
    stop_bg="#c62828",
    stop_hover="#d32f2f",
    stop_text="#ffffff",
    toggle_bg="#1a1a1a",
    toggle_hover="#262626",
    edit_on_bg="#ffffff",
    remote_on_bg="#ffffff",
    toggle_on_text="#000000",
    device_text="#b5b5b5",
    url_text="#ffffff",
    accent_text="#e0e0e0",
    meter_panel="#050505",
    meter_track="#0a0a0a",
    meter_frame="#3a3a3a",
    meter_scale="#8a8a8a",
    meter_grid_major="#5c5c5c",
    meter_grid_minor="#242424",
    pie_colors=("#ffffff", "#c9c9c9", "#a0a0a0", "#7d7d7d", "#616161",
                "#4a4a4a", "#e4e4e4", "#8e8e8e", "#6e6e6e", "#565656"),
    pie_empty="#242424",
    pie_edge="#000000",
    pdf_bg="#0a0a0a",
    pdf_placeholder_border="#3a3a3a",
    caption_active="#000000",
    caption_inactive="#ffffff",
    caption_text_active="#ffffff",
    caption_text_inactive="#000000",
)

CLASSIC = Theme(
    name="classic",
    label="クラシック（ブルーグレー）",
    window="#1c2529",
    base="#263238",
    text="#eceff1",
    text_dim="#90a4ae",
    button="#37474f",
    button_hover="#455a64",
    border="#546e7a",
    highlight="#1565c0",
    highlight_text="#ffffff",
    dialog_border="#78909c",
    pad_bg="#37474f",
    pad_border="#546e7a",
    pad_hover="#455a64",
    pad_text="#eceff1",
    pad_empty_bg="#263238",
    pad_empty_border="#455a64",
    pad_empty_text="#78909c",
    pad_play_border="#1b5e20",
    pad_played_lo="#0a2410",
    pad_played_hi="#2e7d32",
    pad_unplayed_lo="#061a0c",
    pad_unplayed_hi="#14441a",
    pad_play_text="#ffffff",
    pad_load_bg="#5d4037",
    pad_load_border="#795548",
    pad_load_text="#ffe0b2",
    pad_key_overlay="rgba(255,255,255,0.10)",
    fader_groove="#22303a",
    fader_fill="#4fc3f7",
    fader_handle="#eceff1",
    fader_handle_border="#90a4ae",
    fader_handle_hover="#ffffff",
    fader_handle_hover_border="#4fc3f7",
    stop_bg="#c62828",
    stop_hover="#d32f2f",
    stop_text="#ffffff",
    toggle_bg="#37474f",
    toggle_hover="#455a64",
    edit_on_bg="#1565c0",
    remote_on_bg="#00838f",
    toggle_on_text="#ffffff",
    device_text="#80cbc4",
    url_text="#80d8ff",
    accent_text="#ffe0b2",
    meter_panel="#11171a",
    meter_track="#0a0e10",
    meter_frame="#2b383f",
    meter_scale="#90a4ae",
    meter_grid_major="#5b6b73",
    meter_grid_minor="#222d33",
    pie_colors=("#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8",
                "#4db6ac", "#fff176", "#a1887f", "#90a4ae", "#f06292"),
    pie_empty="#37474f",
    pie_edge="#1c2529",
    pdf_bg="#202a2f",
    pdf_placeholder_border="#3a4750",
    caption_active=None,
    caption_inactive=None,
    caption_text_active=None,
    caption_text_inactive=None,
)

THEMES: dict[str, Theme] = {t.name: t for t in (MONO, CLASSIC)}
DEFAULT_THEME = MONO.name

_current: Theme = MONO


def t() -> Theme:
    """現在のテーマ。"""
    return _current


def set_theme(name: str) -> Theme:
    global _current
    _current = THEMES.get(name, THEMES[DEFAULT_THEME])
    return _current


def palette_of(th: Theme) -> "QPalette":
    """テーマから QPalette を作る（Fusion スタイル前提）。"""
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    p.setColor(QPalette.Window, QColor(th.window))
    p.setColor(QPalette.WindowText, QColor(th.text))
    p.setColor(QPalette.Base, QColor(th.base))
    p.setColor(QPalette.AlternateBase, QColor(th.button))
    p.setColor(QPalette.Text, QColor(th.text))
    p.setColor(QPalette.Button, QColor(th.button))
    p.setColor(QPalette.ButtonText, QColor(th.text))
    p.setColor(QPalette.Highlight, QColor(th.highlight))
    p.setColor(QPalette.HighlightedText, QColor(th.highlight_text))
    p.setColor(QPalette.ToolTipBase, QColor(th.base))
    p.setColor(QPalette.ToolTipText, QColor(th.text))
    p.setColor(QPalette.PlaceholderText, QColor(th.text_dim))
    # Fusion が枠線・立体感に使う色。既定のままだと黒地で境界が見えない
    p.setColor(QPalette.Mid, QColor(th.border))
    p.setColor(QPalette.Dark, QColor(th.border))
    p.setColor(QPalette.Midlight, QColor(th.button))
    p.setColor(QPalette.Light, QColor(th.button_hover))
    p.setColor(QPalette.Shadow, QColor(th.window))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(th.text_dim))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(th.text_dim))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(th.text_dim))
    return p


def app_qss(th: Theme) -> str:
    """アプリ全体のスタイルシート。
    QLabel には背景を指定しない（パッド上の透過ラベルを潰さないため）。"""
    return f"""
QMainWindow {{ background:{th.window}; }}
QDialog {{ background:{th.window}; border:1px solid {th.dialog_border}; }}
QMenuBar {{ background:{th.base}; color:{th.text}; border-bottom:1px solid {th.border}; }}
QMenuBar::item {{ padding:5px 10px; background:transparent; }}
QMenuBar::item:selected {{ background:{th.button_hover}; }}
QMenu {{ background:{th.base}; color:{th.text}; border:1px solid {th.border}; padding:4px; }}
QMenu::item {{ padding:5px 22px 5px 22px; }}
QMenu::item:selected {{ background:{th.highlight}; color:{th.highlight_text}; }}
QMenu::separator {{ height:1px; background:{th.border}; margin:4px 8px; }}
QStatusBar {{ background:{th.base}; color:{th.text_dim}; border-top:1px solid {th.border}; }}
QStatusBar::item {{ border:none; }}
QPushButton {{ background:{th.button}; color:{th.text}; border:1px solid {th.border};
               border-radius:6px; padding:5px 10px; }}
QPushButton:hover {{ background:{th.button_hover}; }}
QPushButton:pressed {{ background:{th.base}; }}
QPushButton:disabled {{ color:{th.text_dim}; border-color:{th.base}; }}
QLineEdit {{
    background:{th.base}; color:{th.text}; border:1px solid {th.border};
    border-radius:4px; padding:3px 6px; selection-background-color:{th.highlight};
    selection-color:{th.highlight_text}; }}
QCheckBox, QRadioButton, QGroupBox {{ color:{th.text}; }}
QCheckBox::indicator {{ width:14px; height:14px; border:1px solid {th.border};
    border-radius:3px; background:{th.base}; }}
QCheckBox::indicator:hover {{ border-color:{th.text_dim}; }}
QCheckBox::indicator:checked {{ background:{th.highlight}; border-color:{th.highlight}; }}
QSplitter::handle {{ background:{th.border}; }}
QSplitter::handle:horizontal {{ width:3px; }}
QToolTip {{ background:{th.base}; color:{th.text}; border:1px solid {th.border}; padding:3px; }}
QScrollBar:vertical {{ background:{th.window}; width:12px; margin:0; }}
QScrollBar::handle:vertical {{ background:{th.border}; border-radius:6px; min-height:28px; }}
QScrollBar::handle:vertical:hover {{ background:{th.text_dim}; }}
QScrollBar:horizontal {{ background:{th.window}; height:12px; margin:0; }}
QScrollBar::handle:horizontal {{ background:{th.border}; border-radius:6px; min-width:28px; }}
QScrollBar::handle:horizontal:hover {{ background:{th.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
"""


def fader_qss(th: Theme) -> str:
    """音量フェーダー（QSlider）のスタイル。"""
    return (
        f"QSlider::groove:horizontal{{height:8px;background:{th.fader_groove};border-radius:4px;}}"
        f"QSlider::sub-page:horizontal{{background:{th.fader_fill};border-radius:4px;}}"
        f"QSlider::add-page:horizontal{{background:{th.fader_groove};border-radius:4px;}}"
        f"QSlider::handle:horizontal{{background:{th.fader_handle};"
        f"border:1px solid {th.fader_handle_border};"
        "width:16px;margin:-5px 0;border-radius:8px;}"
        f"QSlider::handle:horizontal:hover{{background:{th.fader_handle_hover};"
        f"border-color:{th.fader_handle_hover_border};}}"
    )
