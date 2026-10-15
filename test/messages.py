"""Channel/receipt regressions over synthetic files and mocked delivery only."""

import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share"))
import hive_messages as messages
import hive_members
from hive_runtime import atomic


def writer(root, number):
    messages.post(root, "alice", f"concurrent {number}", message_id=f"concurrent-{number}")


def subscribe_worker(root):
    hive_members.update(root, "bob", project="debter", team="titantwoshot")


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in ("alice", "bob", "carol", "beekeeper"):
            (self.root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(self.root)

    def post(self, body, **kwargs):
        return messages.post(self.root, "alice", body, **kwargs)

    def deliver(self, member="bob"):
        offer = messages.prepare(self.root, member)
        if offer:
            messages.accept(self.root, member, offer)
        return offer

    def setup_team(self):
        hive_members.update(self.root, "alice", project="debter", team="titantwoshot")
        hive_members.update(self.root, "bob", project="debter", team="titantwoshot")
        hive_members.update(self.root, "carol", project="other", team="poke")

    def test_explicit_team_filter_and_project_interface_subscription(self):
        self.setup_team()
        row = self.post("team only")
        bob = self.deliver()
        carol = self.deliver("carol")
        self.assertIn("team only", bob["text"])
        self.assertNotIn(row["id"], (carol or {}).get("ids", []))
        self.assertEqual(messages.state(self.root, "carol")["cursor"], 1)
        self.assertNotIn(
            "debter",
            messages.subscriptions(self.root, messages.registry(self.root), "bob"),
        )
        messages.channel_edit(self.root, "beekeeper", "subscribe", "debter", member="bob")
        messages.channel_edit(self.root, "beekeeper", "subscribe", "debter", member="alice")
        row = self.post("shared interface", channel="debter", kind="decision")
        self.assertIn(row["id"], self.deliver()["ids"])
        self.assertIsNone(self.deliver("carol"))

    def test_lossless_overflow_with_character_and_message_limits(self):
        rows = [self.post("x" * 700) for _ in range(35)]
        delivered = []
        while offer := self.deliver():
            self.assertLessEqual(len(offer["text"]), 8000)
            self.assertLessEqual(len(offer["ids"]), 12)
            delivered += offer["ids"]
        self.assertEqual(delivered, [r["id"] for r in rows])
        self.assertEqual(messages.state(self.root, "bob")["cursor"], 35)

    def test_oversized_envelope_is_explicit_and_full_body_readable(self):
        row = self.post("huge\n" + "x" * 30000, channel="dm", kind="request", recipients=["bob"])
        offer = self.deliver()
        self.assertLessEqual(len(offer["text"]), 8000)
        self.assertIn("TRUNCATED notification only", offer["text"])
        self.assertIn("hive messages read " + row["id"], offer["text"])
        self.assertEqual(messages.records(self.root)[0]["body"], row["body"])
        self.assertIn(row["id"], messages.state(self.root, "bob")["assigned_requests"])
        self.assertIn(
            row["id"],
            (
                messages.state(self.root, "alice")["pending_requests"]
                if (self.deliver("alice") or True)
                else []
            ),
        )

    def test_restart_reuses_offer_and_urgent_seen_catches_up(self):
        routine = [self.post("routine " + str(n)) for n in range(20)]
        urgent = self.post("cancel now", priority="urgent", recipients=["bob"])
        offer = messages.prepare(self.root, "bob")
        retry = messages.prepare(self.root, "bob")
        self.assertEqual(offer, retry)
        self.assertEqual(offer["ids"][0], urgent["id"])
        self.assertIn("Possible replay", retry["text"])
        messages.accept(self.root, "bob", retry)
        self.assertIn(urgent["id"], messages.state(self.root, "bob")["seen"])
        delivered = offer["ids"]
        while more := self.deliver():
            delivered += more["ids"]
        self.assertEqual(set(delivered), {r["id"] for r in [*routine, urgent]})
        self.assertEqual(len(delivered), 21)
        self.assertEqual(messages.state(self.root, "bob")["seen"], [])

    def test_retry_ids_and_prompt_receipt_do_not_deliver_room_copy(self):
        row = self.post(
            "@bob action",
            channel="dm",
            kind="request",
            recipients=["bob"],
            message_id="caller-id",
        )
        self.assertEqual(
            self.post(
                "@bob action",
                channel="dm",
                kind="request",
                recipients=["bob"],
                message_id="caller-id",
            ),
            row,
        )
        self.assertEqual(len(messages.records(self.root)), 1)
        with self.assertRaises(ValueError):
            self.post("different", message_id="caller-id")
        messages.receipt(self.root, "bob", [row["id"]])
        self.assertIsNone(self.deliver())
        self.assertIn(row["id"], messages.state(self.root, "bob")["assigned_requests"])
        self.assertEqual((self.root / "ROOM.md").read_text().count("id=caller-id"), 1)

    def test_passive_updates_zero_wakes_busy_requests_queue(self):
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, self.post("passive"))
            atomic(
                self.root / "telemetry/members/bob.json",
                {"status": "working", "last_activity": "2000-01-01T00:00:00+00:00"},
            )
            row = self.post("busy request", channel="dm", kind="request", recipients=["bob"])
            self.assertEqual(messages.dispatch(self.root, row)["queued"], ["bob"])
            run.assert_not_called()
        self.assertIn(row["id"], self.deliver()["ids"])

    def test_idle_requests_coalesce_and_result_wakes_pending_requester_once(self):
        atomic(self.root / "telemetry/members/bob.json", {"status": "idle"})
        with patch("hive_messages.subprocess.run") as run:
            def acknowledge_call(args, **kwargs):
                atomic(self.root / f"telemetry/members/{args[2]}.json", {"status": "working"})
                import subprocess
                return subprocess.CompletedProcess(args, 0, "acknowledged", "")
            run.side_effect = acknowledge_call
            first = self.post("first", channel="dm", kind="request", recipients=["bob"])
            self.assertEqual(messages.dispatch(self.root, first)["sent"], ["bob"])
            second = self.post("second", channel="dm", kind="request", recipients=["bob"])
            self.assertEqual(messages.dispatch(self.root, second)["queued"], ["bob"])
            self.assertEqual(run.call_count, 1)
            result = messages.post(
                self.root,
                "bob",
                "done",
                channel="dm",
                kind="result",
                recipients=["alice"],
                reply_to=first["id"],
                message_id="result-id",
            )
            self.assertEqual(messages.dispatch(self.root, result)["sent"], ["alice"])
            self.assertEqual(messages.dispatch(self.root, result)["sent"], [])
            self.assertEqual(run.call_count, 2)
        self.deliver("alice")
        self.assertNotIn(first["id"], messages.state(self.root, "alice")["pending_requests"])
        self.assertIn(second["id"], messages.state(self.root, "alice")["pending_requests"])

    def test_checkpoint_retires_pending_handoff_with_delayed_prompt_hook(self):
        row = self.post("accepted by composer", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 2
            run.return_value.stderr = "processing acknowledgment pending"
            messages.dispatch(self.root, row)
        self.assertTrue((self.root / "members/bob/state/pending-wake.json").exists())
        self.deliver()
        self.assertFalse((self.root / "members/bob/state/pending-wake.json").exists())

    def test_failed_delivery_persists_exact_batch_for_retry(self):
        row = self.post("retry me", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "fixture unavailable"
            self.assertIn("bob", messages.dispatch(self.root, row)["failed"])
            self.assertEqual(messages.state(self.root, "bob")["cursor"], 0)
            batch = json.loads((self.root / "members/bob/state/pending-wake.json").read_text())["batch"]
            run.return_value.returncode = 0
            messages.dispatch(self.root, row, retry=True)
            self.assertEqual(run.call_args.args[0][-1], batch["text"])
        self.assertEqual(len(messages.records(self.root)), 1)

    def test_no_input_failure_can_retry_without_attempt_latch(self):
        row = self.post("first", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 3
            run.return_value.stderr = "composer occupied; no input pasted"
            self.assertEqual(messages.dispatch(self.root, row)["queued"], ["bob"])
            pending = json.loads((self.root / "members/bob/state/pending-wake.json").read_text())
            self.assertFalse(pending["attempted"])
            run.return_value.returncode = 0
            self.assertEqual(messages.drain(self.root, "bob")["sent"], ["bob"])
            self.assertEqual(run.call_count, 2)
        self.assertFalse((self.root / "members/bob/state/pending-wake.json").exists())

    def test_accepted_without_ack_does_not_advance_receipts_or_repaste(self):
        row = self.post("first", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 2
            run.return_value.stderr = "processing acknowledgment pending"
            messages.dispatch(self.root, row)
            value = messages.state(self.root, "bob")
            self.assertEqual(value["cursor"], 0)
            self.assertNotIn(row["id"], value["request_receipts"])
            second = self.post("second", channel="dm", kind="request", recipients=["bob"])
            self.assertEqual(messages.dispatch(self.root, second)["queued"], ["bob"])
            self.assertEqual(run.call_count, 1)

    def test_legacy_attempted_batch_is_not_automatically_retried(self):
        row = self.post("old", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 2
            messages.dispatch(self.root, row)
            messages.drain(self.root, "bob")
            self.assertEqual(run.call_count, 1)

    def test_new_mention_retries_exact_journaled_batch_without_rebuilding_it(self):
        import hashlib
        atomic(self.root / "telemetry/members/bob.json", {"status":"idle", "session_id":"fixture"})
        first = self.post("first", channel="dm", kind="request", recipients=["bob"])
        path = self.root / "members/bob/state"
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 2
            run.return_value.stderr = "paste retained"
            messages.dispatch(self.root, first)
            batch = json.loads((path / "pending-wake.json").read_text())["batch"]
            atomic(path / "prompt-delivery.json", {
                "session_id":"fixture", "stage":"pasted", "ids":batch["ids"],
                "hash":hashlib.sha256(batch["text"].strip().encode()).hexdigest()})
            second = self.post("second", channel="dm", kind="request", recipients=["bob"])
            run.return_value.returncode = 0
            report = messages.dispatch(self.root, second)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0][-1], batch["text"])
            self.assertEqual(report["queued"], ["bob"])
            self.assertEqual(report["sent"], [])
            pending = json.loads((path / "pending-wake.json").read_text())
            self.assertEqual(pending["ids"], [second["id"]])
            self.assertFalse(pending["attempted"])

    def test_attempt_latch_requires_exact_ids_hash_and_session(self):
        import hashlib
        atomic(self.root / "telemetry/members/bob.json", {"status":"idle", "session_id":"fixture"})
        first = self.post("first", channel="dm", kind="request", recipients=["bob"])
        path = self.root / "members/bob/state"
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 2
            messages.dispatch(self.root, first)
            batch = json.loads((path / "pending-wake.json").read_text())["batch"]
            journal = {"session_id":"fixture", "stage":"pasted", "ids":batch["ids"],
                "hash":hashlib.sha256(batch["text"].strip().encode()).hexdigest()}
            for invalid in (dict(journal, ids=["another"]), dict(journal, hash="0"*64),
                            dict(journal, session_id="another"), dict(journal, stage="unknown")):
                with self.subTest(journal=invalid):
                    atomic(path / "prompt-delivery.json", invalid)
                    self.assertEqual(messages.dispatch(self.root, first)["queued"], ["bob"])
            self.assertEqual(run.call_count, 1)
            atomic(path / "prompt-delivery.json", journal)
            atomic(self.root / "telemetry/members/bob.json", {"status":"working", "session_id":"fixture"})
            self.assertEqual(messages.dispatch(self.root, first)["queued"], ["bob"])
            self.assertEqual(run.call_count, 1)

    def test_new_mention_with_owned_draft_runs_sender_without_a_second_paste(self):
        import hive_prompt
        from prompt import Terminal
        import subprocess
        path = self.root / "members/bob/state"
        atomic(self.root / "telemetry/members/bob.json", {"status":"idle", "session_id":"fixture-session"})
        terminal = Terminal(self.root)
        terminal.footer = "  model status · 97% context left"
        terminal.fail_paste = True
        def transport(args, **kwargs):
            code, detail = hive_prompt.send(self.root, "bob", args[-1], run=terminal.run, require_idle=True)
            return subprocess.CompletedProcess(args, code, detail if code == 0 else "", detail if code else "")
        with patch("hive_messages.subprocess.run", side_effect=transport):
            first = self.post("first", channel="dm", kind="request", recipients=["bob"])
            self.assertIn("bob", messages.dispatch(self.root, first)["unconfirmed"])
            self.assertEqual((terminal.pastes,terminal.enters), (1,0))
            terminal.fail_paste = False
            second = self.post("second", channel="dm", kind="request", recipients=["bob"])
            self.assertEqual(messages.dispatch(self.root, second)["queued"], ["bob"])
            self.assertEqual((terminal.pastes,terminal.enters), (1,1))
            self.assertEqual(json.loads((path / "prompt-delivery.json").read_text())["stage"], "acknowledged")
            self.assertIn(first["id"], messages.state(self.root,"bob")["request_receipts"])
            self.assertNotIn(second["id"], messages.state(self.root,"bob")["request_receipts"])

    def check_hook_handoff_recovery(self, pending_old=False, occupied=False):
        import hashlib
        import hive_prompt
        import subprocess
        from prompt import Terminal
        path = self.root / "members/bob/state"
        atomic(self.root / "telemetry/members/bob.json", {
            "status":"idle", "session_id":"fixture-session"})
        first = self.post("old request", channel="dm", kind="request", recipients=["bob"])
        old_text = messages.WRAPPER + messages.envelope(first, 8000)
        atomic(path / "prompt-delivery.json", {
            "hash":hashlib.sha256(old_text.strip().encode()).hexdigest(),
            "ids":[first["id"]], "session_id":"fixture-session", "pane_id":"%7",
            "receipt_before":"", "stage":"pasted"})
        self.deliver()
        second = self.post("new request", channel="dm", kind="request", recipients=["bob"])
        new_text = messages.WRAPPER + messages.envelope(second, 8000)
        atomic(path / "pending-wake.json", {
            "ids":[first["id"],second["id"]] if pending_old else [second["id"]], "attempted":True,
            "batch":{"ids":[first["id"]] if pending_old else [second["id"]],
                "text":old_text if pending_old else new_text}})
        terminal = Terminal(self.root)
        if occupied:
            terminal.draft = 'UNRELATED OWNER DRAFT'
        terminal.footer = ("  GPT-6.1-Sol xhigh · /srv/hive/projects · Work\n"
            "  ? for shortcuts                                     ⚠ 2 warnings · f2 to view")
        def transport(args, **kwargs):
            code, detail = hive_prompt.send(self.root, "bob", args[-1], run=terminal.run, require_idle=True)
            return subprocess.CompletedProcess(args, code, detail if code == 0 else "", detail if code else "")
        with patch("hive_messages.subprocess.run", side_effect=transport):
            report = messages.dispatch(self.root, second)
        if occupied:
            self.assertEqual(report["queued"], ["bob"])
            self.assertEqual((terminal.pastes, terminal.enters), (0, 0))
            self.assertIn(second["id"],json.loads((path / "pending-wake.json").read_text())["ids"])
            self.assertFalse((path / "retired-prompt-delivery.json").exists())
            return
        self.assertEqual(report["sent"], ["bob"])
        self.assertEqual((terminal.pastes, terminal.enters), (1, 1))
        retired = json.loads((path / "retired-prompt-delivery.json").read_text())
        self.assertEqual(retired["stage"], "retired")
        self.assertEqual(retired["ids"], [first["id"]])
        self.assertIn(second["id"], messages.state(self.root,"bob")["request_receipts"])
        self.assertFalse((path / "pending-wake.json").exists())

    def test_hook_handoff_releases_stale_journal_and_latched_new_batch(self):
        self.check_hook_handoff_recovery()

    def test_hook_handoff_prunes_an_already_received_old_batch_before_new_send(self):
        self.check_hook_handoff_recovery(pending_old=True)

    def test_hook_handoff_queue_recovery_preserves_an_unrelated_draft(self):
        self.check_hook_handoff_recovery(pending_old=True,occupied=True)

    def test_handoff_latch_release_requires_all_ids_and_same_session(self):
        path = self.root / "members/bob/state"
        value = messages.state(self.root,"bob")
        value["cursor"] = 99999
        value["request_receipts"] = ["old-one"]
        messages.save_state(self.root,"bob",value)
        atomic(path / "prompt-delivery.json", {"session_id":"fixture", "ids":["old-one","old-two"]})
        self.assertFalse(messages.journal_handed_off(path,{"session_id":"fixture"}))
        atomic(path / "prompt-delivery.json", {"session_id":"fixture", "ids":["old-one"]})
        self.assertFalse(messages.journal_handed_off(path,{"session_id":"another"}))
        self.assertTrue(messages.journal_handed_off(path,{"session_id":"fixture"}))

    def test_busy_to_idle_drain_and_new_working_race(self):
        atomic(self.root / "telemetry/members/bob.json", {"status":"working"})
        row = self.post("late busy", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            self.assertEqual(messages.dispatch(self.root, row)["queued"], ["bob"])
            self.assertEqual(messages.drain(self.root, "bob")["queued"], ["bob"])
            run.assert_not_called()
            atomic(self.root / "telemetry/members/bob.json", {"status":"idle"})
            run.return_value.returncode = 0
            self.assertEqual(messages.drain(self.root, "bob")["sent"], ["bob"])
            self.assertEqual(run.call_count, 1)

    def test_successful_old_batch_keeps_new_request_queued(self):
        first = self.post("first", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 3
            messages.dispatch(self.root, first)
            second = self.post("second", channel="dm", kind="request", recipients=["bob"])
            run.return_value.returncode = 0
            report = messages.dispatch(self.root, second)
            self.assertEqual(report["queued"], ["bob"])
            self.assertEqual(report["sent"], [])
            self.assertNotIn(second["id"], messages.state(self.root, "bob")["request_receipts"])
            self.assertEqual(messages.drain(self.root, "bob")["sent"], ["bob"])

    def test_actual_stop_hook_drains_only_synthetic_queue(self):
        import os
        import subprocess
        import time
        atomic(self.root / "telemetry/members/bob.json", {"status":"working", "session_id":"fixture"})
        row = self.post("late busy", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, row)
            run.assert_not_called()
        binary = self.root / "fake-member"
        binary.write_text("#!/bin/sh\nprintf 'fixture send\\n' > \"$HIVE_ROOT/fake-send\"\n")
        binary.chmod(0o755)
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob", HIVE_SHARE=str(repo/"share"), HIVE_MEMBER_BIN=str(binary), PATH=str(repo/"bin")+":"+os.environ["PATH"])
        subprocess.run([str(repo/"bin/hive-hook"), "claude", "Stop"], env=env, input='{"session_id":"fixture"}', text=True, check=True, capture_output=True)
        for _ in range(100):
            if not (self.root/"members/bob/state/pending-wake.json").exists():
                break
            time.sleep(0.01)
        self.assertTrue((self.root/"fake-send").exists())
        self.assertFalse((self.root/"members/bob/state/pending-wake.json").exists())

    def test_native_migration_pauses_old_adapter_and_retains_pending(self):
        atomic(self.root / "members/bob/state/transport.json", {"adapter": "native"})
        row = self.post("native request", channel="dm", kind="request", recipients=["bob"])
        self.assertIsNone(self.deliver())
        with patch("hive_messages.subprocess.run") as run:
            self.assertEqual(messages.dispatch(self.root, row)["queued"], ["bob"])
            run.assert_not_called()
        self.assertEqual(messages.state(self.root, "bob")["cursor"], 0)

    def test_channel_join_catchup_is_bounded_and_passive(self):
        self.setup_team()
        self.post("pinned", kind="decision", message_id="pinned")
        messages.channel_edit(self.root, "titantwoshot", "pin", "debter/titantwoshot", pin="pinned")
        for n in range(10):
            self.post(f"historical {n}")
        messages.channel_edit(self.root, "beekeeper", "subscribe", "debter/titantwoshot", member="carol")
        self.post("live")
        offer = self.deliver("carol")
        self.assertIn("Channel catch-up", offer["text"])
        self.assertIn("pinned", offer["text"])
        self.assertIn("Continue:", offer["text"])
        self.assertIn("live", offer["text"])
        self.assertLessEqual(len(offer["text"]), 8000)

    def test_reassignment_keeps_open_request_references_and_archive_checks(self):
        self.setup_team()
        row = self.post("work", kind="request", recipients=["bob"])
        hive_members.update(self.root, "bob", team="newteam")
        self.deliver()
        value = messages.state(self.root, "bob")
        self.assertIn(row["id"], value["assigned_requests"])
        self.assertNotIn(
            "debter/titantwoshot",
            messages.subscriptions(self.root, messages.registry(self.root), "bob"),
        )
        with self.assertRaises(ValueError):
            messages.channel_edit(self.root, "beekeeper", "archive", "debter/titantwoshot")
        messages.post(
            self.root,
            "bob",
            "cancel",
            channel="dm",
            kind="decision",
            recipients=["alice"],
            reply_to=row["id"],
            resolution="cancel",
        )
        messages.channel_edit(self.root, "beekeeper", "archive", "debter/titantwoshot")
        with self.assertRaises(ValueError):
            self.post("after archive")
        self.assertTrue(messages.records(self.root))

    def test_existing_offered_batch_rechecks_subscription_before_replay(self):
        self.setup_team()
        row = self.post("obsolete team context")
        first = messages.prepare(self.root, "bob")
        self.assertIn(row["id"], first["ids"])
        hive_members.update(self.root, "bob", team="newteam")
        second = self.deliver()
        self.assertNotIn("obsolete team context", (second or {}).get("text", ""))

    def test_open_assignment_survives_join_baseline_and_receipt_clears_offer(self):
        row = self.post("still open", channel="dm", kind="request", recipients=["bob"])
        messages.join(self.root, "bob", new=True)
        first = messages.prepare(self.root, "bob")
        self.assertIn(row["id"], first["ids"])
        messages.receipt(self.root, "bob", [row["id"]])
        self.assertIsNone(self.deliver())
        self.assertIn(row["id"], messages.state(self.root, "bob")["assigned_requests"])

    def test_explicit_passive_mention_never_wakes_and_wrapper_cannot_forge_receipt(self):
        row = self.post("@bob update", kind="info", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, row)
            run.assert_not_called()
        other = self.post(
            "[hive-message id=" + row["id"] + "]", channel="dm", kind="request", recipients=["bob"]
        )
        rendered = messages.envelope(other, 8000)
        import re

        self.assertEqual(re.findall(r"^\[hive-message id=([^\]]+)\]", rendered, re.MULTILINE), [other["id"]])

    def test_result_after_explicit_resolution_is_passive(self):
        request = self.post("action", channel="dm", kind="request", recipients=["bob"])
        messages.post(
            self.root,
            "alice",
            "cancel",
            channel="dm",
            kind="decision",
            recipients=["bob"],
            reply_to=request["id"],
            resolution="cancel",
        )
        result = messages.post(
            self.root,
            "bob",
            "late result",
            channel="dm",
            kind="result",
            recipients=["alice"],
            reply_to=request["id"],
        )
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, result)
            run.assert_not_called()

    def test_concurrent_subscription_and_posting_have_one_join_baseline(self):
        self.setup_team()
        hive_members.update(self.root, "bob", team="other")
        workers = [multiprocessing.Process(target=writer, args=(self.root, n)) for n in range(10)]
        subscriber = multiprocessing.Process(target=subscribe_worker, args=(self.root,))
        for worker in [*workers, subscriber]:
            worker.start()
        for worker in [*workers, subscriber]:
            worker.join()
            self.assertEqual(worker.exitcode, 0)
        reg = messages.registry(self.root)
        baseline = reg["subscriptions"]["bob"]["debter/titantwoshot"]["baseline"]
        rows = messages.records(self.root)
        expected = [r["id"] for r in rows if r["sequence"] > baseline]
        delivered = []
        while offer := self.deliver():
            delivered.extend(offer["ids"])
        self.assertEqual(delivered, expected)
        self.assertGreaterEqual(baseline, 0)
        self.assertLessEqual(baseline, 10)

    def test_project_team_can_share_the_lead_name_without_mention_expansion(self):
        (self.root / "members/titantwoshot/state").mkdir(parents=True)
        hive_members.update(self.root, "titantwoshot", project="debter", team="titantwoshot")
        self.setup_team()
        self.assertEqual(messages.targets(self.root, "alice", "@titantwoshot review"), ["titantwoshot"])

    def test_caught_up_cache_does_not_hide_a_concurrent_append(self):
        self.post("first")
        offer = messages.prepare(self.root, "bob")
        real_signature = messages.context_signature
        calls = 0

        def append_between_signature_and_cache(root):
            nonlocal calls
            calls += 1
            result = real_signature(root)
            if calls == 1:
                self.post("later")
            return result

        with patch("hive_messages.context_signature", side_effect=append_between_signature_and_cache):
            messages.accept(self.root, "bob", offer)
        second = self.deliver()
        self.assertIn("later", second["text"])

    def test_legacy_migration_retains_history_without_broad_context_injection(self):
        (self.root / "messages.jsonl").unlink()
        (self.root / "channels.json").unlink()
        (self.root / "ROOM.md").write_text(
            "<!-- hive:entry gen=4 member=alice -->\n## 2026-10-01T00:00:00+00:00 — alice\n\nOld unrelated project\n\nGeneration: 4\n\n"
        )
        messages.initialize(self.root)
        row = messages.records(self.root)[0]
        self.assertEqual(row["id"], "legacy-4")
        self.assertEqual(row["sequence"], 4)
        self.assertEqual(row["channel"], "legacy")
        self.assertTrue(messages.registry(self.root)["channels"]["legacy"]["archived"])
        self.assertIsNone(self.deliver())
        self.assertIn("Old unrelated project", row["body"])

    def test_restored_cursor_is_rechecked_after_delivery_cache_invalidation(self):
        row = self.post("saved notification")
        self.deliver()
        path = self.root / "members/bob/state/delivery.json"
        value = json.loads(path.read_text())
        value["cursor"] = 0
        value["seen"] = []
        atomic(path, value)
        offer = messages.prepare(self.root, "bob")
        self.assertIn(row["id"], offer["ids"])

    def test_partial_trailing_write_recovery_and_concurrent_posts(self):
        self.post("first")
        with (self.root / "messages.jsonl").open("ab") as log:
            log.write(b'{"id":"partial')
        self.post("second")
        self.assertEqual([r["sequence"] for r in messages.records(self.root)], [1, 2])
        workers = [multiprocessing.Process(target=writer, args=(self.root, n)) for n in range(15)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual([r["sequence"] for r in messages.records(self.root)], list(range(1, 18)))
        with (self.root / "messages.jsonl").open("a") as log:
            log.write("broken\n")
        with self.assertRaises(ValueError):
            self.post("must not erase complete corrupt record")


if __name__ == "__main__":
    unittest.main()
