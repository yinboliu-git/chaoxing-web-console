# -*- coding: utf-8 -*-
"""AI 答题客户端：OpenAI 兼容接口的地址规范化、答题调用与分步自检。

与 ``api/answer.py`` 里的 ``AI`` 题库不同，本模块把「测试接口」与「答题接口」
分开处理：

* **测试**：先探 ``/models``（验证鉴权与连通性），再用一次真实 ``/chat/completions``
  验证答题链路，逐步返回结果与失败原因；
* **答题**：只走 ``/chat/completions``，按题型构造提示词，并把模型输出规整成
  引擎需要的答案文本。

地址支持三种写法，都会自动纠正：
``https://api.deepseek.com``、``https://api.deepseek.com/v1``、
``https://api.deepseek.com/v1/chat/completions``。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from api.logger import logger

DEFAULT_TIMEOUT = 60
DEFAULT_MIN_INTERVAL = 3

# 常见服务商预设：base_url 与示例模型
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "key 以 sk- 开头；模型常用 deepseek-chat / deepseek-reasoner",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "hint": "国内访问通常需要代理",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "hint": "模型名带厂商前缀，如 deepseek-ai/DeepSeek-V3",
        "key_url": "https://cloud.siliconflow.cn/account/ak",
    },
    "dashscope": {
        "label": "通义千问（DashScope）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "hint": "使用兼容模式地址",
        "key_url": "https://bailian.console.aliyun.com/",
    },
    "moonshot": {
        "label": "Kimi（Moonshot）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "hint": "",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "hint": "",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "ollama": {
        "label": "本地 Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "hint": "本地服务，Key 随便填（部分实现要求非空）",
        "key_url": "https://ollama.com/download",
    },
    "custom": {
        "label": "自定义 / 中转站",
        "base_url": "",
        "model": "",
        "hint": "填兼容 OpenAI 协议的地址，一般以 /v1 结尾",
        "key_url": "",
    },
}


@dataclass
class Step:
    """一步自检结果。"""

    name: str
    ok: Optional[bool]
    detail: str
    ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        """转成可直接 JSON 序列化的字典。"""
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "ms": self.ms}


@dataclass
class TestReport:
    """一次完整自检的报告。"""

    ok: bool = False
    reason: str = ""
    suggestion: str = ""
    base_url: str = ""
    resolved_base: str = ""
    model: str = ""
    sample: str = ""
    elapsed_ms: int = 0
    steps: list[Step] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典。"""
        return {
            "ok": self.ok,
            "reason": self.reason,
            "suggestion": self.suggestion,
            "base_url": self.base_url,
            "resolved_base": self.resolved_base,
            "model": self.model,
            "sample": self.sample,
            "elapsed_ms": self.elapsed_ms,
            "steps": [step.as_dict() for step in self.steps],
        }


class AIRequestError(Exception):
    """AI 调用失败，带可读原因与修复建议。"""

    def __init__(self, reason: str, suggestion: str = "", status: Optional[int] = None,
                 raw: str = "") -> None:
        """初始化。

        Args:
            reason: 人话版失败原因。
            suggestion: 修复建议。
            status: HTTP 状态码（若来自服务端）。
            raw: 原始响应片段，便于排查。
        """
        super().__init__(reason)
        self.reason = reason
        self.suggestion = suggestion
        self.status = status
        self.raw = raw[:800]


def normalize_base_url(raw: str) -> tuple[str, list[str]]:
    """规范化用户填写的地址。

    允许写成 ``host``、``.../v1``、``.../v1/chat/completions``，
    多余的 ``/chat/completions``、``/completions``、尾部斜杠都会被去掉。

    Args:
        raw: 原始输入。

    Returns:
        ``(base_url, candidates)``，candidates 是可供尝试的候选地址（自动补 /v1）。

    Raises:
        ValueError: 输入为空或不是 http(s) 地址。
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("请填写 API 地址")

    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text

    text = text.split("?")[0].split("#")[0].rstrip("/")
    text = re.sub(r"/(chat/completions|completions|models)$", "", text, flags=re.IGNORECASE)

    if not re.match(r"^https?://[^/\s]+", text, re.IGNORECASE):
        raise ValueError(f"地址格式不正确: {raw}")

    candidates = [text]
    if text.rstrip("/").endswith("/v1"):
        candidates.append(text[: -len("/v1")])
    else:
        candidates.append(text + "/v1")

    # 去重且保序
    unique: list[str] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return text, unique


def _extract_error(resp: requests.Response) -> tuple[str, str]:
    """从响应中提取 (code, message)。"""
    try:
        payload = resp.json()
    except ValueError:
        return "", (resp.text or "").strip()[:300]

    if isinstance(payload, dict):
        node = payload.get("error", payload)
        if isinstance(node, dict):
            code = str(node.get("code") or node.get("type") or "")
            message = str(node.get("message") or node.get("msg") or "")
            return code, message or json.dumps(node, ensure_ascii=False)[:300]
        return "", str(node)[:300]
    return "", str(payload)[:300]


def diagnose_http_error(status: int, message: str) -> tuple[str, str]:
    """把 HTTP 错误翻译成「原因 + 建议」。

    Args:
        status: HTTP 状态码。
        message: 服务端返回的错误信息。

    Returns:
        ``(reason, suggestion)``。
    """
    text = (message or "").lower()
    detail = f"服务端返回 {status}: {message[:200]}" if message else f"服务端返回 {status}"

    if status == 400:
        if "model" in text or "模型" in text:
            return detail, "模型名不存在或不支持：检查 model 拼写（如 deepseek-chat / deepseek-reasoner）"
        return detail, "请求参数不被接受：常见原因是模型名错误，或该服务不支持当前参数"
    if status == 401:
        return detail, "API Key 无效：检查是否完整复制、是否有多余空格；部分服务要求 sk- 前缀"
    if status == 402:
        return detail, "账户余额不足：请到服务商控制台充值"
    if status == 403:
        return detail, "无权限：可能 Key 被禁用、IP 受限或该模型未开通"
    if status == 404:
        return detail, "接口路径不对：多数服务需要 /v1 前缀（如 https://api.deepseek.com/v1），本页会自动尝试补全"
    if status == 413:
        return detail, "请求体过大：题目或提示词太长"
    if status == 429:
        return detail, "触发限流：降低并发、增大搜题间隔，或查看账户配额"
    if status == 500:
        return detail, "服务端内部错误：稍后重试"
    if status == 502 or status == 503 or status == 504:
        return detail, "网关/服务不可用：稍后重试，或检查是否需要代理"
    return detail, "请对照服务商文档检查地址、Key 与模型名"


def diagnose_exception(exc: BaseException) -> tuple[str, str]:
    """把网络层异常翻译成「原因 + 建议」。"""
    if isinstance(exc, requests.exceptions.SSLError):
        return ("TLS/证书校验失败",
                "常见于公司网络/杀毒软件对 HTTPS 做了解密：请勾选「使用系统证书」；"
                "或用「代理」走内网出口；最后手段才是勾选「跳过证书校验」")
    if isinstance(exc, requests.exceptions.ProxyError):
        return "代理连接失败", "检查代理地址与端口是否可用，或清空代理配置"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "连接超时", "网络到该域名不通：检查是否需要代理、DNS 是否正常"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "等待响应超时", "服务端响应过慢：增大超时时间，或换更快的模型"
    if isinstance(exc, requests.exceptions.ConnectionError):
        text = str(exc)
        if "Name or service not known" in text or "getaddrinfo failed" in text or "Temporary failure" in text:
            return "域名解析失败", "检查地址拼写（域名是否写错），或本机 DNS/网络异常"
        if "Connection refused" in text or "actively refused" in text:
            return "连接被拒绝", "端口不对或本地服务未启动（本地模型请确认已运行）"
        return "无法建立连接", "检查网络、代理与防火墙设置"
    if isinstance(exc, requests.exceptions.InvalidURL):
        return "地址不合法", "检查是否包含多余字符或缺少协议头"
    return f"请求异常: {type(exc).__name__}: {exc}", "请检查网络与服务地址"


def parse_answer_text(content: str) -> Optional[str]:
    """把模型输出解析成引擎需要的答案文本。

    优先解析 ``{"Answer": [...]}``；失败则去掉 Markdown 包裹后返回原文，
    以便兼容非严格遵循 JSON 的模型。

    Args:
        content: 模型返回的文本。

    Returns:
        答案文本，空内容返回 None。
    """
    if not content or not str(content).strip():
        return None

    text = str(content).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(payload, dict):
        for key in ("Answer", "answer", "answers", "结果", "答案"):
            value = payload.get(key)
            if isinstance(value, list):
                joined = "\n".join(str(item).strip() for item in value if str(item).strip())
                return joined or None
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, list):
        joined = "\n".join(str(item).strip() for item in payload if str(item).strip())
        return joined or None
    return text


SYSTEM_PROMPTS = {
    "single": (
        "本题为单选题，你只能选择一个选项。请输出选项的完整内容（不要只给字母），"
        '以 JSON 格式回答：{"Answer": ["选项内容"]}。不要输出任何额外文字，不要使用 Markdown。'
    ),
    "multiple": (
        "本题为多选题，请选出所有正确选项。请输出每个选项的完整内容（不要只给字母），"
        '以 JSON 格式回答：{"Answer": ["选项1", "选项2"]}。不要输出任何额外文字，不要使用 Markdown。'
    ),
    "completion": (
        "本题为填空题，请直接给出应填入的内容。"
        '以 JSON 格式回答：{"Answer": ["答案文本"]}。不要输出任何额外文字，不要使用 Markdown。'
    ),
    "judgement": (
        "本题为判断题，只能回答“正确”或“错误”。"
        '以 JSON 格式回答：{"Answer": ["正确"]}。不要输出任何额外文字，不要使用 Markdown。'
    ),
    "short": (
        "本题为简答题，请给出简洁准确的答案。"
        '以 JSON 格式回答：{"Answer": ["答案文本"]}。不要输出任何额外文字，不要使用 Markdown。'
    ),
}


class AIClient:
    """OpenAI 兼容接口的最小客户端（只依赖 requests，便于精确定位错误）。"""

    def __init__(self, base_url: str, api_key: str = "", model: str = "",
                 timeout: float = DEFAULT_TIMEOUT, proxy: str = "",
                 temperature: float = 0.2, max_tokens: int = 1024,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 use_system_certs: bool = True, insecure: bool = False):
        """初始化客户端。

        Args:
            base_url: 用户填写的地址（会被自动规范化）。
            api_key: 密钥。
            model: 模型名。
            timeout: 单次请求超时（秒）。
            proxy: 可选代理，如 ``http://127.0.0.1:7890``。
            temperature: 采样温度。
            max_tokens: 最大输出 token 数。
            min_interval: 两次请求的最小间隔（秒）。
            use_system_certs: 使用操作系统证书库校验 TLS（公司代理/自签证书场景需要）。
            insecure: 跳过证书校验（危险，仅在无法信任证书时临时使用）。
        """
        self.raw_base_url = str(base_url or "").strip()
        self.base_url, self.candidates = normalize_base_url(self.raw_base_url)
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self.proxy = str(proxy or "").strip()
        self.temperature = float(temperature if temperature is not None else 0.2)
        self.max_tokens = int(max_tokens or 1024)
        self.min_interval = float(min_interval or 0)
        self.use_system_certs = bool(use_system_certs)
        self.insecure = bool(insecure)
        self._resolved_base: Optional[str] = None
        self._last_request = 0.0
        self._trust_ready = False

    def _ensure_trust(self) -> None:
        """让 requests 使用操作系统证书库。

        很多公司网络/沙箱会用代理做 TLS 解密，根证书只装在系统库里，
        而 requests 默认用 certifi 自带的 CA 列表，会直接报 CERTIFICATE_VERIFY_FAILED。
        """
        if self._trust_ready or self.insecure or not self.use_system_certs:
            return
        self._trust_ready = True
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001 - 没有 truststore 就沿用 certifi
            pass

    @property
    def verify(self) -> bool:
        """requests 的 verify 参数。"""
        return not self.insecure

    # ------------------------------------------------------------------ 基础
    @property
    def proxies(self) -> Optional[dict[str, str]]:
        """requests 使用的代理配置。"""
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _headers(self) -> dict[str, str]:
        """构造请求头。"""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _throttle(self) -> None:
        """按最小间隔节流。"""
        if not self.min_interval:
            return
        gap = time.time() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.time()

    def chat(self, messages: list[dict[str, str]], *, max_tokens: Optional[int] = None,
             temperature: Optional[float] = None) -> str:
        """调用 ``/chat/completions`` 并返回文本内容。

        Args:
            messages: 消息列表。
            max_tokens: 覆盖默认输出上限。
            temperature: 覆盖默认温度。

        Returns:
            模型返回的文本。

        Raises:
            AIRequestError: 请求失败或返回为空。
        """
        if not self.model:
            raise AIRequestError("未配置模型名", "在页面里填写 model，例如 deepseek-chat")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": int(max_tokens or self.max_tokens),
            "stream": False,
        }
        if self.model.lower().startswith("deepseek-v4"):
            # DeepSeek V4 默认思考模式可能返回空 content，这里显式关闭
            payload["thinking"] = {"type": "disabled"}

        bases = [self._resolved_base] if self._resolved_base else self.candidates
        last_error: Optional[AIRequestError] = None
        self._ensure_trust()

        for base in bases:
            if not base:
                continue
            url = f"{base}/chat/completions"
            self._throttle()
            try:
                resp = requests.post(url, headers=self._headers(), json=payload,
                                     timeout=self.timeout, proxies=self.proxies, verify=self.verify)
            except requests.exceptions.RequestException as exc:
                reason, suggestion = diagnose_exception(exc)
                raise AIRequestError(reason, suggestion) from exc

            if resp.status_code == 200:
                self._resolved_base = base
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise AIRequestError(
                        "返回内容不是 JSON", "地址可能指向了网页/网关，请确认是 OpenAI 兼容接口", 200,
                        resp.text,
                    ) from exc

                choices = data.get("choices") or []
                if not choices:
                    raise AIRequestError("响应中没有 choices", "模型或该接口返回格式不兼容", 200,
                                         json.dumps(data, ensure_ascii=False))
                message = choices[0].get("message") or {}
                content = message.get("content") or message.get("reasoning_content") or ""
                if not str(content).strip():
                    raise AIRequestError("模型返回空内容", "换一个模型，或检查该模型是否为推理型（需要更长 max_tokens）",
                                         200, json.dumps(data, ensure_ascii=False))
                return str(content)

            if resp.status_code == 404:
                last_error = AIRequestError(*diagnose_http_error(404, _extract_error(resp)[1]), 404, resp.text)
                continue  # 换下一个候选地址（自动补 /v1）

            code, message = _extract_error(resp)
            reason, suggestion = diagnose_http_error(resp.status_code, message or code)
            raise AIRequestError(reason, suggestion, resp.status_code, resp.text)

        raise last_error or AIRequestError("接口不可用", "检查地址、Key 与模型名")

    # ------------------------------------------------------------------ 自检
    def test(self) -> TestReport:
        """执行分步自检并返回报告。

        步骤：地址解析 → 连通性/鉴权（/models）→ 答题链路（/chat/completions）。

        Returns:
            :class:`TestReport`。
        """
        report = TestReport(base_url=self.raw_base_url, model=self.model)
        started = time.time()
        self._ensure_trust()

        # 1. 地址解析（本地）
        step_started = time.time()
        try:
            report.base_url = self.base_url
            report.steps.append(Step("地址解析", True,
                                     f"规范化为 {self.base_url}；候选 {', '.join(self.candidates)}",
                                     int((time.time() - step_started) * 1000)))
        except Exception as exc:  # noqa: BLE001 - 兜底
            report.steps.append(Step("地址解析", False, str(exc), 0))
            report.reason = "地址不合法"
            report.suggestion = "请填写完整的 http(s) 地址"
            return report

        # 2. 连通性 + 鉴权（/models，部分服务不支持）
        step_started = time.time()
        models_ok = False
        models_note = ""
        for base in self.candidates:
            try:
                resp = requests.get(f"{base}/models", headers=self._headers(),
                                    timeout=min(self.timeout, 20), proxies=self.proxies,
                                    verify=self.verify)
            except requests.exceptions.RequestException as exc:
                reason, suggestion = diagnose_exception(exc)
                models_note = f"{base}/models 请求失败：{reason}"
                report.reason = reason
                report.suggestion = suggestion
                continue

            if resp.status_code == 200:
                models_ok = True
                report.resolved_base = base
                try:
                    names = [item.get("id") for item in (resp.json().get("data") or []) if isinstance(item, dict)]
                except (ValueError, AttributeError):
                    names = []
                if names:
                    matched = self.model in names
                    models_note = f"{base}/models 可用，返回 {len(names)} 个模型" + (
                        "" if matched or not self.model else f"；未在列表中找到 {self.model}（仍可能可用）")
                else:
                    models_note = f"{base}/models 可用"
                break

            if resp.status_code in (401, 403):
                code, message = _extract_error(resp)
                reason, suggestion = diagnose_http_error(resp.status_code, message or code)
                models_note = f"{base}/models 鉴权失败：{reason}"
                report.reason = reason
                report.suggestion = suggestion
                break

            if resp.status_code == 404:
                models_note = f"{base}/models 不存在（服务可能不提供该接口），继续测试答题接口"
                continue

            code, message = _extract_error(resp)
            reason, suggestion = diagnose_http_error(resp.status_code, message or code)
            models_note = f"{base}/models 返回 {resp.status_code}"
            report.reason = reason
            report.suggestion = suggestion

        report.steps.append(Step(
            "连通性与鉴权（GET /models）",
            True if models_ok else (None if not report.reason else False),
            models_note or "未执行",
            int((time.time() - step_started) * 1000),
        ))
        if report.reason and not models_ok:
            # 鉴权类错误无需继续
            report.elapsed_ms = int((time.time() - started) * 1000)
            return report

        # 3. 答题链路（/chat/completions）
        step_started = time.time()
        try:
            content = self.chat(
                [{"role": "user", "content": "请只回答一个字符：1+1 等于几？"}],
                max_tokens=64,
            )
        except AIRequestError as exc:
            report.steps.append(Step("答题接口（POST /chat/completions）", False, exc.reason,
                                     int((time.time() - step_started) * 1000)))
            report.reason = exc.reason
            report.suggestion = exc.suggestion
            report.elapsed_ms = int((time.time() - started) * 1000)
            return report

        report.sample = content.strip()[:60]
        report.steps.append(Step("答题接口（POST /chat/completions）", True,
                                 f"返回内容：{report.sample}",
                                 int((time.time() - step_started) * 1000)))
        report.steps.append(Step("答题结果解析", True,
                                 "可正常解析为答案", 0))
        report.ok = True
        report.elapsed_ms = int((time.time() - started) * 1000)
        return report

    # ------------------------------------------------------------------ 答题
    def answer(self, question: dict[str, Any], feedback: Optional[str] = None) -> Optional[str]:
        """回答一道题。

        Args:
            question: 引擎解析出的题目，含 ``title`` / ``type`` / ``options``。
            feedback: 上一轮答错的反馈文本（重做模式使用）。

        Returns:
            答案文本；失败返回 None。
        """
        q_type = question.get("type") or "single"
        title = str(question.get("title") or "").strip()
        options = question.get("options")

        if isinstance(options, str):
            option_text = options
        elif isinstance(options, list):
            option_text = "\n".join(str(item) for item in options)
        else:
            option_text = ""

        # 去掉 "A. " 前缀，避免模型直接回字母
        if q_type in ("single", "multiple") and option_text:
            lines = [re.sub(r"^[A-Za-z]\s*[.、:：)）]?\s*", "", line) for line in option_text.split("\n") if line.strip()]
            option_text = "\n".join(lines)

        system = SYSTEM_PROMPTS.get(q_type, SYSTEM_PROMPTS["short"])
        user_parts = [f"题目：{title}"]
        if option_text and q_type in ("single", "multiple"):
            user_parts.append(f"选项：\n{option_text}")

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if feedback:
            messages.append({"role": "system", "content": str(feedback)})
        messages.append({"role": "user", "content": "\n".join(user_parts)})

        try:
            content = self.chat(messages)
        except AIRequestError as exc:
            logger.error(f"AI 答题失败：{exc.reason}（{exc.suggestion}）")
            return None

        answer = parse_answer_text(content)
        if not answer:
            logger.error("AI 返回内容为空，已放弃该题")
            return None
        return answer
