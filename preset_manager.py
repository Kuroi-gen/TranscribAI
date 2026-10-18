"""
preset_manager.py
文字起こし精度向上用の「前提プロンプト」プリセットを presets.json で管理するモジュール。
GUI上のプルダウン選択・編集・新規追加・上書き保存・削除と外部JSONファイルを同期する。
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRESETS_FILE = os.path.join(BASE_DIR, "presets.json")

DEFAULT_PRESETS_DATA = {
    "selected": "汎用（指定なし）",
    "presets": [
        {
            "name": "汎用（指定なし）",
            "prompt": ""
        },
        {
            "name": "IT・システム開発会議",
            "prompt": "Azure OpenAI, API, GitHub, Python, PyQt6, デプロイ, プルリクエスト, スプリント, 要件定義, 基本設計, 詳細設計, バグ改修, リファクタリング, ステージング環境, 本番環境。"
        },
        {
            "name": "ビジネス・定例議事録",
            "prompt": "アジェンダ, KPI, KGI, マイルストーン, 進捗報告, 課題管理, アクションアイテム, 承認, リスケ, ステークホルダー, コンセンサス, 費用対効果。"
        },
        {
            "name": "Oracle DB・インフラ構築",
            "prompt": "Oracle Database, RMAN, Data Pump, expdp, impdp, 表領域, アーカイブログ, バックアップ, リカバリ, インスタンス, リスナー, SQL, パフォーマンスチューニング, 実行計画。"
        }
    ]
}


class PresetManager:
    """presets.json の読み書きとプリセット一覧管理を行うクラス。"""

    def __init__(self, filepath: str = PRESETS_FILE):
        self.filepath = filepath
        self.data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.filepath):
            self._save_data(DEFAULT_PRESETS_DATA)
            return json.loads(json.dumps(DEFAULT_PRESETS_DATA))

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "presets" not in data or not data["presets"]:
                raise ValueError("Invalid presets.json structure")
            return data
        except Exception as e:
            print(f"[TranscribAI] presets.json の読み込みに失敗したため初期値を使用します: {e}")
            return json.loads(json.dumps(DEFAULT_PRESETS_DATA))

    def _save_data(self, data: dict):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TranscribAI] presets.json の保存エラー: {e}")

    def save(self):
        """現在のメモリ上のデータを presets.json に書き込む。"""
        self._save_data(self.data)

    def reload(self):
        """外部エディタで presets.json が編集された場合などに再読み込みする。"""
        self.data = self._load()

    def get_names(self) -> list[str]:
        return [p.get("name", "") for p in self.data.get("presets", []) if p.get("name")]

    def get_selected_name(self) -> str:
        selected = self.data.get("selected", "")
        names = self.get_names()
        if selected in names:
            return selected
        return names[0] if names else ""

    def set_selected_name(self, name: str):
        self.data["selected"] = name
        self.save()

    def get_prompt(self, name: str) -> str:
        for p in self.data.get("presets", []):
            if p.get("name") == name:
                return p.get("prompt", "")
        return ""

    def upsert_preset(self, name: str, prompt: str):
        """既存プリセットの上書き更新、または新規プリセットの追加を行い保存する。"""
        name = name.strip()
        if not name:
            return
        for p in self.data.get("presets", []):
            if p.get("name") == name:
                p["prompt"] = prompt
                self.data["selected"] = name
                self.save()
                return

        self.data.setdefault("presets", []).append({
            "name": name,
            "prompt": prompt
        })
        self.data["selected"] = name
        self.save()

    def delete_preset(self, name: str) -> bool:
        """指定したプリセットを削除する（最後の1件は削除不可）。"""
        presets = self.data.get("presets", [])
        if len(presets) <= 1:
            return False

        new_presets = [p for p in presets if p.get("name") != name]
        if len(new_presets) == len(presets):
            return False

        self.data["presets"] = new_presets
        if self.data.get("selected") == name:
            self.data["selected"] = new_presets[0]["name"]
        self.save()
        return True
