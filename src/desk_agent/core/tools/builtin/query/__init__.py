"""统一本地查询工具包：system_query（monitor / port / software 三个实现类别）。

- tool.SystemQueryTool      —— 统一工具入口（按 category 分发）
- monitor / port / software —— 各类别实现（只提供纯函数 + 参数模型，不定义 BaseTool）
"""

from desk_agent.core.tools.builtin.query.tool import SystemQueryParams, SystemQueryTool

__all__ = ["SystemQueryParams", "SystemQueryTool"]
