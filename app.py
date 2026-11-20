# -*- coding: utf-8 -*-
"""统一入口。

* 双击 / 无参数运行 → 启动 Web 控制台（``python app.py``）
* 传入命令行参数    → 走原来的 CLI（``python app.py -c config.ini``）
* ``python main.py`` → 纯 CLI，行为与旧版完全一致

这样打包成单个 exe 后，老用户可以继续用全部旧参数，新用户双击即可开网页。
"""
from __future__ import annotations

import sys

# 只要出现这些参数就走 CLI，保证旧用法 100% 兼容
_CLI_FLAGS = {
    "-c", "--config",
    "-u", "--username",
    "-p", "--password",
    "-l", "--list",
    "-s", "--speed",
    "-j", "--jobs",
    "-a", "--notopen-action",
    "-v", "--verbose", "--debug",
    "--use-cookies",
    "--auto-sign",
    "--retry-interval",
    "-lc", "--add-learning-count",
    "-tc", "--target-count",
}

_WEB_FLAGS = {"--web", "--host", "--port", "--no-browser"}


def celery_init_app(app):
    """初始化 Celery（保留旧接口，Web 控制台本身不依赖它）。"""
    from celery import Celery, Task

    class FlaskTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


def _run_cli(argv):
    """以 CLI 模式运行（main.main 从 sys.argv 读参数，这里临时替换）。"""
    from main import main as cli_main

    old_argv = sys.argv
    sys.argv = ["chaoxing"] + list(argv)
    try:
        cli_main()
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = old_argv
    return 0


def main(argv=None):
    """按参数决定启动 Web 控制台还是命令行模式。"""
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in {"-h", "--help"}:
        return _run_cli(["--help"])

    if argv and argv[0] in _WEB_FLAGS:
        from web.__main__ import main as web_main

        return web_main(argv)

    if argv and (argv[0] in _CLI_FLAGS or any(arg in _CLI_FLAGS for arg in argv)):
        return _run_cli(argv)

    # 无参数：打开 Web 控制台
    from web.__main__ import main as web_main

    return web_main(argv)


if __name__ == "__main__":
    sys.exit(main())
