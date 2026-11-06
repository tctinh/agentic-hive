"""Closed legacy journal recovery: synthetic habitats and read-only fake tmux."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get("HIVE_TEST_SOURCE", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(SOURCE / "share"))
import hive_messages as messages
import hive_prompt as prompt
from hive_runtime import atomic, load
from prompt import Terminal


class ObsoleteDelivery(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hive-obsolete-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for member in ("alice", "bob", "carol", "beekeeper"):
            (self.root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(self.root)
        self.state = self.root / "members/bob/state"
        self.telemetry = self.root / "telemetry/members/bob.json"
        atomic(self.telemetry, {"session_id": "fixture-session", "status": "idle", "harness": "codex"})
        self.terminal = Terminal(self.root)
        self.request = messages.post(self.root, "alice", "old work", channel="dm", kind="request", recipients=["bob"], message_id="old")
        self.text = messages.WRAPPER + messages.envelope(self.request, 8000)
        self.original = {"hash": prompt.prompt_hash(self.text), "ids": ["old"], "session_id": None,
            "pane_id": "%legacy", "receipt_before": "", "stage": "pasted"}
        atomic(self.state / "prompt-delivery.json", self.original)

    def close(self, *, sender="alice", resolution="cancel", kind="decision"):
        return messages.post(self.root, sender, "close old work", channel="dm", kind=kind,
            recipients=["bob"], reply_to="old", resolution=resolution, message_id="closure")

    def reconcile(self, run=None):
        return prompt.reconcile_delivery(self.root, "bob", run=run or self.terminal.run)

    def assert_no_input(self):
        self.assertFalse(any(args[0] == "tmux" and args[1] not in ("display-message", "capture-pane") for args in self.terminal.calls))
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))
        self.assertNotIn("old", messages.state(self.root, "bob")["request_receipts"])

    def assert_blocked(self):
        report = self.reconcile()
        self.assertEqual(report["status"], "inspection-required")
        self.assertFalse(report["submission_proven"])
        self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
        self.assertFalse((self.state / "delivery-audit").exists())
        self.assert_no_input()

    def test_null_session_closed_request_is_retired_without_ack_or_binding(self):
        closure = self.close()
        status = prompt.delivery_status(self.root, "bob")
        self.assertTrue(status["retirement_candidate"])
        self.assertEqual(self.terminal.calls, [])
        report = self.reconcile()
        self.assertEqual(report["status"], "retired")
        self.assertFalse(report["submission_proven"])
        record = load(self.state / "prompt-delivery.json")
        self.assertIsNone(record["session_id"])
        audit = load(report["audit"])
        self.assertEqual(audit["journal"], self.original)
        self.assertEqual(audit["closure"]["records"][0]["resolutions"], [closure])
        self.assertEqual(audit["inspection"]["pane_id"], "%7")
        before = list((self.state / "delivery-audit").glob("*.json"))
        self.assertEqual(self.reconcile()["status"], "retired")
        self.assertEqual(before, list((self.state / "delivery-audit").glob("*.json")))
        self.assert_no_input()

    def test_changed_historical_session_can_retire_but_is_not_rebound(self):
        self.original["session_id"] = "old-session"
        atomic(self.state / "prompt-delivery.json", self.original)
        self.close()
        self.assertEqual(self.reconcile()["status"], "retired")
        self.assertEqual(load(self.state / "prompt-delivery.json")["session_id"], "old-session")
        self.assert_no_input()

    def test_open_request_and_high_cursor_cannot_retire(self):
        value = messages.state(self.root, "bob")
        value["cursor"] = 999999
        messages.save_state(self.root, "bob", value)
        self.assert_blocked()

    def test_unrelated_member_cannot_forge_closure(self):
        forged = self.close(sender="carol", resolution=None)
        forged["resolution"] = "ack"
        (self.root / "messages.jsonl").write_text(json.dumps(self.request) + "\n" + json.dumps(forged) + "\n")
        self.assert_blocked()

    def test_partial_unknown_and_idless_batches_cannot_retire(self):
        self.close()
        for ids in ([], ["old", "unknown"]):
            with self.subTest(ids=ids):
                self.original["ids"] = ids
                atomic(self.state / "prompt-delivery.json", self.original)
                self.assert_blocked()

    def test_mixed_batch_cannot_drop_unconfirmed_information(self):
        self.close()
        row = messages.post(self.root, "alice", "passive context", message_id="info")
        self.original["ids"] = ["old", row["id"]]
        atomic(self.state / "prompt-delivery.json", self.original)
        self.assert_blocked()

    def test_working_or_unknown_current_session_cannot_retire(self):
        self.close()
        for telemetry in ({"session_id": "fixture-session", "status": "working"},
                {"session_id": "fixture-session", "status": "unknown"}, {"session_id": None, "status": "idle"}):
            with self.subTest(telemetry=telemetry):
                atomic(self.telemetry, telemetry)
                self.assert_blocked()

    def test_occupied_dialog_and_copy_mode_preserve_current_draft(self):
        self.close()
        self.terminal.draft = "OWNER DRAFT"
        self.assert_blocked()
        self.assertEqual(self.terminal.draft, "OWNER DRAFT")
        self.terminal.draft = ""
        self.terminal.dialog = True
        self.assert_blocked()
        self.terminal.dialog = False
        self.terminal.mode = 1
        self.assert_blocked()

    def test_session_or_pane_change_between_reads_blocks_retirement(self):
        self.close()
        def run(args, **kwargs):
            result = self.terminal.run(args, **kwargs)
            if args[:2] == ["tmux", "capture-pane"]:
                atomic(self.telemetry, {"session_id": "changed", "status": "idle"})
            return result
        self.assertEqual(self.reconcile(run)["status"], "inspection-required")
        self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
        self.assert_no_input()

    def test_append_during_inspection_forces_fresh_retirement_review(self):
        self.close()
        added = False
        def run(args, **kwargs):
            nonlocal added
            result = self.terminal.run(args, **kwargs)
            if args[:2] == ["tmux", "capture-pane"] and not added:
                messages.post(self.root, "alice", "concurrent context")
                added = True
            return result
        self.assertEqual(self.reconcile(run)["status"], "inspection-required")
        self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
        self.assert_no_input()

    def test_pane_or_draft_change_during_inspection_preserves_journal(self):
        self.close()
        for change in ("pane", "draft"):
            self.terminal = Terminal(self.root)
            changed = False
            def run(args, **kwargs):
                nonlocal changed
                result = self.terminal.run(args, **kwargs)
                if args[:2] == ["tmux", "capture-pane"] and not changed:
                    if change == "pane":
                        self.terminal.pane = "%changed"
                    else:
                        self.terminal.draft = "NEW OWNER DRAFT"
                    changed = True
                return result
            self.assertEqual(self.reconcile(run)["status"], "inspection-required")
            self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
            self.assert_no_input()

    def test_retirement_interruption_preserves_original_and_audit(self):
        self.close()
        original_atomic = prompt.atomic
        def interrupted(path, value):
            if Path(path).name == "prompt-delivery.json":
                raise OSError("fixture interruption before journal replacement")
            return original_atomic(path, value)
        with patch("hive_prompt.atomic", side_effect=interrupted), self.assertRaises(OSError):
            self.reconcile()
        self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
        self.assertEqual(len(list((self.state / "delivery-audit").glob("*.json"))), 1)
        self.assertEqual(self.reconcile()["status"], "retired")
        self.assert_no_input()

    def test_retry_of_closed_original_never_pastes_or_returns_ack(self):
        self.close()
        code, _ = prompt.send(self.root, "bob", self.text, run=self.terminal.run)
        self.assertEqual(code, 3)
        self.assert_no_input()

    def test_new_batch_after_null_closed_journal_uses_guarded_sender_once(self):
        self.close()
        fresh = "[hive-message id=new]\nnew work"
        code, _ = prompt.send(self.root, "bob", fresh, run=self.terminal.run)
        self.assertEqual(code, 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.assertEqual(self.terminal.message, fresh)
        audits = [load(p) for p in (self.state / "delivery-audit").glob("*.json")]
        self.assertTrue(any(r["event"] == "retire-closed-requests" and r["journal"] == self.original for r in audits))
        self.assertNotIn("old", messages.state(self.root, "bob")["request_receipts"])

    def test_retirement_cannot_license_another_batchs_uncertain_retry(self):
        self.close()
        with patch.dict(os.environ, HIVE_DELIVERY_RETRY_UNCERTAIN="1"):
            code, _ = prompt.send(self.root, "bob", "[hive-message id=new]\nnew work", run=self.terminal.run)
        self.assertEqual(code, 2)
        self.assertEqual(load(self.state / "prompt-delivery.json"), self.original)
        self.assert_no_input()

    def test_no_input_block_then_owner_closure_allows_later_dispatch_without_replay(self):
        new = messages.post(self.root, "alice", "new work", channel="dm", kind="request", recipients=["bob"], message_id="new")
        def sender(args, **kwargs):
            with patch.dict(os.environ, kwargs["env"]):
                code, detail = prompt.send(self.root, "bob", args[-1], run=self.terminal.run, require_idle=True)
            return subprocess.CompletedProcess(args, code, detail if not code else "", detail if code else "")
        with patch("hive_messages.subprocess.run", side_effect=sender):
            self.assertEqual(messages.dispatch(self.root, new)["queued"], ["bob"])
            pending = load(self.state / "pending-wake.json")
            self.assertFalse(pending["attempted"])
            self.assertEqual(pending["batch"]["ids"], ["new"])
            self.assert_no_input()
            self.close()
            self.assertEqual(messages.dispatch(self.root, new)["sent"], ["bob"])
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.assertFalse((self.state / "pending-wake.json").exists())
        self.assertIn("new", messages.state(self.root, "bob")["request_receipts"])
        self.assertNotIn("old", messages.state(self.root, "bob")["request_receipts"])

    def test_actual_reconcile_cli_retires_closed_null_journal_using_only_reads(self):
        self.close()
        fake = self.root / "bin"
        fake.mkdir()
        tmux = fake / "tmux"
        tmux.write_text('#!/usr/bin/env bash\ncase $1 in\n display-message) echo "1 0 %fake";;\n capture-pane) printf "Transcript\\n› Ask Codex to do anything\\n";;\n *) exit 99;;\nesac\n')
        tmux.chmod(0o755)
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_SHARE=str(SOURCE / "share"),
            HIVE_USER=pwd.getpwuid(os.getuid()).pw_name, PATH=str(fake) + ":" + os.environ["PATH"])
        result = subprocess.run([str(SOURCE / "bin/hive-member"), "delivery-reconcile", "bob"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "retired")
        self.assertIsNone(load(self.state / "prompt-delivery.json")["session_id"])
        self.assert_no_input()

    def test_closed_latched_legacy_batch_does_not_strand_a_new_unattempted_request(self):
        atomic(self.state / "pending-wake.json", {"ids": ["old"], "attempted": True,
            "batch": {"ids": ["old"], "text": self.text}})
        self.close()
        new = messages.post(self.root, "alice", "new work", channel="dm", kind="request", recipients=["bob"], message_id="new")
        def sender(args, **kwargs):
            with patch.dict(os.environ, kwargs["env"]):
                code, detail = prompt.send(self.root, "bob", args[-1], run=self.terminal.run, require_idle=True)
            return subprocess.CompletedProcess(args, code, detail if not code else "", detail if code else "")
        with patch("hive_messages.subprocess.run", side_effect=sender):
            report = messages.dispatch(self.root, new)
        self.assertEqual(report["sent"], ["bob"])
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.assertEqual(messages.state(self.root, "bob")["request_receipts"], ["new"])


if __name__ == "__main__":
    unittest.main()
