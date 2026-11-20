// 课程选择页：拉取课程列表、搜索、勾选、预览章节、创建学习任务
import { api } from "../api.js";
import { navigate } from "../router.js";
import { state } from "../state.js";
import { debounce, escapeHtml, statusClass, statusText, toast } from "../ui.js";

const previewCache = new Map();
const MAX_VISIBLE = 8;
let expanded = false;

function courseKey(course) {
  return `${course.courseId}::${course.clazzId}`;
}

function isSelected(course) {
  return state.selectedCourses.some((item) => courseKey(item) === courseKey(course));
}

function toggleSelection(course) {
  if (isSelected(course)) {
    state.selectedCourses = state.selectedCourses.filter((item) => courseKey(item) !== courseKey(course));
  } else {
    state.selectedCourses.push(course);
  }
}

function cardTemplate(course) {
  const key = courseKey(course);
  const preview = previewCache.get(key);
  const selected = isSelected(course);
  const avatarText = (course.title || "课").trim().slice(0, 1);

  let previewHtml = `<span class="badge">未预览</span>`;
  let percent = 0;
  if (preview?.loading) {
    previewHtml = `<span class="badge"><span class="spinner" style="width:11px;height:11px;border-width:2px"></span> 读取中</span>`;
  } else if (preview?.error) {
    previewHtml = `<span class="badge err">预览失败</span>`;
  } else if (preview) {
    percent = preview.total ? Math.round((preview.finished / preview.total) * 100) : 0;
    previewHtml = `<span class="badge ${preview.locked ? "warn" : percent === 100 ? "ok" : ""}">
        ${preview.finished}/${preview.total} 章${preview.locked ? " · 含锁定" : ""}
      </span>`;
  }

  return `
  <article class="course ${selected ? "selected" : ""}" data-key="${escapeHtml(key)}">
    <div class="course-top">
      <span class="course-avatar">${escapeHtml(avatarText)}</span>
      <div class="course-title">${escapeHtml(course.title || "未命名课程")}</div>
      <span class="check">
        <svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7"/></svg>
      </span>
    </div>
    <div class="course-sub">
      ${course.teacher ? `<span>${escapeHtml(course.teacher)}</span>` : ""}
      ${course.desc ? `<span>${escapeHtml(course.desc)}</span>` : ""}
      <span>班级 ${escapeHtml(course.clazzId || "-")}</span>
    </div>
    ${preview && !preview.error && !preview.loading ? `<div class="course-progress"><i style="width:${percent}%"></i></div>` : ""}
    <div class="course-foot">
      ${previewHtml}
      <button class="btn btn-sm btn-ghost" data-action="preview" type="button">预览章节</button>
    </div>
  </article>`;
}

function renderList(root, courses, keyword) {
  const list = root.querySelector("#course-grid");
  const more = root.querySelector("#course-more");
  const filtered = courses.filter((course) => {
    if (!keyword) return true;
    const haystack = `${course.title} ${course.teacher} ${course.desc} ${course.clazzId}`.toLowerCase();
    return haystack.includes(keyword.toLowerCase());
  });

  if (!filtered.length) {
    list.innerHTML = `<div class="empty" style="grid-column:1/-1"><strong>没有匹配的课程</strong>换个关键词试试，或点右上角刷新</div>`;
    if (more) more.innerHTML = "";
    return;
  }

  // 默认最多展示 8 个，搜索时全部展示
  const limit = keyword || expanded ? filtered.length : MAX_VISIBLE;
  const visible = filtered.slice(0, limit);
  list.innerHTML = visible.map(cardTemplate).join("");

  if (more) {
    const hidden = filtered.length - visible.length;
    if (hidden > 0) {
      more.innerHTML = `<button class="btn btn-ghost" id="more-btn" type="button">查看更多（还有 ${hidden} 门）</button>`;
    } else if (!keyword && expanded && filtered.length > MAX_VISIBLE) {
      more.innerHTML = `<button class="btn btn-ghost" id="collapse-btn" type="button">收起</button>`;
    } else {
      more.innerHTML = "";
    }
  }
}

function updateSummary(root, courses) {
  const countNode = root.querySelector("#selected-count");
  const startBtn = root.querySelector("#start-btn");
  if (countNode) countNode.textContent = String(state.selectedCourses.length);
  if (startBtn) startBtn.disabled = state.selectedCourses.length === 0;
}

export async function render(container) {
  expanded = false;
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>课程选择</h1>
        <p>勾选需要自动学习的课程，可先预览章节确认任务点数量与锁定情况</p>
      </div>
      <div class="page-actions">
        <input class="input" id="search" placeholder="搜索课程 / 教师" style="width:200px">
        <button class="btn btn-ghost" id="refresh" type="button">刷新列表</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <h2>我的课程</h2>
        <span class="hint" id="course-hint">正在读取…</span>
      </div>
      <div class="grid cols-3" id="course-grid"></div>
      <div id="course-more" style="text-align:center;margin-top:14px"></div>
    </div>

    <div class="card">
      <div class="card-title">
        <h2>答题方式</h2>
        <span class="hint" id="plan-hint">读取中…</span>
      </div>
      <div id="plan-summary" class="diag" style="background:var(--surface-2);color:var(--text-dim)">
        <span class="spinner"></span> 正在读取当前配置…
      </div>
      <div class="page-actions" style="margin-top:12px" id="plan-actions">
        <button class="btn btn-sm btn-ghost" data-mode="ai_only" type="button">仅 AI</button>
        <button class="btn btn-sm btn-ghost" data-mode="ai_first" type="button">AI 优先 · 题库兜底</button>
        <button class="btn btn-sm btn-ghost" data-mode="tiku_first" type="button">题库优先 · AI 兜底</button>
        <button class="btn btn-sm btn-ghost" data-mode="tiku_only" type="button">仅题库</button>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <h2>运行参数</h2>
        <span class="hint">仅对本次任务生效，默认值来自「设置」</span>
      </div>
      <div class="grid cols-2">
        <div class="field">
          <label>视频倍速 <span class="tip"><span id="speed-value">1.0</span>x（最大 2.0，运行中也能改）</span></label>
          <input type="range" id="speed" min="1" max="2" step="0.1" value="1">
        </div>
        <div class="field">
          <label>并发章节数 <span class="tip">建议 1（最稳）；2–4 更快但更容易触发风控</span></label>
          <input class="input" id="jobs" type="number" min="1" max="16" value="1">
        </div>
      </div>
    </div>

    <div class="card" style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>
        <strong style="font-size:14px">已选 <span id="selected-count">0</span> 门课程</strong>
        <p class="hint" style="margin:4px 0 0">开始后可在「运行进度」页查看实时状态，随时暂停或停止</p>
      </div>
      <button class="btn btn-primary" id="start-btn" type="button" disabled>开始学习</button>
    </div>
  `;

  const grid = container.querySelector("#course-grid");
  const hint = container.querySelector("#course-hint");
  const search = container.querySelector("#search");
  const speedInput = container.querySelector("#speed");
  const jobsInput = container.querySelector("#jobs");

  speedInput.addEventListener("input", () => {
    container.querySelector("#speed-value").textContent = Number(speedInput.value).toFixed(1);
  });

  // 答题方式：显示当前会用题库还是 AI，并提供一键切换
  const planSummary = container.querySelector("#plan-summary");
  const planHint = container.querySelector("#plan-hint");
  const planButtons = container.querySelectorAll("[data-mode]");

  function renderPlan(plan) {
    planHint.textContent = plan.label || "";
    const chainText = (plan.chain || []).map((item) => item.label).join(" → ") || "无";
    const tone = plan.mode === "none" ? "var(--err)" : plan.has_ai ? "var(--brand)" : "var(--text-dim)";
    planSummary.style.color = "var(--text-dim)";
    planSummary.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span class="badge ${plan.has_ai ? "brand" : ""}">${escapeHtml(plan.label || "-")}</span>
        <span style="font-size:12.5px;color:${tone}">${escapeHtml(chainText)}</span>
      </div>
      ${
        plan.warning
          ? `<div style="margin-top:8px;font-size:12px;color:var(--warn);display:flex;align-items:center;gap:10px;flex-wrap:wrap">
               <span>⚠ ${escapeHtml(plan.warning)}</span>
               <button class="btn btn-sm btn-ghost" id="goto-ai" type="button">去配置 AI →</button>
             </div>`
          : ""
      }`;
    planButtons.forEach((button) => {
      button.classList.toggle("btn-primary", button.dataset.mode === plan.mode);
      button.classList.toggle("btn-ghost", button.dataset.mode !== plan.mode);
    });

    const gotoAi = planSummary.querySelector("#goto-ai");
    if (gotoAi) gotoAi.addEventListener("click", () => navigate("#/ai"));
  }

  async function loadPlan() {
    try {
      const data = await api.answerPlan();
      renderPlan(data.plan);
    } catch (error) {
      planSummary.innerHTML = `<span style="color:var(--err)">读取失败：${escapeHtml(error.message)}</span>`;
    }
  }

  planButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const data = await api.setAnswerMode(button.dataset.mode);
        renderPlan(data.plan);
        toast(`答题方式已切换为：${data.plan.label}`, "ok");
      } catch (error) {
        toast(error.message, "err");
      } finally {
        button.disabled = false;
      }
    });
  });

  loadPlan();

  // 用「设置」里的默认值预填本次参数
  api
    .getSettings()
    .then((data) => {
      const common = data.settings?.common || {};
      if (common.speed) {
        speedInput.value = String(common.speed);
        container.querySelector("#speed-value").textContent = Number(common.speed).toFixed(1);
      }
      if (common.jobs) jobsInput.value = String(common.jobs);
    })
    .catch(() => {});

  let courses = [];

  async function load() {
    hint.textContent = "正在读取课程列表…";
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><span class="spinner"></span> 正在从学习通拉取课程…</div>`;
    try {
      const data = await api.courses();
      courses = data.courses || [];
      hint.textContent = `共 ${courses.length} 门课程`;
      renderList(container, courses, search.value.trim());
      // 已选课程可能不在新列表里，做一次交集
      state.selectedCourses = state.selectedCourses.filter((selected) =>
        courses.some((course) => courseKey(course) === courseKey(selected)),
      );
      updateSummary(container, courses);
    } catch (error) {
      if (error.status === 401) {
        toast("登录已失效，请重新登录", "warn");
        navigate("#/login");
        return;
      }
      hint.textContent = "读取失败";
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><strong>课程列表读取失败</strong>${escapeHtml(error.message)}</div>`;
    }
  }

  async function preview(course) {
    const key = courseKey(course);
    previewCache.set(key, { loading: true });
    renderList(container, courses, search.value.trim());
    try {
      const data = await api.points(course.courseId, course.clazzId, course.cpi);
      const points = data.points || [];
      previewCache.set(key, {
        total: points.length,
        finished: points.filter((point) => point.has_finished).length,
        locked: Boolean(data.hasLocked),
      });
    } catch (error) {
      previewCache.set(key, { error: error.message });
      toast(`章节预览失败：${error.message}`, "err");
    }
    renderList(container, courses, search.value.trim());
  }

  grid.addEventListener("click", async (event) => {
    const card = event.target.closest(".course");
    if (!card) return;
    const course = courses.find((item) => courseKey(item) === card.dataset.key);
    if (!course) return;

    if (event.target.closest('[data-action="preview"]')) {
      event.stopPropagation();
      await preview(course);
      return;
    }

    toggleSelection(course);
    card.classList.toggle("selected", isSelected(course));
    updateSummary(container, courses);
  });

  search.addEventListener("input", debounce(() => renderList(container, courses, search.value.trim()), 200));
  container.querySelector("#refresh").addEventListener("click", load);
  container.querySelector("#course-more").addEventListener("click", (event) => {
    if (event.target.id === "more-btn") {
      expanded = true;
      renderList(container, courses, search.value.trim());
    } else if (event.target.id === "collapse-btn") {
      expanded = false;
      renderList(container, courses, search.value.trim());
    }
  });

  container.querySelector("#start-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!state.selectedCourses.length) return;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 正在创建任务…';
    try {
      const result = await api.createJob(state.selectedCourses, {
        speed: Number(speedInput.value),
        jobs: Number(jobsInput.value),
      });
      toast("任务已创建，正在跳转…", "ok");
      navigate(`#/run/${result.job_id}`);
    } catch (error) {
      toast(error.message, "err");
      button.disabled = false;
      button.textContent = "开始学习";
    }
  });

  updateSummary(container, courses);
  await load();
}
