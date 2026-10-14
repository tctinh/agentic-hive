"""Guard one tmux prompt handoff; callers hold the member's .prompt.lock.

The small journal survives interruption between paste, Enter and the prompt hook.
It never licenses input into another draft or treats a composer reset as an ACK.
"""
import hashlib
import json
import os
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
    return bool(isinstance(proof, dict) and proof.get("event") == "UserPromptSubmit"
        and proof.get("binding") == receipt_binding(record)
        and receipt_acknowledges(record, proof.get("receipt", ""), session_id))


def normalized(text):
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def trailing_footer(rows, cursor):
    """Recognize only a separated status row at the terminal's bottom edge."""
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].strip()), -1)
    if last <= cursor or last < len(rows) - 3 or not last or rows[last - 1].strip():
        return None
    status = r"^ {2,}(?:[^\n]*?\s)?(?:100|[0-9]{1,2})% context left(?:\s*[·•|].*)?\s*$"
    shortcuts = r"^ {2,}\? for shortcuts(?:\s*[·•|]\s*(?:100|[0-9]{1,2})% context left)?\s*$"
    return last if re.fullmatch(status, rows[last]) or re.fullmatch(shortcuts, rows[last]) else None


def composer(rows, cursor, *, owned=False, expected=None):
    """Read only the cursor's contiguous composer, bounded by non-input rows.

    Empty rows are continuations only when a prior empty-composer paste is
    journaled. A separated trailing footer can end the input only after the
    complete expected draft matches. Other below-cursor text remains input.
    """
    if not 0 <= cursor < len(rows):
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
    identity = load(Path(root) / "telemetry/members" / f"{member}.json").get("session_id")
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
    if previous and previous.get("stage") != "acknowledged" and acknowledged():
        saved("acknowledged")
    same = previous.get("hash") == key
    if previous and previous.get("stage") != "acknowledged":
        if not same or previous.get("session_id") != identity:
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
