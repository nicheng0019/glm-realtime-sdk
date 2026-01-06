# -- coding: utf-8 --
import os, asyncio, pyaudio, queue, threading
from omni_realtime_client import OmniRealtimeClient, TurnDetectionMode

# 音频播放器类（处理中断）
class AudioPlayer:
    def __init__(self, pyaudio_instance, rate=24000):
        self.stream = pyaudio_instance.open(format=pyaudio.paInt16, channels=1, rate=rate, output=True)
        self.queue = queue.Queue()
        self.stop_evt = threading.Event()
        self.interrupt_evt = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                data = self.queue.get(timeout=0.5)
                if data is None: break
                if not self.interrupt_evt.is_set(): self.stream.write(data)
                self.queue.task_done()
            except queue.Empty: continue

    def add_audio(self, data): self.queue.put(data)
    def handle_interrupt(self): self.interrupt_evt.set(); self.queue.queue.clear()
    def stop(self): self.stop_evt.set(); self.queue.put(None); self.stream.stop_stream(); self.stream.close()

# 麦克风录音并发送
async def record_and_send(client):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=3200)
    print("开始录音，请讲话...")
    try:
        while True:
            audio_data = stream.read(3200)
            await client.stream_audio(audio_data)
            await asyncio.sleep(0.02)
    finally:
        stream.stop_stream(); stream.close(); p.terminate()

async def main():
    p = pyaudio.PyAudio()
    player = AudioPlayer(pyaudio_instance=p)

    client = OmniRealtimeClient(
        # 以下是中国内地（北京）地域 base_url，国际（新加坡）地域base_url为wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model="qwen3-omni-flash-realtime",
        voice="Cherry",
        instructions="你是小云，风趣幽默的好助手",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        on_text_delta=lambda t: print(f"\nAssistant: {t}", end="", flush=True),
        on_audio_delta=player.add_audio,
    )

    await client.connect()
    print("连接成功，开始实时对话...")

    # 并发运行
    await asyncio.gather(client.handle_messages(), record_and_send(client))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出。")