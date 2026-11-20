# -*- coding: utf-8 -*-
"""Web 控制台相关单元测试：配置映射、事件总线、任务调度、REST 接口。"""
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import config_store  # noqa: E402
from api.progress import CallbackProgress, RunControl  # noqa: E402
from api.runner import ChapterResult, ChapterTask, JobProcessor  # noqa: E402


class TempHomeMixin:
    """把应用数据目录指向临时路径，避免污染真实用户目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"CHAOXING_HOME": self._tmp.name})
        self._env.start()
        config_store._home_cache = None

    def tearDown(self):
        self._env.stop()
        config_store._home_cache = None
        self._tmp.cleanup()


class ConfigStoreTest(TempHomeMixin, unittest.TestCase):
    def test_defaults_and_clamping(self):
        settings = config_store.normalize({"common": {"jobs": 99, "speed": 3, "notopen_action": "x"}})
        self.assertEqual(settings["common"]["jobs"], 16)
        self.assertEqual(settings["common"]["speed"], 2.0)
        self.assertEqual(settings["common"]["notopen_action"], "retry")
        self.assertEqual(settings["tiku"]["provider"], "TikuYanxi")

    def test_provider_chain_allowed(self):
        settings = config_store.normalize({"tiku": {"provider": "TikuGo,TikuYanxi"}})
        self.assertEqual(settings["tiku"]["provider"], "TikuGo,TikuYanxi")

    def test_invalid_provider_falls_back(self):
        settings = config_store.normalize({"tiku": {"provider": "Nope"}})
        self.assertEqual(settings["tiku"]["provider"], "TikuYanxi")

    def test_to_tiku_conf_stringifies_booleans(self):
        conf = config_store.to_tiku_conf({"submit": True, "cover_rate": 0.8, "provider": "AI"})
        self.assertEqual(conf["submit"], "true")
        self.assertEqual(conf["cover_rate"], "0.8")
        self.assertIsInstance(conf["cover_rate"], str)

    def test_save_and_load_roundtrip(self):
        saved = config_store.save_settings({"common": {"username": "13800000000", "jobs": 3}})
        loaded = config_store.load_settings()
        self.assertEqual(loaded["common"]["username"], "13800000000")
        self.assertEqual(loaded["common"]["jobs"], 3)
        self.assertEqual(saved["tiku"]["provider"], loaded["tiku"]["provider"])

    def test_ini_export_import_roundtrip(self):
        config_store.save_settings({
            "common": {"username": "u1", "password": "p1", "jobs": 5, "speed": 1.7, "course_list": ["11", "22"]},
            "tiku": {"provider": "TikuGo", "submit": True, "cover_rate": 0.6},
        })
        ini_path = config_store.export_ini(config_store.app_home() / "config.ini")
        self.assertTrue(ini_path.is_file())

        imported = config_store.import_ini(ini_path)
        self.assertEqual(imported["common"]["username"], "u1")
        self.assertEqual(imported["common"]["jobs"], 5)
        self.assertAlmostEqual(imported["common"]["speed"], 1.7)
        self.assertEqual(imported["common"]["course_list"], ["11", "22"])
        self.assertEqual(imported["tiku"]["provider"], "TikuGo")
        self.assertTrue(imported["tiku"]["submit"])

    def test_default_jobs_is_one(self):
        settings = config_store.normalize({})
        self.assertEqual(settings["common"]["jobs"], 1)

    def test_answer_plan_modes(self):
        base = config_store.normalize({"tiku": {"provider": "TikuYanxi"}})

        plan = config_store.answer_plan(base)
        self.assertEqual(plan["mode"], "tiku_only")
        self.assertEqual(plan["text"], "言溪题库")

        ai_only = config_store.apply_answer_mode(base, "ai_only")
        self.assertEqual(ai_only["tiku"]["provider"], "TikuAI")
        self.assertEqual(config_store.answer_plan(ai_only)["mode"], "ai_only")

        ai_first = config_store.apply_answer_mode(base, "ai_first")
        self.assertEqual(ai_first["tiku"]["provider"], "TikuAI,TikuYanxi")
        self.assertTrue(config_store.answer_plan(ai_first)["has_tiku"])

        tiku_first = config_store.apply_answer_mode(base, "tiku_first")
        self.assertEqual(tiku_first["tiku"]["provider"], "TikuYanxi,TikuAI")
        self.assertEqual(config_store.answer_plan(tiku_first)["mode"], "tiku_first")

    def test_answer_plan_warns_when_ai_unconfigured(self):
        settings = config_store.normalize({"tiku": {"provider": "TikuAI"}})
        plan = config_store.answer_plan(settings)
        self.assertFalse(plan["ai_ready"])
        self.assertIn("AI", plan["warning"])

    def test_answer_mode_requires_tiku(self):
        settings = config_store.normalize({"tiku": {"provider": "TikuAI"}})
        with self.assertRaises(ValueError):
            config_store.apply_answer_mode(settings, "tiku_only")


class EventBusTest(unittest.TestCase):
    def test_publish_and_history(self):
        from web.events import EventBus

        bus = EventBus(history_size=5)
        queue = bus.subscribe()
        bus.emit("progress", {"percent": 12})
        event, data = queue.get(timeout=1)
        self.assertEqual(event, "progress")
        self.assertEqual(data["percent"], 12)

        bus.emit("log", {"msg": "hello"})
        history = bus.history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1][1]["msg"], "hello")

        bus.unsubscribe(queue)
        while not queue.empty():
            queue.get_nowait()
        bus.emit("progress", {"percent": 50})
        self.assertTrue(queue.empty())

    def test_history_is_bounded(self):
        from web.events import EventBus

        bus = EventBus(history_size=3)
        for index in range(10):
            bus.emit("log", {"i": index})
        self.assertEqual(len(bus.history()), 3)
        self.assertEqual(bus.history()[-1][1]["i"], 9)


class DummyChaoxing:
    """最小替身：不发网络请求，按 job_info 决定章节是否开放。"""

    def __init__(self, job_info):
        self.job_info = job_info
        self.rate_limiter = _NoopRateLimiter()

    def get_job_list(self, course, point):
        return self.job_info["jobs"], self.job_info["job_info"]


class _NoopRateLimiter:
    def limit_rate(self, *args, **kwargs):
        return None


class RunnerProgressTest(unittest.TestCase):
    def _make_processor(self, events, control=None, notopen_action="retry"):
        course = {"title": "课程"}
        point = {"title": "章节", "has_finished": False}
        task = ChapterTask(index=0, point=point, course=course)
        config = {"speed": 1.0, "jobs": 1, "notopen_action": notopen_action, "retry_interval": 0.01}
        processor = JobProcessor(
            DummyChaoxing({"jobs": [], "job_info": {"notOpen": True}}),
            [task],
            config,
            progress=CallbackProgress(lambda event, data: events.append((event, data))),
            control=control,
        )
        processor.max_tries = 3
        return processor, task

    def _run(self, processor, timeout=5.0):
        thread = threading.Thread(target=processor.run, daemon=True)
        thread.start()
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "JobProcessor.run() 未在预期时间内返回")

    def test_emits_chapter_and_progress_events(self):
        events = []
        processor, _ = self._make_processor(events)
        self._run(processor)

        names = [name for name, _ in events]
        self.assertIn("job", names)
        self.assertIn("chapter", names)
        self.assertIn("progress", names)

        chapter_events = [data for name, data in events if name == "chapter"]
        self.assertTrue(any(item["status"] == "running" for item in chapter_events))
        self.assertTrue(any(item["status"] == "failed" for item in chapter_events))

        progress_events = [data for name, data in events if name == "progress"]
        self.assertEqual(progress_events[-1]["total"], 1)
        self.assertEqual(progress_events[-1]["percent"], 100.0)

    def test_stop_marks_tasks_skipped(self):
        events = []
        control = RunControl()
        control.stop()
        processor, _ = self._make_processor(events, control=control)
        self._run(processor)
        self.assertEqual(processor.stats["skipped"], 1)
        self.assertEqual(processor.stats["success"], 0)

    def test_continue_action_skips_not_open(self):
        events = []
        processor, _ = self._make_processor(events, notopen_action="continue")
        self._run(processor)
        self.assertEqual(processor.stats["skipped"], 1)
        self.assertEqual(processor.stats["failed"], 0)


class StudyRunnerPlanTest(unittest.TestCase):
    def test_plan_event_lists_all_chapters(self):
        from api.runner import StudyRunner

        class FakeChaoxing:
            def get_course_point(self, *args):
                return {"hasLocked": False, "points": [
                    {"id": "1", "title": "第一章", "has_finished": True, "jobCount": 2},
                    {"id": "2", "title": "第二章", "has_finished": False, "jobCount": 1},
                ]}

        events = []
        runner = StudyRunner(
            FakeChaoxing(),
            [{"courseId": "c1", "clazzId": "k1", "cpi": "p1", "title": "课程"}],
            progress=CallbackProgress(lambda event, data: events.append((event, data))),
        )
        tasks = runner.collect_tasks()

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].weight, 2)
        self.assertEqual(tasks[1].weight, 1)

        plan = next(data for event, data in events if event == "plan")
        self.assertEqual(plan["total"], 2)
        self.assertEqual(plan["chapters"][0]["status"], "success")
        self.assertEqual(plan["chapters"][1]["status"], "pending")
        self.assertEqual(plan["chapters"][1]["title"], "第二章")


class StopDuringVideoTest(unittest.TestCase):
    def test_stop_during_video_returns_pending(self):
        from api.progress import JobStopped
        from api.runner import ChapterResult, process_chapter

        class FakeChaoxing:
            rate_limiter = _NoopRateLimiter()

            def get_job_list(self, course, point):
                return [{"type": "video", "jobid": "1", "name": "视频", "objectid": "o", "playTime": "0"}], {}

            def study_video(self, *args, **kwargs):
                raise JobStopped()

        result = process_chapter(
            FakeChaoxing(),
            {"title": "课程"},
            {"title": "章节", "has_finished": False},
            1.0,
        )
        self.assertEqual(result, ChapterResult.PENDING)


class ResumeJobTest(TempHomeMixin, unittest.TestCase):
    def _runtime(self):
        from web.runtime import Runtime

        runtime = Runtime()
        runtime._log_handler  # noqa: B018 - 仅确认属性存在
        runtime.logged_in = True
        runtime.chaoxing = object()
        return runtime

    def test_resume_reuses_courses_and_options(self):
        from web import store
        from api.logger import logger

        runtime = self._runtime()
        try:
            job_id = store.create_job(
                [{"courseId": "1", "clazzId": "2", "cpi": "3", "title": "课程"}],
                {"speed": 1.5, "jobs": 3, "notopen_action": "continue"},
            )
            captured = {}

            def fake_start(courses, options):
                captured["courses"] = courses
                captured["options"] = options
                return 99

            runtime.start_job = fake_start
            new_id = runtime.resume_job(job_id)

            self.assertEqual(new_id, 99)
            self.assertEqual(captured["courses"][0]["courseId"], "1")
            self.assertEqual(captured["options"]["speed"], 1.5)
            self.assertEqual(captured["options"]["jobs"], 3)
            self.assertEqual(captured["options"]["notopen_action"], "continue")
        finally:
            logger.remove(runtime._log_handler)

    def test_resume_missing_job_raises(self):
        from api.logger import logger

        runtime = self._runtime()
        try:
            with self.assertRaises(ValueError):
                runtime.resume_job(999999)
        finally:
            logger.remove(runtime._log_handler)

    def test_resume_while_running_raises(self):
        from api.logger import logger

        runtime = self._runtime()
        try:
            runtime.status = "running"
            with self.assertRaises(RuntimeError):
                runtime.resume_job(1)
        finally:
            logger.remove(runtime._log_handler)


class WebApiTest(TempHomeMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = None

    def _client(self):
        from web import create_app
        from web import runtime as runtime_module

        runtime_module._runtime = None
        app = create_app({"TESTING": True})
        return app.test_client()

    def test_health_and_meta(self):
        client = self._client()
        health = client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])

        meta = client.get("/api/meta")
        self.assertIn("TikuYanxi", meta.get_json()["tiku_providers"])

    def test_index_served(self):
        client = self._client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>", response.get_data(as_text=True))

    def test_settings_put_and_get(self):
        client = self._client()
        response = client.put("/api/settings", json={"common": {"jobs": 7}, "tiku": {"submit": True}})
        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["settings"]
        self.assertEqual(settings["common"]["jobs"], 7)
        self.assertTrue(settings["tiku"]["submit"])

        again = client.get("/api/settings").get_json()["settings"]
        self.assertEqual(again["common"]["jobs"], 7)

    def test_requires_login(self):
        client = self._client()
        for path in ("/api/courses", "/api/auth/me"):
            response = client.get(path)
            self.assertIn(response.status_code, (200, 401))

        response = client.post("/api/jobs", json={"courses": [{"courseId": "1", "clazzId": "2"}]})
        self.assertEqual(response.status_code, 401)

    def test_unknown_job_404(self):
        client = self._client()
        self.assertEqual(client.get("/api/jobs/99999").status_code, 404)

    def test_static_assets_served(self):
        client = self._client()
        for path in ("/static/css/app.css", "/static/js/app.js", "/static/js/views/login.js"):
            response = client.get(path)
            try:
                self.assertEqual(response.status_code, 200, path)
            finally:
                response.close()

    def test_sse_replays_history(self):
        from web import routes as routes_module
        from web import runtime as runtime_module
        from web.events import EventBus

        bus = EventBus()
        bus.emit("log", {"level": "INFO", "time": "10:00:00", "msg": "hello"})
        bus.emit("progress", {"percent": 42, "done": 2, "total": 5})

        class FakeRuntime:
            def __init__(self):
                self.bus = bus

        original = routes_module.get_runtime
        routes_module.get_runtime = lambda: FakeRuntime()
        try:
            client = self._client()
            response = client.get("/api/jobs/1/events", buffered=False)
            try:
                first = next(response.response)
                text = first.decode("utf-8") if isinstance(first, bytes) else str(first)
                self.assertIn("event: log", text)
                self.assertIn("hello", text)
            finally:
                response.close()
        finally:
            routes_module.get_runtime = original
            runtime_module._runtime = None


if __name__ == "__main__":
    unittest.main()
