from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_POLICY_PATH = Path("~/.desk/policy.toml")

_VALID_DECISIONS = ("allow", "deny")


# 加载 policy.toml 中 [always] 节，返回 {tool_name: "allow"/"deny"}；
# 文件不存在、非法 TOML 或读取出错时返回空字典（记 WARNING，不阻断启动）
def load_policy_file(path: Path | None = None) -> dict[str, str]:
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning(
            "policy: failed to parse %s (%s); ignoring [always] entries", p, exc
        )
        return {}

    always_raw: Any = data.get("always", {})
    if not isinstance(always_raw, dict):
        logger.warning("policy: [always] must be a table in %s; ignoring", p)
        return {}

    result: dict[str, str] = {}
    for tool, decision in always_raw.items():
        if isinstance(tool, str) and isinstance(decision, str) and decision in _VALID_DECISIONS:
            result[tool] = decision
        else:
            logger.warning(
                "policy: ignoring invalid [always] entry %r = %r in %s",
                tool, decision, p,
            )
    return result


# 将 {tool_name: "allow"/"deny"} 写入 policy.toml，覆盖 [always] 节
def save_policy_file(always: dict[str, str], path: Path | None = None) -> None:
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ~/.desk/policy.toml",
        "# 由 desk-core 自动管理，手动编辑生效但格式须正确",
        "",
        "[always]",
    ]
    for tool, decision in sorted(always.items()):
        lines.append(f'{tool} = "{decision}"')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
