"""Synthetic representative-history probe; no production messages are copied.

python3 test/village_costs.py [--samples 5]
"""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--profile", type=Path, help="Optional cProfile output; timings then include profiling")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source / "share"))
    import hive_messages as messages
    from hive_runtime import atomic
    # Shape observed read-only on 2026-11-05; all content below is invented.
    counts = {"info": 17104, "request": 12646, "result": 1102, "decision": 70}
    kinds = [kind for kind, count in counts.items() for _ in range(count)]
    random.Random(42).shuffle(kinds)
    body_length = 15964173 // len(kinds)
    with tempfile.TemporaryDirectory(prefix="hive-village-costs-") as temporary:
        root = Path(temporary)
        for member in ["alice", "bob", "beekeeper", *[f"peer-{n}" for n in range(20)]]:
            (root / "members" / member / "state").mkdir(parents=True)
        messages.initialize(root)
        rows, requests = [], deque()
        for n, kind in enumerate(kinds, 1):
            row = {"id": f"synthetic-{n}", "sequence": n, "channel": "hive", "sender": "alice",
                   "sender_authority": "peer", "kind": kind, "priority": "routine",
                   "created_at": "2026-11-05T00:00:00+00:00", "body": "synthetic detail " * (body_length // 17)}
            if kind == "request":
                target = "bob" if n % 20 == 0 else f"peer-{n % 20}"
                row["recipients"] = [target]
                requests.append(row)
            elif kind == "result" and requests:
                request = requests.popleft()
                row.update(sender=request["recipients"][0], recipients=["alice"], reply_to=request["id"])
            elif kind == "result":
                row["kind"] = "info"
            rows.append(row)
        path = root / "messages.jsonl"
        log_text = "".join(json.dumps(r) + "\n" for r in rows)
        path.write_text(log_text)
        (root / ".room-generation").write_text(str(len(rows)))
        _, closed = messages.request_status(rows)
        received = [r["id"] for r in rows if r["kind"] == "request" and "bob" in r.get("recipients", [])
                    and "bob" not in closed[r["id"]]]
        env = dict(os.environ, HIVE_ROOT=str(root), HIVE_MEMBER="bob", HIVE_ROOM_PROJECTION="0",
                   PATH=str(source / "bin") + ":" + os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1")
        results = {"records": len(rows), "synthetic_bytes": path.stat().st_size,
                   "request_records": counts["request"], "open_requests_for_recipient": len(received), "cases": {}}
        cases = ["recent_checkpoint", "unreceipted_requests"]
        if not args.profile:
            cases.append("concurrent_append_before_accept")
        bootstrap = f'''import sys, json, os
from pathlib import Path
sys.path.insert(0, {str(source / "share")!r})
import hive_messages as messages
original_accept = messages.accept
def concurrent_accept(root, member, offer, **kwargs):
    row = {{"id":"concurrent-probe", "sequence":{len(rows)+1}, "channel":"hive", "sender":"alice",
           "kind":"request", "priority":"routine", "body":"arrived during handoff", "recipients":["bob"],
           "sender_authority":"peer", "created_at":"2026-11-05T00:00:01+00:00"}}
    with (Path(root)/"messages.jsonl").open("ab") as stream:
        stream.write((json.dumps(row)+"\\n").encode()); stream.flush(); os.fsync(stream.fileno())
    return original_accept(root, member, offer, **kwargs)
messages.accept = concurrent_accept
sys.argv = ["hive_messages.py", "hook", "PostToolUse"]
messages.main()
'''
        for case in cases:
            timings, deadline_runs = [], []
            for _ in range(args.samples):
                path.write_text(log_text)
                state = {"cursor": len(rows) - 1, "seen": [], "pending_requests": [], "assigned_requests": [],
                         "request_receipts": [] if case == "unreceipted_requests" else received, "offered": None}
                atomic(root / "members/bob/state/delivery.json", state)
                start = time.perf_counter()
                command = [sys.executable]
                if args.profile:
                    command += ["-m", "cProfile", "-o", str(args.profile)]
                helper = [*command, str(source / "share/hive_messages.py"), "hook", "PostToolUse"]
                if case == "concurrent_append_before_accept":
                    helper = [sys.executable, "-c", bootstrap]
                run = subprocess.run(helper,
                                     input="", text=True, capture_output=True, env=env, timeout=10)
                timings.append((time.perf_counter() - start) * 1000)
                if run.returncode:
                    raise RuntimeError(run.stderr)
                path.write_text(log_text)
                atomic(root / "members/bob/state/delivery.json", state)
                stamp = root / "members/bob/state/context-stamp"
                stamp.unlink(missing_ok=True)
                start = time.perf_counter()
                run = subprocess.run(["timeout", "0.5s", *helper], input="", text=True, capture_output=True, env=env)
                state_after = messages.state(root, "bob")
                deadline_runs.append({"exit_code": run.returncode, "elapsed_ms": round((time.perf_counter()-start)*1000, 2),
                                      "emitted_context": bool(run.stdout),
                                      "retained_offer": bool(state_after.get("offered")),
                                      "concurrent_request_preserved": "concurrent-probe" in state_after.get("assigned_requests", [])
                                      if case == "concurrent_append_before_accept" else None})
            results["cases"][case] = {"helper_median_ms": round(statistics.median(timings), 2),
                                     "helper_max_ms": round(max(timings), 2),
                                     "samples": args.samples, "deadline_runs": deadline_runs}
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
