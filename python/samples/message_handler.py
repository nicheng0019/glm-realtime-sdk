"""
统一的消息处理模块
用于处理与智谱AI实时音视频API的WebSocket通信消息
支持音频、视频、函数调用和服务端VAD等多种场景
"""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, Optional

from rtclient import RTLowLevelClient
from rtclient.models import (
    FunctionCallOutputItem,
    ItemCreateMessage,
)

try:
    from audio_player import AudioPlayer
except ImportError:
    try:
        from samples.audio_player import AudioPlayer
    except ImportError:
        AudioPlayer = None
        print("警告: 无法导入AudioPlayer，音频播放功能将不可用")


class MessageHandler:
    def __init__(
        self,
        client: RTLowLevelClient,
        shutdown_event: Optional[asyncio.Event] = None,
        enable_audio_playback: bool = False,
        audio_sample_rate: int = 24000,
        audio_channels: int = 1,
        audio_sample_width: int = 2,
        latency_stats=None,
        session_ready_event: Optional[asyncio.Event] = None,
    ):
        """
        初始化消息处理器
        Args:
            client: WebSocket客户端实例
            shutdown_event: 关闭事件，用于控制消息接收的终止
            enable_audio_playback: 是否启用音频播放功能
            audio_sample_rate: 音频采样率 (默认24000Hz)
            audio_channels: 音频声道数 (默认1=单声道)
            audio_sample_width: 音频采样位深字节数 (默认2=16位)
            latency_stats: 延迟统计对象
            session_ready_event: 会话准备就绪事件
        """
        self.client = client
        self.shutdown_event = shutdown_event
        self._custom_handlers: dict[str, Callable] = {}
        self.latency_stats = latency_stats
        self.session_ready_event = session_ready_event

        # 用于跟踪文字响应到音频的时间
        self.text_response_time: Optional[float] = None
        self.first_audio_delta_time: Optional[float] = None

        # 音频播放器
        self.audio_player: Optional[AudioPlayer] = None
        if enable_audio_playback and AudioPlayer is not None:
            try:
                self.audio_player = AudioPlayer(
                    sample_rate=audio_sample_rate,
                    channels=audio_channels,
                    sample_width=audio_sample_width,
                )
                print("音频播放功能已启用")
            except Exception as e:
                print(f"初始化音频播放器失败: {e}")
                self.audio_player = None

    def register_handler(self, message_type: str, handler: Callable):
        """
        注册自定义消息处理器
        Args:
            message_type: 消息类型
            handler: 处理函数，接收message参数
        """
        self._custom_handlers[message_type] = handler

    async def _handle_function_call(self, message: Any):
        """处理函数调用消息"""
        print("函数调用参数完成消息")
        if isinstance(message, dict):
            response_id = message.get("response_id", "Unknown")
            name = message.get("name")
            arguments = message.get("arguments")
        else:
            response_id = message.response_id if hasattr(message, "response_id") else "Unknown"
            name = message.name if hasattr(message, "name") else None
            arguments = message.arguments if hasattr(message, "arguments") else None
        print(f"  Response Id: {response_id}")
        if name:
            print(f"  Function Name: {name}")
        print(f"  Arguments: {arguments if arguments else 'None'}")

        # 如果是电话功能的函数调用，处理响应
        if name == "phoneCall":
            try:
                args = json.loads(arguments) if arguments else {}
                response = {"status": "success", "message": f"成功拨打电话给 {args.get('contact_name', '未知姓名')}"}
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")

    async def _handle_session_messages(self, message: Any, msg_type: str):
        """处理会话相关消息"""
        match msg_type:
            case "session.created":
                print("会话创建消息")
                if isinstance(message, dict):
                    print(f"  Session Id: {message.get('session', {}).get('id', 'Unknown')}")
                elif hasattr(message, "session"):
                    print(f"  Session Id: {message.session.id}")
            case "session.updated":
                print("✅ 会话更新消息 - 配置已完成")
                if isinstance(message, dict):
                    print(f"updated session: {message.get('session', {})}")
                elif hasattr(message, "session"):
                    print(f"updated session: {message.session}")

                # 设置会话准备就绪事件
                if self.session_ready_event:
                    self.session_ready_event.set()
                    print("🚀 会话已就绪，可以开始发送音频")

            case "error":
                print("错误消息")
                if isinstance(message, dict):
                    print(f"  Error: {message.get('error', 'Unknown error')}")
                elif hasattr(message, "error"):
                    print(f"  Error: {message.error}")

    async def _handle_audio_input_messages(self, message: Any, msg_type: str):
        """处理音频输入相关消息"""
        match msg_type:
            case "input_audio_buffer.committed":
                print(f"[{time.strftime('%H:%M:%S')}] 音频缓冲区提交消息")
                if hasattr(message, "item_id"):
                    print(f"  Item Id: {message.item_id}")
            case "input_audio_buffer.speech_started":
                print(f"[{time.strftime('%H:%M:%S')}] ✓ 检测到语音开始")
            case "input_audio_buffer.speech_stopped":
                print(f"[{time.strftime('%H:%M:%S')}] ✓ 检测到语音结束，等待响应...")

    async def _handle_conversation_messages(self, message: Any, msg_type: str):
        """处理会话项目相关消息"""
        match msg_type:
            case "conversation.item.created":
                print("会话项目创建消息")
            case "conversation.item.input_audio_transcription.completed":
                print(f"[{time.strftime('%H:%M:%S')}] 输入音频转写完成消息")
                if isinstance(message, dict):
                    print(f"  Transcript: {message.get('transcript', 'N/A')}")
                elif hasattr(message, "transcript"):
                    print(f"  Transcript: {message.transcript}")

    async def _handle_response_messages(self, message: Any, msg_type: str):
        """处理响应相关消息"""
        match msg_type:
            case "response.created":
                print(f"[{time.strftime('%H:%M:%S')}] ✓ 响应创建")
                if isinstance(message, dict):
                    print(f"  Response Id: {message.get('response', {}).get('id', 'Unknown')}")
                elif hasattr(message, "response"):
                    print(f"  Response Id: {message.response.id}")
                # 重置计时器
                self.text_response_time = None
                self.first_audio_delta_time = None
            case "response.done":
                print(f"[{time.strftime('%H:%M:%S')}] ✓ 响应完成")
                if isinstance(message, dict):
                    response = message.get("response", {})
                    print(f"  Response Id: {response.get('id', 'Unknown')}")
                    print(f"  Status: {response.get('status', 'Unknown')}")
                elif hasattr(message, "response"):
                    print(f"  Response Id: {message.response.id}")
                    print(f"  Status: {message.response.status}")
            case "response.audio.delta":
                print(f"[{time.strftime('%H:%M:%S')}] 模型音频增量消息")
                # 记录第一个音频数据包的时间
                if self.first_audio_delta_time is None and self.text_response_time is not None:
                    self.first_audio_delta_time = time.time() * 1000
                    latency = self.first_audio_delta_time - self.text_response_time
                    if self.latency_stats:
                        self.latency_stats.add_text_to_audio(latency)
                    print(f"  ⏱️  文字到音频延迟: {latency:.2f}ms")

                # 不打印每个音频增量，避免刷屏
                delta = None
                if isinstance(message, dict):
                    delta = message.get('delta')
                else:
                    if hasattr(message, 'delta') and message.delta:
                        delta = message.delta

                # 播放音频数据
                if delta and self.audio_player:
                    try:
                        self.audio_player.play_base64(delta)
                    except Exception as e:
                        print(f"播放音频失败: {e}")
            case "response.audio.done":
                print(f"[{time.strftime('%H:%M:%S')}] ✓ 音频播放完成")
            case "response.audio_transcript.delta":
                # 记录文字响应时间
                if self.text_response_time is None:
                    self.text_response_time = time.time() * 1000

                print(f"[{time.strftime('%H:%M:%S')}] 模型音频文本增量消息")
                if isinstance(message, dict):
                    print(f"  Response Id: {message.get('response_id', 'Unknown')}")
                    print(f"  Delta: {message.get('delta', 'None')}")
                else:
                    print(f"  Response Id: {message.response_id if hasattr(message, 'response_id') else 'Unknown'}")
                    print(f"  Delta: {message.delta if hasattr(message, 'delta') and message.delta else 'None'}")
            case "response.audio_transcript.done":
                print("模型音频文本完成消息")

    async def receive_messages(self):
        """
        统一的消息接收处理函数
        处理所有类型的消息，包括音频、视频、函数调用和服务端VAD等场景
        """
        try:
            while not self.client.closed:
                if self.shutdown_event and self.shutdown_event.is_set():
                    print("正在停止消息接收...")
                    break

                try:
                    message = await asyncio.wait_for(self.client.recv(), timeout=5.0)
                    if message is None:
                        continue

                    msg_type = message.type if hasattr(message, "type") else message.get("type")
                    if msg_type is None:
                        print("收到未知类型的消息:", message)
                        continue

                    # 检查是否有自定义处理器
                    if msg_type in self._custom_handlers:
                        await self._custom_handlers[msg_type](message)
                        continue

                    if msg_type.startswith("session.") or msg_type == "error":
                        await self._handle_session_messages(message, msg_type)
                    elif msg_type.startswith("input_audio_buffer."):
                        await self._handle_audio_input_messages(message, msg_type)
                    elif msg_type.startswith("conversation."):
                        await self._handle_conversation_messages(message, msg_type)
                    elif msg_type.startswith("response."):
                        if msg_type == "response.function_call_arguments.done":
                            await self._handle_function_call(message)
                        else:
                            await self._handle_response_messages(message, msg_type)
                    elif msg_type == "heartbeat":
                        print("心跳消息")
                    else:
                        print(f"未处理的消息类型: {msg_type}")
                        print(message)

                except TimeoutError:
                    continue  # 超时后继续尝试接收
                except Exception as e:
                    if not self.shutdown_event or not self.shutdown_event.is_set():
                        print(f"接收消息时发生错误: {e}")
                    break
        finally:
            # 停止音频播放器
            if self.audio_player:
                self.audio_player.stop()

            if not self.client.closed:
                await self.client.close()
                print("WebSocket连接已关闭")


async def create_message_handler(
    client: RTLowLevelClient,
    shutdown_event: Optional[asyncio.Event] = None,
    enable_audio_playback: bool = False,
    audio_sample_rate: int = 24000,
    audio_channels: int = 1,
    audio_sample_width: int = 2,
    latency_stats=None,
    session_ready_event: Optional[asyncio.Event] = None,
) -> MessageHandler:
    """
    创建消息处理器实例
    Args:
        client: WebSocket客户端实例
        shutdown_event: 关闭事件
        enable_audio_playback: 是否启用音频播放功能
        audio_sample_rate: 音频采样率 (默认24000Hz)
        audio_channels: 音频声道数 (默认1=单声道)
        audio_sample_width: 音频采样位深字节数 (默认2=16位)
        latency_stats: 延迟统计对象
        session_ready_event: 会话准备就绪事件
    Returns:
        MessageHandler实例
    """
    return MessageHandler(
        client,
        shutdown_event,
        enable_audio_playback=enable_audio_playback,
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        audio_sample_width=audio_sample_width,
        latency_stats=latency_stats,
        session_ready_event=session_ready_event,
    )
