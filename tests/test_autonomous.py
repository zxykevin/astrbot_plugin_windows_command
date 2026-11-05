from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from windows_command.autonomous import AutonomousWindowsManager, AutonomousWindowsPolicy
from windows_command.safe_actions import SafeActionResult


NOW = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc).timestamp()


class RecordingActions:
    def __init__(self, idle_seconds=0):
        self.calls = []
        self.idle_seconds = idle_seconds

    async def execute(self, action, **arguments):
        self.calls.append((action, arguments))
        stdout = json.dumps({"idle_seconds": self.idle_seconds}) if action == "status" else "{}"
        return SafeActionResult(action, 0, stdout, "", False, False)


def create_database(path: Path, *, replied: bool) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE proactive_contacts (
          contact_id TEXT, task_id TEXT, origin_key TEXT, record_json TEXT,
          body TEXT, payload_hash TEXT, operation_id TEXT, created_at TEXT
        );
        CREATE TABLE background_deliveries (
          delivery_id TEXT, owner TEXT, origin_key TEXT, request_json TEXT,
          payload_hash TEXT, sender_json TEXT, task_id TEXT, state TEXT,
          operation_id TEXT, dispatched_at TEXT, receipt_ref TEXT,
          platform_ref TEXT, delivered_at TEXT, reason TEXT
        );
        CREATE TABLE conversation_batches (
          batch_id TEXT, origin_key TEXT, context_json TEXT, role_instruction TEXT,
          state TEXT, revision INTEGER, created_at TEXT, generation_task_id TEXT,
          contributions_json TEXT, call_id TEXT, manifest_hash TEXT,
          part_count INTEGER, presentation_action TEXT
        );
        CREATE TABLE conversation_inputs (
          event_key TEXT, batch_id TEXT, position INTEGER, event_json TEXT,
          event_hash TEXT, withdrawn INTEGER
        );
        """
    )
    sent = "2026-11-05T22:00:00+00:00"
    db.execute("INSERT INTO proactive_contacts VALUES (?,?,?,?,?,?,?,?)",
               ("c1", "t1", "owner-private", "{}", "你怎么还没理我呀", "h", "o", sent))
    db.execute("INSERT INTO background_deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("d1", "x", "owner-private", "{}", "h", "{}", "t1", "delivered",
                "o", sent, "r", "p", sent, None))
    if replied:
        db.execute("INSERT INTO conversation_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("b1", "owner-private", "{}", "", "done", 1,
                    "2026-11-05T22:20:00+00:00", "g", "{}", "", "", 1, "text"))
        event = json.dumps({"received_at": "2026-11-05T22:20:00+00:00"})
        db.execute("INSERT INTO conversation_inputs VALUES (?,?,?,?,?,?)",
                   ("e1", "b1", 0, event, "h", 0))
    db.commit()
    db.close()


class AutonomousWindowsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unanswered_contact_notifies_and_can_write_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "companion.sqlite3"
            create_database(database, replied=False)
            actions = RecordingActions()

            async def decide(_prompt):
                return '{"write_note":true,"reason":"像一封信"}'

            manager = AutonomousWindowsManager(
                actions=actions,
                policy=AutonomousWindowsPolicy(
                    enabled=True,
                    companion_db_path=str(database),
                    provider_id="provider",
                    timezone="UTC",
                    bedtime_enabled=False,
                ),
                state_path=root / "state.json",
                decision_call=decide,
                clock=lambda: NOW,
            )
            result = await manager.check_once()
            self.assertEqual(result["contacts"], 1)
            self.assertEqual([item[0] for item in actions.calls], ["notify", "write_desktop_note"])
            await manager.check_once()
            self.assertEqual(len(actions.calls), 2)

    async def test_reply_after_contact_suppresses_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "companion.sqlite3"
            create_database(database, replied=True)
            actions = RecordingActions()
            manager = AutonomousWindowsManager(
                actions=actions,
                policy=AutonomousWindowsPolicy(enabled=True, companion_db_path=str(database), timezone="UTC"),
                state_path=root / "state.json",
                clock=lambda: NOW,
            )
            result = await manager.check_once()
            self.assertEqual(result["contacts"], 0)
            self.assertEqual(actions.calls, [])

    async def test_bedtime_warns_but_never_shutdown_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actions = RecordingActions(idle_seconds=1)
            manager = AutonomousWindowsManager(
                actions=actions,
                policy=AutonomousWindowsPolicy(
                    enabled=True,
                    companion_db_path=str(root / "missing.sqlite3"),
                    timezone="UTC",
                    bedtime_enabled=True,
                    bedtime_start_hour=22,
                    active_minutes_before_warning=30,
                    shutdown_enabled=False,
                    poll_seconds=60,
                ),
                state_path=root / "state.json",
                clock=lambda: NOW,
            )
            manager.state.active_seconds = 30 * 60
            await manager.check_once()
            self.assertEqual([item[0] for item in actions.calls], ["status", "notify"])
            self.assertNotIn("schedule_shutdown", [item[0] for item in actions.calls])


if __name__ == "__main__":
    unittest.main()
