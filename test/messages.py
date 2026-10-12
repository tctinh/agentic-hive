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
            run.return_value.returncode = 0
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

    def test_checkpoint_retires_confirmed_handoff_with_delayed_prompt_hook(self):
        row = self.post("accepted by composer", channel="dm", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value.returncode = 0
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
        self.assertIsNone(self.deliver())
        self.assertIn("Old unrelated project", row["body"])

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
