"""メインウィンドウ。

画面の組み立てと、ユーザー操作を各担当（PadPlayer / ScriptView / RemoteServer）へ
振り分ける役に徹する。音の鳴らし方そのものは padplayer.py 側にある。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter,
    QStatusBar, QVBoxLayout, QWidget,
)

from appconfig import (APP_NAME, SOUND_EXTS, bundled_presets_dir,
                       load_app_config, save_app_config)
from dialogs import GridSizeDialog, PadEditDialog, RemoteDialog
from engine import AudioEngine
from models import Pad, Preset
from padplayer import PadPlayer, TriggerResult
from pdfview import ScriptView
from remote import RemoteServer
from theme import THEMES, app_qss, fader_qss, palette_of, set_theme, t
from widgets import LevelMeter, PadCell
from winui import set_caption_colors

log = logging.getLogger("tips_se")


class MainWindow(QMainWindow):
    def __init__(self, engine: AudioEngine):
        super().__init__()
        self.engine = engine
        self.player = PadPlayer(engine, self)
        self.player.loadingChanged.connect(self._on_pad_loading)
        self.player.progressChanged.connect(self._on_load_progress)
        self.player.loadFailed.connect(self._on_load_failed)

        self.preset = Preset()
        self.preset.ensure_pads()
        self.preset_path: str | None = None
        self.pad_buttons: list[PadCell] = []
        self._key_map: dict[str, Pad] = {}
        self._duplicate_keys: list[str] = []
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

    # ================= UI構築 =================
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
            f"エンジン: {self.engine.describe()} ｜ 未保存プリセット")

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

    def _add_check_action(self, menu, text: str, checked: bool, slot) -> QAction:
        act = QAction(text, self, checkable=True)
        act.setChecked(checked)
        act.toggled.connect(slot)
        menu.addAction(act)
        return act

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
        self.act_pdf = self._add_check_action(
            sm, "PDFビューを表示", self.ext_pdf, self._on_ext_pdf_toggled)
        self.act_pdf_edit = self._add_check_action(
            sm, "編集モードを使う", self.ext_pdf_edit, self._on_ext_pdf_edit_toggled)

        cm = mb.addMenu("コンフィグ")
        self.act_random = self._add_check_action(
            cm, "ランダム再生", self.ext_random, self._on_ext_random_toggled)
        self.act_continuous = self._add_check_action(
            cm, "連続再生", self.ext_continuous, self._on_ext_continuous_toggled)
        self.act_remote = self._add_check_action(
            cm, "スマホ操作", self.ext_remote, self._on_ext_remote_toggled)
        self.act_fade_sec = self._add_check_action(
            cm, "フェード秒数を表示", self.show_fade_sec, self._on_fade_sec_toggled)

        cm.addSeparator()
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

        # 出力スピーカー選択（開くたびに最新デバイスへ再構築＝接続直後も反映）
        self.output_menu = cm.addMenu("出力デバイス")
        self.output_menu.aboutToShow.connect(self._rebuild_output_menu)

    # ================= 配色 =================
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

    # ================= 拡張スイッチ =================
    def _apply_extension(self, attr: str, cfg_key: str, on: bool,
                         msg_on: str, msg_off: str) -> None:
        """拡張スイッチ共通の処理（属性更新・設定の永続化・ステータス表示）。"""
        setattr(self, attr, on)
        self.app_config[cfg_key] = on
        save_app_config(self.app_config)
        self.statusBar().showMessage(msg_on if on else msg_off)

    def _on_ext_random_toggled(self, on: bool):
        self._apply_extension(
            "ext_random", "random_playback", on,
            "ランダム再生拡張: 有効（パッド設定に「確率」が出ます）",
            "ランダム再生拡張: 無効")

    def _on_ext_continuous_toggled(self, on: bool):
        self._apply_extension(
            "ext_continuous", "continuous_playback", on,
            "連続再生拡張: 有効（パッド設定に「連続再生」が出ます）",
            "連続再生拡張: 無効")

    def _on_ext_pdf_edit_toggled(self, on: bool):
        if not on and self.edit_btn.isChecked():
            self.edit_btn.setChecked(False)   # 隠す前に本番モードへ戻す
        self._apply_extension(
            "ext_pdf_edit", "pdf_edit", on,
            "戯曲PDFの編集モード: 使う（ツールバーに「✎ 編集モード」が出ます）",
            "戯曲PDFの編集モード: 使わない")
        self._update_optional_ui()

    def _on_ext_remote_toggled(self, on: bool):
        if not on and self.remote_btn.isChecked():
            self.remote_btn.setChecked(False)  # 隠す前に待受を止める
        self._apply_extension(
            "ext_remote", "remote_control", on,
            "スマホ操作: 使う（ツールバーに「📱 スマホ操作」が出ます）",
            "スマホ操作: 使わない")
        self._update_optional_ui()

    def _on_fade_sec_toggled(self, on: bool):
        self._apply_extension(
            "show_fade_sec", "fade_seconds", on,
            "フェード表示: アイコン＋秒数", "フェード表示: アイコンのみ")
        for cell in self.pad_buttons:
            cell.set_show_fade_sec(on)

    def _update_optional_ui(self):
        """オプション扱いの機能ボタンをツールバーに出す/隠す。"""
        self.edit_btn.setVisible(self.ext_pdf_edit and self.ext_pdf)
        self.remote_btn.setVisible(self.ext_remote)

    def _on_ext_pdf_toggled(self, on: bool):
        if not on and self.edit_btn.isChecked():
            self.edit_btn.setChecked(False)
        self._apply_extension("ext_pdf", "pdf_view", on, "", "")
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

    # ================= 出力デバイス（スピーカー）=================
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
        self.statusBar().showMessage(
            f"出力デバイス: {name} ｜ {self.engine.describe()}")

    def _restore_output_device(self):
        """前回選んだ出力デバイスを名前で復元（見つからなければ既定のまま）。"""
        name = self.app_config.get("output_device")
        if not name:
            return
        try:
            for d in self.engine.list_output_devices():
                if d["name"] != name:
                    continue
                if d["index"] != self.engine.device_index:
                    self.engine.set_output_device(d["index"])
                self._update_device_label()
                self.statusBar().showMessage(f"出力デバイス: {name}")
                return
        except Exception as e:
            log.warning("出力デバイス復元失敗: %s", e)

    # ================= 編集モード / 戯曲PDF =================
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
        self.edit_tools.setVisible(self.edit_btn.isChecked())

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
        path, _ = QFileDialog.getOpenFileName(self, "戯曲PDFを開く", "", "PDF (*.pdf)")
        if not path:
            return
        if self.script.load_pdf(path):
            self.preset.pdf_path = path
            self._mark_dirty()
            self.statusBar().showMessage(f"戯曲PDF: {Path(path).name}")
        else:
            QMessageBox.critical(self, "エラー", f"PDFを開けません:\n{path}")

    # ================= スマホ・リモート操作 =================
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
            RemoteDialog(url, self).exec()
            self.statusBar().showMessage(f"スマホ操作 待受中: {url}")
        else:
            if self.remote:
                self.remote.stop()
            self.statusBar().showMessage("スマホ操作を停止しました")

    def set_pad_gain_remote(self, idx: int, v: float):
        """スマホからの音量変更を反映（パッドのフェーダー表示も同期）。"""
        p = self.preset.pads[idx]
        p.gain = max(0.0, min(1.5, v))
        self.player.set_gain(p, p.gain)
        self.pad_buttons[idx].sync_gain()
        self._mark_dirty()

    # ================= グリッド =================
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
            cell = PadCell(p, show_fade_sec=self.show_fade_sec)
            self._connect_cell(cell)
            self.grid.addWidget(cell, r, c)
            self.pad_buttons.append(cell)
        self.player.reload_all(self.preset.pads)
        self._rebuild_key_map()
        # 戯曲スタンプの番号範囲をパッド数に合わせる
        if hasattr(self, "script"):
            self.script.pad_count = self.preset.count
            self.stamp_num.setMaximum(self.preset.count)

    def _connect_cell(self, cell: PadCell):
        cell.toggleRequested.connect(lambda i: self.toggle_pad(self.preset.pads[i]))
        cell.triggerRequested.connect(lambda i: self.trigger_pad(self.preset.pads[i]))
        cell.stopRequested.connect(lambda i: self.stop_pad(self.preset.pads[i]))
        cell.editRequested.connect(lambda i: self.edit_pad(self.preset.pads[i]))
        cell.clearRequested.connect(lambda i: self.clear_pad(self.preset.pads[i]))
        cell.gainChanged.connect(self._on_pad_gain_changed)

    def _on_pad_gain_changed(self, index: int, gain: float):
        self.player.set_gain(self.preset.pads[index], gain)
        self._mark_dirty()

    def _rebuild_key_map(self):
        """キー→パッドの対応表を作り直す（同じキーは後ろのパッドが勝つ）。"""
        self._key_map = {}
        self._duplicate_keys = []
        for p in self.preset.pads:
            if not (p.key and p.assigned):
                continue
            if p.key in self._key_map:
                self._duplicate_keys.append(p.key)
            self._key_map[p.key] = p

    def _warn_duplicate_keys(self):
        """キーが重複していたらステータスバーで知らせる（本番での取り違え防止）。"""
        if not self._duplicate_keys:
            return
        keys = "・".join(sorted({k.upper() for k in self._duplicate_keys}))
        self.statusBar().showMessage(
            f"⚠ キー {keys} が複数のパッドに割り当てられています"
            "（同じキーでは後ろのパッドが鳴ります）")

    def _taken_keys(self, exclude: Pad | None = None) -> dict[str, int]:
        """{割当キー: パッドindex}。パッド設定の重複チェックに使う。"""
        return {p.key: p.index for p in self.preset.pads
                if p.key and p.assigned and p is not exclude}

    def change_grid(self):
        dlg = GridSizeDialog(self.preset.rows, self.preset.cols, self)
        if dlg.exec() == QDialog.Accepted:
            self.preset.rows, self.preset.cols = dlg.result_size()
            self._rebuild_grid()
            self._mark_dirty()

    # ================= 読み込み状態 =================
    def _on_pad_loading(self, index: int, loading: bool):
        if 0 <= index < len(self.pad_buttons):
            self.pad_buttons[index].set_loading(loading)

    def _on_load_progress(self, done: int, total: int):
        self.statusBar().showMessage(
            f"読み込み中… {done}/{total}" if total > 0 else "準備完了")

    def _on_load_failed(self, errors: list):
        QMessageBox.warning(
            self, "読み込み警告",
            "一部の音源を読み込めませんでした:\n\n" + "\n".join(errors))

    # ================= パッド操作 =================
    def trigger_pad(self, pad: Pad):
        if self.player.trigger(pad) is TriggerResult.LOADING:
            self.statusBar().showMessage("読み込み中… 完了までお待ちください")

    def toggle_pad(self, pad: Pad):
        """クリック時の挙動: 再生中なら停止、停止中なら再生（トグル）。

        連続再生が有効なパッドは停止せず、押すたびに重ねて発音する。
        """
        if not pad.assigned:
            return
        if pad.retrigger or not self.player.is_playing(pad):
            self.trigger_pad(pad)
        else:
            self.stop_pad(pad)

    def stop_pad(self, pad: Pad):
        self.player.stop(pad)

    def stop_all(self):
        self.player.stop_all(fade_out=self.preset.default_fade_out)

    def edit_pad(self, pad: Pad):
        dlg = PadEditDialog(pad, allow_random=self.ext_random,
                            allow_continuous=self.ext_continuous,
                            taken_keys=self._taken_keys(exclude=pad), parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        dlg.result_pad()
        missing = self.player.reload_pad(pad)
        if missing:
            QMessageBox.warning(self, "読み込み警告",
                                "ファイルが見つかりません:\n" + "\n".join(missing))
        self._rebuild_key_map()
        self._mark_dirty()

    def clear_pad(self, pad: Pad):
        self.player.unload(pad)
        idx = pad.index
        self.preset.pads[idx] = Pad(index=idx)
        cell = self.pad_buttons[idx]
        cell.pad = self.preset.pads[idx]
        cell.refresh()
        self._rebuild_key_map()
        self._mark_dirty()

    # ================= プリセット =================
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
        self.statusBar().showMessage(
            f"{min(len(files), self.preset.count)} 個を割り当てました")

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
        d = bundled_presets_dir()
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
        self._warn_duplicate_keys()

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

    # ================= 入力・状態反映 =================
    def _on_master_changed(self, v):
        self.engine.master_gain = v / 100.0
        self.master_lbl.setText(f"{v}%")

    def keyPressEvent(self, ev):
        if ev.isAutoRepeat():
            return
        if ev.key() in (Qt.Key_Space, Qt.Key_Escape):
            self.stop_all()
            return
        pad = self._key_map.get(ev.text().lower())
        if pad is not None:
            # 同じキーをもう一度押すと停止（トグル）
            self.toggle_pad(pad)
            return
        super().keyPressEvent(ev)

    def _refresh_playing_state(self):
        # 全ボイス状態を1回のロックでまとめて取得（パッドごとのロック競合を避ける）
        states = self.player.snapshot(self.preset.pads)
        for cell in self.pad_buttons:
            if not cell.pad.assigned or cell.is_loading:
                continue
            st = states.get(cell.pad.index)
            if st is None:
                cell.set_playing(False)
            else:
                cell.set_playing(True, st.progress, st.fade)
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
        for shutdown in (lambda: self.remote and self.remote.stop(),
                         self.player.shutdown, self.engine.close):
            try:
                shutdown()
            except Exception:
                log.exception("終了処理でエラー")
        super().closeEvent(ev)
