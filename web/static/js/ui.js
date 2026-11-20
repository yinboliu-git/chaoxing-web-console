// 通用 UI 小工具：转义、提示、时间格式化、状态文案
const TOAST_ICONS = { ok: "✓", err: "!", warn: "!", info: "i" };

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function toast(message, type = "info", timeout = 3800) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.innerHTML = `<span class="badge ${type === "ok" ? "ok" : type === "err" ? "err" : type === "warn" ? "warn" : ""}">${
    TOAST_ICONS[type] || "i"
  }</span> ${escapeHtml(message)}`;
  host.appendChild(node);
  window.setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateX(12px)";
    node.style.transition = "all .2s ease";
    window.setTimeout(() => node.remove(), 220);
  }, timeout);
}

export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes > 0) return `${minutes}m ${String(secs).padStart(2, "0")}s`;
  return `${secs}s`;
}

export function timeAgo(isoString) {
  if (!isoString) return "-";
  const then = new Date(isoString).getTime();
  if (Number.isNaN(then)) return isoString;
  const diff = Math.max(0, Date.now() - then);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

export const STATUS_TEXT = {
  success: "完成",
  failed: "失败",
  skipped: "跳过",
  running: "运行中",
  retrying: "重试中",
  pending: "未开始",
  ready: "就绪",
  preparing: "读取中",
  finished: "已完成",
  stopped: "已停止",
  interrupted: "被中断",
  error: "出错",
  queued: "排队中",
};

export function statusText(status) {
  return STATUS_TEXT[status] || status || "-";
}

export function statusClass(status) {
  if (["success", "finished"].includes(status)) return "success";
  if (["failed", "error"].includes(status)) return "failed";
  if (["running", "retrying", "preparing"].includes(status)) return status === "retrying" ? "retrying" : "running";
  if (status === "pending") return "pending";
  if (["skipped", "stopped", "interrupted", "queued"].includes(status)) return "skipped";
  return "pending";
}

export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}
