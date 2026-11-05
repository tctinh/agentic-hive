"""Reproduce indexed-helper pending-offer upgrades in temporary habitats.

python3 test/offer_upgrade.py --baseline /path/to/e8d7096/source
The baseline is read only. Only the fixture's SQLite metadata is corrupted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from metadata_integrity import MetadataIntegrity, SOURCE
import hive_messages as messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    results = []
    for interrupted in (None, "before-save", "before-output", "after-output"):
        fixture = MetadataIntegrity(methodName="runTest")
        fixture.setUp()
        try:
            root = fixture.root
            value = fixture.ledger()
            value["cursor"] = 4
            messages.atomic(root / "members/bob/state/delivery.json", value)
            fixture.corrupt("UPDATE messages SET kind='decision',sender='bob',reply_to='row-5',resolution='ack' WHERE sequence=999")
            env = dict(os.environ, HIVE_ROOT=str(root), HIVE_MEMBER="bob", HIVE_DELIVERY_CACHE="off",
                       PYTHONDONTWRITEBYTECODE="1", HIVE_QUIET_MINUTES="0")
            program = '''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import hive_messages
hive_messages.prepare(Path(sys.argv[2]),"bob")
'''
            old = subprocess.run([sys.executable,"-c",program,str(baseline/"share"),str(root)],env=env,
                                 text=True,capture_output=True,timeout=5)
            assert old.returncode == 0, old.stderr
            prior = messages.state(root,"bob")["offered"]
            assert prior and "row-5" in prior["ignored"] and "row-5" not in prior["ids"]
            assert "[hive-message id=row-5]" not in prior["text"]
            assert not messages.state(root,"bob")["request_receipts"]
            assert fixture.log.read_bytes() == fixture.original

            replay_ids = None
            if interrupted:
                stage = root/"probe-share"
                stage.mkdir()
                for file in (SOURCE/"share").glob("*.py"):
                    if file.name != "hive_messages.py": (stage/file.name).symlink_to(file)
                setup="import os,sys\nsys.path.insert(0,"+repr(str(SOURCE/"share"))+")\nimport hive_messages\n"
                if interrupted == "before-output":
                    setup+="original=hive_messages.prepare\ndef stopped(*args,**kwargs):\n original(*args,**kwargs)\n os._exit(9)\nhive_messages.prepare=stopped\n"
                else:
                    target="save_state" if interrupted == "before-save" else "accept"
                    setup+="hive_messages."+target+"=lambda *args,**kwargs:os._exit(9)\n"
                (stage/"hive_messages.py").write_text(setup+"hive_messages.main()\n")
                stopped = subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],
                    env=dict(env,HIVE_SHARE=str(stage)),input="{}",text=True,capture_output=True,timeout=5)
                assert stopped.returncode == 0
                marker=json.loads((root/"members/bob/state/hook-delivery-error.json").read_text())
                assert marker["exit_code"] == 9 and marker["receipt_confirmed"] is None
                assert "row-5" not in messages.state(root,"bob")["request_receipts"]
                if interrupted == "before-save":
                    assert not stopped.stdout
                    assert messages.state(root,"bob")["offered"] == prior
                else:
                    if interrupted == "before-output":
                        assert not stopped.stdout
                    else:
                        assert "[hive-message id=row-5]" in stopped.stdout
                    rebuilt=messages.state(root,"bob")["offered"]
                    assert rebuilt["version"] == messages.OFFER_VERSION
                    assert "row-5" in rebuilt["ids"] and "row-5" not in rebuilt["ignored"]
                    replay_ids=rebuilt["ids"]

            started=time.perf_counter()
            run = subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],env=env,
                                 input="{}",text=True,capture_output=True,timeout=5)
            elapsed=round((time.perf_counter()-started)*1000,2)
            assert run.returncode == 0, run.stderr
            context=json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"]
            emitted=re.findall(r"^\[hive-message id=([^\]]+)\]",context,re.M)
            value=messages.state(root,"bob")
            assert "row-5" in emitted, json.dumps({"failure":"unseen request was not emitted during upgrade",
                "emitted":emitted,"request_receipts":value["request_receipts"],"retained_offer":bool(value["offered"])})
            if replay_ids is not None:
                assert emitted == replay_ids, "restart did not reuse the canonical interrupted batch"
            assert "row-5" in value["request_receipts"] and "row-5" in value["assigned_requests"]
            assert not value["offered"]
            assert not (root/"members/bob/state/hook-delivery-error.json").exists()
            assert fixture.log.read_bytes() == fixture.original
            follow=subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],env=env,
                                  input="{}",text=True,capture_output=True,timeout=5)
            assert follow.returncode == 0, follow.stderr
            assert "[hive-message id=row-5]" not in follow.stdout
            assert "row-5" in messages.state(root,"bob")["request_receipts"]
            results.append({"interrupted":interrupted,"old_request_was_ignored":True,
                            "emitted_before_receipt":True,"accepted_receipt_preserved":True,
                            "followup_replayed_request":False,"canonical_log_unchanged":True,
                            "interrupted_batch_ids_preserved":True if replay_ids is not None else None,
                            "wrapper_ms":elapsed})
        finally:
            fixture.doCleanups()
    print(json.dumps({"indexed_baseline":str(baseline),"scenarios":results,"production_touched":False},indent=2))


if __name__ == "__main__":
    main()
