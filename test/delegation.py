"""Real CLI reservation races with a fake adapter; no actual agents or homes."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sender = self.root / "adapter"
        sender.write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"$HIVE_ROOT/adapter-calls"\n[[ ${TEST_FAIL:-0} != 1 ]]\n'
        )
        sender.chmod(0o755)
        self.env = dict(
            os.environ,
            HIVE_ROOT=str(self.root / "hive"),
            HIVE_MEMBER_BIN=str(sender),
            HOME=str(self.root / "home"),
            CODEX_HOME=str(self.root / "codex"),
        )
        self.run_hive("alice", "init")
        for member in ("alice", "bob", "carol"):
            self.run_hive(member, "join", member)

    def run_hive(self, member, *args, env=None, success=True):
        result = subprocess.run(
            [str(REPO / "bin/hive"), *args],
            env=dict(self.env, HIVE_MEMBER=member, **(env or {})),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode == 0, success, result.stderr + result.stdout)
        return result

    def rows(self):
        return [
            json.loads(line)
            for line in (Path(self.env["HIVE_ROOT"]) / "messages.jsonl").read_text().splitlines()
        ]

    def state(self, name="bob", done=False):
        return (
            Path(self.env["HIVE_ROOT"])
            / "members"
            / name
            / "state"
            / ("delegation.done.json" if done else "delegation.json")
        )

    def test_single_winner_concurrent_assignment(self):
        processes = [
            subprocess.Popen(
                [str(REPO / "bin/hive"), "delegate", "bob", "--task", sender + " task"],
                env=dict(self.env, HIVE_MEMBER=sender),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for sender in ("alice", "carol")
        ]
        results = []
        for process in processes:
            process.communicate(timeout=10)
            results.append(process.returncode)
        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(len(self.rows()), 1)
        assignment = json.loads(self.state().read_text())
        self.assertEqual(self.rows()[0]["id"], assignment["request_id"])
        self.assertIn(assignment["parent_member"], ("alice", "carol"))
        self.run_hive("carol", "delegate", "bob", "--task", "overlap", success=False)

    def test_failed_delivery_keeps_reservation_and_same_request_on_retry(self):
        self.run_hive(
            "alice",
            "delegate",
            "bob",
            "--task",
            "fixture",
            "--claim",
            "fixture/path",
            env={"TEST_FAIL": "1"},
            success=False,
        )
        assignment = json.loads(self.state().read_text())
        self.assertEqual(assignment["delivery_status"], "failed")
        self.assertEqual(len(self.rows()), 1)
        self.run_hive("carol", "delegate", "bob", "--task", "other", success=False)
        self.run_hive("alice", "delegate", "retry", "bob")
        self.assertEqual(json.loads(self.state().read_text())["task_id"], assignment["task_id"])
        self.assertEqual(len(self.rows()), 1)

    def test_completion_reaches_recorded_requester_once_with_room_disabled(self):
        self.run_hive("alice", "delegate", "bob", "--task", "fixture", "--claim", "fixture/path")
        room = Path(self.env["HIVE_ROOT"]) / "ROOM.md"
        room.unlink()
        self.run_hive("bob", "delegate", "done", "tested handoff", env={"HIVE_ROOM_PROJECTION": "0"})
        self.assertFalse(room.exists())
        self.assertFalse(self.state().exists())
        self.assertTrue(self.state(done=True).exists())
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["kind"], "result")
        self.assertEqual(rows[1]["recipients"], ["alice"])
        self.assertEqual(rows[1]["reply_to"], rows[0]["id"])
        calls = (Path(self.env["HIVE_ROOT"]) / "adapter-calls").read_text()
        self.assertEqual(calls.count("send alice "), 1)
        self.run_hive("bob", "delegate", "done", "ignored retry wording", env={"HIVE_ROOM_PROJECTION": "0"})
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual((Path(self.env["HIVE_ROOT"]) / "adapter-calls").read_text().count("send alice "), 1)

    def test_failed_result_delivery_recovers_without_releasing_new_claims(self):
        self.run_hive("alice", "delegate", "bob", "--task", "fixture")
        self.run_hive("bob", "delegate", "done", "ready", env={"TEST_FAIL": "1"}, success=False)
        self.assertFalse(self.state().exists())
        self.assertEqual(len(self.rows()), 2)
        self.run_hive("bob", "delegate", "done", "ready")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[1]["recipients"], ["alice"])

    def test_result_retry_by_task_id_preserves_new_active_assignment(self):
        self.run_hive("alice", "delegate", "bob", "--task", "first")
        first = json.loads(self.state().read_text())["task_id"]
        self.run_hive("bob", "delegate", "done", "first ready", env={"TEST_FAIL": "1"}, success=False)
        self.run_hive("carol", "delegate", "bob", "--task", "second")
        second = json.loads(self.state().read_text())["task_id"]
        self.run_hive("bob", "delegate", "done", "--task-id", first, "retry")
        self.assertEqual(json.loads(self.state().read_text())["task_id"], second)
        self.assertEqual(len([row for row in self.rows() if row["kind"] == "result"]), 1)

    def test_stale_working_target_is_rejected_without_reservation(self):
        path = Path(self.env["HIVE_ROOT"]) / "telemetry/members/bob.json"
        path.write_text('{"status":"working","last_activity":"2000-01-01T00:00:00+00:00"}')
        self.run_hive("alice", "delegate", "bob", "--task", "unsafe", success=False)
        self.assertFalse(self.state().exists())
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
