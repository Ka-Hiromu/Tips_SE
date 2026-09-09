"""Windows 固有の見た目まわり（タイトルバー配色・タスクバー・スプラッシュ・QR）。

DWM や AppUserModelID は Windows 10/11 でのみ意味を持つ。
呼び出し側が OS を気にせず使えるよう、失敗はすべてここで握り潰す。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QSplashScreen, QWidget

from appconfig import resource_path
from theme import t

# DwmSetWindowAttribute の属性ID（Windows 11 以降で有効）
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

_splash_font_family = ""   # 同梱フォント読み込み後に load_bundled_font() が設定する


def _colorref(hex_color: str) -> int:
    """#rrggbb → COLORREF(0x00bbggrr)。"""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def set_caption_colors(win: QWidget, caption: str | None, text: str | None,
                       dark: bool) -> None:
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


def app_icon() -> QIcon:
    """アプリ／タスクバーのアイコン（ロゴの「音」を抜き出したもの）。"""
    p = resource_path("assets/icon.ico")
    return QIcon(str(p)) if p.exists() else QIcon()


def set_app_user_model_id() -> None:
    """タスクバーで自前アイコンが使われるようにする（開発時の pythonw 対策）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TipsSE.PonDashi")
    except Exception:
        pass


def load_bundled_font() -> str:
    """同梱の Noto Sans JP を登録し、ファミリ名を返す（失敗時は空文字）。"""
    global _splash_font_family
    p = resource_path("assets/fonts/NotoSansJP.ttf")
    if not p.exists():
        return ""
    fid = QFontDatabase.addApplicationFont(str(p))
    fams = QFontDatabase.applicationFontFamilies(fid)
    _splash_font_family = fams[0] if fams else ""
    return _splash_font_family


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


class SplashScreen(QSplashScreen):
    """起動待ちに出すロゴのスプラッシュ。"""

    MIN_VISIBLE_SEC = 1.2

    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._text = ""
        self._shown_at = time.monotonic()

    def status(self, text: str) -> None:
        self._text = text
        self.repaint()
        QApplication.processEvents()

    def drawContents(self, painter: QPainter):
        f = QFont(_splash_font_family or painter.font().family(), 10)
        painter.setFont(f)
        painter.setPen(QColor(t().text_dim))
        painter.drawText(self.rect().adjusted(0, 0, 0, -22),
                         Qt.AlignBottom | Qt.AlignHCenter, self._text)

    def close_after_min(self, win: QWidget | None = None) -> None:
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
