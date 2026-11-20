# -*- coding: utf-8 -*-
"""学习执行器：命令行与 Web 控制台共用的任务调度层。

原先这段逻辑散落在 ``main.py`` 里，只能通过 stdout/tqdm 观察进度。
抽到本模块后：

* CLI（``main.py``）继续使用同样的流程，进度仍由底层 tqdm 输出，行为不变；
* Web 端传入 :class:`api.progress.ProgressSink`，即可拿到结构化事件；
* 暂停/停止通过 :class:`api.progress.RunControl` 实现。
"""
from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Any, Optional

from api.base import Chaoxing, StudyResult
from api.live import Live
from api.live_process import LiveProcessor
from api.logger import logger
from api.progress import JobStopped, NullProgress, ProgressSink, RunControl, _safe_emit

try:  # Python 3.13+ 才有 queue.ShutDown
    from queue import ShutDown
except ImportError:  # pragma: no cover - 兼容低版本解释器

    class ShutDown(Exception):
        """低版本 Python 的占位实现。"""


class ChapterResult(enum.Enum):
    """章节处理结果。"""

    SUCCESS = (0,)
    ERROR = (1,)
    NOT_OPEN = (2,)
    PENDING = (3,)


@dataclass
class ChapterTask:
    """一个章节的待办任务。"""

    index: int
    point: dict[str, Any]
    course: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0
    weight: int = 1

    def __lt__(self, other):
        """按索引比较，供优先级队列排序。"""
        if not isinstance(other, ChapterTask):
            return NotImplemented
        return self.index < other.index


@dataclass
class RunOptions:
    """一次学习任务的运行参数。"""

    speed: float = 1.0
    jobs: int = 1
    notopen_action: str = "retry"
    retry_interval: float = 1.0
    add_learning_count: bool = False
    target_count: int = 100
    max_tries: int = 5
    answer_plan: Optional[dict[str, Any]] = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RunOptions":
        """从旧版配置字典构造，保持 CLI 兼容。"""
        return cls(
            speed=float(config.get("speed") or 1.0),
            jobs=int(config.get("jobs") or 1),
            notopen_action=str(config.get("notopen_action") or "retry"),
            retry_interval=float(config.get("retry_interval") or 1.0),
            add_learning_count=bool(config.get("add_learning_count") or False),
            target_count=int(config.get("target_count") or 100),
            answer_plan=config.get("answer_plan"),
        )

    def as_config(self) -> dict[str, Any]:
        """转换回旧版配置字典，供 JobProcessor / 章节学习次数使用。"""
        return {
            "speed": self.speed,
            "jobs": self.jobs,
            "notopen_action": self.notopen_action,
            "retry_interval": self.retry_interval,
            "add_learning_count": self.add_learning_count,
            "target_count": self.target_count,
            "answer_plan": self.answer_plan,
        }


@dataclass
class RunSummary:
    """一次学习任务的汇总结果。"""

    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    interrupted: bool = False
    elapsed: float = 0.0
    failed_chapters: list[dict[str, Any]] = field(default_factory=list)


def process_job(chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float,
                progress: Optional[ProgressSink] = None,
                control: Optional[RunControl] = None) -> StudyResult:
    """处理单个任务点。"""
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        _safe_emit(progress or NullProgress(), "task", {
            "course": course.get("title"),
            "job": job.get("name"),
            "type": "video",
        })

        progress_cb = None
        if progress is not None:
            job_name = job.get("name")

            def progress_cb(play_time, duration, _name=job_name):
                _safe_emit(progress, "video", {
                    "course": course.get("title"),
                    "job": _name,
                    "play": int(play_time),
                    "duration": int(duration),
                })

        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video",
            _progress_cb=progress_cb, _control=control,
        )
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio",
                _progress_cb=progress_cb, _control=control)
        if video_result.is_failure():
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
        return video_result
    # 文档任务
    elif job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        _safe_emit(progress or NullProgress(), "task", {
            "course": course.get("title"),
            "job": job.get("name"),
            "type": "document",
        })
        return chaoxing.study_document(course, job)
    # 测验任务
    elif job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        _safe_emit(progress or NullProgress(), "task", {
            "course": course.get("title"),
            "job": job.get("name"),
            "type": "work",
        })
        return chaoxing.study_work(course, job, job_info)
    # 阅读任务
    elif job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        _safe_emit(progress or NullProgress(), "task", {
            "course": course.get("title"),
            "job": job.get("name"),
            "type": "read",
        })
        return chaoxing.study_read(course, job, job_info)
    # 直播任务
    elif job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        _safe_emit(progress or NullProgress(), "task", {
            "course": course.get("title"),
            "job": job.get("name"),
            "type": "live",
        })
        try:
            # 准备直播所需参数
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid")
            }

            # 创建直播对象
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId")
            )

            # 启动直播处理线程
            thread = threading.Thread(
                target=LiveProcessor.run_live,
                args=(live, speed),
                daemon=True
            )
            thread.start()
            thread.join()  # 等待直播处理完成
            return StudyResult.SUCCESS
        except Exception as e:
            logger.error(f"处理直播任务时出错: {str(e)}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any], speed: float,
                    progress: Optional[ProgressSink] = None,
                    control: Optional[RunControl] = None) -> ChapterResult:
    """处理单个章节。"""
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    if control is not None and control.stopped:
        return ChapterResult.PENDING

    # 随机等待，避免请求过快
    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)

    # 获取当前章节的所有任务点
    job_info = None
    jobs, job_info = chaoxing.get_job_list(course, point)

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    job_results: list[StudyResult] = []
    for job in jobs:
        if control is not None:
            if control.stopped:
                return ChapterResult.PENDING
            control.wait_if_paused(progress)
        try:
            result = process_job(chaoxing, course, job, job_info, speed,
                                 progress=progress, control=control)
        except JobStopped:
            # 视频播放中被手动停止：立刻退出，章节标记为跳过
            logger.warning(f"章节被手动停止: {point.get('title')}")
            return ChapterResult.PENDING
        job_results.append(result)

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS


class JobProcessor:
    """章节任务调度器：多 worker 并发处理章节，失败重试，支持暂停/停止。"""

    def __init__(self, chaoxing: Chaoxing, tasks: list[ChapterTask], config: dict[str, Any],
                 progress: Optional[ProgressSink] = None,
                 control: Optional[RunControl] = None):
        """初始化任务处理器。

        Args:
            chaoxing: 超星实例。
            tasks: 章节任务列表。
            config: 旧版配置字典（speed / jobs / notopen_action / retry_interval）。
            progress: 可选的进度接收器。
            control: 可选的运行控制器。
        """
        if "jobs" not in config or not config["jobs"]:
            config["jobs"] = 1

        self.chaoxing = chaoxing
        self.speed = config["speed"]
        self.max_tries = config.get("max_tries", 5)
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.wait_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = config["jobs"]
        self.config = config
        self.retry_interval = config.get("retry_interval", 1.0)
        self.progress = progress
        self.control = control or RunControl()

        self.total = len(tasks)
        self.total_weight = sum(max(1, int(getattr(t, "weight", 1) or 1)) for t in tasks) or 1
        self.done_weight = 0
        self.stats = {"success": 0, "failed": 0, "skipped": 0}
        self._started_at = time.time()
        self._finished = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 进度
    def _emit(self, event: str, data: dict[str, Any]) -> None:
        """向接收器上报事件（容错）。"""
        if self.progress is not None:
            _safe_emit(self.progress, event, data)

    def _emit_progress(self) -> None:
        """上报总体进度。"""
        percent = min(100.0, self.done_weight / self.total_weight * 100)
        self._emit("progress", {
            "done": self.stats["success"] + self.stats["failed"] + self.stats["skipped"],
            "total": self.total,
            "percent": round(percent, 2),
            "counts": dict(self.stats),
            "elapsed": round(time.time() - self._started_at, 1),
            "speed": self.current_speed,
        })

    @property
    def current_speed(self) -> float:
        """当前生效的倍速：运行中可被 ``RunControl.set_speed`` 覆盖。"""
        if self.control.speed:
            return float(self.control.speed)
        return float(self.speed)

    def _finish(self, task: ChapterTask, status: str) -> None:
        """记录一个任务已离开调度队列。"""
        with self._lock:
            weight = max(1, int(getattr(task, "weight", 1) or 1))
            if status in self.stats:
                self.stats[status] += 1
            self.done_weight += weight
        if status == "failed":
            self.failed_tasks.append(task)
        self._emit("chapter", {
            "index": task.index,
            "course": task.course.get("title"),
            "title": task.point.get("title"),
            "status": status,
            "tries": task.tries,
        })
        self._emit_progress()

    # ------------------------------------------------------------------ 调度
    def run(self) -> None:
        """启动 worker 与重试线程，阻塞直到队列清空。"""
        for task in self.tasks:
            self.task_queue.put(task)

        self._emit("job", {
            "total": self.total,
            "total_weight": self.total_weight,
            "courses": sorted({t.course.get("title") for t in self.tasks if t.course.get("title")}),
        })
        self._emit_progress()

        for _ in range(max(1, int(self.worker_num))):
            thread = threading.Thread(target=self.worker_thread, daemon=True)
            self.threads.append(thread)
            thread.start()

        threading.Thread(target=self.retry_thread, daemon=True).start()
        watchdog = threading.Thread(target=self._stop_watcher, daemon=True)
        watchdog.start()

        self.task_queue.join()
        self._finished.set()
        time.sleep(0.2)
        if hasattr(self.task_queue, "shutdown"):
            self.task_queue.shutdown()

    def _stop_watcher(self) -> None:
        """收到停止请求后清空待办队列，避免 join 永久阻塞。"""
        while not self._finished.is_set():
            if self.control.stopped:
                while True:
                    try:
                        task = self.task_queue.get_nowait()
                    except Empty:
                        break
                    else:
                        self._finish(task, "skipped")
                        self.task_queue.task_done()
                return
            time.sleep(0.2)

    def worker_thread(self) -> None:
        """worker：取任务 → 处理章节 → 记录结果。"""
        while True:
            try:
                task = self.task_queue.get()
            except ShutDown:
                logger.info("Queue shut down")
                return

            if self.control.stopped:
                self._finish(task, "skipped")
                self.task_queue.task_done()
                continue

            self.control.wait_if_paused(self.progress)
            if self.control.stopped:
                self._finish(task, "skipped")
                self.task_queue.task_done()
                continue

            self._emit("chapter", {
                "index": task.index,
                "course": task.course.get("title"),
                "title": task.point.get("title"),
                "status": "running",
                "tries": task.tries,
            })

            try:
                task.result = process_chapter(self.chaoxing, task.course, task.point, self.current_speed,
                                              progress=self.progress, control=self.control)
            except BaseException as exc:  # noqa: BLE001 - 单章节异常不应拖垮整个队列
                logger.error(f"Error in thread {threading.current_thread().name}: {exc}")
                task.result = ChapterResult.ERROR

            match task.result:
                case ChapterResult.SUCCESS:
                    logger.debug("Task success: {} - {}", task.course["title"], task.point["title"])
                    self._finish(task, "success")
                    self.task_queue.task_done()
                    logger.debug(f"unfinished task: {self.task_queue.unfinished_tasks}")

                case ChapterResult.NOT_OPEN:
                    if self.config["notopen_action"] == "continue":
                        logger.warning("章节未开启: {} - {}, 正在跳过", task.course["title"], task.point["title"])
                        self._finish(task, "skipped")
                        self.task_queue.task_done()
                        continue

                    task.tries += 1
                    if task.tries >= self.max_tries:
                        logger.error(
                            "章节未开启: {} - {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                            "请手动检查完成并提交再重试。或者在配置中配置(自动跳过关闭章节/开启题库并启用提交)"
                            , task.course["title"], task.point["title"])
                        self._finish(task, "failed")
                        self.task_queue.task_done()
                        continue

                    self._emit("chapter", {
                        "index": task.index,
                        "course": task.course.get("title"),
                        "title": task.point.get("title"),
                        "status": "retrying",
                        "tries": task.tries,
                    })
                    self.retry_queue.put(task)

                case ChapterResult.ERROR:
                    task.tries += 1
                    logger.warning("重试任务 {} - {} ({}/{} 次尝试)", task.course["title"], task.point["title"],
                                   task.tries,
                                   self.max_tries)
                    if task.tries >= self.max_tries:
                        logger.error("任务重试次数达到上限: {} - {}", task.course["title"], task.point["title"])
                        self._finish(task, "failed")
                        self.task_queue.task_done()
                        continue
                    self._emit("chapter", {
                        "index": task.index,
                        "course": task.course.get("title"),
                        "title": task.point.get("title"),
                        "status": "retrying",
                        "tries": task.tries,
                    })
                    self.retry_queue.put(task)

                case ChapterResult.PENDING:
                    # 主动停止时返回，任务视为跳过
                    self._finish(task, "skipped")
                    self.task_queue.task_done()

                case _:
                    logger.error("任务 {} 的状态无效 {}", task.result, task.point["title"])
                    self._finish(task, "failed")
                    self.task_queue.task_done()

    def retry_thread(self) -> None:
        """重试线程：把重试队列中的任务重新压回主队列。"""
        try:
            while True:
                task = self.retry_queue.get()
                self.task_queue.put(task)
                # task_done is not called when a task failed and needs to be retried so if is reinserted into the queue,
                # the task num will increase by one and become more than the real task number
                self.task_queue.task_done()
                time.sleep(self.retry_interval)
        except ShutDown:
            pass


class StudyRunner:
    """一次完整的"登录后 → 学习指定课程"的执行流程。"""

    def __init__(self, chaoxing: Chaoxing, courses: list[dict[str, Any]],
                 options: Optional[RunOptions] = None,
                 progress: Optional[ProgressSink] = None,
                 control: Optional[RunControl] = None,
                 notification: Any = None):
        """初始化执行器。

        Args:
            chaoxing: 已登录的超星实例。
            courses: 要学习的课程字典列表。
            options: 运行参数。
            progress: 进度接收器。
            control: 运行控制器（暂停/停止）。
            notification: 可选的通知对象，需实现 ``send(msg)``。
        """
        self.chaoxing = chaoxing
        self.courses = courses or []
        self.options = options or RunOptions()
        self.progress = progress or NullProgress()
        self.control = control or RunControl()
        self.notification = notification
        self.summary = RunSummary()

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        """上报事件（容错）。"""
        _safe_emit(self.progress, event, data)

    def _notify(self, message: str) -> None:
        """发送外部通知，失败不影响主流程。"""
        if self.notification is None:
            return
        try:
            self.notification.send(message)
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响学习
            logger.warning(f"通知发送失败: {exc}")

    def collect_tasks(self) -> list[ChapterTask]:
        """读取所有课程章节并展开成任务列表。"""
        tasks: list[ChapterTask] = []
        for course in self.courses:
            if self.control.stopped:
                break
            self._emit("course", {
                "courseId": course.get("courseId"),
                "clazzId": course.get("clazzId"),
                "title": course.get("title"),
                "status": "preparing",
            })
            logger.info(f"正在读取课程章节: {course['title']}")
            point_list = self.chaoxing.get_course_point(
                course["courseId"], course["clazzId"], course["cpi"]
            )
            points = point_list.get("points", [])
            self._emit("course", {
                "courseId": course.get("courseId"),
                "clazzId": course.get("clazzId"),
                "title": course.get("title"),
                "status": "ready",
                "chapters": len(points),
                "finished": sum(1 for p in points if p.get("has_finished")),
            })
            for point in points:
                try:
                    weight = max(1, int(point.get("jobCount") or 1))
                except (TypeError, ValueError):
                    weight = 1
                tasks.append(ChapterTask(index=len(tasks), point=point, course=course, weight=weight))

        # 把完整章节清单先上报一次，前端可以立刻展示"还没轮到"的章节
        self._emit("plan", {
            "total": len(tasks),
            "chapters": [
                {
                    "index": task.index,
                    "course": task.course.get("title"),
                    "title": task.point.get("title"),
                    "status": "success" if task.point.get("has_finished") else "pending",
                }
                for task in tasks
            ],
        })
        return tasks

    def run(self) -> RunSummary:
        """执行学习流程并返回汇总结果。"""
        started_at = time.time()
        config = self.options.as_config()
        config["max_tries"] = self.options.max_tries

        plan = self.options.answer_plan
        if plan:
            logger.info(f"答题方式：{plan.get('label')}（{plan.get('text')}）")
            if plan.get("warning"):
                logger.warning(f"答题方式提示：{plan['warning']}")
            self._emit("answer", plan)
        else:
            logger.info("答题方式：沿用当前题库配置")

        try:
            tasks = self.collect_tasks()
        except BaseException as exc:  # noqa: BLE001 - 统一转为事件上报
            logger.error(f"读取课程章节失败: {exc}")
            self._emit("error", {"message": str(exc)})
            raise

        processor = JobProcessor(self.chaoxing, tasks, config,
                                 progress=self.progress, control=self.control)
        processor.run()

        self.summary.total = processor.total
        self.summary.success = processor.stats["success"]
        self.summary.failed = processor.stats["failed"]
        self.summary.skipped = processor.stats["skipped"]
        self.summary.interrupted = self.control.stopped
        self.summary.elapsed = round(time.time() - started_at, 1)
        self.summary.failed_chapters = [
            {"course": t.course.get("title"), "title": t.point.get("title"), "tries": t.tries}
            for t in processor.failed_tasks
        ]

        if self.summary.interrupted:
            logger.warning("学习任务已被手动停止")
            self._notify("chaoxing : 学习任务已被手动停止")
        else:
            logger.info("所有课程学习任务已完成")
            self._notify("chaoxing : 所有课程学习任务已完成")

        if self.options.add_learning_count and not self.control.stopped:
            from api.process import increase_learning_count_for_course

            logger.info("刷课完成，开始增加章节学习次数...")
            count_config = dict(config)
            count_config["target_count"] = self.options.target_count
            for course in self.courses:
                if self.control.stopped:
                    break
                try:
                    increase_learning_count_for_course(self.chaoxing, course, count_config)
                except Exception as exc:  # noqa: BLE001 - 单课程失败不影响其他课程
                    logger.error(f"课程 [{course.get('title')}] 增加学习次数失败: {exc}")
            logger.info("所有课程章节学习次数增加完成")
            self._notify("chaoxing : 所有课程章节学习次数增加完成")

        self._emit("done", {
            "total": self.summary.total,
            "success": self.summary.success,
            "failed": self.summary.failed,
            "skipped": self.summary.skipped,
            "interrupted": self.summary.interrupted,
            "elapsed": self.summary.elapsed,
            "failed_chapters": self.summary.failed_chapters,
        })
        return self.summary
