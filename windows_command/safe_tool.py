from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .executor import WindowsCommandError
from .safe_actions import SafeWindowsActions
from .tool import WindowsPowerShellTool


@dataclass
class WindowsSafeActionTool(FunctionTool[AstrAgentContext]):
    __pydantic_config__ = {"arbitrary_types_allowed": True}

    actions: SafeWindowsActions = field(default=None)
    execution_enabled: bool = False
    authorized_sender_ids: frozenset[str] = field(default_factory=frozenset)
    allow_shutdown: bool = False
    name: str = "windows_safe_action"
    description: str = (
        "控制肥鱼的受信任 Windows 电脑，只提供固定的安全动作：读取状态、通知、"
        "写桌面信、媒体键和受控关机。只有主人真实明确提出时才能调用；"
        "不能用它删除、下载、安装或启动任意程序。"
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "notify",
                        "write_desktop_note",
                        "media_play_pause",
                        "media_next",
                        "media_previous",
                        "schedule_shutdown",
                        "cancel_shutdown",
                    ],
                },
                "title": {"type": "string", "maxLength": 80},
                "text": {"type": "string", "maxLength": 4000},
                "shutdown_delay_seconds": {
                    "type": "integer",
                    "minimum": 60,
                    "maximum": 3600,
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        action: str,
        title: str = "",
        text: str = "",
        shutdown_delay_seconds: int = 600,
    ) -> ToolExecResult:
        if not self.execution_enabled:
            return self._error("EXECUTION_DISABLED", "Windows 安全动作未启用")
        event = WindowsPowerShellTool._event(context)
        sender_id = WindowsPowerShellTool._event_value(event, "get_sender_id", "sender_id")
        if not WindowsPowerShellTool._is_real_user_event(event):
            return self._error("DIRECT_USER_REQUIRED", "普通工具只允许真实平台用户触发")
        if sender_id not in self.authorized_sender_ids:
            return self._error("SENDER_NOT_AUTHORIZED", "当前用户无权控制 Windows 电脑")
        if action in {"schedule_shutdown", "cancel_shutdown"} and not self.allow_shutdown:
            return self._error("SHUTDOWN_DISABLED", "受控关机功能尚未启用")
        try:
            result = await self.actions.execute(
                action,
                title=title,
                text=text,
                shutdown_delay_seconds=shutdown_delay_seconds,
            )
            return json.dumps(result.payload(), ensure_ascii=False, separators=(",", ":"))
        except WindowsCommandError as exc:
            return self._error(exc.code, str(exc), retryable=exc.retryable)
        except Exception:
            return self._error("INTERNAL_ERROR", "Windows 安全动作执行失败，请检查日志")

    @staticmethod
    def _error(code: str, message: str, *, retryable: bool = False) -> str:
        return json.dumps(
            {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
