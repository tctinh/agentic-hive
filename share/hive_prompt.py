"""Guard one tmux prompt handoff; callers hold the member's .prompt.lock.

The small journal survives interruption between paste, Enter and the prompt hook.
It never licenses input into another draft or treats a composer reset as an ACK.
"""
import hashlib
import json
import mmap
import os
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from hive_runtime import atomic, load


def prompt_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()


def receipt_acknowledges(record, current, session_id):
    """A new exact prompt receipt, never a cursor or request-ID inference."""
    if not isinstance(record, dict) or not isinstance(current, str) or not session_id or record.get("session_id") != session_id:
        return False
    target = record.get("hash", "")
    before = record.get("receipt_before")
    if not isinstance(target, str) or not re.fullmatch(r"[0-9a-f]{64}", target) or not isinstance(before, str):
        return False
    fields = current.split()
    prior = before.split()
    if len(fields) != 2 or not fields[0].isdigit() or fields[1] != target:
        return False
    if prior and (len(prior) != 2 or not prior[0].isdigit() or not re.fullmatch(r"[0-9a-f]{64}", prior[1])):
        return False
    created = record.get("created_ns", 0)
    if not isinstance(created, int) or created < 0:
        return False
    return int(fields[0]) > max(int(prior[0]) if prior else 0, created)


def receipt_binding(record):
    names = ["hash", "ids", "session_id", "receipt_before"]
    names += [name for name in ("attempt_id", "created_ns") if name in record]
    return {name: record.get(name) for name in names}


def current_receipt_acknowledges(record, path, session):
    """New attempts require a session-bound hook receipt, not a bare hash slot."""
    typed = load(path / "last-submitted-prompt.json")
    if typed:
        return typed.get("event") == "UserPromptSubmit" and typed.get("session_id") == session and receipt_acknowledges(record, typed.get("receipt"), session)
    if record.get("version", 1) < 2:
        marker = path / "last-submitted-prompt"
        return receipt_acknowledges(record, marker.read_text().strip() if marker.exists() else "", session)
    return False


def audit_record(path, event, record, **evidence):
    """Preserve each journal before replacement; never overwrite prior evidence."""
    target = path / "delivery-audit" / (str(uuid.uuid4()) + ".json")
    atomic(target, {"event": event, "at_ns": time.time_ns(), "journal": record, **evidence})
    return str(target)


def record_receipt(root, member, payload):
    """Retain exact evidence for the active journal at UserPromptSubmit only."""
    path = Path(root) / "members" / member / "state"
    record = load(path / "prompt-delivery.json")
    if not isinstance(payload, dict) or not isinstance(record, dict):
        return False
    text = payload.get("prompt")
    session = payload.get("session_id")
    if not record or not isinstance(text, str):
        return False
    ids = re.findall(r"^\[hive-message id=([A-Za-z0-9._:-]+)\]", text, re.M)
    marker = f"{time.time_ns()} {prompt_hash(text)}"
    if ids != record.get("ids") or not receipt_acknowledges(record, marker, session):
        return False
    atomic(path / "journal-prompt-receipt.json", {
        "binding": receipt_binding(record), "receipt": marker,
        "event": "UserPromptSubmit"})
    return True


def proof_acknowledges(record, proof, session_id):
    return bool(isinstance(proof, dict) and proof.get("event") in ("UserPromptSubmit", "NativeUserMessage")
        and proof.get("binding") == receipt_binding(record)
        and receipt_acknowledges(record, proof.get("receipt", ""), session_id))


def native_receipt(record, cache, codex_home):
    """Recover an overwritten receipt from an exact persisted Codex user turn.

    The member's cached transcript must belong to this session and live under
    its Codex home. Never use quoted/tool/developer text or compaction summaries
    as submission evidence. Search is bounded; absence establishes nothing.
    """
    if not isinstance(record, dict) or not isinstance(cache, dict):
        return None
    sid = record.get("session_id")
    ids = record.get("ids")
    if (not isinstance(sid, str) or not sid.strip() or not isinstance(ids, list)
        or cache.get("session") != sid
        or (not ids and not record.get("created_ns"))):
        return None
    try:
        path = Path(cache.get("path", "")).resolve(strict=True)
        sessions = (Path(codex_home) / "sessions").resolve(strict=True)
        if not path.is_relative_to(sessions) or not path.name.endswith(sid + ".jsonl"):
            return None
        with path.open("rb") as stream:
            metadata = json.loads(stream.readline(1024 * 1024))
            if metadata.get("type") != "session_meta" or metadata.get("payload", {}).get("id") != sid:
                return None
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                end = len(data)
                start = max(0, end - 64 * 1024 * 1024)
                # ID-less direct prompts still have an exact hash/session/time
                # binding. Inspect bounded complete user records, never infer
                # submission from a quoted body, a cursor or an assistant reply.
                for _ in range(80 if ids else 20000):
                    offset = data.rfind(ids[0].encode(), start, end) if ids else data.rfind(b"\n", start, max(start, end - 1)) + 1
                    if offset < 0:
                        break
                    if not ids and (offset < start or end <= start):
                        break
                    first = data.rfind(b"\n", start, offset) + 1
                    last = data.find(b"\n", offset)
                    end = first
                    if first < start or last < 0 or last - first > 2 * 1024 * 1024:
                        continue
                    try:
                        row = json.loads(data[first:last])
                        payload = row.get("payload", {})
                        if row.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
                            continue
                        # Only an entire text part can confirm the prompt. An
                        # embedded quote or matching message ID is insufficient.
                        for part in payload.get("content", []):
                            text = part.get("text") if isinstance(part, dict) else None
                            if not isinstance(text, str) or prompt_hash(text) != record.get("hash"):
                                continue
                            if re.findall(r"^\[hive-message id=([A-Za-z0-9._:-]+)\]", text, re.M) != ids:
                                continue
                            stamp = datetime.fromisoformat(row["timestamp"])
                            if stamp.tzinfo is None:
                                continue
                            marker = f"{int(stamp.timestamp() * 10**9)} {record['hash']}"
                            if receipt_acknowledges(record, marker, sid):
                                return {"binding": receipt_binding(record), "receipt": marker,
                                    "event": "NativeUserMessage", "path": str(path)}
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def closed_journal_proof(root, member, record):
    """Canonical closure makes a request obsolete; it does not prove receipt.

    This deliberately excludes unknown IDs, ID-less attempts and mixed batches.
    A null historical session can never license input or become an ACK here.
    """
    from hive_messages import records, request_status, context_signature
    ids = record.get("ids")
    if (not isinstance(ids, list) or not ids or any(not isinstance(id, str) for id in ids)
        or len(set(ids)) != len(ids)
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("hash", "")))):
        return None
    try:
        signature = context_signature(root)
        rows = records(root)
    except FileNotFoundError:
        return None
    requests, closed = request_status(rows)
    if any(id not in requests or member not in requests[id].get("recipients", [])
        or member not in closed[id] for id in ids):
        return None
    # Keep the exact request and authorized closure records in the audit, not
    # an inference from the current assignment list, a cursor or a receipt.
    evidence = []
    for identity in ids:
        request = requests[identity]
        resolutions = [row for row in rows if row.get("reply_to") == identity and (
            (row.get("resolution") in ("ack", "cancel") and (
                row["sender"] == request["sender"] or row.get("sender_authority") == "owner"))
            or (row["sender"] == member and (row["kind"] == "result" or row.get("resolution") in ("ack", "cancel"))))]
        evidence.append({"request": request, "resolutions": resolutions})
    if context_signature(root) != signature:
        return None
    return {"basis": "canonical-requests-closed", "history_signature": signature, "records": evidence}


def retire_closed_delivery(root, member, record, *, run=subprocess.run, compatible=False):
    """Caller holds .prompt.lock. Retire obsolete work only at a quiet target.

    Inspect the current target rather than adopting a missing historical session.
    Preserve the original record and proof; never clear input or create an ACK.
    """
    path = Path(root) / "members" / member / "state"
    proof = closed_journal_proof(root, member, record)
    if not proof:
        return None, "no canonical closure proof for every journal ID"
    pending_before = load(path / "pending-wake.json") if compatible else None
    if compatible and pending_before.get("attempted"):
        batch_ids = (pending_before.get("batch") or {}).get("ids")
        if not batch_ids or not set(batch_ids).issubset(record["ids"]):
            return None, "another queued attempt is uncertain; compatibility retirement refused"
    telemetry_path = Path(root) / "telemetry/members" / f"{member}.json"
    before = load(telemetry_path)
    session = before.get("session_id")
    initializing = (os.environ.get("HIVE_DELIVERY_STARTUP_EVENT")
        and before.get("event_id") == os.environ["HIVE_DELIVERY_STARTUP_EVENT"]
        and before.get("last_event") == "SessionStart")
    if not isinstance(session, str) or not session.strip() or (before.get("status") != "idle" and not initializing):
        return None, "current target must have an identified idle session"
    try:
        pane_process = run(["tmux", "display-message", "-p", "-t", f"=hive-{member}:0.0", "#{pane_pid}"],
            text=True, capture_output=True, check=True).stdout.strip()
        if not pane_process.isdigit() or not process_in_pane(before.get("pid"), int(pane_process)):
            return None, "lifecycle telemetry is not bound to the current pane process"
        first, pane = terminal_snapshot(member, run)
        second, other_pane = terminal_snapshot(member, run)
    except (OSError, subprocess.CalledProcessError) as error:
        return None, f"could not inspect current target; no retirement: {error}"
    after = load(telemetry_path)
    if any(after.get(key) != before.get(key) for key in ("session_id", "status", "event_id", "last_event", "pid")):
        return None, "session or turn changed during retirement inspection"
    if not pane or pane != other_pane or not empty(first) or not empty(second):
        return None, "current composer is occupied, unavailable or changed; preserve its draft"
    # Re-read canonical state after the terminal reads. No persisted cache or
    # previously printed status report can authorize this transition.
    if closed_journal_proof(root, member, record) != proof:
        return None, "canonical history changed during retirement inspection"
    if load(path / "prompt-delivery.json") != record:
        return None, "journal changed during retirement inspection"
    if not process_in_pane(before.get("pid"), int(pane_process)):
        return None, "current lifecycle process changed during retirement inspection"
    if compatible and load(path / "pending-wake.json") != pending_before:
        return None, "queue changed during compatibility retirement inspection"
    inspection = {"session_id": session, "pane_id": pane, "status": before.get("status"),
        "event_id": before.get("event_id"), "pid": before.get("pid"), "pane_pid": int(pane_process), "empty_composer_observations": 2}
    audit = audit_record(path, "retire-closed-requests-compatible" if compatible else "retire-closed-requests",
        record, closure=proof, inspection=inspection, queue=pending_before)
    retired = dict(record, stage="retired", retirement={"reason": "canonical requests closed; submission remains unproven",
        "audit": audit, "basis": proof["basis"], "ids": record["ids"], "inspection": inspection})
    if compatible:
        retired["retirement"]["mode"] = "legacy-empty-active"
        # Older senders understand an empty active slot, not a retired stage.
        # Preserve both the immutable audit and a readable retirement marker
        # before writing that slot. No unknown attempt is discarded or ACKed.
        atomic(path / "last-retired-prompt-delivery.json", retired)
        atomic(path / "prompt-delivery.json", {})
    else:
        atomic(path / "prompt-delivery.json", retired)
    return retired, None


def delivery_status(root, member):
    """Read-only evidence report. Absence of proof never means non-delivery."""
    path = Path(root) / "members" / member / "state"
    record = load(path / "prompt-delivery.json")
    active_empty = not record
    if active_empty:
        marker = load(path / "last-retired-prompt-delivery.json")
        if marker.get("retirement", {}).get("mode") == "legacy-empty-active":
            record = marker
    telemetry = load(Path(root) / "telemetry/members" / f"{member}.json")
    session = telemetry.get("session_id")
    proof = None
    stable = isinstance(session, str) and bool(session.strip()) and record.get("session_id") == session
    confirmed = bool(record and stable and (current_receipt_acknowledges(record, path, session)
        or proof_acknowledges(record, load(path / "journal-prompt-receipt.json"), session)))
    if record and stable and not confirmed and telemetry.get("harness") == "codex":
        proof = native_receipt(record, load(path / "quota-cache.json"),
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        confirmed = proof_acknowledges(record, proof, session)
    received = load(path / "delivery.json").get("request_receipts", [])
    pending = load(path / "pending-wake.json")
    closure = closed_journal_proof(root, member, record) if record and record.get("stage") != "acknowledged" else None
    retired = record.get("stage") == "retired" and bool(record.get("retirement"))
    return {"member": member, "journal": record, "current_session": session,
        "active_journal_empty": active_empty,
        "pending_ids": pending.get("ids", []), "attempted": pending.get("attempted", False),
        "last_outcome": load(path / "delivery-outcome.json"),
        "submission_proven": confirmed, "native_proof": proof,
        "closure_proof": closure, "retirement_candidate": bool(closure and not retired),
        "hook_handoff": bool(record.get("ids") and all(id in received for id in record["ids"])),
        "status": "no-journal" if not record else "retired" if retired else "submission-proven" if confirmed else "inspection-required",
        "reason": "closed requests retired; submission remains unproven" if retired else None if confirmed or not record else
            "canonical requests closed; reconcile at an identified quiet empty target; submission remains unproven" if closure else
            "missing or changed session identity" if not stable else "no exact submission proof; preserve the draft and do not resend"}


def reconcile_delivery(root, member, *, run=subprocess.run, compatible=False):
    """Caller holds .prompt.lock. Confirm or retire proven obsolete work only."""
    report = delivery_status(root, member)
    if compatible and report["active_journal_empty"] and report["status"] == "retired":
        return report
    if compatible and not report["active_journal_empty"] and not report["submission_proven"] and not report["closure_proof"]:
        report.update(status="inspection-required", reason="no canonical closure proof; compatibility retirement refused")
        return report
    if not report["submission_proven"]:
        if report["retirement_candidate"] or (compatible and report["closure_proof"] and report["status"] == "retired"):
            retired, reason = retire_closed_delivery(root, member, report["journal"], run=run, compatible=compatible)
            if retired:
                report.update(status="retired", journal=retired, retirement_candidate=False,
                    active_journal_empty=compatible,
                    audit=retired["retirement"]["audit"], reason="closed requests retired; submission remains unproven")
            else:
                report["reason"] = reason
                if compatible:
                    report["status"] = "inspection-required"
        return report
    path = Path(root) / "members" / member / "state"
    record = report["journal"]
    if record.get("stage") != "acknowledged":
        # Persist the proof and original before changing the active stage.
        report["audit"] = audit_record(path, "reconcile-submission", record,
            proof=report["native_proof"] or load(path / "journal-prompt-receipt.json"),
            hook_receipt=load(path / "last-submitted-prompt.json"),
            legacy_receipt=(path / "last-submitted-prompt").read_text() if (path / "last-submitted-prompt").exists() else None)
        if report["native_proof"]:
            atomic(path / "journal-prompt-receipt.json", report["native_proof"])
        atomic(path / "prompt-delivery.json", dict(record, stage="acknowledged"))
    return report


def normalized(text):
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def trailing_footer(rows, cursor):
    """Recognize a separated native status block at the terminal's bottom edge."""
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].strip()), -1)
    if last <= cursor or last < len(rows) - 3:
        return None
    status = r"^ {2,}(?:[^\n]*?\s)?(?:100|[0-9]{1,2})% context left(?:\s*[·•|].*)?\s*$"
    warning = r"⚠ [0-9]+ warnings? · f2 to view"
    shortcuts = (r"^ {2,}\? for shortcuts(?:\s*[·•|]\s*(?:100|[0-9]{1,2})% context left)?"
                 r"(?:\s+" + warning + r")?\s*$")
    model = r"^ {2,}GPT-\S+ (?:minimal|low|medium|high|xhigh|max|ultra)(?: · [^\n]+){2}\s*$"
    if not any(re.fullmatch(pattern, rows[last]) for pattern in
               (status, shortcuts, model, r"^ {2,}" + warning + r"\s*$")):
        return None
    start = last
    if start > cursor + 1 and re.fullmatch(model, rows[start - 1]):
        start -= 1
    return start if start > cursor + 1 and not rows[start - 1].strip() else None


def composer(rows, cursor, *, owned=False, expected=None):
    """Read only the cursor's contiguous composer, bounded by non-input rows.

    Empty rows are continuations only when a prior empty-composer paste is
    journaled. A separated trailing footer can end the input only after the
    complete expected draft matches. Other below-cursor text remains input.
    """
    if not 0 <= cursor < len(rows):
        return None
    # A native resume selector uses the same arrow as the input box. It is a
    # dialog, never a place to paste a prompt or confirm a directory choice.
    if (re.match(r"^[›❯][ \u00a0][1-4]\. (?:Use|Always use) (?:session|current) directory", rows[cursor])
        and any(row.strip() == "Working directory · resume" for row in rows[max(0, cursor - 5):cursor])):
        return None
    start = cursor
    while cursor - start < 128:
        line = rows[start]
        match = re.match(r"^[›❯][ \u00a0](.*)$", line)
        if match:
            parts = [match[1]]
            for row in rows[start + 1:cursor + 1]:
                parts.append(row[2:] if row.startswith("  ") else "")
            # Text below an edited cursor also belongs to this draft. Stop at
            # the first non-continuation, never cross another prompt/dialog.
            footer = trailing_footer(rows, cursor)
            for index, row in enumerate(rows[cursor + 1:], cursor + 1):
                if index == footer and (empty(parts) or
                    (owned and ((expected is not None and matches(parts, expected)) or collapsed(parts)))):
                    break
                if row.startswith("  "):
                    parts.append(row[2:])
                elif not row and owned:
                    parts.append("")
                else:
                    break
            return parts
        if not line.startswith("  ") and not (owned and not line):
            return None
        start -= 1
        if start < 0:
            return None
    return None


def matches(parts, message):
    """Match all visible rows, including hard newlines and soft wraps.

    Right padding is not input; only whitespace at a captured row edge may be
    absent. A shortened prefix or appended non-whitespace text cannot match.
    """
    expected = normalized(message)
    chunks = [part.rstrip() for part in parts]
    while chunks and not chunks[-1]:
        chunks.pop()
    positions = {0}
    for index, chunk in enumerate(chunks):
        next_positions = set()
        for pos in positions:
            options = {pos}
            if index:
                if expected[pos:pos + 1] == "\n":
                    options.add(pos + 1)
                # Terminal trimming can remove spaces at a wrap edge.
                while expected[pos:pos + 1] == " ":
                    pos += 1
                    options.add(pos)
            for candidate in options:
                if expected.startswith(chunk, candidate):
                    next_positions.add(candidate + len(chunk))
        positions = next_positions
    return len(expected) in positions


def empty(parts):
    return parts is not None and normalized("\n".join(parts)) in ("", "Ask Codex to do anything")


def collapsed(parts):
    return parts is not None and re.fullmatch(r"\[Pasted (?:Content|text) [^\]\n]+\]", normalized("\n".join(parts))) is not None


def terminal_snapshot(member, run, *, owned=False, expected=None):
    """Read one stable composer. All callers share the same dialog/mode guard."""
    def command(*args):
        return run(["tmux", *args], text=True, capture_output=True, check=True)
    metadata = command("display-message", "-p", "-t", f"=hive-{member}:0.0", "#{cursor_y} #{pane_in_mode} #{pane_id}").stdout.strip().split()
    if len(metadata) != 3 or not metadata[0].isdigit() or metadata[1] != "0" or not metadata[2]:
        return None, None
    screen = command("capture-pane", "-p", "-t", f"=hive-{member}:0.0").stdout.splitlines()
    if command("display-message", "-p", "-t", f"=hive-{member}:0.0", "#{cursor_y} #{pane_in_mode} #{pane_id}").stdout.strip().split() != metadata:
        return None, None
    return composer(screen, int(metadata[0]), owned=owned, expected=expected), metadata[2]


def process_in_pane(pid, pane_pid):
    """Reject lifecycle state left by another process, including resumed panes."""
    if not isinstance(pid, int) or pid <= 1:
        return False
    for _ in range(256):
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            return False
        if re.search(r"^State:\s*[ZX]", status, re.M):
            return False
        if pid == pane_pid:
            return True
        parent = re.search(r"^PPid:\s*(\d+)$", status, re.M)
        if not parent or int(parent[1]) <= 1 or int(parent[1]) == pid:
            return False
        pid = int(parent[1])
    return False


def send(root, member, message, *, run=subprocess.run, require_idle=False):
    path = Path(root) / "members" / member / "state"
    journal = path / "prompt-delivery.json"
    receipt_file = path / "last-submitted-prompt"
    telemetry = load(Path(root) / "telemetry/members" / f"{member}.json")
    identity = telemetry.get("session_id")
    key = prompt_hash(message)
    ids = re.findall(r"^\[hive-message id=([A-Za-z0-9._:-]+)\]", message, re.M)
    previous = load(journal)
    if not previous:
        retired = load(path / "last-retired-prompt-delivery.json")
        if (retired.get("retirement", {}).get("mode") == "legacy-empty-active"
            and retired.get("hash") == key and retired.get("ids") == ids):
            return 3, "obsolete request is retired; submission remains unproven; no input was pasted"
    was_acknowledged = previous.get("stage") == "acknowledged"

    if not isinstance(identity, str) or not identity.strip():
        return 3, "session identity is not ready; no input was pasted"
    if (os.environ.get("HIVE_DELIVERY_RETRY_UNCERTAIN") == "1"
        and (previous.get("hash") != key or previous.get("ids") != ids)):
        # Retiring some other obsolete journal cannot license replay of this
        # batch's own unknown attempt. Its evidence must be reconciled first.
        return 2, "this batch has an uncertain prior attempt without its matching journal; no input was pasted"

    def same_session():
        return load(Path(root) / "telemetry/members" / f"{member}.json").get("session_id") == identity

    def receipt():
        return receipt_file.read_text().strip() if receipt_file.exists() else ""

    def busy():
        current = load(Path(root) / "telemetry/members" / f"{member}.json")
        if current.get("session_id") != identity:
            return True
        initializing = (os.environ.get("HIVE_DELIVERY_STARTUP_EVENT")
            and current.get("event_id") == os.environ["HIVE_DELIVERY_STARTUP_EVENT"]
            and current.get("last_event") == "SessionStart")
        return require_idle and current.get("status") == "working" and not initializing

    def command(*args, input=None):
        return run(["tmux", *args], input=input, text=True, capture_output=True, check=True)

    def pause(seconds=0.25):
        run(["sleep", str(seconds)], check=True)

    def snapshot(owned=False):
        return terminal_snapshot(member, run, owned=owned, expected=message if owned else None)

    def saved(stage, **extra):
        if previous.get("stage") != stage:
            audit_record(path, "stage-" + stage, dict(previous), next_stage=stage,
                hook_receipt=load(path / "last-submitted-prompt.json"),
                proof=load(path / "journal-prompt-receipt.json"))
        previous.update(stage=stage, **extra)
        atomic(journal, previous)

    last_native_check = -float("inf")

    def acknowledged():
        nonlocal last_native_check
        active = load(Path(root) / "telemetry/members" / f"{member}.json").get("session_id")
        if current_receipt_acknowledges(previous, path, active) or proof_acknowledges(
            previous, load(path / "journal-prompt-receipt.json"), active):
            return True
        # A resumed harness can accept the exact user turn while its lifecycle
        # hook is missing. Corroborate during this attempt instead of requiring
        # a later retry/reconciliation. Composer resets alone still prove none.
        if (telemetry.get("harness") == "codex" and active == previous.get("session_id")
            and previous.get("stage") in ("submitted", "accepted")
            and time.monotonic() - last_native_check >= 1):
            last_native_check = time.monotonic()
            proof = native_receipt(previous, load(path / "quota-cache.json"),
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            if proof_acknowledges(previous, proof, active):
                atomic(path / "journal-prompt-receipt.json", proof)
                return True
        return False

    if previous.get("stage") == "queued" and previous.get("session_id") != identity:
        # queued is written only before any paste invocation. Unlike pasted,
        # this is an explicit no-input attempt and can start in a new session.
        audit_record(path, "replace-no-input-session", previous)
        previous = {}

    # A manually submitted old batch may have an exact receipt even though the
    # sender was not running to retire its journal. Check it before rejecting
    # a different incoming batch; no terminal input is licensed by this check.
    if (previous and previous.get("stage") != "acknowledged"
        and previous.get("session_id") == identity and not acknowledged()
        and telemetry.get("harness") == "codex"):
        proof = native_receipt(previous, load(path / "quota-cache.json"),
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        if proof:
            atomic(path / "journal-prompt-receipt.json", proof)
    if previous and previous.get("stage") != "acknowledged" and acknowledged():
        saved("acknowledged")
    if previous and previous.get("stage") not in ("acknowledged", "retired") and closed_journal_proof(root, member, previous):
        retired, reason = retire_closed_delivery(root, member, previous, run=run)
        if not retired:
            return 3, f"closed old request awaits safe retirement: {reason}; no input was pasted"
        previous = retired
    same = previous.get("hash") == key and previous.get("ids") == ids
    if previous.get("stage") == "retired":
        if same:
            return 3, "obsolete request is retired; submission remains unproven; no input was pasted"
        audit_record(path, "replace-retired", previous)
        previous = {}
    if previous and previous.get("stage") != "acknowledged":
        # A hook can hand off the old request without submitting its terminal
        # draft. That is not a prompt ACK. Retire this obsolete transport only
        # for a different batch after verifying the current composer is empty;
        # retain its evidence and never repaste it or erase another draft.
        received = load(path / "delivery.json").get("request_receipts", [])
        if (not same and previous.get("session_id") == identity
            and previous.get("ids") and all(id in received for id in previous["ids"])):
            draft, pane = snapshot()
            if not empty(draft):
                return 3, "old request reached a hook, but the composer is not empty; no input was pasted"
            atomic(path / "retired-prompt-delivery.json", dict(previous,
                stage="retired", reason="request handed off through hook; prompt submission unproven"))
            audit_record(path, "retire-hook-handoff", previous)
            previous = {}
        if not same or previous.get("session_id") != identity:
            if previous:
                if not same:
                    return 3, "another delivery is pending; this new batch received no input; inspect the old journal"
                return 2, "another delivery is pending or the session changed; inspect its terminal"
    elif same and (ids or not was_acknowledged) and previous.get("session_id") == identity:
        return 0, "submission already acknowledged"
    else:
        if previous:
            audit_record(path, "replace-acknowledged", previous)
        previous = {}

    if not previous or previous.get("stage") == "queued":
        if busy():
            return 3, "member became working; no input was pasted"
        ready, pane = None, None
        for _ in range(40):
            ready, pane = snapshot()
            if ready is not None:
                break
            pause()
        if ready is None:
            return 3, "no ready prompt composer; no input was pasted"
        if not empty(ready):
            return 3, "composer contains an unrelated draft; no input was pasted"
        if not pane or not same_session():
            return 3, "session or pane identity changed before paste; no input was pasted"
        if previous:
            audit_record(path, "replace-queued", previous)
        previous = {"version": 2, "attempt_id": str(uuid.uuid4()), "created_ns": time.time_ns(),
            "hash": key, "ids": ids, "session_id": identity, "pane_id": pane, "receipt_before": receipt(), "stage": "queued"}
        atomic(journal, previous)
        buffer = f"hive-send-{os.getpid()}"
        try:
            command("load-buffer", "-b", buffer, "-", input=message)
        except (OSError, subprocess.CalledProcessError):
            return 3, "could not load message; no input was pasted"
        # Recheck after loading; user typing can race with buffer creation.
        draft, current_pane = snapshot()
        if not empty(draft) or current_pane != pane or busy() or not same_session():
            return 3, "composer changed before paste; no input was pasted"
        # Write before the terminal mutation: an interrupted paste is ambiguous.
        saved("pasted")
        if not same_session() or busy():
            saved("queued", no_input=True)
            return 3, "session or turn changed before paste; no input was pasted"
        try:
            command("paste-buffer", "-d", "-p", "-r", "-b", buffer, "-t", f"=hive-{member}:0.0")
        except (OSError, subprocess.CalledProcessError):
            return 2, "paste outcome is unconfirmed; inspect its terminal before retrying"
        pause(1)
        draft, current_pane = snapshot(owned=True)
        if current_pane == pane and collapsed(draft):
            saved("pasted", collapsed_view=normalized("\n".join(draft)))

    cleared = 0
    for attempt in range(40):
        if acknowledged():
            saved("acknowledged")
            return 0, "submission confirmed"
        if not same_session():
            return 2, "session changed during delivery; inspect its terminal"
        draft, pane = snapshot(owned=True)
        if not same_session():
            return 2, "session changed during composer inspection; no Enter sent"
        ours = draft is not None and (matches(draft, message) or (collapsed(draft) and previous.get("collapsed_view") == normalized("\n".join(draft))))
        if pane == previous.get("pane_id") and ours and previous.get("stage") != "accepted" and not busy():
            cleared = 0
            if attempt % 4 == 0:
                saved("submitted")
                try:
                    command("send-keys", "-t", f"=hive-{member}:0.0", "Enter")
                except (OSError, subprocess.CalledProcessError):
                    return 2, "Enter outcome is unconfirmed; inspect its terminal"
        elif pane == previous.get("pane_id") and empty(draft) and previous.get("stage") in ("submitted", "accepted"):
            cleared += 1
            if cleared >= 2:
                saved("accepted")
        else:
            cleared = 0
        pause()
    return 2, "submission unconfirmed; input " + ("accepted, processing acknowledgment pending" if previous.get("stage") == "accepted" else "retained for inspection before retrying")


def main():
    if sys.argv[1:] == ["record-receipt"]:
        member = os.environ.get("HIVE_MEMBER", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", member):
            return 0
        record_receipt(os.environ.get("HIVE_ROOT", "/srv/hive"), member, json.load(sys.stdin))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] in ("delivery-status", "delivery-reconcile", "delivery-retire-compatible"):
        root = os.environ.get("HIVE_ROOT", "/srv/hive")
        report = delivery_status(root, sys.argv[2]) if sys.argv[1] == "delivery-status" else reconcile_delivery(
            root, sys.argv[2], compatible=sys.argv[1] == "delivery-retire-compatible")
        print(json.dumps(report))
        return 2 if sys.argv[1] != "delivery-status" and report["status"] == "inspection-required" else 0
    try:
        code, detail = send(os.environ.get("HIVE_ROOT", "/srv/hive"), sys.argv[1], sys.argv[2], require_idle=os.environ.get("HIVE_DELIVERY_REQUIRE_IDLE") == "1")
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        code, detail = 2, f"transport outcome unconfirmed: {error}"
    print(f"hive-member: {detail}", file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
