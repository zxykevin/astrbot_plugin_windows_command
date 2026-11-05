from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star

from .windows_command import ExecutorConfig, WindowsCommandError, WindowsPowerShellExecutor
from .windows_command.tool import WindowsPowerShellTool


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
        self.context.add_llm_tools(
            WindowsPowerShellTool(
                executor=self.executor,
                execution_enabled=execution_enabled,
                authorized_sender_ids=authorized_sender_ids,
            )
        )

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
        logger.info("Windows Command v0.1.0 initialized")

    async def terminate(self):
        logger.info("Windows Command terminated")
