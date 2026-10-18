"""
audio_recorder.py
PyAudioWPatch を用いた非ブロッキング録音・一時停止・リアルタイムチャンク分割スレッド。
マイク入力・PC内部音声（WASAPI Loopback）・両方ミックスの3モードに対応する。
録音中も他のマウス操作・キーボード操作を一切妨げないバックグラウンド設計。
"""

import io
import time
import wave
import threading
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

import config

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False


TARGET_RATE = 16000
TARGET_CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM (2 bytes)


def _resample_to_16k_mono(raw_bytes: bytes, src_rate: int, src_channels: int) -> np.ndarray:
    """
    任意のサンプルレート・チャンネル数の16bit PCMバイト列を
    16kHz モノラル int16 の numpy 配列に変換する。
    """
    if not raw_bytes:
        return np.array([], dtype=np.int16)

    samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    if src_channels > 1:
        # 端数がある場合は切り捨ててチャンネルごとに平均化
        valid_len = (len(samples) // src_channels) * src_channels
        samples = samples[:valid_len].reshape(-1, src_channels).mean(axis=1)

    if src_rate != TARGET_RATE and len(samples) > 0:
        duration = len(samples) / float(src_rate)
        target_len = max(1, int(round(duration * TARGET_RATE)))
        x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        samples = np.interp(x_new, x_old, samples)

    return np.clip(samples, -32768, 32767).astype(np.int16)


def _pcm_to_wav_bytes(pcm_samples: np.ndarray) -> bytes:
    """int16 16kHz モノラル配列をインメモリWAVバイト列に変換する。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(TARGET_CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm_samples.tobytes())
    return buf.getvalue()


class AudioRecorderThread(QThread):
    """
    バックグラウンドで音声をキャプチャし、無音区切りまたは一定秒数ごとに
    WAVデータ (bytes) をシグナルで送出する QThread。

    シグナル:
        chunk_ready(bytes, float) : (WAVバイト列, 音声秒数)
        level_changed(int)        : 現在の音量レベル (0〜100)
        status_message(str)       : ステータスメッセージ
        error_occurred(str)       : エラー発生時のメッセージ
    """

    chunk_ready = pyqtSignal(bytes, float)
    level_changed = pyqtSignal(int)
    status_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    # 入力ソース定数
    SOURCE_MIC = "mic"
    SOURCE_LOOPBACK = "loopback"
    SOURCE_BOTH = "both"

    def __init__(self, source_mode: str = SOURCE_MIC, parent=None):
        super().__init__(parent)
        self.source_mode = source_mode
        self._running = False
        self._paused = False
        self._lock = threading.Lock()

        # コールバックから受け取る生PCMバイト列バッファ (raw_bytes, rate, channels)
        self._mic_raw: list[bytes] = []
        self._mic_meta: tuple[int, int] = (TARGET_RATE, 1)
        self._loop_raw: list[bytes] = []
        self._loop_meta: tuple[int, int] = (48000, 2)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool):
        """一時停止 / 再開を切り替える。"""
        with self._lock:
            self._paused = paused

    def stop_recording(self):
        """録音を終了する。"""
        with self._lock:
            self._running = False

    def _open_mic_stream(self, p: "pyaudio.PyAudio"):
        """デフォルトマイクのストリームを非ブロッキングコールバックで開く。"""
        mic_info = p.get_default_input_device_info()
        rate = int(mic_info.get("defaultSampleRate", TARGET_RATE))
        channels = max(1, min(2, int(mic_info.get("maxInputChannels", 1))))
        self._mic_meta = (rate, channels)

        def _mic_cb(in_data, frame_count, time_info, status):
            if self._running and not self._paused and in_data:
                with self._lock:
                    self._mic_raw.append(in_data)
            return (None, pyaudio.paContinue)

        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=int(rate * 0.1),
            stream_callback=_mic_cb,
        )
        stream.start_stream()
        return stream, mic_info.get("name", "Microphone")

    def _open_loopback_stream(self, p: "pyaudio.PyAudio"):
        """WASAPI Loopback (PC内部音声) のストリームを非ブロッキングコールバックで開く。"""
        loop_info = p.get_default_wasapi_loopback()
        rate = int(loop_info.get("defaultSampleRate", 48000))
        channels = max(1, int(loop_info.get("maxInputChannels", 2)))
        self._loop_meta = (rate, channels)

        def _loop_cb(in_data, frame_count, time_info, status):
            if self._running and not self._paused and in_data:
                with self._lock:
                    self._loop_raw.append(in_data)
            return (None, pyaudio.paContinue)

        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=loop_info["index"],
            frames_per_buffer=int(rate * 0.1),
            stream_callback=_loop_cb,
        )
        stream.start_stream()
        return stream, loop_info.get("name", "PC System Audio")

    def _pop_mixed_samples(self) -> np.ndarray:
        """溜まったマイク・ループバックの生データを取り出し、ワーカースレッド側で16kHz変換・合成する。"""
        with self._lock:
            mic_bytes = b"".join(self._mic_raw)
            loop_bytes = b"".join(self._loop_raw)
            self._mic_raw.clear()
            self._loop_raw.clear()

        mic_rate, mic_ch = self._mic_meta
        loop_rate, loop_ch = self._loop_meta

        mic_arr = _resample_to_16k_mono(mic_bytes, mic_rate, mic_ch) if mic_bytes else np.array([], dtype=np.int16)
        loop_arr = _resample_to_16k_mono(loop_bytes, loop_rate, loop_ch) if loop_bytes else np.array([], dtype=np.int16)

        if len(mic_arr) > 0 and len(loop_arr) > 0:
            max_len = max(len(mic_arr), len(loop_arr))
            m_pad = np.pad(mic_arr.astype(np.float32), (0, max_len - len(mic_arr)))
            l_pad = np.pad(loop_arr.astype(np.float32), (0, max_len - len(loop_arr)))
            # PC内部音声が大きすぎてマイク音声をかき消すのを防ぐためループバック側を0.75倍でミックス
            mixed = np.clip(m_pad + l_pad * 0.75, -32768, 32767).astype(np.int16)
            return mixed
        elif len(mic_arr) > 0:
            return mic_arr
        elif len(loop_arr) > 0:
            return loop_arr
        return np.array([], dtype=np.int16)

    def run(self):
        self._running = True
        self._paused = False

        if not HAS_PYAUDIO:
            self.error_occurred.emit("PyAudioWPatch がインストールされていません。")
            return

        p = pyaudio.PyAudio()
        streams = []
        device_names = []

        try:
            # 入力ソースに応じてストリームをオープン
            if self.source_mode in (self.SOURCE_MIC, self.SOURCE_BOTH):
                try:
                    s_mic, name_mic = self._open_mic_stream(p)
                    streams.append(s_mic)
                    device_names.append(f"🎤 {name_mic}")
                except Exception as e:
                    if self.source_mode == self.SOURCE_MIC:
                        raise RuntimeError(f"マイクの初期化に失敗しました: {e}")
                    print(f"[TranscribAI] マイクオープン警告: {e}")

            if self.source_mode in (self.SOURCE_LOOPBACK, self.SOURCE_BOTH):
                try:
                    s_loop, name_loop = self._open_loopback_stream(p)
                    streams.append(s_loop)
                    device_names.append(f"🔊 {name_loop}")
                except Exception as e:
                    if self.source_mode == self.SOURCE_LOOPBACK:
                        raise RuntimeError(f"PC内部音声(WASAPI Loopback)の初期化に失敗しました: {e}")
                    print(f"[TranscribAI] ループバックオープン警告: {e}")

            if not streams:
                raise RuntimeError("有効な音声入力デバイスが見つかりませんでした。")

            self.status_message.emit(f"録音中 ({' + '.join(device_names)})")

            # チャンク切り出し用バッファ
            accumulated = np.array([], dtype=np.int16)
            has_speech_in_chunk = False
            silence_elapsed = 0.0
            was_paused = False

            max_chunk_sec = max(2.0, float(getattr(config, "AUDIO_CHUNK_SECONDS", 5.0)))
            silence_limit_sec = max(0.4, float(getattr(config, "SILENCE_DURATION_SECONDS", 0.8)))
            min_speech_sec = 0.5  # 短すぎるノイズは無視
            rms_threshold = 120.0  # 発話判定しきい値 (int16スケール: 0〜32768)

            last_tick = time.monotonic()
            idle_elapsed = 0.0

            while self._running:
                time.sleep(0.08)
                now = time.monotonic()
                dt = now - last_tick
                last_tick = now

                if self._paused:
                    # 一時停止に入った瞬間に未送信の発話があればフラッシュする
                    if not was_paused and has_speech_in_chunk and len(accumulated) >= int(TARGET_RATE * min_speech_sec):
                        dur = len(accumulated) / float(TARGET_RATE)
                        wav_bytes = _pcm_to_wav_bytes(accumulated)
                        self.chunk_ready.emit(wav_bytes, dur)
                        accumulated = np.array([], dtype=np.int16)
                        has_speech_in_chunk = False
                        silence_elapsed = 0.0
                        idle_elapsed = 0.0

                    # キューを空にしてレベル0を通知
                    self._pop_mixed_samples()
                    self.level_changed.emit(0)
                    was_paused = True
                    continue

                was_paused = False
                new_samples = self._pop_mixed_samples()
                idle_elapsed += dt

                if len(new_samples) == 0:
                    # PC内部音声が無音でコールバックが来ない場合も無音時間を加算
                    self.level_changed.emit(0)
                    if has_speech_in_chunk:
                        silence_elapsed += dt
                else:
                    rms = float(np.sqrt(np.mean(new_samples.astype(np.float32) ** 2)))
                    # UIメーター用に 0〜100 にスケーリング
                    level = min(100, int((rms / 3000.0) * 100))
                    self.level_changed.emit(level)

                    if rms >= rms_threshold:
                        has_speech_in_chunk = True
                        silence_elapsed = 0.0
                        idle_elapsed = 0.0
                        accumulated = np.concatenate([accumulated, new_samples])
                    else:
                        if has_speech_in_chunk:
                            silence_elapsed += dt
                            accumulated = np.concatenate([accumulated, new_samples])
                        else:
                            # 発話開始前は直近0.3秒だけプリロールとして保持（語頭切れ防止）
                            preroll_len = int(TARGET_RATE * 0.3)
                            combined = np.concatenate([accumulated, new_samples])
                            accumulated = combined[-preroll_len:] if len(combined) > preroll_len else combined

                current_dur = len(accumulated) / float(TARGET_RATE)

                should_flush = False
                if has_speech_in_chunk:
                    if current_dur >= max_chunk_sec:
                        should_flush = True
                    elif silence_elapsed >= silence_limit_sec and current_dur >= min_speech_sec:
                        should_flush = True
                elif config.USE_DUMMY_API and idle_elapsed >= max_chunk_sec:
                    # ダミーモード時は無音環境でのGUI動作テストでも一定間隔でダミー文字起こしを発火
                    dummy_samples = np.zeros(int(TARGET_RATE * max_chunk_sec), dtype=np.int16)
                    wav_bytes = _pcm_to_wav_bytes(dummy_samples)
                    self.chunk_ready.emit(wav_bytes, max_chunk_sec)
                    idle_elapsed = 0.0

                if should_flush:
                    wav_bytes = _pcm_to_wav_bytes(accumulated)
                    self.chunk_ready.emit(wav_bytes, current_dur)
                    accumulated = np.array([], dtype=np.int16)
                    has_speech_in_chunk = False
                    silence_elapsed = 0.0
                    idle_elapsed = 0.0

            # 録音停止時に残っている発話バッファをフラッシュ
            if has_speech_in_chunk and len(accumulated) >= int(TARGET_RATE * 0.4):
                dur = len(accumulated) / float(TARGET_RATE)
                wav_bytes = _pcm_to_wav_bytes(accumulated)
                self.chunk_ready.emit(wav_bytes, dur)

            self.level_changed.emit(0)

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            for s in streams:
                try:
                    s.stop_stream()
                    s.close()
                except Exception:
                    pass
            p.terminate()
