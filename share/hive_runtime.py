"""Small runtime helpers; state is inspectable JSON and never contains prompts."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
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


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        sys.exit(f"hive-runtime: {error}")
