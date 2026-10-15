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
    return int(fields[0]) > (int(prior[0]) if prior else 0)


def receipt_binding(record):
    return {name: record.get(name) for name in ("hash", "ids", "session_id", "receipt_before")}


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
    if not sid or not ids or cache.get("session") != sid:
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
                for _ in range(80):
                    offset = data.rfind(ids[0].encode(), start, end)
                    if offset < 0:
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


def send(root, member, message, *, run=subprocess.run, require_idle=False):
    path = Path(root) / "members" / member / "state"
    journal = path / "prompt-delivery.json"
    receipt_file = path / "last-submitted-prompt"
    telemetry = load(Path(root) / "telemetry/members" / f"{member}.json")
    identity = telemetry.get("session_id")
    key = prompt_hash(message)
    ids = re.findall(r"^\[hive-message id=([A-Za-z0-9._:-]+)\]", message, re.M)
    previous = load(journal)

    def receipt():
        return receipt_file.read_text().strip() if receipt_file.exists() else ""

    def busy():
        return require_idle and load(Path(root) / "telemetry/members" / f"{member}.json").get("status") == "working"

    def command(*args, input=None):
        return run(["tmux", *args], input=input, text=True, capture_output=True, check=True)

    def pause(seconds=0.25):
        run(["sleep", str(seconds)], check=True)

    def snapshot(owned=False):
        metadata = command("display-message", "-p", "-t", f"=hive-{member}:0.0", "#{cursor_y} #{pane_in_mode} #{pane_id}").stdout.strip().split()
        if not metadata or not metadata[0].isdigit() or (len(metadata) > 1 and metadata[1] != "0"):
            return None, None
        screen = command("capture-pane", "-p", "-t", f"=hive-{member}:0.0").stdout.splitlines()
        # Reject cursor/focus/mode changes between the separate tmux reads.
        if command("display-message", "-p", "-t", f"=hive-{member}:0.0", "#{cursor_y} #{pane_in_mode} #{pane_id}").stdout.strip().split() != metadata:
            return None, None
        return composer(screen, int(metadata[0]), owned=owned, expected=message if owned else None), metadata[-1] if len(metadata) > 2 else None

    def saved(stage, **extra):
        previous.update(stage=stage, **extra)
        atomic(journal, previous)

    def acknowledged():
        active = load(Path(root) / "telemetry/members" / f"{member}.json").get("session_id")
        return receipt_acknowledges(previous, receipt(), active) or proof_acknowledges(
            previous, load(path / "journal-prompt-receipt.json"), active)

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
    same = previous.get("hash") == key
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
            previous = {}
        if not same or previous.get("session_id") != identity:
            if previous:
                return 2, "another delivery is pending or the session changed; inspect its terminal"
    elif same and ids and previous.get("ids") == ids and previous.get("session_id") == identity:
        return 0, "submission already acknowledged"
    else:
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
        previous = {"hash": key, "ids": ids, "session_id": identity, "pane_id": pane, "receipt_before": receipt(), "stage": "queued"}
        atomic(journal, previous)
        buffer = f"hive-send-{os.getpid()}"
        try:
            command("load-buffer", "-b", buffer, "-", input=message)
        except (OSError, subprocess.CalledProcessError):
            return 3, "could not load message; no input was pasted"
        # Recheck after loading; user typing can race with buffer creation.
        draft, current_pane = snapshot()
        if not empty(draft) or current_pane != pane or busy():
            return 3, "composer changed before paste; no input was pasted"
        # Write before the terminal mutation: an interrupted paste is ambiguous.
        saved("pasted")
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
        if load(Path(root) / "telemetry/members" / f"{member}.json").get("session_id") != previous.get("session_id"):
            return 2, "session changed during delivery; inspect its terminal"
        draft, pane = snapshot(owned=True)
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
    try:
        code, detail = send(os.environ.get("HIVE_ROOT", "/srv/hive"), sys.argv[1], sys.argv[2], require_idle=os.environ.get("HIVE_DELIVERY_REQUIRE_IDLE") == "1")
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        code, detail = 2, f"transport outcome unconfirmed: {error}"
    print(f"hive-member: {detail}", file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
