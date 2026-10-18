"""
test_transcribai.py
TranscribAI の各モジュール（設定・プリセット管理・音声処理・APIワーカー・API設定/接続テストダイアログ・GUI動作）を検証するテストスクリプト。
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from PyQt6.QtWidgets import QApplication, QLineEdit

import config
from model_registry import ModelRegistry
from preset_manager import PresetManager
from audio_recorder import _resample_to_16k_mono, _pcm_to_wav_bytes
from api_worker import TranscriptionWorker, SummaryWorker, ConnectionTestWorker, ModelDiscoveryWorker
from settings_dialog import ApiSettingsDialog
from main_window import MainWindow


app = QApplication.instance() or QApplication(sys.argv)


class TestTranscribAI(unittest.TestCase):

    def test_preset_manager_crud(self):
        """presets.json の作成・読込・新規追加・上書き・削除が正しく動作するか検証"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_json = os.path.join(tmpdir, "presets.json")
            mgr = PresetManager(filepath=test_json)
            self.assertTrue(os.path.exists(test_json))

            names = mgr.get_names()
            self.assertGreaterEqual(len(names), 3)

            # 新規追加
            mgr.upsert_preset("テスト会議", "用語A, 用語B")
            self.assertIn("テスト会議", mgr.get_names())
            self.assertEqual(mgr.get_prompt("テスト会議"), "用語A, 用語B")
            self.assertEqual(mgr.get_selected_name(), "テスト会議")

            # 上書き保存
            mgr.upsert_preset("テスト会議", "用語A, 用語B, 用語C")
            mgr2 = PresetManager(filepath=test_json)
            self.assertEqual(mgr2.get_prompt("テスト会議"), "用語A, 用語B, 用語C")

            # 削除
            self.assertTrue(mgr2.delete_preset("テスト会議"))
            self.assertNotIn("テスト会議", mgr2.get_names())

    def test_audio_resampling_and_wav(self):
        """48kHz ステレオ PCM データが 16kHz モノラル WAV へ正しく変換されるか検証"""
        src_rate = 48000
        src_channels = 2
        t = np.linspace(0, 1.0, src_rate, endpoint=False)
        sine = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        stereo = np.column_stack([sine, sine]).flatten()

        resampled = _resample_to_16k_mono(stereo.tobytes(), src_rate, src_channels)
        self.assertEqual(len(resampled), 16000)

        wav_bytes = _pcm_to_wav_bytes(resampled)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertGreater(len(wav_bytes), 32000)

    def test_summary_worker_dummy(self):
        """ダミーモードで SummaryWorker が要約テキストを返すか検証"""
        config.USE_DUMMY_API = True
        worker = SummaryWorker("これはテスト用の文字起こしテキストです。", "IT用語プリセット")
        results = []
        worker.result_ready.connect(results.append)
        worker.run()
        self.assertEqual(len(results), 1)
        self.assertIn("ダミー要約", results[0])

    @patch("api_worker._get_azure_client")
    def test_transcription_worker_modes(self, mock_get_client):
        """本番モードの audio_api と chat_audio の両方が正しくリクエストを構築するか検証"""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # 1. audio_api モード
        mock_trans_resp = MagicMock()
        mock_trans_resp.text = "Audio APIの文字起こし結果です。"
        mock_client.audio.transcriptions.create.return_value = mock_trans_resp

        worker = TranscriptionWorker()
        dummy_wav = _pcm_to_wav_bytes(np.zeros(1600, dtype=np.int16))
        res_audio = worker._transcribe_audio_api(dummy_wav, "専門用語プロンプト")
        self.assertEqual(res_audio, "Audio APIの文字起こし結果です。")
        mock_client.audio.transcriptions.create.assert_called_once()

        # 2. chat_audio モード
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [MagicMock()]
        mock_chat_resp.choices[0].message.content = "Chat Audioの文字起こし結果です。"
        mock_client.chat.completions.create.return_value = mock_chat_resp

        res_chat = worker._transcribe_chat_audio(dummy_wav, "専門用語プロンプト")
        self.assertEqual(res_chat, "Chat Audioの文字起こし結果です。")
        mock_client.chat.completions.create.assert_called_once()

        worker.stop_worker()

    @patch("api_worker.create_azure_client")
    def test_connection_test_worker_and_dialog(self, mock_create_client):
        """API設定・接続テストダイアログおよび ConnectionTestWorker の動作を検証"""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [MagicMock()]
        mock_chat_resp.choices[0].message.content = "OK"
        mock_client.chat.completions.create.return_value = mock_chat_resp

        mock_trans_resp = MagicMock()
        mock_trans_resp.text = ""
        mock_client.audio.transcriptions.create.return_value = mock_trans_resp

        # ConnectionTestWorker の検証
        worker = ConnectionTestWorker(
            endpoint="https://my-resource.openai.azure.com/",
            api_key="test-secret-key-12345",
            api_version="2024-06-01",
            chat_deployment="gpt-4o",
            transcribe_deployment="whisper",
            transcription_mode="audio_api",
        )
        finished_results = []
        worker.test_finished.connect(lambda ok, msg: finished_results.append((ok, msg)))
        worker.run()

        self.assertEqual(len(finished_results), 1)
        self.assertTrue(finished_results[0][0])
        self.assertIn("接続テストに成功しました", finished_results[0][1])

        # ApiSettingsDialog のUI動作検証
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(filepath=os.path.join(tmpdir, "models_cache.json"))
            dlg = ApiSettingsDialog(is_first_launch=True, registry=reg)
            self.assertEqual(dlg._api_key_edit.echoMode(), QLineEdit.EchoMode.Password)
            dlg._show_key_btn.setChecked(True)
            self.assertEqual(dlg._api_key_edit.echoMode(), QLineEdit.EchoMode.Normal)
            dlg.close()

            # save_env_settings の一時ファイル出力検証
            tmp_env = os.path.join(tmpdir, ".env")
            config.save_env_settings(
                use_dummy_api=False,
                endpoint="https://example.openai.azure.com/",
                api_key="real_key_abc",
                api_version="2024-06-01",
                chat_deployment="gpt-4o",
                transcribe_deployment="gpt-4o-transcribe",
                transcription_mode="audio_api",
                disable_ssl_verify=True,
                env_path=tmp_env,
            )
            with open(tmp_env, "r", encoding="utf-8") as f:
                env_text = f.read()
            self.assertIn("AZURE_OPENAI_API_KEY=real_key_abc", env_text)
            self.assertIn("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT=gpt-4o-transcribe", env_text)
            self.assertIn("DISABLE_SSL_VERIFY=True", env_text)

    @patch("api_worker.create_azure_client")
    def test_model_discovery_and_hide_unavailable(self, mock_create_client):
        """使えるモデル一覧の更新と、使えなくなったモデルの非表示フィルタリングを検証"""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        # チャットは gpt-4o-mini と gpt-4.1 の2つが利用可能、それ以外 (gpt-4o, gpt-35-turbo 等) は廃止・利用不可とするモック
        def fake_chat_create(model, **kwargs):
            if model in ("gpt-4o-mini", "gpt-4.1"):
                # chat_audio のテストでない場合のみ成功
                messages = kwargs.get("messages", [])
                if messages and isinstance(messages[0].get("content"), list):
                    raise Exception("AudioNotSupported")
                return MagicMock()
            raise Exception("DeploymentNotFound")

        # 文字起こしは whisper-1 と gpt-4o-transcribe が利用可能、whisper は利用不可とするモック
        def fake_trans_create(model, **kwargs):
            if model in ("whisper-1", "gpt-4o-transcribe"):
                return MagicMock()
            raise Exception("DeploymentNotFound")

        mock_client.chat.completions.create.side_effect = fake_chat_create
        mock_client.audio.transcriptions.create.side_effect = fake_trans_create

        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(filepath=os.path.join(tmpdir, "models_cache.json"))
            dlg = ApiSettingsDialog(is_first_launch=False, registry=reg)

            worker = ModelDiscoveryWorker(
                endpoint="https://my-resource.openai.azure.com/",
                api_key="test-key",
                api_version="2024-06-01",
                current_chat_dep="",
                current_trans_dep="",
            )
            worker.scan_results_ready.connect(dlg._on_scan_results_ready)
            worker.discovery_finished.connect(dlg._on_discovery_finished)
            worker.run()

            # 1) 使えるモデル一覧が更新されていること
            self.assertEqual(reg.available_chat_models, ["gpt-4o-mini", "gpt-4.1"])
            self.assertEqual(
                [x["name"] for x in reg.available_transcribe_models],
                ["whisper-1", "gpt-4o-transcribe"],
            )

            # 2) 使えなくなったモデル（gpt-35-turbo や whisper 等）が unavailable に記録され、
            #    プルダウン一覧から非表示になっていること
            self.assertIn("gpt-35-turbo", reg.unavailable_models)
            self.assertIn("whisper", reg.unavailable_models)

            chat_combo_items = [
                dlg._chat_dep_combo.itemText(i) for i in range(dlg._chat_dep_combo.count())
            ]
            self.assertEqual(chat_combo_items, ["gpt-4o-mini", "gpt-4.1"])
            self.assertNotIn("gpt-35-turbo", chat_combo_items)

            trans_combo_items = [
                dlg._trans_dep_combo.itemText(i) for i in range(dlg._trans_dep_combo.count())
            ]
            self.assertEqual(trans_combo_items, ["whisper-1", "gpt-4o-transcribe"])
            self.assertNotIn("whisper", trans_combo_items)

            # 3) 「使えなくなったモデルを非表示にする」チェックをOFFにすると全候補が表示されること
            dlg._hide_unavail_check.setChecked(False)
            all_chat_items = [
                dlg._chat_dep_combo.itemText(i) for i in range(dlg._chat_dep_combo.count())
            ]
            self.assertIn("gpt-35-turbo", all_chat_items)

            dlg.close()

    def test_main_window_ui_flow(self):
        """MainWindow の初期化、プリセット切替、カーソル位置を維持した文字起こし追記、要約ボタン動作を検証"""
        config.USE_DUMMY_API = True
        win = MainWindow()

        # プリセットが読み込まれていること
        self.assertGreater(win._preset_combo.count(), 1)
        win._preset_combo.setCurrentIndex(1)
        self.assertTrue(len(win._prompt_edit.toPlainText()) > 0)

        # 文字起こし結果の追記シミュレーション
        win._on_chunk_transcribed("第1チャンクの認識テキストです。")
        cursor = win._transcript_area.textCursor()
        cursor.setPosition(0)
        win._transcript_area.setTextCursor(cursor)

        win._on_chunk_transcribed("第2チャンクの認識テキストです。")
        self.assertEqual(win._transcript_area.textCursor().position(), 0)

        transcript = win._transcript_area.toPlainText()
        self.assertIn("第1チャンクの認識テキストです。", transcript)
        self.assertIn("第2チャンクの認識テキストです。", transcript)

        # 要約結果の反映シミュレーション
        win._on_summary_ready("・要約ポイント1\n・要約ポイント2")
        self.assertIn("要約ポイント1", win._summary_area.toPlainText())

        win.close()


if __name__ == "__main__":
    unittest.main()
