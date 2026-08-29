// 聊天视图：会话侧栏 + 消息流（流式 LLM 文本 / 工具卡片 / run 状态）+ 输入框 + 权限审批。

import {
  closeSession,
  compact,
  createSession,
  getState,
  loadHistory,
  on,
  onEvent,
  previewPermission,
  respondPermission,
  sendMessage,
  switchSession,
} from "../store.js";
import { el, permissionModal, toolCard } from "../components.js";

// 从 Anthropic content（string 或 blocks 数组）中抽取纯文本
function extractText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((b) => b && b.type === "text")
    .map((b) => b.text || "")
    .join("");
}

export function initChat(root) {
  // ---- 布局 ----
  const sidebar = el("aside", { class: "sidebar" });
  const sidebarHead = el("div", { class: "sidebar-head" },
    el("span", {}, "Sessions"),
    el("button", {
      class: "btn btn-primary btn-sm",
      title: "New session",
      onclick: () => doCreateSession(),
    }, "+ New"),
  );
  const sessionList = el("div", { class: "session-list" });
  sidebar.append(sidebarHead, sessionList);

  const main = el("div", { class: "chat-main" });
  const runStatus = el("div", { class: "run-status" }, "");
  const messages = el("div", { class: "messages" });
  const inputRow = el("div", { class: "input-row" },
    el("textarea", {
      class: "chat-input",
      placeholder: "Type a message — /skill to invoke, /compact to compress",
      rows: "1",
    }),
    el("button", { class: "btn btn-primary", text: "Send", onclick: () => doSend() }),
    el("button", { class: "btn btn-ghost", text: "⚡ Compact", title: "Compress context", onclick: () => doCompact() }),
  );
  main.append(runStatus, messages, inputRow);

  const layout = el("div", { class: "chat-layout" }, sidebar, main);
  root.appendChild(layout);

  const textarea = inputRow.querySelector(".chat-input");

  // ---- 内部状态 ----
  let currentAssistant = null;
  let assistantText = "";
  const toolCards = new Map();
  const pendingPerms = new Map(); // toolUseId -> { overlay, close }
  let activeRunId = null;

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function breakAssistant() {
    currentAssistant = null;
    assistantText = "";
  }

  function appendAssistantToken(token) {
    if (!currentAssistant) {
      currentAssistant = el("div", { class: "msg assistant" });
      messages.appendChild(currentAssistant);
    }
    assistantText += token;
    currentAssistant.textContent = assistantText;
    scrollToBottom();
  }

  function appendBlock(node) {
    breakAssistant();
    messages.appendChild(node);
    scrollToBottom();
    return node;
  }

  function setRunStatus(text, kind = "") {
    runStatus.textContent = text;
    runStatus.className = "run-status" + (kind ? " run-status-" + kind : "");
  }

  // ---- 输入处理 ----
  function doSend() {
    const content = textarea.value.trim();
    if (!content) return;
    const sid = getState().currentSessionId;
    if (!sid) {
      setRunStatus("No active session — create one first", "err");
      return;
    }
    if (getState().busy) {
      setRunStatus("Agent is busy…", "warn");
      return;
    }
    if (content === "/compact") {
      textarea.value = "";
      doCompact();
      return;
    }
    textarea.value = "";
    // 乐观渲染用户消息
    appendBlock(el("div", { class: "msg user" }, content));
    setRunStatus("running…", "warn");
    sendMessage(content).catch((e) => {
      setRunStatus("send error: " + e.message, "err");
    });
  }

  async function doCompact() {
    const sid = getState().currentSessionId;
    if (!sid) return;
    appendBlock(el("div", { class: "line system" }, "⚡ compacting context…"));
    try {
      const r = await compact();
      if (r) {
        appendBlock(el("div", { class: "line ok" },
          `⚡ Context compacted — summary=${r.summary_tokens} tokens, saved≈${r.saved_tokens}`));
      }
    } catch (e) {
      appendBlock(el("div", { class: "line err" }, "compact error: " + e.message));
    }
  }

  async function doCreateSession() {
    try {
      await createSession();
    } catch (e) {
      setRunStatus("create error: " + e.message, "err");
    }
  }

  function doSwitchSession(sessionId) {
    if (getState().currentSessionId === sessionId) return;
    switchSession(sessionId);
  }

  async function doCloseSession(sessionId) {
    try {
      await closeSession(sessionId);
    } catch (e) {
      setRunStatus("close error: " + e.message, "err");
    }
  }

  // 输入框快捷键：Enter 发送，Shift/Ctrl+Enter 换行
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      doSend();
    }
  });

  // ---- 历史渲染 ----
  function renderHistory(messagesList) {
    for (const m of messagesList) {
      if (m.role === "user") {
        appendBlock(el("div", { class: "msg user" }, extractText(m.content)));
      } else if (m.role === "assistant") {
        if (typeof m.content === "string") {
          if (m.content) appendBlock(el("div", { class: "msg assistant" }, m.content));
        } else if (Array.isArray(m.content)) {
          for (const block of m.content) {
            if (!block) continue;
            if (block.type === "text" && block.text) {
              appendBlock(el("div", { class: "msg assistant" }, block.text));
            } else if (block.type === "tool_use") {
              const card = toolCard(block.name, block.input);
              card.root.open = false;
              appendBlock(card.root);
            }
          }
        }
      }
    }
  }

  // ---- 侧栏渲染 ----
  function renderSessions() {
    const { sessions, currentSessionId } = getState();
    sessionList.replaceChildren();
    if (!sessions.length) {
      sessionList.appendChild(el("div", { class: "session-empty" }, "No sessions yet"));
    }
    for (const s of sessions) {
      const title = s.title || s.id;
      const item = el("div", {
        class: "session-item" + (s.id === currentSessionId ? " active" : ""),
        onclick: () => doSwitchSession(s.id),
      },
        el("div", { class: "session-title" }, title),
        el("div", { class: "session-meta" }, `${s.run_count} runs · ${s.status}`),
        el("button", {
          class: "session-close",
          title: "Close session",
          onclick: (e) => { e.stopPropagation(); doCloseSession(s.id); },
        }, "×"),
      );
      sessionList.appendChild(item);
    }
  }

  // 当前会话变化时（含初始选中/新建）加载历史
  let loadedSessionId = null;
  async function loadCurrentSession(sessionId) {
    messages.replaceChildren();
    breakAssistant();
    toolCards.clear();
    setRunStatus("");
    if (!sessionId) return;
    try {
      const data = await loadHistory(sessionId);
      renderHistory(data.messages || []);
    } catch (e) {
      appendBlock(el("div", { class: "line err" }, "history error: " + e.message));
    }
  }

  // ---- daemon 事件处理 ----
  function handleEvent(evt) {
    const t = evt.type;
    if (t === "llm.token") {
      appendAssistantToken(evt.token || "");
      return;
    }
    // 非 token 事件：结束当前流式块
    breakAssistant();

    switch (t) {
      case "session.message_received":
        // 已乐观渲染，跳过（多客户端场景可在此同步）
        break;

      case "skill.invoked": {
        const args = evt.arguments ? "  " + evt.arguments : "";
        appendBlock(el("div", { class: "line skill" }, `/${evt.skill_name}${args}`));
        break;
      }

      case "run.started":
        activeRunId = evt.run_id;
        appendBlock(el("div", { class: "line run" },
          "run ", el("span", { class: "mono" }, evt.run_id),
          evt.goal ? "  " + evt.goal.slice(0, 96) : ""));
        setRunStatus("run " + (evt.run_id || "").slice(0, 8) + " — step 0", "warn");
        break;

      case "step.started":
        if (evt.run_id && evt.run_id !== activeRunId) break;
        appendBlock(el("div", { class: "line step" }, "step " + evt.step));
        setRunStatus("run " + (activeRunId || "").slice(0, 8) + " — step " + evt.step, "warn");
        break;

      case "tool.call_started": {
        const card = toolCard(evt.tool_name, evt.params);
        toolCards.set(String(evt.tool_use_id), card);
        appendBlock(card.root);
        break;
      }

      case "tool.call_finished": {
        const card = toolCards.get(String(evt.tool_use_id));
        if (card) {
          card.finish(evt.output, evt.elapsed_ms);
          toolCards.delete(String(evt.tool_use_id));
        }
        break;
      }

      case "tool.call_failed": {
        const card = toolCards.get(String(evt.tool_use_id));
        if (card) {
          card.fail(evt.error_message, evt.elapsed_ms);
          toolCards.delete(String(evt.tool_use_id));
        }
        break;
      }

      case "llm.usage": {
        const pct = Math.round((evt.context_pct || 0) * 100);
        appendBlock(el("div", { class: "line usage" },
          `tokens in=${evt.input_tokens} out=${evt.output_tokens} cache=${evt.cache_read_input_tokens} ctx=${pct}%`));
        break;
      }

      case "context.compacted":
        appendBlock(el("div", { class: "line ok" },
          `⚡ Context compacted — original≈${evt.original_tokens} → summary=${evt.summary_tokens}`));
        break;

      case "run.finished": {
        activeRunId = null;
        setRunStatus("");
        if (evt.status === "success") {
          appendBlock(el("div", { class: "line ok" }, `✓ completed — ${evt.steps} steps`));
        } else {
          const reason = evt.reason ? "  " + evt.reason : "";
          appendBlock(el("div", { class: "line err" }, `✗ failed${reason} — ${evt.steps} steps`));
        }
        break;
      }

      case "permission.requested": {
        const sid = getState().currentSessionId;
        const modal = permissionModal(evt, (toolUseId, decision) => {
          respondPermission(toolUseId, decision).catch((e) => {
            setRunStatus("permission respond error: " + e.message, "err");
          });
        }, () => previewPermission(evt.tool_use_id, evt.tool_name, evt.params, sid));
        document.body.appendChild(modal.overlay);
        pendingPerms.set(String(evt.tool_use_id), modal);
        break;
      }

      case "permission.denied": {
        const key = String(evt.tool_use_id);
        const modal = pendingPerms.get(key);
        if (modal) {
          modal.close();
          pendingPerms.delete(key);
        }
        appendBlock(el("div", { class: "line muted" }, `✗ permission denied (${evt.decision})`));
        break;
      }

      case "permission.granted":
        appendBlock(el("div", { class: "line ok" }, `✓ permission granted (${evt.decision})`));
        break;

      case "log.line": {
        if (evt.level === "ERROR" || evt.level === "WARNING") {
          appendBlock(el("div", { class: "line muted" },
            `${evt.level} ${evt.message || ""}`));
        }
        break;
      }

      default:
        break;
    }
  }

  function onState() {
    renderSessions();
    const sid = getState().currentSessionId;
    if (sid !== loadedSessionId) {
      loadedSessionId = sid;
      loadCurrentSession(sid);
    }
  }

  const offEvent = onEvent(handleEvent);
  const offState = on("state", onState);

  // 初始渲染
  renderSessions();

  return () => {
    offEvent();
    offState();
    for (const modal of pendingPerms.values()) modal.close();
    root.removeChild(layout);
  };
}
