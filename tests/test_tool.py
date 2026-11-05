from __future__ import annotations

import dataclasses
import json
import sys
import types
import unittest
from types import SimpleNamespace


def _install_astrbot_stubs() -> None:
    names = (
        "astrbot",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.run_context",
        "astrbot.core.agent.tool",
        "astrbot.core.astr_agent_context",
    )
    modules = {name: sys.modules.get(name) or types.ModuleType(name) for name in names}

    class GenericBase:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    modules["astrbot.core.agent.run_context"].ContextWrapper = GenericBase
    modules["astrbot.core.agent.tool"].FunctionTool = GenericBase
    modules["astrbot.core.agent.tool"].ToolExecResult = str
    modules["astrbot.core.astr_agent_context"].AstrAgentContext = object
    sys.modules.update(modules)
    if "pydantic.dataclasses" not in sys.modules:
        pydantic_module = sys.modules.get("pydantic") or types.ModuleType("pydantic")
        pydantic_dataclasses = types.ModuleType("pydantic.dataclasses")
        pydantic_dataclasses.dataclass = dataclasses.dataclass
        pydantic_module.dataclasses = pydantic_dataclasses
        sys.modules["pydantic"] = pydantic_module
        sys.modules["pydantic.dataclasses"] = pydantic_dataclasses


_install_astrbot_stubs()

from windows_command.executor import ExecutionResult
from windows_command.tool import WindowsPowerShellTool


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, command, timeout_seconds=None):
        self.calls.append((command, timeout_seconds))
        return ExecutionResult(0, "ok\n", "", False, False)


class RealEvent:
    def __init__(self, sender_id="10001"):
        self.sender_id = sender_id
        self.unified_msg_origin = f"napcat:FriendMessage:{sender_id}"
        self.message_str = "请在电脑上执行命令"
        self.message_obj = SimpleNamespace(
            message_id="message-1", raw_message={"post_type": "message"}
        )

    def get_sender_id(self):
        return self.sender_id

    def get_platform_name(self):
        return "aiocqhttp"


RealEvent.__module__ = "astrbot.core.platform.sources.aiocqhttp.message_event"


def context_for(event):
    return SimpleNamespace(context=SimpleNamespace(event=event))


class WindowsPowerShellToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = RecordingExecutor()

    def make_tool(self, *, enabled=True):
        return WindowsPowerShellTool(
            executor=self.executor,
            execution_enabled=enabled,
            authorized_sender_ids=frozenset({"10001"}),
        )

    async def test_authorized_real_event_executes(self):
        result = json.loads(
            await self.make_tool().call(
                context_for(RealEvent()), "Get-Date", timeout_seconds=10
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.executor.calls, [("Get-Date", 10)])

    async def test_disabled_fails_closed(self):
        result = json.loads(
            await self.make_tool(enabled=False).call(
                context_for(RealEvent()), "Get-Date"
            )
        )
        self.assertEqual(result["error"]["code"], "EXECUTION_DISABLED")
        self.assertEqual(self.executor.calls, [])

    async def test_unauthorized_sender_does_not_execute(self):
        result = json.loads(
            await self.make_tool().call(context_for(RealEvent("99999")), "Get-Date")
        )
        self.assertEqual(result["error"]["code"], "SENDER_NOT_AUTHORIZED")
        self.assertEqual(self.executor.calls, [])

    async def test_synthetic_event_does_not_execute(self):
        event = RealEvent()
        event.__class__ = type("SyntheticEvent", (RealEvent,), {})
        result = json.loads(await self.make_tool().call(context_for(event), "Get-Date"))
        self.assertEqual(result["error"]["code"], "DIRECT_USER_REQUIRED")
        self.assertEqual(self.executor.calls, [])


if __name__ == "__main__":
    unittest.main()
