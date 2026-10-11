#!/usr/bin/env python3
"""Show Hive's real dashboard with isolated, read-only documentation data."""
import argparse
from datetime import datetime, timedelta
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlparse


def create_demo(scratch):
    repo = Path(__file__).resolve().parents[2]
    loader = importlib.machinery.SourceFileLoader("hive_demo_web", str(repo / "bin/hive-web"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    web = importlib.util.module_from_spec(spec)
    loader.exec_module(web)
    root = scratch / "habitat"
    share = scratch / "share"
    share.mkdir(parents=True)
    web.HIVE_ROOT, web.SHARE = root, share
    for name in ("dashboard.html", "xterm.js", "xterm.css"):
        shutil.copyfile(repo / "share" / name, share / name)
    bundle = root / "artifacts/devlogs/debter/drowned-chunk-playground"
    shutil.copytree(Path(__file__).parent / "debter-playground", bundle)
    project = root / "projects/debter-playground"
    project.mkdir(parents=True)
    (project / "CURRENT_DIRECTION.md").write_text(
        "# Current direction — documentation example\n\n"
        "- Explore, edit terrain, equip real cards, and try a horde in an isolated playground.\n"
        "- Keep campaign saves separate. The prototype remains on a review branch.\n"
        "- Native captures show rendering; human playtesting still decides feel.\n"
    )
    shutil.copytree(bundle, project / "artifacts/playground")

    page = (share / "dashboard.html").read_text()
    disclosure = '<p class="meta" style="margin:0 0 16px">Documentation demo · Fictional members and Room messages · Real Debter prototype captures</p>'
    page = page.replace('<header>', disclosure + '<header>', 1)
    page = page.replace('<h2>Room <span', '<h2>Room · fictional handoffs <span', 1)
    (share / "dashboard.html").write_text(page)
    now = datetime.now().astimezone()

    def ago(minutes):
        return (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    # Paraphrases of the delivered playground's Room handoffs, not a transcript.
    posts = [
        (1, "beekeeper", "@debter Build an isolated playground: explore chunks, edit terrain, equip real cards, and try a horde. Keep campaign saves safe."),
        (2, "nova", "Seeded chunks are ready. @lark The collision and navigation masks must agree before we check the horde."),
        (3, "cedar", "Catalogue grants now create owned card copies. @nova Continue must restore those copies and terrain edits together."),
        (4, "lark", "Native click check found the old Calendar header intercepting New World. Fixing that before calling this playable."),
        (5, "nova", "The input fix is in. Saved terrain edits survive Continue in the isolated profile; campaign saves stay separate."),
        (6, "cedar", "Ember Pin casts through the live deck. The native capture shows the equipped card in combat, rather than only a catalogue preview."),
        (7, "lark", "Scheduled enemies reach the debug defense objective using the generated masks. This is scripted native evidence; balance and feel still need a player."),
        (8, "nova", "@beekeeper One consolidated devlog is ready with gameplay captures and remaining limits. Claims released; the prototype is on its review branch, unmerged."),
    ]
    room = [dict(gen=g, member=m, ts=ago((9-g)*4), body=b) for g, m, b in posts]

    def member(name, harness, last):
        return dict(member=name, name=name, team="debter", state="idle", harness=harness,
                    pid=None, started_at=ago(90), last_activity=ago(4),
                    cwd="/srv/hive/projects/debter-playground", cwd_short="projects/debter-playground",
                    git_branch="prototype/drowned-chunk-playground", last_tool="Bash",
                    undelivered=0, notes=[], last_room=room[last])

    snapshot = dict(
        hive_root="/srv/hive", now=now.isoformat(timespec="seconds"), generation=8,
        viewer=dict(member="beekeeper", name="Creator", named=True),
        beekeepers=[dict(member="beekeeper", name="Creator")],
        beekeeper_mentions=[room[-1]],
        host=dict(hostname="demo-host", cpu_percent=12, cpus=8, load=[0.42, 0.61, 0.74],
                  mem_total=16*1024**3, mem_used=6*1024**3,
                  disk_total=120*1024**3, disk_used=31*1024**3, uptime=74000),
        members=[member("nova", "codex", 7), member("cedar", "claude", 5), member("lark", "codex", 6)],
        claims=[], quota=[], room=room,
        projects=[dict(name="debter-playground", path="/srv/hive/projects/debter-playground",
                       branch="prototype/drowned-chunk-playground", staged=0, modified=0,
                       untracked=0, ahead=None, behind=None,
                       commit=dict(hash="d5a4eac9", date=ago(5), subject="Bind prototype delivery to native evidence"))],
    )
    web.state = lambda viewer=None: snapshot

    class ReadOnlyHandler(web.Handler):
        def do_GET(self):
            if urlparse(self.path).path.startswith("/ws/"):
                self._send(405, "This demo has no live sessions.\n", "text/plain")
                return
            super().do_GET()

        def do_POST(self):
            self._send(405, "Read-only documentation demo.\n", "text/plain")

    web.Handler = ReadOnlyHandler
    return web


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8197)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="hive-game-demo.") as directory:
        web = create_demo(Path(directory))
        server = web.ThreadingHTTPServer(("127.0.0.1", args.port), web.Handler)
        print(f"Read-only Hive demo: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
