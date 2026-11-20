// AI 答题页：服务商预设、地址/Key/模型配置、分步自检（含失败原因）
import { api } from "../api.js";
import { setSettings, state } from "../state.js";
import { escapeHtml, toast } from "../ui.js";

const PRESET_ORDER = ["deepseek", "openai", "siliconflow", "dashscope", "moonshot", "zhipu", "ollama", "custom"];

function presets() {
  return state.meta?.ai_presets || {};
}

function presetCards(current) {
  const list = presets();
  return PRESET_ORDER.filter((key) => list[key])
    .map((key) => {
      const item = list[key];
      return `
      <button type="button" class="course ${current === key ? "selected" : ""}" data-preset="${escapeHtml(key)}"
              style="padding:12px;text-align:left">
        <strong style="font-size:13px">${escapeHtml(item.label)}</strong>
        <span style="font-size:11.5px;color:var(--muted);line-height:1.4">${escapeHtml(item.hint || item.base_url || "")}</span>
      </button>`;
    })
    .join("");
}

function configSection(ai) {
  return `
  <div class="card">
    <div class="card-title">
      <h2>接口配置</h2>
      <span class="hint">兼容 OpenAI 协议即可；地址会自动补全 /v1 与 /chat/completions</span>
    </div>

    <div class="field">
      <label>API 地址 <span class="tip">例：https://api.deepseek.com/v1</span></label>
      <input class="input" id="ai-base" placeholder="https://api.deepseek.com/v1" value="${escapeHtml(ai.base_url || "")}">
    </div>

    <div class="grid cols-2">
      <div class="field">
        <label>API Key <span class="tip">只保存在本机</span></label>
        <input class="input" id="ai-key" type="password" placeholder="sk-..." value="${escapeHtml(ai.api_key || "")}">
        <div id="ai-key-link"></div>
      </div>
      <div class="field">
        <label>模型名 <span class="tip">例：deepseek-chat</span></label>
        <input class="input" id="ai-model" placeholder="deepseek-chat" value="${escapeHtml(ai.model || "")}">
      </div>
    </div>

    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:4px">
      <label class="switch">
        <input type="checkbox" id="ai-enabled" ${ai.use_for_answer ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">答题时使用 AI（优先于题库）</span>
      </label>
      <label class="switch">
        <input type="checkbox" id="ai-show-key">
        <span class="track"></span>
        <span class="label">显示 Key</span>
      </label>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><h2>高级参数</h2><span class="hint">一般保持默认即可</span></div>
    <div class="grid cols-2">
      <div class="field">
        <label>温度 <span class="tip"><span id="ai-temp-value">${ai.temperature ?? 0.2}</span>（越低越稳定）</span></label>
        <input type="range" id="ai-temperature" min="0" max="1.5" step="0.1" value="${ai.temperature ?? 0.2}">
      </div>
      <div class="field">
        <label>最大输出 tokens</label>
        <input class="input" id="ai-max-tokens" type="number" min="64" step="64" value="${ai.max_tokens ?? 1024}">
      </div>
      <div class="field">
        <label>超时（秒）</label>
        <input class="input" id="ai-timeout" type="number" min="5" step="5" value="${ai.timeout ?? 60}">
      </div>
      <div class="field">
        <label>请求间隔（秒） <span class="tip">避免限流</span></label>
        <input class="input" id="ai-interval" type="number" min="0" step="0.5" value="${ai.min_interval_seconds ?? 3}">
      </div>
      <div class="field">
        <label>代理 <span class="tip">可选，例 http://127.0.0.1:7890</span></label>
        <input class="input" id="ai-proxy" placeholder="留空表示直连" value="${escapeHtml(ai.proxy || "")}">
      </div>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:4px">
      <label class="switch">
        <input type="checkbox" id="ai-system-certs" ${ai.use_system_certs !== false ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">使用系统证书（公司网络/自签证书建议开启）</span>
      </label>
      <label class="switch">
        <input type="checkbox" id="ai-insecure" ${ai.insecure ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">跳过证书校验（不安全，最后手段）</span>
      </label>
    </div>
  </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>AI 答题</h1>
        <p>接入任意 OpenAI 兼容接口，配置后可一键自检；开启后章节测验优先由 AI 作答</p>
      </div>
    </div>
    <div id="ai-body"><div class="card"><div class="empty"><span class="spinner"></span> 正在读取配置…</div></div></div>
    <div id="ai-result" style="margin-top:16px"></div>
    <div class="card" style="margin-top:16px;display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap">
      <button class="btn btn-ghost" id="sample-btn" type="button">答一题试试</button>
      <button class="btn btn-primary" id="test-btn" type="button">测试并保存</button>
    </div>
  `;

  const body = container.querySelector("#ai-body");
  const resultNode = container.querySelector("#ai-result");
  let settings = null;
  let ai = null;

  try {
    const data = await api.getSettings();
    settings = data.settings;
    ai = settings.ai;
    setSettings(settings);
  } catch (error) {
    body.innerHTML = `<div class="card"><div class="empty"><strong>读取设置失败</strong>${escapeHtml(error.message)}</div></div>`;
    return;
  }

  function paint() {
    body.innerHTML = `
      <div class="card">
        <div class="card-title"><h2>选择服务商</h2><span class="hint">点一下自动填好地址与常用模型</span></div>
        <div class="grid cols-4" id="preset-grid">${presetCards(ai.preset)}</div>
      </div>
      ${configSection(ai)}
    `;
    wire();
    updateKeyLink(ai.preset);
  }

  function collect() {
    return {
      ...ai,
      base_url: body.querySelector("#ai-base").value.trim(),
      api_key: body.querySelector("#ai-key").value.trim(),
      model: body.querySelector("#ai-model").value.trim(),
      temperature: Number(body.querySelector("#ai-temperature").value),
      max_tokens: Number(body.querySelector("#ai-max-tokens").value),
      timeout: Number(body.querySelector("#ai-timeout").value),
      min_interval_seconds: Number(body.querySelector("#ai-interval").value),
      proxy: body.querySelector("#ai-proxy").value.trim(),
      use_system_certs: body.querySelector("#ai-system-certs").checked,
      insecure: body.querySelector("#ai-insecure").checked,
      use_for_answer: body.querySelector("#ai-enabled").checked,
    };
  }

  function updateKeyLink(presetKey) {
    const node = body.querySelector("#ai-key-link");
    if (!node) return;
    const preset = presets()[presetKey] || {};
    if (!preset.key_url) {
      node.innerHTML = "";
      return;
    }
    node.innerHTML = `
      <a href="${escapeHtml(preset.key_url)}" target="_blank" rel="noopener noreferrer"
         style="font-size:12px;color:var(--brand);text-decoration:none">
        没有 Key？去 ${escapeHtml(preset.label || "服务商")} 官网申请 →
      </a>
      <span class="hint" style="display:block;margin-top:2px">${escapeHtml(preset.key_url)}</span>`;
  }

  function wire() {
    body.querySelectorAll("[data-preset]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const key = chip.dataset.preset;
        const preset = presets()[key];
        if (!preset) return;
        ai.preset = key;
        body.querySelector("#ai-base").value = preset.base_url || "";
        body.querySelector("#ai-model").value = preset.model || "";
        body.querySelectorAll("[data-preset]").forEach((item) => {
          item.classList.toggle("selected", item.dataset.preset === key);
        });
        updateKeyLink(key);
        toast(`已切换到 ${preset.label}`, "info", 1800);
      });
    });

    body.querySelector("#ai-show-key").addEventListener("change", (event) => {
      body.querySelector("#ai-key").type = event.target.checked ? "text" : "password";
    });

    const temp = body.querySelector("#ai-temperature");
    temp.addEventListener("input", () => {
      body.querySelector("#ai-temp-value").textContent = Number(temp.value).toFixed(1);
    });
  }

  paint();

  function renderSteps(report) {
    const steps = (report.steps || [])
      .map((step) => {
        const cls = step.ok === true ? "ok" : step.ok === false ? "fail" : "skip";
        const icon = step.ok === true ? "✓" : step.ok === false ? "✕" : "–";
        return `
        <div class="step ${cls}">
          <span class="icon">${icon}</span>
          <div class="body">
            <strong>${escapeHtml(step.name)}</strong>
            <span>${escapeHtml(step.detail || "")}</span>
          </div>
          <span class="ms">${step.ms ? step.ms + "ms" : ""}</span>
        </div>`;
      })
      .join("");

    const verdict = report.ok
      ? `<div class="diag ok">✓ 接口可用${report.sample ? `，示例返回：${escapeHtml(report.sample)}` : ""}${
          report.elapsed_ms ? `（${report.elapsed_ms}ms）` : ""
        }</div>`
      : `<div class="diag fail">
           <strong>✕ ${escapeHtml(report.reason || "测试未通过")}</strong>
           ${report.suggestion ? `<div style="margin-top:6px;font-size:12.5px">建议：${escapeHtml(report.suggestion)}</div>` : ""}
         </div>`;

    const chain = state.settings?.tiku?.provider || "";
    resultNode.innerHTML = `
      <div class="card">
        <div class="card-title">
          <h2>自检结果</h2>
          <span class="hint">${report.resolved_base ? `实际使用：${escapeHtml(report.resolved_base)}` : ""}</span>
        </div>
        ${verdict}
        <div class="steps" style="margin-top:14px">${steps || '<div class="empty">没有可展示的步骤</div>'}</div>
        ${
          report.ok
            ? `<p class="hint" style="margin:14px 0 0">当前答题顺序：<code>${escapeHtml(chain || "未设置")}</code>
               ${chain.includes("TikuAI") ? "（AI 优先）" : ""}</p>`
            : ""
        }
      </div>`;
  }

  container.querySelector("#test-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 测试中…';
    resultNode.innerHTML = `<div class="card"><div class="empty"><span class="spinner"></span> 正在测试接口，可能需要几秒…</div></div>`;

    try {
      ai = collect();
      const saved = await api.saveSettings({ ...settings, ai });
      settings = saved.settings;
      setSettings(settings);
      ai = settings.ai;

      const report = await api.aiTest(ai);
      renderSteps(report);
      if (report.ok) {
        toast("接口可用，配置已保存", "ok");
      } else {
        toast(`测试失败：${report.reason}`, "err", 6000);
      }
    } catch (error) {
      resultNode.innerHTML = `<div class="card"><div class="diag fail">✕ ${escapeHtml(error.message)}</div></div>`;
      toast(error.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = "测试并保存";
    }
  });

  container.querySelector("#sample-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 作答中…';
    try {
      const result = await api.aiAnswerSample();
      if (result.ok) {
        toast(`AI 回答示例题：${result.answer}（${result.elapsed}s）`, "ok", 8000);
      } else {
        toast(result.msg || "AI 未返回答案", "err", 6000);
      }
    } catch (error) {
      toast(error.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = "答一题试试";
    }
  });
}
