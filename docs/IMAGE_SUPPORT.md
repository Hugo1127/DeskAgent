# 图片多模态识别（read_image 工具）

> 状态：**已实施**。
> 技术路线：**内核工具化** —— 新增 `read_image` 工具，由 AI 在对话中自主调用，图片以 Anthropic image content block 进入 LLM 上下文，由模型视觉能力识别；不引入 OCR / opencv / Pillow，仅用标准库（`base64` / `pathlib`）。

## 能力

- 支持格式：png / jpg / jpeg / gif / webp；单张 ≤ 5MB（Anthropic API 上限）
- 默认权限 ALLOW（与 `read_file` 同级），Skills 白名单 / 策略可单独收紧
- `desk chat` / `desk-tui` / `desk run --goal` 三种入口零改动获得能力；协议层零改动

## 数据流

```
用户消息（纯文本，含图片路径）
  → AgentLoop 模型路由 → read_image{path}
  → 校验（文件存在 / 格式受支持 / ≤5MB）→ base64 → image content block
  → tool_result.content = [text, image]（list 多块）
  → 下一轮 LLM 请求，API 多模态识别
```

## 实现要点

| 模块 | 作用 |
|------|------|
| `core/llm/image_blocks.py` | 格式映射、5MB 上限、image block 构造、token 估算占位、脱敏占位符 |
| `core/tools/builtin/read_image.py` | read_image 工具（成功返回 [text, image]，失败返回中文错误） |
| `core/permissions/policy.py` | 默认 ALLOW + preview 参数 `path` |
| `core/runner.py` | 注册 ReadImageTool（受 `tool_whitelist` 约束，subagent / Skill 自动继承） |
| `core/tools/builtin/read_file.py` | 检测到图片扩展名时返回提示，引导改用 read_image |
| `core/tools/invocation.py` | 事件展示用 `[image: media_type, size]` 占位符，不落 base64 |
| `core/compact/compactor.py` | 摘要与 token 估算对 image 块用占位符 / 固定值，避免 base64 算成天量 token |
| `core/trace/provider.py` | trace 写盘前递归脱敏 image 块 |

## 边界情况

- 路径不存在 / 非图片格式 / 超 5MB → 工具返回错误结果，AI 可换路径或压缩后重试
- 多张图 → 模型多次调用工具，各生成独立 tool_result
- /compact、trace、事件流 → 图片仅以占位符呈现，不含 base64

## 验证

- 单元测试：`tests/unit/test_read_image.py`
- 手动冒烟：`desk chat` 输入「这张图里有什么 D:\test.png」→ 模型应调用 `read_image` 并流式返回描述

## 后续扩展（本期不做）

- 应用层 `@路径` 提及、文本文件 `@` 提及、PDF / 音频工具、按尺寸精确 token 估算、附件目录化持久化
