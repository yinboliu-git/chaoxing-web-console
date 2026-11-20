# 架构说明

本文简要说明本分支的分层设计，方便二次开发。

## 一次任务的数据流

```
浏览器                    Flask                         引擎
  │  POST /api/jobs        │                              │
  ├───────────────────────►│ runtime.start_job()          │
  │                        ├── StudyRunner(chaoxing,...)  │
  │                        │        │                     │
  │  GET  /events (SSE)    │        ├─ collect_tasks()    │
  ├───────────────────────►│        │    └─ emit("plan")  │
  │◄──── event: plan ──────┤◄───────┤                     │
  │◄──── event: chapter ───┤◄───────┤ JobProcessor.run()  │
  │◄──── event: progress ──┤◄───────┤   （多 worker）      │
  │◄──── event: log ───────┤◄───────┤                     │
  │  POST /control         │        │                     │
  ├───────────────────────►│──► RunControl.pause/stop      │
```

## 分层

| 层 | 模块 | 职责 | 是否依赖 Web |
| --- | --- | --- | :-: |
| 执行层 | `api/base.py` | 超星接口（登录、课程、视频、测验、文档…） | ❌ |
| 执行层 | `api/answer.py` | 题库体系（8 个 provider）、答案解析与校验 | ❌ |
| 执行层 | `api/ai_client.py` | OpenAI 兼容客户端、自检、错误诊断、答案解析 | ❌ |
| 调度层 | `api/runner.py` | `StudyRunner` / `JobProcessor`：章节调度、重试、暂停/停止 | ❌ |
| 调度层 | `api/progress.py` | `ProgressSink`、`RunControl`、`JobStopped` | ❌ |
| 配置层 | `api/config_store.py` | JSON 设置 ↔ 引擎配置 ↔ `config.ini` | ❌ |
| 应用层 | `main.py` | 原 CLI（薄壳：解析参数 → StudyRunner） | ❌ |
| 应用层 | `web/*` | Flask 路由、SSE、SQLite、运行时 | ✅ |
| 展示层 | `web/static/js` | 原生 ES Modules，5 个视图 | ✅ |

关键点：**执行层与调度层不认识 Web**。CLI 与 Web 共用同一套 `StudyRunner`，
Web 只是多传了一个 `ProgressSink` 和一个 `RunControl`：

```python
# CLI（main.py）
runner = StudyRunner(chaoxing, courses, options=RunOptions(...))          # 无 sink
runner.run()

# Web（web/runtime.py）
runner = StudyRunner(chaoxing, courses, options=..., progress=JobProgress(bus, job_id),
                     control=control)
runner.run()
```

## 进度事件

| 事件 | 触发时机 | 关键字段 |
| --- | --- | --- |
| `answer` | 任务开始 | `label` / `text` / `chain`（答题方式） |
| `plan` | 章节读取完毕 | `total` / `chapters[]`（含 `pending`） |
| `job` | 调度器启动 | `total` / `courses` |
| `chapter` | 章节状态变化 | `index` / `course` / `title` / `status` / `tries` |
| `progress` | 每个章节结束 | `done` / `total` / `percent` / `counts` / `elapsed` / `speed` |
| `task` | 进入任务点 | `type` / `job` / `course` |
| `video` | 视频播放中（约每 0.5s） | `play` / `duration` |
| `speed` | 运行中调速 | `speed` |
| `state` | 暂停/恢复 | `state` |
| `log` | loguru 日志 | `level` / `time` / `msg` |
| `done` | 任务结束 | `summary` 字段 |
| `error` | 异常 | `message` |

## 暂停与停止

- `RunControl` 持有 `pause_event` / `stop_event`，并由 `speed` 支持运行中调速。
- **章节级检查**：worker 取任务前、每个任务点前都会检查，暂停时阻塞等待。
- **任务点内检查**：`study_video` 每个循环 tick 检查控制器——
  - 暂停：`sleep(0.2)` + 重置时间基准，**播放进度不推进**；
  - 停止：抛 `JobStopped`，`process_chapter` 捕获后返回 `PENDING`，该章节记为跳过。
- **队列清空**：停止巡检线程把剩余待办逐条标为跳过并 `task_done()`，避免 `join()` 永久阻塞。
- **继续学习**：`POST /api/jobs/<id>/resume` 用原任务的课程与参数新建一轮，
  已完成的章节由超星侧标记并自动跳过。

## 单用户约束

超星的 `SessionManager` 是进程级单例，`cookies.txt` 也是全局文件，因此：

- 同一时刻只能有**一个登录会话**、**一个运行中的任务**；
- 服务只监听 `127.0.0.1`，不做多租户；
- 想要多账号，需要先把 `SessionManager` 改成按账号隔离。

## 前端约定

- 无构建链：`index.html` 只加载 `app.js`（`type="module"`），其余按需 import；
- 路由为 hash 路由（`#/courses`、`#/run/3`…），刷新不丢页面；
- 所有请求走 `web/static/js/api.js`，统一错误处理与 401 跳转；
- 主题用 CSS 变量 + `data-theme`，状态存 `localStorage`。
