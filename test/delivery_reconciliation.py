"""Transport safety regressions. All state and terminal input are synthetic."""
import json
import os
import pwd
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get("HIVE_TEST_SOURCE", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(SOURCE / "share"))
import hive_prompt as prompt
import hive_messages as messages
from hive_runtime import atomic, load
from prompt import Terminal


class DeliveryReconciliation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hive-delivery-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for member in ("alice", "bob", "beekeeper"):
            (self.root / "members" / member / "state").mkdir(parents=True)
        self.state = self.root / "members/bob/state"
        self.telemetry = self.root / "telemetry/members/bob.json"
        atomic(self.telemetry, {"session_id": "fixture-session", "status": "idle", "harness": "codex"})
        self.terminal = Terminal(self.root)
        self.text = "HIVE messages\n[hive-message id=first]\nPlease check this."
        messages.initialize(self.root)
        (self.root / ".room-generation").write_text("0\n")

    def send(self, text=None, run=None):
        return prompt.send(self.root, "bob", text or self.text, run=run or self.terminal.run)

    def retained(self, text=None, session="fixture-session"):
        record = {"hash": prompt.prompt_hash(text or self.text), "ids": [] if text else ["first"],
            "receipt_before": "900000000000000000 " + "a" * 64,
            "pane_id": "%7", "session_id": session, "stage": "pasted"}
        if text:
            record["created_ns"] = 1793923200000000000  # 2026-11-06 00:00 UTC
        atomic(self.state / "prompt-delivery.json", record)
        return record

    def test_missing_identity_never_pastes_or_creates_journal(self):
        for identity in (None, "", " ", 42):
            with self.subTest(identity=identity):
                atomic(self.telemetry, {"session_id": identity, "status": "idle"})
                self.assertEqual(self.send()[0], 3)
                self.assertFalse((self.state / "prompt-delivery.json").exists())
        self.assertEqual(self.terminal.calls, [])

    def test_session_change_after_load_never_pastes(self):
        def run(args, **kwargs):
            result = self.terminal.run(args, **kwargs)
            if args[:2] == ["tmux", "load-buffer"]:
                atomic(self.telemetry, {"session_id": "changed", "status": "idle"})
            return result
        self.assertEqual(self.send(run=run)[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))

    def test_session_change_during_owned_capture_never_enters(self):
        def run(args, **kwargs):
            result = self.terminal.run(args, **kwargs)
            if args[:2] == ["tmux", "capture-pane"] and self.terminal.pastes:
                atomic(self.telemetry, {"session_id": "changed", "status": "idle"})
            return result
        self.assertEqual(self.send(run=run)[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_missing_pane_identity_never_pastes(self):
        def run(args, **kwargs):
            result = self.terminal.run(args, **kwargs)
            if args[:2] == ["tmux", "display-message"]:
                result.stdout = "1 0\n"
            return result
        self.assertEqual(self.send(run=run)[0], 3)
        self.assertEqual(self.terminal.pastes, 0)

    def test_bound_receipt_from_different_session_is_rejected(self):
        self.terminal.ack_delay = 9999
        self.assertEqual(self.send()[0], 2)
        record = load(self.state / "prompt-delivery.json")
        marker = f"{time.time_ns()} {record['hash']}"
        (self.state / "last-submitted-prompt").write_text(marker)
        atomic(self.state / "last-submitted-prompt.json", {"receipt": marker, "session_id": "another-session", "event": "UserPromptSubmit"})
        self.assertEqual(self.send()[0], 2)
        self.assertEqual(self.terminal.pastes, 1)

    def test_new_attempt_does_not_accept_unbound_legacy_slot(self):
        self.terminal.ack_delay = 9999
        self.assertEqual(self.send()[0], 2)
        record = load(self.state / "prompt-delivery.json")
        (self.state / "last-submitted-prompt").write_text(f"{time.time_ns()} {record['hash']}")
        self.assertEqual(self.send()[0], 2)
        self.assertEqual(self.terminal.pastes, 1)

    def history(self, text, *, role="user", timestamp="2026-11-06T01:00:00Z"):
        home = self.root / "codex"
        path = home / "sessions/rollout-fixture-session.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "fixture-session"}}) + "\n" +
            json.dumps({"type": "response_item", "timestamp": timestamp, "payload": {
                "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}) + "\n")
        atomic(self.state / "quota-cache.json", {"session": "fixture-session", "path": str(path)})
        return home

    def test_idless_native_proof_reconciles_without_terminal_mutation(self):
        record = self.retained("owner direct request")
        home = self.history("owner direct request")
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            report = prompt.reconcile_delivery(self.root, "bob")
            self.assertTrue(report["submission_proven"])
            self.assertEqual(load(self.state / "prompt-delivery.json")["stage"], "acknowledged")
            self.assertEqual(load(report["audit"])["journal"], record)
            self.assertEqual(prompt.reconcile_delivery(self.root, "bob")["status"], "submission-proven")
        self.assertEqual(self.terminal.calls, [])
        self.assertFalse((self.state / "delivery.json").exists())

    def test_idless_confirmed_retry_does_not_repaste(self):
        self.retained("owner direct request")
        home = self.history("owner direct request")
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            self.assertEqual(self.send("owner direct request")[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))

    def test_idless_quotes_assistant_and_old_records_do_not_prove_submission(self):
        record = self.retained("owner direct request")
        for text, role, stamp in (("quoted owner direct request", "user", "2026-11-06T01:00:00Z"),
                ("owner direct request", "assistant", "2026-11-06T01:00:00Z"),
                ("owner direct request", "user", "1990-01-01T00:00:00Z")):
            home = self.history(text, role=role, timestamp=stamp)
            self.assertIsNone(prompt.native_receipt(record, load(self.state / "quota-cache.json"), home))

    def test_idless_without_any_time_boundary_is_not_native_proof(self):
        record = dict(self.retained("owner direct request"), receipt_before="")
        record.pop("created_ns")
        home = self.history("owner direct request")
        self.assertIsNone(prompt.native_receipt(record, load(self.state / "quota-cache.json"), home))

    def test_legacy_idless_history_has_no_attempt_time_and_stays_uncertain(self):
        record = self.retained("owner direct request")
        record.pop("created_ns")
        home = self.history("owner direct request")
        self.assertIsNone(prompt.native_receipt(record, load(self.state / "quota-cache.json"), home))

    def test_null_legacy_identity_is_not_invented_from_current_session(self):
        self.retained(session=None)
        home = self.history(self.text)
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            report = prompt.reconcile_delivery(self.root, "bob")
        self.assertEqual(report["status"], "inspection-required")
        self.assertFalse(report["submission_proven"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_audit_status_is_read_only_and_preserves_unrelated_draft(self):
        self.retained()
        self.terminal.draft = "OWNER DRAFT"
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(prompt.delivery_status(self.root, "bob")["status"], "inspection-required")
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(self.terminal.calls, [])

    def test_hook_handoff_is_reported_separately_and_cannot_forge_prompt_ack(self):
        self.retained()
        atomic(self.state / "delivery.json", {"request_receipts": ["first"]})
        report = prompt.reconcile_delivery(self.root, "bob")
        self.assertTrue(report["hook_handoff"])
        self.assertFalse(report["submission_proven"])
        self.assertEqual(load(self.state / "prompt-delivery.json")["stage"], "pasted")

    def test_archived_acknowledgment_keeps_exact_prior_journal_and_proof(self):
        self.assertEqual(self.send()[0], 0)
        first = load(self.state / "prompt-delivery.json")
        self.assertEqual(self.send("second direct request")[0], 0)
        events = [load(p) for p in (self.state / "delivery-audit").glob("*.json")]
        self.assertTrue(any(e["event"] == "replace-acknowledged" and e["journal"] == first for e in events))
        self.assertTrue(any(e["event"] == "stage-acknowledged" and e["hook_receipt"]["session_id"] == "fixture-session" for e in events))

    def test_assignment_closure_retains_transport_receipt(self):
        row = messages.post(self.root, "alice", "work", channel="dm", kind="request", recipients=["bob"], message_id="first")
        messages.receipt(self.root, "bob", [row["id"]])
        messages.post(self.root, "alice", "cancel", kind="decision", channel="dm", reply_to=row["id"], resolution="cancel", recipients=["bob"])
        messages.prepare(self.root, "bob")
        value = messages.state(self.root, "bob")
        self.assertNotIn(row["id"], value["assigned_requests"])
        self.assertIn(row["id"], value["request_receipts"])
        self.retained()
        self.assertTrue(messages.journal_handed_off(self.state, load(self.telemetry)))

    def test_resolved_retained_offer_does_not_replay(self):
        row = messages.post(self.root, "alice", "work", channel="dm", kind="request", recipients=["bob"], message_id="first")
        messages.prepare(self.root, "bob")
        messages.post(self.root, "alice", "cancel", channel="dm", kind="decision", reply_to=row["id"], resolution="cancel", recipients=["bob"])
        self.assertNotIn(row["id"], (messages.prepare(self.root, "bob") or {}).get("ids", []))

    def test_timeout_is_uncertain_and_retains_exact_batch_and_latch(self):
        row = messages.post(self.root, "alice", "work", channel="dm", kind="request", recipients=["bob"], message_id="first")
        with patch("hive_messages.subprocess.run", side_effect=subprocess.TimeoutExpired("sender", 85)):
            report = messages.dispatch(self.root, row)
        self.assertIn("bob", report["unconfirmed"])
        self.assertFalse(report["failed"])
        pending = load(self.state / "pending-wake.json")
        self.assertTrue(pending["attempted"])
        self.assertEqual(pending["batch"]["ids"], ["first"])
        self.assertEqual(load(self.state / "delivery-outcome.json")["outcome"], "unconfirmed")
        self.assertNotIn("first", messages.state(self.root, "bob")["request_receipts"])

    def test_startup_exception_is_bound_to_one_event_and_turn_activity_revokes_it(self):
        atomic(self.telemetry, {"session_id": "fixture-session", "status": "working", "last_event": "SessionStart", "event_id": "startup-1"})
        with patch.dict(os.environ, HIVE_DELIVERY_STARTUP_EVENT="startup-1"):
            self.assertEqual(prompt.send(self.root, "bob", self.text, run=self.terminal.run, require_idle=True)[0], 0)
        atomic(self.telemetry, {"session_id": "fixture-session", "status": "working", "last_event": "UserPromptSubmit", "event_id": "turn-2"})
        with patch.dict(os.environ, HIVE_DELIVERY_STARTUP_EVENT="startup-1"):
            self.assertEqual(prompt.send(self.root, "bob", "another task", run=self.terminal.run, require_idle=True)[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_actual_session_start_without_identity_does_not_reuse_old_identity(self):
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob", HIVE_SHARE=str(SOURCE / "share"), HIVE_QUIET_MINUTES="0")
        subprocess.run([str(SOURCE / "bin/hive-hook"), "claude", "SessionStart"], env=env,
            input=json.dumps({"cwd": str(self.root)}), capture_output=True, text=True, check=True)
        self.assertIsNone(load(self.telemetry)["session_id"])
        self.assertEqual(self.send()[0], 3)
        self.assertEqual(self.terminal.pastes, 0)

    def test_known_no_input_attempt_can_rebind_after_session_change(self):
        atomic(self.state / "prompt-delivery.json", dict(self.retained(), stage="queued", session_id="old-session"))
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_status_cli_is_read_only_and_reconcile_cli_refuses_uncertain_input(self):
        self.retained(session=None)
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_USER=pwd.getpwuid(os.getuid()).pw_name,
            HIVE_SHARE=str(SOURCE / "share"), CODEX_HOME=str(self.root / "codex"))
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = subprocess.run([str(SOURCE / "bin/hive-member"), "delivery-status", "bob"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "inspection-required")
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        result = subprocess.run([str(SOURCE / "bin/hive-member"), "delivery-reconcile", "bob"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(load(self.state / "prompt-delivery.json")["session_id"], None)
        self.assertFalse((self.state / "delivery-audit").exists())

    def test_current_send_confirms_exact_native_turn_when_hook_does_not_fire(self):
        self.terminal.ack_delay=9999
        home=self.root/'codex'
        def run(args,**kwargs):
            result=self.terminal.run(args,**kwargs)
            if args[:2]==['tmux','send-keys']:
                self.history(self.text,timestamp=datetime.now(timezone.utc).isoformat())
            return result
        with patch.dict(os.environ,CODEX_HOME=str(home)):
            self.assertEqual(self.send(run=run)[0],0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))
        self.assertFalse((self.state/'last-submitted-prompt').exists())
        self.assertEqual(load(self.state/'journal-prompt-receipt.json')['event'],'NativeUserMessage')

    def test_current_send_does_not_accept_quoted_native_text_without_hook(self):
        self.terminal.ack_delay=9999
        home=self.root/'codex'
        def run(args,**kwargs):
            result=self.terminal.run(args,**kwargs)
            if args[:2]==['tmux','send-keys']:
                self.history('quoted\n'+self.text,timestamp=datetime.now(timezone.utc).isoformat())
            return result
        with patch.dict(os.environ,CODEX_HOME=str(home)):
            self.assertEqual(self.send(run=run)[0],2)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))
        self.assertFalse((self.state/'journal-prompt-receipt.json').exists())

    def test_claude_pasted_content_wrapper_acknowledges_via_hook(self):
        atomic(self.telemetry, {"session_id": "claude-sid-1", "status": "idle", "harness": "claude"})
        record = self.retained(session="claude-sid-1")
        wrapped = f'\n\n<pasted_content id="a0a0">\n{self.text}\n\n</pasted_content id="a0a0">\n'
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob", HIVE_SHARE=str(SOURCE / "share"), HIVE_QUIET_MINUTES="0")
        subprocess.run([str(SOURCE / "bin/hive-hook"), "claude", "UserPromptSubmit"], env=env,
            input=json.dumps({"session_id": "claude-sid-1", "cwd": str(self.root), "prompt": wrapped}),
            capture_output=True, text=True, check=True)
        receipt = load(self.state / "last-submitted-prompt.json")
        self.assertEqual(receipt["receipt"].split()[1], record["hash"])
        self.assertTrue(prompt.current_receipt_acknowledges(record, self.state, "claude-sid-1"))
        self.assertTrue(prompt.proof_acknowledges(record, load(self.state / "journal-prompt-receipt.json"), "claude-sid-1"))
        report = prompt.reconcile_delivery(self.root, "bob")
        self.assertEqual(report["status"], "submission-proven")
        self.assertEqual(load(self.state / "prompt-delivery.json")["stage"], "acknowledged")

    def test_claude_native_transcript_reconciles_stuck_pasted_journal(self):
        atomic(self.telemetry, {"session_id": "claude-sid-2", "status": "idle", "harness": "claude"})
        record = self.retained(session="claude-sid-2")
        record["stage"] = "accepted"
        atomic(self.state / "prompt-delivery.json", record)
        claude_home = self.root / "claude-home"
        transcript = claude_home / "projects/-srv-hive-projects/claude-sid-2.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        wrapped = f'\n\n<pasted_content id="99b2">\n{self.text}\n\n</pasted_content id="99b2">\n'
        transcript.write_text(json.dumps({
            "type": "user",
            "sessionId": "claude-sid-2",
            "timestamp": "2026-11-24T01:35:25.928Z",
            "message": {"role": "user", "content": wrapped},
        }) + "\n")
        with patch.dict(os.environ, CLAUDE_CONFIG_DIR=str(claude_home)):
            report = prompt.reconcile_delivery(self.root, "bob")
        self.assertTrue(report["submission_proven"])
        self.assertEqual(report["status"], "submission-proven")
        self.assertEqual(load(self.state / "prompt-delivery.json")["stage"], "acknowledged")

    def test_claude_hook_status_lifecycle(self):
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob", HIVE_SHARE=str(SOURCE / "share"), HIVE_QUIET_MINUTES="0")
        def fire(event, payload):
            subprocess.run([str(SOURCE / "bin/hive-hook"), "claude", event], env=env,
                input=json.dumps(payload), capture_output=True, text=True, check=True)
            return load(self.telemetry)
        self.assertEqual(fire("SessionStart", {"session_id": "s1", "cwd": str(self.root), "source": "startup"})["status"], "idle")
        self.assertEqual(fire("SessionStart", {"session_id": "s1", "cwd": str(self.root), "source": "resume"})["status"], "idle")
        self.assertEqual(fire("SessionStart", {"session_id": "s1", "cwd": str(self.root), "source": "compact"})["status"], "working")
        self.assertEqual(fire("StopFailure", {"session_id": "s1", "cwd": str(self.root)})["status"], "idle")
        self.assertEqual(fire("PostToolUseFailure", {"session_id": "s1", "cwd": str(self.root), "tool_name": "Bash"})["status"], "working")
        self.assertEqual(fire("Notification", {"session_id": "s1", "cwd": str(self.root), "notification_type": "permission_prompt"})["status"], "working")
        self.assertEqual(fire("Notification", {"session_id": "s1", "cwd": str(self.root), "notification_type": "idle_prompt"})["status"], "idle")


if __name__ == "__main__":
    unittest.main()
