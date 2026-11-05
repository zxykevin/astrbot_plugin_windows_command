from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, StarTools

from .windows_command import (
    ExecutorConfig,
    SafeWindowsActions,
    WindowsCommandError,
    WindowsPowerShellExecutor,
)
from .windows_command.safe_tool import WindowsSafeActionTool
from .windows_command.tool import WindowsPowerShellTool
from .windows_command.autonomous import AutonomousWindowsManager, AutonomousWindowsPolicy


class WindowsCommandPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        executor_config = ExecutorConfig(
            ssh_host=str(config.get("ssh_host", "127.0.0.1")).strip(),
            ssh_port=int(config.get("ssh_port", 2222)),
            ssh_user=str(config.get("ssh_user", "")).strip(),
            identity_file=str(config.get("identity_file", "")).strip(),
            known_hosts_file=str(config.get("known_hosts_file", "")).strip(),
            host_key_alias=str(config.get("host_key_alias", "feiyu-windows")).strip(),
            ssh_executable=str(config.get("ssh_executable", "ssh")).strip(),
            powershell_executable=str(
                config.get("powershell_executable", "powershell.exe")
            ).strip(),
            connect_timeout_seconds=int(config.get("connect_timeout_seconds", 10)),
            default_timeout_seconds=int(config.get("default_timeout_seconds", 30)),
            max_timeout_seconds=int(config.get("max_timeout_seconds", 120)),
            max_command_chars=int(config.get("max_command_chars", 12000)),
            max_output_bytes=int(config.get("max_output_bytes", 65536)),
        )
        self.executor = WindowsPowerShellExecutor(executor_config)
        execution_enabled = bool(config.get("execution_enabled", False))
        authorized_sender_ids = frozenset(
            str(item).strip()
            for item in config.get("authorized_sender_ids", [])
            if str(item).strip()
        )
        self.safe_actions = SafeWindowsActions(self.executor)
        self.context.add_llm_tools(
            WindowsPowerShellTool(
                executor=self.executor,
                execution_enabled=execution_enabled,
                authorized_sender_ids=authorized_sender_ids,
            ),
            WindowsSafeActionTool(
                actions=self.safe_actions,
                execution_enabled=execution_enabled,
                authorized_sender_ids=authorized_sender_ids,
                allow_shutdown=bool(config.get("safe_action_allow_shutdown", False)),
            ),
        )
        shutdown_allowed = bool(config.get("safe_action_allow_shutdown", False))
        self.autonomous = AutonomousWindowsManager(
            actions=self.safe_actions,
            policy=AutonomousWindowsPolicy(
                enabled=execution_enabled and bool(config.get("autonomous_enabled", False)),
                companion_db_path=str(config.get("autonomous_companion_db_path", "")).strip(),
                provider_id=str(config.get("autonomous_provider_id", "")).strip(),
                timezone=str(config.get("autonomous_timezone", "Europe/Berlin")).strip(),
                poll_seconds=int(config.get("autonomous_poll_seconds", 60)),
                unanswered_minutes=int(config.get("autonomous_unanswered_minutes", 15)),
                daily_notification_limit=int(config.get("autonomous_daily_notification_limit", 8)),
                daily_note_limit=int(config.get("autonomous_daily_note_limit", 2)),
                note_classifier_enabled=bool(config.get("autonomous_note_classifier_enabled", True)),
                bedtime_enabled=bool(config.get("autonomous_bedtime_enabled", False)),
                bedtime_start_hour=int(config.get("autonomous_bedtime_start_hour", 0)),
                bedtime_end_hour=int(config.get("autonomous_bedtime_end_hour", 7)),
                active_minutes_before_warning=int(config.get("autonomous_active_minutes_before_warning", 180)),
                idle_reset_minutes=int(config.get("autonomous_idle_reset_minutes", 20)),
                shutdown_enabled=shutdown_allowed and bool(config.get("autonomous_shutdown_enabled", False)),
                shutdown_after_warning_minutes=int(config.get("autonomous_shutdown_after_warning_minutes", 30)),
                shutdown_delay_seconds=int(config.get("autonomous_shutdown_delay_seconds", 600)),
            ),
            state_path=StarTools.get_data_dir("astrbot_plugin_windows_command") / "autonomous_state.json",
            decision_call=self._autonomous_decision,
        )
        self.autonomous.start()

        try:
            executor_config.validate(check_files=execution_enabled)
        except WindowsCommandError as exc:
            if execution_enabled:
                logger.error("Windows Command 配置无效，执行请求将失败: %s", exc)
            else:
                logger.warning("Windows Command 尚未完成配置: %s", exc)
        if not execution_enabled:
            logger.warning("Windows Command execution_enabled=false；远程执行已禁用")
        if not authorized_sender_ids:
            logger.warning("Windows Command authorized_sender_ids 为空；所有执行请求都会被拒绝")
        logger.info("Windows Command v0.2.0 initialized; fixed safe actions registered")

    async def _autonomous_decision(self, prompt: str) -> str:
        provider_id = self.autonomous.policy.provider_id
        if not provider_id:
            raise RuntimeError("autonomous provider is not configured")
        provider = await self.context.provider_manager.get_provider_by_id(provider_id)
        if provider is None:
            raise RuntimeError("autonomous provider was not found")
        response = await provider.text_chat(prompt=prompt)
        for name in ("completion_text", "text", "content"):
            value = getattr(response, name, None)
            if isinstance(value, str) and value.strip():
                return value
        if isinstance(response, str):
            return response
        raise RuntimeError("autonomous provider returned no text")

    async def terminate(self):
        await self.autonomous.stop()
        logger.info("Windows Command terminated")
