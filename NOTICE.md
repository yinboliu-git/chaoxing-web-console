# NOTICE / 归属说明

本仓库是以下项目的**衍生作品**（derivative work），依据 **GPL-3.0** 发布：

| 项目 | 地址 | 用途 |
| --- | --- | --- |
| chaoxing | https://github.com/Samueli924/chaoxing | 上游项目：超星接口封装、任务调度、题库体系、字体反爬、验证码识别 |
| 上游作者 | https://github.com/Samueli924 | 原始代码与架构 |

## 本仓库相对上游的修改

1. **新增 Web 控制台**（`web/`）：Flask + SSE + 原生 ES Modules 前端，5 个页面。
2. **抽离执行器**（`api/runner.py`、`api/progress.py`）：把 `main.py` 里的调度逻辑抽成可被 Web 驱动的 `StudyRunner`，
   支持进度事件、暂停、立即停止。
3. **新增配置层**（`api/config_store.py`）：`settings.json` ↔ 引擎配置 ↔ `config.ini` 双向映射。
4. **新增 AI 答题客户端**（`api/ai_client.py`）与 `TikuAI` provider：分步自检、错误诊断、TLS 系统证书兼容。
5. **行为调整**：并发章节数默认值由 4 改为 1；`app.py` 改为统一入口（无参数启动 Web）。
6. **其他**：图标、单元测试、打包脚本、文档。

上游原有的 CLI 参数与输出保持兼容，未做破坏性修改。

## 协议继承

依据 GPL-3.0，本仓库同样以 **GPL-3.0** 发布（见 `LICENSE`）。
任何基于本仓库的修改或衍生程序，**必须**同样以 GPL-3.0 开源，不得作为闭源商业软件发布或销售，不得用于盈利。

## 商标说明

"超星"、"学习通"、"尔雅"、"泛雅" 等名称与标识归其权利人所有。
本项目与超星公司无任何关联，图标为自绘的通用"学士帽"图形，未使用任何官方素材。
