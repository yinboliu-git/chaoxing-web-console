# -*- coding: utf-8 -*-
"""Web 控制台应用工厂。

命令行入口：

    python -m web            # 启动并自动打开浏览器
    python -m web --no-browser --port 9000

注意：本应用**只面向本机单用户**（超星 Session 是进程级单例），
默认绑定 127.0.0.1，请勿直接暴露到公网。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request

__version__ = "3.2.0-web"


def create_app(config: Optional[dict[str, Any]] = None) -> Flask:
    """创建 Flask 应用。

    Args:
        config: 可选的 Flask 配置覆盖项（测试用）。

    Returns:
        配置完成的 Flask 应用。
    """
    base_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(base_dir / "static"),
        template_folder=str(base_dir / "templates"),
        static_url_path="/static",
    )
    app.config.update(
        JSON_AS_ASCII=False,
        SEND_FILE_MAX_AGE_DEFAULT=0,
        SECRET_KEY=os.environ.get("CHAOXING_SECRET", os.urandom(24).hex()),
    )
    if config:
        app.config.update(config)

    try:  # Flask 2.3+ 用 app.json.ensure_ascii 控制中文输出
        app.json.ensure_ascii = False
    except AttributeError:  # pragma: no cover - 兼容旧版 Flask
        pass

    from web import store
    from web.routes import api

    store.init_db()  # 保证数据表存在（应用数据目录可能刚被创建）
    app.register_blueprint(api)

    @app.get("/")
    def index():
        """单页应用外壳。"""
        return render_template("index.html", version=__version__)

    @app.errorhandler(404)
    def not_found(_error):
        """API 返回 JSON，其余路径回到单页应用。"""
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": "接口不存在"}), 404
        return render_template("index.html", version=__version__)

    @app.errorhandler(500)
    def server_error(error):
        """兜底 500。"""
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": f"服务端错误: {error}"}), 500
        return render_template("index.html", version=__version__)

    return app
