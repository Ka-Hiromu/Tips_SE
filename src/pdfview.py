"""
戯曲PDFビューア + 注釈（音出しスタンプ / メモ）。

QPdfDocument でページを画像化し QGraphicsScene に敷く。その上に注釈アイテムを
重ねることで、ズーム非依存（座標はページ正規化値で保持）の貼り付けを実現する。
- 編集モード: クリックでスタンプ/テキストを配置、ドラッグで移動、Deleteで削除
- 本番モード: スタンプをクリックすると対象パッドを再生（戯曲を見ながら音出し）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPen, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsTextItem,
)
from PySide6.QtPdf import QPdfDocument

from models import Annotation


class StampItem(QGraphicsItem):
    """音出しスタンプ（番号入りの丸）。位置はアイテム中心。"""

    R = 16

    def __init__(self, ann: Annotation, view: "ScriptView"):
        super().__init__()
        self.ann = ann
        self.view = view
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(10)

    def boundingRect(self) -> QRectF:
        r = self.R
        return QRectF(-r - 2, -r - 2, 2 * r + 4, 2 * r + 4)

    def paint(self, p: QPainter, opt, widget=None):
        r = self.R
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(self.ann.color))
        pen = QPen(QColor("#ffffff"), 2)
        if self.isSelected():
            pen = QPen(QColor("#ffeb3b"), 3)
        p.setPen(pen)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(QColor("#ffffff"))
        f = QFont(); f.setBold(True); f.setPointSize(11); p.setFont(f)
        p.drawText(QRectF(-r, -r, 2 * r, 2 * r), Qt.AlignCenter, str(self.ann.pad + 1))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.view._sync_item(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, ev):
        if not self.view.edit_mode:
            self.view.stampClicked.emit(self.ann.pad)
            ev.accept()
            return
        super().mousePressEvent(ev)


class TextNoteItem(QGraphicsTextItem):
    """きっかけメモ（付箋風テキストボックス）。位置は左上。"""

    def __init__(self, ann: Annotation, view: "ScriptView"):
        super().__init__(ann.text)
        self.ann = ann
        self.view = view
        self.setDefaultTextColor(QColor("#1a1a1a"))
        f = self.font(); f.setPointSize(ann.font_size); self.setFont(f)
        self.setZValue(9)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def paint(self, p: QPainter, opt, widget=None):
        p.setBrush(QColor(255, 245, 157, 235))
        p.setPen(QPen(QColor("#f9a825"), 1))
        p.drawRect(self.boundingRect().adjusted(0, 0, -0.5, -0.5))
        super().paint(p, opt, widget)

    def focusOutEvent(self, ev):
        self.ann.text = self.toPlainText()
        self.view._emit_changed()
        super().focusOutEvent(ev)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.view._sync_item(self)
        return super().itemChange(change, value)


class ScriptView(QGraphicsView):
    stampClicked = Signal(int)        # 本番モードでスタンプクリック → pad index
    changed = Signal()                # 注釈の追加/移動/編集/削除
    pageChanged = Signal(int, int)    # current(0-based), total
    toolCleared = Signal()            # 配置ツールを自動解除した（UIのチェック解除用）

    GAP = 14  # ページ間の余白(px)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setBackgroundBrush(QColor("#202a2f"))
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setDragMode(QGraphicsView.NoDrag)

        self.doc = QPdfDocument(self)
        self.page = 0            # 現在の表示先頭ページ（スクロール位置から算出）
        self.zoom = 1.0
        self._fit_scale = 1.0
        self.edit_mode = False
        self.tool: str | None = None      # None / "stamp" / "text"
        self.stamp_pad = 0
        self.pad_count = 16

        self._anns: list[Annotation] = []
        self._items: list[QGraphicsItem] = []
        # 連続スクロール用のページ配置とレンダリングキャッシュ
        self._layout: list[dict] = []         # {x,y,w,h} ページごとの scene座標
        self._page_pixmaps: dict[int, QGraphicsItem] = {}
        self._placeholders: list[QGraphicsItem] = []

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

        # リサイズ時の再フィットをデバウンス
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._relayout_keep)
        self._last_vw = 0

    # ---- PDF/注釈の読み込み ----
    def load_pdf(self, path: str) -> bool:
        self.doc.load(path)
        ready = self.doc.status() == QPdfDocument.Status.Ready
        self.page = 0
        self._relayout()
        return ready

    def has_pdf(self) -> bool:
        return self.doc.status() == QPdfDocument.Status.Ready

    def clear(self):
        self.doc.close()
        self._anns = []
        self.page = 0
        self.zoom = 1.0
        self._relayout()

    def set_annotations(self, anns: list[Annotation]):
        self._anns = anns
        self._relayout()

    def export_annotations(self) -> list[Annotation]:
        return self._anns  # 位置は itemChange で逐次同期済み

    # ---- ページ/ズーム ----
    def page_count(self) -> int:
        return self.doc.pageCount()

    def set_page(self, n: int):
        """指定ページの先頭が見えるようスクロール。"""
        if not self.has_pdf() or not self._layout:
            return
        n = max(0, min(n, len(self._layout) - 1))
        self.verticalScrollBar().setValue(int(self._layout[n]["y"]))

    def next_page(self):
        self.set_page(self.page + 1)

    def prev_page(self):
        self.set_page(self.page - 1)

    def zoom_in(self):
        self.zoom = min(4.0, self.zoom * 1.25)
        self._relayout_keep()

    def zoom_out(self):
        self.zoom = max(0.4, self.zoom / 1.25)
        self._relayout_keep()

    def zoom_reset(self):
        self.zoom = 1.0
        self._relayout_keep()

    # ---- 編集モード/ツール ----
    def set_edit_mode(self, on: bool):
        self.edit_mode = on
        if not on:
            self.tool = None
        for it in self._items:
            self._apply_flags(it)

    def set_tool(self, tool: str | None):
        self.tool = tool

    def set_stamp_pad(self, pad_index: int):
        self.stamp_pad = pad_index

    # ---- レイアウト/描画（連続スクロール + 可視ページのみ遅延レンダリング） ----
    def _relayout(self):
        """全ページを縦に積むレイアウトを再構築。ページ画像は可視分のみ描画。"""
        self._scene.clear()
        self._items = []
        self._page_pixmaps = {}
        self._placeholders = []
        self._layout = []
        if not self.has_pdf():
            self._scene.setSceneRect(0, 0, 10, 10)
            return

        n = self.doc.pageCount()
        sizes = [self.doc.pagePointSize(p) for p in range(n)]
        vw = max(200, self.viewport().width() - 4)
        self._last_vw = self.viewport().width()
        max_pt_w = max((s.width() for s in sizes), default=1.0) or 1.0
        self._fit_scale = vw / max_pt_w
        s = self._fit_scale * self.zoom
        max_w = max_pt_w * s

        y = float(self.GAP)
        for p in range(n):
            w = max(1.0, sizes[p].width() * s)
            h = max(1.0, sizes[p].height() * s)
            x = (max_w - w) / 2.0
            self._layout.append({"x": x, "y": y, "w": w, "h": h})
            # 仮の白ページ（描画前のプレースホルダ）
            ph = self._scene.addRect(x, y, w, h, QPen(QColor("#3a4750")),
                                     QColor("#ffffff"))
            ph.setZValue(-1)
            self._placeholders.append(ph)
            y += h + self.GAP

        self._scene.setSceneRect(0, 0, max_w, y)

        for ann in self._anns:
            self._add_item(ann)

        self._render_visible()
        self._update_current_page()

    def _relayout_keep(self):
        """現在ページを保ったままレイアウトし直す（ズーム/リサイズ時）。"""
        cur = self.page
        self._relayout()
        self.set_page(cur)

    def _render_visible(self):
        """ビューポートに入っている（近傍含む）ページだけ画像化する。"""
        if not self.has_pdf() or not self._layout:
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        margin = vis.height()
        s = self._fit_scale * self.zoom
        keep_top = vis.top() - 3 * margin
        keep_bottom = vis.bottom() + 3 * margin
        for p, lay in enumerate(self._layout):
            top, bottom = lay["y"], lay["y"] + lay["h"]
            near = bottom >= vis.top() - margin and top <= vis.bottom() + margin
            if near and p not in self._page_pixmaps:
                W = max(1, int(self.doc.pagePointSize(p).width() * s))
                H = max(1, int(self.doc.pagePointSize(p).height() * s))
                img = self.doc.render(p, QSize(W, H))
                item = self._scene.addPixmap(QPixmap.fromImage(img))
                item.setPos(lay["x"], lay["y"])
                item.setZValue(0)
                self._page_pixmaps[p] = item
            elif not near and p in self._page_pixmaps:
                # メモリ節約: 遠いページの画像は破棄（プレースホルダは残す）
                if bottom < keep_top or top > keep_bottom:
                    self._scene.removeItem(self._page_pixmaps.pop(p))

    def _page_at_scene_y(self, sy: float) -> int:
        for p, lay in enumerate(self._layout):
            if lay["y"] <= sy <= lay["y"] + lay["h"] + self.GAP:
                return p
        if self._layout and sy < self._layout[0]["y"]:
            return 0
        return max(0, len(self._layout) - 1)

    def _update_current_page(self):
        if not self._layout:
            self.pageChanged.emit(0, 0)
            return
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        p = self._page_at_scene_y(vis.top() + 1)
        if p != self.page:
            self.page = p
        self.pageChanged.emit(self.page, self.doc.pageCount())

    def _on_scroll(self, _value):
        self._render_visible()
        self._update_current_page()

    def _add_item(self, ann: Annotation) -> QGraphicsItem:
        if ann.kind == "stamp":
            it = StampItem(ann, self)
        else:
            it = TextNoteItem(ann, self)
        self._place_item(it)
        self._apply_flags(it)
        self._scene.addItem(it)
        self._items.append(it)
        return it

    def _place_item(self, it: QGraphicsItem):
        if not (0 <= it.ann.page < len(self._layout)):
            return
        lay = self._layout[it.ann.page]
        it.setPos(lay["x"] + it.ann.x * lay["w"], lay["y"] + it.ann.y * lay["h"])

    def _apply_flags(self, it: QGraphicsItem):
        movable = self.edit_mode
        it.setFlag(QGraphicsItem.ItemIsMovable, movable)
        it.setFlag(QGraphicsItem.ItemIsSelectable, movable)
        if isinstance(it, TextNoteItem):
            it.setTextInteractionFlags(
                Qt.TextEditorInteraction if self.edit_mode else Qt.NoTextInteraction)

    def _sync_item(self, it: QGraphicsItem):
        if not self._layout:
            return
        pos = it.scenePos()
        # アイテム基準点（stampは中心, textは左上）から所属ページを判定
        ref_y = pos.y() + (0 if isinstance(it, StampItem) else 4)
        p = self._page_at_scene_y(ref_y)
        lay = self._layout[p]
        it.ann.page = p
        it.ann.x = min(1.0, max(0.0, (pos.x() - lay["x"]) / lay["w"]))
        it.ann.y = min(1.0, max(0.0, (pos.y() - lay["y"]) / lay["h"]))
        self._emit_changed()

    def _emit_changed(self):
        self.changed.emit()

    # ---- 配置/操作 ----
    def mousePressEvent(self, ev):
        if (self.edit_mode and self.tool and ev.button() == Qt.LeftButton
                and self.has_pdf()):
            item = self.itemAt(ev.position().toPoint())
            if item is None or not hasattr(item, "ann"):
                self._place_at(ev.position().toPoint())
                return
        super().mousePressEvent(ev)

    def _place_at(self, vpos):
        if not self._layout:
            return
        sp = self.mapToScene(vpos)
        p = self._page_at_scene_y(sp.y())
        lay = self._layout[p]
        nx = min(1.0, max(0.0, (sp.x() - lay["x"]) / lay["w"]))
        ny = min(1.0, max(0.0, (sp.y() - lay["y"]) / lay["h"]))
        if self.tool == "stamp":
            ann = Annotation(kind="stamp", page=p, x=nx, y=ny, pad=self.stamp_pad)
        else:
            ann = Annotation(kind="text", page=p, x=nx, y=ny, text="メモ")
        self._anns.append(ann)
        it = self._add_item(ann)
        self._emit_changed()
        if isinstance(it, TextNoteItem):
            it.setFocus()
            # テキストは1個置いたら配置モードを解除（誤爆防止）
            self.tool = None
            self.toolCleared.emit()

    def keyPressEvent(self, ev):
        if self.edit_mode and ev.key() == Qt.Key_Delete:
            removed = False
            for it in list(self._scene.selectedItems()):
                if hasattr(it, "ann"):
                    if it.ann in self._anns:
                        self._anns.remove(it.ann)
                    self._scene.removeItem(it)
                    if it in self._items:
                        self._items.remove(it)
                    removed = True
            if removed:
                self._emit_changed()
                return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            if ev.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            ev.accept()
            return
        super().wheelEvent(ev)  # 通常ホイールは縦スクロール

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if abs(self.viewport().width() - self._last_vw) > 2:
            self._resize_timer.start(120)
