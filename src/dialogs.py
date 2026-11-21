"""設定・入力用のダイアログ群。

いずれも MainWindow の状態を覗かず、必要な値は引数で受け取り結果を返すだけにする。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from appconfig import SOUND_FILE_FILTER
from models import Pad
from theme import t
from widgets import SourcesEditor
from winui import make_qr_pixmap

log = logging.getLogger("tips_se")


class PadEditDialog(QDialog):
    """1パッドの設定編集ダイアログ。

    allow_random / allow_continuous はコンフィグの拡張機能スイッチに対応する。
    taken_keys は {キー: パッドindex} で、他パッドとキーが衝突したら確認を出す。
    """

    def __init__(self, pad: Pad, *, allow_random: bool = False,
                 allow_continuous: bool = False,
                 taken_keys: dict[str, int] | None = None, parent=None):
        super().__init__(parent)
        self.pad = pad
        self._taken_keys = taken_keys or {}
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
        if allow_random:
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

        # 再生範囲（0のままなら音源全体）。ONJO の「再生範囲」に相当。
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 99999.0)
        self.start_spin.setDecimals(2)
        self.start_spin.setSingleStep(0.1)
        self.start_spin.setSuffix(" 秒")
        self.start_spin.setSpecialValueText("先頭から")
        self.start_spin.setValue(pad.start)
        form.addRow("再生開始", self.start_spin)

        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(0.0, 99999.0)
        self.end_spin.setDecimals(2)
        self.end_spin.setSingleStep(0.1)
        self.end_spin.setSuffix(" 秒")
        self.end_spin.setSpecialValueText("末尾まで")
        self.end_spin.setValue(pad.end)
        form.addRow("再生終了", self.end_spin)

        self.loop_chk = QCheckBox("ループ再生")
        self.loop_chk.setChecked(pad.loop)
        form.addRow("", self.loop_chk)

        self.excl_chk = QCheckBox("排他再生（他の音を止めて鳴らす）")
        self.excl_chk.setChecked(pad.exclusive)
        form.addRow("", self.excl_chk)

        self.retrigger_chk = None
        if allow_continuous:
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

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "音源を選択", "", SOUND_FILE_FILTER)
        if path:
            self.path_edit.setText(path)

    def accept(self):
        end = self.end_spin.value()
        if end > 0 and end <= self.start_spin.value():
            QMessageBox.warning(
                self, "再生範囲",
                "再生終了は再生開始より後にしてください。\n"
                "（音源全体を鳴らすなら両方を0にします）")
            self.end_spin.setFocus()
            self.end_spin.selectAll()
            return
        # 同じキーが2つのパッドに載ると本番でどちらが鳴るか分からなくなるため、
        # 黙って上書きせず必ず確認を挟む。
        key = self.key_edit.text().strip().lower()
        other = self._taken_keys.get(key)
        if key and other is not None and other != self.pad.index:
            ret = QMessageBox.warning(
                self, "キーの重複",
                f"キー「{key.upper()}」はパッド{other + 1}にも割り当てられています。\n"
                "このままだと本番でどちらが鳴るか分かりません。\n\nこの設定で保存しますか？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                self.key_edit.setFocus()
                self.key_edit.selectAll()
                return
        super().accept()

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
        self.pad.start = self.start_spin.value()
        self.pad.end = self.end_spin.value()
        self.pad.loop = self.loop_chk.isChecked()
        self.pad.exclusive = self.excl_chk.isChecked()
        if self.retrigger_chk is not None:
            self.pad.retrigger = self.retrigger_chk.isChecked()
        return self.pad


class GridSizeDialog(QDialog):
    """パッドグリッドの行数・列数を決めるダイアログ。"""

    def __init__(self, rows: int, cols: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("グリッドサイズ")
        form = QFormLayout(self)
        self.rows_spin = QSpinBox(); self.rows_spin.setRange(1, 8)
        self.rows_spin.setValue(rows)
        self.cols_spin = QSpinBox(); self.cols_spin.setRange(1, 8)
        self.cols_spin.setValue(cols)
        form.addRow("行数", self.rows_spin)
        form.addRow("列数", self.cols_spin)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result_size(self) -> tuple[int, int]:
        return self.rows_spin.value(), self.cols_spin.value()


class RemoteDialog(QDialog):
    """スマホ操作の接続先をQRコードで案内するダイアログ。"""

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("スマホ操作")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("同じWi-Fiにつないだスマホで、このQRコードを読み取ってください"))
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
        btn.clicked.connect(self.accept)
        v.addWidget(btn)
