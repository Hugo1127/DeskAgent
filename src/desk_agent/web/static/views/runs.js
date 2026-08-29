// 运行视图：历史 run 列表 + 事件流回放查看器（复用 daemon 的 replay_from_run）。

import { getState, loadRuns } from "../store.js";
import { connectReplayWS } from "../ws.js";
import { el, formatTime, toolCard } from "../components.js";

export function initRuns(root) {
  const list = el("div", { class: "runs-list" });
  const viewer = el("div", { class: "replay-viewer" });
  const layout = el("div", { class: "runs-layout" }, list, viewer);
  root.appendChild(layout);

  let replayConn = null;

  function showEmpty() {
    viewer.replaceChildren(
      el("div", { class: "replay-empty" }, "Select a run to replay its event stream"),
    );
  }

  function renderList() {
    const { runs } = getState();
    list.replaceChildren(
      el("div", { class: "runs-head" },
        el("span", {}, "Runs"),
        el("button", { class: "btn btn-ghost btn-sm", onclick: () => doLoad() }, "↻"),
      ),
    );
    if (!runs.length) {
      list.appendChild(el("div", { class: "session-empty" }, "No runs yet"));
    }
    for (const r of runs) {
      const item = el("div", {
        class: "run-item",
        onclick: () => doReplay(r.run_id),
      },
        el("div", { class: "mono" }, r.run_id),
        el("div", { class: "run-session" },
          (r.session_title || r.session_id) + " · " + formatTime(r.session_updated_at),
        ),
      );
      list.appendChild(item);
    }
  }

  async function doLoad() {
    try {
      await loadRuns();
      renderList();
    } catch (e) {
      viewer.replaceChildren(el("div", { class: "replay-empty" }, "load error: " + e.message));
    }
  }

  function doReplay(runId) {
    if (replayConn) replayConn.close();
    const { csrfToken } = getState();

    viewer.replaceChildren(
      el("div", { class: "replay-head" },
        "Replay ", el("span", { class: "mono" }, runId)),
      el("div", { class: "replay-log" }),
    );
    const logEl = viewer.querySelector(".replay-log");

    let currentAssistant = null;
    let assistantText = "";
    const toolCards = new Map();

    function breakAssistant() {
      currentAssistant = null;
      assistantText = "";
    }

    function appendAssistantToken(token) {
      if (!currentAssistant) {
        currentAssistant = el("div", { class: "msg assistant" });
        logEl.appendChild(currentAssistant);
      }
      assistantText += token;
      currentAssistant.textContent = assistantText;
      logEl.scrollTop = logEl.scrollHeight;
    }

    function appendBlock(node) {
      breakAssistant();
      logEl.appendChild(node);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function handleEvent(evt) {
      const t = evt.type;
      if (t === "llm.token") {
        appendAssistantToken(evt.token || "");
        return;
      }
      breakAssistant();

      switch (t) {
        case "run.started":
          appendBlock(el("div", { class: "line run" },
            "run ", el("span", { class: "mono" }, evt.run_id),
            evt.goal ? "  " + evt.goal : ""));
          break;
        case "step.started":
          appendBlock(el("div", { class: "line step" }, "step " + evt.step));
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
        case "run.finished":
          appendBlock(el("div", { class: evt.status === "success" ? "line ok" : "line err" },
            evt.status === "success"
              ? `✓ completed — ${evt.steps} steps`
              : `✗ failed — ${evt.steps} steps`));
          break;
        default:
          break;
      }
    }

    replayConn = connectReplayWS({
      csrf: csrfToken,
      runId,
      onEvent: handleEvent,
      onReplayEnd: (msg) => {
        const count = msg.replayed_count || 0;
        appendBlock(el("div", { class: "line muted" },
          `— replay complete (${count} events)` + (msg.error ? ", error" : "") + " —"));
      },
      onDone: () => {
        replayConn = null;
      },
    });
  }

  doLoad();
  showEmpty();

  return () => {
    if (replayConn) replayConn.close();
    root.removeChild(layout);
  };
}
