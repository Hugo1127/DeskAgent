"""TUI 权限审批控件测试：鼠标点击、键盘快捷键、强制屏障只允许单次决策。"""
from __future__ import annotations

from textual.app import App, ComposeResult

from desk_agent.tui.app import PermissionChoice, PermissionSelect


class _Harness(App[None]):
    """只挂载一个审批控件的最小 App，记录宿主收到的决策消息。"""

    def __init__(self, *, tier: str = "tier4_default", forced: bool = False) -> None:
        super().__init__()
        self._tier = tier
        self._forced = forced
        self.decisions: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield PermissionSelect("toolu_1", tier=self._tier, forced=self._forced)

    def on_permission_select_decided(self, msg: PermissionSelect.Decided) -> None:
        self.decisions.append((msg.tool_use_id, msg.decision))


async def test_click_choice_posts_decision() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        choices = list(app.query(PermissionChoice))
        assert [c.decision for c in choices] == [
            "allow_once",
            "session_allow",
            "always_allow_global",
            "deny_once",
            "session_deny",
            "always_deny_global",
        ]
        # 点击第二行「Allow for session」应等价于键盘选择
        await pilot.click(choices[1])
        await pilot.pause()
        assert app.decisions == [("toolu_1", "session_allow")]


async def test_keyboard_shortcut_posts_decision() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert app.decisions == [("toolu_1", "session_deny")]


async def test_forced_only_exposes_once_choices() -> None:
    app = _Harness(tier="tier1_deny_pattern", forced=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        choices = list(app.query(PermissionChoice))
        assert [c.decision for c in choices] == ["allow_once", "deny_once"]

        # 记忆型快捷键在屏障下被忽略，不会产生决策
        await pilot.press("2")
        await pilot.pause()
        assert app.decisions == []

        # 点击「Deny」仍可正常拒绝
        await pilot.click(choices[1])
        await pilot.pause()
        assert app.decisions == [("toolu_1", "deny_once")]


async def test_enter_uses_highlighted_row() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.decisions == [("toolu_1", "session_allow")]
