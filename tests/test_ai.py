# -*- coding: utf-8 -*-
"""AI 答题相关测试：地址规范化、错误诊断、自检流程、配置映射。"""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import ai_client, config_store  # noqa: E402
from api.progress import RunControl  # noqa: E402


class FakeResponse:
    """最小化的 requests.Response 替身。"""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def chat_ok(content="2"):
    return FakeResponse(200, {
        "choices": [{"message": {"content": content}}],
    })


class NormalizeTest(unittest.TestCase):
    def test_adds_scheme_and_slash(self):
        base, candidates = ai_client.normalize_base_url("api.deepseek.com//")
        self.assertEqual(base, "https://api.deepseek.com")
        self.assertIn("https://api.deepseek.com", candidates)
        self.assertIn("https://api.deepseek.com/v1", candidates)

    def test_strips_chat_completions_suffix(self):
        base, candidates = ai_client.normalize_base_url("https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(base, "https://api.deepseek.com/v1")
        self.assertEqual(candidates[0], "https://api.deepseek.com/v1")
        self.assertIn("https://api.deepseek.com", candidates)

    def test_keeps_v1(self):
        base, candidates = ai_client.normalize_base_url("https://api.openai.com/v1")
        self.assertEqual(base, "https://api.openai.com/v1")
        self.assertEqual(candidates, ["https://api.openai.com/v1", "https://api.openai.com"])

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            ai_client.normalize_base_url("   ")


class DiagnoseTest(unittest.TestCase):
    def test_401_points_to_key(self):
        reason, suggestion = ai_client.diagnose_http_error(401, "invalid api key")
        self.assertIn("401", reason)
        self.assertIn("Key", suggestion)

    def test_404_points_to_path(self):
        _, suggestion = ai_client.diagnose_http_error(404, "Not Found")
        self.assertIn("/v1", suggestion)

    def test_429_points_to_rate_limit(self):
        _, suggestion = ai_client.diagnose_http_error(429, "rate limit")
        self.assertIn("限流", suggestion)

    def test_dns_error(self):
        import requests

        exc = requests.exceptions.ConnectionError("getaddrinfo failed")
        reason, _ = ai_client.diagnose_exception(exc)
        self.assertIn("域名解析失败", reason)


class ParseAnswerTest(unittest.TestCase):
    def test_json_answer_list(self):
        self.assertEqual(ai_client.parse_answer_text('{"Answer": ["北京"]}'), "北京")

    def test_json_answer_multiple(self):
        self.assertEqual(ai_client.parse_answer_text('{"Answer": ["A内容", "B内容"]}'), "A内容\nB内容")

    def test_lowercase_key(self):
        self.assertEqual(ai_client.parse_answer_text('{"answer": "对"}'), "对")

    def test_markdown_fenced(self):
        self.assertEqual(ai_client.parse_answer_text('```json\n{"Answer": ["2"]}\n```'), "2")

    def test_plain_text(self):
        self.assertEqual(ai_client.parse_answer_text("答案是北京"), "答案是北京")

    def test_empty(self):
        self.assertIsNone(ai_client.parse_answer_text(""))


class ClientChatTest(unittest.TestCase):
    def test_retries_next_candidate_on_404(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.endswith("/chat/completions") and "/v1/" not in url:
                return FakeResponse(404, {"error": {"message": "not found"}})
            return chat_ok("2")

        with mock.patch.object(ai_client.requests, "post", side_effect=fake_post):
            client = ai_client.AIClient("https://api.deepseek.com", api_key="k", model="deepseek-chat",
                                        min_interval=0)
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "2")

        self.assertTrue(any("/v1/chat/completions" in url for url in calls))

    def test_http_error_raises_with_reason(self):
        with mock.patch.object(ai_client.requests, "post",
                               return_value=FakeResponse(401, {"error": {"message": "invalid key"}})):
            client = ai_client.AIClient("https://api.deepseek.com/v1", api_key="bad", model="m", min_interval=0)
            with self.assertRaises(ai_client.AIRequestError) as ctx:
                client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status, 401)

    def test_empty_content_raises(self):
        with mock.patch.object(ai_client.requests, "post", return_value=chat_ok("")):
            client = ai_client.AIClient("https://api.deepseek.com/v1", api_key="k", model="m", min_interval=0)
            with self.assertRaises(ai_client.AIRequestError):
                client.chat([{"role": "user", "content": "hi"}])

    def test_answer_strips_option_letters(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured["payload"] = kwargs.get("json")
            return chat_ok('{"Answer": ["北京"]}')

        with mock.patch.object(ai_client.requests, "post", side_effect=fake_post):
            client = ai_client.AIClient("https://api.deepseek.com/v1", api_key="k", model="m", min_interval=0)
            answer = client.answer({"title": "首都是？", "type": "single", "options": "A. 上海\nB. 北京"})

        self.assertEqual(answer, "北京")
        user_message = captured["payload"]["messages"][-1]["content"]
        self.assertIn("上海", user_message)
        self.assertNotIn("A. 上海", user_message)


class ClientTestFlow(unittest.TestCase):
    def _client(self):
        return ai_client.AIClient("https://api.deepseek.com/v1", api_key="k", model="deepseek-chat",
                                  min_interval=0, timeout=10)

    def test_success_report(self):
        responses = {
            "get": FakeResponse(200, {"data": [{"id": "deepseek-chat"}]}),
            "post": chat_ok("2"),
        }
        with mock.patch.object(ai_client.requests, "get", return_value=responses["get"]), \
                mock.patch.object(ai_client.requests, "post", return_value=responses["post"]):
            report = self._client().test()

        self.assertTrue(report.ok)
        self.assertEqual(report.sample, "2")
        names = [step.name for step in report.steps]
        self.assertTrue(any("models" in name for name in names))
        self.assertTrue(any("chat/completions" in name for name in names))

    def test_bad_key_report(self):
        with mock.patch.object(ai_client.requests, "get",
                               return_value=FakeResponse(401, {"error": {"message": "invalid key"}})):
            report = self._client().test()

        self.assertFalse(report.ok)
        self.assertIn("401", report.reason)
        self.assertIn("Key", report.suggestion)

    def test_models_404_still_tests_chat(self):
        with mock.patch.object(ai_client.requests, "get", return_value=FakeResponse(404, {})), \
                mock.patch.object(ai_client.requests, "post", return_value=chat_ok("2")):
            report = self._client().test()
        self.assertTrue(report.ok, report.reason)

    def test_chat_failure_reports_suggestion(self):
        with mock.patch.object(ai_client.requests, "get", return_value=FakeResponse(404, {})), \
                mock.patch.object(ai_client.requests, "post",
                                  return_value=FakeResponse(400, {"error": {"message": "model not found"}})):
            report = self._client().test()
        self.assertFalse(report.ok)
        self.assertIn("模型", report.suggestion)


class ConfigMappingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"CHAOXING_HOME": self._tmp.name})
        self._env.start()
        config_store._home_cache = None

    def tearDown(self):
        self._env.stop()
        config_store._home_cache = None
        self._tmp.cleanup()

    def test_ai_block_injected_into_tiku_conf(self):
        settings = config_store.normalize({
            "ai": {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-1", "model": "deepseek-chat"},
        })
        conf = config_store.to_tiku_conf(settings["tiku"], settings["ai"])
        self.assertEqual(conf["ai_base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(conf["ai_key"], "sk-1")
        self.assertEqual(conf["ai_model"], "deepseek-chat")

    def test_use_for_answer_prepends_tikuai(self):
        settings = config_store.normalize({"ai": {"use_for_answer": True}})
        self.assertTrue(settings["tiku"]["provider"].startswith("TikuAI"))
        self.assertIn("TikuYanxi", settings["tiku"]["provider"])

    def test_ai_numeric_clamped(self):
        settings = config_store.normalize({"ai": {"temperature": 9, "max_tokens": 1, "timeout": 1}})
        self.assertEqual(settings["ai"]["temperature"], 2.0)
        self.assertEqual(settings["ai"]["max_tokens"], 64)
        self.assertEqual(settings["ai"]["timeout"], 5)


class AiRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"CHAOXING_HOME": self._tmp.name})
        self._env.start()
        config_store._home_cache = None
        from web import create_app
        from web import runtime as runtime_module

        runtime_module._runtime = None
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self._env.stop()
        config_store._home_cache = None
        self._tmp.cleanup()

    def test_empty_base_url_is_reported(self):
        response = self.client.post("/api/ai/test", json={"ai": {"base_url": "", "model": "x"}})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("API 地址", payload["reason"])

    def test_missing_model_is_reported(self):
        response = self.client.post("/api/ai/test",
                                    json={"ai": {"base_url": "https://api.deepseek.com/v1", "model": ""}})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ok"])

    def test_test_endpoint_success(self):
        with mock.patch.object(ai_client.requests, "get",
                               return_value=FakeResponse(200, {"data": [{"id": "deepseek-chat"}]})), \
                mock.patch.object(ai_client.requests, "post", return_value=chat_ok("2")):
            response = self.client.post("/api/ai/test", json={"ai": {
                "base_url": "https://api.deepseek.com/v1", "api_key": "k",
                "model": "deepseek-chat", "min_interval_seconds": 0,
            }})
        payload = response.get_json()
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["sample"], "2")

    def test_set_speed_control(self):
        from web import routes as routes_module
        from web.events import EventBus
        from web.runtime import Runtime

        stub = types.SimpleNamespace(
            job_id=1,
            control=RunControl(),
            status="running",
            bus=EventBus(),
            _lock=threading.RLock(),
        )
        stub.control_job = lambda action, value=None: Runtime.control_job(stub, action, value)

        original = routes_module.get_runtime
        routes_module.get_runtime = lambda: stub
        try:
            response = self.client.post("/api/jobs/1/control", json={"action": "set_speed", "value": 1.8})
            payload = response.get_json()
            self.assertEqual(response.status_code, 200, payload)
            self.assertAlmostEqual(stub.control.speed, 1.8)
        finally:
            routes_module.get_runtime = original


if __name__ == "__main__":
    unittest.main()
