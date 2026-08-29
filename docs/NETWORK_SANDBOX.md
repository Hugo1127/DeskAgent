# 网络沙箱（L1：强制网络审批层）

> 状态：**规划（未实现）**。本文档仅保留选定方案（L1）的设计要点，落地后更新。

## 目标

把「网络行为」从 bash 审批中独立出来，做成**不可被 always_allow / allow_patterns / 缓存绕过的强制审批层**：网络命令永远需要当场确认（或按模式 block / allowlist）。

## 设计要点

- **配置**（`core/config.py` 新增 `SandboxConfig`）：

```toml
[sandbox]
mode = "ask"   # off | ask | block | allowlist
```

- **静态检测**（`core/permissions/policy.py` 新增 `NETWORK_HEURISTICS`，与 OUTSIDE_CWD 启发式同构）：curl / wget / git clone|fetch|pull|push / pip install|download / npm install|ci|publish / ssh / scp / nc / telnet / ping / python urllib|socket|requests|http 等；边界匹配避免误伤（`cat curl.txt` 不命中）。
- **强制层**（`core/permissions/manager.py` 的 `_trace()`，置于 outside-cwd 之后、session 缓存之前）：
  - `ask`：强制 ASK，审批只接受 `allow_once` / `deny_once`（always_* 不生效、不写缓存）；
  - `block`：直接拒绝；`allowlist`：仅放行 allow_patterns 显式命中的网络命令，其余拒绝；
  - 该层**不可被 session 缓存 / policy.toml / allow_patterns 绕过**。
- **dry-run 预览**（`core/permissions/preview.py`）：命中网络规则时标注「将发起网络访问（目标不可静态确定）」。

## 非目标（本期不做）

- 过滤代理 / 目的地级管控、network.jsonl 审计、MCP 子进程注入、AppContainer / 防火墙
- 只覆盖 bash 工具参数这一路径；MCP 等非 bash 路径不在本期范围

## 涉及文件（落地时）

`core/config.py`、`core/permissions/policy.py`、`core/permissions/manager.py`、`core/permissions/preview.py`、`tests/unit/test_network_heuristics.py`、README。
