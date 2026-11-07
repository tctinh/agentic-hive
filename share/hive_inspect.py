"""Bounded, read-only operational view. No handoff, acknowledgment or recovery."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import hive_members
import hive_messages
import hive_prompt
from hive_runtime import lifecycle_state, load


def web_snapshot(url):
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/state", timeout=10) as response:
            value = json.load(response)
            if not isinstance(value, dict) or not isinstance(value.get("members"), list):
                raise ValueError("dashboard returned no member list")
            return value, {"url": url, "ok": True, "seconds": round(time.monotonic() - started, 3),
                "runtime": value.get("runtime")}
    except (OSError, ValueError, urllib.error.URLError) as error:
        return {}, {"url": url, "ok": False, "error": str(error)}


def object_state(path):
    value = load(path)
    if not isinstance(value, dict):
        raise ValueError(path.name + " must contain a JSON object")
    return value


def identity_list(record, field):
    value = record.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(field + " must contain a list of IDs")
    return value


def delivery_records(path):
    journal = object_state(path / "prompt-delivery.json")
    if not journal:
        marker = object_state(path / "last-retired-prompt-delivery.json")
        retirement = marker.get("retirement", {})
        if not isinstance(retirement, dict):
            raise ValueError("retirement must contain an object")
        if marker.get("stage") == "retired" and retirement.get("mode") == "legacy-empty-active":
            journal = marker
    return journal, object_state(path / "pending-wake.json")


def unreadable_delivery():
    return {"review_reason": "delivery state unreadable", "journal_stage": None,
        "journal_ids": [], "queued_count": None, "uncertain_attempt": True,
        "no_input": False, "compatible_retired": False}


def delivery_summary(journal, pending):
    ids = identity_list(journal, "ids")
    queued = identity_list(pending, "ids")
    stage = journal.get("stage")
    version = journal.get("version", 1)
    retirement = journal.get("retirement", {})
    if (type(version) is not int or version < 1 or not isinstance(retirement, dict)
        or stage not in (None, "queued", "pasted", "submitted", "accepted", "acknowledged", "retired")
        or type(pending.get("attempted", False)) is not bool):
        raise ValueError("invalid journal version, stage, retirement or attempt flag")
    no_input = stage == "queued"
    if no_input or stage in (None, "acknowledged", "retired"):
        reason = None
    elif not journal.get("session_id"):
        reason = "missing journal session identity"
    elif not ids:
        reason = "direct submission is unconfirmed" if version >= 2 else "legacy direct attempt without IDs"
    else:
        reason = "earlier submission is unconfirmed"
    return {"journal_stage": stage, "journal_ids": ids,
        "review_reason": reason, "queued_count": len(queued),
        "uncertain_attempt": bool(pending.get("attempted")), "no_input": no_input,
        "compatible_retired": stage == "retired" and retirement.get("mode") == "legacy-empty-active"}


def member_delivery(path):
    try:
        return delivery_summary(*delivery_records(path))
    except (OSError, ValueError, TypeError, AttributeError):
        return unreadable_delivery()


def snapshot(root, *, member=None, include_all=False, limit=3, web=None, health=None):
    root = Path(root)
    web = web or {}
    errors = []
    try:
        rows = hive_messages.records(root)
        requests, closed = hive_messages.request_status(rows)
    except (OSError, ValueError) as error:
        rows, requests, closed = [], {}, {}
        errors.append("canonical history unavailable: " + str(error))
    live = {m["member"]: m for m in web.get("members", [])}
    profiles = hive_members.profiles(root)
    if member:
        member = hive_members.resolve(root, member)
    result, offline = [], []
    for profile in profiles:
        name = profile["member"]
        if member and member != name:
            continue
        path = root / "members" / name / "state"
        try:
            telemetry = object_state(root / "telemetry/members" / (name + ".json"))
            journal, pending = delivery_records(path)
            ledger = object_state(path / "delivery.json")
            proof = object_state(path / "journal-prompt-receipt.json")
            identity_list(ledger, "request_receipts")
            delivery = delivery_summary(journal, pending)
            submission_proven = bool(journal and (hive_prompt.proof_acknowledges(journal, proof, journal.get("session_id"))
                or hive_prompt.current_receipt_acknowledges(journal, path, journal.get("session_id"))))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append(name + ": " + str(error))
            telemetry, journal, pending, ledger, proof = {}, {}, {}, {}, {}
            delivery = unreadable_delivery()
            submission_proven = False
        current = live.get(name, {})
        running = current.get("running")
        state = lifecycle_state(telemetry, current.get("pid")) if running is True else (
            current.get("state", "unknown") if running is False else "unknown")
        binding = "current-process" if running and telemetry.get("pid") == current.get("pid") else (
            "previous-process" if running else "unavailable")
        delivery["submission_proven"] = submission_proven
        ids = journal.get("ids", [])
        delivery["closed_request_retirement_candidate"] = bool(journal.get("stage") not in (None, "acknowledged", "retired")
            and isinstance(ids, list) and ids and all(isinstance(identity, str) for identity in ids)
            and len(ids) == len(set(ids)) and re.fullmatch(r"[0-9a-f]{64}", str(journal.get("hash", ""))) and all(
            identity in requests and name in requests[identity].get("recipients", [])
            and name in closed[identity] for identity in ids))
        receipts = set(ledger.get("request_receipts", []))
        assigned = [r for r in rows if r["kind"] == "request" and name in r.get("recipients", [])]
        recent = [{"sequence": r["sequence"], "id": r["id"],
            "handoff": "recorded" if r["id"] in receipts else "not recorded; unknown",
            "submission": "proven in journal session" if submission_proven and r["id"] in journal.get("ids", []) else "not established",
            "request_closed": name in closed[r["id"]], "text": r["body"][:320]}
            for r in assigned[-limit:]]
        posts = [r for r in rows if r["sender"] == name]
        last = posts[-1] if posts else None
        item = {"member": name, "name": profile.get("name", name), "state": state,
            "running": running, "live_pid": current.get("pid") if running else None,
            "telemetry_pid": telemetry.get("pid"), "telemetry_binding": binding,
            "recorded_state": telemetry.get("status"), "last_hook_activity": telemetry.get("last_activity"),
            "last_hook_event": telemetry.get("last_event"), "delivery": delivery,
            "latest_requests": recent,
            "last_post": {"sequence": last["sequence"], "kind": last["kind"],
                "reply_to": last.get("reply_to"), "text": last["body"][:320]} if last else None}
        if include_all or member or running is True:
            result.append(item)
        elif delivery["queued_count"] or delivery["review_reason"] or delivery["no_input"]:
            offline.append({"member": name, "queued_count": delivery["queued_count"],
                "review_reason": delivery["review_reason"], "running": running})
    return {"at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
        "hive_root": str(root), "web": health or {"ok": None, "note": "web not queried"},
        "generation": rows[-1]["sequence"] if rows else None, "members": result,
        "offline_pending": offline, "errors": errors,
        "note": "Request handoff, a member reply and task completion are separate evidence. No input or recovery was attempted."}


def render(report):
    web = report["web"]
    lines = ["Hive inspection · " + report["at_utc"], "Read only; no message, acknowledgment or recovery attempted."]
    lines.append("Dashboard: " + ("responding" if web.get("ok") else "unavailable" if web.get("ok") is False else "not queried")
        + (" · " + web["error"] if web.get("error") else ""))
    for item in report["members"]:
        lines.append("\n" + item["member"] + ": " + ("running · " if item["running"] else "") + item["state"])
        if item["telemetry_binding"] == "previous-process":
            lines.append("  Activity unknown: lifecycle telemetry belongs to a previous process.")
        lines.append("  Last hook: " + str(item["last_hook_activity"]) + " · " + str(item["last_hook_event"]))
        delivery = item["delivery"]
        lines.append("  Queue: " + str(delivery["queued_count"]) + " · journal: " + str(delivery["journal_stage"]))
        if delivery["no_input"]:
            lines.append("  Journal queued; no input was pasted by this attempt.")
        if delivery["compatible_retired"]:
            lines.append("  Closed requests retired for compatibility; active journal empty; submission remains unproven.")
        if delivery["review_reason"]:
            lines.append("  Delivery review: " + delivery["review_reason"])
        if delivery["closed_request_retirement_candidate"]:
            lines.append("  Old journal requests are closed; audited retirement still requires a quiet empty target.")
        for request in item["latest_requests"]:
            lines.append(f"  Request #{request['sequence']}: handoff {request['handoff']}; submission {request['submission']}; closed={request['request_closed']} · {request['id']}")
        if item["last_post"]:
            last = item["last_post"]
            lines.append(f"  Last post #{last['sequence']} ({last['kind']}): " + " ".join(last["text"].split()))
    if report["offline_pending"]:
        lines.append("\nOffline/unverified members with retained queues/journals: " + str(len(report["offline_pending"])) + ". Use --all for details.")
    lines.extend("Error: " + error for error in report["errors"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("member", nargs="?")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--url", default=os.environ.get("HIVE_WEB_URL", "http://127.0.0.1"))
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.limit <= 12:
        parser.error("--limit must be 1–12")
    web, health = ({}, None) if args.no_web else web_snapshot(args.url)
    try:
        report = snapshot(os.environ.get("HIVE_ROOT", "/srv/hive"), member=args.member,
            include_all=args.all, limit=args.limit, web=web, health=health)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 2 if report["errors"] or (health and health.get("ok") is False) else 0


if __name__ == "__main__":
    raise SystemExit(main())
