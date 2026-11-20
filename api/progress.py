# -*- coding: utf-8 -*-
"""结构化进度上报与运行控制。

CLI 默认不关心进度（继续用 tqdm 画进度条），Web 控制台订阅 ``ProgressSink``
后即可实时渲染进度与日志。两者共用同一套执行器，行为保持一致。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class ProgressSink:
    """进度接收器基类，所有方法默认空实现。"""

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """上报一个进度事件。

        Args:
            event: 事件名，如 ``job`` / ``course`` / ``chapter`` / ``progress`` / ``video`` / ``log``。
            data: 事件负载，必须可 JSON 序列化。
        """


class JobStopped(Exception):
    """任务被手动停止：用于从视频等长耗时任务中立刻中断。"""


class NullProgress(ProgressSink):
    """空接收器：CLI 与测试默认使用。"""


class CallbackProgress(ProgressSink):
    """把事件转交给回调函数，便于测试或自定义集成。"""

    def __init__(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """初始化回调接收器。

        Args:
            callback: 形如 ``callback(event, data)`` 的函数。
        """
        self._callback = callback

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """把事件原样交给回调。"""
        self._callback(event, data)


class RunControl:
    """运行控制：暂停、继续、停止。所有状态变更线程安全。"""

    def __init__(self) -> None:
        """初始化控制标志。"""
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        # 运行中可动态调整的视频倍速（None 表示沿用启动时的设置）
        self.speed: Optional[float] = None

    @property
    def stopped(self) -> bool:
        """是否已请求停止。"""
        return self.stop_event.is_set()

    @property
    def paused(self) -> bool:
        """是否处于暂停状态。"""
        return self.pause_event.is_set()

    def stop(self) -> None:
        """请求停止：会立即解除暂停并置停止标志。"""
        self.pause_event.clear()
        self.stop_event.set()

    def pause(self) -> None:
        """请求暂停（当前任务点执行完后生效）。"""
        if not self.stopped:
            self.pause_event.set()

    def resume(self) -> None:
        """解除暂停。"""
        self.pause_event.clear()

    def set_speed(self, value: float) -> float:
        """设置运行中使用的视频倍速（引擎会在下一个任务点生效）。

        Args:
            value: 倍速，会被夹在 1.0–2.0。

        Returns:
            实际生效的倍速。
        """
        try:
            speed = float(value)
        except (TypeError, ValueError):
            speed = 1.0
        self.speed = min(2.0, max(1.0, speed))
        return self.speed

    def wait_if_paused(self, sink: Optional[ProgressSink] = None) -> bool:
        """若处于暂停状态则阻塞，直到恢复或停止。

        Args:
            sink: 用于上报 state 事件的进度接收器。

        Returns:
            是否曾经进入过暂停等待。
        """
        if not self.pause_event.is_set():
            return False
        if sink is not None:
            _safe_emit(sink, "state", {"state": "paused"})
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.2)
        if sink is not None and not self.stop_event.is_set():
            _safe_emit(sink, "state", {"state": "running"})
        return True


def _safe_emit(sink: ProgressSink, event: str, data: dict[str, Any]) -> None:
    """容错上报：接收器异常不得影响主流程。"""
    try:
        sink.emit(event, data)
    except Exception:  # noqa: BLE001 - 进度上报不允许中断学习任务
        pass
