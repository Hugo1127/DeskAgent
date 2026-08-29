// WebSocket 封装：实时事件流（自动重连）与运行回放（单次连接）。

import { getAuthToken } from "./api.js";

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

function buildQuery(csrf, extra) {
  let url = `/ws?csrf=${encodeURIComponent(csrf || "")}`;
  const token = getAuthToken();
  if (token) url += `&token=${encodeURIComponent(token)}`;
  if (extra) url += extra;
  return url;
}

// 实时事件流：断线指数退避自动重连；code 1008（安全校验拒绝）视为致命错误不再重连
export function connectLiveWS({ csrf, onEvent, onStatus, onOpen, onClose }) {
  let ws = null;
  let closed = false;
  let retryDelay = 1000;
  let retryTimer = null;

  function connect() {
    if (closed) return;
    ws = new WebSocket(wsUrl(buildQuery(csrf)));

    ws.onopen = () => {
      retryDelay = 1000;
      if (onOpen) onOpen();
    };
    ws.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.kind === "event") {
        if (onEvent) onEvent(msg.event);
      } else if (msg.kind === "status") {
        if (onStatus) onStatus(msg.connected);
      }
    };
    ws.onclose = (e) => {
      if (onClose) onClose();
      if (closed || e.code === 1008) return;
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    };
    ws.onerror = () => {
      /* onclose 随后触发 */
    };
  }

  connect();
  return {
    close() {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws) ws.close();
    },
  };
}

// 回放连接：单次拉取历史事件后关闭（不自动重连）
export function connectReplayWS({ csrf, runId, onEvent, onReplayEnd, onDone }) {
  const ws = new WebSocket(wsUrl(buildQuery(csrf, `&replay=${encodeURIComponent(runId)}`)));

  ws.onmessage = (e) => {
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    if (msg.kind === "event") {
      if (onEvent) onEvent(msg.event);
    } else if (msg.kind === "replay_end") {
      if (onReplayEnd) onReplayEnd(msg);
    }
  };
  ws.onclose = () => {
    if (onDone) onDone();
  };
  ws.onerror = () => {
    /* onclose 随后触发 */
  };

  return { close: () => ws.close() };
}
