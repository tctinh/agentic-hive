"""Small runtime helpers; state is inspectable JSON and never contains prompts."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o664)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def lifecycle_state(telemetry, live_pid):
    """A previous process's idle/working marker cannot describe a resumed one."""
    if not live_pid or telemetry.get("pid") != live_pid:
        return "unknown"
    status = telemetry.get("status")
    return status if status in ("working", "idle") else "unknown"


def freshness(telemetry, now=None):
    try:
        age = (time.time() if now is None else now) - datetime.fromisoformat(
            telemetry["last_activity"]
        ).timestamp()
        return "recent" if 0 <= age < 120 else "unknown"
    except (KeyError, TypeError, ValueError):
        return "unknown"


# Only these structured settings survive wake. Unknown options and positional
# text still reach the initial CLI invocation, but are never written to disk.
OPTIONS = {
    "codex": {
        "--profile": ("profile", r"[A-Za-z0-9_.-]{1,64}"),
        "-p": ("profile", r"[A-Za-z0-9_.-]{1,64}"),
        "--model": ("model", r"[A-Za-z0-9_./:-]{1,128}"),
        "-m": ("model", r"[A-Za-z0-9_./:-]{1,128}"),
        "--sandbox": ("sandbox", r"read-only|workspace-write|danger-full-access"),
        "-s": ("sandbox", r"read-only|workspace-write|danger-full-access"),
        "--ask-for-approval": ("approval", r"untrusted|on-failure|on-request|never"),
        "-a": ("approval", r"untrusted|on-failure|on-request|never"),
    },
    "claude": {
        "--model": ("model", r"[A-Za-z0-9_./:-]{1,128}"),
        "--permission-mode": (
            "permission_mode",
            r"auto|default|acceptEdits|plan|dontAsk|bypassPermissions",
        ),
        "--effort": ("effort", r"low|medium|high|max"),
    },
    "antigravity": {
        "--model": ("model", r"[A-Za-z0-9_./:() -]{1,128}"),
        "--effort": ("effort", r"low|medium|high|xhigh|max"),
        "--mode": ("mode", r"accept-edits|auto|plan"),
        "--agent": ("agent", r"[A-Za-z0-9_.-]{1,64}"),
    },
}


def settings(harness, args):
    result = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        option, separator, value = arg.partition("=")
        if option in OPTIONS.get(harness, {}):
            if not separator:
                index += 1
                value = args[index] if index < len(args) else ""
            key, pattern = OPTIONS[harness][option]
            if re.fullmatch(pattern, value):
                result[key] = value
        elif harness == "codex" and option in ("-c", "--config"):
            if not separator:
                index += 1
                value = args[index] if index < len(args) else ""
            match = re.fullmatch(
                r"model_reasoning_effort=[\"\']?(minimal|low|medium|high|xhigh)[\"\']?",
                value,
            )
            if match:
                result["reasoning_effort"] = match[1]
        index += 1
    return result


def arguments(harness, saved):
    result = []
    emitted = set()
    for option, (key, pattern) in OPTIONS.get(harness, {}).items():
        value = saved.get(key)
        if key not in emitted and isinstance(value, str) and re.fullmatch(pattern, value):
            result.extend([option, value])
            emitted.add(key)
    effort = saved.get("reasoning_effort")
    if harness == "codex" and effort in ("minimal", "low", "medium", "high", "xhigh"):
        result.extend(["-c", "model_reasoning_effort=" + effort])
    return result


def quota(root, member, payload, codex_home, now=None):
    """Nonblocking, attempt-throttled quota read, bounded by time/entries/bytes.

    A supplied transcript or a matching session ID is required: do not borrow
    another member's most recently modified transcript. Cache the resolved path.
    """
    now = time.time() if now is None else now
    directory = Path(root) / "telemetry/quota"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".codex.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        cache_path = Path(root) / "members" / member / "state/quota-cache.json"
        cache = load(cache_path)
        session = payload.get("session_id")
        if cache.get("session") == session and now - cache.get("attempted_at", 0) < 30:
            return
        supplied = payload.get("transcript_path")
        candidate = supplied or (cache.get("path") if cache.get("session") == session else None)
        path = Path(candidate) if isinstance(candidate, str) else None
        if (
            (path is None or not path.is_file())
            and isinstance(session, str)
            and re.fullmatch(r"[A-Za-z0-9-]{1,128}", session)
        ):
            deadline, count = time.monotonic() + 0.05, 0
            for base, dirs, files in os.walk(Path(codex_home) / "sessions"):
                dirs.sort(reverse=True)
                count += len(dirs) + len(files)
                if count > 2048 or time.monotonic() > deadline:
                    break
                match = next(
                    (
                        name
                        for name in files
                        if name.startswith("rollout-") and name.endswith(session + ".jsonl")
                    ),
                    None,
                )
                if match:
                    path = Path(base) / match
                    break
        atomic(
            cache_path,
            {
                "attempted_at": now,
                "session": session,
                "path": str(path) if path else None,
            },
        )
        if path is None or not path.is_file():
            return
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 256 * 1024))
            lines = stream.read(256 * 1024).splitlines()[-400:]
        for line in reversed(lines):
            try:
                data = json.loads(line).get("payload", {})
                limits = data.get("rate_limits", {})
                if data.get("type") != "token_count" or not (
                    limits.get("primary") or limits.get("secondary")
                ):
                    continue
                windows = []
                for key in ("primary", "secondary"):
                    window = limits.get(key)
                    if not window:
                        continue
                    minutes = window.get("window_minutes", 0)
                    reset = window.get("resets_at")
                    if isinstance(reset, (float, int)):
                        reset = datetime.fromtimestamp(reset, timezone.utc).isoformat()
                    windows.append(
                        {
                            "name": (
                                "5h"
                                if minutes == 300
                                else (
                                    "week"
                                    if minutes == 10080
                                    else f"{minutes // 60}h" if minutes else "limit"
                                )
                            ),
                            "used_percent": window.get("used_percent"),
                            "resets_at": reset,
                        }
                    )
                atomic(
                    directory / "codex.json",
                    {
                        "harness": "codex",
                        "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                        "reported_by": member,
                        "rate_limits": limits,
                        "plan": limits.get("plan_type"),
                        "windows": windows,
                    },
                )
                return
            except (ValueError, AttributeError, TypeError, OverflowError):
                continue


def _antigravity_group_key(group_name, bucket_id=""):
    low_name = str(group_name or "").lower()
    low_id = str(bucket_id or "").lower()
    if "gemini" in low_name or low_id.startswith("gemini"):
        return "gemini"
    if "claude" in low_name or "gpt" in low_name or low_id.startswith("3p"):
        return "claude"
    slug = re.sub(r"[^a-z0-9]+", "-", low_name).strip("-")
    return slug.split("-", 1)[0] if slug else "model"


def _antigravity_window_name(window, bucket_name=""):
    low_win = str(window or "").strip().lower()
    low_name = str(bucket_name or "").strip().lower()
    if low_win in ("5h", "5hr", "five_hour") or "five hour" in low_name or "5h" in low_name:
        return "5h"
    if low_win in ("weekly", "week", "7d", "seven_day") or "weekly" in low_name or "7d" in low_name:
        return "week"
    return low_win or low_name or "limit"


def _antigravity_window_order(win):
    return {"5h": 0, "week": 1}.get(win, 2)


def parse_antigravity_usage(payload):
    """Extract normal (Gemini) quota windows from `agy -p /usage` JSON (omitting 3p Claude/GPT)."""
    if not isinstance(payload, dict):
        return [], None
    command = payload.get("command")
    data = command.get("data") if isinstance(command, dict) else None
    groups = data.get("groups") if isinstance(data, dict) else None
    windows = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = str(group.get("name") or "").strip()
            buckets = group.get("buckets")
            if not isinstance(buckets, list):
                continue
            group_windows = []
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                gkey = _antigravity_group_key(group_name, bucket.get("id"))
                if gkey == "claude":
                    continue
                rem = bucket.get("remaining_fraction")
                if isinstance(rem, (int, float)) and not isinstance(rem, bool):
                    clamped = max(0.0, min(1.0, float(rem)))
                    remaining_percent = round(clamped * 100.0, 1)
                    used_percent = round((1.0 - clamped) * 100.0, 1)
                else:
                    remaining_percent = None
                    used_percent = None
                win = _antigravity_window_name(bucket.get("window"), bucket.get("name"))
                reset = bucket.get("reset_time") or bucket.get("resets_at")
                group_windows.append(
                    {
                        "name": f"{gkey} {win}",
                        "group": group_name or gkey,
                        "group_key": gkey,
                        "window": win,
                        "used_percent": used_percent,
                        "remaining_percent": remaining_percent,
                        "resets_at": reset if isinstance(reset, str) and reset else None,
                    }
                )
            group_windows.sort(key=lambda w: _antigravity_window_order(w["window"]))
            windows.extend(group_windows)
    if not windows and isinstance(payload.get("response"), str):
        grouped = {}
        order = []
        for line in payload["response"].splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 3:
                continue
            group_name, bucket_name, pct_text = parts[0], parts[1], parts[2]
            reset = parts[3] if len(parts) >= 4 and parts[3] else None
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", pct_text)
            if not match:
                continue
            rem_pct = max(0.0, min(100.0, float(match[1])))
            gkey = _antigravity_group_key(group_name)
            if gkey == "claude":
                continue
            win = _antigravity_window_name("", bucket_name)
            if gkey not in grouped:
                grouped[gkey] = []
                order.append(gkey)
            grouped[gkey].append(
                {
                    "name": f"{gkey} {win}",
                    "group": group_name or gkey,
                    "group_key": gkey,
                    "window": win,
                    "used_percent": round(100.0 - rem_pct, 1),
                    "remaining_percent": round(rem_pct, 1),
                    "resets_at": reset,
                }
            )
        for gkey in order:
            grouped[gkey].sort(key=lambda w: _antigravity_window_order(w["window"]))
            windows.extend(grouped[gkey])
    return windows, data


def antigravity_quota(root, reported_by, agy_bin=None, now=None, min_interval=60, timeout=15):
    """Query `agy -p /usage` for both Gemini (normal) and Claude/GPT model quotas."""
    now = time.time() if now is None else now
    agy_bin = agy_bin or shutil.which("agy")
    if not agy_bin:
        return None
    directory = Path(root) / "telemetry/quota"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".antigravity.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        cache_path = directory / ".antigravity-cache"
        cache = load(cache_path)
        if min_interval > 0 and now - cache.get("attempted_at", 0) < min_interval:
            return None
        atomic(cache_path, {"attempted_at": now, "reported_by": reported_by})
        env = os.environ.copy()
        env.pop("HIVE_MEMBER", None)
        env.pop("HIVE_AGY_SESSION", None)
        try:
            result = subprocess.run(
                [agy_bin, "-p", "/usage", "--output-format", "json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=tempfile.gettempdir(),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return None
        windows, raw_data = parse_antigravity_usage(payload)
        if not windows:
            return None
        record = {
            "harness": "antigravity",
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "reported_by": reported_by,
            "rate_limits": raw_data,
            "windows": windows,
        }
        atomic(directory / "antigravity.json", record)
        return record


def main():
    action, *args = sys.argv[1:]
    if action == "freshness":
        print(freshness(load(args[0])))
    elif action == "preflight":
        status = {}
        for line in Path("/proc/self/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("NoNewPrivs", "Seccomp", "CapEff"):
                status[key] = value.strip()
        print(
            json.dumps(
                {
                    "read_only": True,
                    "uid": os.getuid(),
                    "root": args[0],
                    "delivery_cache": {"mode": os.environ.get("HIVE_DELIVERY_CACHE", "off") or "off",
                                       "enabled": False,
                                       "supported": (os.environ.get("HIVE_DELIVERY_CACHE", "off") or "off") == "off"},
                    "message_metadata_authority": "messages.jsonl",
                    "tools": {
                        name: shutil.which(name) for name in ("tmux", "claude", "codex", "nix", "bwrap")
                    },
                    "process_restrictions": status,
                    "live_sandbox_probe": "unrun; inspect service namespace and filesystem restrictions as the owner",
                },
                indent=2,
            )
        )
    elif action == "atomic-json":
        atomic(args[0], json.load(sys.stdin))
    elif action == "save-launch":
        path, harness, directory, *flags = args
        atomic(
            path,
            {
                "harness": harness,
                "dir": directory,
                "launched_at": datetime.now(timezone.utc).isoformat(),
                "settings": settings(harness, flags),
            },
        )
    elif action == "launch-args":
        record = load(args[0])
        for arg in arguments(record.get("harness"), record.get("settings", {})):
            sys.stdout.write(arg + "\0")
    elif action == "quota":
        quota(
            args[0],
            args[1],
            json.load(sys.stdin),
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        )
    elif action == "antigravity-quota":
        antigravity_quota(
            args[0],
            args[1],
            min_interval=0 if "--force" in args[2:] else 60,
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        sys.exit(f"hive-runtime: {error}")
