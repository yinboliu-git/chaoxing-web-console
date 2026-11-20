// 登录页：账号密码 / Cookie 两种方式
import { api } from "../api.js";
import { navigate } from "../router.js";
import { setUser, state } from "../state.js";
import { escapeHtml, toast } from "../ui.js";

const PHONE_KEY = "chaoxing-phone";

function template() {
  let savedPhone = "";
  try {
    savedPhone = localStorage.getItem(PHONE_KEY) || "";
  } catch {
    /* 忽略 */
  }

  return `
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-hero">
        <div>
          <div class="brand" style="padding:0">
            <img class="brand-mark" src="/static/img/icon.png" alt="chaoxing">
            <div class="brand-text">
              <strong>chaoxing</strong>
              <span>学习通自动化控制台</span>
            </div>
          </div>
          <h2 style="margin-top:26px">刷课、答题、进度<br>一个页面全搞定</h2>
          <p>登录后即可选择课程、实时查看进度、配置题库并自动答题。</p>
        </div>
        <ul class="hero-list">
          <li>视频 / 文档 / 阅读任务点自动完成</li>
          <li>章节测验接入 7 种题库，支持回退链</li>
          <li>进度、日志实时推送到浏览器</li>
          <li>所有数据只留在本机，不上传任何服务器</li>
        </ul>
        <p class="hero-foot">仅供学习交流 · GPL-3.0 · 请遵守学校规定</p>
      </div>

      <div class="login-form">
        <h3>欢迎回来</h3>
        <p class="sub">使用你的学习通账号登录，或直接复用浏览器 Cookie</p>

        <div class="diag" style="background:var(--ok-soft);border-color:rgba(52,211,153,.28);color:var(--ok);margin-bottom:16px;font-size:12.5px">
          🔒 本服务运行在你自己的电脑本地（127.0.0.1）
        </div>

        <div class="tabs" id="tabs">
          <button class="tab active" data-mode="password" type="button">账号登录</button>
          <button class="tab" data-mode="cookie" type="button">Cookie 登录</button>
        </div>

        <div class="form-error" id="form-error"></div>

        <form id="login-form" autocomplete="off">
          <div class="field">
            <label for="username">手机号</label>
            <input class="input" id="username" name="username" placeholder="请输入学习通手机号" value="${escapeHtml(savedPhone)}">
          </div>
          <div class="field">
            <label for="password">密码</label>
            <input class="input" id="password" name="password" type="password" placeholder="请输入密码">
          </div>
          <div class="field" style="flex-direction:row;align-items:center;justify-content:space-between">
            <label class="switch">
              <input type="checkbox" id="remember" checked>
              <span class="track"></span>
              <span class="label">记住手机号</span>
            </label>
          </div>
          <button class="btn btn-primary btn-block" id="submit-btn" type="submit">登录</button>
        </form>

        <div id="cookie-hint" style="display:none">
          <div class="field">
            <label>Cookie 登录说明 <span class="tip">适合有验证码 / 异地登录限制</span></label>
            <div class="card" style="box-shadow:none;padding:14px;background:var(--surface-2)">
              <p style="margin:0 0 8px;font-size:12.5px;color:var(--text-dim)">
                1. 浏览器登录 <code>i.chaoxing.com</code>，用开发者工具复制 Cookie<br>
                2. 把 Cookie 内容保存为程序目录下的 <code>cookies.txt</code><br>
                3. 点击下方按钮直接使用该 Cookie 登录
              </p>
              <p style="margin:0;font-size:11.5px;color:var(--muted)" id="cookie-path"></p>
            </div>
          </div>
          <button class="btn btn-primary btn-block" id="cookie-btn" type="button">用 cookies.txt 登录</button>
        </div>

        <p style="margin:18px 0 0;font-size:11.5px;color:var(--muted)">
          密码仅用于向超星发起登录请求，不会写入磁盘。
        </p>
      </div>
    </div>
  </div>`;
}

export async function render(container) {
  if (state.user.loggedIn) {
    navigate("#/courses");
    return;
  }
  container.innerHTML = template();

  const form = container.querySelector("#login-form");
  const errorBox = container.querySelector("#form-error");
  const submitBtn = container.querySelector("#submit-btn");
  const cookieHint = container.querySelector("#cookie-hint");
  const cookieBtn = container.querySelector("#cookie-btn");
  const tabs = container.querySelectorAll(".tab");

  if (state.meta?.settings_path) {
    container.querySelector("#cookie-path").textContent =
      `设置目录：${state.meta.settings_path.replace(/settings\.json$/, "cookies.txt")}`;
  }

  let mode = "password";
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      mode = tab.dataset.mode;
      tabs.forEach((item) => item.classList.toggle("active", item === tab));
      form.style.display = mode === "password" ? "" : "none";
      cookieHint.style.display = mode === "cookie" ? "" : "none";
      errorBox.classList.remove("show");
    });
  });

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.add("show");
  }

  function setLoading(button, loading, label) {
    button.disabled = loading;
    button.innerHTML = loading ? '<span class="spinner"></span> 登录中…' : label;
  }

  async function doLogin(payload) {
    try {
      const result = await api.login(payload);
      setUser({ logged_in: true, name: result.name });
      toast(`登录成功，欢迎 ${result.name || ""}`, "ok");
      navigate("#/courses");
    } catch (error) {
      setUser({ logged_in: false });
      showError(error.message || "登录失败");
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = container.querySelector("#username").value.trim();
    const password = container.querySelector("#password").value;
    const remember = container.querySelector("#remember").checked;

    if (!username || !password) {
      showError("请填写手机号和密码");
      return;
    }

    try {
      if (remember) localStorage.setItem(PHONE_KEY, username);
      else localStorage.removeItem(PHONE_KEY);
    } catch {
      /* 忽略 */
    }

    setLoading(submitBtn, true);
    await doLogin({ username, password, use_cookies: false });
    setLoading(submitBtn, false, "登录");
  });

  cookieBtn.addEventListener("click", async () => {
    setLoading(cookieBtn, true);
    await doLogin({ use_cookies: true, username: "", password: "" });
    setLoading(cookieBtn, false, "用 cookies.txt 登录");
  });
}
