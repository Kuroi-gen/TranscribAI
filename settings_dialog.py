"""
settings_dialog.py
初回起動時（APIキー未設定時）およびメイン画面の「⚙️ API設定」ボタンから開く
Azure OpenAI API 設定・接続テスト・モデル一覧管理ダイアログ。

機能:
- 使えるモデル一覧の自動スキャン・更新（「🔄 使えるモデル一覧を更新」ボタン）
- 使えなくなった（未対応・廃止された）モデルの自動非表示フィルタリング
- 編集可能なプルダウン（QComboBox）によるモデル選択＋カスタムデプロイ名の直接追加入力
- その場での接続テストと外部 .env ファイルへの保存
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QCheckBox, QPushButton,
    QLabel, QTextEdit, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt

import config
from model_registry import ModelRegistry
from api_worker import ConnectionTestWorker, ModelDiscoveryWorker, reset_azure_client


class ApiSettingsDialog(QDialog):
    """
    Azure OpenAI API キー・エンドポイント・デプロイメント名を設定し、
    使えるモデル一覧の更新（使えないモデルの非表示化）・接続テスト・.env 保存を行うダイアログ。
    """

    def __init__(
        self,
        is_first_launch: bool = False,
        registry: ModelRegistry | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._is_first_launch = is_first_launch
        self._registry = registry or ModelRegistry()
        self._test_worker: ConnectionTestWorker | None = None
        self._discover_worker: ModelDiscoveryWorker | None = None

        self.setWindowTitle("🔑 Azure OpenAI API 設定・モデル管理 - TranscribAI")
        self.setModal(True)
        self.setMinimumWidth(680)
        self.setMinimumHeight(600)

        self._init_ui()
        self._apply_style()
        self._load_current_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # ── 案内バナー ──
        banner = QFrame()
        banner.setObjectName("bannerFrame")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(4)

        if self._is_first_launch:
            title_text = "👋 TranscribAI へようこそ！（初回API設定）"
            desc_text = (
                "Azure OpenAI の API キーが未設定です。エンドポイントとAPIキーを入力して「🔄 使えるモデル一覧を更新」を押すと、\n"
                "現在使えるモデルだけを自動判別してプルダウンに登録し、使えないモデルは非表示にします。\n"
                "※ 今すぐAPIキーを設定せずに試す場合は、左下の「🧪 ダミーモードで開始」を押してください。"
            )
        else:
            title_text = "⚙️ Azure OpenAI API 設定・使えるモデル一覧管理"
            desc_text = (
                "モデルが更新された際は「🔄 使えるモデル一覧を更新」を押すと、最新の使えるモデル一覧に更新され、\n"
                "使えなくなったモデルは自動的にプルダウンから非表示になります（新しいモデル名の手入力も可能です）。"
            )

        b_title = QLabel(title_text)
        b_title.setObjectName("bannerTitle")
        banner_layout.addWidget(b_title)

        b_desc = QLabel(desc_text)
        b_desc.setObjectName("bannerDesc")
        b_desc.setWordWrap(True)
        banner_layout.addWidget(b_desc)

        layout.addWidget(banner)

        # ── フォーム入力欄 ──
        form_frame = QFrame()
        form_frame.setObjectName("formFrame")
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(14, 12, 14, 12)
        form_layout.setSpacing(9)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 動作モード
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("☁️ 本番モード（実際の Azure OpenAI API を使用する）", False)
        self._mode_combo.addItem("🧪 ダミーモード（課金なし・API通信なしで動作テスト）", True)
        form_layout.addRow(self._make_label("動作モード:"), self._mode_combo)

        # エンドポイントURL
        self._endpoint_edit = QLineEdit()
        self._endpoint_edit.setPlaceholderText("https://YOUR_RESOURCE_NAME.openai.azure.com/")
        form_layout.addRow(self._make_label("エンドポイント URL:"), self._endpoint_edit)

        # APIキー（表示/非表示トグル付き）
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("Azure OpenAI API キーを入力...")
        key_row.addWidget(self._api_key_edit, stretch=1)

        self._show_key_btn = QPushButton("👁 表示")
        self._show_key_btn.setObjectName("secondaryBtn")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(74)
        self._show_key_btn.toggled.connect(self._on_toggle_key_visibility)
        key_row.addWidget(self._show_key_btn)
        form_layout.addRow(self._make_label("API キー:"), key_row)

        # APIバージョン
        self._api_ver_edit = QLineEdit()
        self._api_ver_edit.setPlaceholderText("2024-06-01")
        form_layout.addRow(self._make_label("API バージョン:"), self._api_ver_edit)

        # 要約用デプロイメント名（編集可能プルダウン）
        self._chat_dep_combo = QComboBox()
        self._chat_dep_combo.setEditable(True)
        self._chat_dep_edit = self._chat_dep_combo.lineEdit()
        if self._chat_dep_edit:
            self._chat_dep_edit.setPlaceholderText("プルダウンから選択、または新しいデプロイ名を入力...")
        form_layout.addRow(self._make_label("要約用デプロイ名:"), self._chat_dep_combo)

        # 文字起こし用デプロイメント名（編集可能プルダウン）
        self._trans_dep_combo = QComboBox()
        self._trans_dep_combo.setEditable(True)
        self._trans_dep_edit = self._trans_dep_combo.lineEdit()
        if self._trans_dep_edit:
            self._trans_dep_edit.setPlaceholderText("プルダウンから選択、または新しいデプロイ名を入力...")
        self._trans_dep_combo.currentTextChanged.connect(self._on_trans_model_changed)
        form_layout.addRow(self._make_label("文字起こしデプロイ名:"), self._trans_dep_combo)

        # 文字起こしAPI方式
        self._trans_mode_combo = QComboBox()
        self._trans_mode_combo.addItem(
            "audio_api （Whisper / gpt-4o-transcribe / gpt-4o-mini-transcribe）",
            "audio_api",
        )
        self._trans_mode_combo.addItem(
            "chat_audio （gpt-4o-audio-preview / gpt-4o-mini-audio-preview）",
            "chat_audio",
        )
        form_layout.addRow(self._make_label("文字起こしAPI方式:"), self._trans_mode_combo)

        # 使えなくなったモデルの非表示フィルタ設定
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self._hide_unavail_check = QCheckBox("🚫 使えなくなったモデルをプルダウンから非表示にする")
        self._hide_unavail_check.setChecked(self._registry.hide_unavailable)
        self._hide_unavail_check.toggled.connect(self._on_hide_unavailable_toggled)
        filter_row.addWidget(self._hide_unavail_check)

        self._model_stats_lbl = QLabel("")
        self._model_stats_lbl.setObjectName("statsLabel")
        filter_row.addWidget(self._model_stats_lbl, stretch=1)
        form_layout.addRow(self._make_label("モデル一覧フィルタ:"), filter_row)

        # SSL検証無効化オプション
        self._ssl_check = QCheckBox("SSL証明書の検証を無効化する（社内プロキシ環境等でSSLエラーが出る場合）")
        form_layout.addRow(self._make_label("ネットワーク設定:"), self._ssl_check)

        layout.addWidget(form_frame)

        # ── モデル一覧更新 ＆ 接続テストセクション ──
        test_bar = QHBoxLayout()
        test_bar.setSpacing(8)

        self._discover_btn = QPushButton("🔄 使えるモデル一覧を更新")
        self._discover_btn.setObjectName("discoverBtn")
        self._discover_btn.setFixedHeight(36)
        self._discover_btn.setToolTip(
            "全候補モデルをスキャンして「使えるモデル一覧」を最新化し、使えなくなったモデルを非表示にします"
        )
        self._discover_btn.clicked.connect(self._on_discover_clicked)
        test_bar.addWidget(self._discover_btn)

        self._test_btn = QPushButton("🔌 接続テストを実行")
        self._test_btn.setObjectName("testBtn")
        self._test_btn.setFixedHeight(36)
        self._test_btn.setToolTip("現在選択・入力されているデプロイ名で疎通テストを実行します")
        self._test_btn.clicked.connect(self._on_test_clicked)
        test_bar.addWidget(self._test_btn)

        self._test_status_lbl = QLabel("モデル更新時は「🔄 使えるモデル一覧を更新」を実行")
        self._test_status_lbl.setObjectName("subLabel")
        test_bar.addWidget(self._test_status_lbl, stretch=1)
        layout.addLayout(test_bar)

        self._test_log = QTextEdit()
        self._test_log.setObjectName("testLog")
        self._test_log.setReadOnly(True)
        self._test_log.setPlaceholderText("ここに「使えるモデル一覧の更新」や「接続テスト」の結果ログが表示されます...")
        self._test_log.setMaximumHeight(120)
        layout.addWidget(self._test_log)

        # ── 下部アクションボタン ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._dummy_start_btn = QPushButton("🧪 ダミーモードで開始（後で設定）")
        self._dummy_start_btn.setObjectName("secondaryBtn")
        self._dummy_start_btn.setFixedHeight(38)
        self._dummy_start_btn.clicked.connect(self._on_start_dummy_clicked)
        btn_row.addWidget(self._dummy_start_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("キャンセル")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.setFixedHeight(38)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._save_btn = QPushButton("💾 設定を保存して適用")
        self._save_btn.setObjectName("saveBtn")
        self._save_btn.setFixedHeight(38)
        self._save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)

    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("formLabel")
        return lbl

    # ------------------------------------------------------------------ #
    # モデルプルダウンの構築・フィルタ反映
    # ------------------------------------------------------------------ #
    def _refresh_model_combos(self, selected_chat: str = "", selected_trans: str = ""):
        """
        ModelRegistry の状態と「使えなくなったモデルを非表示にする」チェック状態に基づき、
        要約用・文字起こし用の両プルダウン項目を再構築する。
        """
        cur_chat = selected_chat or self._chat_dep_combo.currentText().strip()
        cur_trans = selected_trans or self._trans_dep_combo.currentText().strip()
        hide_unavail = self._hide_unavail_check.isChecked()

        chat_models = self._registry.get_display_chat_models(hide_unavailable=hide_unavail)
        trans_entries = self._registry.get_display_transcribe_models(hide_unavailable=hide_unavail)

        # 要約モデルコンボの更新
        self._chat_dep_combo.blockSignals(True)
        self._chat_dep_combo.clear()
        for m in chat_models:
            is_verified = m in self._registry.available_chat_models
            label = f"✅ {m}" if is_verified else m
            self._chat_dep_combo.addItem(label, m)
            # 編集可能コンボでテキストが m になるよう ItemText を m に統一し ToolTip で状態を示す
            idx = self._chat_dep_combo.count() - 1
            self._chat_dep_combo.setItemText(idx, m)
        if cur_chat:
            self._chat_dep_combo.setEditText(cur_chat)
        elif chat_models:
            self._chat_dep_combo.setCurrentIndex(0)
        self._chat_dep_combo.blockSignals(False)

        # 文字起こしモデルコンボの更新
        self._trans_dep_combo.blockSignals(True)
        self._trans_dep_combo.clear()
        for entry in trans_entries:
            m = entry["name"]
            mode = entry["mode"]
            self._trans_dep_combo.addItem(m, mode)
        if cur_trans:
            self._trans_dep_combo.setEditText(cur_trans)
        elif trans_entries:
            self._trans_dep_combo.setCurrentIndex(0)
        self._trans_dep_combo.blockSignals(False)

        # 統計ラベルの更新
        usable_cnt = len(self._registry.available_chat_models) + len(
            self._registry.available_transcribe_models
        )
        hidden_cnt = len(self._registry.unavailable_models)
        if self._registry.last_updated:
            self._model_stats_lbl.setText(
                f"（確認済み: {usable_cnt}件 / 非表示: {hidden_cnt}件 / 更新: {self._registry.last_updated}）"
            )
        elif hidden_cnt > 0 or usable_cnt > 0:
            self._model_stats_lbl.setText(f"（確認済み: {usable_cnt}件 / 非表示: {hidden_cnt}件）")
        else:
            self._model_stats_lbl.setText("（未スキャン：標準候補を表示中）")

    def _on_hide_unavailable_toggled(self, checked: bool):
        self._registry.hide_unavailable = checked
        self._registry.save()
        self._refresh_model_combos()

    def _on_trans_model_changed(self, model_name: str):
        """文字起こしデプロイ名が変更された際、対応するAPI方式が判明していれば自動切替する。"""
        mode = self._registry.get_transcribe_mode_for_model(model_name)
        if mode:
            idx = self._trans_mode_combo.findData(mode)
            if idx >= 0:
                self._trans_mode_combo.setCurrentIndex(idx)

    def _load_current_config(self):
        config.reload_config()

        if self._is_first_launch and not config.is_api_configured():
            self._mode_combo.setCurrentIndex(0)  # 本番モード
        else:
            idx = 1 if config.USE_DUMMY_API else 0
            self._mode_combo.setCurrentIndex(idx)

        ep = config.AZURE_OPENAI_ENDPOINT
        if ep and ep != config.PLACEHOLDER_ENDPOINT:
            self._endpoint_edit.setText(ep)

        key = config.AZURE_OPENAI_API_KEY
        if key and key != config.PLACEHOLDER_KEY:
            self._api_key_edit.setText(key)

        self._api_ver_edit.setText(config.AZURE_OPENAI_API_VERSION or "2024-06-01")

        chat_dep = config.AZURE_OPENAI_DEPLOYMENT_NAME or "gpt-4o"
        trans_dep = config.AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT or "whisper"
        self._refresh_model_combos(selected_chat=chat_dep, selected_trans=trans_dep)

        t_mode_idx = self._trans_mode_combo.findData(config.TRANSCRIPTION_MODE)
        if t_mode_idx >= 0:
            self._trans_mode_combo.setCurrentIndex(t_mode_idx)

        self._ssl_check.setChecked(config.DISABLE_SSL_VERIFY)

    def _on_toggle_key_visibility(self, checked: bool):
        if checked:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("🙈 隠す")
        else:
            self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("👁 表示")

    # ------------------------------------------------------------------ #
    # 使えるモデル一覧の更新 (Full Scan & Prune Unavailable)
    # ------------------------------------------------------------------ #
    def _on_discover_clicked(self):
        if (self._discover_worker and self._discover_worker.isRunning()) or (
            self._test_worker and self._test_worker.isRunning()
        ):
            return

        endpoint = self._endpoint_edit.text().strip()
        api_key = self._api_key_edit.text().strip()
        api_version = self._api_ver_edit.text().strip() or "2024-06-01"
        chat_dep = self._chat_dep_combo.currentText().strip()
        trans_dep = self._trans_dep_combo.currentText().strip()
        trans_mode = self._trans_mode_combo.currentData() or "audio_api"
        disable_ssl = self._ssl_check.isChecked()

        # 手入力されたカスタム名があれば候補レジストリへ登録しておく
        self._registry.register_custom_candidates(chat_dep, trans_dep, trans_mode)

        self._test_log.clear()
        self._discover_btn.setEnabled(False)
        self._test_btn.setEnabled(False)
        self._discover_btn.setText("⏳ モデル一覧を更新中...")
        self._test_status_lbl.setText("使えるモデルをスキャンし、使えなくなったモデルを非表示化しています...")

        self._discover_worker = ModelDiscoveryWorker(
            endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            current_chat_dep=chat_dep,
            current_trans_dep=trans_dep,
            extra_chat_candidates=self._registry.chat_candidates,
            extra_audio_candidates=self._registry.audio_api_candidates,
            extra_chat_audio_candidates=self._registry.chat_audio_candidates,
            disable_ssl_verify=disable_ssl,
            parent=self,
        )
        self._discover_worker.log_message.connect(self._test_log.append)
        self._discover_worker.scan_results_ready.connect(self._on_scan_results_ready)
        self._discover_worker.discovery_finished.connect(self._on_discovery_finished)
        self._discover_worker.start()

    def _on_scan_results_ready(
        self,
        usable_chat: list[str],
        usable_trans: list[dict[str, str]],
        unusable: list[str],
    ):
        """スキャン完了時にレジストリを更新し、使えないモデルをプルダウンから非表示化する。"""
        self._registry.update_from_scan(
            usable_chat=usable_chat,
            usable_transcribe=usable_trans,
            unusable=unusable,
        )

    def _on_discovery_finished(
        self, found_chat: str, found_trans: str, found_mode: str, summary: str
    ):
        self._discover_btn.setEnabled(True)
        self._test_btn.setEnabled(True)
        self._discover_btn.setText("🔄 使えるモデル一覧を更新")

        # プルダウン一覧を最新化し、発見されたモデルを選択状態にする
        self._refresh_model_combos(
            selected_chat=found_chat or self._chat_dep_combo.currentText().strip(),
            selected_trans=found_trans or self._trans_dep_combo.currentText().strip(),
        )

        if found_mode:
            idx = self._trans_mode_combo.findData(found_mode)
            if idx >= 0:
                self._trans_mode_combo.setCurrentIndex(idx)

        if found_chat and found_trans:
            self._mode_combo.setCurrentIndex(0)  # 本番モードにセット
            self._test_status_lbl.setText("✅ 使えるモデル一覧を更新し、使えないモデルを非表示にしました！")
        else:
            self._test_status_lbl.setText("⚠️ モデル一覧更新のログを確認してください")

        self._test_log.append(f"\n{summary}")

    # ------------------------------------------------------------------ #
    # 個別接続テスト実行
    # ------------------------------------------------------------------ #
    def _on_test_clicked(self):
        if (self._test_worker and self._test_worker.isRunning()) or (
            self._discover_worker and self._discover_worker.isRunning()
        ):
            return

        endpoint = self._endpoint_edit.text().strip()
        api_key = self._api_key_edit.text().strip()
        api_version = self._api_ver_edit.text().strip() or "2024-06-01"
        chat_dep = self._chat_dep_combo.currentText().strip() or "gpt-4o"
        trans_dep = self._trans_dep_combo.currentText().strip() or chat_dep
        trans_mode = self._trans_mode_combo.currentData() or "audio_api"
        disable_ssl = self._ssl_check.isChecked()

        self._pending_test_chat = chat_dep
        self._pending_test_trans = trans_dep
        self._pending_test_mode = trans_mode

        self._test_log.clear()
        self._discover_btn.setEnabled(False)
        self._test_btn.setEnabled(False)
        self._test_btn.setText("⏳ テスト中...")
        self._test_status_lbl.setText("Azure OpenAI へテストリクエストを送信しています...")

        self._test_worker = ConnectionTestWorker(
            endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            chat_deployment=chat_dep,
            transcribe_deployment=trans_dep,
            transcription_mode=trans_mode,
            disable_ssl_verify=disable_ssl,
            parent=self,
        )
        self._test_worker.log_message.connect(self._test_log.append)
        self._test_worker.test_detail_ready.connect(self._on_single_test_detail)
        self._test_worker.test_finished.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_single_test_detail(self, chat_ok: bool, trans_ok: bool):
        """個別テストでも成功したモデルを一覧へ追加、失敗したモデルを非表示リストへ反映する。"""
        chat_dep = getattr(self, "_pending_test_chat", "")
        trans_dep = getattr(self, "_pending_test_trans", "")
        trans_mode = getattr(self, "_pending_test_mode", "audio_api")
        self._registry.mark_single_test_result(
            chat_dep=chat_dep,
            chat_ok=chat_ok,
            trans_dep=trans_dep,
            trans_mode=trans_mode,
            trans_ok=trans_ok,
        )
        self._refresh_model_combos(selected_chat=chat_dep, selected_trans=trans_dep)

    def _on_test_finished(self, success: bool, summary: str):
        self._discover_btn.setEnabled(True)
        self._test_btn.setEnabled(True)
        self._test_btn.setText("🔌 接続テストを実行")
        self._test_status_lbl.setText("✅ 接続テスト成功！" if success else "⚠️ 接続テスト結果を確認してください")
        self._test_log.append(f"\n{summary}")

        if success:
            self._mode_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------ #
    # 保存アクション
    # ------------------------------------------------------------------ #
    def _on_save_clicked(self):
        use_dummy = bool(self._mode_combo.currentData())
        endpoint = self._endpoint_edit.text().strip() or config.PLACEHOLDER_ENDPOINT
        api_key = self._api_key_edit.text().strip() or config.PLACEHOLDER_KEY

        if not use_dummy and (api_key == config.PLACEHOLDER_KEY or "YOUR_RESOURCE" in endpoint):
            reply = QMessageBox.question(
                self,
                "APIキー未入力の確認",
                "「本番モード」が選択されていますが、エンドポイントURLまたはAPIキーが未入力です。\n"
                "このまま保存しますか？（ダミーモードで試す場合は「いいえ」を押してダミーモードを選択してください）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._save_to_env(use_dummy=use_dummy)
        self.accept()

    def _on_start_dummy_clicked(self):
        self._save_to_env(use_dummy=True)
        self.accept()

    def _save_to_env(self, use_dummy: bool):
        endpoint = self._endpoint_edit.text().strip() or config.PLACEHOLDER_ENDPOINT
        api_key = self._api_key_edit.text().strip() or config.PLACEHOLDER_KEY
        api_version = self._api_ver_edit.text().strip() or "2024-06-01"
        chat_dep = self._chat_dep_combo.currentText().strip() or "gpt-4o"
        trans_dep = self._trans_dep_combo.currentText().strip() or "whisper"
        trans_mode = self._trans_mode_combo.currentData() or "audio_api"
        disable_ssl = self._ssl_check.isChecked()

        self._registry.register_custom_candidates(chat_dep, trans_dep, trans_mode)

        config.save_env_settings(
            use_dummy_api=use_dummy,
            endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            chat_deployment=chat_dep,
            transcribe_deployment=trans_dep,
            transcription_mode=trans_mode,
            disable_ssl_verify=disable_ssl,
        )
        reset_azure_client()

    # ------------------------------------------------------------------ #
    # スタイル
    # ------------------------------------------------------------------ #
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1E1E1E;
                color: #E0E0E0;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
            }
            QFrame#bannerFrame {
                background-color: rgba(0, 122, 204, 0.14);
                border: 1px solid rgba(0, 122, 204, 0.4);
                border-radius: 8px;
            }
            QLabel#bannerTitle {
                color: #66BFFF;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel#bannerDesc {
                color: #CCCCCC;
                font-size: 12px;
            }
            QFrame#formFrame {
                background-color: #252526;
                border: 1px solid #333337;
                border-radius: 8px;
            }
            QLabel#formLabel {
                color: #D4D4D4;
                font-size: 12px;
                font-weight: bold;
            }
            QLabel#subLabel {
                color: #AAAAAA;
                font-size: 12px;
            }
            QLabel#statsLabel {
                color: #88C0D0;
                font-size: 11px;
            }
            QLineEdit, QComboBox {
                background-color: #1B1B1C;
                color: #E8E8E8;
                border: 1px solid #3E3E42;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #007ACC;
            }
            QComboBox QAbstractItemView {
                background-color: #2D2D30;
                color: #E8E8E8;
                selection-background-color: #094771;
            }
            QCheckBox {
                color: #D4D4D4;
                font-size: 12px;
            }
            QTextEdit#testLog {
                background-color: #141415;
                color: #DCDCDC;
                border: 1px solid #333337;
                border-radius: 6px;
                font-family: "Consolas", "Yu Gothic UI", monospace;
                font-size: 11px;
                padding: 8px;
            }
            QPushButton#discoverBtn {
                background-color: #8957E5;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#discoverBtn:hover {
                background-color: #A371F7;
            }
            QPushButton#discoverBtn:disabled {
                background-color: #3B2854;
                color: #888888;
            }
            QPushButton#testBtn {
                background-color: #2EA043;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#testBtn:hover {
                background-color: #3FB950;
            }
            QPushButton#testBtn:disabled {
                background-color: #23482A;
                color: #888888;
            }
            QPushButton#saveBtn {
                background-color: #007ACC;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0 18px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#saveBtn:hover {
                background-color: #1F8AD2;
            }
            QPushButton#secondaryBtn {
                background-color: #2D2D30;
                color: #D4D4D4;
                border: 1px solid #44444A;
                border-radius: 6px;
                padding: 0 12px;
                font-size: 12px;
            }
            QPushButton#secondaryBtn:hover {
                background-color: #3E3E42;
                color: #FFFFFF;
            }
        """)
