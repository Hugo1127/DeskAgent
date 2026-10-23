# Skills 加载与执行机制（SKILLS）

> 状态：**设计稿，尚未改动代码**。
> 目标：让用户自己的 Skill（任意目录、Claude-style `SKILL.md`）既能被模型自动发现，也能真正执行其脚本与资源；同时保持对现有 `.desk/skills/*.md` 的兼容。
> 结论：现有 `SkillLoader` 只完成了「发现」，还缺「模型侧加载入口」「技能根目录」「路径策略」「管理面」四块。

## 1. 结论

当前 Skill 的本质是一个**斜杠宏 + system prompt 覆盖**，不是一项模型可自主使用的能力：

- 只有用户输入 `/name` 时，`SessionManager` 才会解析并注入 Skill；
- 模型系统提示中没有 Skill 清单，也没有 `use_skill` 之类的工具；
- `Skill` 对象不携带技能目录，`read_file` / `list_dir` / `bash` 无法访问 `SKILL.md` 旁边的 `scripts/`、`schemas/`、`assets/`；
- 因此「内置 Skill 看起来能用」只是因为它们不依赖外部文件；用户带资源的 Skill 会卡在路径上。

目标机制拆成三层：

```
来源发现（SkillRoot）
    → 注册表（SkillRegistry：解析 / 校验 / 缓存 / catalog / diagnostics）
        → 激活（显式 /skill、隐式 use_skill、资源访问）
```

## 2. 现状

### 2.1 已有能力

`SkillLoader`（`src/desk_agent/core/skills/loader.py`）已经支持：

- 三级查找：项目 `.desk/skills` > 用户 `~/.desk/skills` > 内建 `core/skills/builtin`；
- 两种布局：扁平 `name.md` 与目录式 `name/SKILL.md`；
- front-matter：`name`、`description`、`allowed_tools`；
- `/name args` 触发：`SessionManager.send_message` 解析，展开成 `goal` + `system_prompt_override` + `tool_whitelist`；
- TUI 斜杠补全：`tui/app.py` 在客户端本地 `new SkillLoader()` 列出候选。

### 2.2 实测

用户目录下已安装 `archify`（约 15 KB，带 `bin/`、`schemas/`、`references/` 等）。在当前代码上直接运行 loader：

```
list: ['init', 'orchestrate', 'review', 'summarize', 'archify']
resolve: archify
body: 15212 chars
allowed_tools: []
```

说明**发现机制是通的**，`archify` 并不是没被扫到。真正的问题是发现之后无法被模型使用、无法执行。

### 2.3 断点

#### P0-1：模型侧没有加载入口

`SessionManager.send_message` 只在内容以 `/` 开头时解析 Skill（`session/manager.py:100`）。系统提示里没有目录，模型也没有工具可以主动加载。

结果：用户说「帮我画一张微服务架构图」时，模型不知道 `archify` 存在；只有精确输入 `/archify` 才会触发。内置 Skill 和用户 Skill 在这一层没有区别，都只是斜杠宏。

#### P0-2：技能根目录没有传给工具

`Skill` 只有 `name` / `description` / `system_prompt_template` / `allowed_tools`（`skills/loader.py:8`），没有 `root`。而 `archify` 的指令要求：

- 读 `schemas/*.json`、`examples/*.json`、`references/*.md`；
- 执行 `node bin/archify.mjs validate ...`、`node bin/archify.mjs deliver ...`。

这些路径都相对技能目录。当前工具的行为：

- `read_file` / `list_dir` 只接受相对 cwd 的路径，绝对路径和 `..` 直接拒绝（`tools/path_guard.py:13`）；
- `bash` 继承 daemon 进程的 cwd（`tools/builtin/bash.py:49`），不会切到技能目录。

所以即使 `/archify` 被触发，模型也读不到 `C:\Users\Hugo\.desk\skills\archify\schemas\...`，更跑不了 `bin/archify.mjs`。所有带脚本/参考文件的 Skill 都会失效。

#### P0-3：注入语义错位

`SessionManager` 同时设置：

```python
goal = self._skill_loader.render_prompt(skill, arguments)
system_prompt_override = skill.system_prompt_template
```

（`session/manager.py:106`）

而 `ExecutionContext` 在 history 非空时直接用 history，忽略 `goal`（`context.py:25`）：

- 基础 system prompt 被整个替换，agent 身份、工具使用规范丢失（`context.py:32`）；
- `$ARGUMENTS` 只替换了 `goal`，`system_prompt_override` 里仍是字面量；
- `archify` 没有 `$ARGUMENTS`，用户请求只以原始 `/archify ...` 留在 history 里；
- 15 KB body 每次 run 全量进 system，重复且昂贵。

#### P1-1：发现面碎片化

- `.desk/skills` 是 CWD 相对路径（`skills/loader.py:85`），daemon 的 cwd 取决于启动目录；
- TUI 在客户端本地再建一个 loader（`tui/app.py:595`），和 core 可能看到不同集合；
- Web 没有任何 Skill 列表或补全；
- 没有 `desk skill` 管理命令。

#### P1-2：解析器脆弱且静默失败

- 手写 front-matter：任意 `- ` 行都会被当成 `allowed_tools`，与所在 key 无关；
- 不支持 `allowed-tools`、inline list、嵌套 `metadata`；
- `resolve()` 捕获所有异常返回 `None`（`skills/loader.py:76`），`list_all_skills()` 也 `except: pass`；
- 结果：Skill 写错就静默消失，用户没有任何诊断信息。

#### P1-3：没有测试

`tests/` 下没有 Skill 相关用例，上述行为均未被锁定。

## 3. 目标与非目标

### 3.1 目标

1. 用户可以把 Skill 放在任意配置目录，或放进 `~/.desk/skills` / `<项目>/.desk/skills`，无需改代码。
2. 兼容 Claude-style `name/SKILL.md` 目录布局，以及现有扁平 `name.md`。
3. 模型能通过 catalog + `use_skill` 自动发现并加载 Skill，不再依赖用户记住 `/name`。
4. Skill 的 `root` 对工具可见：能读 `references/`，能执行 `scripts/`（仍走权限审批）。
5. core 是唯一事实来源；TUI / Web / CLI 通过 RPC 获取同一份列表。
6. 解析错误、重名、非法字段有可查询的诊断，不再静默。
7. 不破坏现有 4 个内建 Skill 和 `allowed_tools` / `/name args` 用法。

### 3.2 非目标（本期不做）

- Skill 市场 / 远程仓库浏览；
- Skill 自带依赖的自动安装（`npm install`、`pip install`）；
- 在 OS 层面隔离 Skill 脚本（仍由现有权限系统约束）；
- 多版本并存与语义化版本解析；
- 自动根据关键词触发 Skill（由模型判断，避免不可控的隐式行为）。

## 4. 概念与目录规范

### 4.1 数据模型

```python
@dataclass(frozen=True)
class SkillRoot:
    path: Path
    origin: str          # "builtin" | "user" | "project" | "config"
    priority: int        # 越大越优先


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    root: Path                          # 技能目录（资源基准）
    source: Path                        # SKILL.md 或 name.md
    origin: str
    allowed_tools: tuple[str, ...] = ()
    when_to_use: str = ""
    version: str = ""
    resources: tuple[str, ...] = ()     # 顶层条目名，供 use_skill 回显


@dataclass(frozen=True)
class SkillDiagnostic:
    path: Path
    level: str          # "error" | "warning"
    message: str
```

`root` 与 `source` 是本次设计的关键新增字段：没有它们，Skill 只是一段无法落地执行的文本。

### 4.2 目录布局

推荐（也是 Claude Skills 的布局）：

```
my-skill/
├── SKILL.md              # 必需：front-matter + 指令正文
├── scripts/              # 可选：可执行脚本
├── references/           # 可选：按需读取的参考文档
├── assets/               # 可选：模板、图片等
└── ...
```

同时保留兼容：

```
~/.desk/skills/foo.md                 # 扁平文件，root = ~/.desk/skills
~/.desk/skills/foo/SKILL.md           # 目录式，root = ~/.desk/skills/foo
```

### 4.3 front-matter

```yaml
---
name: archify
description: Create polished, validated architecture/... diagrams.
when_to_use: 用户要求画架构图、流程图、时序图、数据流图时使用。
allowed-tools:
  - read_file
  - list_dir
  - bash
  - write_file
version: "2.16"
metadata:
  author: tt-a1i
---
```

规则：

- `name`：`^[a-z0-9][a-z0-9_-]{0,63}$`；缺省时用目录名/文件名，但要做同样校验。
- `description`：必填，单行摘要，建议 ≤ 1024 字符；catalog 只取首行并截断。
- `when_to_use`：可选，给模型更多判断依据；没有时用 `description`。
- `allowed-tools` / `allowed_tools`：都接受；支持 YAML list 与 inline `[read_file, bash]`。
- `version` / `metadata`：可选，仅展示与诊断，不参与解析逻辑。
- 正文上限 256 KB；资源不读入上下文，只回显顶层条目。
- 未知字段保留为 warning，不阻断加载。

### 4.4 命名冲突

同一 `name` 出现在多个 root 时，按优先级覆盖，低优先级不报错但记录 debug 日志。同一 root 内同时存在 `foo.md` 与 `foo/SKILL.md` 时，目录式优先，并产生 warning。

## 5. 发现与优先级

### 5.1 默认 roots（低 → 高）

| 优先级 | 路径 | origin | 说明 |
|---|---|---|---|
| 10 | `<package>/skills/builtin` | `builtin` | 随包分发，只读 |
| 20 | `~/.desk/skills` | `user` | 用户全局 |
| 30 | `<project_root>/.desk/skills` | `project` | 项目本地 |
| 40+ | `[skills].dirs` 中每一项 | `config` | 按配置顺序递增 |

`[skills].dirs` 允许绝对路径与 `~`；相对路径相对项目根解析。

### 5.2 项目根

不再直接用裸 cwd：

```python
def find_project_root(start: Path | None = None) -> Path:
    # 1. DESK_PROJECT_ROOT 环境变量优先
    # 2. 从 start（默认 cwd）向上找 .desk/、.git/、pyproject.toml
    # 3. 找不到则回退 start
```

core 启动时解析一次并写入日志，避免长驻进程 cwd 漂移。TUI / Web 不再自己解析，统一走 core 的 `skill.list`。

### 5.3 缓存与热更新

`SkillRegistry` 以 `(path, mtime_ns, size)` 为缓存键；`list()` 每次重新扫描目录（目录项少，成本可忽略），文件未变则复用解析结果。

- 新增/删除/修改 Skill 在下一次 `list()` / 下一次 run 生效；
- 运行中的 run 使用启动时的 catalog 快照，不在中途替换；
- `skill.reload` 提供显式清缓存入口，供调试使用。

### 5.4 诊断

`SkillRegistry.diagnostics()` 返回：

- front-matter 解析失败；
- `name` 非法、`description` 缺失；
- 同名覆盖；
- 同一 root 内重复布局；
- body 超限；
- 未识别的 `allowed_tools`（在构建工具注册表时补充）。

`desk skill doctor`、`GET /api/skills`、TUI `/skills` 都展示这份列表。

## 6. 激活机制

### 6.1 显式：`/name args`

保留现有入口，但修正语义：

1. core 从消息中提取 `skill_name` 与 `arguments`；
2. 若 Skill 存在：
   - `system_prompt_extra` 追加 `## Active Skill` 段（含 `root`、`allowed_tools`、渲染后的 body）；
   - 不再设置 `system_prompt_override`，保留基础系统提示；
   - 工具白名单与当前 registry 取交集；
   - 发布 `skill.invoked` 事件（带 `origin`、`source`）；
3. 不重写用户消息：history 中仍是原始 `/name args`，args 已在其中；`$ARGUMENTS` 在 system 段里替换为 args；
4. 若 Skill 不存在：保留原有行为（当成普通消息），但记录 debug 日志；可选地返回一条「未知 Skill」提示。

`system_prompt_extra` 示例：

```text
## Active Skill: archify
Root: C:\Users\Hugo\.desk\skills\archify
Allowed tools: read_file, list_dir, bash, write_file

<SKILL.md 正文，$ARGUMENTS 已替换>
```

不再把 body 同时塞进 `goal` 和 system，避免重复与 history 覆盖。

### 6.2 隐式：catalog + `use_skill`

每次 run 构建系统提示时，若存在 Skill，追加紧凑目录：

```text
## Available skills

If a task matches one of these skills, call `use_skill` with its name
BEFORE starting. Load only the skills you need.

- archify — Create polished, validated architecture/workflow/sequence/
  data-flow/lifecycle diagrams ... (origin: user)
- review — 对指定路径做代码审查，输出严重/建议/可选三级分类。
```

目录只包含 `name` + 首行 description，按 name 排序，最多 `max_catalog` 条（默认 30），单条描述截断到 160 字符。

`use_skill` 工具：

```python
class UseSkillParams(BaseModel):
    name: str
    arguments: str = ""

class UseSkillTool(BaseTool):
    name = "use_skill"
    description = (
        "Load the full instructions of an installed skill by name. "
        "Call this before doing a task that matches a skill in the catalog. "
        "Returns the skill body, its root directory, and its available files."
    )
```

返回格式：

```text
# Skill: archify
Root: C:\Users\Hugo\.desk\skills\archify
Allowed tools: (all)
Files: bin/, schemas/, references/, examples/, assets/
---
<渲染后的 body>
```

错误格式：

```text
Error: unknown skill "foo". Available: archify, init, review, summarize.
```

实现细节：

- `use_skill` 只读，不授予新工具；
- 同一 run 内重复调用同一 Skill 直接返回缓存结果，避免重复 token；
- 调用后把 `skill.root` 加入本 run 的路径白名单；
- 发布与显式触发相同的 `skill.invoked` 事件；
- 单次 run 最多加载 3 个 Skill，防止上下文爆炸。

### 6.3 一次典型时序

```
run 开始
  → 构建 system prompt：基础 prompt + catalog
  → 模型决定调用 use_skill("archify")
  → 工具结果：body + root + files
  → 模型读 schemas/architecture.schema.json（root 白名单允许）
  → 模型写 candidate.json 到项目目录
  → 模型 bash: node bin/archify.mjs validate ...（cwd=skill root）
  → 模型 bash: node bin/archify.mjs deliver ...（cwd=skill root）
  → 最终答复
```

### 6.4 与子 Agent 的关系

子 Agent 通过 `spawn_agent` 冷启动时，沿用父级的 `SkillRegistry` 和 catalog；子 Agent 的 `use_skill` 只影响它自己的 run 路径白名单。Skill 的 `allowed_tools` 只约束显式 `/name` 触发的 registry 构建，不跨 run 继承。

## 7. 资源访问与路径策略

### 7.1 现有约束

`require_relative_path()` 为了防路径遍历，直接拒绝：

- 绝对路径（含 Windows 盘符、UNC）；
- `~`；
- 包含 `..` 的路径。

这对项目内工具是安全的，但对「Skill 资源在另一个目录」这个场景过于严格。

### 7.2 新策略：根目录白名单

引入 `PathPolicy`：

```python
@dataclass
class PathPolicy:
    read_roots: list[Path]      # 项目 cwd + 当前 run 已激活的 skill roots
    write_roots: list[Path]     # 仅项目 cwd / workspace
```

规则：

1. 相对路径：以 daemon 进程 cwd 为基准（保持现有行为）；
2. 绝对路径 / `~`：`expanduser().resolve()` 后，必须落在某个 `read_roots` / `write_roots` 内；
3. `..`：不再一律拒绝，解析后做 containment 检查；
4. 符号链接：`resolve()` 后检查真实路径，防止 symlink 逃逸；
5. 不在白名单内：抛出 `PermissionError`，由工具转成错误结果。

工具侧：

| 工具 | 规则 |
|---|---|
| `read_file` | 允许 `read_roots` |
| `read_image` | 允许 `read_roots` |
| `list_dir` | 允许 `read_roots` |
| `write_file` | 仅允许 `write_roots`；Skill 目录默认只读 |
| `bash` | 新增可选 `cwd`，必须落在 `read_roots`；命令本身仍走权限策略 |

`PathPolicy` 由 `AgentRunner._build_registry()` 构造，传入各工具。显式 `/name` 时，`SessionManager` 把 `skill.root` 传给 runner，初始化 `read_roots` 时就加入；隐式加载时由 `UseSkillTool` 在激活后把 `skill.root` 追加到 `read_roots`。因为工具持有同一个可变策略对象，后续步骤立即可见。

### 7.3 bash 的 cwd

```python
class BashParams(BaseModel):
    command: str
    timeout: int = 60
    cwd: str | None = None      # 新增
```

- `cwd` 为空：沿用 daemon cwd；
- 相对路径：相对 daemon cwd；
- 绝对路径：必须落在 `read_roots`，否则报错；
- `create_subprocess_shell(..., cwd=str(resolved))`。

这样 `archify` 可以直接：

```json
{"command": "node bin/archify.mjs validate architecture candidate.json --quality showcase --json",
 "cwd": "C:\\Users\\Hugo\\.desk\\skills\\archify"}
```

而不需要 `cd`。`bash` 默认仍是 ASK；`cwd` 不会绕过权限。

### 7.4 与权限策略的衔接

`permissions/policy.py` 的 `OUTSIDE_CWD_HEURISTICS` 会把 `cd`、绝对路径、`..` 判为越界并强制 ASK。设计上：

- `read_file` / `list_dir` 默认 ALLOW，新增的 Skill 根目录读取不改变默认决策；
- `bash` 默认 ASK，`cwd` 只是执行目录，不改变审批；
- 若希望 Skill 内相对命令不触发 `cd` 越界规则，模型应使用 `cwd` 而不是命令里的 `cd`；
- P4 可评估把「当前激活 Skill root」加入 bash 的 allowed roots，减少审批噪音；本期不做，避免削弱现有安全边界。

## 8. 接口设计

### 8.1 Python

```python
class SkillRegistry:
    def __init__(
        self,
        roots: Sequence[SkillRoot],
        *,
        max_body_bytes: int = 256_000,
        max_catalog: int = 30,
    ) -> None: ...

    def reload(self) -> None: ...
    def list(self) -> list[Skill]: ...
    def get(self, name: str) -> Skill | None: ...
    def catalog(self, *, max_desc: int = 160) -> str: ...
    def diagnostics(self) -> list[SkillDiagnostic]: ...
    def render(self, skill: Skill, arguments: str) -> str: ...
```

`SkillLoader` 保留为兼容包装：

```python
class SkillLoader:
    """Deprecated: 内部委托给 SkillRegistry。"""
    def resolve(self, name: str) -> Skill | None: ...
    def list_all(self) -> list[str]: ...
    def list_all_skills(self) -> list[Skill]: ...
    def render_prompt(self, skill: Skill, arguments: str) -> str: ...
```

### 8.2 core 命令

`bus/commands.py` 新增：

```python
class SkillListCommand(BaseModel):
    type: Literal["skill.list"] = "skill.list"

class SkillSummary(BaseModel):
    name: str
    description: str
    origin: str
    root: str
    source: str
    allowed_tools: list[str] = []

class SkillDiagnosticInfo(BaseModel):
    path: str
    level: str
    message: str

class SkillListResult(BaseModel):
    skills: list[SkillSummary]
    diagnostics: list[SkillDiagnosticInfo] = []
    roots: list[str] = []
```

`core/app.py` 注册 `skill.list` 处理器；`scripts/gen_protocol_doc.py` 重新生成 `docs/WIRE_PROTOCOL.md`。

### 8.3 CLI

```
desk skill list [--json] [--verbose]
desk skill show <name>
desk skill new <name> [--scope user|project]
desk skill install <path> [--scope user|project] [--force]
desk skill remove <name> [--scope user|project]
desk skill doctor
```

- `list`：本地构建 registry，打印 name / origin / root / 描述；`--verbose` 显示 diagnostics；
- `show`：打印 front-matter 摘要与 body 前 N 行；
- `new`：在目标 scope 创建 `name/SKILL.md` 模板，含 `description`、`allowed-tools`、正文骨架；
- `install`：复制本地目录到 scope；校验通过才写入；`--force` 才覆盖；打印最终路径；Git 安装放到 P5；
- `remove`：只删目标 scope 下的目录，需确认；
- `doctor`：打印 roots、数量、重名、解析错误、非法工具名。

### 8.4 TUI / Web

- TUI：连接后调用 `skill.list`，用返回结果构建斜杠补全；连接失败时回退本地 registry，保证离线可用。
- Web：`GET /api/skills` 返回同一份 `SkillListResult`；聊天输入 `/` 时展示补全菜单；消息流中的 `skill.invoked` 增加 origin 标记。
- 两边都不再各自解析 front-matter。

## 9. 配置

```toml
[skills]
enabled = true
dirs = ["D:/my-skills", "~/shared/skills"]   # 额外 root，按顺序升优先级
max_catalog = 30                             # 注入目录的最大条数
```

环境变量：

| 变量 | 说明 |
|---|---|
| `DESK_SKILLS_DIRS` | 额外 root，`os.pathsep` 分隔（Windows `;`，Unix `:`） |
| `DESK_PROJECT_ROOT` | 覆盖项目根探测 |
| `DESK_SKILLS_ENABLED` | `0` / `false` 关闭 Skill 机制 |

配置解析沿用 `core/config.py` 的分层：默认值 → `~/.desk/config.toml` → `.desk/config.toml` → `.env` → 环境变量。

## 10. 安全

1. **信任模型**：Skill 正文是「用户安装的提示词代码」，安装第三方 Skill 等于信任其指令。`install` 必须展示 name、description、文件数量与来源，不自动执行安装脚本。
2. **路径**：只允许白名单根目录；`resolve()` 后做 containment 检查；拒绝 symlink 逃逸；Windows 大小写不敏感由 `Path` 处理。
3. **大小**：body ≤ 256 KB；catalog 条数与描述长度有上限；`use_skill` 单次返回可截断。
4. **工具白名单**：与当前 registry 取交集，Skill 不能凭空获得不存在的工具；未知工具名写入 diagnostics。
5. **不自动执行脚本**：`scripts/` 只能由模型通过 `bash` 调用，仍走权限审批；`use_skill` 本身只读。
6. **提示注入**：Skill 正文来自本地文件，默认视为可信；工具结果和外部网页仍按不可信处理。后续可在 `use_skill` 结果中加固定分隔符，明确「以下为 Skill 指令」。
7. **审计**：`skill.invoked` 事件记录 `name`、`arguments`、`origin`、`source`、`run_id`；trace 同步落盘。
8. **写入隔离**：Skill 目录默认不可写；只有 `write_file` 的 `write_roots`（项目 cwd / workspace）可写，防止 Skill 自我篡改。

## 11. 兼容与迁移

- 保留 `SkillLoader` 公共方法，内部委托 `SkillRegistry`；现有 import 不需要改。
- 保留扁平 `name.md`、`allowed_tools`、`system_prompt_override`（后者仅供 subagent 角色使用，Skill 不再使用）。
- 内建 4 个 Skill 原样保留，作为 `origin=builtin` 的最低优先级。
- 旧 `.desk/skills/*.md` 无需迁移；新目录式布局可直接共存。
- 客户端升级顺序：core 先支持 `skill.list`；旧 TUI/Web 继续用本地 loader 也能工作，只是看不到新 root 的 Skill。
- `docs/WIRE_PROTOCOL.md` 由脚本生成，新增命令后必须重新生成并校验。

## 12. 测试计划

### 12.1 单元测试

`tests/unit/test_skill_registry.py`：

- 多级 root 的覆盖顺序；
- 扁平文件与 `name/SKILL.md`；
- `description: >` / `|` 块标量；
- `allowed-tools` 与 `allowed_tools`、inline list；
- 非法 name、缺 description、超限 body；
- 同名覆盖与同 root 重复布局的 diagnostics；
- 缓存命中与 mtime 失效；
- catalog 排序、截断、空列表。

`tests/unit/test_skill_tool.py`：

- `use_skill` 返回 body/root/files；
- 未知 Skill 的错误信息包含可用列表；
- 重复调用去重；
- `skill.invoked` 事件字段；
- 单 run 最多加载 3 个。

`tests/unit/test_skill_paths.py`：

- Skill root 内绝对路径允许；
- 项目 cwd 内相对路径允许；
- 白名单外绝对路径拒绝；
- `..` 逃逸拒绝；
- symlink 逃逸拒绝；
- `write_file` 不能写 Skill root。

`tests/unit/test_skill_session.py`：

- `/name args` 注入 `system_prompt_extra`，且 `system_prompt_override` 为空；
- 基础 system prompt 仍然存在；
- `allowed_tools` 与 registry 交集；
- history 中保留原始 `/name args`。

`tests/unit/test_skill_api.py`：

- `skill.list` 返回 skills/diagnostics/roots；
- 未启用时返回空列表。

### 12.2 手动冒烟

1. 在 `~/.desk/skills/archify` 已存在的前提下启动 core；
2. `desk skill list` 能看到 `archify`、origin=user、root 正确；
3. `desk-tui` 输入 `/archify` 能补全；
4. 新建会话输入「画一张微服务架构图」，模型应调用 `use_skill("archify")`；
5. 模型能读 `schemas/architecture.schema.json`；
6. 模型能以 `cwd=skill root` 执行 `node bin/archify.mjs validate ...`；
7. 产出 HTML 写入项目 `workspace/`，`skill.invoked` 事件可在 trace 中看到。

## 13. 分阶段落地

| 阶段 | 内容 | 预计 | 验收 |
|---|---|---|---|
| P1 | `SkillRegistry`、`Skill.root`、多 root、项目根、diagnostics、配置项 | 0.5–1 天 | `desk skill list --verbose` 能看到任意目录的 Skill，错误可见 |
| P2 | catalog 注入、`use_skill`、`/name` 语义修正、`skill.invoked` | 0.5–1 天 | 自然语言任务能自动加载 Skill，基础 prompt 不丢失 |
| P3 | `PathPolicy`、绝对路径白名单、bash `cwd` | 0.5 天 | `archify` 能读资源、跑脚本 |
| P4 | `skill.list` RPC、`desk skill` CLI、TUI/Web 列表、install/doctor | 0.5–1 天 | 三端列表一致，可安装/诊断 |
| P5 | Git 安装、版本、更新提示、自建 Skill 模板 | 1–2 天 | 可分享、可升级 |

最小可用闭环是 **P1 + P2 + P3**；P4 是体验与管理面；P5 是生态能力。

## 14. 开放问题

| 问题 | 当前建议 |
|---|---|
| 相对路径基准用 daemon cwd 还是项目根？ | 保持 cwd，避免行为突变；项目根只用于发现与上下文，后续再统一 |
| 是否自动读取 `.claude/skills`？ | 不做默认；通过 `[skills].dirs` 显式加入，避免隐式信任 |
| 是否引入 PyYAML？ | 先用现有 mini parser 补 key/inline list；需要完整 YAML 时再评估 |
| `use_skill` 是否动态强制 `allowed_tools`？ | 本期只对显式 `/name` 强制；隐式加载先做 advisory，P4 再评估 |
| Skill 脚本是否需要独立沙箱？ | 本期不做；沿用 bash 权限审批与路径策略 |
| 是否支持 Skill 自带依赖安装？ | 不做；由 Skill 文档说明前置条件，模型在权限审批下执行 |
