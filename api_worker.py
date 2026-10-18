"""
api_worker.py
Azure OpenAI API との非同期通信を担当するモジュール。
1) TranscriptionWorker : 録音チャンク (WAV) をリアルタイムに文字起こしする QThread
   - TRANSCRIPTION_MODE="audio_api"  (Whisper / gpt-4o-transcribe / gpt-4o-mini-transcribe)
   - TRANSCRIPTION_MODE="chat_audio" (gpt-4o-audio-preview / gpt-4o-mini-audio-preview)
   - USE_DUMMY_API=True 時は課金なしのダミー文字起こしを返す
2) SummaryWorker : 文字起こしテキスト（＋前提プロンプト文脈）から AI 要約を生成する QThread
3) ConnectionTestWorker : 設定ダイアログ上で API キー・各デプロイメントの接続テストを実行する QThread
"""

import io
import os
import time
import wave
import queue
import base64
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal

import config


SUMMARY_SYSTEM_PROMPT = (
    "あなたは優秀な議事録作成・要約アシスタントです。\n"
    "提供された音声文字起こしテキストの内容を整理し、以下の構成で簡潔かつ分かりやすく日本語で要約してください。\n\n"
    "【全体の要点】\n"
    "・（重要なポイントを箇条書きで整理）\n\n"
    "【詳細・決定事項・今後のアクション（該当する場合）】\n"
    "・（具体的な内容やToDoがあれば記載）\n\n"
    "※音声認識特有の言い淀みや軽微な誤変換がある場合は、前後の文脈や前提知識から適切に補正して要約してください。"
)


# ================================================================== #
# Azure OpenAI クライアント生成（プロキシ・SSL検証設定対応）
# ================================================================== #
_azure_client = None


def reset_azure_client():
    """設定変更時にキャッシュ済みクライアントを破棄し、次回通信時に再生成させる。"""
    global _azure_client
    _azure_client = None


def create_azure_client(
    endpoint: str,
    api_key: str,
    api_version: str,
    disable_ssl_verify: bool = False,
):
    """指定パラメータで AzureOpenAI クライアントを生成する。"""
    from openai import AzureOpenAI
    import httpx

    client_kwargs = {}
    if disable_ssl_verify:
        print("[TranscribAI API] WARNING: SSL証明書の検証を無効にしています (DISABLE_SSL_VERIFY=True)")
        client_kwargs["verify"] = False

    http_proxy = os.getenv("HTTP_PROXY")
    https_proxy = os.getenv("HTTPS_PROXY")

    pref = getattr(config, "PROXY_PREFERENCE", "https")
    if pref == "http":
        proxy_url = http_proxy or https_proxy
    else:
        proxy_url = https_proxy or http_proxy

    if proxy_url:
        print(f"[TranscribAI API] INFO: プロキシ設定を適用します (Priority: {pref})")
        client_kwargs["proxy"] = proxy_url

    http_client = httpx.Client(**client_kwargs)

    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        http_client=http_client,
    )


def _get_azure_client():
    """
    現在の config 設定に基づく AzureOpenAI クライアントをシングルトン的に生成して返す。
    """
    global _azure_client
    if _azure_client is None:
        _azure_client = create_azure_client(
            endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_API_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
            disable_ssl_verify=getattr(config, "DISABLE_SSL_VERIFY", False),
        )
    return _azure_client


def _make_test_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 16000) -> bytes:
    """接続テスト用の極小無音WAVデータ (0.5秒) をインメモリ生成する。"""
    buf = io.BytesIO()
    num_samples = int(sample_rate * duration_sec)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)
    return buf.getvalue()


# ================================================================== #
# リアルタイム文字起こしキューワーカー
# ================================================================== #
class TranscriptionWorker(QThread):
    """
    音声チャンク (WAV bytes) をキューで受け取り、順次 Azure OpenAI で文字起こしを行うワーカースレッド。

    シグナル:
        text_transcribed(str)    : 文字起こし完了テキスト
        transcribing_state(bool) : API通信中フラグ
        error_occurred(str)      : エラーメッセージ
    """

    text_transcribed = pyqtSignal(str)
    transcribing_state = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._chunk_counter = 0

    def enqueue_chunk(self, wav_bytes: bytes, duration_sec: float, prompt_text: str):
        """音声チャンクと現在の前提プロンプトをキューに追加する。"""
        self._queue.put((wav_bytes, duration_sec, prompt_text))

    def stop_worker(self):
        self._running = False
        self._queue.put(None)

    def _transcribe_dummy(self, duration_sec: float, prompt_text: str) -> str:
        time.sleep(0.6)
        self._chunk_counter += 1
        now_str = datetime.now().strftime("%H:%M:%S")
        prompt_preview = prompt_text.strip()[:30] + ("..." if len(prompt_text.strip()) > 30 else "")
        return (
            f"[{now_str} ダミー文字起こし #{self._chunk_counter} ({duration_sec:.1f}秒)] "
            f"音声を正常にキャプチャしました。（適用プロンプト: 「{prompt_preview or 'なし'}」）"
        )

    def _transcribe_audio_api(self, wav_bytes: bytes, prompt_text: str) -> str:
        """
        Whisper / gpt-4o-transcribe / gpt-4o-mini-transcribe 用 (Audio Transcriptions API)
        """
        client = _get_azure_client()
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "chunk.wav"

        kwargs = {
            "model": config.AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT,
            "file": audio_file,
            "language": config.TRANSCRIPTION_LANGUAGE,
        }
        if prompt_text.strip():
            kwargs["prompt"] = prompt_text.strip()

        response = client.audio.transcriptions.create(**kwargs)
        return (response.text or "").strip()

    def _transcribe_chat_audio(self, wav_bytes: bytes, prompt_text: str) -> str:
        """
        gpt-4o-audio-preview / gpt-4o-mini-audio-preview 用 (Chat Completions Audio Input)
        """
        client = _get_azure_client()
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")

        sys_instruction = (
            "あなたは高精度な音声文字起こしエンジンです。"
            "入力された音声をそのまま正確に日本語テキストへ書き起こしてください。"
            "挨拶や解説は一切出力せず、書き起こした本文のみを出力してください。"
        )
        if prompt_text.strip():
            sys_instruction += f"\n\n【前提知識・専門用語・文脈プロンプト】\n{prompt_text.strip()}"

        response = client.chat.completions.create(
            model=config.AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": sys_instruction},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "この音声を正確に文字起こししてください。"},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": "wav",
                            },
                        },
                    ],
                },
            ],
        )
        return (response.choices[0].message.content or "").strip()

    def run(self):
        while self._running:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                break

            wav_bytes, duration_sec, prompt_text = item
            self.transcribing_state.emit(True)

            try:
                if config.USE_DUMMY_API:
                    text = self._transcribe_dummy(duration_sec, prompt_text)
                else:
                    mode = getattr(config, "TRANSCRIPTION_MODE", "audio_api")
                    if mode == "chat_audio":
                        text = self._transcribe_chat_audio(wav_bytes, prompt_text)
                    else:
                        text = self._transcribe_audio_api(wav_bytes, prompt_text)

                if text:
                    self.text_transcribed.emit(text)

            except Exception as e:
                err_msg = f"文字起こしAPIエラー ({type(e).__name__}): {e}"
                print(f"[TranscribAI API] {err_msg}")
                self.error_occurred.emit(err_msg)
            finally:
                self.transcribing_state.emit(False)
                self._queue.task_done()


# ================================================================== #
# AI 要約専用ワーカースレッド
# ================================================================== #
class SummaryWorker(QThread):
    """
    文字起こしされたテキストと前提プロンプトを受け取り、
    Azure OpenAI チャットモデルで要約を生成する QThread。

    シグナル:
        result_ready(str)   : 要約完了テキスト
        error_occurred(str) : エラーメッセージ
    """

    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, transcript_text: str, preset_prompt: str = "", parent=None):
        super().__init__(parent)
        self._transcript_text = transcript_text
        self._preset_prompt = preset_prompt

    def run(self):
        try:
            char_count = len(self._transcript_text)

            if config.USE_DUMMY_API:
                time.sleep(1.2)
                lines = [line.strip() for line in self._transcript_text.splitlines() if line.strip()]
                preview = self._transcript_text[:120] + ("..." if char_count > 120 else "")
                dummy_summary = (
                    "【全体の要点（ダミー要約）】\n"
                    f"・文字起こしテキスト（全 {char_count} 文字 / {len(lines)} 行）を受け取りました。\n"
                    f"・設定中の前提プロンプト: 「{self._preset_prompt.strip()[:40] or '指定なし'}」\n\n"
                    "【対象テキスト抜粋】\n"
                    f"{preview}\n\n"
                    "※ 現在 .env の USE_DUMMY_API=True（ダミーモード）で動作しています。\n"
                    "   実際の Azure OpenAI で要約を行うには、「⚙️ API設定」ボタンからAPIキーとデプロイメント名を設定し、"
                    "本番モードに切り替えてください。"
                )
                self.result_ready.emit(dummy_summary)
                return

            client = _get_azure_client()

            system_content = SUMMARY_SYSTEM_PROMPT
            if self._preset_prompt.strip():
                system_content += f"\n\n【前提プロンプト・専門用語コンテキスト】\n{self._preset_prompt.strip()}"

            messages = [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": f"以下の文字起こしテキストを要約してください。\n\n---\n{self._transcript_text}",
                },
            ]

            print(
                f"[TranscribAI API] 要約リクエスト送信 deployment={config.AZURE_OPENAI_DEPLOYMENT_NAME}, "
                f"chars={char_count}"
            )

            response = client.chat.completions.create(
                model=config.AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=messages,
                stream=False,
            )

            answer = (response.choices[0].message.content or "").strip()
            print(f"[TranscribAI API] 要約完了 ({len(answer)} 文字)")
            self.result_ready.emit(answer)

        except Exception as e:
            err_msg = f"❌ 要約エラーが発生しました:\n{type(e).__name__}: {e}"
            print(f"[TranscribAI API] {err_msg}")
            self.error_occurred.emit(err_msg)


# ================================================================== #
# API 接続テスト専用ワーカースレッド
# ================================================================== #
class ConnectionTestWorker(QThread):
    """
    設定ダイアログ上で入力された認証情報・デプロイメント名を用いて
    1) 要約（チャット）モデル
    2) 音声文字起こしモデル
    の疎通テストを非同期実行する QThread。

    シグナル:
        log_message(str)             : テスト進行ログ
        test_detail_ready(bool, bool): (chat_ok, trans_ok)
        test_finished(bool, str)     : (全体成功フラグ, サマリーメッセージ)
    """

    log_message = pyqtSignal(str)
    test_detail_ready = pyqtSignal(bool, bool)
    test_finished = pyqtSignal(bool, str)

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        api_version: str,
        chat_deployment: str,
        transcribe_deployment: str,
        transcription_mode: str,
        disable_ssl_verify: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.endpoint = endpoint.strip()
        self.api_key = api_key.strip()
        self.api_version = api_version.strip() or "2024-06-01"
        self.chat_deployment = chat_deployment.strip()
        self.transcribe_deployment = transcribe_deployment.strip() or self.chat_deployment
        self.transcription_mode = transcription_mode.strip() or "audio_api"
        self.disable_ssl_verify = disable_ssl_verify

    def run(self):
        if not self.endpoint or "YOUR_RESOURCE" in self.endpoint:
            self.test_finished.emit(False, "⚠️ エンドポイントURLを正しく入力してください。")
            return
        if not self.api_key or "YOUR_API_KEY" in self.api_key:
            self.test_finished.emit(False, "⚠️ APIキーを入力してください。")
            return

        try:
            self.log_message.emit(f"🔄 Azure OpenAI クライアントを初期化中 ({self.endpoint})...")
            client = create_azure_client(
                endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
                disable_ssl_verify=self.disable_ssl_verify,
            )
        except Exception as e:
            self.test_finished.emit(False, f"❌ クライアント初期化エラー: {type(e).__name__}: {e}")
            return

        chat_ok = False
        trans_ok = False

        # 1) 要約（チャット）モデルのテスト
        self.log_message.emit(f"⏳ [1/2] 要約用チャットモデル ({self.chat_deployment}) の接続テスト中...")
        try:
            resp = client.chat.completions.create(
                model=self.chat_deployment,
                messages=[{"role": "user", "content": "OKとだけ返答してください。"}],
                max_tokens=10,
            )
            reply = (resp.choices[0].message.content or "").strip()
            self.log_message.emit(f"✅ [1/2] 要約モデル ({self.chat_deployment}) 接続成功！ (応答: {reply})")
            chat_ok = True
        except Exception as e:
            self.log_message.emit(f"❌ [1/2] 要約モデル ({self.chat_deployment}) 失敗: {type(e).__name__}: {e}")

        # 2) 文字起こしモデルのテスト
        self.log_message.emit(
            f"⏳ [2/2] 文字起こしモデル ({self.transcribe_deployment} / 方式: {self.transcription_mode}) の接続テスト中..."
        )
        try:
            test_wav = _make_test_wav_bytes(0.5)
            if self.transcription_mode == "chat_audio":
                b64_audio = base64.b64encode(test_wav).decode("ascii")
                client.chat.completions.create(
                    model=self.transcribe_deployment,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "無音テストです。空文字またはOKを返してください。"},
                                {"type": "input_audio", "input_audio": {"data": b64_audio, "format": "wav"}},
                            ],
                        }
                    ],
                    max_tokens=10,
                )
            else:
                audio_file = io.BytesIO(test_wav)
                audio_file.name = "test.wav"
                client.audio.transcriptions.create(
                    model=self.transcribe_deployment,
                    file=audio_file,
                    language=config.TRANSCRIPTION_LANGUAGE,
                )
            self.log_message.emit(
                f"✅ [2/2] 文字起こしモデル ({self.transcribe_deployment}) 接続成功！"
            )
            trans_ok = True
        except Exception as e:
            self.log_message.emit(
                f"❌ [2/2] 文字起こしモデル ({self.transcribe_deployment}) 失敗: {type(e).__name__}: {e}"
            )

        self.test_detail_ready.emit(chat_ok, trans_ok)

        if chat_ok and trans_ok:
            self.test_finished.emit(
                True,
                "🎉 要約モデル・文字起こしモデルともに接続テストに成功しました！「💾 設定を保存して適用」を押してください。",
            )
        elif chat_ok or trans_ok:
            self.test_finished.emit(
                False,
                "⚠️ 一部のモデルのみ接続に成功しました。失敗したモデルは一覧から非表示（利用不可）としてマークされました。",
            )
        else:
            self.test_finished.emit(
                False,
                "❌ 接続テストに失敗しました。エンドポイントURL、APIキー、デプロイメント名、プロキシ/SSL設定をご確認ください。",
            )


# ================================================================== #
# 利用可能モデル（デプロイメント）一覧更新・自動探索ワーカースレッド
# ================================================================== #
class ModelDiscoveryWorker(QThread):
    """
    エンドポイントとAPIキーを使って、利用可能な「要約用チャットモデル」および
    「文字起こしモデル（audio_api / chat_audio）」を一括スキャンして一覧を更新し、
    使えなくなったモデルを判定する QThread。

    シグナル:
        log_message(str)                          : 探索ログ
        scan_results_ready(list, list, list)      : (usable_chat_list, usable_transcribe_dicts, unusable_list)
        discovery_finished(str, str, str, str)    : (推奨chatデプロイ名, 推奨transcribeデプロイ名, 推奨mode, 結果サマリー)
    """

    log_message = pyqtSignal(str)
    scan_results_ready = pyqtSignal(list, list, list)
    discovery_finished = pyqtSignal(str, str, str, str)

    CHAT_CANDIDATES = [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3-mini",
        "o1-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-35-turbo",
        "gpt-35-turbo-16k",
    ]

    AUDIO_API_CANDIDATES = [
        "whisper",
        "whisper-1",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
    ]

    CHAT_AUDIO_CANDIDATES = [
        "gpt-4o-audio-preview",
        "gpt-4o-mini-audio-preview",
    ]

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        api_version: str,
        current_chat_dep: str = "",
        current_trans_dep: str = "",
        extra_chat_candidates: list[str] | None = None,
        extra_audio_candidates: list[str] | None = None,
        extra_chat_audio_candidates: list[str] | None = None,
        disable_ssl_verify: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.endpoint = endpoint.strip()
        self.api_key = api_key.strip()
        self.api_version = api_version.strip() or "2024-06-01"
        self.current_chat_dep = current_chat_dep.strip()
        self.current_trans_dep = current_trans_dep.strip()
        self.extra_chat_candidates = extra_chat_candidates or []
        self.extra_audio_candidates = extra_audio_candidates or []
        self.extra_chat_audio_candidates = extra_chat_audio_candidates or []
        self.disable_ssl_verify = disable_ssl_verify

    @staticmethod
    def _dedup(items: list[str]) -> list[str]:
        seen = set()
        result = []
        for x in items:
            x = str(x).strip()
            if x and x not in seen:
                seen.add(x)
                result.append(x)
        return result

    def _is_fatal_auth_or_conn_error(self, exc: Exception) -> bool:
        name = type(exc).__name__
        msg = str(exc).lower()
        if name in ("AuthenticationError", "PermissionDeniedError", "APIConnectionError"):
            return True
        if "401" in msg or "403" in msg or "access denied" in msg or "invalid subscription key" in msg:
            return True
        return False

    def run(self):
        if not self.endpoint or "YOUR_RESOURCE" in self.endpoint:
            self.discovery_finished.emit("", "", "", "⚠️ エンドポイントURLを入力してからモデル一覧更新を実行してください。")
            return
        if not self.api_key or "YOUR_API_KEY" in self.api_key:
            self.discovery_finished.emit("", "", "", "⚠️ APIキーを入力してからモデル一覧更新を実行してください。")
            return

        try:
            self.log_message.emit(f"🔄 使えるモデル一覧のスキャン・更新を開始します ({self.endpoint})...")
            client = create_azure_client(
                endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
                disable_ssl_verify=self.disable_ssl_verify,
            )
        except Exception as e:
            self.discovery_finished.emit("", "", "", f"❌ クライアント初期化エラー: {type(e).__name__}: {e}")
            return

        usable_chat: list[str] = []
        usable_trans: list[dict[str, str]] = []
        unusable_set: set[str] = set()

        # ── 1. チャット（要約）モデルの全候補スキャン ──
        chat_candidates = self._dedup(
            [self.current_chat_dep] + self.extra_chat_candidates + self.CHAT_CANDIDATES
        )
        self.log_message.emit(
            f"⏳ [Step 1/3] 要約用チャットモデルをスキャン中 (全{len(chat_candidates)}候補)..."
        )

        for cand in chat_candidates:
            try:
                client.chat.completions.create(
                    model=cand,
                    messages=[{"role": "user", "content": "OK"}],
                    max_tokens=3,
                )
                self.log_message.emit(f"  ✅ 利用可能 (Chat): 「{cand}」")
                usable_chat.append(cand)
            except Exception as e:
                if self._is_fatal_auth_or_conn_error(e):
                    self.log_message.emit(f"  ❌ 認証または通信エラーで中断: {type(e).__name__}: {e}")
                    self.discovery_finished.emit(
                        "", "", "",
                        "❌ APIキーまたはエンドポイントURL、プロキシ設定が正しくないため接続できませんでした。",
                    )
                    return
                unusable_set.add(cand)
                self.log_message.emit(f"  🚫 利用不可・非表示 (Chat): {cand}")

        # ── 2. 文字起こしモデル (audio_api) の全候補スキャン ──
        test_wav = _make_test_wav_bytes(0.4)
        audio_candidates = self._dedup(
            [self.current_trans_dep] + self.extra_audio_candidates + self.AUDIO_API_CANDIDATES
        )
        self.log_message.emit(
            f"⏳ [Step 2/3] 文字起こしモデル (audio_api) をスキャン中 (全{len(audio_candidates)}候補)..."
        )

        for cand in audio_candidates:
            try:
                audio_file = io.BytesIO(test_wav)
                audio_file.name = "probe.wav"
                client.audio.transcriptions.create(
                    model=cand,
                    file=audio_file,
                    language=config.TRANSCRIPTION_LANGUAGE,
                )
                self.log_message.emit(f"  ✅ 利用可能 (Transcribe / audio_api): 「{cand}」")
                usable_trans.append({"name": cand, "mode": "audio_api"})
            except Exception as e:
                if self._is_fatal_auth_or_conn_error(e):
                    self.log_message.emit(f"  ❌ 認証または通信エラー: {type(e).__name__}: {e}")
                    break
                unusable_set.add(cand)
                self.log_message.emit(f"  🚫 利用不可・非表示 (audio_api): {cand}")

        # ── 3. 音声入力チャットモデル (chat_audio) の全候補スキャン ──
        already_trans_names = {x["name"] for x in usable_trans}
        chat_audio_candidates = [
            c
            for c in self._dedup(
                [self.current_trans_dep]
                + self.extra_chat_audio_candidates
                + self.CHAT_AUDIO_CANDIDATES
            )
            if c not in already_trans_names
        ]
        if chat_audio_candidates:
            self.log_message.emit(
                f"⏳ [Step 3/3] 音声入力チャットモデル (chat_audio) をスキャン中 (全{len(chat_audio_candidates)}候補)..."
            )
            b64_audio = base64.b64encode(test_wav).decode("ascii")

            for cand in chat_audio_candidates:
                try:
                    client.chat.completions.create(
                        model=cand,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "OK"},
                                    {"type": "input_audio", "input_audio": {"data": b64_audio, "format": "wav"}},
                                ],
                            }
                        ],
                        max_tokens=5,
                    )
                    self.log_message.emit(f"  ✅ 利用可能 (Transcribe / chat_audio): 「{cand}」")
                    usable_trans.append({"name": cand, "mode": "chat_audio"})
                except Exception:
                    unusable_set.add(cand)
                    self.log_message.emit(f"  🚫 利用不可・非表示 (chat_audio): {cand}")

        # いずれかで使えたモデルは unusable_set から除外
        all_usable_names = set(usable_chat) | {x["name"] for x in usable_trans}
        unusable_list = sorted(unusable_set - all_usable_names)

        # 一覧更新シグナルを発火
        self.scan_results_ready.emit(usable_chat, usable_trans, unusable_list)

        found_chat = usable_chat[0] if usable_chat else ""
        found_trans = usable_trans[0]["name"] if usable_trans else ""
        found_mode = usable_trans[0]["mode"] if usable_trans else ""

        # ── 結果サマリー作成 ──
        if found_chat and found_trans:
            chat_str = ", ".join(usable_chat)
            trans_str = ", ".join(f"{x['name']}({x['mode']})" for x in usable_trans)
            summary = (
                f"🎉 使えるモデル一覧を更新しました！（使えない {len(unusable_list)} モデルを非表示化）\n"
                f"・利用可能な要約モデル ({len(usable_chat)}件): {chat_str}\n"
                f"・利用可能な文字起こしモデル ({len(usable_trans)}件): {trans_str}\n"
                f"プルダウンから選択して「💾 設定を保存して適用」を押してください。"
            )
        elif found_chat and not found_trans:
            chat_str = ", ".join(usable_chat)
            summary = (
                f"⚠️ 要約用モデル ({chat_str}) は利用可能ですが、文字起こしモデルが見つかりませんでした。\n"
                f"社内Wiki等で別名のデプロイ名が指定されている場合は、プルダウンに直接入力して「🔌 接続テストを実行」をお試しください。"
            )
        elif not found_chat and found_trans:
            trans_str = ", ".join(f"{x['name']}({x['mode']})" for x in usable_trans)
            summary = (
                f"⚠️ 文字起こしモデル ({trans_str}) は利用可能ですが、要約用チャットモデルが見つかりませんでした。\n"
                f"PopAIでお使いのデプロイメント名を「要約用デプロイ名」に直接入力して「🔌 接続テストを実行」をお試しください。"
            )
        else:
            summary = (
                "❌ 登録されている候補名では応答が得られませんでした。\n"
                "社内Wiki等に記載されている固有のデプロイメント名をプルダウンに直接入力して「🔌 接続テストを実行」をお試しください。"
            )

        self.discovery_finished.emit(found_chat, found_trans, found_mode, summary)


