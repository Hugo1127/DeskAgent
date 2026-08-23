# 通信安全改造设计方案（TUI ↔ Core IPC）

> 状态：**实现中**（阶段 0/1/3 已落地：令牌认证 + TLS + 应用层防重放 + 攻击模拟脚本；阶段 2 P1 项待实施）
> 范围：desk-core 守护进程与 TUI / CLI 客户端之间的 IPC 通信安全
> 关联文档：[WIRE_PROTOCOL.md](WIRE_PROTOCOL.md)（协议，自动生成）、[RUNBOOK.md](RUNBOOK.md)

---

## 1. 背景与目标

DeskAgent 采用「常驻守护进程 + 多客户端」架构：`desk-core` 监听 TCP 回环端口，CLI / TUI 作为客户端接入，实现"断开不中断执行、重连可恢复订阅"。当前 TUI 与 core 的通信**没有任何安全措施**——任何本地进程都可以连接端口、下发命令、读取事件流，甚至可以伪造权限审批。

本文档的目标：

1. 说清当前通信链路的安全现状与威胁模型；
2. 给出可选方案与**推荐方案**（分阶段落地，先解决最高危问题）；
3. 明确落地计划、涉及文件、测试与验证方式；
4. **本阶段只产出设计，不改代码**。

---

## 2. 现状分析

### 2.1 当前通信链路

```
┌──────────────┐   TCP 127.0.0.1:7437（明文 NDJSON）  ┌──────────────────┐
│  TUI / CLI   │ ───────────────────────────────────▶ │   desk-core      │
│ SocketClient │   JSON-RPC 2.0 请求 + 事件推送        │   SocketServer   │
└──────────────┘ ◀─────────────────────────────────── └──────────────────┘
```

- 传输：`asyncio.start_server` 监听 `host:port`，默认 `127.0.0.1:7437`（`core/config.py`，可被 `DESK_HOST` / `DESK_PORT` 覆盖，**允许配置成非回环地址**）；当前明文，计划升级为 TLS（见 5.8）；
- 协议：每行一个 JSON（NDJSON）。命令为 JSON-RPC 2.0（客户端 → 服务端），事件用 `kind=event` 信封（服务端 → 客户端）；
- 帧大小上限：`_MAX_LINE_BYTES = 64 MB`（`core/transport/socket_server.py:44`、`socket_client.py:13`），为兼容 MCP 大文件工具结果而设；
- 每条命令独立 `asyncio.create_task` 执行，长任务（如 `session.send_message`）不阻塞读循环（`socket_server.py:157`）。

### 2.2 数据面盘点（线上流动的数据）

| 方向 | 内容 | 敏感性 |
|---|---|---|
| 客户端 → core | `session.send_message` 的用户消息 | 高（对话隐私） |
| 客户端 → core | `permission.respond` 审批决策（allow/deny） | **极高（安全边界）** |
| 客户端 → core | `agent.run` 目标、`session.compact` 焦点 | 中 |
| core → 客户端 | `llm.token` 流式输出、`llm.usage` | 高（思考内容） |
| core → 客户端 | `tool.call_started`（参数：bash 命令、文件路径）、`tool.call_finished`（输出，含 `read_file` 读到的文件内容） | 极高（文件内容） |
| core → 客户端 | `permission.requested`（工具名 + 参数预览） | 中 |
| core → 客户端 | `session.get_history` 全量消息历史 | 极高 |
| core → 客户端 | `event.subscribe(scope=global)` / `replay_from_run` 的历史事件回放 | 极高 |

### 2.3 现有防御

- ✅ 默认绑定 `127.0.0.1`（非零网卡，挡住远程攻击者——**前提是用户没改 host**）；
- ❌ 无认证：任何进程可直接连接并调用全部 9 个方法；
- ❌ 无身份/归属：连接之间、session 之间无归属关系；
- ❌ 无加密：环回上全明文；
- ❌ 无资源限制：无连接数上限、无速率限制、帧上限 64 MB 过大；
- ❌ 无审计：trace 里 `client_id` 仅记录 `peername`（IP+端口），无法追溯到具体客户端。

---

## 3. 威胁模型

### 3.1 信任边界

```
┌────────────────────────────────────────────────────────────┐
│  用户（同机同用户）           ← 信任区：可读写 ~/.desk 数据    │
├────────────────────────────────────────────────────────────┤
│  其他本地进程（同机）          ← 半信任：本方案的主要防御对象   │
├────────────────────────────────────────────────────────────┤
│  远程网络                     ← 默认已被 127.0.0.1 绑定挡住   │
└────────────────────────────────────────────────────────────┘

> **本阶段范围注记**：按产品决策，默认**不考虑本机恶意进程**（A1/A2 由令牌认证 + run 文件权限兜底），防御重心放在**网络传输层**：保证消息传输过程中不被窃听、不被篡改、服务端不被冒充（方案见 5.8）。
```

### 3.2 攻击者画像

| 编号 | 画像 | 能力 | 能否被本方案防住 |
|---|---|---|---|
| A1 | 同机**其他用户**的低权限进程（如共享机器的其他账号） | 能 TCP 连接端口，读不到你的 home 目录 | ✅ 令牌 + 文件权限可完全防住 |
| A2 | 同机**同用户**的恶意进程（供应链投毒、浏览器/编辑器插件、被植入的软件） | 能 TCP 连接，通常能读 `~/.desk`（当前无 0600 保护） | ⚠️ 部分：令牌提高门槛；同用户进程若能读到 token 文件则无法根治（可叠加 PIN，见 5.5） |
| A3 | 远程网络攻击者 | 仅当 host 被配成非回环地址时可达 | ✅ 强制回环校验 + 令牌 + TLS 传输保护 |
| A4 | 管理员 / root | 能读一切文件与进程内存 | ❌ 任何纯软件方案都防不住，只能纵深防御 |

> **结论**：本方案按产品决策聚焦**传输完整性**（不考虑本机恶意进程），优先级为：**认证（谁能连）与 TLS（传输不被窃听/篡改/冒充）并重**，应用层防重放作为收尾。

### 3.3 资产与影响

| 资产 | 被攻击的后果 | 严重度 |
|---|---|---|
| 权限审批通道（`permission.respond`） | 攻击者自动 `always_allow` 所有 `bash` / `write_file`，**且持久化写入 `policy.toml` 永久放行**，权限系统形同虚设 | **P0** |
| 会话内容 / 事件流 | 全量窃取对话、LLM 输出、工具参数与 `read_file` 读到的文件内容 | **P0** |
| 计算与存储 | 无限 `agent.run` 烧 API 额度；`session.create` / replay 灌满磁盘 | P1 |
| 可用性 | 连接洪水、64 MB 超大帧内存占用 | P1 |

---

## 4. 安全问题清单

按严重度排序（P0 必修 → P2 可选）。每个问题给出**攻击场景**，用于后续验收。

### 4.1 P0：认证缺失（最严重）

**S-1 伪造权限审批（权限体系绕过）**

- 位置：`core/app.py` 的 `_permission_respond_handler` → `permission/manager.py:respond()`。`respond()` 只凭 `tool_use_id` 查表并 resolve Future，**不校验调用者身份**。
- 攻击场景：恶意进程连上 7437 → `event.subscribe` 订阅 `permission.*` → 收到 `permission.requested` 后立即回复 `permission.respond {tool_use_id, decision: "always_allow"}`。核心中的 `bash` / `write_file` / 未知工具的所有 ASK 审批被自动放行，`always_allow` 还会经 `manager.py:_apply_response` **持久化到 `~/.desk/policy.toml`**——一次攻击，永久放行。
- 这是整个产品安全边界的单点失效，**必须最先修复**。

**S-2 会话与事件全量窃取**

- `session.get_history` 返回完整 Anthropic 消息历史（`app.py:_session_history_handler`）；
- `event.subscribe` 支持 `scope="global"`，订阅全部 run 的事件（`transport/ipc_broadcaster.py:_matches_scope`）；
- `replay_from_run` 可回放任意历史 run 的 `events.jsonl`（`app.py:_replay_events`，且回放时**不校验订阅 scope**）；
- 攻击场景：恶意进程连接 → 订阅全量事件 → 实时偷听 LLM 流式输出与工具结果；或猜测 run_id / session_id 回放历史。

**S-3 任意命令执行面**

- 9 个方法全部无认证可达：`agent.run`（烧 API 额度、触发任意任务）、`session.create/close/compact`（破坏会话）、`core.ping`（信息泄露：版本/uptime）。

### 4.2 P1：身份、归属与资源缺失

- **S-4 无连接身份与归属**：无法审计"谁"发起了命令；session 与连接无绑定，任何连接可操作任何 session（知道 id 即可）。
- **S-5 无资源防护**：无连接数上限、无速率限制；`_MAX_LINE_BYTES = 64 MB` 对控制帧过大（内存 DoS 向量）；replay 无行数上限。
- **S-6 传输层无完整性/机密性保护（P0）**：线上全明文——若 host 被配置为非回环地址（当前代码不阻止），报文在网络中可被窃听、篡改，且无服务端身份校验（MITM 可冒充 core）。在"网络传输不被篡改"的目标下，这是**必做项**（方案见 5.8），不再是纵深防御。
- **S-7 固定端口可探测**：`7437` 固定，可被扫描/探测；`cmd_core_start` 的 `_ping_check` 只验证端口连通，任何占用该端口的服务都会被误判为"already running"。
- **S-8 数据残留**：IPC trace（`socket_server.py:_handle_line/_send`）把命令**完整 params 与响应**写入 `traces/daemon.jsonl`，含工具输出等敏感内容，无脱敏选项（LLM 层已有 `include_llm_payload` 开关，IPC 层没有）。

### 4.3 P2：可选的纵深防御

- **S-9 无应用层防重放（P1）**：TLS 不防应用层报文重放（见 5.8 分析）；需在协议层补充每连接单调 `seq` + 时间戳窗口 + 一次性 ID。
- **S-10 传输介质仍在 IP 栈上**：TCP 端口概念本身可被探测；远期可迁移 Named Pipe / AF_UNIX（见 5.8）。

---

## 5. 方案设计

### 5.1 候选方案对比

| 方案 | 防未授权连接 | 防嗅探 | 实现成本 | 跨平台（本项目 Windows 优先） | 新增依赖 | 结论 |
|---|---|---|---|---|---|---|
| A. 回环 TCP + **共享令牌认证** | ✅（A1/A3，A2 部分） | ❌ | 低（约 1 个模块 + 握手） | ✅ | 无 | **推荐 P0/P1** |
| B. Named Pipe / AF_UNIX | ✅✅（OS 级权限） | ✅ | 高：Windows 上 asyncio 不支持 unix server / named pipe，需自实现 transport 或 pywin32 | ⚠️ 需双实现 | pywin32 或自研 | P2 远期 |
| C. TLS（自签证书 + pinning） | ✅（证书认证） | ✅ | 中：证书引导/pinning 管理（**无需 CA**） | ✅（stdlib `ssl`） | 生成证书需 `cryptography` 或 openssl | **推荐（传输完整性必做）** |
| D. 去掉网络（客户端变 core 内嵌） | ✅✅✅ | ✅ | 高：推翻"多客户端守护进程"架构 | ✅ | 无 | **否决**（违背产品设计） |
| E. A + 帧级 AEAD 加密（HKDF(token) + AES-GCM） | ✅ | ✅ | 中 | ✅ | `cryptography` | 备选（不想引入证书概念时） |

**推荐：A（令牌认证，P0）+ C（TLS 自签 + pinning，传输完整性必做）+ 应用层防重放。** 在"不考虑本机恶意进程"的决策下：认证与授权解决"谁能连"，TLS 解决"传输不被窃听/篡改/冒充"，二者互补、缺一不可；B（Named Pipe）作为远期介质增强保留；E（AEAD）为不引入证书时的备选。

### 5.2 推荐方案总览（分层）

```
P0（本次改造核心，安全默认开）
 ├─ 5.3 身份与握手：每实例随机令牌（256-bit），core.hello 认证（在 TLS 信道内，见 5.8），认证前拒绝一切
 ├─ 5.4 连接生命周期：NEW → AUTHENTICATED 状态机，失败即断开，事件仅推给已认证连接
 └─ 5.5 命令授权：permission.respond 绑定 pending 请求的 session 归属；会话所有权绑定

P1（紧随其后）
 ├─ 5.5 命令分级授权 + session.attach（显式接管）
 ├─ 5.6 资源防护：帧上限收紧、连接数/速率限制、replay 行数上限
 ├─ 5.7 动态端口：port=0 + run 文件通告（消端口扫描/冲突）
 └─ 5.9 审计：认证日志、client_id 进 trace、IPC 层敏感字段脱敏选项

传输层（必做，阶段 3 落地——产品决策聚焦网络传输）
 ├─ 5.8 TLS 自签证书 + 证书固定（pinning）——防窃听 / 防篡改 / 防服务端被冒充
 └─ 5.8 应用层防重放：seq + 时间戳 + 一次性 ID——闭环 TLS 管不到的重放

P2（可选增强）
 └─ 5.8 迁移 Named Pipe / AF_UNIX——彻底移出 IP 栈
```

### 5.3 身份与握手（P0）

**令牌的生成与存储**

- core 每次启动生成 `token = secrets.token_urlsafe(32)`（256-bit 熵，**每次启动轮换**）；
- 写入新增的 `~/.desk/run/daemon.json`（`core/transport/runfile.py` 负责读写）：

```json
{
  "token": "<256-bit urlsafe>",
  "port": 7437,
  "pid": 12345,
  "started_at": "2026-05-16T10:00:00.000Z",
  "version": "0.0.1"
}
```

- POSIX：`chmod 0600`；Windows：依赖用户目录默认 ACL（`%USERPROFILE%\.desk` 默认仅当前用户可读），如需更强可在文档中给出 `icacls` 收紧指引；
- `desk core stop` 改为读取 run 文件（同时兼容旧 `desk-core.pid`），停止后删除 run 文件。

**握手协议（新增方法 `core.hello`）**

```
客户端连接
  │  (1) 读 ~/.desk/run/daemon.json 取 token+port
  ▼
core.hello {token, client}  ──▶  core 用 hmac.compare_digest 校验
                                  ├─ 成功 → 连接置 AUTHENTICATED，返回
                                  │        {protocol_version, server_version, client_id}
                                  └─ 失败 → 返回 -32001 并立即断开
```

- **传输信道**：握手与应用报文均运行在 TLS 信道上（证书 pinning 见 5.8），令牌不会明文暴露在网络中；
- **认证前白名单**：未认证连接只允许 `core.hello`，其余方法一律返回 `-32001 UNAUTHENTICATED` 并断开；
- 10 秒内未完成 hello → 断开（防挂死连接占资源）；
- 校验用 `hmac.compare_digest`（常数时间，防时序侧信道）；
- trace / 日志只记录 `client_id` 与 token 指纹（如 `token[:8]`），**绝不落盘完整 token**；
- 客户端侧：`SocketClient` 新增 `authenticate(token)`；TUI/CLI 每次 connect 前读取 run 文件。TUI 已有 2 秒重连循环（`tui/app.py:_socket_loop`），daemon 重启导致 token 轮换时**自动重连兼容，无需额外逻辑**。

**错误码扩展**（需同步 `scripts/gen_protocol_doc.py` 重新生成 WIRE_PROTOCOL.md）

| 代码 | 名称 | 含义 |
|---|---|---|
| -32001 | UNAUTHENTICATED | 未认证或令牌错误 |
| -32002 | SESSION_FORBIDDEN | 无权操作该 session |
| -32003 | RATE_LIMITED | 超出速率限制 |
| -32004 | AUTH_EXPIRED | 令牌已失效（daemon 重启后旧连接） |
| -32005 | STALE_SEQUENCE | 报文序号重复/过期（防重放，见 5.8） |

### 5.4 连接生命周期（P0）

- 连接状态机：`NEW → AUTHENTICATED → CLOSED`；事件推送（`IpcEventBroadcaster`）只面向 `AUTHENTICATED` 连接；
- 断开清理沿用现有逻辑（`socket_server.py:finally` 清理订阅 + `permissions/manager.py:cancel_session` 拒绝挂起审批），并补充：认证连接断开时，若该连接是某些 session 的 owner，则记录审计日志（可选：标记 session 失联）；
- 空闲超时：chat 长连接场景不做强制空闲断开（避免误杀），仅对 `NEW` 状态强制 10s 超时。

### 5.5 命令授权与会话绑定（P1）

**命令分级**

| 级别 | 方法 | 授权规则 |
|---|---|---|
| 公开（仅认证） | `core.ping` | 认证即可 |
| 会话级 | `session.create` / `send_message` / `get_history` / `close` / `compact` | 创建者连接**拥有**该 session；其他已认证连接需显式 `session.attach` 后获得访问权（记录审计） |
| 审批级 | `permission.respond` | 已认证 + **pending 请求的 session 归属校验**：仅当调用连接拥有该 pending 请求所属 session 时才放行；同时记录来源 `client_id` |
| 订阅级 | `event.subscribe` | 已认证即可（保持多客户端事件共享 UX）；replay 增加行数上限（如 50k） |

**关于多客户端共享的取舍**：当前 TUI 与 CLI chat 各自 `session.create` 各自使用，不存在共享同一 session_id 的现有多客户端场景（`replay` 只读事件）。因此"创建者独占控制命令 + 显式 attach"是安全的，不会破坏现有 UX。

**可选加固（低成本的纵深）**：在令牌之上叠加用户 PIN（`DESK_IPC_PIN`，首启时让用户设置并写入 run 文件），握手时一并校验——可防"同用户恶意进程直接读 token 文件"这一 A2 场景。成本低，是否引入由产品决策（见第 8 节开放问题 O-2）。

### 5.6 资源防护（P1）

| 项 | 现状 | 建议 |
|---|---|---|
| 帧大小 | 统一 64 MB（`socket_server.py:44`） | 控制帧/事件帧上限 1 MB；大工具结果（MCP 大文件）改走**文件通道**（core 写临时文件、线上传路径，P2 实施）或维持 64 MB 但**仅限已认证连接** |
| 连接数 | 无限制 | 单实例上限（如 32），超限拒绝 |
| 速率 | 无 | 认证后每连接令牌桶（如 100 cmd/s）；`agent.run` / `session.send_message` 每连接并发 1（与现有 busy 语义一致） |
| hello 重试 | — | 失败 3 次断开，客户端指数退避 |
| replay | 无上限 | 行数上限 + 单次回放超时 |
| `session.create` | 无上限 | 单连接未关闭 session 上限（如 8），防灌磁盘 |

### 5.7 动态端口（P1）

- 默认改为 `port=0`（OS 分配），真实端口写入 run 文件，客户端从 run 文件读取；
- 收益：无固定端口可扫描、无端口冲突、`desk core status` 不再依赖"端口通即 running"的脆弱判断（改为读 run 文件 + pid 存活校验）；
- 保留 `DESK_PORT` 显式指定时用固定端口（测试/调试用）。

### 5.8 传输安全：TLS 自签证书 + 证书固定（传输完整性，必做）

> 目标：在网络传输维度保证 ① 不被窃听（机密性）② 不被篡改（完整性）③ 服务端不被冒充（认证）。前提假设：不考虑本机恶意进程（A1/A2 由令牌 + run 文件权限兜底），网络攻击者为传输层主要防御对象。

**没有 CA 怎么办：自签证书 + 证书固定（pinning）**

本地守护进程的 TLS **不需要公共 CA**，标准做法是「自签证书 + 客户端固定信任」（Docker daemon 等同类工具一致）：

1. core 每次启动用 `cryptography` 生成**一次性自签证书**（ECDSA P-256，有效期如 10 年），SAN 含 `127.0.0.1`、`localhost` 与配置的 `DESK_HOST`（若为非回环地址）；
2. 私钥落盘 `~/.desk/run/desk.key`（POSIX 0600 / Windows 用户目录 ACL），证书落盘 `~/.desk/run/desk.crt`；
3. run 文件（`daemon.json`）新增字段 `cert_sha256`（证书 DER 的 SHA-256 指纹）；
4. 客户端 connect 前读 run 文件，以该证书为**唯一信任锚**：`ssl.create_default_context()` + `load_verify_locations(cadata=<证书>)` + `check_hostname=True`（证书含 SAN，走标准主机名校验）。任何其他证书（MITM 攻击者的）校验失败 → 握手拒绝。

> **pinning 替代了 CA**：客户端只信任 run 文件里那张证书；攻击者没有它的私钥，无法伪造可验证的证书，冒充服务端在握手阶段即被拒绝——这比公共 CA 更严格（只有一张证书可信）。

**为什么每次启动重新生成**：与令牌轮换一致（daemon 重启 → 新证书新指纹 → 客户端重连时从 run 文件重读，TUI 2 秒重连循环天然兼容）；避免长期私钥泄露风险；省去证书轮换管理。

**TLS 层不能解决什么（重放）**

- 澄清：「重放整条 TLS 会话」在 TLS 1.2/1.3 下**握手阶段就会失败**——服务端每次生成新的随机数，重放的 ClientHello / Finished 校验不过，攻击者拿不到会话密钥，无法解密或注入应用数据；
- 真正的重放风险在**应用层**：攻击者把一条**合法命令报文**（如 `session.send_message`）在自己的**新合法连接**上重放。TLS 管不到这一层。

**应用层防重放（闭环设计，随 TLS 一并实现）**

| 机制 | 说明 | 防什么 |
|---|---|---|
| 每连接单调 `seq` | hello 后每条请求携带递增 `seq`，服务端按连接记录 `last_seq`，`seq <= last_seq` 拒绝（-32005） | 同连接内报文重放 / 乱序 |
| 请求 `ts` + 时间窗 | 每条请求携带客户端时间戳，服务端拒绝 `\|now-ts\| > 60s` 的报文 | 跨时间重放 |
| 一次性 ID | `tool_use_id` 首次响应后即从 pending 表移除（`permissions/manager.py:respond` 已如此） | `permission.respond` 天然不可重放 |
| 连接级令牌 | `core.hello` 令牌只在 TLS 信道内、当次连接有效（每实例轮换） | 旧连接报文在新连接上重放 |

> 结论：在"攻击者无法通过网络获得令牌/证书"的前提下（两者都只在本地 run 文件、不经网络明文传输），攻击者**连合法连接都建立不了**，重放面已经极小；`seq + ts + 一次性 ID` 是防御「合法客户端被攻陷后重放自己流量」的收尾，成本极低。

**备选方案对比**

| 方案 | 机密性 | 完整性 | 认证 | 说明 |
|---|---|---|---|---|
| TLS 自签 + pinning（**推荐**） | ✅ | ✅ | ✅（pinning 认证服务端） | 标准、久经考验、asyncio 原生支持；需 `cryptography` 生成证书 |
| 帧级 AEAD（HKDF(token) + AES-GCM） | ✅ | ✅ | ✅（令牌） | 无证书概念，但属自定义密码学封装，出错风险更高 |
| HMAC-SHA256 帧签名（stdlib only） | ❌ | ✅ | ✅（令牌） | 只能防篡改、不能防窃听，不满足目标 |

**落地要点**

- asyncio 原生支持：`asyncio.start_server(..., ssl=ctx)` / `asyncio.open_connection(..., ssl=ctx)`，Windows Proactor 下可用；NDJSON 帧结构不变，WIRE_PROTOCOL 应用层基本不变（新增 `hello` / `seq` / `ts` 字段）；
- 新依赖：`cryptography`（有 Windows wheel）；
- 性能：环回 + 对话级小报文，TLS 握手与加密开销可忽略；64 MB 大帧会被切成多个 TLS record（≤16 KB），内存拷贝增加 → 与 5.6 帧上限收紧联动；
- 配置：`DESK_IPC_TLS`（默认 on，安全默认；`off` 仅调试用）；
- 与令牌的关系：TLS 完成「服务端 → 客户端」认证（pinning）；令牌完成「客户端 → 服务端」认证（`core.hello` 在 TLS 信道内传输，令牌不再明文暴露在网络上）。

**介质迁移（Named Pipe / AF_UNIX，P2 远期）**

- Unix：AF_UNIX `~/.desk/run/desk.sock` + 0600 → 无 IP 栈 + OS 级权限，最干净；
- Windows：Python 3.9+ 的 `socket` 层支持 AF_UNIX，但 **asyncio 不支持 Windows 上的 unix server**；named pipe 需 pywin32 并显式设置 DACL。成本高，收益与「令牌 + TLS + 动态端口」重叠；
- **结论：列为远期项**。若未来出现"多用户共享 daemon"需求，再评估。

### 5.9 审计与可观测（P1）

- 认证成功/失败日志：`peer`、`client` 标识、token 指纹、结果；
- trace 的 `client_id` 从 `peername` 升级为认证后的 `client_id`（`socket_server.py` 中 `_handle_line` / `_send` / `ipc_broadcaster.py` 同步改）；
- `permission.respond` 记录来源 `client_id`（`manager.py:respond` 增加来源参数）；
- **敏感字段脱敏**：IPC trace 增加 `include_ipc_payload` 开关（默认摘要：只记 `method` / `id` / 事件 `type`），对齐 LLM 层已有的 `include_llm_payload`。

### 5.10 兼容性与回退

- **安全默认开启**；提供临时关闭开关 `[core] auth = false` / `DESK_IPC_AUTH=0` / `DESK_IPC_TLS=0`（调试/测试专用，文档明确警告）；
- 版本协商：`core.hello` 返回 `protocol_version`，客户端按版本适配（为将来协议演进预留）；
- 协议文档：`scripts/gen_protocol_doc.py` 增加 `core.hello`、新错误码、新结果类型后重新生成 `docs/WIRE_PROTOCOL.md`（勿手改）。

---

## 6. 落地计划

### 6.1 阶段划分

| 阶段 | 内容 | 依赖 | 验收标准 |
|---|---|---|---|
| 阶段 0 | 先写攻击模拟测试（红队先行）：未认证连接、伪造审批、偷听事件 | 无 | 测试复现 S-1/S-2 |
| 阶段 1（P0） | run 文件 + 令牌生成 + `core.hello` 握手 + 认证门禁 + `SocketClient.authenticate` + TUI/CLI 接入 + 错误码 | 阶段 0 | 未认证/错令牌连接被拒；正令牌全链路通过；S-1/S-2 攻击失败 |
| 阶段 2（P1） | 命令分级 + session 所有权/attach + `permission.respond` 归属校验 + 限速/连接上限/帧收紧 + 动态端口 + 审计/脱敏 | 阶段 1 | 伪造审批被拒（跨 session）；限速生效；status/stop 走 run 文件 |
| 阶段 3（传输完整性，必做） | TLS 自签 + pinning + 应用层防重放（seq/ts/一次性 ID）+ `cryptography` 依赖 + 协议文档重生成 + README/RUNBOOK 更新 | 阶段 2 | 抓包看不到明文；替换证书/中间人握手失败；重放旧 `seq` 报文被拒（-32005） |

### 6.2 涉及文件

**新增**

- `core/transport/runfile.py` ✅ —— run 文件读写（token/port/pid/证书指纹，含权限设置与旧 pid 兼容）
- `core/transport/auth.py` —— 握手校验、令牌比对、连接状态（已并入 socket_server，未单独建文件）
- `core/transport/tls.py` ✅ —— 自签证书生成、SSLContext 构建、run 文件指纹读写
- `core/transport/helpers.py` ✅ —— 客户端统一认证连接（`connect_authenticated`）
- `tests/unit/test_ipc_auth.py` ✅ —— 安全单元测试（runfile / TLS 握手 / 防重放校验）
- `scripts/security_smoke.py` ✅ —— 攻击模拟脚本（红队验收）

**修改**

| 文件 | 改动 | 状态 |
|---|---|---|
| `core/transport/socket_server.py` | TLS 包装、认证门禁、`core.hello` 处理、连接状态机、`seq`/`ts` 防重放 | ✅ |
| `core/transport/socket_client.py` | TLS 包装 + 证书 pinning、`authenticate()`、`seq`/`ts` 字段、错误处理 | ✅ |
| `core/transport/ipc_broadcaster.py` | 仅向 AUTHENTICATED 连接推送；`client_id` 升级 | ⏳（认证门禁已挡未认证订阅，client_id 升级待做） |
| `core/app.py` | 令牌/证书生成、启动写 run 文件、退出清理、`core.hello`（由 server 内建） | ✅ |
| `core/permissions/manager.py` | `respond()` 增加来源 `client_id` 与归属校验参数 | ⏳（阶段 2） |
| `core/config.py` | `auth` / `tls` 开关、`DESK_IPC_AUTH` / `DESK_IPC_TLS` | ✅ |
| `cli/commands/core.py` | start 等就绪 / stop 读 run 文件 / status 校验 pid | ✅ |
| `cli/commands/ping.py`、`chat.py`、`run.py` | 统一走 `connect_authenticated` | ✅ |
| `tui/app.py`、`tui/__main__.py` | connect 前读 run 文件取 token/port/证书（重连循环天然兼容轮换） | ✅ |
| `scripts/gen_protocol_doc.py` | 登记 `core.hello` / `seq` / `ts` / 新错误码，重生成 WIRE_PROTOCOL.md | ✅ |
| `README.md` / `docs/RUNBOOK.md` | 安全说明、配置项、故障排查 | ✅（README；RUNBOOK 待补） |
| `pyproject.toml` | 新增依赖 `cryptography`（自签证书生成 / TLS 落地） | ✅ |

### 6.3 风险与缓解

| 风险 | 缓解 |
|---|---|
| 客户端与 daemon 版本不同步（旧客户端无 hello） | `protocol_version` 协商；阶段 1 允许旧客户端收到明确错误而非静默失败 |
| run 文件写入与客户端读取竞态（daemon 未写完） | 客户端读不到/读到半截时重试（复用 TUI 重连循环；CLI 短重试 ≤3 次）；`cmd_core_start` 增加 `--wait` 等待 ping 成功 |
| Windows 权限模型差异（chmod 无效） | 文档化 `icacls` 指引；依赖用户目录 ACL |
| 限速误伤正常多客户端 | 限速参数可配置，默认值宽松（100 cmd/s 远高于人工操作） |
| `permission.respond` 归属校验破坏多客户端审批 UX | 当前无共享 session 场景（5.5 已论证）；如需共享，改为"任一已认证连接可响应 + 强制审计" |
| 64 MB 帧收紧影响 MCP 大文件结果 | P2 引入文件通道前，维持大帧上限但仅限已认证连接 |

---

## 7. 测试与验证

### 7.1 单元测试（`tests/unit/test_ipc_auth.py`）

- run 文件：生成/读取/权限（POSIX 0600）/token 轮换（重启后旧 token 失效）；
- 握手：正确/错误/空 token、`hmac.compare_digest`、hello 超时断开；
- 认证门禁：未认证调用 `session.create` 等 → `-32001`；
- 限速：令牌桶超限 → `-32003`；
- 会话绑定：非 owner 调 `get_history`/`close` → `-32002`；`attach` 后放行；
- TLS（阶段 3）：证书生成/指纹写入/客户端 pin 校验；自签证书作为唯一信任锚时，第三方证书握手失败；
- 防重放（阶段 3）：重复 `seq` 被拒（-32005）；超时间窗报文被拒。

### 7.2 集成测试（双进程，`tests/integration/test_ipc_security.py`）

- **S-1 回归**：模拟攻击者连接 → 无 token 发 `permission.respond` → 被拒；用错 token 连接 → 被拒；用 A 的 session 伪造 B 的 pending 审批 → 被拒；
- **S-2 回归**：未认证连接 `event.subscribe` / `replay_from_run` → 被拒；
- 正路径：认证后 TUI 全链路（subscribe → send_message → 事件流 → 审批）通过；
- daemon 重启：旧连接收到 `-32004`，新连接用新 token 成功（模拟 TUI 自动重连）；
- **传输层（阶段 3）**：抓包验证线上无明文（或断言不使用 ssl 的连接被拒）；替换证书/中间人模拟 → 握手失败；重放旧 `seq` 报文 → `-32005`。

### 7.3 攻击模拟（`scripts/security_smoke.py`，可选）

脚本模拟三类攻击并断言被拦截：① 无 token 连接 + 命令注入；② 偷读 run 文件权限校验；③ 伪造审批（自动 always_allow 轰炸）。

### 7.4 回归

- 现有 `tests/integration` 双进程 IPC 用例需适配认证（统一夹具：启动 daemon → 读 run 文件 → 认证）；
- `uv run ruff check` / `uv run mypy src` 保持全绿。

---

## 8. 决策记录与开放问题

### 已定决策（ADR 摘要）

| 编号 | 决策 | 理由 |
|---|---|---|
| ADR-1 | P0 采用**共享令牌认证**（方案 A），而非 TLS / Named Pipe | 本地威胁模型下认证解决 90% 问题；零新依赖；改动面最小 |
| ADR-2 | 令牌**每次 daemon 启动轮换** | 防令牌长期泄露；TUI 重连循环天然兼容 |
| ADR-3 | **认证即授权**（单用户本机模型），不做多租户 RBAC | 与产品定位一致，避免过度设计 |
| ADR-4 | **传输完整性必做**：TLS 自签 + pinning（非可选） | 产品决策聚焦"网络传输不被窃听/篡改/冒充"；无需 CA，成本可控 |
| ADR-5 | 动态端口列为 P1（不阻塞 P0） | 消除端口扫描/冲突；run 文件已承载连接信息 |
| ADR-6 | **否决**去掉网络改内嵌 | 违背"守护进程 + 多客户端"核心架构 |
| ADR-7 | TLS 采用**自签证书 + run 文件证书固定（pinning）**，不依赖 CA | 本地服务标准做法（Docker 等同）；每实例重新生成，与令牌轮换一致 |
| ADR-8 | 重放防护放在**应用层**（seq + 时间戳 + 一次性 ID），不指望 TLS | TLS 只保证传输完整性；应用层防重放成本极低且协议语义更清晰 |

### 开放问题

- **O-1 多客户端共享 session**：未来是否支持两个客户端同时操作同一 chat session？（影响 5.5 的归属策略——当前设计为 owner 独占 + 显式 attach）
- **O-2 用户 PIN**：是否在令牌之上叠加 `DESK_IPC_PIN`，以防御"同用户恶意进程直接读 token 文件"的 A2 场景？（成本低，收益有限但真实）
- **O-3 IPC trace 脱敏粒度**：`include_ipc_payload` 默认值取"摘要"还是"全量"？（涉及排查便利性与隐私的权衡）
- **O-4 协议版本号**：是否在本次改造中一并引入 `protocol_version` 协商？（推荐引入，成本极低）
- **O-5 大帧处理**：MCP 大文件工具结果的"文件通道"方案是否在 P2 一并实施？（影响帧上限收紧的节奏）
- **O-6 TLS 默认开关**：`DESK_IPC_TLS` 是否无条件默认开启（推荐，安全默认），还是仅在 host 非回环时强制开启？（影响调试便利性与安全默认的取舍）
