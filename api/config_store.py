# -*- coding: utf-8 -*-
"""配置中心：Web 控制台的 JSON 设置与 CLI 的 ini 配置之间的唯一映射。

设计目标：
* Web 端只操作一份 JSON（``~/.chaoxing/settings.json``），人类可读、可手改；
* 引擎仍然吃原来那套 dict/ini 结构，由本模块负责补齐与类型转换；
* 支持从已有的 ``config.ini`` 导入，保证老用户无缝迁移。
"""
from __future__ import annotations

import configparser
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "chaoxing"
_SETTINGS_FILENAME = "settings.json"
_home_cache: Path | None = None

DEFAULT_SETTINGS: dict[str, Any] = {
    "common": {
        "username": "",
        "password": "",
        "use_cookies": False,
        "course_list": [],
        "speed": 1.0,
        "jobs": 1,
        "notopen_action": "retry",
        "retry_interval": 1.0,
        "work_redo_enabled": False,
        "work_max_retries": 3,
        "add_learning_count": False,
        "target_count": 100,
    },
    "tiku": {
        "provider": "TikuYanxi",
        "submit": False,
        "cover_rate": 0.9,
        "delay": 1.0,
        "tokens": "",
        "url": "",
        "true_list": "正确,对,√,是",
        "false_list": "错误,错,×,否,不对,不正确",
        "min_interval_seconds": 3,
        "http_proxy": "",
        "endpoint": "",
        "key": "",
        "model": "",
        "check_llm_connection": True,
        "siliconflow_key": "",
        "siliconflow_model": "deepseek-ai/DeepSeek-V3",
        "siliconflow_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "likeapi_search": False,
        "likeapi_vision": True,
        "likeapi_model": "glm-4.5-air",
        "likeapi_retry": True,
        "likeapi_retry_times": 3,
        "go_authorization": "",
        "go_min_interval": 1.0,
        "go_retry_times": 3,
        "go_retry_backoff": 1.2,
        "manual_mode_default": "batch",
        "manual_mode_separator": ";",
    },
    "notification": {
        "provider": "ServerChan",
        "url": "",
        "tg_chat_id": "",
    },
    "ai": {
        "preset": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 1024,
        "timeout": 60,
        "proxy": "",
        "min_interval_seconds": 3,
        "use_system_certs": True,
        "insecure": False,
        "use_for_answer": False,
    },
}

TIKU_PROVIDERS = [
    "TikuYanxi",
    "TikuGo",
    "TikuLike",
    "TikuAdapter",
    "AI",
    "TikuAI",
    "SiliconFlow",
    "TikuManual",
]

NOTIFICATION_PROVIDERS = ["ServerChan", "Qmsg", "Bark", "Telegram"]

# 题库 / AI 的中文名，用于界面展示「答题方式」
PROVIDER_LABELS: dict[str, str] = {
    "TikuYanxi": "言溪题库",
    "TikuGo": "GO题",
    "TikuLike": "LIKE 知识库",
    "TikuAdapter": "TikuAdapter",
    "AI": "AI 大模型（旧）",
    "TikuAI": "AI 答题",
    "SiliconFlow": "硅基流动",
    "TikuManual": "手动输入",
}

ANSWER_MODES = ["ai_only", "ai_first", "tiku_first", "tiku_only"]

_NOT_OPEN_ACTIONS = ("retry", "ask", "continue")


def app_home() -> Path:
    """返回应用数据目录。

    优先 ``CHAOXING_HOME``，其次 ``~/.chaoxing``，再到 ``%LOCALAPPDATA%\\chaoxing``，
    最后退到系统临时目录 —— 某些受限环境（如企业策略、沙箱）下用户主目录可能不可写。
    选定后会做一次写探针，确保目录真的可用。
    """
    global _home_cache
    if _home_cache is not None:
        return _home_cache

    candidates: list[Path] = []
    override = os.environ.get("CHAOXING_HOME")
    if override:
        candidates.append(Path(override))
    candidates.append(Path.home() / f".{APP_NAME}")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / APP_NAME)
    candidates.append(Path(tempfile.gettempdir()) / APP_NAME)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:  # 目录不可写，换下一个候选
            last_error = exc
            continue
        _home_cache = candidate
        return candidate

    raise RuntimeError(f"无法创建应用数据目录（已尝试 {len(candidates)} 个位置）: {last_error}")


def settings_path() -> Path:
    """设置文件路径。"""
    return app_home() / _SETTINGS_FILENAME


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并，override 优先，缺失的键用 base 补齐。

    注意对 base 做深拷贝：``DEFAULT_SETTINGS`` 是模块级常量，绝不能被子调用原地改写。
    """
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize(settings: dict[str, Any] | None) -> dict[str, Any]:
    """补齐默认值并做类型收敛，避免脏数据打进引擎。"""
    merged = _deep_merge(DEFAULT_SETTINGS, settings or {})
    common = merged["common"]
    common["speed"] = min(2.0, max(1.0, _as_float(common.get("speed"), 1.0)))
    common["jobs"] = max(1, min(16, _as_int(common.get("jobs"), 1)))
    common["retry_interval"] = max(0.0, _as_float(common.get("retry_interval"), 1.0))
    common["work_max_retries"] = max(0, _as_int(common.get("work_max_retries"), 3))
    common["target_count"] = max(1, _as_int(common.get("target_count"), 100))
    common["notopen_action"] = common.get("notopen_action") if common.get("notopen_action") in _NOT_OPEN_ACTIONS else "retry"
    if not isinstance(common.get("course_list"), list):
        common["course_list"] = []
    common["use_cookies"] = _as_bool(common.get("use_cookies"))
    common["work_redo_enabled"] = _as_bool(common.get("work_redo_enabled"))
    common["add_learning_count"] = _as_bool(common.get("add_learning_count"))

    tiku = merged["tiku"]
    if tiku.get("provider") not in TIKU_PROVIDERS:
        # 允许 TikuGo,TikuYanxi 这类回退链
        chain = [p.strip() for p in str(tiku.get("provider") or "").split(",") if p.strip()]
        if not chain or any(p not in TIKU_PROVIDERS for p in chain):
            tiku["provider"] = DEFAULT_SETTINGS["tiku"]["provider"]
    tiku["submit"] = _as_bool(tiku.get("submit"))
    tiku["check_llm_connection"] = _as_bool(tiku.get("check_llm_connection"))
    tiku["cover_rate"] = min(1.0, max(0.0, _as_float(tiku.get("cover_rate"), 0.9)))
    tiku["delay"] = max(0.0, _as_float(tiku.get("delay"), 1.0))

    notification = merged["notification"]
    if notification.get("provider") not in NOTIFICATION_PROVIDERS:
        notification["provider"] = DEFAULT_SETTINGS["notification"]["provider"]

    ai = merged["ai"]
    ai["base_url"] = str(ai.get("base_url") or "").strip()
    ai["api_key"] = str(ai.get("api_key") or "").strip()
    ai["model"] = str(ai.get("model") or "").strip()
    ai["proxy"] = str(ai.get("proxy") or "").strip()
    ai["temperature"] = min(2.0, max(0.0, _as_float(ai.get("temperature"), 0.2)))
    ai["max_tokens"] = max(64, _as_int(ai.get("max_tokens"), 1024))
    ai["timeout"] = max(5, _as_int(ai.get("timeout"), 60))
    ai["min_interval_seconds"] = max(0.0, _as_float(ai.get("min_interval_seconds"), 3.0))
    ai["use_for_answer"] = _as_bool(ai.get("use_for_answer"))
    ai["use_system_certs"] = _as_bool(ai.get("use_system_certs", True))
    ai["insecure"] = _as_bool(ai.get("insecure"))

    # 启用 AI 答题时，把 TikuAI 放到回退链最前面（题库仍然可以兜底）
    if ai["use_for_answer"]:
        chain = [p.strip() for p in str(tiku.get("provider") or "").split(",") if p.strip()]
        if "TikuAI" not in chain:
            tiku["provider"] = ",".join(["TikuAI", *chain]) if chain else "TikuAI"

    return merged


def load_settings() -> dict[str, Any]:
    """读取设置；文件不存在或损坏时回落到默认值。"""
    path = settings_path()
    if not path.is_file():
        return normalize({})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return normalize({})
    return normalize(data)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """原子写入设置并返回规范化后的结果。"""
    normalized = normalize(settings)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(normalized, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return normalized


def to_tiku_conf(tiku: dict[str, Any], ai: dict[str, Any] | None = None) -> dict[str, str]:
    """把 JSON 题库设置转成 ``configparser`` 风格的字符串字典。

    ``Tiku`` 系列实现全部按字符串解析配置（如 ``submit == 'true'``、``float(cover_rate)``），
    所以这里统一转字符串，且布尔值必须是小写 ``true`` / ``false``。

    Args:
        tiku: ``settings["tiku"]``。
        ai: ``settings["ai"]``，会以 ``ai_*`` 前缀注入，供 ``TikuAI`` 使用。
    """
    conf: dict[str, str] = {}
    for key, value in (tiku or {}).items():
        if isinstance(value, bool):
            conf[key] = "true" if value else "false"
        elif value is None:
            conf[key] = ""
        else:
            conf[key] = str(value)

    if ai is not None:
        conf.update({
            "ai_base_url": str(ai.get("base_url") or ""),
            "ai_key": str(ai.get("api_key") or ""),
            "ai_model": str(ai.get("model") or ""),
            "ai_temperature": str(ai.get("temperature", 0.2)),
            "ai_max_tokens": str(ai.get("max_tokens", 1024)),
            "ai_timeout": str(ai.get("timeout", 60)),
            "ai_proxy": str(ai.get("proxy") or ""),
            "ai_min_interval": str(ai.get("min_interval_seconds", 3)),
            "ai_use_system_certs": "true" if _as_bool(ai.get("use_system_certs", True)) else "false",
            "ai_insecure": "true" if _as_bool(ai.get("insecure")) else "false",
        })
    return conf


def to_common_conf(common: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 通用设置转成引擎使用的类型（列表/浮点/整数/布尔）。"""
    conf = dict(common or {})
    conf["course_list"] = [str(item) for item in (conf.get("course_list") or []) if str(item).strip()]
    conf["speed"] = _as_float(conf.get("speed"), 1.0)
    conf["jobs"] = _as_int(conf.get("jobs"), 4)
    conf["retry_interval"] = _as_float(conf.get("retry_interval"), 1.0)
    conf["add_learning_count"] = _as_bool(conf.get("add_learning_count"))
    conf["target_count"] = _as_int(conf.get("target_count"), 100)
    conf["use_cookies"] = _as_bool(conf.get("use_cookies"))
    conf["work_redo_enabled"] = _as_bool(conf.get("work_redo_enabled"))
    conf["work_max_retries"] = _as_int(conf.get("work_max_retries"), 3)
    return conf


def export_ini(path: str | os.PathLike[str]) -> Path:
    """把当前 JSON 设置导出成 CLI 可用的 config.ini。"""
    settings = load_settings()
    common, tiku, notification = to_common_conf(settings["common"]), settings["tiku"], settings["notification"]

    parser = configparser.ConfigParser()
    parser["common"] = {
        "use_cookies": str(common["use_cookies"]).lower(),
        "username": str(common.get("username") or ""),
        "password": str(common.get("password") or ""),
        "course_list": ",".join(common["course_list"]),
        "speed": str(common["speed"]),
        "jobs": str(common["jobs"]),
        "notopen_action": str(common.get("notopen_action") or "retry"),
        "retry_interval": str(common["retry_interval"]),
        "work_redo_enabled": str(common["work_redo_enabled"]).lower(),
        "work_max_retries": str(common["work_max_retries"]),
        "add_learning_count": str(common["add_learning_count"]).lower(),
        "target_count": str(common["target_count"]),
    }
    parser["tiku"] = to_tiku_conf(tiku, settings["ai"])
    parser["notification"] = {key: str(value) for key, value in notification.items()}

    target = Path(path)
    with target.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return target


def import_ini(path: str | os.PathLike[str]) -> dict[str, Any]:
    """从已有 ``config.ini`` 导入设置（缺失项保留默认值）。"""
    parser = configparser.ConfigParser()
    parser.read(str(path), encoding="utf-8")

    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if parser.has_section("common"):
        section = dict(parser.items("common"))
        common = settings["common"]
        for key in ("username", "password"):
            if key in section:
                common[key] = section[key]
        if section.get("course_list"):
            common["course_list"] = [item.strip() for item in section["course_list"].split(",") if item.strip()]
        if "use_cookies" in section:
            common["use_cookies"] = _as_bool(section["use_cookies"])
        if "speed" in section:
            common["speed"] = _as_float(section["speed"], 1.0)
        if "jobs" in section:
            common["jobs"] = _as_int(section["jobs"], 4)
        if "notopen_action" in section:
            common["notopen_action"] = section["notopen_action"]
        if "retry_interval" in section:
            common["retry_interval"] = _as_float(section["retry_interval"], 1.0)
        if "work_redo_enabled" in section:
            common["work_redo_enabled"] = _as_bool(section["work_redo_enabled"])
        if "work_max_retries" in section:
            common["work_max_retries"] = _as_int(section["work_max_retries"], 3)
        if "add_learning_count" in section:
            common["add_learning_count"] = _as_bool(section["add_learning_count"])
        if "target_count" in section:
            common["target_count"] = _as_int(section["target_count"], 100)

    if parser.has_section("tiku"):
        section = dict(parser.items("tiku"))
        tiku = settings["tiku"]
        for key in tiku:
            if key in section:
                if isinstance(DEFAULT_SETTINGS["tiku"][key], bool):
                    tiku[key] = _as_bool(section[key])
                elif isinstance(DEFAULT_SETTINGS["tiku"][key], (int, float)):
                    number = _as_float(section[key], DEFAULT_SETTINGS["tiku"][key])
                    tiku[key] = int(number) if isinstance(DEFAULT_SETTINGS["tiku"][key], int) else number
                else:
                    tiku[key] = section[key]
        if "provider" in section:
            tiku["provider"] = section["provider"]

        # 老配置里的 AI 参数（旧 AI 题库）迁移到 ai 段
        ai = settings["ai"]
        if section.get("endpoint") and not section.get("ai_base_url"):
            ai["base_url"] = section["endpoint"]
        if section.get("key") and not section.get("ai_key"):
            ai["api_key"] = section["key"]
        if section.get("model") and not section.get("ai_model"):
            ai["model"] = section["model"]

        # 新配置：ai_* 前缀
        ai_mapping = {
            "ai_base_url": ("base_url", str),
            "ai_key": ("api_key", str),
            "ai_model": ("model", str),
            "ai_proxy": ("proxy", str),
            "ai_temperature": ("temperature", float),
            "ai_max_tokens": ("max_tokens", int),
            "ai_timeout": ("timeout", int),
            "ai_min_interval": ("min_interval_seconds", float),
            "ai_use_system_certs": ("use_system_certs", _as_bool),
            "ai_insecure": ("insecure", _as_bool),
        }
        for source, (target, caster) in ai_mapping.items():
            if source in section:
                try:
                    ai[target] = caster(section[source])
                except (TypeError, ValueError):
                    pass
        if "TikuAI" in str(tiku.get("provider") or ""):
            ai["use_for_answer"] = True

    if parser.has_section("notification"):
        section = dict(parser.items("notification"))
        notification = settings["notification"]
        for key in notification:
            if key in section:
                notification[key] = section[key]

    return normalize(settings)


def parse_provider_chain(provider: Any) -> list[str]:
    """把 ``provider`` 字符串拆成有序列表。"""
    return [name.strip() for name in str(provider or "").split(",") if name.strip()]


def ai_ready(settings: dict[str, Any]) -> bool:
    """AI 答题是否已具备可用配置（地址 + 模型 + Key）。"""
    ai = settings.get("ai") or {}
    return bool(str(ai.get("base_url") or "").strip()
                and str(ai.get("model") or "").strip()
                and str(ai.get("api_key") or "").strip())


def answer_plan(settings: dict[str, Any]) -> dict[str, Any]:
    """根据题库回退链推导「当前会用什么答题」。

    Returns:
        含 ``mode`` / ``label`` / ``text`` / ``chain`` / ``warning`` 的字典，
        供前端与任务日志展示。
    """
    chain = parse_provider_chain((settings.get("tiku") or {}).get("provider"))
    labels = [PROVIDER_LABELS.get(name, name) for name in chain]
    ready = ai_ready(settings)

    if not chain:
        mode, label = "none", "未配置（章节测验将被跳过）"
    elif chain == ["TikuAI"]:
        mode, label = "ai_only", "仅 AI 答题"
    elif chain[0] == "TikuAI" and len(chain) > 1:
        mode, label = "ai_first", "AI 优先，题库兜底"
    elif chain[-1] == "TikuAI" and len(chain) > 1:
        mode, label = "tiku_first", "题库优先，AI 兜底"
    elif "TikuAI" in chain:
        mode, label = "ai_first", "AI 优先，题库兜底"
    else:
        mode, label = "tiku_only", "仅题库"

    warning = ""
    if mode in ("ai_only", "ai_first") and not ready:
        warning = "AI 答题还没配置好（缺地址/模型/Key），遇到测验可能答不出来"
    if mode == "tiku_only" and not chain:
        warning = "没有可用题库"

    return {
        "mode": mode,
        "label": label,
        "provider": ",".join(chain),
        "chain": [{"name": name, "label": PROVIDER_LABELS.get(name, name)} for name in chain],
        "text": " → ".join(labels) if labels else "无",
        "has_ai": "TikuAI" in chain,
        "has_tiku": any(name != "TikuAI" for name in chain),
        "ai_ready": ready,
        "warning": warning,
    }


def apply_answer_mode(settings: dict[str, Any], mode: str) -> dict[str, Any]:
    """按「答题方式」重写 ``tiku.provider`` 回退链。

    Args:
        settings: 当前设置（不会被就地修改）。
        mode: ``ai_only`` / ``ai_first`` / ``tiku_first`` / ``tiku_only``。

    Returns:
        更新后的设置。

    Raises:
        ValueError: 模式非法，或选择「仅题库」但没有任何题库。
    """
    if mode not in ANSWER_MODES:
        raise ValueError(f"未知答题方式: {mode}")

    updated = copy.deepcopy(settings)
    chain = parse_provider_chain((updated.get("tiku") or {}).get("provider"))
    others = [name for name in chain if name != "TikuAI"]

    if mode == "ai_only":
        new_chain = ["TikuAI"]
    elif mode == "ai_first":
        new_chain = ["TikuAI", *others]
    elif mode == "tiku_first":
        new_chain = [*others, "TikuAI"]
    else:  # tiku_only
        new_chain = others
        if not new_chain:
            raise ValueError("当前没有任何题库，请先在「题库配置」里选择至少一个题库")

    updated.setdefault("tiku", {})["provider"] = ",".join(new_chain)
    return normalize(updated)


def _as_bool(value: Any) -> bool:
    """宽松布尔解析。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
def _as_int(value: Any, default: int) -> int:
    """宽松整数解析。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    """宽松浮点解析。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
