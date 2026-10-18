"""
config.py
外部ファイル (.env) から Azure OpenAI 認証情報および動作設定を読み込む・保存するモジュール。
APIキー等の機密情報はアプリケーション本体とは別の .env ファイルに分離して管理する。
"""

import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

PLACEHOLDER_KEY = "YOUR_API_KEY_HERE"
PLACEHOLDER_ENDPOINT = "https://YOUR_RESOURCE_NAME.openai.azure.com/"


def _clean_env(key: str, default: str = "") -> str:
    """環境変数を取得し、インラインコメントや前後の空白を除去して返す。"""
    val = os.getenv(key, default)
    if val is None:
        return default
    if " #" in val:
        val = val.split(" #", 1)[0]
    return val.strip()


def _clean_float(key: str, default: float) -> float:
    raw = _clean_env(key, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


# モジュールレベル変数の初期宣言
USE_DUMMY_API: bool = True
AZURE_OPENAI_ENDPOINT: str = ""
AZURE_OPENAI_API_KEY: str = ""
AZURE_OPENAI_DEPLOYMENT_NAME: str = ""
AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT: str = ""
AZURE_OPENAI_API_VERSION: str = ""
TRANSCRIPTION_MODE: str = "audio_api"
TRANSCRIPTION_LANGUAGE: str = "ja"
AUDIO_CHUNK_SECONDS: float = 5.0
SILENCE_DURATION_SECONDS: float = 0.8
PROXY_PREFERENCE: str = "https"
DISABLE_SSL_VERIFY: bool = False


def reload_config():
    """
    .env ファイルを読み込み、モジュールレベルの設定変数をすべて最新化する。
    """
    global USE_DUMMY_API
    global AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
    global AZURE_OPENAI_DEPLOYMENT_NAME, AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT
    global AZURE_OPENAI_API_VERSION, TRANSCRIPTION_MODE
    global TRANSCRIPTION_LANGUAGE, AUDIO_CHUNK_SECONDS, SILENCE_DURATION_SECONDS
    global PROXY_PREFERENCE, DISABLE_SSL_VERIFY

    load_dotenv(dotenv_path=ENV_PATH, override=True)

    _dummy = _clean_env("USE_DUMMY_API", "True").lower()
    USE_DUMMY_API = (_dummy != "false")

    AZURE_OPENAI_ENDPOINT = _clean_env(
        "AZURE_OPENAI_ENDPOINT",
        PLACEHOLDER_ENDPOINT
    )
    AZURE_OPENAI_API_KEY = _clean_env(
        "AZURE_OPENAI_API_KEY",
        PLACEHOLDER_KEY
    )
    AZURE_OPENAI_DEPLOYMENT_NAME = _clean_env(
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "gpt-4o"
    )
    AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT = (
        _clean_env("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT", "")
        or AZURE_OPENAI_DEPLOYMENT_NAME
    )
    AZURE_OPENAI_API_VERSION = _clean_env(
        "AZURE_OPENAI_API_VERSION",
        "2024-06-01"
    )

    TRANSCRIPTION_MODE = _clean_env("TRANSCRIPTION_MODE", "audio_api").lower()
    TRANSCRIPTION_LANGUAGE = _clean_env("TRANSCRIPTION_LANGUAGE", "ja")
    AUDIO_CHUNK_SECONDS = _clean_float("AUDIO_CHUNK_SECONDS", 5.0)
    SILENCE_DURATION_SECONDS = _clean_float("SILENCE_DURATION_SECONDS", 0.8)

    PROXY_PREFERENCE = _clean_env("PROXY_PREFERENCE", "https").lower()
    DISABLE_SSL_VERIFY = (_clean_env("DISABLE_SSL_VERIFY", "False").lower() == "true")


def is_api_configured() -> bool:
    """
    有効な Azure OpenAI の APIキーとエンドポイントが設定済みかどうかを判定する。
    未設定（プレースホルダーのまま、または空欄）の場合は False を返す。
    """
    key = AZURE_OPENAI_API_KEY.strip()
    endpoint = AZURE_OPENAI_ENDPOINT.strip()
    if not key or key == PLACEHOLDER_KEY or "YOUR_API_KEY" in key:
        return False
    if not endpoint or "YOUR_RESOURCE" in endpoint:
        return False
    return True


def save_env_settings(
    *,
    use_dummy_api: bool,
    endpoint: str,
    api_key: str,
    api_version: str,
    chat_deployment: str,
    transcribe_deployment: str,
    transcription_mode: str,
    disable_ssl_verify: bool = False,
     env_path: str = ENV_PATH,
):
    """
    GUIダイアログで設定された内容を外部 .env ファイルへ保存し、メモリ上の設定を再読み込みする。
    """
    content = (
        "# ==============================================================================\n"
        "# TranscribAI 設定ファイル (.env)\n"
        "# アプリケーション本体とは分離された Azure OpenAI API キー・環境設定ファイルです。\n"
        "# ==============================================================================\n\n"
        "# ── ダミーモード切り替え ─────────────────────────────────────────────────────\n"
        f"USE_DUMMY_API={'True' if use_dummy_api else 'False'}\n\n"
        "# ── Azure OpenAI 基本認証情報 ────────────────────────────────────────────────\n"
        f"AZURE_OPENAI_ENDPOINT={endpoint.strip()}\n"
        f"AZURE_OPENAI_API_KEY={api_key.strip()}\n"
        f"AZURE_OPENAI_API_VERSION={api_version.strip() or '2024-06-01'}\n\n"
        "# ── デプロイメント名（モデル名）設定 ─────────────────────────────────────────\n"
        f"AZURE_OPENAI_DEPLOYMENT_NAME={chat_deployment.strip() or 'gpt-4o'}\n"
        f"AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT={transcribe_deployment.strip() or chat_deployment.strip() or 'whisper'}\n\n"
        "# ── 文字起こしAPIの方式 (TRANSCRIPTION_MODE) ─────────────────────────────────\n"
        "#   audio_api  : Whisper / gpt-4o-transcribe / gpt-4o-mini-transcribe 用\n"
        "#   chat_audio : gpt-4o-audio-preview / gpt-4o-mini-audio-preview 用\n"
        f"TRANSCRIPTION_MODE={transcription_mode.strip() or 'audio_api'}\n\n"
        "# ── 音声認識の言語コードとチャンク分割設定 ───────────────────────────────────\n"
        f"TRANSCRIPTION_LANGUAGE={TRANSCRIPTION_LANGUAGE}\n"
        f"AUDIO_CHUNK_SECONDS={AUDIO_CHUNK_SECONDS}\n"
        f"SILENCE_DURATION_SECONDS={SILENCE_DURATION_SECONDS}\n\n"
        "# ── プロキシ・SSL設定（社内ネットワーク環境等向け） ──────────────────────────\n"
        f"PROXY_PREFERENCE={PROXY_PREFERENCE}\n"
        f"DISABLE_SSL_VERIFY={'True' if disable_ssl_verify else 'False'}\n"
    )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    if env_path == ENV_PATH:
        reload_config()


# 初回インポート時に読み込む
reload_config()
