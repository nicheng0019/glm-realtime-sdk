"""
音频播放示例
演示如何使用MessageHandler的音频播放功能
实时播放从智谱AI实时API接收到的音频响应
"""

import asyncio
import base64
import os
import signal
import sys
import wave
from io import BytesIO

from dotenv import load_dotenv
from message_handler import create_message_handler
from rtclient import RTLowLevelClient
from rtclient.models import (
    InputAudioBufferAppendMessage,
    ResponseCreateMessage,
    SessionUpdateMessage,
)

shutdown_event = None


def handle_shutdown(signum, frame):
    """处理关闭信号"""
    print("\n收到关闭信号，正在退出...")
    if shutdown_event:
        shutdown_event.set()


def encode_wave_to_base64(wave_file_path):
    """将WAV文件转换为base64编码"""
    try:
        with open(wave_file_path, "rb") as audio_file:
            with wave.open(audio_file, "rb") as wave_in:
                # 读取WAV文件参数
                channels = wave_in.getnchannels()
                sample_width = wave_in.getsampwidth()
                frame_rate = wave_in.getframerate()
                frames = wave_in.readframes(wave_in.getnframes())

            # 创建字节流并写入标准WAV格式
            wave_io = BytesIO()
            with wave.open(wave_io, "wb") as wave_out:
                wave_out.setnchannels(channels)
                wave_out.setsampwidth(sample_width)
                wave_out.setframerate(frame_rate)
                wave_out.writeframes(frames)

            wave_io.seek(0)
            print(f"音频参数: 声道数={channels}, 位深度={sample_width*8}位, 采样率={frame_rate}Hz")
            return base64.b64encode(wave_io.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"音频文件处理错误: {str(e)}")
        return None


async def send_audio(client: RTLowLevelClient, audio_file_path: str):
    """发送音频"""
    base64_content = encode_wave_to_base64(audio_file_path)
    if base64_content is None:
        print("音频编码失败")
        return

    if len(base64_content) == 0:
        print("音频数据为空")
        return

    # 发送音频数据
    audio_message = InputAudioBufferAppendMessage(
        audio=base64_content, client_timestamp=int(asyncio.get_event_loop().time() * 1000)
    )
    await client.send(audio_message)


def get_env_var(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise OSError(f"环境变量 '{var_name}' 未设置或为空。")
    return value


async def with_zhipu(audio_file_path: str):
    global shutdown_event
    shutdown_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_shutdown)

    api_key = get_env_var("ZHIPUAI_API_KEY")
    url = "wss://open.bigmodel.cn/api/paas/v4/realtime"

    print("正在连接到智谱AI实时API...")
    async with RTLowLevelClient(url, api_key) as client:
        print("连接成功！")

        # 配置会话参数
        session_update = SessionUpdateMessage(
            session={
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": None,
            }
        )
        await client.send(session_update)

        # 创建消息处理器，启用音频播放功能
        # PCM格式: 采样率24kHz, 单声道, 16位深
        handler = await create_message_handler(
            client,
            shutdown_event,
            enable_audio_playback=True,  # 启用音频播放
            audio_sample_rate=24000,  # 24kHz采样率
            audio_channels=1,  # 单声道
            audio_sample_width=2,  # 16位 = 2字节
        )

        # 启动消息接收任务
        receive_task = asyncio.create_task(handler.receive_messages())

        # 等待一下确保会话建立
        await asyncio.sleep(1)

        # 发送音频文件
        print(f"正在发送音频文件: {audio_file_path}")
        await send_audio(client, audio_file_path)

        # 提交音频并请求响应
        await client.send_json({"type": "input_audio_buffer.commit"})
        await client.send(ResponseCreateMessage())

        print("音频已发送，等待响应...")
        print("音频响应将自动通过扬声器播放")
        print("按 Ctrl+C 退出")

        # 等待接收任务完成或关闭信号
        await receive_task


if __name__ == "__main__":
    load_dotenv()
    if len(sys.argv) < 2:
        print("使用方法: python low_level_sample_audio_playback.py <音频文件>")
        print("示例: python low_level_sample_audio_playback.py audio.wav")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"音频文件 {file_path} 不存在")
        sys.exit(1)

    try:
        asyncio.run(with_zhipu(file_path))
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"程序执行出错: {e}")
    finally:
        print("程序已退出")

