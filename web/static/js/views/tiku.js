// 题库配置页：provider 回退链、各题库参数、提交策略、连通性自检
import { api } from "../api.js";
import { state, setSettings } from "../state.js";
import { escapeHtml, toast } from "../ui.js";

const PROVIDER_LABEL = {
  TikuYanxi: "言溪题库",
  TikuGo: "GO题",
  TikuLike: "LIKE 知识库",
  TikuAdapter: "TikuAdapter",
  AI: "AI 大模型（旧）",
  TikuAI: "AI 答题（推荐）",
  SiliconFlow: "硅基流动",
  TikuManual: "手动输入",
};

const PROVIDER_DESC = {
  TikuYanxi: "老牌题库，需 token，按题计费",
  TikuGo: "免费题库，可能有流控",
  TikuLike: "datam.site，支持视觉与联网",
  TikuAdapter: "自建适配器，填 url",
  AI: "旧的 AI 实现，参数填在下方",
  TikuAI: "在「AI 答题」页配置，支持一键自检",
  SiliconFlow: "硅基流动，需 API Key",
  TikuManual: "运行时由你手动输入答案",
};

function chainOf(tiku) {
  return String(tiku.provider || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function providerSection(tiku, selected) {
  const chips = (state.meta.tiku_providers || Object.keys(PROVIDER_LABEL))
    .map((name) => {
      const order = selected.indexOf(name);
      return `
      <button type="button" class="course ${order >= 0 ? "selected" : ""}" data-provider="${escapeHtml(name)}"
              style="padding:12px;align-items:flex-start;text-align:left">
        <div style="display:flex;align-items:center;gap:8px;width:100%">
          <strong style="font-size:13px">${escapeHtml(PROVIDER_LABEL[name] || name)}</strong>
          ${order >= 0 ? `<span class="badge brand">${order + 1}</span>` : ""}
        </div>
        <span style="font-size:11.5px;color:var(--muted);line-height:1.4">${escapeHtml(PROVIDER_DESC[name] || name)}</span>
      </button>`;
    })
    .join("");

  return `
  <div class="card">
    <div class="card-title">
      <h2>题库来源</h2>
      <span class="hint">按点击顺序组成回退链，前面的查不到才会用后面的</span>
    </div>
    <div class="grid cols-3" id="provider-grid">${chips}</div>
    <p class="hint" style="margin:12px 0 0">当前回退链：<code id="chain-text">${escapeHtml(selected.join(" → ") || "未选择")}</code></p>
  </div>`;
}

function aiSection(tiku) {
  return `
  <div class="card" id="ai-card" style="display:${chainOf(tiku).some((item) => ["AI", "SiliconFlow"].includes(item)) ? "" : "none"}">
    <div class="card-title"><h2>大模型参数</h2><span class="hint">AI 需要 OpenAI 兼容接口；硅基流动用专属两项</span></div>
    <div class="grid cols-2">
      <div class="field">
        <label>AI Endpoint</label>
        <input class="input" id="tiku-endpoint" placeholder="https://example.com/v1" value="${escapeHtml(tiku.endpoint || "")}">
      </div>
      <div class="field">
        <label>AI Key</label>
        <input class="input" id="tiku-key" placeholder="sk-..." value="${escapeHtml(tiku.key || "")}">
      </div>
      <div class="field">
        <label>AI 模型名</label>
        <input class="input" id="tiku-model" placeholder="deepseek-chat" value="${escapeHtml(tiku.model || "")}">
      </div>
      <div class="field">
        <label>请求代理 <span class="tip">可选</span></label>
        <input class="input" id="tiku-proxy" placeholder="http://127.0.0.1:7890" value="${escapeHtml(tiku.http_proxy || "")}">
      </div>
      <div class="field">
        <label>硅基流动 Key</label>
        <input class="input" id="tiku-sf-key" placeholder="sk-..." value="${escapeHtml(tiku.siliconflow_key || "")}">
      </div>
      <div class="field">
        <label>硅基流动模型</label>
        <input class="input" id="tiku-sf-model" value="${escapeHtml(tiku.siliconflow_model || "")}">
      </div>
    </div>
  </div>`;
}

function questionSection(tiku) {
  const cover = Math.round((Number(tiku.cover_rate) || 0) * 100);
  return `
  <div class="card">
    <div class="card-title"><h2>答题与提交策略</h2><span class="hint">建议先用「只保存不提交」跑一轮</span></div>

    <div class="grid cols-2">
      <div class="field">
        <label>题库 Token <span class="tip">多个用英文逗号分隔</span></label>
        <input class="input" id="tiku-tokens" placeholder="token1,token2" value="${escapeHtml(tiku.tokens || "")}">
      </div>
      <div class="field">
        <label>TikuAdapter URL <span class="tip">自建时填写</span></label>
        <input class="input" id="tiku-url" placeholder="http://127.0.0.1:3030" value="${escapeHtml(tiku.url || "")}">
      </div>
      <div class="field">
        <label>搜题间隔（秒） <span class="tip">太快容易触发题库限流</span></label>
        <input class="input" id="tiku-delay" type="number" step="0.1" min="0" value="${escapeHtml(tiku.delay ?? 1)}">
      </div>
      <div class="field">
        <label>判断题词表 <span class="tip">逗号分隔，勿加空格</span></label>
        <input class="input" id="tiku-true" placeholder="正确词表：正确,对,√,是" value="${escapeHtml(tiku.true_list || "")}">
        <input class="input" id="tiku-false" placeholder="错误词表：错误,错,×,否" value="${escapeHtml(tiku.false_list || "")}">
      </div>
    </div>

    <div class="field" style="margin-top:6px">
      <label>最低题库覆盖率 <span class="tip"><span id="cover-value">${cover}</span>% · 低于此值只保存不提交</span></label>
      <input type="range" id="tiku-cover" min="0" max="100" step="5" value="${cover}">
    </div>

    <div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:8px">
      <label class="switch">
        <input type="checkbox" id="tiku-submit" ${tiku.submit ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">自动提交答题（有风险）</span>
      </label>
      <label class="switch">
        <input type="checkbox" id="tiku-check-llm" ${tiku.check_llm_connection ? "checked" : ""}>
        <span class="track"></span>
        <span class="label">启动时检查大模型连接</span>
      </label>
    </div>

    <div class="card" style="margin-top:16px;padding:12px;background:var(--warn-soft);box-shadow:none;border-color:rgba(251,191,36,.3)">
      <p style="margin:0;font-size:12px;color:var(--warn)">
        ⚠️ 部分课程的章节测验只允许作答一次。开启自动提交前，建议先关闭它跑一轮，确认覆盖率后再打开。
      </p>
    </div>
  </div>`;
}

export async function render(container) {
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>题库配置</h1>
        <p>章节测验依赖题库自动作答；不配置题库时测验任务会被跳过</p>
      </div>
      <div class="page-actions">
        <button class="btn btn-ghost" id="test-btn" type="button">测试连接</button>
        <button class="btn btn-primary" id="save-btn" type="button">保存配置</button>
      </div>
    </div>
    <div id="tiku-body"><div class="card"><div class="empty"><span class="spinner"></span> 正在读取设置…</div></div></div>
  `;

  const body = container.querySelector("#tiku-body");
  let tiku = null;
  let selected = [];

  try {
    const data = await api.getSettings();
    setSettings(data.settings);
    tiku = data.settings.tiku;
    selected = chainOf(tiku);
  } catch (error) {
    body.innerHTML = `<div class="card"><div class="empty"><strong>读取设置失败</strong>${escapeHtml(error.message)}</div></div>`;
    return;
  }

  function paint() {
    body.innerHTML = `
      ${providerSection(tiku, selected)}
      ${aiSection(tiku)}
      ${questionSection(tiku)}
    `;
    wire();
  }

  function collect() {
    const get = (id) => body.querySelector(`#${id}`);
    const chain = selected.join(",");
    return {
      ...tiku,
      provider: chain,
      endpoint: get("tiku-endpoint")?.value ?? tiku.endpoint,
      key: get("tiku-key")?.value ?? tiku.key,
      model: get("tiku-model")?.value ?? tiku.model,
      http_proxy: get("tiku-proxy")?.value ?? tiku.http_proxy,
      siliconflow_key: get("tiku-sf-key")?.value ?? tiku.siliconflow_key,
      siliconflow_model: get("tiku-sf-model")?.value ?? tiku.siliconflow_model,
      tokens: get("tiku-tokens")?.value ?? tiku.tokens,
      url: get("tiku-url")?.value ?? tiku.url,
      delay: Number(get("tiku-delay")?.value ?? tiku.delay),
      true_list: get("tiku-true")?.value ?? tiku.true_list,
      false_list: get("tiku-false")?.value ?? tiku.false_list,
      cover_rate: Number(get("tiku-cover")?.value ?? 90) / 100,
      submit: Boolean(get("tiku-submit")?.checked),
      check_llm_connection: Boolean(get("tiku-check-llm")?.checked),
    };
  }

  function wire() {
    body.querySelectorAll("[data-provider]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const name = chip.dataset.provider;
        if (selected.includes(name)) selected = selected.filter((item) => item !== name);
        else selected.push(name);
        const chainNode = body.querySelector("#chain-text");
        if (chainNode) chainNode.textContent = selected.join(" → ") || "未选择";
        chip.classList.toggle("selected", selected.includes(name));
        const orderBadge = chip.querySelector(".badge");
        if (selected.includes(name)) {
          if (!orderBadge) {
            const badge = document.createElement("span");
            badge.className = "badge brand";
            badge.textContent = String(selected.indexOf(name) + 1);
            chip.querySelector("div").appendChild(badge);
          } else {
            orderBadge.textContent = String(selected.indexOf(name) + 1);
          }
        } else if (orderBadge) {
          orderBadge.remove();
        }
        // 重新编号
        body.querySelectorAll("[data-provider]").forEach((item) => {
          const index = selected.indexOf(item.dataset.provider);
          const badge = item.querySelector(".badge");
          if (badge) badge.textContent = String(index + 1);
        });
        const aiCard = body.querySelector("#ai-card");
        if (aiCard) {
          aiCard.style.display = selected.some((item) => ["AI", "SiliconFlow"].includes(item)) ? "" : "none";
        }
      });
    });

    const cover = body.querySelector("#tiku-cover");
    const coverValue = body.querySelector("#cover-value");
    if (cover && coverValue) {
      cover.addEventListener("input", () => {
        coverValue.textContent = cover.value;
      });
    }
  }

  paint();

  container.querySelector("#save-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 保存中…';
    try {
      tiku = collect();
      const payload = { ...state.settings, tiku };
      const data = await api.saveSettings(payload);
      setSettings(data.settings);
      tiku = data.settings.tiku;
      toast("题库配置已保存", "ok");
      paint();
    } catch (error) {
      toast(error.message, "err");
    } finally {
      button.disabled = false;
      button.textContent = "保存配置";
    }
  });

  container.querySelector("#test-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 测试中…';
    try {
      tiku = collect();
      await api.saveSettings({ ...state.settings, tiku });
      const result = await api.testTiku();
      if (result.ok) {
        toast(`题库可用${result.answer ? `，示例答案：${result.answer}` : ""}`, "ok", 6000);
      } else {
        toast(`测试失败：${result.msg}`, "err", 7000);
      }
    } catch (error) {
      toast(error.message, "err", 6000);
    } finally {
      button.disabled = false;
      button.textContent = "测试连接";
    }
  });
}
