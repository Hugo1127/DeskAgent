// REST 封装：统一携带 CSRF 头与登录令牌头，解析 JSON 与错误。

let csrfToken = null;
let authToken = sessionStorage.getItem("desk_web_token") || "";

export function setCsrfToken(token) {
  csrfToken = token;
}

export function setAuthToken(token) {
  authToken = token || "";
  if (authToken) sessionStorage.setItem("desk_web_token", authToken);
  else sessionStorage.removeItem("desk_web_token");
}

export function getAuthToken() {
  return authToken;
}

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (authToken) headers["Authorization"] = "Bearer " + authToken;

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 非 JSON 响应 */
  }

  if (!res.ok) {
    const message = data && data.detail ? data.detail : `HTTP ${res.status}`;
    const err = new Error(String(message));
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  bootstrap: () => request("GET", "/api/bootstrap"),
  login: (token) => request("POST", "/api/login", { token }),
  status: () => request("GET", "/api/status"),
  listSessions: () => request("GET", "/api/sessions"),
  createSession: (mode = "chat", title = "") =>
    request("POST", "/api/sessions", { mode, title }),
  history: (sessionId) => request("GET", `/api/sessions/${encodeURIComponent(sessionId)}/history`),
  sendMessage: (sessionId, content) =>
    request("POST", `/api/sessions/${encodeURIComponent(sessionId)}/messages`, { content }),
  closeSession: (sessionId) =>
    request("POST", `/api/sessions/${encodeURIComponent(sessionId)}/close`),
  compact: (sessionId, focus = "") =>
    request("POST", `/api/sessions/${encodeURIComponent(sessionId)}/compact`, { focus }),
  respondPermission: (toolUseId, decision) =>
    request("POST", "/api/permission/respond", { tool_use_id: toolUseId, decision }),
  listRuns: () => request("GET", "/api/runs"),
};
