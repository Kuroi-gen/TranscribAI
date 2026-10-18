# TranscribAI セットアップ・利用手順書

このドキュメントは、**TranscribAI（リアルタイム音声文字起こし＆AI要約アプリケーション）** のセットアップ手順、使い方、および Azure OpenAI のモデル設定方法を説明します。

---

## 1. 事前準備（必要なライブラリのインストール）

コマンドプロンプトまたは PowerShell でプロジェクトフォルダへ移動し、以下のコマンドを実行してください。

```powershell
cd TranscribAI
pip install -r requirements.txt
```

> **主な利用ライブラリ**
> - `PyQt6`: 標準ウィンドウGUI（最前面ピン留め・上下スプリッター対応）
> - `PyAudioWPatch`: Windows 11 マイク入力および PC内部音声（WASAPI Loopback）のキャプチャ
> - `numpy`: 音声リサンプリング・音量レベル計算・無音区間検知
> - `openai` / `httpx`: Azure OpenAI API クライアント（プロキシ・SSL設定対応）
> - `python-dotenv`: 外部 `.env` ファイルからのAPIキー読み込み

---

## 2. アプリケーションの起動方法

### コンソール付きで起動（動作ログを確認したい場合）
```powershell
python main.py
```

### コンソールなしで起動（通常利用・ショートカット登録向け）
`main.pyw` をダブルクリックするか、以下を実行します。
```powershell
pythonw main.pyw
```

---

## 3. Azure OpenAI API キー・モデルの設定方法（`.env` ファイル）

APIキーやエンドポイントはアプリケーションコードとは分離された **`.env`** ファイルで管理します。

### ① 初期状態（ダミーモード）
初期状態では `.env` の `USE_DUMMY_API=True` となっており、**APIキー未設定・課金なし**で録音、一時停止、プリセット編集、リアルタイム文字起こし表示、要約ボタンの動作を確認できます。

### ② 本番モードへの切り替え
Azure OpenAI のデプロイメントモデルをご確認後、`.env` ファイルをテキストエディタで開き、以下のように編集してください。

```ini
# 本番モードにする場合は False に変更
USE_DUMMY_API=False

# Azure OpenAI のエンドポイントとAPIキー
AZURE_OPENAI_ENDPOINT=https://あなたの環境名.openai.azure.com/
AZURE_OPENAI_API_KEY=あなたのAPIキー
AZURE_OPENAI_API_VERSION=2024-06-01

# AI要約に使用するチャットモデルのデプロイメント名 (例: gpt-4o, gpt-4o-mini 等)
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

# 音声文字起こしに使用するデプロイメント名
AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT=whisper

# 文字起こしAPIの方式
TRANSCRIPTION_MODE=audio_api
```

### ③ 対応モデル別の `TRANSCRIPTION_MODE` 設定
明日以降に Azure OpenAI のデプロイモデルをご確認された際、利用可能なモデルに応じて `TRANSCRIPTION_MODE` を以下のいずれかに設定してください：

1. **`whisper` / `gpt-4o-transcribe` / `gpt-4o-mini-transcribe` がデプロイされている場合（推奨）**
   ```ini
   TRANSCRIPTION_MODE=audio_api
   AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT=デプロイ名（例: whisper）
   ```
2. **`gpt-4o-audio-preview` / `gpt-4o-mini-audio-preview` がデプロイされている場合**
   ```ini
   TRANSCRIPTION_MODE=chat_audio
   AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT=デプロイ名（例: gpt-4o-audio-preview）
   ```

---

## 4. 主な機能と使い方

1. **録音開始 / 停止・一時停止 / 再開**
   - `⏺ 録音開始` を押すと録音が始まり、発話の区切り（または約5秒ごと）にリアルタイムで文字起こし結果が上段エリアに追記されます。
   - **録音中に他のアプリケーションをマウスでクリックしたりキーボード入力を行ったりしても、録音・文字起こしは一切止まりません。**
   - `⏸ 一時停止` を押すとその時点までの音声を文字起こしして待機状態になり、`▶ 再開` で続きから録音を再開できます。
2. **音声入力ソースの切り替え**
   - `🎤+🔊 マイク＋PC内部音声`: Web会議等で自分の声と相手の声の両方を同時に文字起こしします。
   - `🎤 マイクのみ`: 自分のマイク音声のみを文字起こしします。
   - `🔊 PC内部音声のみ`: PC上で再生されている音声（Web会議・動画・配信など）のみを文字起こしします。
3. **前提プロンプト（プリセット選択・編集・保存）**
   - プルダウンから用途に合ったプリセットを選択できます。
   - 選択後、すぐ下のテキスト欄で直接プロンプトを編集・追記できます。
   - `💾 保存`（上書き保存）、`＋ 新規`（新規プリセット登録）、`🗑️`（削除）ボタンで外部ファイル `presets.json` に即座に反映されます（`presets.json` を直接テキストエディタで編集することも可能です）。
4. **AI 要約**
   - `📝 AIで要約する (Alt+S)` ボタンを押すと、文字起こしエリアの内容（および前提プロンプトの文脈）をもとに Azure OpenAI が要点を整理して下段エリアに表示します。
5. **便利なショートカット・操作**
   - `Alt + S`: AI要約を実行
   - `Ctrl + マウスホイール`（または `Ctrl + +/-`）: 文字起こし・要約エリアのフォントサイズを拡大／縮小
   - `📌 常に最前面に固定`: チェックを入れると他ウィンドウ操作中も最前面に表示し続けます
