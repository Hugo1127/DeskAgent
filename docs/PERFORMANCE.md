# 性能优化方案（PERFORMANCE）

> 状态：**规划中，尚未改动任何代码**。本文档记录结论与待办清单，落地后更新。

## 结论

- **不整体重写 C++**：瓶颈是 LLM 网络 I/O（单次 run 墙钟的 80~95%），本地 Python 只是胶水；psutil / pydantic-core / httpx 等重活已在 C / Rust 中。
- 将来确需编译加速时采用**定点替换**（如 system_query 采集器用 PyO3、JSON 换 orjson），**当前不做**。
- 落地任何优化前先做**基线测量**：loop 内分阶段计时（LLM / 工具执行 / 事件发布）+ 空闲 RSS / CPU 采样。

## 措施清单（按优先级）

### A 级：事件总线本地热点（最高 ROI）

- A1 事件发布并发扇出（`core/events/bus.py`，当前串行 await 所有订阅者）
- A2 序列化只做一次（当前 trace / 广播 / 落盘各自重复序列化）
- A3 TraceWriter 批量 flush（当前每条记录同步 flush，卡事件循环）
- A4 token 事件节流 + 不落盘（当前每个 token 逐条广播并写入 events.jsonl）

### B 级：阻塞事件循环的同步调用

- B1 monitor 采集移入线程池（`psutil.cpu_percent(interval=0.1)` 等会冻结守护进程）
- B2 注册表软件枚举移入线程池
- B3 session 落盘复用文件句柄（当前每条消息 open/close）

### C 级：端到端延迟（用户体验）

- C1 并行执行多个工具调用（当前串行 `for`）
- C2 prompt caching 增强（当前仅 system 与最后一个 tool schema 打 cache_control）
- C3 工具 schema 缓存（`tool_schemas()` 每步重建）

### D 级：资源占用治理

- D1 懒加载 anthropic / httpx（当前 daemon 启动即 import 并构造 compact_provider）
- D2 trace / events.jsonl 保留策略（自动轮转与清理）
- D3 缩短关闭看门狗限时（`_SHUTDOWN_WATCHDOG_S = 15.0`）

## 明确排除（不做）

| 项 | 原因 |
|----|------|
| 整体 C++/Rust 重写 | 瓶颈是 I/O；重写作废 Skills / MCP 扩展生态，收益趋近于零 |
| uvloop | Windows 不可用 |
| PyInstaller / Nuitka 冻结打包 | 是分发问题而非性能问题，将来交付用户时再议 |
| orjson 替换 | pydantic-core 已够快，A2 落地后再评估 |

## 实施顺序

1. 基线测量
2. A 级（A4 节流 + A2 单次序列化 + A1 并发扇出 + A3 批量 flush）
3. B 级（消除守护进程冻结）
4. C 级（用户可感知的响应速度）
5. D 级（资源治理，随用随做）

> 预期：A+B 落地后本地 CPU 开销（非 LLM 等待部分）降一个数量级，空闲内存降 20~40MB；全程不动架构、不动协议、不引入新依赖。
