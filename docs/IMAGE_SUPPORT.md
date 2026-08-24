# 图片多模态识别 · 实现方案（v2：内核工具化）

> 状态：**已实施**（2025 版本基线）。本文档为设计说明与实施记录。
> 技术路线：**内核工具化** —— 新增 `read_image` 工具，由 AI 在对话中自主调用，
> 用 **Anthropic API 自带的多模态能力（image content block）** 识别图片。
> 不引入 OCR / opencv / Pillow 等外部库，图片处理全部使用 Python 标准库（`base64` / `pathlib`）。
> 应用层 `@路径` 提及属于**后续增强**（见第 8 节），本期不做。

---

## 1. 目标与约束

### 1.1 目标

- 内核层（AgentLoop + 工具）支持图片识别：AI 在对话中看到图片路径（用户给出 / `list_dir` 列出）后，
  调用 `read_image` 工具读取图片，**图片以原生 image content block 进入 LLM 上下文**，API 完成识别
- 三种入口（`desk chat` / `desk-tui` / `desk run --goal`）**零改动**即获得该能力
- 协议层（`session.send_message` / `agent.run`）**零改动**，完全向后兼容

### 1.2 硬约束

- ✅ 用 Anthropic API 自带多模态（image content block），底层 `anthropic` SDK 已支持
- ❌ 不引入任何外部图像/OCR 库，仅 Python 标准库
- ✅ 协议、客户端、会话消息格式均不动

### 1.3 Anthropic Vision API 约束（已核实）

参考：[Claude Vision 官方文档](https://platform.claude.com/docs/en/build-with-claude/vision)

- 支持格式：`jpeg` / `png` / `gif` / `webp`
- 单张 ≤ **5MB**；单请求 ≤ 100 张
- image 块格式：`{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<base64>"}}`
- **工具结果天然支持多块**：`tool_result` 的 `content` 可以是 `str | list[content blocks]`，
  图片块放进工具结果是 API 原生支持的用法 —— 这正是"工具化"路线的可行性基础

---

## 2. 设计决策：为什么工具化（核心）

### 2.1 图片识别设计为工具，而不是消息附件

| 维度 | 工具化（本次采用） | 消息附件 / `@` 提及（v1，已放弃） |
|------|--------------------|-----------------------------------|
| 改动范围 | 仅内核：新工具 + 工具结果类型放宽 | 协议层 + 会话层 + 3 个客户端入口 |
| 用户输入 | 自然语言里给路径即可，AI 自主调用 | 需新增 `@` 语法解析 |
| 扩展性 | subagent / Skill 白名单 / 权限策略自动继承 | 需为每个客户端重复实现 |
| 权限控制 | `read_image` 可独立授权 | 附件无权限粒度 |
| 失败恢复 | 工具错误进对话，AI 可换路径重试 | 消息级错误，交互生硬 |

工具化把能力放进**内核的既有抽象**（工具注册表 + 权限 + 事件流），
AI 通过 ReAct 循环自主决定何时读图；客户端只需传文本，后续想加 `@` 提及只是
"把路径自动写进用户消息"的体验增强，不动内核。

### 2.2 独立 `read_image` 工具 vs 扩展 `read_file`（定位分析）

**结论：新增独立工具 `read_image`，`read_file` 保持文本定位，但增加"图片检测提示"。**

理由：

1. **职责与校验规则不同，合并会互相污染**
   - `read_file`：文本语义，512KB 截断、utf-8 解码、`..` 路径遍历防护（见 `core/tools/builtin/read_file.py`）
   - `read_image`：视觉语义，5MB 上限、媒体类型白名单、base64 编码
   - 两种上限（512KB vs 5MB）、两种返回格式（str vs blocks）绑在一个工具里，描述与实现都变复杂，
     模型更难预测行为
2. **上下文安全**：`read_file` 默认 ALLOW。若让 `read_file` 对图片返回 base64，模型一旦误判文件类型，
   会把数 MB base64 文本注入上下文。独立工具 + 明确描述（"仅用于图片文件"）+ 5MB 上限，风险可控
3. **模型路由清晰**：工具描述写明适用扩展名，对话中路径/扩展名通常可见（用户给出或 `list_dir` 列出），
   模型按扩展名路由即可
4. **权限粒度**：`read_image` 默认 ALLOW（读用户可访问的文件，与 `read_file` 同级），
   但 Skills 白名单、策略可单独收紧/撤销
5. **未来扩展**：PDF / 音频等后续各自独立工具，不污染 `read_file`

**`read_file` 的唯一小增强**：当前对图片文件会返回 utf-8 replace 解码的乱码（噪音）。
改为：检测到图片扩展名时返回错误结果并提示
`"这是图片文件 (.png)，请使用 read_image 工具查看"` —— 保持文本定位，同时引导模型正确路由，
避免模型反复尝试 `read_file` 读图失败。

---

## 3. 数据流

```
用户: "这张图里写了什么 D:\pic\screenshot.png"
   │  （纯文本消息，客户端/协议零改动）
   ▼
AgentLoop: 模型看到路径 → 发起工具调用 read_image{path}
   │  invoke_tool → 权限(默认 ALLOW) → ReadImageTool.invoke
   ▼
ReadImageTool: 校验(存在/格式/≤5MB) → base64 → 构造 blocks
   │
   ▼
tool_result.content = [
     {"type": "text",  "text": "[read_image] D:\pic\screenshot.png"},
     {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
   ]
   │  context.add_tool_result（content 类型放宽为 str | list）→ 下一轮 LLM 请求
   ▼
Anthropic API 多模态识别 → 文字回复流式回显
```

---

## 4. 分层改动明细（7 个代码文件 + 1 新工具 + 1 新模块 + 测试 + 文档）

> 对比 v1：**协议层、会话层、3 个客户端入口全部不再改动**。

### 4.1 新建 `src/desk_agent/core/llm/image_blocks.py`（图片块构造辅助模块，纯标准库）

> 模块职责是"把图片路径变成 Anthropic image content block"，位于 LLM 消息内容层（`core/llm/`）。
> 不含任何 `@引用` / 消息附件逻辑（应用层职责，后续另行设计）。

- `SUPPORTED_IMAGE_MEDIA_TYPES: dict[str, str]` — `.png/.jpg/.jpeg/.gif/.webp → media_type` 显式映射，新增格式在此扩展
- `MAX_IMAGE_BYTES = 5 * 1024 * 1024`
- `image_suffix(path) -> str | None` — 图片扩展名检测（read_file 用它做提示）
- `image_content_block(path: str) -> dict[str, object]`
  - 校验：文件存在 → 扩展名受支持 → ≤5MB；失败抛中文 `FileNotFoundError` / `ValueError`（附修复建议）
  - 成功：`{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}`
- `estimate_image_tokens(block) -> int` — token 估算扩展点（当前固定占位值，未来按尺寸精确计算）
- `image_block_placeholder(block: dict) -> str` — `[image: image/png, ~1.2MB]`，供 compactor / trace / 事件展示脱敏
- 未来扩展方向：新增图片格式只需扩映射表；PDF 等新内容块类型在同级新建模块（如 `pdf_blocks.py`），复用本模块校验/脱敏模式

### 4.2 新建 `src/desk_agent/core/tools/builtin/read_image.py`（新工具）

```python
class ReadImageParams(BaseModel):   # extra="ignore"
    path: str

class ReadImageTool(BaseTool):
    name = "read_image"
    description = (
        "Read an image file (png/jpg/jpeg/gif/webp, up to 5MB) and return its visual "
        "content so you can see it. Use for screenshots, diagrams, photos, OCR-like tasks. "
        "For text files use read_file."
    )
```

- `invoke`：try/except 包裹 `image_content_block`；
  - 成功 → `ToolResult(content=[{"type":"text","text":f"[read_image] {path}"}, image_block])`
  - 失败 → `ToolResult(content=f"read_image 失败: {中文错误}", is_error=True, error_type="runtime_error")`
    （错误返回 str，保证后续层 `is_error` 路径的 content 恒为 str）

### 4.3 `src/desk_agent/core/tools/base.py`（1 行类型放宽）

```python
@dataclass
class ToolResult:
    content: str | list[dict[str, object]]   # str：文本；list：多 content blocks（如 text + image）
    is_error: bool = False
    error_type: str | None = None
```

### 4.4 `src/desk_agent/core/context.py`（1 行类型放宽）

- `add_tool_result(tool_use_id, content: str | list[dict[str, Any]], is_error=False)`，
  内部 `"content": content` 原样透传（Anthropic tool_result 原生支持 list）

### 4.5 `src/desk_agent/core/tools/invocation.py`（2 处小改，事件展示脱敏）

- `ToolCallFinishedEvent(output=...)` 的 `output` 是 `str`（TUI 展示用）：
  当 `result.content` 为 list 时，传 `image_block_placeholder` 拼接的文本摘要，不落 base64
- `is_error` 分支 `error_message = result.content`：约定错误结果 content 恒为 str，加断言/类型收窄

### 4.6 `src/desk_agent/core/compact/compactor.py`（2 处小改）

1. `_messages_to_text()` 的 `tool_result` 分支：`content` 现可能为 list，
   需递归处理（text 子块 → 文本；image 子块 → `image_block_placeholder`），否则摘要请求混入 base64
2. token 估算：当前 `len(str(content)) // 4` 会把 base64 算成天量（2MB 图 ≈ 70 万 token）。
   改为遍历 blocks：image 子块按固定 `IMAGE_TOKEN_ESTIMATE`（约 1500）计，其余按字符/4

### 4.7 `src/desk_agent/core/trace/provider.py`（脱敏）

- `include_payload=True` 时，写 trace 前对 messages 做**递归脱敏**：
  任意层级的 image 块（顶层 user content 或 tool_result 内）替换为 `image_block_placeholder`；
  发给 API 的请求保持原样

### 4.8 `src/desk_agent/core/permissions/policy.py`（2 行）

```python
DEFAULT_POLICIES: dict[str, ToolPolicy] = {
    ...
    "read_file":  ToolPolicy(default=PermissionDecision.ALLOW),
    "read_image": ToolPolicy(default=PermissionDecision.ALLOW),   # 新增：与 read_file 同级
}
_PREVIEW_KEY 增加 "read_image": "path"
```

### 4.9 `src/desk_agent/core/runner.py`（1 行注册）

- `_build_registry` 中 `ReadFileTool()` 旁注册 `ReadImageTool()`（受 `tool_whitelist` 约束，
  subagent / Skill 白名单自动继承）

### 4.10 `src/desk_agent/core/tools/builtin/read_file.py`（定位增强）

- 读取前检查扩展名：命中 `IMAGE_MEDIA_TYPES` → 返回
  `ToolResult(content="这是图片文件 (.png)，无法作为文本读取。如需查看图片内容，请使用 read_image 工具。", is_error=True)`
- 其余行为不变

### 4.11 测试 `tests/unit/test_read_image.py`（新建，14 个用例，纯只读夹具）

> 夹具使用项目内真实图片 `workspace/1.jpg`（只读），避免测试写入临时文件；
> 超限用例通过 monkeypatch 缩小 `MAX_IMAGE_BYTES` 触发，无需生成 >5MB 文件。

| 用例 | 断言 |
|------|------|
| `read_image` 正常（`workspace/1.jpg`） | content 为 `[text, image]`；image 块 base64 解码后与文件字节一致；media_type=`image/jpeg` |
| 路径不存在 | `is_error=True`，消息含路径与提示 |
| 不支持格式（`pyproject.toml`） | `is_error=True`，提示支持格式 |
| 超 5MB（monkeypatch 上限） | `is_error=True`，提示压缩 |
| `read_file` 读图片 | `is_error=True`，提示改用 read_image |
| `read_file` 读文本（回归） | 正常返回文件内容 |
| `truncate_tool_results` | tool_result 的 list content（含 image 块）不被破坏 |
| `_messages_to_text`（compactor） | image 块输出占位符，不含 base64 |
| `_estimate_tokens` | 图片按固定占位值计，base64 不算成天量 |

### 4.12 文档

- 更新 `docs/WIRE_PROTOCOL.md`：**无需改动**（协议层零改动）
- 更新 README「内置工具与权限」表：新增 `read_image` 一行
- 本文档保留为设计说明

---

## 5. 无需改动的部分（明确列出，防止误改）

| 模块 | 原因 |
|------|------|
| `core/bus/commands.py` | 协议零改动，用户消息仍是纯文本 |
| `core/app.py` | 无新命令 |
| `core/session/manager.py` | 用户消息构造不变（图片由工具结果承载，不落 thread 的 user 消息） |
| `core/session/store.py` | `MessageContent = str | list[dict]` 已支持；`truncate_tool_results` 的 `isinstance(content, str)` 判断天然跳过 list |
| `core/loop.py` | 透传 `result.content`，类型随 `ToolResult` 放宽 |
| `cli/commands/chat.py` / `run.py`、`tui/app.py` | 零改动即获得能力（用户给路径，AI 自主调工具） |

---

## 6. 边界情况与处理

| 场景 | 处理 |
|------|------|
| 路径不存在 / 被删除 | 工具返回错误结果，AI 看到后可换路径或询问用户 |
| 非图片格式 | 错误结果 + 支持格式列表 |
| 单图 > 5MB | 错误结果 + 压缩建议 |
| 模型误调 `read_file` 读图片 | `read_file` 返回提示，引导 `read_image` |
| 多张图 | 模型多次调用工具，各生成独立 tool_result |
| 续聊 / 回放 | thread.jsonl 以 Anthropic 格式存 tool_result blocks，重放即原样 |
| `/compact` 压缩含图会话 | 摘要请求中图片占位呈现，摘要为纯文本 |
| trace 开启 | 图片数据脱敏，仅记录占位符 |
| subagent | 子 Agent 通过 `_build_registry` 自动获得 `read_image` |
| Skill 白名单 | 未列入白名单的 Skill 无法调用 `read_image`（默认行为） |

---

## 7. 验证计划

```bash
uv run ruff check src tests scripts      # lint
uv run mypy src                          # 类型检查（strict）
uv run pytest tests/unit -v              # 单元测试（含新增 test_read_image.py）
uv run python scripts/gen_protocol_doc.py --check   # 协议文档校验（应无变化）
```

**手动冒烟**（需 daemon 运行 + `ANTHROPIC_API_KEY`）：

1. 准备 `test.png`，`desk chat` 输入 `这张图里有什么 D:\test.png` → 模型应调用 `read_image` 并流式返回描述
2. `desk run --goal "识别 D:\test.png 中的文字"` → 单次任务同样生效
3. 输入不存在的路径 → 工具报错，AI 换路径或说明
4. `desk-tui` 同样验证
5. 续聊第二轮，历史中 image 块正常进入上下文

---

## 8. 后续扩展方向（本期不做）

- **应用层 `@路径` 提及**：客户端把提及展开为"请用 read_image 查看 <路径>"注入用户消息（纯体验增强，内核已支持）
- **文本文件 `@` 提及**：把文本内容直接内联进消息（与 Claude Code 对齐）
- **PDF / 音频工具**：各自独立工具，复用 tool_result 多块机制
- **图片 token 精确统计**：按尺寸公式（`tiles × 750 + 85`）替换固定占位值
- **附件目录化持久化**：图片复制进 session 目录，thread 只存相对路径（日志更小、可移植）
