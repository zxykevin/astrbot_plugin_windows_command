from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from .executor import ExecutionResult, WindowsCommandError, WindowsPowerShellExecutor


SAFE_ACTIONS = frozenset(
    {
        "status",
        "notify",
        "write_desktop_note",
        "media_play_pause",
        "media_next",
        "media_previous",
        "schedule_shutdown",
        "cancel_shutdown",
    }
)


def _encoded_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode_expression(value: str) -> str:
    return (
        "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + _encoded_text(value)
        + "'))"
    )


@dataclass(frozen=True)
class SafeActionResult:
    action: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def payload(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "ok": self.ok,
            "action": self.action,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }
        if self.action == "status" and self.ok:
            try:
                value["status"] = json.loads(self.stdout.strip())
            except (TypeError, ValueError, json.JSONDecodeError):
                value["ok"] = False
                value["error"] = {"code": "INVALID_STATUS_RESULT"}
        return value


class SafeWindowsActions:
    """Fixed Windows effects for autonomous use; never accepts source code or paths."""

    def __init__(self, executor: WindowsPowerShellExecutor):
        self.executor = executor

    async def execute(
        self,
        action: str,
        *,
        title: str = "",
        text: str = "",
        shutdown_delay_seconds: int = 600,
    ) -> SafeActionResult:
        if action not in SAFE_ACTIONS:
            raise WindowsCommandError("ACTION_DENIED", "不允许的 Windows 安全动作")
        if not isinstance(title, str) or len(title) > 80 or "\x00" in title:
            raise WindowsCommandError("INVALID_REQUEST", "通知标题不能超过 80 个字符")
        if not isinstance(text, str) or len(text) > 4000 or "\x00" in text:
            raise WindowsCommandError("INVALID_REQUEST", "文本不能超过 4000 个字符")
        if action in {"notify", "write_desktop_note"} and not text.strip():
            raise WindowsCommandError("INVALID_REQUEST", "通知或桌面信需要正文")
        if (
            isinstance(shutdown_delay_seconds, bool)
            or not isinstance(shutdown_delay_seconds, int)
            or not 60 <= shutdown_delay_seconds <= 3600
        ):
            raise WindowsCommandError("INVALID_REQUEST", "关机倒计时必须在 60-3600 秒之间")

        script = self._script(
            action,
            title=title.strip() or "大肥鱼",
            text=text.strip(),
            shutdown_delay_seconds=shutdown_delay_seconds,
        )
        result: ExecutionResult = await self.executor.execute(script)
        return SafeActionResult(
            action,
            result.exit_code,
            result.stdout,
            result.stderr,
            result.stdout_truncated,
            result.stderr_truncated,
        )

    @staticmethod
    def _script(action: str, *, title: str, text: str, shutdown_delay_seconds: int) -> str:
        if action == "status":
            return r"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FeiyuInputState {
  [StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
  public static uint IdleSeconds() { var x = new LASTINPUTINFO(); x.cbSize=(uint)Marshal.SizeOf(x); return GetLastInputInfo(ref x) ? ((uint)Environment.TickCount-x.dwTime)/1000 : 0; }
}
'@
$os = Get-CimInstance Win32_OperatingSystem
$battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
[ordered]@{
  observed_at=(Get-Date).ToString('o')
  computer_name=$env:COMPUTERNAME
  uptime_seconds=[int]((Get-Date)-$os.LastBootUpTime).TotalSeconds
  idle_seconds=[int][FeiyuInputState]::IdleSeconds()
  battery_percent=if($battery){[int]$battery.EstimatedChargeRemaining}else{$null}
  power_online=if($battery){$battery.BatteryStatus -in 2,6,7,8,9}else{$true}
} | ConvertTo-Json -Compress
""".strip()

        if action == "notify":
            title_expr, text_expr = _decode_expression(title), _decode_expression(text)
            return f"""
$title = {title_expr}
$body = {text_expr}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] > $null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $xml.GetElementsByTagName('text')
$null = $nodes.Item(0).AppendChild($xml.CreateTextNode($title))
$null = $nodes.Item(1).AppendChild($xml.CreateTextNode($body))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Windows PowerShell').Show($toast)
[ordered]@{{notified=$true}} | ConvertTo-Json -Compress
""".strip()

        if action == "write_desktop_note":
            text_expr = _decode_expression(text)
            return f"""
$body = {text_expr}
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if([string]::IsNullOrWhiteSpace($desktop)){{ throw 'Desktop directory is unavailable' }}
$name = '大肥鱼给你的信-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([Guid]::NewGuid().ToString('N').Substring(0,6)) + '.txt'
$path = [IO.Path]::Combine($desktop, $name)
[IO.File]::WriteAllText($path, $body, [Text.UTF8Encoding]::new($false))
[ordered]@{{written=$true;file_name=$name;bytes=[Text.Encoding]::UTF8.GetByteCount($body)}} | ConvertTo-Json -Compress
""".strip()

        if action.startswith("media_"):
            key = {"media_play_pause": 0xB3, "media_next": 0xB0, "media_previous": 0xB1}[action]
            return f"""
Add-Type @'
using System.Runtime.InteropServices;
public static class FeiyuMediaKey {{
  [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, uint extra);
}}
'@
[FeiyuMediaKey]::keybd_event([byte]{key},0,0,0)
[FeiyuMediaKey]::keybd_event([byte]{key},0,2,0)
[ordered]@{{media_key={key}}} | ConvertTo-Json -Compress
""".strip()

        if action == "schedule_shutdown":
            return (
                f"shutdown.exe /s /t {shutdown_delay_seconds} /d p:0:0 "
                '/c "大肥鱼提醒你休息：如需继续使用，请运行 shutdown /a 取消。"; '
                f"[ordered]@{{scheduled=$true;delay_seconds={shutdown_delay_seconds}}} | ConvertTo-Json -Compress"
            )
        if action == "cancel_shutdown":
            return (
                "shutdown.exe /a; $cancelled=($LASTEXITCODE -eq 0); "
                "[ordered]@{cancelled=$cancelled} | ConvertTo-Json -Compress"
            )
        raise AssertionError("unreachable safe action")
