// 极简 hash 路由：视图按名注册，参数通过 hash 传递
const routes = new Map();
let container = null;
let onRouteChange = null;

export function register(name, handler) {
  routes.set(name, handler);
}

export function navigate(path) {
  const target = path.startsWith("#") ? path : `#${path}`;
  if (window.location.hash === target) {
    render();
    return;
  }
  window.location.hash = target;
}

export function currentRoute() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [name, ...params] = raw.split("/").filter(Boolean);
  return { name: name || "courses", params };
}

async function render() {
  if (!container) return;
  const { name, params } = currentRoute();
  const handler = routes.get(name) || routes.get("courses");
  container.innerHTML = "";
  try {
    await handler(container, params);
  } catch (error) {
    container.innerHTML = `<div class="card"><div class="empty"><strong>页面加载失败</strong>${error.message || error}</div></div>`;
  }
  if (onRouteChange) onRouteChange(name, params);
}

export function start(node, callback) {
  container = node;
  onRouteChange = callback;
  window.addEventListener("hashchange", render);
  render();
}
