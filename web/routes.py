# -*- coding: utf-8 -*-
"""REST + SSE 路由。"""
from __future__ import annotations

import csv
import io
import json
import queue
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

from api.config_store import (
    TIKU_PROVIDERS,
    export_ini,
    import_ini,
    settings_path,
    to_tiku_conf,
)
from api.logger import logger

from web import store
from web.runtime import AuthRequired, get_runtime

api = Blueprint("api", __name__, url_prefix="/api")


# --------------------------------------------------------------------- 工具
def _runtime():
    """获取运行时单例。"""
    return get_runtime()


def _json_error(message: str, status: int = 400):
    """统一的错误响应。"""
    return jsonify({"ok": False, "msg": message}), status


@api.errorhandler(AuthRequired)
def _handle_auth(exc: AuthRequired):
    """未登录。"""
    return _json_error(str(exc), 401)


@api.errorhandler(PermissionError)
def _handle_permission(exc: PermissionError):
    """文件/系统权限问题，明确告知而不是伪装成未登录。"""
    return _json_error(f"权限不足: {exc}", 500)


@api.errorhandler(RuntimeError)
def _handle_runtime(exc: RuntimeError):
    """状态冲突（如已有任务在跑）。"""
    return _json_error(str(exc), 409)


@api.errorhandler(ValueError)
def _handle_value(exc: ValueError):
    """参数错误。"""
    return _json_error(str(exc), 400)


def _trim_course(course: dict[str, Any]) -> dict[str, Any]:
    """裁剪课程字段，前端只需要这几项。"""
    return {
        "courseId": course.get("courseId"),
        "clazzId": course.get("clazzId"),
        "cpi": course.get("cpi"),
        "title": course.get("title"),
        "teacher": course.get("teacher"),
        "desc": course.get("desc"),
    }


def _sse(event: str, data: dict[str, Any]) -> str:
    """序列化一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------- 基础
@api.get("/health")
def health():
    """健康检查。"""
    runtime = _runtime()
    return jsonify({"ok": True, "time": time.time(), **runtime.snapshot()})


@api.get("/meta")
def meta():
    """前端需要的枚举与路径信息。"""
    from api.ai_client import PROVIDER_PRESETS

    return jsonify({
        "ok": True,
        "tiku_providers": TIKU_PROVIDERS,
        "notopen_actions": ["retry", "ask", "continue"],
        "settings_path": str(settings_path()),
        "ai_presets": {
            key: {"label": value["label"], "base_url": value["base_url"], "model": value["model"],
                  "hint": value["hint"], "key_url": value.get("key_url", "")}
            for key, value in PROVIDER_PRESETS.items()
        },
    })


# --------------------------------------------------------------------- 登录
@api.post("/auth/login")
def login():
    """登录超星。"""
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    use_cookies = bool(payload.get("use_cookies") or False)

    if not use_cookies and not username:
        return _json_error("请输入手机号")
    if not use_cookies and not password:
        return _json_error("请输入密码")

    runtime = _runtime()
    result = runtime.login(username, password, use_cookies=use_cookies)
    return jsonify(result), (200 if result["ok"] else 401)


@api.get("/auth/me")
def me():
    """当前登录态。"""
    runtime = _runtime()
    return jsonify({"ok": True, **runtime.snapshot()})


@api.post("/auth/logout")
def logout():
    """登出。"""
    _runtime().logout()
    return jsonify({"ok": True})


# --------------------------------------------------------------------- 课程
@api.get("/courses")
def courses():
    """拉取课程列表。"""
    runtime = _runtime()
    chaoxing = runtime.require_login()
    try:
        raw = chaoxing.get_course_list()
    except Exception as exc:  # noqa: BLE001 - 网络异常统一转 502
        logger.error(f"获取课程列表失败: {exc}")
        return _json_error(f"获取课程列表失败: {exc}", 502)
    return jsonify({"ok": True, "courses": [_trim_course(c) for c in raw]})


@api.get("/courses/<course_id>/<clazz_id>/points")
def course_points(course_id: str, clazz_id: str):
    """预览某课程的章节列表。"""
    cpi = request.args.get("cpi", "")
    runtime = _runtime()
    chaoxing = runtime.require_login()
    try:
        data = chaoxing.get_course_point(course_id, clazz_id, cpi)
    except Exception as exc:  # noqa: BLE001 - 网络异常统一转 502
        logger.error(f"获取章节失败: {exc}")
        return _json_error(f"获取章节失败: {exc}", 502)
    points = [
        {
            "id": p.get("id"),
            "title": p.get("title"),
            "jobCount": p.get("jobCount"),
            "has_finished": p.get("has_finished"),
            "need_unlock": p.get("need_unlock"),
        }
        for p in data.get("points", [])
    ]
    return jsonify({"ok": True, "hasLocked": data.get("hasLocked", False), "points": points})


# --------------------------------------------------------------------- 设置
@api.get("/settings")
def get_settings():
    """读取设置。"""
    return jsonify({"ok": True, "settings": _runtime().settings})


@api.put("/settings")
def put_settings():
    """保存设置（整体覆盖式合并）。"""
    payload = request.get_json(silent=True) or {}
    runtime = _runtime()
    settings = runtime.update_settings(payload)
    return jsonify({"ok": True, "settings": settings})


@api.post("/settings/export-ini")
def export_settings_ini():
    """把当前设置导出为 CLI 可用的 config.ini。"""
    from api.config_store import app_home

    target = export_ini(app_home() / "config.ini")
    return jsonify({"ok": True, "path": str(target)})


@api.post("/settings/import-ini")
def import_settings_ini():
    """从 config.ini 导入设置。"""
    payload = request.get_json(silent=True) or {}
    path = str(payload.get("path") or "").strip()
    if not path:
        return _json_error("请提供 config.ini 路径")
    try:
        settings = import_ini(path)
    except FileNotFoundError:
        return _json_error(f"文件不存在: {path}", 404)
    runtime = _runtime()
    runtime.settings = runtime.update_settings(settings)
    return jsonify({"ok": True, "settings": runtime.settings})


# --------------------------------------------------------------------- 答题方式
@api.get("/answer-plan")
def get_answer_plan():
    """当前会用「题库」还是「AI」答题，顺序是什么。"""
    return jsonify({"ok": True, "plan": _runtime().current_plan()})


@api.post("/answer-plan")
def set_answer_plan():
    """切换答题方式：ai_only / ai_first / tiku_first / tiku_only。"""
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "").strip()
    if not mode:
        return _json_error("缺少 mode 参数")
    plan = _runtime().set_answer_mode(mode)
    return jsonify({"ok": True, "plan": plan})


# --------------------------------------------------------------------- 题库
@api.post("/tiku/test")
def tiku_test():
    """题库连通性自检：连通性检查 + 一道样例题。"""
    runtime = _runtime()
    tiku_conf = to_tiku_conf(runtime.settings["tiku"], runtime.settings.get("ai"))
    provider = str(tiku_conf.get("provider") or "")
    if not provider:
        return _json_error("未配置题库 provider")

    if provider == "TikuManual" or "TikuManual" in provider:
        return jsonify({"ok": True, "provider": provider, "answer": "",
                        "msg": "手动题库无需联网测试"}), 200

    from api.answer import Tiku

    started = time.time()
    try:
        tiku = Tiku.get_tiku_from_config(tiku_conf)
        tiku.init_tiku()
    except Exception as exc:  # noqa: BLE001 - 初始化失败要回给前端
        return jsonify({"ok": False, "provider": provider, "msg": f"题库初始化失败: {exc}"}), 200

    if getattr(tiku, "DISABLE", False):
        return jsonify({"ok": False, "provider": provider,
                        "msg": "题库已禁用：provider 名称或配置不正确"}), 200

    try:
        if not tiku.check_llm_connection():
            return jsonify({"ok": False, "provider": provider,
                            "msg": "大模型连接检查失败", "elapsed": round(time.time() - started, 1)}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "provider": provider, "msg": f"连接检查异常: {exc}"}), 200

    sample = {
        "title": "【连接测试】1+1 等于多少？（此题仅用于自检，可忽略）",
        "type": "single",
        "options": "A. 1\nB. 2",
        "id": "0",
    }
    try:
        answer = tiku.query(sample)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "provider": provider, "msg": f"搜题异常: {exc}"}), 200

    elapsed = round(time.time() - started, 1)
    if answer:
        return jsonify({"ok": True, "provider": provider, "answer": answer,
                        "msg": "题库可用", "elapsed": elapsed}), 200
    return jsonify({"ok": False, "provider": provider, "answer": "",
                    "msg": "题库未返回答案（token/额度/配置可能有误）", "elapsed": elapsed}), 200


# --------------------------------------------------------------------- AI 答题
@api.post("/ai/test")
def ai_test():
    """AI 答题分步自检。

    请求体可带 ``{"ai": {...}}`` 做「未保存先测试」，不带则用已保存配置。
    """
    from api.ai_client import AIClient

    runtime = _runtime()
    payload = request.get_json(silent=True) or {}
    ai_conf = dict(runtime.settings.get("ai") or {})
    if isinstance(payload.get("ai"), dict):
        ai_conf.update(payload["ai"])

    base_url = str(ai_conf.get("base_url") or "").strip()
    model = str(ai_conf.get("model") or "").strip()
    if not base_url:
        return jsonify({"ok": False, "reason": "未填写 API 地址",
                        "suggestion": "填写服务商地址，例如 https://api.deepseek.com/v1",
                        "steps": []}), 200
    if not model:
        return jsonify({"ok": False, "reason": "未填写模型名",
                        "suggestion": "例如 DeepSeek 填 deepseek-chat", "steps": []}), 200

    try:
        client = AIClient(
            base_url=base_url,
            api_key=str(ai_conf.get("api_key") or ""),
            model=model,
            timeout=float(ai_conf.get("timeout") or 60),
            proxy=str(ai_conf.get("proxy") or ""),
            temperature=float(ai_conf.get("temperature") or 0.2),
            max_tokens=int(ai_conf.get("max_tokens") or 1024),
            min_interval=float(ai_conf.get("min_interval_seconds") or 0),
            use_system_certs=bool(ai_conf.get("use_system_certs", True)),
            insecure=bool(ai_conf.get("insecure", False)),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "reason": str(exc),
                        "suggestion": "请检查地址格式，需以 http:// 或 https:// 开头", "steps": []}), 200

    report = client.test()
    return jsonify(report.as_dict()), 200


@api.post("/ai/answer-sample")
def ai_answer_sample():
    """用已保存的 AI 配置真实答一道示例题，验证答题链路。"""
    from api.ai_client import AIClient, AIRequestError

    runtime = _runtime()
    ai_conf = runtime.settings.get("ai") or {}
    try:
        client = AIClient(
            base_url=str(ai_conf.get("base_url") or ""),
            api_key=str(ai_conf.get("api_key") or ""),
            model=str(ai_conf.get("model") or ""),
            timeout=float(ai_conf.get("timeout") or 60),
            proxy=str(ai_conf.get("proxy") or ""),
            temperature=float(ai_conf.get("temperature") or 0.2),
            max_tokens=int(ai_conf.get("max_tokens") or 1024),
            min_interval=float(ai_conf.get("min_interval_seconds") or 0),
            use_system_certs=bool(ai_conf.get("use_system_certs", True)),
            insecure=bool(ai_conf.get("insecure", False)),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "msg": str(exc)}), 200

    question = {
        "title": "中国的首都是哪座城市？",
        "type": "single",
        "options": "A. 上海\nB. 北京\nC. 广州",
    }
    started = time.time()
    answer = client.answer(question)
    if not answer:
        return jsonify({"ok": False, "msg": "AI 未返回可用答案", "elapsed": round(time.time() - started, 1)}), 200
    return jsonify({"ok": True, "answer": answer, "elapsed": round(time.time() - started, 1)}), 200


# --------------------------------------------------------------------- 任务
@api.post("/jobs")
def create_job():
    """创建并启动学习任务。"""
    payload = request.get_json(silent=True) or {}
    raw_courses = payload.get("courses") or []
    if not isinstance(raw_courses, list) or not raw_courses:
        return _json_error("请至少选择一门课程")

    courses = [_trim_course(c) for c in raw_courses if isinstance(c, dict)]
    courses = [c for c in courses if c.get("courseId") and c.get("clazzId")]
    if not courses:
        return _json_error("课程参数不完整")

    options = payload.get("options") or {}
    job_id = _runtime().start_job(courses, options)
    return jsonify({"ok": True, "job_id": job_id}), 201


@api.get("/jobs")
def jobs():
    """任务历史。"""
    limit = min(200, max(1, int(request.args.get("limit", 50))))
    return jsonify({"ok": True, "jobs": store.list_jobs(limit)})


@api.get("/jobs/<int:job_id>")
def job_detail(job_id: int):
    """任务详情（含章节明细）。"""
    job = store.get_job(job_id)
    if job is None:
        return _json_error("任务不存在", 404)
    return jsonify({"ok": True, "job": job, "items": store.get_items(job_id)})


@api.post("/jobs/<int:job_id>/control")
def job_control(job_id: int):
    """暂停 / 继续 / 停止 / 运行中调速。"""
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "")
    runtime = _runtime()
    if runtime.job_id != job_id:
        return _json_error("该任务不是当前任务", 409)
    state = runtime.control_job(action, payload.get("value"))
    return jsonify({"ok": True, "state": state})


@api.post("/jobs/<int:job_id>/resume")
def resume_job(job_id: int):
    """继续一个已停止/已完成的任务：用同样课程新开一轮，已完成的章节会自动跳过。"""
    new_job_id = _runtime().resume_job(job_id)
    return jsonify({"ok": True, "job_id": new_job_id}), 201


@api.get("/jobs/<int:job_id>/events")
def job_events(job_id: int):
    """SSE 实时进度流。"""
    runtime = _runtime()
    bus = runtime.bus

    def generate():
        q = bus.subscribe()
        try:
            for event, data in bus.history():
                yield _sse(event, data)
            while True:
                try:
                    event, data = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield _sse(event, data)
        finally:
            bus.unsubscribe(q)

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@api.get("/jobs/<int:job_id>/report.csv")
def job_report(job_id: int):
    """导出章节明细 CSV。"""
    job = store.get_job(job_id)
    if job is None:
        return _json_error("任务不存在", 404)
    items = store.get_items(job_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["课程", "章节", "状态", "重试次数"])
    for item in items:
        writer.writerow([item.get("course", ""), item.get("title", ""), item.get("status", ""), item.get("tries", 0)])

    filename = f"chaoxing-job-{job_id}.csv"
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@api.delete("/jobs/<int:job_id>")
def delete_job(job_id: int):
    """删除历史任务。"""
    runtime = _runtime()
    if runtime.job_id == job_id and runtime.status == "running":
        return _json_error("任务正在运行，无法删除", 409)
    store.delete_job(job_id)
    return jsonify({"ok": True})
