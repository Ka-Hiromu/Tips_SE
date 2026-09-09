"""ロゴ原画（tips_onkyo_logo_6b.jpg）から配布用のアセットを生成する。

出力:
  assets/logo.png      — スプラッシュ用のロゴ全体（1024px）
  assets/icon.ico      — アプリ／タスクバー用アイコン（菱形＋「音」・マルチ解像度）
  assets/icon_256.png  — 同アイコンの確認用PNG

再生成: .venv\Scripts\python.exe tools\make_assets.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tips_onkyo_logo_6b.jpg"
OUT = ROOT / "assets"

# 原画（2080x2080）での菱形エンブレムの中心と切り出し半径（実測）。
# 切り出し矩形には四隅のカギ括弧が入り込むので、菱形マスクで外側を落とす。
EMBLEM_CENTER = (711, 662)
EMBLEM_RADIUS = 600
MASK_RADIUS = 590  # 最外の薄い菱形（半径541x573）を欠けさせない余裕を持たせる

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_FILL = 0.94       # アイコン内で菱形が占める割合
SMALL_PX = 32          # これ以下は余白を詰め、縮小で沈む線をガンマで持ち上げる
SMALL_GAMMA = 0.55


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()  # QByteArray を渡すと寿命管理でクラッシュするため内部バッファを使う
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _write_ico(images: list[QImage], path: Path):
    """複数解像度のPNGエントリを持つICOを書き出す（Vista以降が対象）。"""
    blobs = [_png_bytes(im) for im in images]
    offset = 6 + 16 * len(blobs)
    header = struct.pack("<HHH", 0, 1, len(blobs))
    entries = b""
    for im, blob in zip(images, blobs):
        w = 0 if im.width() >= 256 else im.width()
        h = 0 if im.height() >= 256 else im.height()
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def _extract_emblem(src: QImage) -> QImage:
    cx, cy = EMBLEM_CENTER
    r = EMBLEM_RADIUS
    out = src.copy(QRect(cx - r, cy - r, r * 2, r * 2)).convertToFormat(QImage.Format_ARGB32)
    h, w = out.height(), out.width()
    ys, xs = np.mgrid[0:h, 0:w]
    inside = (np.abs(xs - r) + np.abs(ys - r)) <= MASK_RADIUS
    buf = np.frombuffer(out.bits(), np.uint8).reshape(h, out.bytesPerLine() // 4, 4)[:, :w]
    buf[~inside] = (0, 0, 0, 255)
    return out


def make_icon(emblem: QImage, size: int) -> QImage:
    small = size <= SMALL_PX
    side = size if small else int(size * ICON_FILL)
    scaled = emblem.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if small:
        scaled = scaled.convertToFormat(QImage.Format_ARGB32)
        h, w = scaled.height(), scaled.width()
        buf = np.frombuffer(scaled.bits(), np.uint8).reshape(h, scaled.bytesPerLine() // 4, 4)[:, :w]
        lut = (255.0 * (np.arange(256) / 255.0) ** SMALL_GAMMA).astype(np.uint8)
        buf[:, :, :3] = lut[buf[:, :, :3]]
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(QColor("#000000"))
    p = QPainter(canvas)
    p.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return canvas


def main() -> int:
    QGuiApplication([])  # QImage の変換に QGuiApplication が要る
    if not SRC.exists():
        print(f"ロゴ原画が見つかりません: {SRC}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    src = QImage(str(SRC))

    logo = src.scaled(1024, 1024, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    logo.save(str(OUT / "logo.png"), "PNG")

    emblem = _extract_emblem(src)
    icons = [make_icon(emblem, s) for s in ICON_SIZES]
    _write_ico(icons, OUT / "icon.ico")
    icons[-1].save(str(OUT / "icon_256.png"), "PNG")
    print("生成:", OUT / "logo.png", OUT / "icon.ico", OUT / "icon_256.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
