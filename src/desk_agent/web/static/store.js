// 订阅发布 store：视图层唯一数据入口。视图只消费 store 事件与状态，不直接碰 WS/REST。

import { api, getAuthToken, setAuthToken, setCsrfToken } from "./api.js";

const state = {
  csrfToken: null,
  authRequired: false,
  authenticated: false,
  connected: false, // 到 desk-web 的 WS 已连接
  online: false, // daemon 在线
  version: "",
  model: "",
  maxSteps: 20,
  permissionTimeoutS: 60,
  sessions: [],
  currentSessionId: null,
  runs: [],
  busy: false,
};

const listeners = new Map(); // key -> Set<fn>

export function on(key, fn) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key).add(fn);
  return () => off(key, fn);
}

export function off(key, fn) {
  const set = listeners.get(key);
  if (set) set.delete(fn);
}

export function emit(key, payload) {
  const set = listeners.get(key);
  if (!set) return;
  for (const fn of [...set]) {
    try {
      fn(payload);
    } catch (e) {
      console.error("store listener error", e);
    }
  }
}

export function getState() {
  return state;
}

export function setState(patch) {
  Object.assign(state, patch);
  emit("state", patch);
}

// daemon 事件订阅（由 ws 层驱动分发）
export function onEvent(fn) {
  return on("daemon-event", fn);
}

// 分发 daemon 事件；顺带维护 busy 状态
export function dispatchEvent(evt) {
  if (!evt) return;
  if (evt.type === "session.waiting_for_input" || evt.type === "session.closed") {
    if (state.busy) setState({ busy: false });
  }
  emit("daemon-event", evt);
}

// 连接状态变更（由 ws 层调用）
export function setConnected(connected) {
  setState({ connected });
}

export function setOnline(online) {
  setState({ online });
}

// ---- 动作 ----
export async function bootstrap() {
  const data = await api.bootstrap();
  setCsrfToken(data.csrf_token);
  state.csrfToken = data.csrf_token;
  state.authRequired = !!data.auth_required;
  state.version = data.version || "";
  state.model = data.default_model || "";
  state.maxSteps = data.max_steps || 20;
  state.permissionTimeoutS = data.permission_timeout_s || 60;
  state.authenticated = !state.authRequired || !!getAuthToken();
  emit("state", {});
  return data;
}

export async function login(token) {
  await api.login(token);
  setAuthToken(token);
  setState({ authenticated: true });
}

export function logout() {
  setAuthToken("");
  setState({ authenticated: false });
}

export async function refreshStatus() {
  try {
    const s = await api.status();
    setState({ online: !!s.online });
    return s;
  } catch {
    setState({ online: false });
    return { online: false };
  }
}

export async function loadSessions() {
  const data = await api.listSessions();
  setState({ sessions: data.sessions || [] });
  return data;
}

export async function createSession() {
  const data = await api.createSession("chat", "");
  await loadSessions();
  setState({ currentSessionId: data.session_id });
  return data;
}

export function switchSession(sessionId) {
  setState({ currentSessionId: sessionId });
}

// 发送消息：同步置 busy，失败时复位。成功响应（含 run_id）在整轮 run 结束后才返回，
// 期间的渲染由 /ws 事件流驱动，故调用方通常 fire-and-forget。
export async function sendMessage(content) {
  const sid = state.currentSessionId;
  if (!sid) throw new Error("no active session");
  setState({ busy: true });
  try {
    return await api.sendMessage(sid, content);
  } catch (e) {
    setState({ busy: false });
    throw e;
  }
}

export function respondPermission(toolUseId, decision) {
  return api.respondPermission(toolUseId, decision);
}

// dry-run 预览：请求决策链轨迹 + 只读工具副作用预览（不执行、不落盘）
export function previewPermission(toolUseId, toolName, params, sessionId) {
  return api.previewPermission(toolUseId, toolName, params, sessionId);
}

export async function compact() {
  const sid = state.currentSessionId;
  if (!sid) return null;
  return api.compact(sid, "");
}

export async function closeSession(sessionId) {
  await api.closeSession(sessionId);
  await loadSessions();
  if (state.currentSessionId === sessionId) setState({ currentSessionId: null });
}

export async function loadRuns() {
  const data = await api.listRuns();
  setState({ runs: data.runs || [] });
  return data;
}

export function loadHistory(sessionId) {
  return api.history(sessionId);
}
