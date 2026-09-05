// 轻量 DOM 组件与工具函数（无依赖）。
// 安全模型：所有文本一律通过 el() 的「文本节点」插入，浏览器不会解析其中的 HTML，
// 因此无需额外转义；escapeHtml 仅供未来需要 innerHTML 注入的场景使用。

const ESCAPE_MAP = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

// HTML 转义：仅当把文本注入 innerHTML 时使用（el 的文本节点已天然安全，无需调用）
export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);
}

// 创建 DOM 元素。字符串子节点一律作为「文本节点」插入（安全，不解析 HTML）。
// attrs 支持 class / text / html / on* 事件监听 / 其余作为 attribute。
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    // 布尔 false 表示「不设置该属性」（如 disabled:false 不能写成 disabled="false"）
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    if (child instanceof Node) {
      node.appendChild(child);
    } else {
      node.appendChild(document.createTextNode(String(child)));
    }
  }
  return node;
}

// 格式化 JSON（缩进 2）
export function formatJson(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

// 从工具参数中提取最利于摘要展示的关键字段（与 TUI 的 _param_summary 对齐）
export function paramSummary(toolName, params) {
  const keysByTool = {
    read_file: ["path"],
    write_file: ["path"],
    list_dir: ["path", "max_depth"],
    bash: ["command"],
    note_save: ["content"],
  };
  const paramsObj = params || {};
  const keys = keysByTool[toolName] || [];
  let parts = keys
    .filter((k) => k in paramsObj)
    .map((k) => `${k}=${JSON.stringify(paramsObj[k])}`);
  if (!parts.length) {
    parts = Object.entries(paramsObj)
      .slice(0, 2)
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`);
  }
  let text = parts.join(", ");
  if (text.length > 96) text = text.slice(0, 93) + "…";
  return text;
}

// 可折叠工具调用卡片。返回 { root, finish(output, elapsed), fail(error, elapsed) }
export function toolCard(toolName, params) {
  const summary = el("summary", { class: "tc-summary" },
    el("span", { class: "tc-badge" }, "tool"),
    el("span", { class: "tc-name" }, toolName),
    el("span", { class: "tc-params" }, paramSummary(toolName, params)),
    el("span", { class: "tc-status" }, "…"),
  );
  const body = el("div", { class: "tc-body" });
  const root = el("details", { class: "tool-card" }, summary, body);
  const status = summary.querySelector(".tc-status");

  function renderBody(output, elapsed, isError) {
    body.replaceChildren(
      el("div", { class: "tc-section" },
        el("div", { class: "tc-section-title" }, "params"),
        el("pre", { class: "tc-pre" }, formatJson(params)),
      ),
      el("div", { class: "tc-section" },
        el("div", { class: "tc-section-title" }, "output"),
        el("pre", { class: "tc-pre" + (isError ? " tc-error" : "") }, output || ""),
      ),
      el("div", { class: "tc-elapsed" }, `${elapsed}ms`),
    );
  }

  return {
    root,
    finish(output, elapsed) {
      root.classList.add("done");
      status.textContent = "✓ done";
      renderBody(output, elapsed, false);
    },
    fail(error, elapsed) {
      root.classList.add("failed");
      status.textContent = "✗ failed";
      renderBody(error, elapsed, true);
    },
  };
}

// 权限审批模态框。onDecision(toolUseId, decision) 提交决策；
// onPreview() 可选，返回 Promise<{decision, tier, reason, preview_text}>，用于 dry-run 预览。
// 返回 { overlay, close }
export function permissionModal(perm, onDecision, onPreview) {
  const {
    tool_use_id: toolUseId,
    tool_name: toolName,
    params,
    param_preview: preview,
    tier,
    decision_reason: decisionReason,
  } = perm;
  const choices = [
    ["allow_once", "Allow once", "allow"],
    ["always_allow", "Always allow (session)", "allow"],
    ["always_allow_global", "Always allow (global)", "allow"],
    ["deny_once", "Deny", "deny"],
    ["always_deny", "Always deny (session)", "deny"],
    ["always_deny_global", "Always deny (global)", "deny"],
  ];

  const previewArea = el("div", { class: "modal-preview-area" },
    el("div", { class: "modal-preview-muted" },
      "点击下方按钮查看 dry-run 预览（只读分析，不执行命令）"),
  );
  const previewBtn = el("button", {
    class: "btn btn-ghost btn-sm",
    text: "Dry-run preview",
    disabled: !onPreview,
    title: "预览策略决策链与工具副作用（不执行）",
  });

  const overlay = el("div", { class: "modal-overlay" });
  const dialog = el("div", { class: "modal", role: "dialog", "aria-modal": "true" },
    el("div", { class: "modal-title" }, "Permission required"),
    el("div", { class: "modal-tool" },
      el("span", { class: "tc-badge" }, "tool"),
      el("strong", {}, toolName),
    ),
    preview ? el("div", { class: "modal-preview" }, preview) : null,
    tier ? el("div", { class: "modal-reason" },
      el("span", { class: "modal-tier" }, tier),
      decisionReason ? "  " + decisionReason : "",
    ) : null,
    params && Object.keys(params).length
      ? el("pre", { class: "modal-params" }, formatJson(params))
      : null,
    previewArea,
    el("div", { class: "modal-actions" },
      previewBtn,
      ...choices.map(([decision, label, kind]) =>
        el("button", {
          class: "btn btn-" + kind,
          onclick: () => { close(); onDecision(toolUseId, decision); },
        }, label),
      ),
    ),
    el("div", { class: "modal-hint" },
      "Allow/Deny 仅本次生效；session 作用于当前会话；global 持久化到 policy.toml 并跨会话生效"),
  );
  overlay.appendChild(dialog);

  if (onPreview) {
    previewBtn.addEventListener("click", async () => {
      previewBtn.disabled = true;
      previewBtn.textContent = "previewing...";
      previewArea.replaceChildren(
        el("div", { class: "modal-preview-muted" }, "loading preview..."),
      );
      try {
        const r = await onPreview();
        const meta = "decision=" + (r.decision || "?") + "  " + (r.tier || "") +
          (r.reason ? "  -  " + r.reason : "");
        previewArea.replaceChildren(
          el("div", { class: "modal-preview-meta" }, meta),
          el("pre", { class: "modal-preview-text" },
            r.preview_text || "(no preview available)"),
          el("div", { class: "modal-preview-muted" },
            "预览为静态分析，不代表实际执行结果"),
        );
      } catch (e) {
        previewArea.replaceChildren(
          el("div", { class: "modal-preview-err" }, "preview error: " + e.message),
        );
      } finally {
        previewBtn.disabled = false;
        previewBtn.textContent = "Dry-run preview";
      }
    });
  }

  function close() {
    overlay.remove();
  }
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  return { overlay, close };
}

// ISO 时间戳 → 本地可读字符串
export function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// 毫秒 → 可读时长
export function formatDuration(ms) {
  if (ms == null) return "";
  const n = Number(ms);
  if (n < 1000) return `${n}ms`;
  if (n < 60_000) return `${(n / 1000).toFixed(1)}s`;
  return `${Math.floor(n / 60_000)}m ${Math.round((n % 60_000) / 1000)}s`;
}
