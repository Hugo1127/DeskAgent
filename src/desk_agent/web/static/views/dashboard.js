// 仪表盘视图：daemon 状态（在线/离线、版本、uptime、模型、max_steps、权限超时）+ 会话与运行统计。

import { getState, loadRuns, loadSessions, on, refreshStatus } from "../store.js";
import { el, formatDuration } from "../components.js";

function statCard(title, value, hint = "") {
  return el("div", { class: "card stat" },
    el("div", { class: "stat-title" }, title),
    el("div", { class: "stat-value" }, value),
    hint ? el("div", { class: "stat-hint" }, hint) : null,
  );
}

export function initDashboard(root, { onOpenChat, onOpenRuns }) {
  const container = el("div", { class: "dashboard" });
  const statusBadge = el("span", { class: "badge badge-off" }, "offline");
  const statusLine = el("div", { class: "dash-status" });

  const header = el("div", { class: "dash-head" },
    el("h2", {}, "Daemon"),
    statusBadge,
    el("button", { class: "btn btn-ghost btn-sm", onclick: () => refresh() }, "↻ Refresh"),
  );

  const grid = el("div", { class: "stat-grid" });
  const offlineHint = el("div", { class: "offline-hint" });
  const nav = el("div", { class: "dash-nav" },
    el("button", { class: "btn btn-primary", onclick: () => onOpenChat && onOpenChat() }, "Open chat"),
    el("button", { class: "btn btn-ghost", onclick: () => onOpenRuns && onOpenRuns() }, "View runs"),
  );

  container.append(header, statusLine, grid, offlineHint, nav);
  root.appendChild(container);

  let lastStatus = null;

  async function refresh() {
    try {
      lastStatus = await refreshStatus();
    } catch {
      lastStatus = { online: false };
    }
    try {
      await loadSessions();
    } catch {
      /* daemon 离线时忽略 */
    }
    try {
      await loadRuns();
    } catch {
      /* daemon 离线时忽略 */
    }
    render();
  }

  function render() {
    const s = getState();
    const online = !!(lastStatus && lastStatus.online);
    statusBadge.className = "badge " + (online ? "badge-on" : "badge-off");
    statusBadge.textContent = online ? "online" : "offline";

    statusLine.textContent = online
      ? `daemon ${s.model} · max_steps ${s.maxSteps} · permission timeout ${s.permissionTimeoutS}s`
      : "daemon offline — start it with: uv run desk core start";

    offlineHint.replaceChildren();
    if (!online) {
      offlineHint.appendChild(el("div", { class: "offline-hint-text" },
        "请先启动守护进程：", el("code", {}, "uv run desk core start"),
        "，随后点击 Refresh（desk-web 会自动重连）。"));
    }

    grid.replaceChildren(
      statCard("Version", online ? (lastStatus.version || s.version) : "—"),
      statCard("Uptime", online ? formatDuration(lastStatus.uptime_ms) : "—"),
      statCard("Model", s.model),
      statCard("Max steps", String(s.maxSteps)),
      statCard("Permission timeout", `${s.permissionTimeoutS}s`),
      statCard("Sessions", String(s.sessions.length)),
      statCard("Runs", String(s.runs.length)),
      statCard("WS", s.connected ? "connected" : "reconnecting…"),
    );
  }

  const off = on("state", render);
  refresh();
  render();

  return () => {
    off();
    root.removeChild(container);
  };
}
