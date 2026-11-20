// 全局状态：登录态、题库元信息、设置缓存
export const state = {
  user: { loggedIn: false, name: "" },
  meta: { tiku_providers: [], notopen_actions: ["retry", "ask", "continue"], settings_path: "" },
  settings: null,
  selectedCourses: [],
};

export function setUser(payload) {
  state.user = {
    loggedIn: Boolean(payload.logged_in ?? payload.ok ?? false),
    name: payload.name || "",
  };
  renderUserChip();
}

export function setSettings(settings) {
  state.settings = settings;
}

export function renderUserChip() {
  const nameNode = document.getElementById("user-name");
  const stateNode = document.getElementById("user-state");
  const avatar = document.getElementById("avatar");
  if (!nameNode) return;

  if (state.user.loggedIn) {
    nameNode.textContent = state.user.name || "已登录";
    stateNode.textContent = "已连接学习通";
    avatar.textContent = (state.user.name || "U").slice(0, 1).toUpperCase();
  } else {
    nameNode.textContent = "未登录";
    stateNode.textContent = "点击登录";
    avatar.textContent = "?";
  }
}
