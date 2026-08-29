// 引导 + 视图切换 + 登录 + 实时 WS 连接 + 顶部状态栏。

import {
  bootstrap,
  createSession,
  dispatchEvent,
  getState,
  loadSessions,
  login,
  logout,
  on,
  refreshStatus,
  setConnected,
  setOnline,
  switchSession,
} from "./store.js";
import { connectLiveWS } from "./ws.js";
import { initChat } from "./views/chat.js";
import { initDashboard } from "./views/dashboard.js";
import { initRuns } from "./views/runs.js";
import { el } from "./components.js";

let liveWS = null;
let cleanupView = null;

const viewFactories = {
  chat: (root) => initChat(root),
  dashboard: (root) => initDashboard(root, {
    onOpenChat: () => showView("chat"),
    onOpenRuns: () => showView("runs"),
  }),
  runs: (root) => initRuns(root),
};

function buildShell() {
  const app = document.getElementById("app");
  const daemonDot = el("span", { class: "status-dot status-dot-off" });
  const statusText = el("span", { class: "topbar-status" }, "connecting…");
  const logoutBtn = el("button", {
    class: "btn btn-ghost btn-sm",
    hidden: true,
    onclick: () => {
      logout();
      location.reload();
    },
  }, "Logout");

  const topbar = el("header", { class: "topbar" },
    el("span", { class: "brand" }, "DeskAgent"),
    daemonDot,
    statusText,
    el("span", { class: "spacer" }),
    logoutBtn,
  );

  const nav = el("nav", { class: "nav" },
    el("button", { class: "nav-btn", "data-view": "chat" }, "Chat"),
    el("button", { class: "nav-btn", "data-view": "dashboard" }, "Dashboard"),
    el("button", { class: "nav-btn", "data-view": "runs" }, "Runs"),
  );
  const view = el("main", { class: "view", id: "view" });

  app.append(topbar, nav, view);

  nav.addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-btn");
    if (btn) showView(btn.dataset.view);
  });

  // 顶部状态栏刷新
  on("state", () => {
    const s = getState();
    const online = s.online && s.connected;
    daemonDot.className = "status-dot " + (s.online ? "status-dot-on" : "status-dot-off");
    if (!s.connected) statusText.textContent = "reconnecting…";
    else if (!s.online) statusText.textContent = "daemon offline";
    else statusText.textContent = "daemon " + s.version;
    logoutBtn.hidden = !s.authRequired;
  });
}

function showView(name) {
  if (cleanupView) {
    try {
      cleanupView();
    } catch {
      /* 忽略视图清理异常 */
    }
    cleanupView = null;
  }
  const view = document.getElementById("view");
  view.replaceChildren();
  const factory = viewFactories[name] || viewFactories.chat;
  cleanupView = factory(view);
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
}

function startLiveWS() {
  const { csrfToken } = getState();
  liveWS = connectLiveWS({
    csrf: csrfToken,
    onEvent: (evt) => dispatchEvent(evt),
    onStatus: (online) => setOnline(online),
    onOpen: () => {
      setConnected(true);
      refreshStatus();
    },
    onClose: () => setConnected(false),
  });
}

async function ensureSession() {
  if (getState().currentSessionId) return;
  try {
    await loadSessions();
  } catch {
    /* daemon 离线 */
  }
  const { sessions } = getState();
  if (sessions.length) {
    switchSession(sessions[0].id);
  } else {
    try {
      await createSession();
    } catch {
      /* daemon 离线时无会话，聊天视图显示提示 */
    }
  }
}

function start() {
  startLiveWS();
  showView("chat");
  refreshStatus();
  ensureSession();
}

function showLogin() {
  if (cleanupView) {
    cleanupView();
    cleanupView = null;
  }
  const view = document.getElementById("view");
  view.replaceChildren();
  const input = el("input", {
    type: "password",
    class: "login-input",
    placeholder: "Enter DESK_WEB_TOKEN",
  });
  const error = el("div", { class: "login-error" });

  view.appendChild(el("div", { class: "login" },
    el("h2", {}, "Login required"),
    el("p", { class: "login-hint" }, "This desk-web instance requires a token."),
    input,
    el("button", {
      class: "btn btn-primary",
      onclick: async () => {
        const val = input.value.trim();
        if (!val) return;
        error.textContent = "";
        try {
          await login(val);
          start();
        } catch (e) {
          error.textContent = "Login failed: " + e.message;
        }
      },
    }, "Login"),
    error,
  ));
  input.focus();
}

function showFatal(message) {
  const view = document.getElementById("view");
  view.replaceChildren(el("div", { class: "fatal" },
    "Failed to start: " + message));
}

async function init() {
  try {
    const data = await bootstrap();
    if (data.auth_required && !getState().authenticated) {
      showLogin();
      return;
    }
    start();
  } catch (e) {
    showFatal(e.message);
  }
}

buildShell();
init();
