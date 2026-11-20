// 运行进度页：实时进度、视频进度（总时长/已播放/剩余）、章节状态、日志、暂停/停止
import { api } from "../api.js";
import { navigate } from "../router.js";
import { escapeHtml, formatDuration, statusClass, statusText, toast } from "../ui.js";

const MAX_LOG_LINES = 800;
let activeStream = null;

function closeStream() {
  if (activeStream) {
    activeStream.close();
    activeStream = null;
  }
}

/** 把秒数格式化成 12:34 / 1:02:03 */
function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function heroTemplate() {
  return `
  <div class="card">
    <div class="ring-wrap">
      <div class="ring" id="ring" style="--p:0">
        <div class="ring-inner">
          <strong id="ring-percent">0%</strong>
          <span>总进度</span>
        </div>
      </div>

      <div class="run-main">
        <div class="run-status-row">
          <span id="job-status"><span class="badge">等待中</span></span>
          <span class="badge" id="answer-mode-badge">答题方式 -</span>
          <span class="hint" id="chapter-count">0 / 0 章节</span>
        </div>

        <div class="stat-grid">
          <div class="stat ok"><span>成功</span><strong id="stat-success">0</strong></div>
          <div class="stat err"><span>失败</span><strong id="stat-failed">0</strong></div>
          <div class="stat warn"><span>跳过</span><strong id="stat-skipped">0</strong></div>
          <div class="stat"><span>已用时</span><strong id="stat-elapsed">0s</strong></div>
          <div class="stat"><span>预计剩余</span><strong id="stat-eta">-</strong></div>
        </div>

        <div class="run-current">
          <span class="run-current-label">当前任务</span>
          <span class="run-current-value" id="current-task">-</span>
        </div>
      </div>

      <div class="run-controls">
        <div class="field" style="margin:0">
          <label>运行中倍速 <span class="tip"><span id="run-speed-value">-</span>x</span></label>
          <input type="range" id="run-speed" min="1" max="2" step="0.1" value="1.5" disabled>
        </div>
        <button class="btn btn-ghost" id="pause-btn" type="button" disabled>暂停</button>
        <button class="btn btn-danger" id="stop-btn" type="button" disabled>停止</button>
        <button class="btn btn-primary" id="resume-btn" type="button" style="display:none">继续学习</button>
      </div>
    </div>
  </div>`;
}

function videoTemplate() {
  return `
  <div class="card" id="video-card" style="display:none">
    <div class="card-title">
      <h2 id="video-title">视频播放进度</h2>
      <span class="hint" id="video-badge"></span>
    </div>
    <div class="vp-track"><i id="video-fill" style="width:0%"></i></div>
    <div class="vp-times">
      <span><strong id="video-played">00:00</strong> / <span id="video-total">00:00</span></span>
      <span class="hint" id="video-remaining">剩余 --:--</span>
    </div>
  </div>`;
}

export async function render(container, params) {
  closeStream();

  const requestedId = Number(params?.[0] || 0);
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>运行进度</h1>
        <p id="job-subtitle">实时查看章节处理情况与日志，可随时暂停或停止任务</p>
      </div>
      <div class="page-actions">
        <button class="btn btn-ghost" id="back-btn" type="button">返回选课</button>
        <a class="btn btn-ghost" id="report-link" href="#" target="_blank" rel="noopener">导出 CSV</a>
      </div>
    </div>
    ${heroTemplate()}
    ${videoTemplate()}
    <div class="grid cols-2" style="margin-top:16px;align-items:start">
      <div class="card">
        <div class="card-title"><h2>章节</h2><span class="hint" id="chapter-hint">等待任务…</span></div>
        <div class="chapters" id="chapters">
          <div class="empty"><strong>暂无章节</strong>开始任务后这里会实时滚动</div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">
          <h2>实时日志</h2>
          <div style="display:flex;align-items:center;gap:10px">
            <select class="select" id="log-filter" style="width:auto;padding:4px 26px 4px 10px;font-size:12px">
              <option value="all">全部</option>
              <option value="warn">警告及以上</option>
              <option value="error">仅错误</option>
            </select>
            <label class="switch"><input type="checkbox" id="log-follow" checked><span class="track"></span><span class="label">跟随</span></label>
            <button class="btn btn-sm btn-ghost" id="log-clear" type="button">清空</button>
          </div>
        </div>
        <div class="log" id="log"></div>
        <span class="hint" id="log-hint" style="display:block;margin-top:8px">0 条</span>
      </div>
    </div>
  `;

  container.querySelector("#back-btn").addEventListener("click", () => navigate("#/courses"));

  const nodes = {
    ring: container.querySelector("#ring"),
    percent: container.querySelector("#ring-percent"),
    status: container.querySelector("#job-status"),
    answerBadge: container.querySelector("#answer-mode-badge"),
    count: container.querySelector("#chapter-count"),
    success: container.querySelector("#stat-success"),
    failed: container.querySelector("#stat-failed"),
    skipped: container.querySelector("#stat-skipped"),
    elapsed: container.querySelector("#stat-elapsed"),
    eta: container.querySelector("#stat-eta"),
    current: container.querySelector("#current-task"),
    subtitle: container.querySelector("#job-subtitle"),
    chapters: container.querySelector("#chapters"),
    chapterHint: container.querySelector("#chapter-hint"),
    log: container.querySelector("#log"),
    logHint: container.querySelector("#log-hint"),
    logFilter: container.querySelector("#log-filter"),
    logFollow: container.querySelector("#log-follow"),
    logClear: container.querySelector("#log-clear"),
    pause: container.querySelector("#pause-btn"),
    stop: container.querySelector("#stop-btn"),
    resume: container.querySelector("#resume-btn"),
    speed: container.querySelector("#run-speed"),
    speedValue: container.querySelector("#run-speed-value"),
    report: container.querySelector("#report-link"),
    videoCard: container.querySelector("#video-card"),
    videoTitle: container.querySelector("#video-title"),
    videoBadge: container.querySelector("#video-badge"),
    videoFill: container.querySelector("#video-fill"),
    videoPlayed: container.querySelector("#video-played"),
    videoTotal: container.querySelector("#video-total"),
    videoRemaining: container.querySelector("#video-remaining"),
  };

  const rowMap = new Map();
  let logCount = 0;
  let paused = false;
  let jobId = requestedId;
  let currentSpeed = 1;

  // ---------------------------------------------------------------- 状态渲染
  function setStatusBadge(status) {
    if (status === "paused") {
      nodes.status.innerHTML = `<span class="badge warn"><span class="dot pulse"></span>已暂停</span>`;
      return;
    }
    const cls = statusClass(status);
    const tone = cls === "success" ? "ok" : cls === "failed" ? "err" : cls === "running" || cls === "retrying" ? "brand" : "";
    nodes.status.innerHTML = `<span class="badge ${tone}">
      <span class="dot ${status === "running" ? "pulse" : ""}"></span>${escapeHtml(statusText(status))}
    </span>`;
  }

  function setPercent(percent) {
    const safe = Math.max(0, Math.min(100, Number(percent) || 0));
    nodes.ring.style.setProperty("--p", String(safe));
    nodes.percent.textContent = `${Math.round(safe)}%`;
  }

  function setControls(running) {
    nodes.pause.disabled = !running;
    nodes.stop.disabled = !running;
    nodes.speed.disabled = !running;
    if (running) showResume(false);
  }

  function showResume(show) {
    nodes.resume.style.display = show ? "" : "none";
  }

  function setSpeedLabel(speed) {
    const value = Number(speed);
    if (!Number.isFinite(value) || value <= 0) return;
    currentSpeed = value;
    nodes.speedValue.textContent = value.toFixed(1);
    if (Number(nodes.speed.value) !== value) nodes.speed.value = String(Math.min(2, Math.max(1, value)));
  }

  function setAnswerMode(plan) {
    if (!plan) return;
    nodes.answerBadge.textContent = `答题方式 · ${plan.label || "-"}`;
    nodes.answerBadge.title = plan.text || "";
  }

  function formatEta(percent, elapsed) {
    if (!percent || percent <= 0 || !elapsed) return "-";
    if (percent >= 100) return "已完成";
    const remaining = (elapsed * (100 - percent)) / percent;
    if (!Number.isFinite(remaining) || remaining <= 0) return "-";
    return formatDuration(remaining);
  }

  // ---------------------------------------------------------------- 视频进度
  function showVideo(jobName, badge = "视频") {
    nodes.videoCard.style.display = "";
    nodes.videoTitle.textContent = jobName || "视频播放进度";
    nodes.videoBadge.textContent = badge;
  }

  function hideVideo() {
    nodes.videoCard.style.display = "none";
    nodes.videoFill.style.width = "0%";
    nodes.videoPlayed.textContent = "00:00";
    nodes.videoTotal.textContent = "00:00";
    nodes.videoRemaining.textContent = "剩余 --:--";
  }

  function updateVideo(playSeconds, durationSeconds) {
    const duration = Math.max(0, Number(durationSeconds) || 0);
    const play = Math.max(0, Math.min(duration, Number(playSeconds) || 0));
    const percent = duration ? (play / duration) * 100 : 0;

    nodes.videoFill.style.width = `${percent.toFixed(1)}%`;
    nodes.videoPlayed.textContent = clock(play);
    nodes.videoTotal.textContent = clock(duration);

    const remaining = Math.max(0, duration - play);
    const realRemaining = currentSpeed > 0 ? remaining / currentSpeed : remaining;
    nodes.videoRemaining.textContent = duration
      ? `剩余 ${clock(remaining)}（${currentSpeed.toFixed(1)}x 实际约 ${clock(realRemaining)}）`
      : "剩余 --:--";
    nodes.videoBadge.textContent = `${percent.toFixed(0)}% · ${clock(play)} / ${clock(duration)}`;
  }

  // ---------------------------------------------------------------- 章节
  function upsertChapter(data) {
    const key = `${data.course}::${data.title}`;
    let row = rowMap.get(key);

    if (!row) {
      if (!nodes.chapters.querySelector(".chapter")) nodes.chapters.innerHTML = "";
      const el = document.createElement("div");
      el.className = "chapter";
      el.innerHTML = `
        <span class="idx"></span>
        <span class="title"></span>
        <span class="status-tag"></span>`;
      nodes.chapters.appendChild(el);
      row = { el, data: {}, fallbackIndex: rowMap.size };
      rowMap.set(key, row);
    }

    row.data = { ...row.data, ...data };
    const index = row.data.index ?? row.fallbackIndex;
    row.el.className = `chapter ${statusClass(data.status)}`;
    row.el.querySelector(".idx").textContent = String(index + 1).padStart(2, "0");
    row.el.querySelector(".title").textContent = data.title || "-";
    row.el.querySelector(".title").title = `${data.course || ""} · ${data.title || ""}`;
    const tag = row.el.querySelector(".status-tag");
    tag.className = `status-tag ${statusClass(data.status)}`;
    tag.textContent = `${statusText(data.status)}${data.tries ? ` ×${data.tries}` : ""}`;

    const total = rowMap.size;
    let done = 0;
    rowMap.forEach((item) => {
      if (["success", "failed", "skipped"].includes(item.data.status)) done += 1;
    });
    nodes.chapterHint.textContent = `${done} / ${total} 已处理`;
  }

  // ---------------------------------------------------------------- 日志
  function logMatches(level) {
    const mode = nodes.logFilter.value;
    if (mode === "warn") return level === "WARNING" || level === "ERROR";
    if (mode === "error") return level === "ERROR";
    return true;
  }

  function appendLog(entry) {
    const level = String(entry.level || "INFO").toUpperCase();
    const line = document.createElement("div");
    line.className = "log-line";
    line.dataset.level = level;
    line.innerHTML = `
      <span class="log-time">${escapeHtml(entry.time || "")}</span>
      <span class="log-level ${escapeHtml(level)}">${escapeHtml(level)}</span>
      <span class="log-msg">${escapeHtml(entry.msg || "")}</span>`;
    if (!logMatches(level)) line.style.display = "none";

    const follow = nodes.logFollow.checked;
    const nearBottom = nodes.log.scrollHeight - nodes.log.scrollTop - nodes.log.clientHeight < 80;
    nodes.log.appendChild(line);
    logCount += 1;
    while (nodes.log.childElementCount > MAX_LOG_LINES) nodes.log.firstElementChild.remove();
    nodes.logHint.textContent = `${logCount} 条`;
    if (follow && nearBottom) nodes.log.scrollTop = nodes.log.scrollHeight;
  }

  function applyLogFilter() {
    nodes.log.querySelectorAll(".log-line").forEach((line) => {
      line.style.display = logMatches(line.dataset.level || "INFO") ? "" : "none";
    });
  }

  nodes.logFilter.addEventListener("change", applyLogFilter);
  nodes.logClear.addEventListener("click", () => {
    nodes.log.innerHTML = "";
    logCount = 0;
    nodes.logHint.textContent = "0 条";
  });

  // ---------------------------------------------------------------- 进度
  function applyProgress(data) {
    setPercent(data.percent ?? 0);
    const total = data.total ?? 0;
    const done = data.done ?? 0;
    nodes.count.textContent = `${done} / ${total} 章节`;
    const counts = data.counts || {};
    nodes.success.textContent = String(counts.success ?? 0);
    nodes.failed.textContent = String(counts.failed ?? 0);
    nodes.skipped.textContent = String(counts.skipped ?? 0);
    nodes.elapsed.textContent = formatDuration(data.elapsed ?? 0);
    nodes.eta.textContent = formatEta(Number(data.percent) || 0, Number(data.elapsed) || 0);
    if (data.speed) setSpeedLabel(data.speed);
  }

  // ---------------------------------------------------------------- SSE
  function connect() {
    const stream = new EventSource(api.eventsUrl(jobId));
    activeStream = stream;

    stream.addEventListener("job", (event) => {
      const data = JSON.parse(event.data);
      setStatusBadge("running");
      setControls(true);
      nodes.count.textContent = `0 / ${data.total ?? 0} 章节`;
      nodes.chapterHint.textContent = `共 ${data.total ?? 0} 个章节任务点`;
      if (data.courses?.length) nodes.subtitle.textContent = `正在学习：${data.courses.join("、")}`;
    });

    // 完整章节清单（含还没轮到的），先铺满列表再逐条更新状态
    stream.addEventListener("plan", (event) => {
      const data = JSON.parse(event.data);
      nodes.chapters.innerHTML = "";
      rowMap.clear();
      for (const chapter of data.chapters || []) upsertChapter(chapter);
      nodes.chapterHint.textContent = `共 ${data.total ?? rowMap.size} 个章节`;
    });

    stream.addEventListener("progress", (event) => applyProgress(JSON.parse(event.data)));
    stream.addEventListener("chapter", (event) => upsertChapter(JSON.parse(event.data)));

    stream.addEventListener("state", (event) => {
      const data = JSON.parse(event.data);
      paused = data.state === "paused";
      nodes.pause.textContent = paused ? "继续" : "暂停";
      nodes.pause.classList.toggle("btn-primary", paused);
      nodes.pause.classList.toggle("btn-ghost", !paused);
      setStatusBadge(paused ? "paused" : "running");
    });

    stream.addEventListener("task", (event) => {
      const data = JSON.parse(event.data);
      const typeText = { video: "视频", document: "文档", work: "测验", read: "阅读", live: "直播" }[data.type] || "任务";
      nodes.current.textContent = `${data.job || data.course || "-"}（${typeText}）`;
      if (data.type === "video") showVideo(data.job || data.course, typeText);
      else hideVideo();
    });

    stream.addEventListener("video", (event) => {
      const data = JSON.parse(event.data);
      showVideo(data.job, "视频");
      nodes.current.textContent = `${data.job || "-"}（视频）`;
      updateVideo(data.play, data.duration);
    });

    stream.addEventListener("speed", (event) => {
      const data = JSON.parse(event.data);
      setSpeedLabel(data.speed);
      const play = Number(nodes.videoPlayed.textContent.split(":")[0] || 0);
      void play; // 保留当前视频面板，倍速变化本身由下一次 video 事件刷新剩余估算
    });

    stream.addEventListener("answer", (event) => setAnswerMode(JSON.parse(event.data)));
    stream.addEventListener("log", (event) => appendLog(JSON.parse(event.data)));

    stream.addEventListener("done", (event) => {
      const data = JSON.parse(event.data);
      setStatusBadge(data.interrupted ? "stopped" : "finished");
      setControls(false);
      setPercent(100);
      nodes.eta.textContent = "已完成";
      nodes.current.textContent = "-";
      hideVideo();
      toast(
        data.interrupted
          ? `任务已停止：成功 ${data.success}，失败 ${data.failed}，跳过 ${data.skipped}`
          : `任务完成：成功 ${data.success}，失败 ${data.failed}，跳过 ${data.skipped}`,
        data.failed ? "warn" : "ok",
        6000,
      );
      showResume(true);
      closeStream();
    });

    stream.addEventListener("error", (event) => {
      if (event.data) {
        try {
          const data = JSON.parse(event.data);
          toast(`任务出错：${data.message}`, "err", 6000);
        } catch {
          /* 忽略 */
        }
      }
      closeStream();
      setControls(false);
      setStatusBadge("error");
    });

    stream.onerror = () => {
      if (nodes.stop.disabled) closeStream();
    };
  }

  // ---------------------------------------------------------------- 控制
  nodes.pause.addEventListener("click", async () => {
    try {
      await api.controlJob(jobId, paused ? "resume" : "pause");
    } catch (error) {
      toast(error.message, "err");
    }
  });

  let speedTimer = null;
  nodes.speed.addEventListener("input", () => {
    const value = Number(nodes.speed.value);
    nodes.speedValue.textContent = value.toFixed(1);
    currentSpeed = value;
    window.clearTimeout(speedTimer);
    speedTimer = window.setTimeout(async () => {
      try {
        await api.controlJob(jobId, "set_speed", value);
        toast(`倍速已调整到 ${value.toFixed(1)}x（下一个任务点生效）`, "info", 2200);
      } catch (error) {
        toast(error.message, "err");
      }
    }, 400);
  });

  nodes.stop.addEventListener("click", async () => {
    if (!window.confirm("确定要停止当前任务吗？已完成的章节不会回滚。")) return;
    try {
      await api.controlJob(jobId, "stop");
      toast("已停止，当前视频/任务会立即中断", "warn");
      nodes.stop.disabled = true;
      nodes.pause.disabled = true;
      nodes.speed.disabled = true;
    } catch (error) {
      toast(error.message, "err");
    }
  });

  nodes.resume.addEventListener("click", async () => {
    const button = nodes.resume;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 启动中…';
    try {
      const result = await api.resumeJob(jobId);
      toast("已继续学习，未完成的章节会接着跑", "ok");
      navigate(`#/run/${result.job_id}`);
    } catch (error) {
      toast(error.message, "err");
      button.disabled = false;
      button.textContent = "继续学习";
    }
  });

  // ---------------------------------------------------------------- 初始化
  if (!jobId) {
    try {
      const data = await api.listJobs(1);
      if (data.jobs?.length) {
        navigate(`#/run/${data.jobs[0].id}`);
        return;
      }
    } catch {
      /* 忽略 */
    }
    container.querySelector("#chapters").innerHTML =
      `<div class="empty"><strong>还没有任务</strong>先去<a href="#/courses" style="color:var(--brand)">选择课程</a>开始一次学习</div>`;
    return;
  }

  nodes.report.href = api.reportUrl(jobId);

  try {
    const detail = await api.getJob(jobId);
    setStatusBadge(detail.job.status);

    const summary = detail.job.summary || null;
    const items = detail.items || [];
    for (const item of items) upsertChapter(item);

    const plan = (detail.job.options || {}).answer_plan;
    if (plan) setAnswerMode(plan);

    const courses = (detail.job.courses || []).map((course) => course.title).filter(Boolean);
    if (courses.length) nodes.subtitle.textContent = `正在学习：${courses.join("、")}`;

    if (summary) {
      const total = summary.total || items.length || 0;
      const done = (summary.success || 0) + (summary.failed || 0) + (summary.skipped || 0);
      setPercent(total ? (done / total) * 100 : 0);
      nodes.count.textContent = `${done} / ${total} 章节`;
      nodes.success.textContent = String(summary.success ?? 0);
      nodes.failed.textContent = String(summary.failed ?? 0);
      nodes.skipped.textContent = String(summary.skipped ?? 0);
      nodes.elapsed.textContent = formatDuration(summary.elapsed ?? 0);
      nodes.eta.textContent = summary.interrupted ? "已停止" : "已完成";
      nodes.chapterHint.textContent = `历史任务 · 共 ${total} 个章节 · ${statusText(detail.job.status)}`;
    } else {
      nodes.chapterHint.textContent = `历史任务 · ${statusText(detail.job.status)}`;
    }

    if (detail.job.status === "running") {
      setControls(true);
      connect();
    } else {
      setControls(false);
      nodes.current.textContent = "-";
      hideVideo();
      showResume(Boolean(detail.items && detail.items.length));
      if (!nodes.log.childElementCount) {
        nodes.log.innerHTML = `<div class="log-line"><span class="log-msg" style="color:var(--muted)">任务已结束，实时日志不再推送；完整日志见程序目录 chaoxing.log</span></div>`;
      }
    }
  } catch (error) {
    toast(error.message, "err");
    return;
  }

  const cleanup = () => {
    closeStream();
    window.removeEventListener("hashchange", cleanup);
  };
  window.addEventListener("hashchange", cleanup);
}
