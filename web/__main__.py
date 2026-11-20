# -*- coding: utf-8 -*-
"""``python -m web`` 入口：启动本地控制台、打印防误关提示并自动打开浏览器。"""
from __future__ import annotations

import argparse
import ctypes
import os
import socket
import sys
import threading
import webbrowser

from web import __version__, create_app

BANNER = """
==============================================================
  chaoxing 学习通控制台  v{version}
--------------------------------------------------------------
  访问地址 : {url}
  数据目录 : 仅本机，密码不落盘

  【注意】工作期间请勿关闭此控制台窗口！
      关闭窗口 = 服务立即停止，浏览器里的页面将无法使用
      可以最小化，程序会一直挂在这里运行

  停止服务 : 在此窗口按 Ctrl+C（或关闭窗口并确认）
==============================================================
"""


def _enable_utf8() -> None:
    """把控制台切到 UTF-8，避免中文/符号在 GBK 代码页下报错或乱码。"""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 - 无控制台时忽略
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 被重定向或无 reconfigure 时忽略
            pass


def _set_console_title() -> None:
    """把窗口标题改成醒目提示，防止误认成无关窗口。"""
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("chaoxing 控制台 · 运行中，请勿关闭")
    except Exception:  # noqa: BLE001 - 非 Windows 或受限环境忽略
        pass


def _install_close_guard() -> None:
    """拦截「点右上角关闭窗口」，弹窗二次确认，避免误关导致任务中断。"""
    if os.name != "nt":
        return

    try:
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(ctrl_type: int) -> bool:
            # 2=CTRL_CLOSE_EVENT, 5=LOGOFF, 6=SHUTDOWN
            if ctrl_type in (2, 5, 6):
                try:
                    answer = ctypes.windll.user32.MessageBoxW(
                        None,
                        "关闭这个窗口会立即停止 chaoxing 服务，\n正在学习/答题的任务也会中断。\n\n确定要关闭吗？",
                        "chaoxing 正在运行",
                        0x04 | 0x30,  # MB_YESNO | MB_ICONWARNING
                    )
                except Exception:  # noqa: BLE001
                    return False
                return answer != 6  # 非「是」时阻止关闭
            return False

        global _CLOSE_HANDLER  # 保持引用，避免被 GC
        _CLOSE_HANDLER = handler_type(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CLOSE_HANDLER, True)
    except Exception:  # noqa: BLE001 - 任何失败都不影响主流程
        pass


_CLOSE_HANDLER = None


def _free_port(host: str, port: int) -> int:
    """端口被占用时向后找一个空闲端口。"""
    for candidate in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
        return candidate
    raise RuntimeError(f"从 {port} 开始找不到可用端口")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(prog="python -m web", description="chaoxing Web 控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8848, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")
    args = parser.parse_args(argv)

    app = create_app()
    port = _free_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    _enable_utf8()
    _set_console_title()
    _install_close_guard()
    print(BANNER.format(version=__version__, url=url), flush=True)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=args.host, port=port, debug=args.debug, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n服务已停止。如需再次使用，请重新启动程序。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
