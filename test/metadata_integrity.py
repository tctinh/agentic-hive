"""Cached metadata must never become request or receipt authority."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "share"))
import hive_messages as messages
from hive_runtime import atomic

CACHE_NAME = ".messages-cache.sqlite3"


class MetadataIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in ("alice", "bob", "carol", "beekeeper"):
            (self.root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(self.root)
        self.rows = []
        for n in range(1, 1001):
            row = {"id":f"row-{n}","sequence":n,"channel":"hive","sender":"alice",
                   "kind":"request" if n==5 else "info","priority":"routine",
                   "sender_authority":"peer","created_at":"2026-11-05T00:00:00+00:00","body":"detail "*80}
            if n==5:
                row["recipients"]=["bob"]
            self.rows.append(row)
        self.log=self.root/"messages.jsonl"
        self.log.write_text("".join(json.dumps(row)+"\n" for row in self.rows))
        (self.root/".room-generation").write_text("1000")
        self.original=self.log.read_bytes()
        self.seed_previous_cache()

    def seed_previous_cache(self):
        """Exact e8d7096 cache schema/provenance, with canonical bytes untouched."""
        fields=("sequence","id","channel","sender","kind","priority","sender_authority","created_at",
                "recipients","task_id","reply_to","related_message_id","resolution")
        path=self.root/CACHE_NAME
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT);"
                "CREATE TABLE messages(sequence INTEGER PRIMARY KEY,id TEXT,channel TEXT,sender TEXT,kind TEXT,"
                "priority TEXT,sender_authority TEXT,created_at TEXT,recipients TEXT,task_id TEXT,reply_to TEXT,"
                "related_message_id TEXT,resolution TEXT,byte_offset INTEGER,byte_length INTEGER,record_hash TEXT);"
                "PRAGMA user_version=1;")
            offset=0
            for row,line in zip(self.rows,self.original.splitlines(keepends=True)):
                values=[row.get(key) for key in fields]
                if row.get('recipients') is not None:values[8]=json.dumps(row['recipients'])
                connection.execute("INSERT INTO messages VALUES ("+",".join("?" for _ in range(16))+")",
                                   (*values,offset,len(line),hashlib.sha256(line).hexdigest()))
                offset+=len(line)
            stamp=self.log.stat()
            info={"stamp":[stamp.st_ino,stamp.st_size,stamp.st_mtime_ns],"offset":len(self.original),
                  "prefix_hash":hashlib.sha256(self.original).hexdigest(),"count":len(self.rows),"last_sequence":1000}
            connection.execute("INSERT INTO metadata VALUES ('source',?)",(json.dumps(info),))
        (self.root/".messages-cache-owner").write_bytes(b"agentic-hive header cache\n")

    def ledger(self, accepted=False):
        value={"cursor":1000,"seen":[],"pending_requests":[],"assigned_requests":["row-5"],
               "request_receipts":["row-5"] if accepted else [],"offered":None}
        atomic(self.root/"members/bob/state/delivery.json",value)
        return value

    def corrupt(self, sql):
        with closing(sqlite3.connect(self.root/CACHE_NAME)) as connection, connection:
            connection.execute(sql)
        self.assertEqual(self.log.read_bytes(),self.original)

    def test_corrupted_recipient_cannot_exclude_real_unreceived_request(self):
        self.ledger()
        self.corrupt("UPDATE messages SET recipients='[\"carol\"]' WHERE sequence=5")
        offer=messages.prepare(self.root,"bob")
        self.assertIsNotNone(offer)
        self.assertIn("row-5",offer["ids"])
        self.assertIn("row-5",messages.state(self.root,"bob")["assigned_requests"])
        messages.accept(self.root,"bob",offer)
        self.assertIn("row-5",messages.state(self.root,"bob")["request_receipts"])

    def test_unselected_false_acknowledgment_cannot_resolve_request(self):
        self.ledger()
        self.corrupt("UPDATE messages SET kind='decision',sender='bob',reply_to='row-5',resolution='ack' WHERE sequence=999")
        offer=messages.prepare(self.root,"bob")
        self.assertIsNotNone(offer)
        self.assertIn("row-5",offer["ids"])
        self.assertIn("row-5",messages.state(self.root,"bob")["assigned_requests"])
        self.assertFalse(messages.resolved(messages.records(self.root),self.rows[4]))

    def test_accepted_receipt_survives_false_ack_and_cache_removal_without_replay(self):
        self.ledger(accepted=True)
        self.corrupt("UPDATE messages SET kind='decision',sender='bob',reply_to='row-5',resolution='ack' WHERE sequence=999")
        self.assertIsNone(messages.prepare(self.root,"bob"))
        value=messages.state(self.root,"bob")
        self.assertIn("row-5",value["assigned_requests"])
        self.assertIn("row-5",value["request_receipts"])
        (self.root/CACHE_NAME).unlink()
        self.assertIsNone(messages.prepare(self.root,"bob"))
        self.assertIn("row-5",messages.state(self.root,"bob")["request_receipts"])

    def test_old_cache_flags_and_untrusted_snapshot_cannot_enable_metadata_authority(self):
        self.ledger()
        self.corrupt("UPDATE messages SET recipients='[\"carol\"]' WHERE sequence=5")
        rows=messages.records(self.root,cache=True,member="bob",cursor=1000)
        self.assertEqual(rows,self.rows)
        self.assertEqual(rows[4]["recipients"],["bob"])
        offer=messages.prepare(self.root,"bob",use_cache=True)
        self.assertIn("row-5",offer["ids"])
        bad={"rows":[],"status":({},{}),"signature":messages.context_signature(self.root),
             "prefix_hash":hashlib.sha256(self.original).hexdigest(),"offset":len(self.original)}
        messages.accept(self.root,"bob",offer,snapshot=bad)
        self.assertIn("row-5",messages.state(self.root,"bob")["request_receipts"])

    def test_explicit_off_and_default_mode_have_identical_context(self):
        self.ledger()
        with patch.dict(os.environ,{"HIVE_DELIVERY_CACHE":"off"}):
            first=messages.prepare(self.root,"bob")
        with patch.dict(os.environ,{},clear=False):
            os.environ.pop("HIVE_DELIVERY_CACHE",None)
            second=messages.prepare(self.root,"bob")
        self.assertEqual(first,second)

    def test_prompt_without_ids_does_not_read_history_or_change_receipts(self):
        self.ledger(accepted=True)
        ledger=self.root/"members/bob/state/delivery.json"
        before=ledger.read_bytes()
        with patch.object(messages,"records",side_effect=AssertionError("empty receipt read history")):
            messages.receipt(self.root,"bob",[])
        self.assertEqual(ledger.read_bytes(),before)

    def test_wrapper_timeout_is_reported_as_unknown_not_success(self):
        self.ledger(accepted=True)
        ledger=self.root/"members/bob/state/delivery.json"
        before=ledger.read_bytes()
        fake=self.root/"fake-bin"
        fake.mkdir()
        script=fake/"timeout"
        real=shutil.which("timeout")
        script.write_text('#!/usr/bin/env bash\nif [[ $1 == 3s && $* == *hive_messages.py* ]]; then exit 124; fi\nexec '+real+' "$@"\n')
        script.chmod(0o755)
        env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_DELIVERY_CACHE="off",PATH=str(fake)+":"+os.environ["PATH"])
        run=subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],env=env,input="{}",text=True,capture_output=True,timeout=4)
        self.assertEqual(run.returncode,0)
        self.assertEqual(run.stdout,"")
        self.assertEqual(ledger.read_bytes(),before)
        marker=json.loads((self.root/"members/bob/state/hook-delivery-error.json").read_text())
        self.assertEqual(marker["exit_code"],124)
        self.assertIsNone(marker["receipt_confirmed"])
        self.assertIn("exited 124",run.stderr)

    def test_unsupported_cache_modes_fail_before_context_or_receipt_mutation(self):
        self.ledger(accepted=True)
        ledger=self.root/"members/bob/state/delivery.json"
        before=ledger.read_bytes()
        for mode in ("on","verify","auto"):
            with patch.dict(os.environ,{"HIVE_DELIVERY_CACHE":mode}):
                with self.assertRaisesRegex(ValueError,"must be 'off'"):
                    messages.prepare(self.root,"bob")
                with self.assertRaisesRegex(ValueError,"must be 'off'"):
                    messages.receipt(self.root,"bob",["row-5"])
            self.assertEqual(ledger.read_bytes(),before)

    def test_cache_files_are_not_read_rewritten_or_followed(self):
        self.ledger(accepted=True)
        cache=self.root/CACHE_NAME
        cache.write_bytes(b"corrupt and unrelated")
        original=cache.read_bytes()
        self.assertIsNone(messages.prepare(self.root,"bob"))
        self.assertEqual(cache.read_bytes(),original)
        cache.unlink()
        target=self.root/"unrelated-file"
        target.write_text("preserve this work")
        cache.symlink_to(target)
        self.assertIsNone(messages.prepare(self.root,"bob"))
        self.assertTrue(cache.is_symlink())
        self.assertEqual(target.read_text(),"preserve this work")

    def test_replayed_request_id_after_forced_stdout_exit_remains_explicit(self):
        self.ledger()
        program='''import os,sys
sys.path.insert(0,sys.argv[1])
import hive_messages
hive_messages.accept=lambda *args,**kwargs:os._exit(9)
sys.argv=["hive_messages.py","hook","PostToolUse"]
hive_messages.main()
'''
        env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_DELIVERY_CACHE="off")
        first=subprocess.run([sys.executable,"-c",program,str(SOURCE/"share")],env=env,input="",text=True,capture_output=True,timeout=3)
        self.assertEqual(first.returncode,9)
        self.assertIn("id=row-5]",first.stdout)
        self.assertTrue(messages.state(self.root,"bob")["offered"])
        second=subprocess.run([sys.executable,str(SOURCE/"share/hive_messages.py"),"hook","PostToolUse"],env=env,input="",text=True,capture_output=True,timeout=3)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertIn("Possible replay",second.stdout)
        self.assertIn("id=row-5]",second.stdout)
        self.assertFalse(messages.state(self.root,"bob")["offered"])
        self.assertIn("row-5",messages.state(self.root,"bob")["request_receipts"])

    def test_wrapper_reports_unsupported_mode_and_preserves_accepted_receipt(self):
        self.ledger(accepted=True)
        ledger=self.root/"members/bob/state/delivery.json"
        before=ledger.read_bytes()
        env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_DELIVERY_CACHE="on")
        run=subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],env=env,input="{}",text=True,capture_output=True,timeout=4)
        self.assertEqual(run.returncode,0)
        self.assertEqual(run.stdout,"")
        self.assertEqual(ledger.read_bytes(),before)
        marker=json.loads((self.root/"members/bob/state/hook-delivery-error.json").read_text())
        self.assertEqual(marker["exit_code"],1)
        self.assertIsNone(marker["receipt_confirmed"])

    def test_legacy_fast_stamp_cannot_skip_canonical_request_check(self):
        self.ledger()
        stamp=subprocess.check_output(["stat","-c","%i:%s:%y",str(self.log),str(self.root/"channels.json"),
                                       str(self.root/"members/bob/state/delivery.json")],text=True)
        path=self.root/"members/bob/state/context-stamp"
        path.write_text(stamp)
        run=subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],
            env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_DELIVERY_CACHE="off"),
            input="{}",text=True,capture_output=True,timeout=4)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn("id=row-5]",run.stdout)
        self.assertIn("row-5",messages.state(self.root,"bob")["request_receipts"])
        self.assertTrue(path.read_text().startswith("canonical-jsonl-v1\n"))


if __name__=='__main__':
    unittest.main()
