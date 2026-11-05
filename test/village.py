"""Village policy, discovery, attention and interrupted delivery over synthetic nests."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share"))
import hive_members
import hive_messages as messages
from hive_runtime import atomic


class Village(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("alice", "bob", "carol", "beekeeper"):
            (self.root / "members" / name / "state").mkdir(parents=True)
        messages.initialize(self.root)
        self.edit("alice", "create", "combat", purpose="Chase speed and difficulty")

    def edit(self, actor, action, channel="combat", **kwargs):
        return messages.channel_edit(self.root, actor, action, channel, **kwargs)

    def post(self, body, sender="alice", **kwargs):
        return messages.post(self.root, sender, body, channel="combat", **kwargs)

    def deliver(self, member):
        offer = messages.prepare(self.root, member)
        if offer:
            messages.accept(self.root, member, offer)
        return offer

    def test_create_visit_and_discover_do_not_subscribe_or_inject(self):
        row = self.post("Visitor discovered speed issue", sender="bob")
        self.assertEqual(messages.registry(self.root)["subscriptions"], {})
        self.assertIsNone(self.deliver("carol"))
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        board = messages.discover(self.root, "carol", "chase")
        self.assertEqual(board["channels"][0]["name"], "combat")
        self.assertFalse(board["channels"][0]["following"])
        self.assertEqual(board["channels"][0]["explicit_followers"], 0)
        self.assertEqual(board["channels"][0]["participants"], ["bob"])
        page = messages.browse(self.root, query="speed")
        self.assertEqual(page["messages"][0]["id"], row["id"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_follow_leave_and_profile_sync_preserve_explicit_choice(self):
        hive_members.update(self.root, "bob", project="debter", team="titantwoshot")
        self.edit("bob", "leave", "debter/titantwoshot")
        messages.sync_profile(self.root, "bob")
        self.assertFalse(messages.following(messages.registry(self.root), "bob", "debter/titantwoshot"))
        self.edit("bob", "join")
        self.deliver("bob")
        row = self.post("live for followers")
        self.assertIn(row["id"], self.deliver("bob")["ids"])
        self.edit("bob", "leave")
        self.post("quiet after leaving")
        self.assertIsNone(self.deliver("bob"))
        self.edit("bob", "join")
        offer = self.deliver("bob")
        self.assertIn("Channel catch-up", offer["text"])
        self.assertEqual(offer["ids"], [])

    def test_legacy_global_feed_can_be_left_without_losing_direct_requests(self):
        messages.post(self.root, "alice", "legacy global")
        self.assertIn("legacy global", self.deliver("bob")["text"])
        self.edit("bob", "leave", "hive")
        messages.post(self.root, "alice", "global noise")
        self.assertIsNone(self.deliver("bob"))
        row = messages.post(self.root, "alice", "named global", kind="request", recipients=["bob"])
        self.assertIn(row["id"], self.deliver("bob")["ids"])

    def test_rejoin_hive_without_checkpoint_respects_explicit_baseline(self):
        self.edit("bob", "leave", "hive")
        for n in range(16):
            messages.post(self.root, "alice", f"while away {n}")
        self.edit("bob", "join", "hive")
        offer = self.deliver("bob")
        self.assertEqual(offer["ids"], [])
        self.assertIn("Channel catch-up: hive", offer["text"])
        self.assertLessEqual(offer["text"].count("while away"), 2)
        self.assertIsNone(self.deliver("bob"))
        live = messages.post(self.root, "alice", "after rejoin")
        self.assertEqual(self.deliver("bob")["ids"], [live["id"]])

    def test_explicit_join_promotes_team_feed_without_resetting_catchup(self):
        hive_members.update(self.root, "bob", project="debter", team="titantwoshot")
        original = dict(messages.registry(self.root)["subscriptions"]["bob"]["debter/titantwoshot"])
        self.edit("bob", "join", "debter/titantwoshot")
        promoted = messages.registry(self.root)["subscriptions"]["bob"]["debter/titantwoshot"]
        self.assertEqual(promoted, {**original, "source": "explicit"})
        hive_members.update(self.root, "bob", team="newteam")
        self.assertEqual(messages.registry(self.root)["subscriptions"]["bob"]["debter/titantwoshot"], promoted)

    def test_confirmed_sender_preserves_concurrent_later_hook_receipt(self):
        first = self.post("first", kind="request", recipients=["bob"], message_id="first")
        def send(*args, **kwargs):
            second = self.post("second", kind="request", recipients=["bob"], message_id="second")
            messages.receipt(self.root, "bob", [first["id"], second["id"]])
            self.assertEqual(set(messages.state(self.root, "bob")["request_receipts"]), {"first", "second"})
            return subprocess.CompletedProcess(args, 0, "confirmed", "")
        with patch("hive_messages.subprocess.run", side_effect=send):
            self.assertEqual(messages.dispatch(self.root, first)["sent"], ["bob"])
        state = messages.state(self.root, "bob")
        self.assertEqual(set(state["request_receipts"]), {"first", "second"})
        self.assertEqual(set(state["assigned_requests"]), {"first", "second"})
        self.assertEqual(state["cursor"], 2)
        self.assertIsNone(self.deliver("bob"))

    def test_hook_snapshot_invalidates_when_later_receipt_arrives(self):
        first = self.post("first", kind="request", recipients=["bob"], message_id="first")
        snapshot = {}
        offer = messages.prepare(self.root, "bob", snapshot=snapshot)
        second = self.post("second", kind="request", recipients=["bob"], message_id="second")
        messages.receipt(self.root, "bob", [second["id"]])
        messages.accept(self.root, "bob", offer, snapshot=snapshot)
        self.assertEqual(set(messages.state(self.root, "bob")["request_receipts"]), {first["id"], second["id"]})
        self.assertIsNone(self.deliver("bob"))

    def test_requester_can_explicitly_close_after_a_conversational_answer(self):
        request = self.post("Which direction?", kind="request", recipients=["bob"])
        self.post("B seems best", sender="bob", kind="info", recipients=["alice"])
        self.assertFalse(messages.resolved(messages.records(self.root), request))
        resolution = self.post("B selected; request resolved", kind="decision", reply_to=request["id"],
                               resolution="ack", recipients=["bob"])
        self.assertTrue(messages.resolved(messages.records(self.root), request))
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, resolution)
            run.assert_not_called()  # The requester closing its own work is passive to the assignee.
        self.edit("alice", "archive")

    def test_outsider_request_and_result_survive_leave_restart_and_replay(self):
        self.edit("bob", "join")
        request = self.post("Check chase speed", kind="request", recipients=["bob"])
        first = messages.prepare(self.root, "bob")
        self.edit("bob", "leave")
        self.assertEqual(messages.prepare(self.root, "bob")["ids"], first["ids"])
        # A catch-up invalidated by leave can rebuild the envelope with the same ID.
        offer = self.deliver("bob")
        self.assertEqual(offer["ids"], [request["id"]])
        self.assertIn(request["id"], messages.state(self.root, "bob")["assigned_requests"])
        self.assertIsNone(self.deliver("bob"))
        result = self.post("Speed checked", sender="bob", kind="result", recipients=["alice"], reply_to=request["id"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 3, "", "busy")
            self.assertEqual(messages.dispatch(self.root, result)["queued"], ["alice"])
        self.assertIn(result["id"], self.deliver("alice")["ids"])
        self.assertEqual(messages.state(self.root, "bob")["assigned_requests"], [request["id"]])
        self.deliver("bob")  # Reconcile responsibility on the next checkpoint.
        self.assertEqual(messages.state(self.root, "bob")["assigned_requests"], [])
        self.assertEqual(messages.dispatch(self.root, request)["sent"], [])

    def test_direct_delivery_does_not_require_following_and_info_never_wakes(self):
        row = self.post("FYI @bob", kind="info", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            messages.dispatch(self.root, row)
            run.assert_not_called()
        self.assertEqual(self.deliver("bob")["ids"], [row["id"]])
        self.assertFalse(messages.following(messages.registry(self.root), "bob", "combat"))
        self.post("follow-up context")
        self.assertIsNone(self.deliver("bob"))

    def test_named_request_precedes_passive_catchups_with_tiny_budget(self):
        self.edit("bob", "join")
        for n in range(5):
            name = f"other-{n}"
            self.edit("alice", "create", name)
            self.edit("bob", "join", name)
        row = self.post("do this first", kind="request", recipients=["bob"])
        offer = messages.prepare(self.root, "bob", max_messages=1)
        self.assertEqual(offer["ids"], [row["id"]])

    def test_leave_rejoin_does_not_let_old_ack_clear_new_catchup(self):
        self.post("earlier decision", kind="decision")
        self.edit("bob", "join")
        old = messages.prepare(self.root, "bob")
        self.edit("bob", "leave")
        self.edit("bob", "join")  # Same sequence, different attention choice.
        messages.accept(self.root, "bob", old)  # Prior stdout handoff completes late.
        self.assertTrue(messages.registry(self.root)["subscriptions"]["bob"]["combat"]["catchup"])
        fresh = self.deliver("bob")
        self.assertIn("Channel catch-up", fresh["text"])

    def test_cancelled_offer_and_queued_batch_never_replay_stale_request(self):
        old = self.post("stale work", kind="request", recipients=["bob"])
        first = messages.prepare(self.root, "bob")
        atomic(self.root / "members/bob/state/pending-wake.json",
               {"ids": [old["id"]], "attempted": False, "batch": {"ids": [old["id"]], "text": first["text"]}})
        self.post("cancel that", kind="decision", reply_to=old["id"], resolution="cancel", recipients=["bob"])
        new = self.post("replacement work", kind="request", recipients=["bob"])
        with patch("hive_messages.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 3, "", "busy")
            messages.dispatch(self.root, new)
            self.assertNotIn(old["id"], run.call_args.args[0][-1])
            self.assertIn(new["id"], run.call_args.args[0][-1])
        offer = self.deliver("bob")
        self.assertNotIn(old["id"], offer["ids"])
        self.assertIn(new["id"], offer["ids"])
        self.assertFalse((self.root / "members/bob/state/pending-wake.json").exists())

    def test_cancelled_idle_queue_is_retired_without_sending(self):
        old = self.post("stale", kind="request", recipients=["bob"])
        atomic(self.root / "members/bob/state/pending-wake.json", {"ids": [old["id"]], "attempted": True})
        self.post("cancel", kind="decision", reply_to=old["id"], resolution="cancel")
        with patch("hive_messages.subprocess.run") as run:
            self.assertEqual(messages.drain(self.root, "bob"), {"queued": []})
            run.assert_not_called()
        self.assertFalse((self.root / "members/bob/state/pending-wake.json").exists())

    def test_permissions_reserved_names_and_archive_keep_curation_separate(self):
        for actor, action, name, kwargs in [
            ("stranger", "create", "new", {}), ("bob", "create", "dm", {}),
            ("bob", "create", "new", {"lead": "alice"}),
            ("bob", "join", "combat", {"member": "carol"}),
            ("bob", "archive", "combat", {}), ("bob", "create", "../oops", {}),
        ]:
            with self.assertRaises(ValueError):
                self.edit(actor, action, name, **kwargs)
        self.edit("alice", "subscribe", member="bob")  # Existing curator API.
        old = self.post("idempotent", message_id="one-id")
        request = self.post("active", kind="request", recipients=["bob"])
        with self.assertRaises(ValueError):
            self.edit("alice", "archive")
        self.post("cancel", kind="decision", reply_to=request["id"], resolution="cancel")
        self.edit("alice", "archive")
        self.assertEqual(self.post("idempotent", message_id="one-id"), old)
        with self.assertRaises(ValueError):
            self.post("new in archive")
        with self.assertRaises(ValueError):
            self.edit("carol", "join")
        self.assertTrue(messages.browse(self.root, channel="combat")["messages"])
        self.edit("alice", "reopen")
        self.edit("carol", "join")

    def test_discovery_history_search_and_noticeboard_are_bounded(self):
        atomic(self.root / "members/bob/state/delegation.json", {"task": "Check chase speed", "task_id": "task-1"})
        board = messages.discover(self.root, "alice", "chase")
        self.assertEqual(board["residents"][0]["member"], "bob")
        rows = [self.post(f"speed {n} " + "x" * 4000) for n in range(15)]
        page = messages.browse(self.root, channel="combat", query="speed", limit=4)
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), 8200)
        self.assertTrue(all("read_full" in r for r in page["messages"]))
        self.assertIsNotNone(page["next_before"])
        seen = []
        while page["messages"]:
            seen = [r["id"] for r in page["messages"]] + seen
            if page["next_before"] is None:
                break
            page = messages.browse(self.root, channel="combat", before=page["next_before"], limit=4)
        self.assertEqual(seen, [r["id"] for r in rows])
        for limit in (0, -1, 101):
            with self.assertRaises(ValueError):
                messages.browse(self.root, limit=limit)

    def test_concurrent_channel_creation_has_one_winner(self):
        def create(n):
            try:
                self.edit("bob", "create", "same-place")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(create, range(8))), 1)
        self.assertEqual(messages.registry(self.root)["channels"]["same-place"]["lead"], "bob")

    def test_history_with_escaped_content_still_makes_progress(self):
        row = self.post("\x00" * 10000)
        page = messages.browse(self.root, channel="combat")
        self.assertEqual(page["messages"][0]["id"], row["id"])
        self.assertIn("read_full", page["messages"][0])
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), 8200)

    def test_bulk_log_reader_keeps_partial_write_and_corruption_semantics(self):
        body = 'Unicode 日本語; braces {[]}; newline\ncarriage\r quote "'
        row = self.post(body)
        path = self.root / "messages.jsonl"
        complete = path.read_bytes()
        with path.open("ab") as stream:
            stream.write(json.dumps({"sequence": 2, "body": "unfinished"}).encode())
        self.assertEqual(messages.records(self.root), [row])
        self.assertGreater(path.stat().st_size, len(complete))
        self.assertEqual(messages.records(self.root, recover=True), [row])
        self.assertEqual(path.read_bytes(), complete)
        path.write_bytes(complete + b"broken complete record\n")
        with self.assertRaisesRegex(ValueError, "repair required"):
            messages.records(self.root, recover=True)
        for corrupt in (b'{"sequence":1\n"body":"x"},{"sequence":2}\n',
                        b'{"sequence":1},{"sequence":2}\n', b'{"sequence":1}\n\n'):
            path.write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError, "repair required"):
                messages.records(self.root)

    def test_ephemeral_log_snapshot_verifies_prefix_and_partial_append(self):
        first = self.post("first")
        path = self.root / "messages.jsonl"
        snapshot = {}
        complete = path.read_bytes()
        second = {**first, "id": "second", "sequence": 2, "body": "second"}
        encoded = json.dumps(second).encode() + b"\n"
        path.write_bytes(complete + encoded[:20])
        self.assertEqual(messages.records(self.root, snapshot=snapshot), [first])
        path.write_bytes(complete + encoded)
        self.assertEqual(messages.records(self.root, snapshot=snapshot), [first, second])
        changed = {**first, "body": "repaired first"}
        path.write_text(json.dumps(changed) + "\n" + json.dumps(second) + "\n")
        self.assertEqual(messages.records(self.root, snapshot=snapshot), [changed, second])
        path.write_text(json.dumps(changed) + "\n")
        self.assertEqual(messages.records(self.root, snapshot=snapshot), [changed])

    def test_busy_village_does_not_turn_other_workshops_into_context(self):
        self.edit("bob", "leave", "hive")
        self.edit("bob", "join")
        self.deliver("bob")
        with patch.dict(os.environ, {"HIVE_ROOM_PROJECTION": "0"}):
            for n in range(24):
                resident = f"resident-{n}"
                (self.root / "members" / resident / "state").mkdir(parents=True)
                place = f"workshop-{n}"
                self.edit(resident, "create", place)
                messages.post(self.root, resident, "unrelated detail " * 100, channel=place)
            passive = self.post("relevant local discovery")
            direct = messages.post(self.root, "resident-23", "cross-village request", channel="workshop-23",
                                   kind="request", recipients=["bob"])
        offer = self.deliver("bob")
        self.assertEqual(offer["ids"], [direct["id"], passive["id"]])
        self.assertNotIn("unrelated detail", offer["text"])
        self.assertLess(len(offer["text"]), 2000)

    def test_duplicate_registered_hook_processes_have_one_delivery_owner(self):
        row = self.post("single checkpoint delivery", kind="request", recipients=["bob"])
        source = Path(__file__).resolve().parents[1]
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob",
                   PATH=str(source / "bin") + ":" + os.environ["PATH"])
        processes = [subprocess.Popen([str(source / "bin/hive-hook"), "claude", "PostToolUse"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env) for _ in range(2)]
        outputs = [p.communicate(input="{}", timeout=5)[0] for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes))
        self.assertEqual(sum(text.count(f"[hive-message id={row['id']}]") for text in outputs), 1)
        self.assertEqual(messages.state(self.root, "bob")["request_receipts"], [row["id"]])

    def test_cli_self_service_and_paginated_reads(self):
        binary = str(Path(__file__).resolve().parents[1] / "bin/hive")
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob")
        def cli(*args):
            return json.loads(subprocess.check_output([binary, *args], env=env, text=True))
        cli("channel", "join", "combat")
        self.assertTrue(cli("village", "chase")["channels"][0]["following"])
        self.post("speed test")
        self.assertEqual(len(cli("messages", "search", "speed", "--channel", "combat")["messages"]), 1)
        self.assertEqual(cli("messages", "history", "combat")["body"], "speed test")
        self.assertEqual(len(cli("messages", "history", "combat", "--json")["messages"]), 1)
        cli("channel", "leave", "combat")


if __name__ == "__main__":
    unittest.main()
