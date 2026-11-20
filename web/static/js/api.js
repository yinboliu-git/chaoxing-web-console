// 统一的 API 客户端：所有请求都走 /api，错误统一抛 ApiError
const BASE = "/api";

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload || {};
  }
}

async function request(path, { method = "GET", body, params } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, value);
    }
  }

  const init = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    throw new ApiError("无法连接本地服务，请确认控制台仍在运行", 0);
  }

  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError(text.slice(0, 200), response.status);
    }
  }

  if (!response.ok) {
    throw new ApiError(data.msg || `请求失败（HTTP ${response.status}）`, response.status, data);
  }
  return data;
}

export const api = {
  health: () => request("/health"),
  meta: () => request("/meta"),

  login: (payload) => request("/auth/login", { method: "POST", body: payload }),
  me: () => request("/auth/me"),
  logout: () => request("/auth/logout", { method: "POST" }),

  courses: () => request("/courses"),
  points: (courseId, clazzId, cpi) =>
    request(`/courses/${encodeURIComponent(courseId)}/${encodeURIComponent(clazzId)}/points`, { params: { cpi } }),

  getSettings: () => request("/settings"),
  saveSettings: (settings) => request("/settings", { method: "PUT", body: settings }),
  exportIni: () => request("/settings/export-ini", { method: "POST" }),
  importIni: (path) => request("/settings/import-ini", { method: "POST", body: { path } }),

  testTiku: () => request("/tiku/test", { method: "POST" }),
  answerPlan: () => request("/answer-plan"),
  setAnswerMode: (mode) => request("/answer-plan", { method: "POST", body: { mode } }),
  aiTest: (ai) => request("/ai/test", { method: "POST", body: ai ? { ai } : {} }),
  aiAnswerSample: () => request("/ai/answer-sample", { method: "POST" }),

  createJob: (courses, options) => request("/jobs", { method: "POST", body: { courses, options } }),
  listJobs: (limit = 50) => request("/jobs", { params: { limit } }),
  getJob: (id) => request(`/jobs/${id}`),
  controlJob: (id, action, value) => request(`/jobs/${id}/control`, { method: "POST", body: { action, value } }),
  resumeJob: (id) => request(`/jobs/${id}/resume`, { method: "POST" }),
  deleteJob: (id) => request(`/jobs/${id}`, { method: "DELETE" }),
  reportUrl: (id) => `${BASE}/jobs/${id}/report.csv`,
  eventsUrl: (id) => `${BASE}/jobs/${id}/events`,
};
