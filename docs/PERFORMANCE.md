# 性能优化方案（PERFORMANCE）

> 状态：**规划中，尚未改动任何代码**。本文档记录 DeskAgent 的性能优化分析、分级方案与实施顺序，作为后续落地的依据。

## 背景与目标

DeskAgent 是**常驻后台的个人桌面管家**（守护进程 + CLI/TUI 多客户端），对资源占用敏感：

- 空闲时应**低内存、零 CPU**，不能影响用户日常使用电脑；
- 任务执行时端到端延迟主要来自 LLM 网络调用，本地开销应尽量小；
- 守护进程不能被单个工具调用或事件扇出卡死（所有客户端共享同一个事件循环）。

## 核心结论：不整体重写 C++

**结论：不要用 C++ 整体重写，这是当前最不该做的事。** 依据：

1. **瓶颈是 I/O 而非 CPU**。一次 `run` 的墙钟时间中，LLM API 网络调用占 80~95%（`core/llm/provider.py` 的 `chat()` 是纯等待），本地 Python 代码消耗的 CPU 时间占比极小。C++ 对网络等待无帮助。
2. **重活已在 C/Rust 里**：`psutil`（系统监控）是 C 绑定、`pydantic-core`（所有事件序列化）是 Rust、`anthropic`/`httpx` 底层网络栈由 C 加速。Python 只做胶水，不是热点。
3. **"占资源"≠语言问题**：空闲占用由"加载了什么库 + 有没有忙等"决定。asyncio 守护进程空闲时天然 0% CPU；内存大头是 pydantic + httpx + anthropic（约 60~100MB），可用懒加载压低（见 D 级），换 C++ 反而引入运行时成本。
4. **重写代价极高**：项目的核心卖点是 Skills（Markdown 即插即用）、MCP 扩展、TOML 角色档案、快速迭代，全建立在 Python 生态上；重写后扩展模型全部作废，性能收益趋近于零。

将来若确需编译加速，采用**定点替换**（而非整体重写）：`system_query` 采集器用 Rust (PyO3) 写小扩展、JSON 序列化换 `orjson` 等。**当前不做。**

## 基线测量（动手前必须做）

在 `core/loop.py` 每个 step 内用 `perf_counter` 分别记录：

- LLM 调用耗时（`provider.chat()`）；
- 工具执行耗时（`invoke_tool`）；
- 事件发布耗时（`bus.publish`，含 trace / 广播 / 落盘）。

空闲时用 `psutil` 采样守护进程 RSS 与 CPU。跑 2~3 个真实任务拿到分布后再投入。预判：LLM 占 85%+，本地开销中**事件扇出 + 文件写入**占大头。

---

## A 级：事件总线的本地热点（最高 ROI）

每次 LLM 返回一个 token chunk，`provider.py` 就 `bus.publish(LlmTokenEvent)` 一次；而 `EventBus.publish`（`core/events/bus.py`）**串行 await 所有订阅者**，每个事件被重复序列化 3~4 次：

| 位置 | 每事件开销 |
|------|-----------|
| `core/app.py` `_trace_event_handler` | `model_dump()` 一次 → 入队 → trace 落盘**每条 flush** |
| `core/transport/ipc_broadcaster.py` `handle` | 再 `model_dump()` 一次，每个订阅者各 `model_dump_json()` + `drain()` |
| `core/events/writer.py` | 再 `model_dump_json()` 一次落盘 |

### A1. 发布改并发扇出

- **位置**：`core/events/bus.py`
- **做法**：`asyncio.gather(*[h(event) for h in subscribers])`，trace / 广播 / 持久化三者相互独立，串行改并行。
- **收益**：事件发布延迟由"最慢订阅者"之和变为最大值；token 事件密集时收益显著。
- **风险**：订阅者执行顺序不再确定；需确认无订阅者间顺序依赖（如广播必须先于落盘）。

### A2. 序列化只做一次

- **位置**：`core/events/bus.py` + 各订阅者
- **做法**：`publish` 内 `event.model_dump()` 一次，把 dict 传给所有订阅者复用；`EventPushEnvelope` 直接包装该 dict，避免逐订阅者重复 `model_dump_json()`。
- **收益**：每个事件由 3~4 次序列化降为 1 次，token 事件（每 step 数十~数百个）场景最明显。
- **风险**：订阅者签名变化，需同步修改 `core/app.py`、`ipc_broadcaster.py`、`events/writer.py`、`trace/record.py` 调用方。

### A3. TraceWriter 批量 flush

- **位置**：`core/trace/writer.py` `_drain()`
- **做法**：现在每条记录 `f.flush()`（同步 syscall，卡事件循环）；改为定时（如 50ms）或定量（如 200 条）批量 flush，或复用事件总线的批量写入通道。
- **收益**：消除每事件一次同步磁盘 syscall；长 run 落盘开销可忽略。
- **风险**：进程崩溃时最多丢一个 flush 窗口的 trace（可接受，trace 本身是诊断用途）。

### A4. token 事件节流 + 不落盘

- **位置**：`core/llm/provider.py`、`core/events/writer.py`
- **做法**：
  1. token 事件聚合 30~50ms 窗口再广播，IPC 包数量降一个数量级，TUI 渲染更顺；
  2. `LlmTokenEvent` 不写入 events.jsonl（现在每个 token 都落盘，一个 run 的会话文件膨胀到数 MB；回放只需结构事件，不需要逐 token）。
- **收益**：IPC 与磁盘写入量降 10~50 倍，是本地开销最大单项。
- **风险**：TUI 流式渲染依赖 token 事件，节流需保证首包延迟不劣化（窗口内首 token 立即发出）。

---

## B 级：阻塞事件循环的同步调用

以下同步调用会**冻结整个守护进程**（所有客户端 IPC、其他 run 的事件全部卡住），必须移出事件循环：

### B1. monitor 采集移入线程池

- **位置**：`core/tools/builtin/query/monitor.py`
- **做法**：`psutil.cpu_percent(interval=0.1, percpu=True)`（每次阻塞 **100ms**）与 `process_iter` 进程枚举包进 `asyncio.to_thread`。
- **收益**：一次监控查询不再冻结守护进程 100ms+。

### B2. 注册表软件枚举移入线程池

- **位置**：`core/tools/builtin/query/software.py`
- **做法**：Windows 注册表全量枚举（读数百个注册表项）包进 `asyncio.to_thread`。

### B3. session 落盘复用文件句柄

- **位置**：`core/session/store.py`
- **做法**：`thread.jsonl` 每条消息 `open("a")` + write（反复开关文件句柄）改为复用句柄或走写入队列（参照 `TraceWriter` 模式）。
- **收益**：run 内每条消息少一次 open/close 系统调用。

---

## C 级：端到端延迟（用户体验提升最大）

### C1. 并行执行多个工具调用

- **位置**：`core/loop.py` `run()`
- **做法**：模型一次返回多个 `tool_use` 时，用 `asyncio.gather` 并发执行 `invoke_tool`（现在是 `for` 串行）。
- **收益**：工具阶段耗时按并行度成倍下降。
- **风险**：权限审批事件顺序、`ToolCallStarted/Finished` 事件交错；需保证事件仍带 `tool_use_id` 可区分；串行依赖场景（工具间有数据依赖）不受影响——并行与否由模型返回的 tool_calls 结构决定，天然安全。

### C2. Prompt caching 增强

- **位置**：`core/llm/provider.py`
- **做法**：现有对 system 与最后一个 tool schema 打 `cache_control: ephemeral`；再对最近消息尾部打 ephemeral cache。
- **收益**：长会话多步 run 显著降低输入 token 延迟与费用。
- **风险**：需确认 Anthropic 对多 cache 断点的支持；消息变化会失效尾部缓存。

### C3. 工具 schema 缓存

- **位置**：`core/loop.py`（每步调用 `self._registry.tool_schemas()`）、`core/tools/registry.py`
- **做法**：`tool_schemas()` 结果缓存，注册表变化（MCP 工具增删）时失效。
- **收益**：每步省一次 schema 列表重建；量级小，属顺手优化。

---

## D 级：资源占用治理（回应"不要太占性能"）

### D1. 懒加载重依赖

- **位置**：`core/app.py`、`cli/`、`tui/`
- **做法**：守护进程启动即 import `anthropic`/`httpx`（仅为了 `compact_provider`）——改为真正跑 run 时才构建 provider；CLI/TUI 同理按需 import。
- **收益**：空闲 RSS 降 20~40MB，`desk --help` 秒开。

### D2. trace / events.jsonl 保留策略

- **位置**：`core/trace/writer.py`、`core/runs.py`（events 目录）
- **做法**：自动轮转与清理（保留最近 N 个 run / 限制文件大小）。
- **收益**：长期运行磁盘不无限增长。

### D3. 缩短关闭流程

- **位置**：`core/app.py`（`_SHUTDOWN_WATCHDOG_S = 15.0`）
- **做法**：A3 落地后写入变快，可相应缩短看门狗限时，减少"卡住感"。

---

## 明确排除（不做）

| 项 | 原因 |
|----|------|
| 整体 C++/Rust 重写 | 瓶颈是 I/O；重写作废 Skills/MCP 扩展生态，收益趋近于零 |
| uvloop | Windows 不可用，忽略 |
| PyInstaller / Nuitka 冻结打包 | 是分发问题而非性能问题；仅将来交付终端用户时再议 |
| orjson 替换 | pydantic-core 已够快；A2 落地后序列化开销已非热点，后议 |

## 实施顺序

1. **基线测量**（见上）—— 半天
2. **A 级 4 项**：纯本地优化，无风险、收益最直接（A4 节流 + A2 单次序列化 + A1 并发扇出 + A3 批量 flush）
3. **B 级 3 项**：消除守护进程冻结
4. **C 级 3 项**：用户可感知的响应速度
5. **D 级**：资源治理，随用随做

> 预期：A+B 落地后，本地 CPU 开销（非 LLM 等待部分）降一个数量级，空闲内存降 20~40MB；全程不动架构、不动协议、不引入新依赖。

## 落地清单（勾选跟踪）

- [ ] 基线测量脚本 / 数据
- [ ] A1 并发扇出
- [ ] A2 单次序列化
- [ ] A3 TraceWriter 批量 flush
- [ ] A4 token 节流 + 不落盘
- [ ] B1 monitor 移入线程池
- [ ] B2 software 枚举移入线程池
- [ ] B3 session 落盘复用句柄
- [ ] C1 并行工具调用
- [ ] C2 prompt caching 增强
- [ ] C3 工具 schema 缓存
- [ ] D1 懒加载
- [ ] D2 trace/events 保留策略
- [ ] D3 缩短关闭流程
