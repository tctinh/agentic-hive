"""Member names and team labels over existing nests; explicit project and team membership."""
import argparse
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

HANDLE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def profiles(root):
    directory = Path(root) / "members"
    result = []
    for nest in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not nest.is_dir() or nest.name == "beekeeper" or not HANDLE.fullmatch(nest.name):
            continue
        try:
            value = json.loads((nest / "state/profile.json").read_text())
            if not isinstance(value, dict):
                value = {}
        except (OSError, ValueError):
            value = {}
        name, team, workdir = value.get("name", nest.name), value.get("team"), value.get("workdir")
        project = value.get("project")
        result.append({"member": nest.name,
                       "name": name if isinstance(name, str) and HANDLE.fullmatch(name) else nest.name,
                       "project": project if isinstance(project, str) and HANDLE.fullmatch(project) else None,
                       "team": team if isinstance(team, str) and HANDLE.fullmatch(team) else None,
                       "workdir": workdir if isinstance(workdir, str) and workdir.startswith("/") else None})
    return result


def resolve(root, name):
    if not isinstance(name, str) or not HANDLE.fullmatch(name):
        raise ValueError("Valid member name required")
    matches = {member["member"] for member in profiles(root) if name in (member["member"], member["name"])}
    if len(matches) != 1:
        raise ValueError(f"{'Unknown' if not matches else 'Ambiguous'} member: {name}")
    return matches.pop()


def index(root):
    members = profiles(root)
    teams = {}
    for member in members:
        if member["team"] and not member.get("project"):
            teams.setdefault(member["team"], []).append(member["member"])
    return {"members": members, "teams": teams}


def handle(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip().lower()
    if value.endswith("."):
        raise ValueError(f"{label} cannot end in a dot; Room mentions treat it as punctuation")
    if not HANDLE.fullmatch(value) or value in ("all", "beekeeper") or value.startswith("beekeeper-"):
        raise ValueError(f"{label}: use 1–64 lowercase letters, digits, dots, underscores or hyphens; human names and @all are reserved")
    return value


def update(root, member, *, name=None, team=None, workdir=None, project=None):
    root = Path(root)
    with (root / ".members.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        member = resolve(root, member)
        members = profiles(root)
        profile = next(row for row in members if row["member"] == member)
        if name is not None:
            name = handle(name, "Name")
            if any(row["member"] != member and name in (row["member"], row["name"]) for row in members):
                raise ValueError("That name belongs to another member")
            if any(row["team"] == name and not row.get("project") for row in members):
                raise ValueError("That name is already a team tag")
            profile["name"] = name
        if project is not None:
            profile["project"] = handle(project, "Project") if project.strip() else None
        if team is not None:
            if not isinstance(team, str):
                raise ValueError("Team must be text")
            team = handle(team, "Team") if team.strip() else None
            if team and not profile.get("project") and any(team in (row["member"], row["name"]) for row in members):
                raise ValueError("That team tag belongs to a member")
            # A name and team cannot collide within the same edit either.
            if team and not profile.get("project") and team == profile["name"]:
                raise ValueError("Member name and team tag must differ")
            profile["team"] = team
        if workdir is not None:
            if not isinstance(workdir, str):
                raise ValueError("Working folder must be text")
            workdir = workdir.strip()
            if workdir and (not Path(workdir).is_absolute() or not Path(workdir).is_dir()):
                raise ValueError("Working folder must be an existing absolute directory")
            profile["workdir"] = str(Path(workdir).resolve()) if workdir else None
        path = root / "members" / member / "state/profile.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".profile-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({key: profile[key] for key in ("name", "team", "workdir", "project")}, stream)
            os.chmod(temporary, 0o664)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        from hive_messages import sync_profile
        sync_profile(root, member)
        return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "resolve", "update"))
    parser.add_argument("member", nargs="?")
    parser.add_argument("--name")
    parser.add_argument("--team")
    parser.add_argument("--project")
    parser.add_argument("--dir", dest="workdir")
    args = parser.parse_args()
    root = Path(os.environ.get("HIVE_ROOT", "/srv/hive"))
    try:
        if args.command == "resolve":
            print(resolve(root, args.member))
        else:
            print(json.dumps(index(root) if args.command == "list" else
                             update(root, args.member, name=args.name, team=args.team, workdir=args.workdir, project=args.project)))
    except (ValueError, OSError) as error:
        parser.exit(1, f"hive-member: {error}\n")


if __name__ == "__main__":
    main()
