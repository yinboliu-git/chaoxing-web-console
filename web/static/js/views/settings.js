// 设置与历史：通用参数、通知、配置导入导出、历史任务
import { api } from "../api.js";
import { navigate } from "../router.js";
import { setSettings, state } from "../state.js";
import { escapeHtml, formatDuration, statusClass, statusText, timeAgo, toast } from "../ui.js";

function commonSection(common) {
  return `
  <div class="card">
    <div class="card-title"><h2>学习参数</h2><span class="hint">对之后新建的任务生效</span></div>
    <div class="grid cols-2">
      <div class="field">
        <label>视频倍速 <span class="tip"><span id="speed-value">${common.speed}</span>x（最大 2）</span></label>
        <input type="range" id="speed" min="1" max="2" step="0.1" value="${common.speed}">
      </div>
      <div class="field">
        <label>并发章节数 <span class="tip">默认 1（最稳）；越大越容易触发风控</span></label>
        <input class="input" id="jobs" type="number" min="1" max="16" value="${common.jobs}">
      </div>
      <div class="field">
        <label>关闭章节处理</label>
        <select class="select" id="notopen">
          <option value="retry" ${common.notopen_action === "retry" ? "selected" : ""}>retry · 重试上一章</option>
          <option value="ask" ${common.notopen_action === "ask" ? "selected" : ""}>ask · 询问</option>
          <option value="continue" ${common.notopen_action === "continue" ? "selected" : ""}>continue · 直接跳过</option>
        </select>
      </div>
      <div class="field">
        <label>重试间隔（秒）</label>
        <input class="input" id="retry-interval" type="number" step="0.1" min="0" value="${common.retry_interval}">
      </div>
      <div class="field">
        <label>章节检测最大重做次数</label>
        <input class="input" id="work-max-retries" type="number" min="0" max="10" value="${common.work_max_retries}">
      </div>
      <div class="field">
        <label>章节学习次数目标 <span class="tip">需开启下方开关</span></label>
        <input class="input" id="target-count" type="number" min="1" value="${common.target_count}">
      </div>
    </div>
    <div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:4px">
      <label class="switch">
        <input type="checkbox" id="work-redo" ${common.work_redo_enabled ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">答错自动重做（仅限可多次作答的课）</span>
      </label>
      <label class="switch">
        <input type="checkbox" id="add-count" ${common.add_learning_count ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">刷完后增加章节学习次数</span>
      </label>
    </div>
  </div>`;
}

function notificationSection(notification) {
  return `
  <div class="card">
    <div class="card-title"><h2>完成通知</h2><span class="hint">留空则不发送</span></div>
    <div class="grid cols-2">
      <div class="field">
        <label>通知渠道</label>
        <select class="select" id="notify-provider">
          ${["ServerChan", "Qmsg", "Bark", "Telegram"]
            .map((name) => `<option value="${name}" ${notification.provider === name ? "selected" : ""}>${name}</option>`)
            .join("")}
        </select>
      </div>
      <div class="field">
        <label>推送 URL / Key</label>
        <input class="input" id="notify-url" placeholder="https://sctapi.ftqq.com/xxx.send" value="${escapeHtml(notification.url || "")}">
      </div>
    </div>
  </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>设置与历史</h1>
        <p>调整运行参数、配置通知，并回看历史任务</p>
      </div>
      <div class="page-actions">
        <button class="btn btn-ghost" id="export-btn" type="button">导出 config.ini</button>
        <button class="btn btn-ghost" id="import-btn" type="button">导入 config.ini</button>
        <button class="btn btn-primary" id="save-btn" type="button">保存设置</button>
      </div>
    </div>
    <div id="settings-body"><div class="card"><div class="empty"><span class="spinner"></span> 正在读取设置…</div></div></div>
    <div class="card" style="margin-top:16px">
      <div class="card-title"><h2>任务历史</h2><span class="hint" id="history-hint">读取中…</span></div>
      <div id="history"><div class="empty"><span class="spinner"></span> 加载中…</div></div>
    </div>
  `;

  const body = container.querySelector("#settings-body");
  let settings = null;

  try {
    const data = await api.getSettings();
    settings = data.settings;
    setSettings(settings);
  } catch (error) {
    body.innerHTML = `<div class="card"><div class="empty"><strong>读取设置失败</strong>${escapeHtml(error.message)}</div></div>`;
    return;
  }

  body.innerHTML = commonSection(settings.common) + notificationSection(settings.notification);

  const speed = body.querySelector("#speed");
  speed.addEventListener("input", () => {
    body.querySelector("#speed-value").textContent = Number(speed.value).toFixed(1);
  });

  function collect() {
    return {
      ...settings,
      common: {
        ...settings.common,
        speed: Number(body.querySelector("#speed").value),
        jobs: Number(body.querySelector("#jobs").value),
        notopen_action: body.querySelector("#notopen").value,
        retry_interval: Number(body.querySelector("#retry-interval").value),
        work_max_retries: Number(body.querySelector("#work-max-retries").value),
        target_count: Number(body.querySelector("#target-count").value),
        work_redo_enabled: body.querySelector("#work-redo").checked,
        add_learning_count: body.querySelector("#add-count").checked,
      },
      notification: {
        ...settings.notification,
        provider: body.querySelector("#notify-provider").value,
        url: body.querySelector("#notify-url").value.trim(),
      },
    };
  }

  container.querySelector("#save-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 保存中…';
    try {
      settings = collect();
      const data = await api.saveSettings(settings);
      settings = data.settings;
      setSettings(settings);
      toast("设置已保存", "ok");
    } catch (error) {
      toast(error.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = "保存设置";
    }
  });

  container.querySelector("#export-btn").addEventListener("click", async () => {
    try {
      const data = await api.exportIni();
      toast(`已导出到 ${data.path}`, "ok", 6000);
    } catch (error) {
      toast(error.message, "err");
    }
  });

  container.querySelector("#import-btn").addEventListener("click", async () => {
    const path = window.prompt("请输入 config.ini 的完整路径：");
    if (!path) return;
    try {
      const data = await api.importIni(path);
      settings = data.settings;
      setSettings(settings);
      body.innerHTML = commonSection(settings.common) + notificationSection(settings.notification);
      toast("导入成功", "ok");
    } catch (error) {
      toast(error.message, "err");
    }
  });

  // 历史记录
  const historyNode = container.querySelector("#history");
  try {
    const data = await api.listJobs(50);
    const jobs = data.jobs || [];
    container.querySelector("#history-hint").textContent = `最近 ${jobs.length} 条`;
    if (!jobs.length) {
      historyNode.innerHTML = `<div class="empty"><strong>还没有任务记录</strong>去<a href="#/courses" style="color:var(--brand)">选课</a>开始第一次学习</div>`;
    } else {
      historyNode.innerHTML = `
        <table class="table">
          <thead><tr><th>任务</th><th>状态</th><th>课程</th><th>成功/失败/跳过</th><th>耗时</th><th>时间</th><th></th></tr></thead>
          <tbody>
            ${jobs
              .map((job) => {
                const summary = job.summary || {};
                const courses = (job.courses || []).length;
                return `<tr>
                  <td>#${job.id}</td>
                  <td><span class="status-tag ${statusClass(job.status)}">${escapeHtml(statusText(job.status))}</span></td>
                  <td>${courses} 门</td>
                  <td>${summary.success ?? 0} / ${summary.failed ?? 0} / ${summary.skipped ?? 0}</td>
                  <td>${summary.elapsed ? formatDuration(summary.elapsed) : "-"}</td>
                  <td title="${escapeHtml(job.created_at || "")}">${timeAgo(job.created_at)}</td>
                  <td style="white-space:nowrap">
                    <button class="btn btn-sm btn-ghost" data-open="${job.id}">回看</button>
                    <a class="btn btn-sm btn-ghost" href="${api.reportUrl(job.id)}" target="_blank" rel="noopener">CSV</a>
                  </td>
                </tr>`;
              })
              .join("")}
          </tbody>
        </table>`;
      historyNode.querySelectorAll("[data-open]").forEach((button) => {
        button.addEventListener("click", () => navigate(`#/run/${button.dataset.open}`));
      });
    }
  } catch (error) {
    historyNode.innerHTML = `<div class="empty"><strong>历史记录读取失败</strong>${escapeHtml(error.message)}</div>`;
  }
}
