"""Merge/remove Hive hooks without replacing a harness's other settings."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile


def command_id(hook):
    try:
        words = shlex.split(hook.get("command", ""))
    except (ValueError, AttributeError):
        return None
    if len(words) == 3 and Path(words[0]).name == "hive-hook":
        return tuple(words[1:])
    return None


def prune(entries, owned):
    """Remove only our commands, including from mixed matcher groups."""
    kept = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("hook entries must be objects")
        if "hooks" in entry:
            children = entry["hooks"]
            if not isinstance(children, list):
                raise ValueError("nested hooks must be arrays")
            children = [h for h in children if command_id(h) not in owned]
            if children:
                kept.append(dict(entry, hooks=children))
        elif command_id(entry) not in owned:
            kept.append(entry)
    return kept


def merge(current, desired, remove=False):
    if not isinstance(current, dict) or not isinstance(desired, dict):
        raise ValueError("settings must be JSON objects")
    result = copy.deepcopy(current)
    # OpenHands uses event arrays at the root; other harnesses use `hooks`.
    template = desired.get("hooks", desired)
    if not isinstance(template, dict):
        raise ValueError("hooks must be an object")
    target = result.setdefault("hooks", {}) if "hooks" in desired else result
    if not isinstance(target, dict):
        raise ValueError("hooks must be an object")
    for event, additions in template.items():
        if not isinstance(additions, list):
            continue
        owned = {command_id(h) for e in additions for h in e.get("hooks", [e])}
        owned.discard(None)
        existing = target.get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"{event} hooks must be an array")
        entries = prune(existing, owned)
        if not remove:
            entries += copy.deepcopy(additions)
        if entries:
            target[event] = entries
        else:
            target.pop(event, None)
    if "hooks" in desired and not target:
        result.pop("hooks", None)
    # Keep an administrator's status line. Only remove ours on uninstall.
    if "statusLine" in desired:
        if remove:
            if result.get("statusLine") == desired["statusLine"]:
                result.pop("statusLine", None)
        else:
            result.setdefault("statusLine", desired["statusLine"])
    if not remove and "version" in desired:
        if result.get("version", desired["version"]) != desired["version"]:
            raise ValueError("unsupported existing hooks version")
        result.setdefault("version", desired["version"])
    return result


def plugin_entries(current, directory):
    """Register Hive's Antigravity plugin directory in a plugins.json.

    Earlier Hive entries (another install prefix or package path) are replaced;
    every other entry and key is preserved.
    """
    if not isinstance(current, dict):
        raise ValueError("plugins.json must be a JSON object")
    result = copy.deepcopy(current)
    entries = result.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("plugins.json entries must be an array")
    ours = str(Path(directory))
    kept = [e for e in entries if not (isinstance(e, dict) and isinstance(e.get("path"), str)
            and (e["path"] == ours or e["path"].rstrip("/").endswith("/agentic-hive/antigravity")))]
    result["entries"] = kept + [{"path": ours}]
    return result


def trusted(current, directory):
    """Add a launch directory to Antigravity's trusted workspaces."""
    if not isinstance(current, dict):
        raise ValueError("settings must be a JSON object")
    result = copy.deepcopy(current)
    workspaces = result.get("trustedWorkspaces", [])
    if not isinstance(workspaces, list):
        raise ValueError("trustedWorkspaces must be an array")
    if directory not in workspaces:
        result["trustedWorkspaces"] = workspaces + [directory]
    return result


def update(path, desired, remove=False, transform=None):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".hive.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = path.stat() if path.exists() else None
        current = json.loads(path.read_text()) if previous else {}
        result = transform(current) if transform else merge(current, desired, remove)
        if previous and result == current:
            return
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), stat.S_IMODE(previous.st_mode) if previous else 0o600)
                if previous and os.geteuid() == 0:
                    os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("merge", "remove", "antigravity-plugin", "antigravity-trust"))
    parser.add_argument("path")
    parser.add_argument("directory", nargs="?")
    args = parser.parse_args()
    if args.action.startswith("antigravity-") and not args.directory:
        parser.error(f"{args.action} requires a directory")
    try:
        import sys
        if args.action == "antigravity-plugin":
            update(args.path, None, transform=lambda current: plugin_entries(current, args.directory))
        elif args.action == "antigravity-trust":
            update(args.path, None, transform=lambda current: trusted(current, args.directory))
        else:
            update(args.path, json.load(sys.stdin), args.action == "remove")
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"hive-config: {error}; existing settings left intact\n")
