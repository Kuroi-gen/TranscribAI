"""
model_registry.py
利用可能な Azure OpenAI デプロイメント（モデル）の一覧・スキャン結果・非表示（利用不可）リストを
外部 JSON ファイル (models_cache.json) で永続化管理するモジュール。

機能:
- 使えるモデル一覧の保持・更新（チャット用 / 文字起こし用）
- 使えなくなったモデルの記録と非表示フィルタリング
- ユーザーが新しく手入力したカスタムデプロイ名の候補への自動追加
"""

import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_CACHE_PATH = os.path.join(BASE_DIR, "models_cache.json")

DEFAULT_CHAT_CANDIDATES = [
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

DEFAULT_AUDIO_API_CANDIDATES = [
    "whisper",
    "whisper-1",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
]

DEFAULT_CHAT_AUDIO_CANDIDATES = [
    "gpt-4o-audio-preview",
    "gpt-4o-mini-audio-preview",
]


def _dedup(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


class ModelRegistry:
    """
    使えるモデル一覧と使えなくなった（非表示）モデルを管理するクラス。
    """

    def __init__(self, filepath: str = MODELS_CACHE_PATH):
        self.filepath = filepath
        self.chat_candidates: list[str] = list(DEFAULT_CHAT_CANDIDATES)
        self.audio_api_candidates: list[str] = list(DEFAULT_AUDIO_API_CANDIDATES)
        self.chat_audio_candidates: list[str] = list(DEFAULT_CHAT_AUDIO_CANDIDATES)

        # スキャンで確認された「使えるモデル」
        self.available_chat_models: list[str] = []
        # [{"name": "whisper", "mode": "audio_api"}, ...]
        self.available_transcribe_models: list[dict[str, str]] = []

        # スキャンで応答しなかった（使えなくなった・未デプロイの）モデル名一覧
        self.unavailable_models: list[str] = []

        # UI設定: 使えなくなったモデルを非表示にするか
        self.hide_unavailable: bool = True

        # 最終スキャン日時
        self.last_updated: str = ""

        self.load()

    def load(self):
        if not os.path.exists(self.filepath):
            self.save()
            return

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.chat_candidates = _dedup(
                data.get("chat_candidates", []) + DEFAULT_CHAT_CANDIDATES
            )
            self.audio_api_candidates = _dedup(
                data.get("audio_api_candidates", []) + DEFAULT_AUDIO_API_CANDIDATES
            )
            self.chat_audio_candidates = _dedup(
                data.get("chat_audio_candidates", []) + DEFAULT_CHAT_AUDIO_CANDIDATES
            )
            self.available_chat_models = _dedup(data.get("available_chat_models", []))

            raw_trans = data.get("available_transcribe_models", [])
            valid_trans = []
            seen_t = set()
            for entry in raw_trans:
                if isinstance(entry, dict) and entry.get("name"):
                    name = str(entry["name"]).strip()
                    mode = str(entry.get("mode", "audio_api")).strip()
                    if name and name not in seen_t:
                        seen_t.add(name)
                        valid_trans.append({"name": name, "mode": mode})
            self.available_transcribe_models = valid_trans

            self.unavailable_models = _dedup(data.get("unavailable_models", []))
            self.hide_unavailable = bool(data.get("hide_unavailable", True))
            self.last_updated = str(data.get("last_updated", ""))
        except Exception as e:
            print(f"[TranscribAI ModelRegistry] 読み込みエラー ({e})。初期値を使用します。")

    def save(self):
        data = {
            "chat_candidates": self.chat_candidates,
            "audio_api_candidates": self.audio_api_candidates,
            "chat_audio_candidates": self.chat_audio_candidates,
            "available_chat_models": self.available_chat_models,
            "available_transcribe_models": self.available_transcribe_models,
            "unavailable_models": self.unavailable_models,
            "hide_unavailable": self.hide_unavailable,
            "last_updated": self.last_updated,
        }
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TranscribAI ModelRegistry] 保存エラー: {e}")

    def register_custom_candidates(
        self, chat_dep: str = "", trans_dep: str = "", trans_mode: str = "audio_api"
    ):
        """ユーザーが入力したカスタムデプロイ名を次回以降のスキャン候補にも登録する。"""
        chat_dep = chat_dep.strip()
        trans_dep = trans_dep.strip()
        if chat_dep and chat_dep not in self.chat_candidates:
            self.chat_candidates.insert(0, chat_dep)
        if trans_dep:
            if trans_mode == "chat_audio":
                if trans_dep not in self.chat_audio_candidates:
                    self.chat_audio_candidates.insert(0, trans_dep)
            else:
                if trans_dep not in self.audio_api_candidates:
                    self.audio_api_candidates.insert(0, trans_dep)
        self.save()

    def update_from_scan(
        self,
        *,
        usable_chat: list[str],
        usable_transcribe: list[dict[str, str]],
        unusable: list[str],
    ):
        """
        一括スキャン結果で「使えるモデル一覧」と「使えなくなったモデル一覧」を更新し保存する。
        """
        self.available_chat_models = _dedup(usable_chat)

        valid_trans = []
        seen_t = set()
        for entry in usable_transcribe:
            name = str(entry.get("name", "")).strip()
            mode = str(entry.get("mode", "audio_api")).strip()
            if name and name not in seen_t:
                seen_t.add(name)
                valid_trans.append({"name": name, "mode": mode})
        self.available_transcribe_models = valid_trans

        # 使えると判明したモデルは unavailable から除外する
        usable_names = set(self.available_chat_models) | seen_t
        self.unavailable_models = [
            m for m in _dedup(unusable) if m not in usable_names
        ]
        self.last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def mark_single_test_result(
        self,
        *,
        chat_dep: str,
        chat_ok: bool,
        trans_dep: str,
        trans_mode: str,
        trans_ok: bool,
    ):
        """
        個別接続テストの結果をレジストリに反映する。
        使えたモデルは「使えるモデル一覧」へ追加し、失敗したモデルは「使えないモデル」に移す。
        """
        self.register_custom_candidates(chat_dep, trans_dep, trans_mode)

        chat_dep = chat_dep.strip()
        if chat_dep:
            if chat_ok:
                if chat_dep not in self.available_chat_models:
                    self.available_chat_models.insert(0, chat_dep)
                if chat_dep in self.unavailable_models:
                    self.unavailable_models.remove(chat_dep)
            else:
                if chat_dep in self.available_chat_models:
                    self.available_chat_models.remove(chat_dep)
                if chat_dep not in self.unavailable_models:
                    self.unavailable_models.append(chat_dep)

        trans_dep = trans_dep.strip()
        if trans_dep:
            if trans_ok:
                existing_names = [x["name"] for x in self.available_transcribe_models]
                if trans_dep not in existing_names:
                    self.available_transcribe_models.insert(
                        0, {"name": trans_dep, "mode": trans_mode}
                    )
                else:
                    for x in self.available_transcribe_models:
                        if x["name"] == trans_dep:
                            x["mode"] = trans_mode
                if trans_dep in self.unavailable_models:
                    self.unavailable_models.remove(trans_dep)
            else:
                self.available_transcribe_models = [
                    x for x in self.available_transcribe_models if x["name"] != trans_dep
                ]
                if trans_dep not in self.unavailable_models:
                    self.unavailable_models.append(trans_dep)

        self.save()

    def get_display_chat_models(self, hide_unavailable: bool | None = None) -> list[str]:
        """
        UIのプルダウンに表示する要約用モデル一覧を返す。
        - hide_unavailable=True かつスキャン済みの場合は、使えるモデルのみ（使えなくなったモデルを非表示）
        - 未スキャンまたは hide_unavailable=False の場合は、候補一覧から非表示対象を除いた（または全候補）リスト
        """
        hide = self.hide_unavailable if hide_unavailable is None else hide_unavailable
        if hide:
            if self.available_chat_models:
                return list(self.available_chat_models)
            # まだ一度もスキャン成功していない場合は、明示的に不可と判明したものを除いた候補を返す
            unavail = set(self.unavailable_models)
            return [m for m in self.chat_candidates if m not in unavail]
        else:
            return _dedup(self.available_chat_models + self.chat_candidates)

    def get_display_transcribe_models(
        self, hide_unavailable: bool | None = None
    ) -> list[dict[str, str]]:
        """
        UIのプルダウンに表示する文字起こしモデル一覧 [{"name": ..., "mode": ...}] を返す。
        """
        hide = self.hide_unavailable if hide_unavailable is None else hide_unavailable

        all_candidate_entries: list[dict[str, str]] = []
        seen = set()
        for entry in self.available_transcribe_models:
            all_candidate_entries.append(entry)
            seen.add(entry["name"])
        for m in self.audio_api_candidates:
            if m not in seen:
                all_candidate_entries.append({"name": m, "mode": "audio_api"})
                seen.add(m)
        for m in self.chat_audio_candidates:
            if m not in seen:
                all_candidate_entries.append({"name": m, "mode": "chat_audio"})
                seen.add(m)

        if hide:
            if self.available_transcribe_models:
                return list(self.available_transcribe_models)
            unavail = set(self.unavailable_models)
            return [e for e in all_candidate_entries if e["name"] not in unavail]
        else:
            return all_candidate_entries

    def get_transcribe_mode_for_model(self, model_name: str) -> str | None:
        """モデル名に対応する推奨文字起こし方式 ('audio_api' or 'chat_audio') を返す。"""
        model_name = model_name.strip()
        for entry in self.available_transcribe_models:
            if entry["name"] == model_name:
                return entry["mode"]
        if model_name in self.chat_audio_candidates or "audio-preview" in model_name:
            return "chat_audio"
        if model_name in self.audio_api_candidates or "whisper" in model_name or "transcribe" in model_name:
            return "audio_api"
        return None
