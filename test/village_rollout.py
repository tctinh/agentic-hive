"""Rehearse forward/rollback with an actual baseline helper in a temporary habitat.

python3 test/village_rollout.py --baseline /path/to/previous/source
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share"))
    import hive_messages as current
    from hive_runtime import atomic
    import hive_config
    spec = importlib.util.spec_from_file_location("baseline_messages", args.baseline / "share/hive_messages.py")
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    with tempfile.TemporaryDirectory(prefix="hive-rollout-rehearsal-") as temporary:
        root = Path(temporary)
        for member in ("alice", "bob", "beekeeper"):
            (root / "members" / member / "state").mkdir(parents=True)
        (root / "ROOM.md").write_text("<!-- hive:entry gen=7 member=alice -->\n## 2026-10-01T00:00:00+00:00 — alice\n\nOld unscoped handoff\n\nGeneration: 7\n\n")
        (root / "members/bob/state/last-delivered-generation").write_text("5")
        baseline.initialize(root)
        history = baseline.records(root)[0]
        assert history["id"] == "legacy-7" and history["kind"] == "info" and history["channel"] == "legacy"
        assert baseline.state(root, "bob")["cursor"] == 5
        old = baseline.post(root, "alice", "Existing named work", channel="dm", kind="request",
                            recipients=["bob"], message_id="existing-request")
        baseline.receipt(root, "bob", [old["id"]])
        offer = baseline.prepare(root, "bob")
        if offer:
            baseline.accept(root, "bob", offer)
        journal = root / "members/bob/state/prompt-delivery.json"
        atomic(journal, {"stage": "acknowledged", "session_id": "fixture", "ids": [old["id"]]})
        old_journal = journal.read_bytes()

        current.initialize(root)
        current.channel_edit(root, "alice", "create", "workshop", purpose="Temporary collaboration")
        current.channel_edit(root, "bob", "join", "workshop")
        current.channel_edit(root, "bob", "leave", "hive")
        new = current.post(root, "alice", "New village work", channel="workshop", kind="request",
                           recipients=["bob"], message_id="village-request")
        offer = current.prepare(root, "bob")
        current.accept(root, "bob", offer)
        assert set(current.state(root, "bob")["request_receipts"]) == {old["id"], new["id"]}
        assert journal.read_bytes() == old_journal

        native = current.post(root, "alice", "Native handoff remains queued", channel="dm", kind="request",
                              recipients=["bob"], message_id="native-queued")
        transport = root / "members/bob/state/transport.json"
        atomic(transport, {"adapter": "native"})
        with patch("subprocess.run") as run:
            assert current.prepare(root, "bob") is None
            assert baseline.prepare(root, "bob") is None
            assert current.dispatch(root, native)["queued"] == ["bob"]
            run.assert_not_called()
        assert current.load(root / "members/bob/state/pending-wake.json")["ids"] == [native["id"]]

        # Simulated maintenance pause: no concurrent dispatcher or live service.
        forward_registry = (root / "channels.json").read_bytes()
        message_bytes = (root / "messages.jsonl").read_bytes()
        rollback = current.registry(root)
        removed = 0
        for subs in rollback["subscriptions"].values():
            for channel in list(subs):
                if not subs[channel].get("following", True):
                    del subs[channel]
                    removed += 1
        atomic(root / "channels.json", rollback)
        transport.unlink()  # The rehearsal chooses hooks again, only in its temp nest.
        offer = baseline.prepare(root, "bob")
        assert offer["ids"] == [native["id"]]
        baseline.accept(root, "bob", offer)
        assert (root / "messages.jsonl").read_bytes() == message_bytes
        assert journal.read_bytes() == old_journal
        (root / "channels.json").write_bytes(forward_registry)
        assert current.prepare(root, "bob") is None
        assert set(current.state(root, "bob")["request_receipts"]) == {old["id"], new["id"], native["id"]}

        desired = {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "/new/bin/hive-hook codex PostToolUse"}]}]}}
        duplicates = {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "/old/bin/hive-hook codex PostToolUse"},
                                                           {"type": "command", "command": "/other/bin/hive-hook codex PostToolUse"}]}]}}
        merged = hive_config.merge(duplicates, desired)
        commands = [h["command"] for entry in merged["hooks"]["PostToolUse"] for h in entry["hooks"]]
        assert commands == ["/new/bin/hive-hook codex PostToolUse"]
        # Persistent metadata is disabled. Both versions must ignore an old,
        # damaged cache while canonical state survives forward/rollback.
        last = current.records(root)[-1]["sequence"]
        with (root / "messages.jsonl").open("a") as stream:
            for n in range(1, 501):
                row = {"id":f"padding-{n}","sequence":last+n,"channel":"legacy","sender":"alice",
                       "kind":"info","priority":"routine","sender_authority":"peer",
                       "created_at":"2026-11-05T00:00:00+00:00","body":"archived padding "*40}
                stream.write(json.dumps(row)+"\n")
        assert current.prepare(root, "bob") is None
        indexed_log = (root / "messages.jsonl").read_bytes()
        assert baseline.prepare(root, "bob") is None
        (root / ".messages-cache.sqlite3").write_bytes(b"discardable damaged cache")
        cache_bytes = (root / ".messages-cache.sqlite3").read_bytes()
        assert baseline.prepare(root, "bob") is None
        assert current.prepare(root, "bob") is None
        assert (root / ".messages-cache.sqlite3").read_bytes() == cache_bytes
        assert current.prepare(root, "bob") is None
        assert (root / "messages.jsonl").read_bytes() == indexed_log
        assert journal.read_bytes() == old_journal
        print(json.dumps({"forward_migration": "passed", "rollback_with_previous_helper": "passed",
                          "tombstones_removed_in_temporary_copy": removed, "forward_restore": "passed",
                          "legacy_history_preserved_passively": True, "cursor_receipts_and_journal_preserved": True,
                          "native_marker_pauses_both_versions": True, "queued_native_request_preserved": True,
                          "duplicate_owned_hook_registrations_replaced": True,
                          "previous_helper_ignores_derived_index": True,
                          "damaged_index_ignored_without_rebuild": True,
                          "production_touched": False}, indent=2))


if __name__ == "__main__":
    main()
