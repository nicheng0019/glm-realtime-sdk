# Copyright (c) ZhiPu Corporation.
# Licensed under the MIT license.

import argparse
import asyncio
import base64
import os
import signal
from typing import Optional

import pyaudio
from dotenv import load_dotenv
from message_handler import create_message_handler

from rtclient import RTLowLevelClient
from rtclient.models import (
    InputAudioBufferAppendMessage,
    ServerVAD,
    SessionUpdateMessage,
    SessionUpdateParams,
)

shutdown_event: Optional[asyncio.Event] = None
session_ready_event: Optional[asyncio.Event] = None  # 会话准备就绪事件

# 延迟统计
class LatencyStats:
    def __init__(self):
        self.mic_to_send_latencies = []
        self.text_to_audio_latencies = []
        self.last_mic_latency = 0.0
        self.last_text_latency = 0.0

    def add_mic_to_send(self, latency_ms: float):
        self.mic_to_send_latencies.append(latency_ms)
        self.last_mic_latency = latency_ms

    def add_text_to_audio(self, latency_ms: float):
        self.text_to_audio_latencies.append(latency_ms)
        self.last_text_latency = latency_ms

    def get_current_stats(self) -> str:
        """获取当前统计信息的字符串"""
        stats = []
        if self.mic_to_send_latencies:
            avg_mic = sum(self.mic_to_send_latencies) / len(self.mic_to_send_latencies)
            stats.append(f"麦克风→发送: 当前={self.last_mic_latency:.1f}ms 平均={avg_mic:.1f}ms")
        if self.text_to_audio_latencies:
            avg_text = sum(self.text_to_audio_latencies) / len(self.text_to_audio_latencies)
            stats.append(f"文字→音频: 当前={self.last_text_latency:.1f}ms 平均={avg_text:.1f}ms")
        return " | ".join(stats) if stats else "暂无数据"

    def print_stats(self):
        print("\n" + "=" * 70)
        if self.mic_to_send_latencies:
            avg_mic = sum(self.mic_to_send_latencies) / len(self.mic_to_send_latencies)
            min_mic = min(self.mic_to_send_latencies)
            max_mic = max(self.mic_to_send_latencies)
            print(f"📊 麦克风到发送延迟统计 (共 {len(self.mic_to_send_latencies)} 个样本):")
            print(f"   平均: {avg_mic:.2f}ms | 最小: {min_mic:.2f}ms | 最大: {max_mic:.2f}ms")
        else:
            print("📊 麦克风到发送延迟统计: 暂无数据")

        if self.text_to_audio_latencies:
            avg_text = sum(self.text_to_audio_latencies) / len(self.text_to_audio_latencies)
            min_text = min(self.text_to_audio_latencies)
            max_text = max(self.text_to_audio_latencies)
            print(f"📊 文字响应到音频生成延迟统计 (共 {len(self.text_to_audio_latencies)} 个样本):")
            print(f"   平均: {avg_text:.2f}ms | 最小: {min_text:.2f}ms | 最大: {max_text:.2f}ms")
        else:
            print("📊 文字响应到音频生成延迟统计: 暂无数据")
        print("=" * 70 + "\n")

latency_stats = LatencyStats()


def handle_shutdown(sig=None, frame=None):
    """处理关闭信号"""
    if shutdown_event:
        print("\n正在关闭程序...")
        latency_stats.print_stats()
        shutdown_event.set()


async def send_audio_from_mic(client: RTLowLevelClient, enable_mic_playback: bool = False):
    """
    从麦克风实时录音并发送音频：
    DefaultServerVADCfg
    var DefaultVadConfig = VadConfig{
        PositiveSpeechThreshold: 0.85,
        NegativeSpeechThreshold: 0.35,
        RedemptionFrames:        8, // 8x96ms = 768ms
        MinSpeechFrames:         3, // 3x96ms = 288ms
        PreSpeechPadFrames:      1,
        FrameSamples:            1536, // 96ms
        VadInterval:             32 * time.Millisecond,
    }

    Args:
        client: WebSocket客户端
        enable_mic_playback: 是否启用麦克风音频回放（监听自己的声音）
    """
    # 等待会话准备就绪
    if session_ready_event:
        print("⏳ 等待会话配置完成...")
        await session_ready_event.wait()
        print("✅ 会话已就绪，开始录音")

    # 音频参数 (PCM16 格式)
    channels = 1  # 单声道
    sample_width = 2  # 16位 (PCM16)
    frame_rate = 16000  # 16kHz采样率
    packet_ms = 100  # 每包时长（毫秒）- 可调整为 50-100ms
    packet_samples = int(frame_rate * packet_ms / 1000)  # 每包采样点数

    print(f"音频信息: 格式=PCM16, 采样率={frame_rate}Hz, 声道数={channels}, 位深={sample_width*8}位")
    print(f"数据包大小: {packet_ms}ms")
    print("开始从麦克风录音，按 Ctrl+C 停止...")
    print("提示: 说话后停顿 0.5 秒以触发响应")
    if enable_mic_playback:
        print("⚠️  麦克风回放已启用 - 你将听到自己的声音（可能产生回声）")

    p = pyaudio.PyAudio()
    stream = None
    playback_stream = None

    try:
        # 打开麦克风流（输入）
        stream = p.open(
            format=p.get_format_from_width(sample_width),
            channels=channels,
            rate=frame_rate,
            input=True,
            frames_per_buffer=packet_samples,
        )

        # 如果启用回放，打开播放流（输出）
        if enable_mic_playback:
            playback_stream = p.open(
                format=p.get_format_from_width(sample_width),
                channels=channels,
                rate=frame_rate,
                output=True,
                frames_per_buffer=packet_samples,
            )

        # 持续录音并发送
        while not shutdown_event.is_set():
            try:
                # 记录麦克风读取开始时间
                mic_start_time = asyncio.get_event_loop().time() * 1000

                # 从麦克风读取音频数据
                packet_data = stream.read(packet_samples, exception_on_overflow=False)

                # 如果启用回放，直接播放麦克风音频
                if enable_mic_playback and playback_stream:
                    playback_stream.write(packet_data)

                # 直接使用 PCM16 格式（不需要 WAV 封装）
                # packet_data 已经是 PCM16 格式的原始音频数据
                base64_data = base64.b64encode(packet_data).decode("utf-8")
                message = InputAudioBufferAppendMessage(
                    audio=base64_data, client_timestamp=int(asyncio.get_event_loop().time() * 1000)
                )

                await client.send(message)

                # 计算并记录延迟
                send_end_time = asyncio.get_event_loop().time() * 1000
                latency = send_end_time - mic_start_time
                latency_stats.add_mic_to_send(latency)

                # 每100个数据包打印一次统计（约10秒）
                if len(latency_stats.mic_to_send_latencies) % 100 == 0:
                    print(f"\n⏱️  延迟统计: {latency_stats.get_current_stats()}\n")

            except Exception as e:
                if shutdown_event.is_set():
                    break
                print(f"发送失败: {e}")
                break

    except Exception as e:
        print(f"音频处理失败: {e}")
    finally:
        # 清理资源
        if stream:
            stream.stop_stream()
            stream.close()
        if playback_stream:
            playback_stream.stop_stream()
            playback_stream.close()
        p.terminate()
        print("麦克风已关闭")


def get_env_var(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise OSError(f"环境变量 '{var_name}' 未设置或为空。")
    return value


async def with_zhipu(enable_mic_playback: bool = False):
    global shutdown_event, session_ready_event
    shutdown_event = asyncio.Event()
    session_ready_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_shutdown)

    api_key = get_env_var("ZHIPU_API_KEY")
    try:
        async with RTLowLevelClient(
            url="wss://open.bigmodel.cn/api/paas/v4/realtime", headers={"Authorization": f"Bearer {api_key}"}
        ) as client:
            if shutdown_event.is_set():
                return

            # 创建消息处理器（在发送 session 消息之前）
            message_handler = await create_message_handler(
                client,
                shutdown_event,
                enable_audio_playback=True,
                latency_stats=latency_stats,
                session_ready_event=session_ready_event
            )

            # 启动接收任务（先启动接收，才能收到 session.updated）
            receive_task = asyncio.create_task(message_handler.receive_messages())

            # 发送会话配置消息
            session_message = SessionUpdateMessage(
                session=SessionUpdateParams(
                    model="glm-realtime-flash",
                    input_audio_format="pcm",
                    output_audio_format="pcm",
                    modalities={"audio", "text"},
                    turn_detection=ServerVAD(
                        threshold=0.6,              # 语音检测阈值（0.0-1.0）
                        prefix_padding_ms=200,      # 语音前填充 300ms
                        silence_duration_ms=300     # 检测到 500ms 静音后认为说话结束
                    ),
                    input_audio_noise_reduction={
                            "type": "near_field"
                        },
                    temperature=0.01,
                    max_response_output_tokens=512,
                    voice="female-tianmei",
                    beta_fields={"chat_mode": "audio", "tts_source": "e2e", "auto_search": True,"greeting_config": {
                            "enable": True,
                            "content": "你好，我是小智，有什么可以帮助你的吗？"}},
                    tools=[],
                )
            )
            print("📤 发送会话配置...")
            await client.send(session_message)

            if shutdown_event.is_set():
                return

            # 创建发送任务（会等待 session.updated 事件）
            send_task = asyncio.create_task(send_audio_from_mic(client, enable_mic_playback=enable_mic_playback))

            try:
                await asyncio.gather(send_task, receive_task)
            except Exception as e:
                print(f"任务执行出错: {e}")
                for task in [send_task, receive_task]:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        if shutdown_event.is_set():
            print("程序已完成退出")
        latency_stats.print_stats()


if __name__ == "__main__":
    load_dotenv()

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="实时语音对话程序")
    parser.add_argument(
        "--mic-playback",
        action="store_true",
        help="启用麦克风音频回放（监听自己的声音，可能产生回声）"
    )
    args = parser.parse_args()

    print("实时语音对话程序")
    print("使用麦克风进行实时语音输入")
    print("按 Ctrl+C 停止程序")
    if args.mic_playback:
        print("⚠️  麦克风回放已启用")
    print("-" * 50)

    try:
        asyncio.run(with_zhipu(enable_mic_playback=args.mic_playback))
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        latency_stats.print_stats()
    except Exception as e:
        print(f"程序执行出错: {e}")
        latency_stats.print_stats()
    finally:
        print("程序已退出")
