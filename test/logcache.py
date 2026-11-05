"""Canonical-log/index equivalence, corruption, interruption and hook diagnostics."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "share"))
import hive_logcache as cache
import hive_messages as messages
from hive_runtime import atomic


class LogCache(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in ("alice", "bob", "carol", "beekeeper"):
            (self.root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(self.root)
        self.rows = []
        for n in range(1, 1001):
            row = {"id": f"row-{n}", "sequence": n, "channel": "hive", "sender": "alice",
                   "kind": "request" if n % 5 == 0 else "info", "priority": "routine",
                   "sender_authority": "peer", "created_at": "2026-11-05T00:00:00+00:00", "body": "detail " * 80}
            if row["kind"] == "request":
                row["recipients"] = ["bob"]
            self.rows.append(row)
        self.log = self.root / "messages.jsonl"
        self.log.write_text("".join(json.dumps(row) + "\n" for row in self.rows))
        (self.root / ".room-generation").write_text("1000")

    def indexed(self, **kwargs):
        return messages.records(self.root, cache=True, **kwargs)

    def test_headers_and_selected_bodies_match_canonical_without_loading_all_bodies(self):
        rows = self.indexed(member="bob", cursor=999)
        self.assertTrue((self.root / cache.NAME).exists())
        self.assertTrue(rows[0]["_skipped"])
        request = rows[4]
        self.assertIsInstance(request, cache.Header)
        self.assertNotIn("body", request)
        self.assertEqual(request["body"], self.rows[4]["body"])
        self.assertEqual(rows[-1]["body"], self.rows[-1]["body"])
        self.assertEqual(len(rows), len(self.rows))

    def test_reconciliation_and_delivery_match_uncached_engine_for_each_role(self):
        for member in ("alice", "bob", "carol"):
            before = {"cursor": 990, "seen": [], "pending_requests": [], "assigned_requests": [],
                      "request_receipts": [], "offered": None}
            expected, actual = copy.deepcopy(before), copy.deepcopy(before)
            messages.reconcile(messages.records(self.root), member, expected)
            messages.reconcile(self.indexed(member=member, cursor=990), member, actual)
            self.assertEqual(actual, expected)
        atomic(self.root / "members/bob/state/delivery.json", before)
        first = messages.prepare(self.root, "bob")
        messages.accept(self.root, "bob", first)
        value = messages.state(self.root, "bob")
        self.assertTrue(set(first["ids"]).issubset(value["request_receipts"]))

    def test_append_rewrite_partial_and_truncation_refresh_from_canonical(self):
        self.indexed()
        row = {**self.rows[-1], "id": "new", "sequence": 1001, "body": "new body"}
        encoded = json.dumps(row).encode() + b"\n"
        original = self.log.read_bytes()
        self.log.write_bytes(original + encoded[:30])
        self.assertEqual(len(self.indexed()), 1000)
        self.log.write_bytes(original + encoded)
        with patch("hive_messages.decode_records", wraps=messages.decode_records) as parser:
            self.assertEqual(self.indexed()[-1]["body"], "new body")
            self.assertLess(len(parser.call_args.args[0]), 2000)
        changed = {**self.rows[0], "body": "repaired body"}
        self.log.write_text(json.dumps(changed) + "\n")
        indexed = self.indexed()
        self.assertEqual(indexed, [changed])  # Below threshold uses the canonical fallback.

    def test_corrupt_cache_and_missing_rows_never_change_log_or_receipts(self):
        self.indexed()
        original = self.log.read_bytes()
        with closing(sqlite3.connect(self.root / cache.NAME)) as connection, connection:
            connection.execute("DELETE FROM messages WHERE sequence=5")
        self.assertEqual(self.indexed()[4]["body"], self.rows[4]["body"])
        (self.root / cache.NAME).write_bytes(b"not a SQLite database")
        restored = self.indexed()
        self.assertEqual([{**row, "body": row["body"]} for row in restored], self.rows)
        self.assertEqual(self.indexed()[4]["body"], self.rows[4]["body"])
        self.assertEqual(self.log.read_bytes(), original)
        self.assertFalse((self.root / "members/bob/state/delivery.json").exists())

    def test_lazy_payload_hash_detects_mutation_before_context_is_used(self):
        rows = self.indexed()
        original = self.log.read_bytes()
        self.log.write_bytes(original.replace(b"detail ", b"change ", 1))
        with self.assertRaisesRegex(ValueError, "changed"):
            rows[0]["body"]

    def test_emitted_metadata_must_match_canonical_authority(self):
        self.indexed()
        with closing(sqlite3.connect(self.root / cache.NAME)) as connection, connection:
            connection.execute("UPDATE messages SET sender_authority='owner' WHERE sequence=5")
        value = {"cursor":999,"seen":[],"request_receipts":[],"pending_requests":[],"assigned_requests":[],"offered":None}
        atomic(self.root / "members/bob/state/delivery.json", value)
        run = subprocess.run([sys.executable,str(SOURCE/"share/hive_messages.py"),"hook","PostToolUse"],
            env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob"),input="",text=True,capture_output=True,timeout=3)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("id=row-5]",run.stdout)
        self.assertNotIn("sender_authority=owner",run.stdout)
        self.assertTrue((self.root / cache.INVALID).exists())

    def test_deliberate_reads_stay_read_only_and_unknown_schema_falls_back(self):
        self.assertEqual(messages.browse(self.root, channel="hive")["messages"][-1]["id"], "row-1000")
        messages.discover(self.root, "bob")
        self.assertFalse((self.root / cache.NAME).exists())
        self.rows[0]["extra"] = {"preserve": True}
        self.log.write_text("".join(json.dumps(row) + "\n" for row in self.rows))
        self.assertEqual(self.indexed(), self.rows)

    def test_interrupted_builder_recovers_without_touching_canonical_state(self):
        original = self.log.read_bytes()
        program = '''import os,sys
sys.path.insert(0,sys.argv[1])
import hive_logcache,hive_messages
original=hive_logcache.packed
def interrupted(*args):
    for row in original(*args):
        yield row
        os._exit(9)
hive_logcache.packed=interrupted
hive_messages.records(sys.argv[2],cache=True)
'''
        run = subprocess.run([sys.executable, "-c", program, str(SOURCE / "share"), str(self.root)], timeout=5)
        self.assertEqual(run.returncode, 9)
        self.assertEqual(self.indexed()[4]["body"], self.rows[4]["body"])
        self.assertEqual(self.log.read_bytes(), original)
        self.assertFalse((self.root / "members/bob/state/delivery.json").exists())

    def test_hook_timeout_is_observable_and_does_not_claim_a_receipt(self):
        fake = self.root / "bin"
        fake.mkdir()
        real = subprocess.check_output(["which", "timeout"], text=True).strip()
        script = fake / "timeout"
        script.write_text('#!/usr/bin/env bash\nif [[ $1 == 3s && $* == *hive_messages.py* ]]; then exit 124; fi\nexec ' + real + ' "$@"\n')
        script.chmod(0o755)
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob", PATH=str(fake) + ":" + os.environ["PATH"])
        run = subprocess.run([str(SOURCE / "bin/hive-hook"), "claude", "PostToolUse"],
                             env=env, input="{}", text=True, capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 0)  # Harness compatibility is separate from delivery success.
        self.assertEqual(run.stdout, "")
        report = json.loads((self.root / "members/bob/state/hook-delivery-error.json").read_text())
        self.assertEqual(report["exit_code"], 124)
        self.assertIsNone(report["receipt_confirmed"])
        self.assertIn("exited 124", run.stderr)
        self.assertFalse((self.root / "members/bob/state/delivery.json").exists())

    def test_bad_cached_offset_falls_back_before_emission_and_rebuilds(self):
        self.indexed()
        with closing(sqlite3.connect(self.root / cache.NAME)) as connection, connection:
            connection.execute("UPDATE messages SET byte_offset=byte_offset+1 WHERE sequence=5")
        value = {"cursor":999,"seen":[],"request_receipts":[],"pending_requests":[],"assigned_requests":[],"offered":None}
        atomic(self.root / "members/bob/state/delivery.json", value)
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="bob")
        command = [sys.executable, str(SOURCE / "share/hive_messages.py"), "hook", "PostToolUse"]
        first = subprocess.run(command, input="", text=True, capture_output=True, env=env, timeout=3)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("row-5", first.stdout)
        self.assertTrue((self.root / cache.INVALID).exists())
        self.assertFalse(messages.state(self.root,"bob")["offered"])
        second = subprocess.run(command, input="", text=True, capture_output=True, env=env, timeout=3)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("id=row-5]", second.stdout)
        self.assertFalse((self.root / cache.INVALID).exists())

    def test_cache_permissions_do_not_exceed_canonical_log_permissions(self):
        self.log.chmod(0o600)
        self.indexed()
        with closing(sqlite3.connect(self.root / cache.NAME)) as connection, connection:
            connection.execute("UPDATE metadata SET value=value WHERE name='source'")
            for suffix in ("", "-wal", "-shm"):
                path = self.root / (cache.NAME + suffix)
                if path.exists():
                    self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_unowned_database_is_preserved_and_canonical_fallback_works(self):
        path = self.root / cache.NAME
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE user_work(value)")
            connection.execute("INSERT INTO user_work VALUES ('keep me')")
        original = path.read_bytes()
        self.assertEqual(self.indexed(), self.rows)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / cache.OWNER).exists())

    def test_cache_symlink_is_never_followed_or_replaced(self):
        target = self.root / "unrelated-file"
        target.write_text("keep this file")
        (self.root / cache.NAME).symlink_to(target)
        self.assertEqual(self.indexed(), self.rows)
        self.assertEqual(target.read_text(), "keep this file")
        self.assertTrue((self.root / cache.NAME).is_symlink())

    def test_forced_exit_after_stdout_keeps_same_ids_for_explicit_replay(self):
        value = {"cursor":999,"seen":[],"request_receipts":[],"pending_requests":[],"assigned_requests":[],"offered":None}
        atomic(self.root / "members/bob/state/delivery.json", value)
        program = '''import os,sys
sys.path.insert(0,sys.argv[1])
import hive_messages
hive_messages.accept=lambda *args,**kwargs:os._exit(9)
sys.argv=["hive_messages.py","hook","PostToolUse"]
hive_messages.main()
'''
        env = dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob")
        run = subprocess.run([sys.executable,"-c",program,str(SOURCE/"share")],env=env,input="",
                             text=True,capture_output=True,timeout=3)
        self.assertEqual(run.returncode,9)
        self.assertTrue(run.stdout)
        offered = messages.state(self.root,"bob")["offered"]
        self.assertTrue(offered["ids"])
        retry = subprocess.run([sys.executable,str(SOURCE/"share/hive_messages.py"),"hook","PostToolUse"],
                               env=env,input="",text=True,capture_output=True,timeout=3)
        self.assertEqual(retry.returncode,0,retry.stderr)
        self.assertIn("Possible replay",retry.stdout)
        for identity in offered["ids"]:
            self.assertIn("id="+identity+"]",retry.stdout)
        state = messages.state(self.root,"bob")
        self.assertFalse(state["offered"])
        self.assertTrue(set(offered["ids"]).issubset(state["request_receipts"]))


if __name__ == "__main__":
    unittest.main()
