from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from windows_command.executor import (
    ExecutorConfig,
    WindowsCommandError,
    WindowsPowerShellExecutor,
)


class ExecutorConfigTests(unittest.TestCase):
    def make_config(self, directory: str, **overrides) -> ExecutorConfig:
        identity = Path(directory) / "id_ed25519"
        known_hosts = Path(directory) / "known_hosts"
        identity.write_text("test", encoding="utf-8")
        known_hosts.write_text("feiyu-windows ssh-ed25519 test", encoding="utf-8")
        values = {
            "ssh_host": "127.0.0.1",
            "ssh_port": 2222,
            "ssh_user": "windows-user",
            "identity_file": str(identity),
            "known_hosts_file": str(known_hosts),
        }
        values.update(overrides)
        return ExecutorConfig(**values)

    def test_accepts_loopback_and_existing_key_files(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_config(directory).validate()

    def test_rejects_non_loopback_host(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory, ssh_host="192.0.2.10")
            with self.assertRaisesRegex(WindowsCommandError, "loopback"):
                config.validate()

    def test_rejects_unsafe_user(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory, ssh_user="user name")
            with self.assertRaisesRegex(WindowsCommandError, "ssh_user"):
                config.validate()


class EncodingTests(unittest.TestCase):
    def test_command_is_utf16le_encoded_without_shell_metacharacters(self):
        config = ExecutorConfig(
            ssh_host="::1",
            ssh_port=2222,
            ssh_user="user",
            identity_file="/tmp/key",
            known_hosts_file="/tmp/known_hosts",
        )
        executor = WindowsPowerShellExecutor(config)
        remote = executor._encode_remote_command("Write-Output '你好 & test'")
        encoded = remote.rsplit(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-16-le")
        self.assertTrue(decoded.endswith("Write-Output '你好 & test'"))
        self.assertNotIn("你好", remote)
        self.assertNotIn("&", remote)

    def test_ssh_arguments_force_key_and_host_checking(self):
        config = ExecutorConfig(
            ssh_host="127.0.0.1",
            ssh_port=2222,
            ssh_user="user",
            identity_file="/tmp/key",
            known_hosts_file="/tmp/known_hosts",
        )
        argv = WindowsPowerShellExecutor(config)._ssh_argv("powershell encoded")
        joined = " ".join(argv)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("HostKeyAlias=feiyu-windows", joined)
        self.assertNotIn("StrictHostKeyChecking=no", joined)


if __name__ == "__main__":
    unittest.main()
