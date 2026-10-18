"""
main_window.py
TranscribAI のメインウィンドウ（標準ウィンドウ型・上下スプリッター2ペイン構成）。
- 録音開始/停止・一時停止/再開（他ウィンドウでのマウス・キーボード操作を一切妨げない）
- 音声入力ソース選択（マイク / PC内部音声 / 両方ミックス）
- 前提プロンプトのプリセット選択プルダウン＋直接編集＋保存・追加・削除（presets.json連携）
- リアルタイム文字起こし表示＆AI要約ボタン
"""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QComboBox, QCheckBox,
    QProgressBar, QSplitter, QFrame, QInputDialog, QMessageBox,
    QApplication, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QSettings, QSize
from PyQt6.QtGui import (
    QCloseEvent, QKeySequence, QShortcut, QWheelEvent, QKeyEvent, QTextCursor
)

import config
from preset_manager import PresetManager
from audio_recorder import AudioRecorderThread
from api_worker import TranscriptionWorker, SummaryWorker
from settings_dialog import ApiSettingsDialog


class MainWindow(QMainWindow):
    """
    TranscribAI 標準ウィンドウ。
    上段: 録音コントロール ＆ 前提プロンプト設定
    中段: リアルタイム文字起こしエリア
    下段: AI 要約ボタン ＆ AI 要約結果エリア
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎙️ TranscribAI - リアルタイム文字起こし＆AI要約")

        self._preset_mgr = PresetManager()
        self._recorder: AudioRecorderThread | None = None
        self._summary_worker: SummaryWorker | None = None

        # バックグラウンドで常時待機する文字起こしキューワーカー
        self._transcribe_worker = TranscriptionWorker(self)
        self._transcribe_worker.text_transcribed.connect(self._on_chunk_transcribed)
        self._transcribe_worker.transcribing_state.connect(self._on_transcribing_state_changed)
        self._transcribe_worker.error_occurred.connect(self._on_transcribe_error)
        self._transcribe_worker.start()

        # 録音経過時間タイマー
        self._elapsed_seconds = 0
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._on_rec_timer_tick)

        # フォントサイズ保持用 (単位: pt)
        self._current_font_size: int = 11

        self._init_ui()
        self._apply_style()
        self._load_settings_and_apply()

        # Ctrl + ホイールでフォントサイズ変更
        self._transcript_area.installEventFilter(self)
        self._summary_area.installEventFilter(self)
        self._prompt_edit.installEventFilter(self)

        # ショートカット登録
        sum_shortcut = QShortcut(QKeySequence("Alt+S"), self)
        sum_shortcut.activated.connect(self._on_summarize_clicked)

    # ------------------------------------------------------------------ #
    # 設定保存・復元 (settings.ini)
    # ------------------------------------------------------------------ #
    def _get_settings(self) -> QSettings:
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.ini")
        return QSettings(settings_path, QSettings.Format.IniFormat)

    def _save_settings(self):
        settings = self._get_settings()
        settings.setValue("window/size", self.size())
        settings.setValue("window/pos", self.pos())
        settings.setValue("window/always_on_top", self._pin_checkbox.isChecked())
        settings.setValue("window/font_size", self._current_font_size)
        settings.setValue("window/splitter", self._splitter.saveState())
        settings.setValue("audio/source_index", self._source_combo.currentIndex())
        settings.sync()

    def _load_settings_and_apply(self):
        settings = self._get_settings()
        saved_size = settings.value("window/size")
        saved_pos = settings.value("window/pos")
        always_on_top = settings.value("window/always_on_top", False, type=bool)
        font_size = settings.value("window/font_size", 11, type=int)
        splitter_state = settings.value("window/splitter")
        source_index = settings.value("audio/source_index", 0, type=int)

        self._current_font_size = max(8, min(28, font_size))
        if 0 <= source_index < self._source_combo.count():
            self._source_combo.setCurrentIndex(source_index)

        self._pin_checkbox.blockSignals(True)
        self._pin_checkbox.setChecked(always_on_top)
        self._pin_checkbox.blockSignals(False)
        self._apply_always_on_top(always_on_top, show_window=False)
        self._apply_style()

        if saved_size is not None and saved_pos is not None:
            self.resize(saved_size)
            self.move(saved_pos)
            self._ensure_visible()
        else:
            avail = QApplication.primaryScreen().availableGeometry()
            w = min(820, max(700, avail.width() - 80))
            h = min(780, max(680, avail.height() - 40))
            self.resize(w, h)
            self._center_on_primary_screen()

        if splitter_state is not None:
            self._splitter.restoreState(splitter_state)
        else:
            self._splitter.setSizes([340, 300])

    def _center_on_primary_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.left() + (screen.width() - self.width()) // 2
        y = screen.top() + (screen.height() - self.height()) // 2
        self.move(x, y)

    def _ensure_visible(self):
        center_point = self.geometry().center()
        for screen in QApplication.screens():
            if screen.geometry().contains(center_point):
                return
        self._center_on_primary_screen()

    # ------------------------------------------------------------------ #
    # UI 構築
    # ------------------------------------------------------------------ #
    def _init_ui(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 10, 14, 12)
        root_layout.setSpacing(8)

        # ══════════════════════════════════════════════════════════════
        # 1. 録音コントロールパネル
        # ══════════════════════════════════════════════════════════════
        rec_panel = QFrame()
        rec_panel.setObjectName("recPanel")
        rec_layout = QVBoxLayout(rec_panel)
        rec_layout.setContentsMargins(12, 8, 12, 8)
        rec_layout.setSpacing(6)

        # ── 上段：録音ボタン・一時停止ボタン・ステータス・音量メーター ──
        rec_top_row = QHBoxLayout()
        rec_top_row.setSpacing(10)

        self._record_btn = QPushButton("⏺ 録音開始")
        self._record_btn.setObjectName("recordBtn")
        self._record_btn.setProperty("recState", "idle")
        self._record_btn.setFixedHeight(34)
        self._record_btn.setMinimumWidth(130)
        self._record_btn.setToolTip("録音を開始 / 停止します。録音中も他ウィンドウのマウス・キーボード操作が可能です。")
        self._record_btn.clicked.connect(self._on_record_clicked)
        rec_top_row.addWidget(self._record_btn)

        self._pause_btn = QPushButton("⏸ 一時停止")
        self._pause_btn.setObjectName("pauseBtn")
        self._pause_btn.setProperty("pauseState", "normal")
        self._pause_btn.setFixedHeight(34)
        self._pause_btn.setMinimumWidth(115)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setToolTip("録音を一時停止 / 再開します。")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        rec_top_row.addWidget(self._pause_btn)

        self._status_label = QLabel("⚪ 待機中 (00:00)")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setMinimumWidth(140)
        rec_top_row.addWidget(self._status_label)

        level_title = QLabel("音量:")
        level_title.setObjectName("subLabel")
        rec_top_row.addWidget(level_title)

        self._level_bar = QProgressBar()
        self._level_bar.setObjectName("levelBar")
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(10)
        self._level_bar.setMinimumWidth(80)
        rec_top_row.addWidget(self._level_bar, stretch=1)

        rec_layout.addLayout(rec_top_row)

        # ── 下段：音声入力ソース選択・APIモード表示・最前面ピン留め ──
        rec_bottom_row = QHBoxLayout()
        rec_bottom_row.setSpacing(8)

        src_label = QLabel("入力ソース:")
        src_label.setObjectName("subLabel")
        rec_bottom_row.addWidget(src_label)

        self._source_combo = QComboBox()
        self._source_combo.setObjectName("comboBox")
        self._source_combo.addItem("🎤+🔊 マイク＋PC内部音声（会議・汎用）", AudioRecorderThread.SOURCE_BOTH)
        self._source_combo.addItem("🎤 マイクのみ（自分の声）", AudioRecorderThread.SOURCE_MIC)
        self._source_combo.addItem("🔊 PC内部音声のみ（Web会議・動画音声）", AudioRecorderThread.SOURCE_LOOPBACK)
        self._source_combo.setMinimumWidth(190)
        self._source_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        rec_bottom_row.addWidget(self._source_combo, stretch=1)

        mode_text = "🧪 ダミーモード" if config.USE_DUMMY_API else f"☁️ Azure OpenAI ({config.TRANSCRIPTION_MODE})"
        self._mode_badge = QLabel(mode_text)
        self._mode_badge.setObjectName("modeBadgeDummy" if config.USE_DUMMY_API else "modeBadgeLive")
        rec_bottom_row.addWidget(self._mode_badge)

        self._api_settings_btn = QPushButton("⚙️ API設定")
        self._api_settings_btn.setObjectName("smallBtn")
        self._api_settings_btn.setToolTip("Azure OpenAI API キーやモデルの設定・接続テストを行います")
        self._api_settings_btn.clicked.connect(lambda: self.open_api_settings_dialog(is_first_launch=False))
        rec_bottom_row.addWidget(self._api_settings_btn)

        self._pin_checkbox = QCheckBox("📌 常に最前面")
        self._pin_checkbox.setObjectName("pinCheck")
        self._pin_checkbox.setToolTip("チェックを入れると、他のウィンドウで作業中もこのウィンドウを最前面に表示し続けます。")
        self._pin_checkbox.toggled.connect(lambda checked: self._apply_always_on_top(checked, show_window=True))
        rec_bottom_row.addWidget(self._pin_checkbox)

        rec_layout.addLayout(rec_bottom_row)
        root_layout.addWidget(rec_panel)

        # ══════════════════════════════════════════════════════════════
        # 2. 前提プロンプト（プリセット選択 ＆ 直接編集・保存）パネル
        # ══════════════════════════════════════════════════════════════
        prompt_panel = QFrame()
        prompt_panel.setObjectName("promptPanel")
        prompt_layout = QVBoxLayout(prompt_panel)
        prompt_layout.setContentsMargins(12, 8, 12, 10)
        prompt_layout.setSpacing(6)

        prompt_header = QHBoxLayout()
        prompt_header.setSpacing(8)

        self._prompt_toggle_btn = QPushButton("▼ 💡 前提プロンプト")
        self._prompt_toggle_btn.setObjectName("toggleHeaderBtn")
        self._prompt_toggle_btn.setCheckable(True)
        self._prompt_toggle_btn.setChecked(True)
        self._prompt_toggle_btn.clicked.connect(self._on_toggle_prompt_area)
        prompt_header.addWidget(self._prompt_toggle_btn)

        preset_lbl = QLabel("プリセット:")
        preset_lbl.setObjectName("subLabel")
        prompt_header.addWidget(preset_lbl)

        self._preset_combo = QComboBox()
        self._preset_combo.setObjectName("comboBox")
        self._preset_combo.setMinimumWidth(160)
        self._preset_combo.currentTextChanged.connect(self._on_preset_selected)
        prompt_header.addWidget(self._preset_combo, stretch=1)

        self._save_preset_btn = QPushButton("💾 保存")
        self._save_preset_btn.setObjectName("smallBtn")
        self._save_preset_btn.setToolTip("現在選択中のプリセットに下のテキストを上書き保存します (presets.json)")
        self._save_preset_btn.clicked.connect(self._on_save_preset_clicked)
        prompt_header.addWidget(self._save_preset_btn)

        self._add_preset_btn = QPushButton("＋ 新規")
        self._add_preset_btn.setObjectName("smallBtn")
        self._add_preset_btn.setToolTip("下のテキストを新しいプリセット名で presets.json に追加します")
        self._add_preset_btn.clicked.connect(self._on_add_preset_clicked)
        prompt_header.addWidget(self._add_preset_btn)

        self._del_preset_btn = QPushButton("🗑️")
        self._del_preset_btn.setObjectName("smallDangerBtn")
        self._del_preset_btn.setFixedWidth(34)
        self._del_preset_btn.setToolTip("選択中のプリセットを削除します")
        self._del_preset_btn.clicked.connect(self._on_delete_preset_clicked)
        prompt_header.addWidget(self._del_preset_btn)

        prompt_layout.addLayout(prompt_header)

        self._prompt_edit = QTextEdit()
        self._prompt_edit.setObjectName("promptEdit")
        self._prompt_edit.setPlaceholderText(
            "認識させたい専門用語、固有名詞、人名、製品名や会議の文脈を入力してください（選択後にここで直接追記・編集も可能）..."
        )
        self._prompt_edit.setMaximumHeight(68)
        prompt_layout.addWidget(self._prompt_edit)

        self._populate_presets()
        root_layout.addWidget(prompt_panel)

        # ══════════════════════════════════════════════════════════════
        # 3. 上下スプリッター（リアルタイム文字起こし ／ AI要約）
        # ══════════════════════════════════════════════════════════════
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setObjectName("mainSplitter")
        self._splitter.setChildrenCollapsible(False)

        # ── 上ペイン：リアルタイム文字起こしエリア ──
        trans_widget = QWidget()
        trans_layout = QVBoxLayout(trans_widget)
        trans_layout.setContentsMargins(0, 2, 0, 4)
        trans_layout.setSpacing(6)

        trans_header = QHBoxLayout()
        trans_title = QLabel("📄 リアルタイム文字起こし")
        trans_title.setObjectName("sectionTitle")
        trans_header.addWidget(trans_title)

        self._api_busy_label = QLabel("")
        self._api_busy_label.setObjectName("busyLabel")
        trans_header.addWidget(self._api_busy_label)

        trans_header.addStretch()

        clear_trans_btn = QPushButton("🧹 クリア")
        clear_trans_btn.setObjectName("smallBtn")
        clear_trans_btn.setToolTip("文字起こしテキストをクリアします")
        clear_trans_btn.clicked.connect(self._on_clear_transcript)
        trans_header.addWidget(clear_trans_btn)

        copy_trans_btn = QPushButton("📋 文字起こしをコピー")
        copy_trans_btn.setObjectName("smallBtn")
        copy_trans_btn.setToolTip("文字起こしテキスト全文をクリップボードにコピーします")
        copy_trans_btn.clicked.connect(self._on_copy_transcript)
        trans_header.addWidget(copy_trans_btn)

        trans_layout.addLayout(trans_header)

        self._transcript_area = QTextEdit()
        self._transcript_area.setObjectName("transcriptArea")
        self._transcript_area.setMinimumHeight(140)
        self._transcript_area.setPlaceholderText(
            "「⏺ 録音開始」を押すと、ここにリアルタイムで文字起こし結果が順次表示されます。\n"
            "録音中に他のウィンドウでマウス操作やキーボード入力を行っても録音は止まりません。\n"
            "また、この欄のテキストは自由に手動編集・追記することもできます。"
        )
        trans_layout.addWidget(self._transcript_area)
        self._splitter.addWidget(trans_widget)

        # ── 下ペイン：AI要約ボタン ＆ AI要約結果エリア ──
        sum_widget = QWidget()
        sum_layout = QVBoxLayout(sum_widget)
        sum_layout.setContentsMargins(0, 6, 0, 0)
        sum_layout.setSpacing(8)

        # 要約アクションバー
        sum_bar = QHBoxLayout()
        sum_bar.setSpacing(10)

        self._summarize_btn = QPushButton("📝 AIで要約する (Alt+S)")
        self._summarize_btn.setObjectName("summarizeBtn")
        self._summarize_btn.setFixedHeight(38)
        self._summarize_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._summarize_btn.setToolTip("上の文字起こしテキストを Azure OpenAI で要約し、下に結果を表示します (Alt+S)")
        self._summarize_btn.clicked.connect(self._on_summarize_clicked)
        sum_bar.addWidget(self._summarize_btn)

        sum_layout.addLayout(sum_bar)

        # 要約ヘッダー
        sum_header = QHBoxLayout()
        sum_title = QLabel("🤖 AI 要約結果")
        sum_title.setObjectName("sectionTitle")
        sum_header.addWidget(sum_title)
        sum_header.addStretch()

        clear_sum_btn = QPushButton("🧹 クリア")
        clear_sum_btn.setObjectName("smallBtn")
        clear_sum_btn.clicked.connect(lambda: self._summary_area.clear())
        sum_header.addWidget(clear_sum_btn)

        copy_sum_btn = QPushButton("📋 要約をコピー")
        copy_sum_btn.setObjectName("smallBtn")
        copy_sum_btn.setToolTip("AI要約テキストをクリップボードにコピーします")
        copy_sum_btn.clicked.connect(self._on_copy_summary)
        sum_header.addWidget(copy_sum_btn)

        sum_layout.addLayout(sum_header)

        self._summary_area = QTextEdit()
        self._summary_area.setObjectName("summaryArea")
        self._summary_area.setReadOnly(True)
        self._summary_area.setMinimumHeight(140)
        self._summary_area.setPlaceholderText("「📝 AIで要約する」ボタンを押すと、文字起こし内容のAI要約がここに表示されます...")
        sum_layout.addWidget(self._summary_area)

        self._splitter.addWidget(sum_widget)
        root_layout.addWidget(self._splitter, stretch=1)

        self.setMinimumSize(QSize(680, 620))

    # ------------------------------------------------------------------ #
    # プリセット管理操作
    # ------------------------------------------------------------------ #
    def _populate_presets(self):
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        names = self._preset_mgr.get_names()
        for name in names:
            self._preset_combo.addItem(name)

        selected = self._preset_mgr.get_selected_name()
        idx = self._preset_combo.findText(selected)
        if idx >= 0:
            self._preset_combo.setCurrentIndex(idx)
        self._preset_combo.blockSignals(False)

        current_name = self._preset_combo.currentText()
        self._prompt_edit.setPlainText(self._preset_mgr.get_prompt(current_name))

    def _on_preset_selected(self, name: str):
        if not name:
            return
        self._preset_mgr.set_selected_name(name)
        self._prompt_edit.setPlainText(self._preset_mgr.get_prompt(name))

    def _on_save_preset_clicked(self):
        name = self._preset_combo.currentText().strip()
        if not name:
            return
        prompt_text = self._prompt_edit.toPlainText().strip()
        self._preset_mgr.upsert_preset(name, prompt_text)
        self._status_label.setText(f"💾 「{name}」を保存しました")

    def _on_add_preset_clicked(self):
        name, ok = QInputDialog.getText(
            self,
            "新規プリセットの追加",
            "新しいプリセット名を入力してください:",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        prompt_text = self._prompt_edit.toPlainText().strip()
        self._preset_mgr.upsert_preset(name, prompt_text)
        self._populate_presets()
        self._status_label.setText(f"✨ プリセット「{name}」を追加しました")

    def _on_delete_preset_clicked(self):
        name = self._preset_combo.currentText().strip()
        if not name:
            return
        if len(self._preset_mgr.get_names()) <= 1:
            QMessageBox.information(self, "削除不可", "最後の1件のプリセットは削除できません。")
            return

        reply = QMessageBox.question(
            self,
            "プリセットの削除",
            f"プリセット「{name}」を削除してもよろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self._preset_mgr.delete_preset(name):
                self._populate_presets()
                self._status_label.setText(f"🗑️ 「{name}」を削除しました")

    def _on_toggle_prompt_area(self, checked: bool):
        if checked:
            self._prompt_toggle_btn.setText("▼ 💡 前提プロンプト")
            self._prompt_edit.show()
        else:
            self._prompt_toggle_btn.setText("▶ 💡 前提プロンプト（折りたたみ）")
            self._prompt_edit.hide()

    # ------------------------------------------------------------------ #
    # 録音・一時停止アクション
    # ------------------------------------------------------------------ #
    def _on_record_clicked(self):
        if self._recorder and self._recorder.isRunning():
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        source_mode = self._source_combo.currentData() or AudioRecorderThread.SOURCE_BOTH

        self._recorder = AudioRecorderThread(source_mode=source_mode, parent=self)
        self._recorder.chunk_ready.connect(self._on_audio_chunk_ready)
        self._recorder.level_changed.connect(self._level_bar.setValue)
        self._recorder.error_occurred.connect(self._on_recorder_error)
        self._recorder.finished.connect(self._on_recorder_finished)

        self._elapsed_seconds = 0
        self._rec_timer.start()
        self._recorder.start()

        # UI状態の更新
        self._record_btn.setText("⏹ 録音停止")
        self._record_btn.setProperty("recState", "recording")
        self._pause_btn.setText("⏸ 一時停止")
        self._pause_btn.setProperty("pauseState", "normal")
        self._pause_btn.setEnabled(True)
        self._source_combo.setEnabled(False)
        self._update_rec_status_label()
        self._refresh_button_styles()

    def _stop_recording(self):
        self._rec_timer.stop()
        if self._recorder and self._recorder.isRunning():
            self._record_btn.setEnabled(False)
            self._pause_btn.setEnabled(False)
            self._status_label.setText("⏳ 録音停止処理中...")
            self._recorder.stop_recording()

    def _on_recorder_finished(self):
        self._rec_timer.stop()
        self._level_bar.setValue(0)
        self._record_btn.setEnabled(True)
        self._record_btn.setText("⏺ 録音開始")
        self._record_btn.setProperty("recState", "idle")
        self._pause_btn.setText("⏸ 一時停止")
        self._pause_btn.setProperty("pauseState", "normal")
        self._pause_btn.setEnabled(False)
        self._source_combo.setEnabled(True)
        mins, secs = divmod(self._elapsed_seconds, 60)
        self._status_label.setText(f"⚪ 停止 ({mins:02d}:{secs:02d})")
        self._refresh_button_styles()

    def _on_pause_clicked(self):
        if not self._recorder or not self._recorder.isRunning():
            return

        new_paused = not self._recorder.is_paused
        self._recorder.set_paused(new_paused)
        if new_paused:
            self._pause_btn.setText("▶ 再開")
            self._pause_btn.setProperty("pauseState", "paused")
        else:
            self._pause_btn.setText("⏸ 一時停止")
            self._pause_btn.setProperty("pauseState", "normal")

        self._update_rec_status_label()
        self._refresh_button_styles()

    def _on_rec_timer_tick(self):
        if self._recorder and self._recorder.isRunning() and not self._recorder.is_paused:
            self._elapsed_seconds += 1
        self._update_rec_status_label()

    def _update_rec_status_label(self):
        mins, secs = divmod(self._elapsed_seconds, 60)
        if self._recorder and self._recorder.isRunning():
            if self._recorder.is_paused:
                self._status_label.setText(f"🟡 一時停止中 ({mins:02d}:{secs:02d})")
            else:
                self._status_label.setText(f"🔴 録音中 ({mins:02d}:{secs:02d})")
        else:
            self._status_label.setText(f"⚪ 待機中 ({mins:02d}:{secs:02d})")

    def _on_audio_chunk_ready(self, wav_bytes: bytes, duration_sec: float):
        prompt_text = self._prompt_edit.toPlainText().strip()
        self._transcribe_worker.enqueue_chunk(wav_bytes, duration_sec, prompt_text)

    def _append_to_transcript_safely(self, text_to_append: str):
        """
        ユーザーが文字起こし欄の途中を選択・編集していてもカーソル位置や選択状態を壊さずに
        ドキュメント末尾へテキストを追記する。
        """
        scrollbar = self._transcript_area.verticalScrollBar()
        was_near_bottom = (scrollbar.maximum() - scrollbar.value()) <= 24

        doc = self._transcript_area.document()
        doc_cursor = QTextCursor(doc)
        doc_cursor.movePosition(QTextCursor.MoveOperation.End)

        current = doc.toPlainText()
        sep = "\n" if current and not current.endswith("\n") else ""
        doc_cursor.insertText(f"{sep}{text_to_append}\n")

        if was_near_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _on_chunk_transcribed(self, text: str):
        if not text:
            return
        self._append_to_transcript_safely(text)

    def _on_transcribing_state_changed(self, busy: bool):
        self._api_busy_label.setText("⏳ 音声認識中..." if busy else "")

    def _on_transcribe_error(self, err_msg: str):
        self._api_busy_label.setText("⚠️ 認識エラー")
        self._append_to_transcript_safely(f"[⚠️ {err_msg}]")

    def _on_recorder_error(self, err_msg: str):
        self._rec_timer.stop()
        QMessageBox.warning(self, "録音デバイスエラー", f"音声の録音中にエラーが発生しました:\n{err_msg}")
        self._on_recorder_finished()

    # ------------------------------------------------------------------ #
    # AI 要約アクション
    # ------------------------------------------------------------------ #
    def _on_summarize_clicked(self):
        transcript = self._transcript_area.toPlainText().strip()
        if not transcript:
            self._summary_area.setPlainText("⚠️ 文字起こしテキストが空です。録音を行うかテキストを入力してから要約ボタンを押してください。")
            return

        if self._summary_worker and self._summary_worker.isRunning():
            self._summary_worker.quit()

        preset_prompt = self._prompt_edit.toPlainText().strip()
        self._summarize_btn.setEnabled(False)
        self._summarize_btn.setText("⏳ AIで要約を生成中...")
        self._summary_area.setPlainText("⏳ Azure OpenAI で要約を生成しています...\n")

        self._summary_worker = SummaryWorker(
            transcript_text=transcript,
            preset_prompt=preset_prompt,
            parent=self,
        )
        self._summary_worker.result_ready.connect(self._on_summary_ready)
        self._summary_worker.error_occurred.connect(self._on_summary_error)
        self._summary_worker.finished.connect(self._on_summary_finished)
        self._summary_worker.start()

    def _on_summary_ready(self, summary_text: str):
        self._summary_area.setPlainText(summary_text)
        scrollbar = self._summary_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.minimum())

    def _on_summary_error(self, err_msg: str):
        self._summary_area.setPlainText(err_msg)

    def _on_summary_finished(self):
        self._summarize_btn.setEnabled(True)
        self._summarize_btn.setText("📝 AIで要約する (Alt+S)")

    # ------------------------------------------------------------------ #
    # コピー・クリア・最前面固定
    # ------------------------------------------------------------------ #
    def _on_clear_transcript(self):
        self._transcript_area.clear()

    def _on_copy_transcript(self):
        text = self._transcript_area.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._api_busy_label.setText("✅ コピーしました")
            QTimer.singleShot(2000, lambda: self._api_busy_label.setText(""))

    def _on_copy_summary(self):
        text = self._summary_area.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._status_label.setText("✅ 要約をコピーしました")
            QTimer.singleShot(2000, self._update_rec_status_label)

    def _apply_always_on_top(self, enabled: bool, show_window: bool = True):
        win_handle = self.windowHandle()
        if win_handle is not None:
            # 既存HWNDのフラグを直接切り替えることでウィンドウの点滅・再生成を防ぐ
            win_handle.setFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
            if show_window:
                self.show()

    def _refresh_button_styles(self):
        self._record_btn.style().unpolish(self._record_btn)
        self._record_btn.style().polish(self._record_btn)
        self._pause_btn.style().unpolish(self._pause_btn)
        self._pause_btn.style().polish(self._pause_btn)

    def _update_mode_badge(self):
        mode_text = "🧪 ダミーモード (課金なし)" if config.USE_DUMMY_API else f"☁️ Azure OpenAI ({config.TRANSCRIPTION_MODE})"
        self._mode_badge.setText(mode_text)
        self._mode_badge.setObjectName("modeBadgeDummy" if config.USE_DUMMY_API else "modeBadgeLive")
        self._mode_badge.style().unpolish(self._mode_badge)
        self._mode_badge.style().polish(self._mode_badge)

    def open_api_settings_dialog(self, is_first_launch: bool = False):
        """API設定・接続テストダイアログを表示し、保存されたらバッジ表示を更新する。"""
        dlg = ApiSettingsDialog(is_first_launch=is_first_launch, parent=self)
        if dlg.exec():
            self._update_mode_badge()
            self._status_label.setText("✅ API設定を反映しました")
            QTimer.singleShot(2500, self._update_rec_status_label)

    def check_first_launch_api_setup(self):
        """
        初回起動時（または .env の APIキーが未設定の状態）に自動でAPI設定ダイアログを表示する。
        """
        if not config.is_api_configured():
            self.open_api_settings_dialog(is_first_launch=True)

    # ------------------------------------------------------------------ #
    # スタイル定義
    # ------------------------------------------------------------------ #
    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget#centralWidget {{
                background-color: #1E1E1E;
                color: #E0E0E0;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
            }}

            QFrame#recPanel, QFrame#promptPanel {{
                background-color: #252526;
                border: 1px solid #333337;
                border-radius: 10px;
            }}

            QLabel#sectionTitle {{
                color: #E8E8E8;
                font-size: 13px;
                font-weight: bold;
            }}

            QLabel#statusLabel {{
                color: #E0E0E0;
                font-size: 13px;
                font-weight: bold;
            }}

            QLabel#subLabel {{
                color: #AAAAAA;
                font-size: 12px;
            }}

            QLabel#busyLabel {{
                color: #4FC1FF;
                font-size: 12px;
                font-weight: bold;
            }}

            QLabel#modeBadgeDummy {{
                background-color: rgba(255, 170, 0, 0.16);
                color: #FFCC4D;
                border: 1px solid rgba(255, 170, 0, 0.4);
                border-radius: 5px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }}

            QLabel#modeBadgeLive {{
                background-color: rgba(0, 122, 204, 0.2);
                color: #66BFFF;
                border: 1px solid rgba(0, 122, 204, 0.5);
                border-radius: 5px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }}

            QCheckBox#pinCheck {{
                color: #D4D4D4;
                font-size: 12px;
                spacing: 6px;
            }}
            QCheckBox#pinCheck::indicator {{
                width: 16px;
                height: 16px;
            }}

            QProgressBar#levelBar {{
                background-color: #1A1A1A;
                border: 1px solid #3A3A3A;
                border-radius: 5px;
            }}
            QProgressBar#levelBar::chunk {{
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2EA043, stop:0.7 #D29922, stop:1 #F85149
                );
                border-radius: 4px;
            }}

            QComboBox#comboBox {{
                background-color: #2D2D30;
                color: #E0E0E0;
                border: 1px solid #3E3E42;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QComboBox#comboBox:disabled {{
                color: #666666;
                background-color: #222225;
            }}
            QComboBox QAbstractItemView {{
                background-color: #2D2D30;
                color: #E0E0E0;
                selection-background-color: #094771;
            }}

            QPushButton#recordBtn[recState="idle"] {{
                background-color: #2EA043;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 14px;
            }}
            QPushButton#recordBtn[recState="idle"]:hover {{
                background-color: #3FB950;
            }}
            QPushButton#recordBtn[recState="recording"] {{
                background-color: #DA3633;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 14px;
            }}
            QPushButton#recordBtn[recState="recording"]:hover {{
                background-color: #F85149;
            }}

            QPushButton#pauseBtn[pauseState="normal"] {{
                background-color: #38383D;
                color: #E8E8E8;
                border: 1px solid #55555C;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 12px;
            }}
            QPushButton#pauseBtn[pauseState="normal"]:hover {{
                background-color: #46464D;
            }}
            QPushButton#pauseBtn[pauseState="normal"]:disabled {{
                background-color: #252528;
                color: #555558;
                border-color: #333337;
            }}
            QPushButton#pauseBtn[pauseState="paused"] {{
                background-color: #D29922;
                color: #1A1A1A;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 12px;
            }}
            QPushButton#pauseBtn[pauseState="paused"]:hover {{
                background-color: #E3B341;
            }}

            QPushButton#summarizeBtn {{
                background-color: #007ACC;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#summarizeBtn:hover {{
                background-color: #1F8AD2;
            }}
            QPushButton#summarizeBtn:pressed {{
                background-color: #005A9E;
            }}
            QPushButton#summarizeBtn:disabled {{
                background-color: #1A3A5A;
                color: #888888;
            }}

            QPushButton#smallBtn {{
                background-color: #2D2D30;
                color: #D4D4D4;
                border: 1px solid #44444A;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton#smallBtn:hover {{
                background-color: #3E3E42;
                color: #FFFFFF;
            }}

            QPushButton#smallDangerBtn {{
                background-color: #2D2D30;
                color: #E06C75;
                border: 1px solid #6B3035;
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 12px;
            }}
            QPushButton#smallDangerBtn:hover {{
                background-color: rgba(224, 108, 117, 0.2);
                color: #FFFFFF;
            }}

            QPushButton#toggleHeaderBtn {{
                background: transparent;
                color: #D4D4D4;
                border: none;
                font-size: 12px;
                font-weight: bold;
                text-align: left;
                padding: 2px 4px;
            }}
            QPushButton#toggleHeaderBtn:hover {{
                color: #FFFFFF;
            }}

            QTextEdit#promptEdit {{
                background-color: #1B1B1C;
                color: #D4D4D4;
                border: 1px solid #3A3A3D;
                border-radius: 6px;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
                font-size: {max(9, self._current_font_size - 1)}pt;
                padding: 6px;
            }}

            QTextEdit#transcriptArea {{
                background-color: #161617;
                color: #E6E6E6;
                border: 1px solid #333337;
                border-radius: 8px;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
                font-size: {self._current_font_size}pt;
                padding: 10px;
                selection-background-color: #264F78;
            }}

            QTextEdit#summaryArea {{
                background-color: #161B22;
                color: #E6E6E6;
                border: 1px solid rgba(0, 122, 204, 0.45);
                border-radius: 8px;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
                font-size: {self._current_font_size}pt;
                padding: 10px;
                selection-background-color: #264F78;
            }}

            QSplitter::handle {{
                background-color: #333337;
                height: 4px;
                border-radius: 2px;
            }}
            QSplitter::handle:hover {{
                background-color: #007ACC;
            }}
        """)

    # ------------------------------------------------------------------ #
    # フォントサイズ変更 (Ctrl + ホイール / Ctrl + Plus / Minus)
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.wheelEvent(event)
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._current_font_size = min(28, self._current_font_size + 1)
            elif delta < 0:
                self._current_font_size = max(8, self._current_font_size - 1)
            self._apply_style()
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._current_font_size = min(28, self._current_font_size + 1)
                self._apply_style()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Minus:
                self._current_font_size = max(8, self._current_font_size - 1)
                self._apply_style()
                event.accept()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    # 終了処理
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent):
        self._save_settings()
        if self._recorder and self._recorder.isRunning():
            self._recorder.stop_recording()
            self._recorder.wait(1500)
        if self._transcribe_worker and self._transcribe_worker.isRunning():
            self._transcribe_worker.stop_worker()
            self._transcribe_worker.wait(1500)
        super().closeEvent(event)
