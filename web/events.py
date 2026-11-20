# -*- coding: utf-8 -*-
"""事件总线与日志桥接。

引擎通过 :class:`BusProgress` 把进度写进 :class:`EventBus`，
每个 SSE 连接对应一个队列；日志通过 loguru 的 sink 一并进入总线。
"""
from __future__ import annotations

import queue
import threading
from collections import deque
from typing import Any

from api.logger import logger
from api.progress import ProgressSink


class EventBus:
    """线程安全的发布/订阅总线，带有限历史以便新连接补发状态。"""

    def __init__(self, history_size: int = 400) -> None:
        """初始化总线。

        Args:
            history_size: 保留的历史事件条数。
        """
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._history: deque[tuple[str, dict[str, Any]]] = deque(maxlen=history_size)

    def subscribe(self) -> queue.Queue:
        """注册一个订阅者并返回其队列。"""
        q: queue.Queue = queue.Queue(maxsize=10000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """注销订阅者。"""
        with self._lock:
            self._subscribers.discard(q)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """广播一个事件（永不抛异常）。"""
        payload = (event, data)
        self._history.append(payload)
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                continue

    def history(self) -> list[tuple[str, dict[str, Any]]]:
        """返回历史事件快照。"""
        return list(self._history)

    def clear_history(self) -> None:
        """清空历史（开始新任务时调用，避免旧日志串场）。"""
        self._history.clear()


class BusProgress(ProgressSink):
    """把引擎进度事件转发到总线。"""

    def __init__(self, bus: EventBus) -> None:
        """初始化。

        Args:
            bus: 目标事件总线。
        """
        self._bus = bus

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """转发事件。"""
        self._bus.emit(event, data)


class _LogSink:
    """loguru sink：把日志行转成 ``log`` 事件。"""

    def __init__(self, bus: EventBus) -> None:
        """初始化。

        Args:
            bus: 目标事件总线。
        """
        self._bus = bus

    def __call__(self, message) -> None:
        """loguru 回调入口。"""
        try:
            record = message.record
            self._bus.emit("log", {
                "level": record["level"].name,
                "time": record["time"].strftime("%H:%M:%S"),
                "msg": record["message"],
            })
        except Exception:  # noqa: BLE001 - 日志上报失败不允许影响业务
            pass


def attach_log_sink(bus: EventBus) -> int:
    """把总线挂到全局 loguru logger 上。

    Args:
        bus: 目标事件总线。

    Returns:
        loguru handler id，便于测试时移除。
    """
    return logger.add(_LogSink(bus), format="{message}", level="INFO", enqueue=False)
