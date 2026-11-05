from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path


_SSH_USER_RE = re.compile(r"^[A-Za-z0-9_.@+-]{1,128}$")
_HOST_KEY_ALIAS_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")


class WindowsCommandError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ExecutorConfig:
    ssh_host: str
    ssh_port: int
    ssh_user: str
    identity_file: str
    known_hosts_file: str
    host_key_alias: str = "feiyu-windows"
    ssh_executable: str = "ssh"
    powershell_executable: str = "powershell.exe"
    connect_timeout_seconds: int = 10
    default_timeout_seconds: int = 30
    max_timeout_seconds: int = 120
    max_command_chars: int = 12_000
    max_output_bytes: int = 65_536

    def validate(self, *, check_files: bool = True) -> None:
        host = self.ssh_host.strip()
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise WindowsCommandError(
                "CONFIG_ERROR", "ssh_host 必须是 loopback IP 地址"
            ) from exc
        if not address.is_loopback:
            raise WindowsCommandError(
                "CONFIG_ERROR", "ssh_host 只允许 loopback；请连接 SSH 反向隧道监听端口"
            )
        if not 1 <= self.ssh_port <= 65_535:
            raise WindowsCommandError("CONFIG_ERROR", "ssh_port 必须在 1-65535 之间")
        if not _SSH_USER_RE.fullmatch(self.ssh_user.strip()):
            raise WindowsCommandError("CONFIG_ERROR", "ssh_user 为空或含不安全字符")
        if not _HOST_KEY_ALIAS_RE.fullmatch(self.host_key_alias.strip()):
            raise WindowsCommandError("CONFIG_ERROR", "host_key_alias 为空或含不安全字符")
        if self.powershell_executable not in {"powershell.exe", "pwsh.exe"}:
            raise WindowsCommandError(
                "CONFIG_ERROR", "powershell_executable 只允许 powershell.exe 或 pwsh.exe"
            )
        if not self.ssh_executable.strip():
            raise WindowsCommandError("CONFIG_ERROR", "ssh_executable 不能为空")
        self._validate_absolute_file("identity_file", self.identity_file, check_files)
        self._validate_absolute_file(
            "known_hosts_file", self.known_hosts_file, check_files
        )
        if not 1 <= self.connect_timeout_seconds <= 60:
            raise WindowsCommandError(
                "CONFIG_ERROR", "connect_timeout_seconds 必须在 1-60 之间"
            )
        if not 1 <= self.default_timeout_seconds <= self.max_timeout_seconds <= 900:
            raise WindowsCommandError(
                "CONFIG_ERROR", "执行超时必须满足 1 <= default <= max <= 900"
            )
        if not 100 <= self.max_command_chars <= 50_000:
            raise WindowsCommandError(
                "CONFIG_ERROR", "max_command_chars 必须在 100-50000 之间"
            )
        if not 1_024 <= self.max_output_bytes <= 1_048_576:
            raise WindowsCommandError(
                "CONFIG_ERROR", "max_output_bytes 必须在 1024-1048576 之间"
            )

    @staticmethod
    def _validate_absolute_file(name: str, value: str, check_files: bool) -> None:
        path = Path(value).expanduser()
        if not value.strip() or not path.is_absolute():
            raise WindowsCommandError("CONFIG_ERROR", f"{name} 必须是绝对路径")
        if check_files and (not path.is_file() or not os.access(path, os.R_OK)):
            raise WindowsCommandError("CONFIG_ERROR", f"{name} 不存在或不可读")


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


class WindowsPowerShellExecutor:
    def __init__(self, config: ExecutorConfig):
        self.config = config
        self._lock = asyncio.Lock()

    async def execute(
        self, command: str, timeout_seconds: int | None = None
    ) -> ExecutionResult:
        self.config.validate()
        if not isinstance(command, str) or not command.strip() or "\x00" in command:
            raise WindowsCommandError(
                "INVALID_REQUEST", "command 必须是非空字符串且不能包含 NUL"
            )
        if len(command) > self.config.max_command_chars:
            raise WindowsCommandError(
                "INVALID_REQUEST",
                f"command 超过 {self.config.max_command_chars} 字符限制",
            )
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int)
        ):
            raise WindowsCommandError("INVALID_REQUEST", "timeout_seconds 必须是整数")
        timeout = (
            self.config.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not 1 <= timeout <= self.config.max_timeout_seconds:
            raise WindowsCommandError(
                "INVALID_REQUEST",
                f"timeout_seconds 必须在 1-{self.config.max_timeout_seconds} 之间",
            )

        remote_command = self._encode_remote_command(command)
        argv = self._ssh_argv(remote_command)
        async with self._lock:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, PermissionError, OSError) as exc:
                raise WindowsCommandError(
                    "SSH_START_FAILED", f"无法启动 SSH 客户端: {exc}"
                ) from exc
            stdout_task = asyncio.create_task(self._read_limited(process.stdout))
            stderr_task = asyncio.create_task(self._read_limited(process.stderr))
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
                raise WindowsCommandError(
                    "COMMAND_TIMEOUT",
                    f"PowerShell 执行超过 {timeout} 秒，SSH 会话已终止",
                    retryable=True,
                ) from exc

            (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
                stdout_task, stderr_task
            )
        return ExecutionResult(
            exit_code=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def _ssh_argv(self, remote_command: str) -> list[str]:
        config = self.config
        return [
            config.ssh_executable,
            "-T",
            "-p",
            str(config.ssh_port),
            "-i",
            str(Path(config.identity_file).expanduser()),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={Path(config.known_hosts_file).expanduser()}",
            "-o",
            f"HostKeyAlias={config.host_key_alias}",
            "-o",
            f"ConnectTimeout={config.connect_timeout_seconds}",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "LogLevel=ERROR",
            "--",
            f"{config.ssh_user}@{config.ssh_host}",
            remote_command,
        ]

    def _encode_remote_command(self, command: str) -> str:
        prefix = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
            "$OutputEncoding=[Console]::OutputEncoding;"
        )
        encoded = base64.b64encode((prefix + command).encode("utf-16-le")).decode(
            "ascii"
        )
        return (
            f"{self.config.powershell_executable} -NoLogo -NoProfile "
            f"-NonInteractive -EncodedCommand {encoded}"
        )

    async def _read_limited(
        self, stream: asyncio.StreamReader | None
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        retained = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            remaining = self.config.max_output_bytes - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return bytes(retained), truncated
