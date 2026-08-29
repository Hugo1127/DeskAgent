# IPC 通信安全（TUI / CLI ↔ desk-core）

> 状态：**已落地**（令牌认证 + TLS 传输加密 + 应用层防重放）；P1 增强项规划中（见下文）。
> 范围：desk-core 守护进程与 CLI / TUI 客户端之间的 IPC 通信。
> 协议细节见 [WIRE_PROTOCOL.md](WIRE_PROTOCOL.md)（自动生成，勿手改）。

## 安全机制（默认全部开启）

### 1. 令牌认证（core.hello）

- daemon 每次启动生成 256-bit 随机令牌（`secrets.token_urlsafe(32)`，**每次启动轮换**），写入 `~/.desk/run/daemon.json`（token / port / pid / started_at / version / cert_sha256）；POSIX 下文件 0600，Windows 依赖用户目录 ACL。
- 客户端连接后必须先发 `core.hello {token, client}`，服务端用 `hmac.compare_digest` 常数时间校验；成功后返回 `protocol_version / server_version / client_id`，连接进入 AUTHENTICATED 状态。
- 未认证连接只允许 `core.hello`，其余命令一律返回 `-32001 UNAUTHENTICATED` 并断开。
- token **绝不写入日志 / trace**（hello 入 trace 时仅保留 `client` 字段）。
- 关闭认证（仅调试）：`[core] auth = false` 或 `DESK_IPC_AUTH=0`。

### 2. TLS 传输加密（自签证书 + 证书固定）

- 本地服务不需要公共 CA：daemon 每次启动用 `cryptography` 生成**一次性自签证书**（ECDSA P-256，SAN 含 host / 127.0.0.1 / localhost），私钥与证书落盘 `~/.desk/run/desk.key`、`desk.crt`（POSIX 0600），证书 SHA-256 指纹写入 run 文件。
- 客户端以 run 文件中的证书 PEM 为**唯一信任锚**（`load_verify_locations(cadata=...)`）；任何其他证书（MITM 攻击者）在握手阶段即被拒绝。
- 关闭 TLS（仅调试）：`[core] tls = false` 或 `DESK_IPC_TLS=0`。

### 3. 应用层防重放

- 每条请求携带 `seq`（每连接从 1 严格递增）与 `ts`（epoch 秒）；服务端拒绝 `seq <= last_seq` 或 `|now - ts| > 60s` 的报文，返回 `-32005 STALE_SEQUENCE`。
- `permission.respond` 依赖一次性 `tool_use_id`（响应后即从 pending 表移除），天然不可重放。

## 连接流程

```
客户端
 1. 读 ~/.desk/run/daemon.json（token / port / 证书）
 2. TLS 握手（以 run 文件证书固定校验服务端）
 3. core.hello {token, client} → AUTHENTICATED
 4. 正常命令 / 事件订阅
```

CLI / TUI 统一走 `connect_authenticated()`（`core/transport/helpers.py`）；daemon 重启导致令牌 / 证书轮换时，客户端重读 run 文件自动重连。

## 错误码

| 代码 | 含义 | 状态 |
|------|------|------|
| `-32001` | 未认证 / 令牌错误 | 已启用 |
| `-32005` | 报文序号重复 / 时间戳过期（防重放） | 已启用 |
| `-32002` / `-32003` / `-32004` | 会话归属 / 限速 / 令牌过期 | 协议预留，尚未启用 |

## 未落地（P1 规划）

- 命令分级与会话归属（`session.attach`、`permission.respond` 来源归属校验、trace 的 `client_id` 升级）
- 连接数 / 速率限制、动态端口、IPC trace 敏感字段脱敏开关（`include_ipc_payload`）

## 验证

- 单元测试：`tests/unit/test_ipc_auth.py`
- 红队脚本：`scripts/security_smoke.py`（无令牌 / 错令牌 / 明文连接 / 伪造审批 / 重放 seq / 过期时间戳等场景）

## 涉及文件

| 文件 | 作用 |
|------|------|
| `core/transport/runfile.py` | run 文件读写（token / port / pid / 证书指纹），兼容旧 pid 文件 |
| `core/transport/tls.py` | 自签证书生成 + 服务端 / 客户端 SSLContext（pinning） |
| `core/transport/socket_server.py` | TLS 包装、认证门禁、core.hello、seq/ts 防重放 |
| `core/transport/socket_client.py` | TLS 连接、authenticate()、seq/ts 字段 |
| `core/transport/helpers.py` | 客户端统一认证连接 |
| `core/bus/envelope.py` | 请求信封（seq / ts）与应用层错误码 |
| `cli/commands/core.py` | start / stop / status（读 run 文件、PID 校验） |
