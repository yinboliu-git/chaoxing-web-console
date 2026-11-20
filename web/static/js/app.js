// 应用入口：主题、导航、路由注册与登录守卫
import { api } from "./api.js";
import { currentRoute, navigate, register, start } from "./router.js";
import { renderUserChip, setSettings, setUser, state } from "./state.js";
import { toast } from "./ui.js";

import * as aiView from "./views/ai.js";
import * as coursesView from "./views/courses.js";
import * as loginView from "./views/login.js";
import * as runView from "./views/run.js";
import * as settingsView from "./views/settings.js";
import * as tikuView from "./views/tiku.js";

const THEME_KEY = "chaoxing-theme";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* 隐私模式下忽略 */
  }
}

function initTheme() {
  let theme = "dark";
  try {
    theme = localStorage.getItem(THEME_KEY) || "dark";
  } catch {
    /* 忽略 */
  }
  applyTheme(theme);

  const button = document.getElementById("theme-toggle");
  if (button) {
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      toast(next === "dark" ? "已切换为深色主题" : "已切换为浅色主题", "info", 1800);
    });
  }
}

function highlightNav(name) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.route === name);
  });
}

async function boot() {
  initTheme();
  renderUserChip();

  try {
    state.meta = await api.meta();
  } catch {
    /* 元信息失败不阻塞 */
  }

  register("login", loginView.render);
  register("courses", coursesView.render);
  register("run", runView.render);
  register("tiku", tikuView.render);
  register("ai", aiView.render);
  register("settings", settingsView.render);

  try {
    const me = await api.me();
    setUser(me);
  } catch {
    setUser({ logged_in: false });
  }

  const view = document.getElementById("view");
  if (!state.user.loggedIn && currentRoute().name !== "login") {
    window.location.hash = "#/login";
  } else if (state.user.loggedIn && currentRoute().name === "login") {
    window.location.hash = "#/courses";
  }

  start(view, (name) => {
    highlightNav(name);
    document.getElementById("app")?.classList.toggle("is-login", name === "login");
  });

  // 未登录时点侧边栏应回到登录页
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", (event) => {
      if (!state.user.loggedIn) {
        event.preventDefault();
        navigate("#/login");
      }
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    const message = event.reason?.message || "发生未知错误";
    toast(message, "err");
  });
}

document.addEventListener("DOMContentLoaded", boot);
