"""Direct helper deadline/receipt probe with shared history and concurrent members.

Invented content matches village_costs.py. No wrapper exit is treated as proof.
Cold means disposable index removed and advisory page eviction on this own log.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import inspect
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--deadline", type=float, default=3)
    parser.add_argument("--cache", choices=("warm", "cold"), default="warm")
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source / "share"))
    import hive_messages as messages
    from hive_runtime import atomic
    counts = {"info": 17104, "request": 12646, "result": 1102, "decision": 70}
    kinds = [kind for kind, count in counts.items() for _ in range(count)]
    random.Random(42).shuffle(kinds)
    members = ["bob", *[f"peer-{n}" for n in range(1, args.workers)]]
    rows, requests = [], deque()
    for n, kind in enumerate(kinds, 1):
        row = {"id": f"synthetic-{n}", "sequence": n, "channel": "hive", "sender": "alice",
               "sender_authority": "peer", "kind": kind, "priority": "routine",
               "created_at": "2026-11-05T00:00:00+00:00", "body": "synthetic detail " * 30}
        if kind == "request":
            row["recipients"] = ["bob" if n % 20 == 0 else f"peer-{n % 20}"]
            requests.append(row)
        elif kind == "result" and requests:
            request = requests.popleft()
            row.update(sender=request["recipients"][0], recipients=["alice"], reply_to=request["id"])
        elif kind == "result":
            row["kind"] = "info"
        rows.append(row)
    log_text = "".join(json.dumps(row) + "\n" for row in rows)
    base_sequences = {row["id"]:row["sequence"] for row in rows}
    _, closed = messages.request_status(rows)
    received = {member: [r["id"] for r in rows if r["kind"] == "request" and member in r.get("recipients", [])
                        and member not in closed[r["id"]]] for member in members}
    result = {"source": str(source), "records": len(rows), "bytes": len(log_text.encode()),
              "workers": args.workers, "samples": args.samples, "deadline_seconds": args.deadline,
              "cache": args.cache, "cases": {}}
    with tempfile.TemporaryDirectory(prefix="hive-deadline-stress-") as temporary:
        root = Path(temporary)
        for member in {"alice", "beekeeper", "bob", *[f"peer-{n}" for n in range(20)]}:
            (root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(root)
        path = root / "messages.jsonl"
        (root / ".room-generation").write_text(str(len(rows)))
        env = dict(os.environ, HIVE_ROOT=str(root), HIVE_ROOM_PROJECTION="0", PYTHONDONTWRITEBYTECODE="1",
                   PATH=str(source / "bin") + ":" + os.environ["PATH"])
        bootstrap = f'''import fcntl,json,os,sys
from pathlib import Path
sys.path.insert(0,{str(source / "share")!r})
import hive_messages as messages
original_accept=messages.accept
def appended(root,member,offer,**kwargs):
    with (Path(root)/".probe-append.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=Path(root)/"messages.jsonl"
        with path.open("rb") as stream:
            stream.seek(max(0,path.stat().st_size-8192))
            last=json.loads(stream.read().splitlines()[-1])
        row={{"id":"probe-"+member,"sequence":last["sequence"]+1,"channel":"hive","sender":"alice",
             "kind":"request","priority":"routine","body":"arrived during handoff","recipients":[member],
             "sender_authority":"peer","created_at":"2026-11-05T00:00:01+00:00"}}
        with path.open("ab") as stream:
            stream.write((json.dumps(row)+"\\n").encode());stream.flush();os.fsync(stream.fileno())
    return original_accept(root,member,offer,**kwargs)
messages.accept=appended
sys.argv=["hive_messages.py","hook","PostToolUse"]
messages.main()
'''
        for case in ("unreceipted_requests", "concurrent_append_before_accept"):
            samples = []
            for sample in range(args.samples):
                path.write_text(log_text)
                if args.cache == "warm" and "cache" in inspect.signature(messages.records).parameters:
                    messages.records(root, cache=True, member="bob", cursor=len(rows)-1)
                    # The same prefix with a fresh mtime exercises invalidation,
                    # instead of timing an unchanged cache stamp only.
                    path.write_text(log_text)
                elif args.cache == "cold":
                    for suffix in ("", "-wal", "-shm"):
                        (root / (".messages-cache.sqlite3" + suffix)).unlink(missing_ok=True)
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
                        if hasattr(os, "posix_fadvise"):
                            os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                for member in members:
                    value = {"cursor": len(rows)-1, "seen": [], "pending_requests": [], "assigned_requests": [],
                             "request_receipts": [] if case == "unreceipted_requests" else received[member], "offered": None}
                    atomic(root / "members" / member / "state/delivery.json", value)
                    (root / "members" / member / "state/context-stamp").unlink(missing_ok=True)
                processes = []
                for member in members:
                    helper = [sys.executable, str(source / "share/hive_messages.py"), "hook", "PostToolUse"]
                    if case == "concurrent_append_before_accept":
                        helper = [sys.executable, "-c", bootstrap]
                    started = time.perf_counter()
                    process = subprocess.Popen(["timeout", str(args.deadline) + "s", *helper],
                        env=dict(env, HIVE_MEMBER=member), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    processes.append((member, process, started))
                def collect(item):
                    member, process, started = item
                    output, error = process.communicate(timeout=args.deadline + 10)
                    elapsed = (time.perf_counter()-started)*1000
                    state = messages.state(root, member)
                    emitted = []
                    if output:
                        context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
                        emitted = re.findall(r"^\[hive-message id=([^\]]+)\]", context, re.M)
                    handed = set(state["request_receipts"]) | set(state["seen"])
                    seq = dict(base_sequences)
                    with path.open("rb") as stream:
                        stream.seek(max(0,path.stat().st_size-65536))
                        for line in stream.read().splitlines():
                            try:
                                row = json.loads(line)
                                seq[row["id"]] = row["sequence"]
                            except ValueError:
                                pass
                    committed = bool(emitted) and all(identity in handed or seq.get(identity,10**9)<=state["cursor"] for identity in emitted)
                    return {"sample":sample, "member":member, "exit_code":process.returncode,
                            "elapsed_ms":round(elapsed,2), "emitted_ids":emitted, "receipt_committed":committed,
                            "retained_offer":bool(state.get("offered")),
                            "concurrent_request_tracked": "probe-"+member in state.get("assigned_requests",[])
                            if case == "concurrent_append_before_accept" else None,
                            "error":error[-240:]}
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    samples.extend(pool.map(collect, processes))
            result["cases"][case] = {"runs":len(samples), "clean_exits":sum(r["exit_code"]==0 for r in samples),
                "committed":sum(r["receipt_committed"] and not r["retained_offer"] for r in samples),
                "max_ms":max(r["elapsed_ms"] for r in samples),"details":samples}
        print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
