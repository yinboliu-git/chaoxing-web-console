# -*- coding: utf-8 -*-
"""Web 控制台运行时：登录态、设置、当前任务。

本项目是**本机单用户**工具：超星的 ``SessionManager`` 是进程级单例，
``cookies.txt`` 也是全局文件，因此同一时刻只允许一个账号、一个任务。
本模块用一把锁把这条约束显式化。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Optional

from api.answer import Tiku
from api.base import Account, Chaoxing
from api.config_store import (
    apply_answer_mode,
    answer_plan,
    load_settings,
    save_settings,
    to_common_conf,
    to_tiku_conf,
)
from api.logger import logger
from api.notification import Notification
from api.progress import ProgressSink, RunControl
from api.runner import RunOptions, StudyRunner

from web import store
from web.events import BusProgress, EventBus, attach_log_sink


class AuthRequired(Exception):
    """未登录时抛出，由路由层转成 401。"""


class JobProgress(ProgressSink):
    """进度落库 + 广播：章节状态变化同时写入 SQLite。"""

    def __init__(self, bus: EventBus, job_id: int) -> None:
        """初始化。

        Args:
            bus: 事件总线。
            job_id: 当前任务 id。
        """
        self._bus = bus
        self._job_id = job_id

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """转发事件；章节事件落库，plan 事件把待处理章节也写进去。"""
        self._bus.emit(event, data)
        try:
            if event == "chapter":
                store.upsert_item(
                    self._job_id,
                    str(data.get("course") or ""),
                    str(data.get("title") or ""),
                    str(data.get("status") or ""),
                    int(data.get("tries") or 0),
                )
            elif event == "plan":
                for chapter in data.get("chapters") or []:
                    store.upsert_item(
                        self._job_id,
                        str(chapter.get("course") or ""),
                        str(chapter.get("title") or ""),
                        str(chapter.get("status") or "pending"),
                        0,
                    )
        except Exception as exc:  # noqa: BLE001 - 落库失败不影响学习
            logger.debug(f"章节状态落库失败: {exc}")


class Runtime:
    """全局运行时单例。"""

    def __init__(self) -> None:
        """初始化运行时。"""
        self.bus = EventBus()
        self._log_handler = attach_log_sink(self.bus)
        store.init_db()

        self._lock = threading.RLock()
        self.settings: dict[str, Any] = load_settings()
        self.chaoxing: Optional[Chaoxing] = None
        self.user_name: str = ""
        self.logged_in: bool = False
        self.control: Optional[RunControl] = None
        self.job_id: Optional[int] = None
        self.job_thread: Optional[threading.Thread] = None
        self.status: str = "idle"  # idle | running | stopping
        self.last_error: str = ""

    # ---------------------------------------------------------------- 设置
    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """合并并保存设置。"""
        with self._lock:
            self.settings = save_settings(payload)
            return self.settings

    def reload_settings(self) -> dict[str, Any]:
        """从磁盘重新加载设置。"""
        with self._lock:
            self.settings = load_settings()
            return self.settings

    def current_plan(self) -> dict[str, Any]:
        """当前答题方式（题库 / AI 的组合与顺序）。"""
        return answer_plan(self.settings)

    def set_answer_mode(self, mode: str) -> dict[str, Any]:
        """切换答题方式并落盘。

        Args:
            mode: ``ai_only`` / ``ai_first`` / ``tiku_first`` / ``tiku_only``。

        Returns:
            切换后的答题方式描述。
        """
        with self._lock:
            updated = apply_answer_mode(self.settings, mode)
            self.settings = save_settings(updated)
            return answer_plan(self.settings)

    # ---------------------------------------------------------------- 登录
    def build_chaoxing(self, username: str, password: str) -> Chaoxing:
        """按当前设置构造超星实例（含题库 / AI 答题）。"""
        common = to_common_conf(self.settings["common"])
        tiku_conf = to_tiku_conf(self.settings["tiku"], self.settings.get("ai"))

        tiku = Tiku.get_tiku_from_config(tiku_conf)
        tiku.init_tiku()

        return Chaoxing(
            account=Account(username, password),
            tiku=tiku,
            query_delay=float(tiku_conf.get("delay") or 0),
            work_max_retries=common.get("work_max_retries", 3),
            work_redo_enabled=common.get("work_redo_enabled", False),
        )

    def login(self, username: str, password: str, use_cookies: bool = False) -> dict[str, Any]:
        """登录超星，成功后保留实例供后续调用。"""
        with self._lock:
            if self.status == "running":
                raise RuntimeError("任务运行中，无法切换账号")

            chaoxing = self.build_chaoxing(username, password)
            state = chaoxing.login(login_with_cookies=use_cookies)
            if not state.get("status"):
                self.logged_in = False
                self.chaoxing = None
                return {"ok": False, "msg": state.get("msg") or "登录失败"}

            name = ""
            try:
                name = chaoxing.get_name() or username
            except Exception as exc:  # noqa: BLE001 - 取姓名失败不影响登录
                logger.debug(f"获取用户名失败: {exc}")
                name = username

            self.chaoxing = chaoxing
            self.user_name = name
            self.logged_in = True
            self.last_error = ""
            return {"ok": True, "msg": "登录成功", "name": name}

    def logout(self) -> None:
        """登出：清空运行时状态（不动磁盘上的 cookies.txt）。"""
        with self._lock:
            if self.status == "running":
                raise RuntimeError("任务运行中，请先停止任务")
            self.chaoxing = None
            self.user_name = ""
            self.logged_in = False

    def require_login(self) -> Chaoxing:
        """返回已登录实例，未登录时抛出 :class:`AuthRequired`。"""
        if not self.logged_in or self.chaoxing is None:
            raise AuthRequired("尚未登录")
        return self.chaoxing

    # ---------------------------------------------------------------- 任务
    def snapshot(self) -> dict[str, Any]:
        """当前运行状态快照。"""
        return {
            "logged_in": self.logged_in,
            "name": self.user_name,
            "status": self.status,
            "job_id": self.job_id,
        }

    def start_job(self, courses: list[dict[str, Any]], options: dict[str, Any]) -> int:
        """创建并启动一个学习任务（同一时刻只允许一个）。"""
        with self._lock:
            if self.status == "running":
                raise RuntimeError("已有任务正在运行，请先停止")
            if not courses:
                raise ValueError("请至少选择一门课程")
            chaoxing = self.require_login()

            merged_options = self._job_options(options)
            job_id = store.create_job(courses, merged_options)
            self.bus.clear_history()

            control = RunControl()
            self.control = control
            self.job_id = job_id
            self.status = "running"

            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, courses, merged_options, control, chaoxing),
                name=f"job-{job_id}",
                daemon=True,
            )
            self.job_thread = thread
            thread.start()
            return job_id

    def resume_job(self, job_id: int) -> int:
        """用历史任务的课程与参数重新开始一次（已完成的章节会被跳过）。

        Args:
            job_id: 要被"继续"的任务 id。

        Returns:
            新任务的 id。

        Raises:
            RuntimeError: 已有任务在运行。
            ValueError: 任务不存在或缺少课程信息。
        """
        with self._lock:
            if self.status == "running":
                raise RuntimeError("已有任务正在运行，请先停止")
            job = store.get_job(job_id)
            if job is None:
                raise ValueError("任务不存在")
            courses = [c for c in (job.get("courses") or []) if c.get("courseId")]
            if not courses:
                raise ValueError("该任务没有可用的课程信息")
            self.require_login()

            options = job.get("options") or {}
            return self.start_job(courses, {
                "speed": options.get("speed"),
                "jobs": options.get("jobs"),
                "notopen_action": options.get("notopen_action"),
                "add_learning_count": options.get("add_learning_count"),
                "target_count": options.get("target_count"),
                "retry_interval": options.get("retry_interval"),
            })

    def _job_options(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """把设置里的通用项与本次覆盖项合并成任务参数。"""
        common = to_common_conf(self.settings["common"])
        options = RunOptions(
            speed=float(overrides.get("speed") or common["speed"]),
            jobs=int(overrides.get("jobs") or common["jobs"]),
            notopen_action=str(overrides.get("notopen_action") or common.get("notopen_action") or "retry"),
            retry_interval=float(overrides.get("retry_interval") or common["retry_interval"]),
            add_learning_count=bool(overrides.get("add_learning_count", common["add_learning_count"])),
            target_count=int(overrides.get("target_count") or common["target_count"]),
            answer_plan=answer_plan(self.settings),
        )
        return options.as_config()

    def _build_notification(self) -> Optional[Notification]:
        """构造外部通知对象；未配置 url 时返回 None。"""
        notification_conf = self.settings.get("notification") or {}
        if not str(notification_conf.get("url") or "").strip():
            return None
        try:
            notification = Notification()
            notification.config_set({key: str(value) for key, value in notification_conf.items()})
            notification = notification.get_notification_from_config()
            notification.init_notification()
            return notification
        except Exception as exc:  # noqa: BLE001 - 通知不配置也能用
            logger.warning(f"初始化通知失败: {exc}")
            return None

    def _run_job(self, job_id: int, courses: list[dict[str, Any]], options: dict[str, Any],
                 control: RunControl, chaoxing: Chaoxing) -> None:
        """任务线程主体。"""
        store.update_job(job_id, status="running", started_at=_now())
        progress = JobProgress(self.bus, job_id)
        try:
            runner = StudyRunner(
                chaoxing,
                courses,
                options=RunOptions(**{
                    "speed": options["speed"],
                    "jobs": options["jobs"],
                    "notopen_action": options["notopen_action"],
                    "retry_interval": options["retry_interval"],
                    "add_learning_count": options["add_learning_count"],
                    "target_count": options["target_count"],
                    "answer_plan": options.get("answer_plan"),
                }),
                progress=progress,
                control=control,
                notification=self._build_notification(),
            )
            summary = runner.run()
            finished_status = "stopped" if summary.interrupted else "finished"
            store.update_job(
                job_id,
                status=finished_status,
                finished_at=_now(),
                summary=json.dumps({
                    "total": summary.total,
                    "success": summary.success,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                    "interrupted": summary.interrupted,
                    "elapsed": summary.elapsed,
                    "failed_chapters": summary.failed_chapters,
                }, ensure_ascii=False),
            )
        except BaseException as exc:  # noqa: BLE001 - 线程内异常必须落库并广播
            self.last_error = str(exc)
            logger.error(f"任务执行失败: {exc}")
            store.update_job(job_id, status="error", finished_at=_now(), error=str(exc))
            self.bus.emit("error", {"message": str(exc)})
        finally:
            with self._lock:
                self.status = "idle"
                self.control = None

    def control_job(self, action: str, value: Any = None) -> str:
        """控制当前任务：pause / resume / stop / set_speed。"""
        with self._lock:
            if self.control is None:
                raise RuntimeError("当前没有运行中的任务")
            if action == "pause":
                self.control.pause()
                self.status = "running"
                self.bus.emit("state", {"state": "paused"})
                return "paused"
            if action == "resume":
                self.control.resume()
                self.bus.emit("state", {"state": "running"})
                return "running"
            if action == "stop":
                self.control.stop()
                self.status = "stopping"
                return "stopping"
            if action == "set_speed":
                speed = self.control.set_speed(value if value is not None else 1.0)
                self.bus.emit("speed", {"speed": speed})
                return f"speed={speed}"
            raise ValueError(f"未知操作: {action}")


def _now() -> str:
    """当前时间字符串。"""
    return datetime.now().isoformat(timespec="seconds")


_runtime: Optional[Runtime] = None
_runtime_lock = threading.Lock()


def get_runtime() -> Runtime:
    """获取全局运行时单例。"""
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = Runtime()
        return _runtime
