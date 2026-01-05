"""
音频播放器模块
用于实时播放PCM音频数据
"""

import base64
import queue
import threading
from typing import Optional

try:
    import pyaudio
except ImportError:
    pyaudio = None
    print("警告: pyaudio未安装，音频播放功能将不可用")
    print("请运行: pip install pyaudio")


class AudioPlayer:
    """
    实时音频播放器
    支持PCM格式音频流式播放
    """

    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,  # 16位 = 2字节
        chunk_size: int = 1024,
    ):
        """
        初始化音频播放器
        Args:
            sample_rate: 采样率 (Hz)
            channels: 声道数 (1=单声道, 2=立体声)
            sample_width: 采样位深 (字节数, 2=16位)
            chunk_size: 每次播放的数据块大小
        """
        if pyaudio is None:
            raise ImportError("pyaudio未安装，无法使用音频播放功能")

        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.chunk_size = chunk_size

        self.audio_queue: queue.Queue = queue.Queue()
        self.is_playing = False
        self.play_thread: Optional[threading.Thread] = None
        self.pyaudio_instance: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None

    def start(self):
        """启动音频播放器"""
        if self.is_playing:
            return

        self.is_playing = True
        self.pyaudio_instance = pyaudio.PyAudio()

        # 打开音频流
        self.stream = self.pyaudio_instance.open(
            format=self.pyaudio_instance.get_format_from_width(self.sample_width),
            channels=self.channels,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=self.chunk_size,
        )

        # 启动播放线程
        self.play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self.play_thread.start()
        print(f"音频播放器已启动 (采样率: {self.sample_rate}Hz, 声道: {self.channels}, 位深: {self.sample_width*8}位)")

    def _play_loop(self):
        """播放循环，在独立线程中运行"""
        while self.is_playing:
            try:
                # 从队列获取音频数据，超时1秒
                audio_data = self.audio_queue.get(timeout=1.0)
                if audio_data is None:  # None作为停止信号
                    break

                # 播放音频数据
                if self.stream and self.stream.is_active():
                    self.stream.write(audio_data)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"播放音频时出错: {e}")

    def play_base64(self, base64_audio: str):
        """
        播放base64编码的PCM音频数据
        Args:
            base64_audio: base64编码的PCM音频数据
        """
        if not base64_audio:
            return

        try:
            # 解码base64数据
            audio_bytes = base64.b64decode(base64_audio)
            self.play_bytes(audio_bytes)
        except Exception as e:
            print(f"解码音频数据失败: {e}")

    def play_bytes(self, audio_bytes: bytes):
        """
        播放原始PCM字节数据
        Args:
            audio_bytes: PCM音频字节数据
        """
        if not self.is_playing:
            self.start()

        # 将音频数据加入播放队列
        self.audio_queue.put(audio_bytes)

    def stop(self):
        """停止音频播放器"""
        if not self.is_playing:
            return

        self.is_playing = False

        # 发送停止信号
        self.audio_queue.put(None)

        # 等待播放线程结束
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=2.0)

        # 关闭音频流
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

        # 关闭PyAudio实例
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None

        # 清空队列
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        print("音频播放器已停止")

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.stop()

