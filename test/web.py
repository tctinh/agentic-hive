#!/usr/bin/env python3
"""Focused dashboard control tests; no live sessions or Room are touched."""
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
source = Path(__file__).resolve().parents[1] / "bin/hive-web"
loader = importlib.machinery.SourceFileLoader("hive_web_test", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
web = importlib.util.module_from_spec(spec)
loader.exec_module(web)


class DashboardControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in ("alice", "bob", "beekeeper"):
            (self.root / "members" / member).mkdir(parents=True)
        self.root_patch = patch.object(web, "HIVE_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        sender = self.root / "send-member"
        sender.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$2" >>"$HIVE_ROOT/sent-targets"
printf '%s' "$3" >"$HIVE_ROOT/sent-$2"
if [[ $2 == ${HIVE_TEST_FAIL_MEMBER:-} ]]; then echo 'member unavailable' >&2; exit 1; fi
if [[ $2 == ${HIVE_TEST_UNCONFIRMED_MEMBER:-} ]]; then echo 'submission unconfirmed; inspect terminal before resending' >&2; exit 2; fi
''')
        sender.chmod(0o755)
        env = patch.dict(os.environ, {"HIVE_MEMBER_BIN": str(sender)})
        env.start()
        self.addCleanup(env.stop)
        web.run_hive("hive", "init")

    def request(self, path, payload, origin="http://hive.local", cookie=None):
        handler = object.__new__(web.Handler)
        body = json.dumps(payload).encode()
        handler.path = path
        handler.headers = {"Content-Length": str(len(body)), "Origin": origin, "Host": "hive.local"}
        if cookie:
            handler.headers["Cookie"] = cookie
        handler.rfile = io.BytesIO(body)
        responses = []
        handler._send = lambda status, data, _, *headers: responses.append((status, json.loads(data)))
        handler.do_POST()
        return responses[0]

    def get_json(self, path, cookie=None):
        handler = object.__new__(web.Handler)
        handler.path = path
        handler.headers = {"Cookie": cookie} if cookie else {}
        handler._send = lambda status, data, _: responses.append((status, json.loads(data)))
        responses = []
        handler.do_GET()
        return responses[0]

    def test_room_all_posts_once_and_sends_to_every_agent(self):
        status, data = self.request("/api/room", {"message": "@all please check status"})
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], ["alice", "bob"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")
        self.assertEqual(sorted((self.root / "sent-targets").read_text().splitlines()), ["alice", "bob"])
        self.assertIn("sender_authority=owner", (self.root / "sent-alice").read_text())

    def test_cli_room_mentions_prompt_peers_and_skip_sender_and_beekeeper(self):
        data = json.loads(web.run_hive("hive", "say", "--json", "@all @alice @bob. check the interface", member="alice"))
        self.assertEqual(data["sent"], ["bob"])
        self.assertEqual((self.root / "sent-targets").read_text().splitlines(), ["bob"])
        self.assertIn("sender_authority=peer", (self.root / "sent-bob").read_text())

    def test_room_reports_partial_delivery_without_losing_or_reposting_message(self):
        with patch.dict(os.environ, {"HIVE_TEST_FAIL_MEMBER": "bob"}):
            status, data = self.request("/api/room", {"message": "@all check this"})
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], ["alice"])
        self.assertIn("member unavailable", data["failed"]["bob"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")

    def test_direct_message_does_not_deliver_its_room_record_twice(self):
        web.run_hive("hive", "message", "bob", "@bob check this", member="alice")
        self.assertEqual((self.root / "sent-targets").read_text().splitlines(), ["bob"])

    def test_unconfirmed_submission_is_separate_from_failed_delivery(self):
        with patch.dict(os.environ, {"HIVE_TEST_UNCONFIRMED_MEMBER": "bob"}):
            status, data = self.request("/api/room", {"message": "@all check this"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["sent"], ["alice"])
        self.assertEqual(data["failed"], {})
        self.assertIn("inspect terminal", data["unconfirmed"]["bob"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")
        self.assertEqual(sorted((self.root / "sent-targets").read_text().splitlines()), ["alice", "bob"])

    def test_room_only_and_human_mentions_do_not_prompt(self):
        report = json.loads(web.run_hive("hive", "say", "--json", "--room-only", "Quoting @all and @unknown", member="alice"))
        self.assertEqual(report["sent"], [])
        report = json.loads(web.run_hive("hive", "say", "--json", "@beekeeper please choose a direction", member="alice"))
        self.assertEqual(report["sent"], [])
        self.assertFalse((self.root / "sent-targets").exists())

    def test_plain_room_post_uses_hive_cli(self):
        with patch.object(web, "run_hive", wraps=web.run_hive):
            web.run_hive("hive", "init")
            status, data = self.request("/api/room", {"message": "A note for the Room"})
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], [])
        room = (self.root / "ROOM.md").read_text()
        self.assertIn("member=beekeeper", room)
        self.assertIn("A note for the Room", room)

    def test_unknown_mention_never_posts(self):
        status, data = self.request("/api/room", {"message": "@missing do work"})
        self.assertEqual(status, 400)
        self.assertIn("unknown member", data["error"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "0")
        self.assertFalse((self.root / "sent-targets").exists())

    def test_delivery_uses_member_state_when_external_temp_directory_is_unavailable(self):
        blocked = self.root / "not-a-directory"
        blocked.write_text("blocked")
        with patch.dict(os.environ, {"TMPDIR": str(blocked)}):
            status, data = self.request("/api/room", {"message": "@alice please check this"})
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], ["alice"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")
        self.assertIn("please check this", (self.root / "ROOM.md").read_text())

    def test_lifecycle_buttons_execute_session_commands(self):
        with patch.object(web, "run_hive", return_value="ok") as run:
            status, _ = self.request("/api/member", {"member": "alice", "action": "restart"})
        self.assertEqual(status, 200)
        run.assert_called_once_with("hive-member", "restart", "alice", "--force", "--no-attach")
        with patch.object(web, "run_hive", return_value="ok") as run:
            status, _ = self.request("/api/member", {"member": "alice", "action": "delete"})
        self.assertEqual(status, 200)
        run.assert_called_once_with("hive-member", "delete", "alice", "--force")

    def test_live_pane_overrides_stale_telemetry_pid(self):
        values = {"/proc/100/comm": "bash", "/proc/100/task/100/children": "101",
                  "/proc/101/comm": ".codex-wrapped", "/proc/101/task/101/children": ""}
        with patch.object(web, "read", side_effect=lambda path, default="": values.get(str(path), default)):
            self.assertEqual(web.pane_harness_pid(100, "codex"), 101)
        telemetry = self.root / "telemetry/members/alice.json"
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        telemetry.write_text(json.dumps({"harness": "codex", "pid": 99999, "status": "idle"}))
        with patch.object(web, "member_panes", return_value={"alice": 100}), \
             patch.object(web, "pane_harness_pid", return_value=101):
            alice = next(m for m in web.members(0, 0, []) if m["member"] == "alice")
        self.assertEqual((alice["state"], alice["pid"]), ("idle", 101))

    def test_cross_origin_command_is_rejected(self):
        with patch.object(web, "run_hive") as run:
            status, _ = self.request("/api/member", {"member": "alice", "action": "kill"}, "http://other.site")
        self.assertEqual(status, 403)
        run.assert_not_called()

    def test_stale_working_turn_keeps_guard_state_and_exposes_unknown_freshness(self):
        path=self.root/'telemetry/members/alice.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{"status":"working","harness":"codex","last_activity":"2000-01-01T00:00:00+00:00"}')
        with patch.object(web,'member_panes',return_value={'alice':{}}), patch.object(web,'pane_harness_pid',return_value=123):
            member=next(m for m in web.members(0,1700000000,[]) if m['member']=='alice')
        self.assertEqual(member['state'],'working')
        self.assertEqual(member['telemetry_freshness'],'unknown')

    def test_member_card_uses_latest_room_message(self):
        posts = [{"member": "alice", "body": "first", "ts": "2026-10-01T10:00:00-04:00"},
                 {"member": "alice", "body": "latest", "ts": "2026-10-01T10:01:00-04:00"}]
        members = web.members(2, 0, posts)
        self.assertEqual(len(members), 2)
        self.assertEqual(next(m for m in members if m["member"] == "alice")["last_room"]["body"], "latest")

    def test_steer_uses_member_send(self):
        with patch.object(web, "run_hive", return_value="ok") as run:
            status, _ = self.request("/api/steer", {"member": "alice", "prompt": "do work"})
        self.assertEqual(status, 200)
        run.assert_called_once_with("hive-member", "send", "alice", "do work")

    def test_member_rename_preserves_nest_launch_conversation_and_room_history(self):
        state = self.root / "members/alice/state"
        state.mkdir()
        launch = state / "launch.json"
        launch.write_text(json.dumps({"harness": "codex", "dir": str(self.root / "projects")}))
        telemetry = self.root / "telemetry/members/alice.json"
        telemetry.write_text(json.dumps({"session_id": "ongoing-conversation", "cwd": str(self.root / "projects")}))
        web.run_hive("hive", "say", "Original discovery", member="alice")
        history = (self.root / "ROOM.md").read_bytes()
        with patch.object(web, "run_hive") as run:
            status, result = self.request("/api/member-profile", {"member": "alice", "name": " Atlas ", "team": "titantwoshot"})
        self.assertEqual(status, 200)
        run.assert_not_called()  # Editing names never sends keystrokes or restarts.
        self.assertEqual(result["profile"]["member"], "alice")
        self.assertEqual(result["profile"]["name"], "atlas")
        self.assertTrue((self.root / "members/alice").is_dir())
        self.assertFalse((self.root / "members/atlas").exists())
        self.assertEqual(json.loads(telemetry.read_text())["session_id"], "ongoing-conversation")
        self.assertEqual(json.loads(launch.read_text())["harness"], "codex")
        self.assertEqual((self.root / "ROOM.md").read_bytes(), history)
        _, snapshot = self.get_json("/api/state")
        member = next(m for m in snapshot["members"] if m["member"] == "alice")
        self.assertEqual((member["name"], member["team"]), ("atlas", "titantwoshot"))
        self.assertEqual(snapshot["teams"], {"titantwoshot": ["alice"]})
        text = web.run_hive("hive", "room", "--last", "1")
        self.assertIn("atlas (alice) @titantwoshot", text)

    def test_team_mentions_expand_aliases_once_in_the_shared_room(self):
        self.request("/api/member-profile", {"member": "alice", "name": "atlas", "team": "titantwoshot"})
        self.request("/api/member-profile", {"member": "bob", "name": "cedar", "team": "titantwoshot"})
        web.run_hive("hive", "join", "carol")
        self.request("/api/member-profile", {"member": "carol", "team": "poke"})
        status, data = self.request("/api/room", {"message": "@titantwoshot @atlas @alice review this"})
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], ["alice", "bob"])
        self.assertEqual(sorted((self.root / "sent-targets").read_text().splitlines()), ["alice", "bob"])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")
        self.assertIn("@titantwoshot @atlas @alice", web.room()[0]["body"])
        self.assertFalse((self.root / "teams").exists())
        data = json.loads(web.run_hive("hive", "say", "--json", "@titantwoshot @atlas @carol peers check this", member="alice"))
        self.assertEqual(data["sent"], ["carol"])
        self.assertEqual(data["queued"], ["bob"])

    def test_aliases_route_prompts_lifecycle_and_terminals_to_existing_sessions(self):
        self.request("/api/member-profile", {"member": "alice", "name": "atlas"})
        with patch.object(web, "run_hive", return_value="ok") as run:
            self.assertEqual(self.request("/api/steer", {"member": "atlas", "prompt": "check source"})[0], 200)
            run.assert_called_with("hive-member", "send", "alice", "check source")
            self.assertEqual(self.request("/api/member", {"member": "atlas", "action": "wake"})[0], 200)
            run.assert_called_with("hive-member", "wake", "alice", "--no-attach")
        data = json.loads(web.run_hive("hive", "say", "--json", "@atlas review", member="bob"))
        self.assertEqual(data["sent"], ["alice"])
        handler = object.__new__(web.Handler)
        handler.headers = {"Upgrade": "websocket", "Origin": "http://hive.local", "Host": "hive.local", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        handler.request = object()
        handler.send_response = handler.send_header = handler.end_headers = lambda *args: None
        with patch.object(web, "TerminalBridge") as bridge:
            bridge.return_value.running = False
            handler.handle_websocket(web.urlparse("/ws/terminal?member=atlas&mode=watch"))
            self.assertEqual(bridge.call_args.args[3], "=hive-alice")

    def test_member_and_team_namespace_collisions_fail_without_partial_edit(self):
        self.request("/api/member-profile", {"member": "alice", "name": "atlas", "team": "titantwoshot"})
        before = (self.root / "members/alice/state/profile.json").read_bytes()
        for values in ({"name": "bob"}, {"name": "all"}, {"name": "beekeeper-mina"},
                       {"name": "titantwoshot"}, {"team": "atlas"}, {"name": "new", "team": "bob"},
                       {"name": "../outside"}, {"name": "atlas."}, {"name": None}, {"team": True}, {"workdir": 3},
                       {"name": "new", "workdir": str(self.root / "missing")}, {"workdir": "relative"}):
            self.assertEqual(self.request("/api/member-profile", {"member": "alice", **values})[0], 400, values)
            self.assertEqual((self.root / "members/alice/state/profile.json").read_bytes(), before)
        self.assertEqual(self.request("/api/member-profile", {"member": "bob", "name": "atlas"})[0], 400)
        self.assertEqual(self.request("/api/member-profile", {"member": "beekeeper", "name": "owner"})[0], 400)
        self.assertEqual(self.request("/api/member-profile", {"member": "bob", "team": "poke"}, "http://other.site")[0], 403)
        with self.assertRaises(RuntimeError):
            web.run_hive("hive", "join", "atlas")
        with self.assertRaises(RuntimeError):
            web.run_hive("hive", "join", "titantwoshot")

    def test_leaving_a_team_removes_its_tag_and_unknown_tags_never_post(self):
        self.request("/api/member-profile", {"member": "alice", "team": "poke"})
        self.request("/api/member-profile", {"member": "alice", "team": ""})
        self.assertEqual(self.get_json("/api/state")[1]["teams"], {})
        self.assertEqual(self.request("/api/room", {"message": "@poke do this"})[0], 400)
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "0")
        self.request("/api/member-profile", {"member": "alice", "name": "atlas", "team": "poke", "workdir": str(self.root / "projects")})
        profile = web.hive_members.index(self.root)
        self.assertEqual(profile["teams"], {"poke": ["alice"]})
        self.assertEqual(profile["members"][0]["workdir"], str(self.root / "projects"))

    def test_live_member_learns_its_new_name_and_team_once_without_room_noise(self):
        self.request("/api/member-profile", {"member": "alice", "name": "atlas", "team": "titantwoshot"})
        hook = source.parent / "hive-hook"
        env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_MEMBER="alice")
        def boundary():
            return subprocess.run([str(hook), "claude", "PostToolUse"], input=json.dumps({"cwd": str(self.root)}),
                                  text=True, capture_output=True, env=env, check=True).stdout
        context = json.loads(boundary())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("HIVE member name: atlas (session identity alice)", context)
        self.assertIn("team: @titantwoshot", context)
        self.assertEqual(boundary(), "")
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "0")

    def test_named_beekeeper_posts_and_prompts_keep_the_requester(self):
        self.assertEqual(self.request("/api/identity", {"name": " TCTinh "})[0], 200)
        status, data = self.request("/api/room", {"message": "@all review source", "member": "someone-else"}, cookie="hive_beekeeper=tctinh")
        self.assertEqual(status, 200)
        self.assertEqual(data["sent"], ["alice", "bob"])
        self.assertIn("member=beekeeper-tctinh", (self.root / "ROOM.md").read_text())
        self.assertIn("beekeeper-tctinh", (self.root / "sent-alice").read_text())
        self.assertFalse((self.root / "members/beekeeper-tctinh").exists())
        with patch.object(web, "run_hive", return_value="ok") as run:
            self.assertEqual(self.request("/api/steer", {"member": "alice", "prompt": "check the API"}, cookie="hive_beekeeper=tctinh")[0], 200)
        self.assertIn("Beekeeper beekeeper-tctinh via dashboard", run.call_args.args[-1])
        self.assertTrue(run.call_args.args[-1].endswith("check the API"))

    def test_name_selection_rejects_bad_handles_and_cross_origin_requests(self):
        for name in ("../escape", "a/b", "a\r\nInjected: yes", "", "a"*40, None):
            self.assertEqual(self.request("/api/identity", {"name": name})[0], 400)
        self.assertEqual(self.request("/api/identity", {"name": "eve"}, "https://other.site")[0], 403)
        (self.root / "members/beekeeper-conflict").mkdir()
        self.assertEqual(self.request("/api/identity", {"name": "conflict"})[0], 400)
        self.assertEqual(web.browser_beekeeper({"Cookie": "hive_beekeeper=unknown"})["member"], "beekeeper")

    def test_cookie_remembers_browser_name_and_rename_keeps_old_room_authors(self):
        handler = object.__new__(web.Handler)
        payload = json.dumps({"name": "mina"}).encode()
        handler.path = "/api/identity"
        handler.headers = {"Content-Length": str(len(payload)), "Origin": "https://hive.local", "Host": "hive.local"}
        handler.rfile = io.BytesIO(payload)
        responses = []
        handler._send = lambda *args: responses.append(args)
        handler.do_POST()
        self.assertEqual(responses[0][0], 200)
        cookie = dict(responses[0][3])["Set-Cookie"]
        for flag in ("hive_beekeeper=mina", "HttpOnly", "SameSite=Lax", "Path=/", "Secure"):
            self.assertIn(flag, cookie)
        self.request("/api/room", {"message": "My original request"}, cookie="hive_beekeeper=mina")
        self.request("/api/identity", {"name": "new-name"})
        self.request("/api/room", {"message": "My next request"}, cookie="hive_beekeeper=new-name")
        posts = web.room()
        self.assertEqual([post["member"] for post in posts], ["beekeeper-mina", "beekeeper-new-name"])
        self.assertEqual(self.get_json("/api/state", cookie="hive_beekeeper=new-name")[1]["viewer"]["name"], "new-name")

    def test_named_human_mentions_and_done_are_personal(self):
        for name in ("mina", "lee"):
            self.request("/api/identity", {"name": name})
        web.run_hive("hive", "say", "@beekeeper Shared question?", member="alice")
        web.run_hive("hive", "say", "@beekeeper-mina Personal question?", member="alice")
        web.run_hive("hive", "say", "@beekeeper-lee Another question?", member="alice")
        self.assertFalse((self.root / "sent-targets").exists())
        def mentions(name):
            return [post["gen"] for post in self.get_json("/api/state", cookie=f"hive_beekeeper={name}")[1]["beekeeper_mentions"]]
        self.assertEqual(mentions("mina"), [1, 2])
        self.assertEqual(mentions("lee"), [1, 3])
        self.assertEqual(self.request("/api/room-done", {"generation": 1}, cookie="hive_beekeeper=mina")[0], 200)
        self.assertEqual(mentions("mina"), [2])
        self.assertEqual(mentions("lee"), [1, 3])
        self.assertEqual(self.request("/api/room-done", {"generation": 3}, cookie="hive_beekeeper=mina")[0], 400)
        self.request("/api/room", {"message": "> Reply to alice · Room #2: Personal question?\n\n@alice Use B"}, cookie="hive_beekeeper=mina")
        self.assertEqual(mentions("mina"), [])
        self.assertEqual(mentions("lee"), [1, 3])
        self.assertNotIn("beekeeper-mina", [member["member"] for member in web.members(4, 0, web.room())])

    def test_git_projects_include_projects_without_live_members(self):
        repo = self.root / "projects" / "sample"
        repo.mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "README.md").write_text("first\n")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "Initial"], check=True)
        (repo / "README.md").write_text("changed\n")
        (repo / "new.txt").write_text("new\n")
        projects = web.git_projects([])
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "sample")
        self.assertEqual((projects[0]["modified"], projects[0]["untracked"]), (1, 1))
        self.assertEqual(projects[0]["commit"]["subject"], "Initial")

    def test_hive_files_list_and_preview_with_path_boundaries(self):
        note = self.root / "members" / "alice" / "notes" / "plan.md"
        note.parent.mkdir()
        note.write_text("Only relevant notes are read.\n")
        status, listing = self.get_json("/api/files?path=members/alice/notes")
        self.assertEqual(status, 200)
        self.assertEqual(listing["entries"][0]["name"], "plan.md")
        status, preview = self.get_json("/api/file?path=members/alice/notes/plan.md")
        self.assertEqual(status, 200)
        self.assertEqual(preview["text"], note.read_text())
        handler = object.__new__(web.Handler)
        handler.path = "/api/file?path=members/alice/notes/plan.md&download=1"
        handler.command = "GET"
        handler.wfile = io.BytesIO()
        headers = {}
        handler.send_response = lambda code: headers.update(status=code)
        handler.send_header = lambda name, value: headers.update({name: value})
        handler.end_headers = lambda: None
        handler.do_GET()
        self.assertEqual(headers["status"], 200)
        self.assertEqual(handler.wfile.getvalue(), note.read_bytes())
        with self.assertRaises(ValueError):
            web.browser_path("members/alice/notes/../../bob/state/launch.json")
        (note.parent / "outside").symlink_to(Path(self.temp.name).parent)
        with self.assertRaises(ValueError):
            web.browser_path("members/alice/notes/outside")

    def test_project_documents_and_paginated_artifacts(self):
        project = self.root / "projects" / "sample"
        project.mkdir(parents=True)
        for number in range(305):
            (project / f"artifact-{number:03}.md").write_text(f"Artifact {number}\n")
        status, listing = self.get_json("/api/files?path=projects")
        self.assertEqual(status, 200)
        self.assertEqual(listing["entries"][0]["path"], "projects/sample")
        _, first = self.get_json("/api/files?path=projects/sample")
        _, rest = self.get_json("/api/files?path=projects/sample&offset=300")
        self.assertEqual(first["next_offset"], 300)
        self.assertIsNone(rest["next_offset"])
        self.assertEqual(len({entry["path"] for entry in first["entries"] + rest["entries"]}), 305)
        _, preview = self.get_json("/api/file?path=projects/sample/artifact-304.md")
        self.assertEqual(preview["text"], "Artifact 304\n")
        self.assertEqual(self.get_json("/api/files?path=projects/sample&offset=-1")[0], 400)
        with self.assertRaises(ValueError):
            web.browser_path("projects/sample/../../ROOM.md")
        (project / "outside").symlink_to(self.root.parent)
        self.assertNotIn("outside", [entry["name"] for entry in web.file_entries("projects/sample")])
        self.assertEqual(self.get_json("/api/file?path=projects/sample/outside/secret")[0], 400)

    def test_raster_preview_streams_image_but_never_html_or_svg(self):
        project = self.root / "projects" / "sample"
        project.mkdir(parents=True)
        image = project / "capture.bin"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        _, preview = self.get_json("/api/file?path=projects/sample/capture.bin")
        self.assertEqual(preview["image_mime"], "image/png")
        handler = object.__new__(web.Handler)
        handler.path = "/api/file?path=projects/sample/capture.bin&view=1"
        handler.command = "GET"
        handler.wfile = io.BytesIO()
        headers = {}
        handler.send_response = lambda code: headers.update(status=code)
        handler.send_header = lambda name, value: headers.update({name: value})
        handler.end_headers = lambda: None
        handler.do_GET()
        self.assertEqual(headers["status"], 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(headers["Content-Disposition"].startswith("inline;"))
        self.assertEqual(handler.wfile.getvalue(), image.read_bytes())
        for filename, body in (("page.png", "<script>alert(1)</script>"), ("drawing.svg", "<svg onload='alert(1)'/>")):
            (project / filename).write_text(body)
            self.assertEqual(self.get_json(f"/api/file?path=projects/sample/{filename}&view=1")[0], 400)
            _, preview = self.get_json(f"/api/file?path=projects/sample/{filename}")
            self.assertIsNone(preview["image_mime"])
            self.assertEqual(preview["text"], body)

    def test_only_explicit_human_mentions_need_beekeeper_attention(self):
        bodies = ["Ordinary update", "@all any feedback?", "@bob which API?",
                  "mail me at person@beekeeper", "`/path/@beekeeper`",
                  "> Reply to alice · Room #1: @beekeeper choose?\n\nNo human question here",
                  "@beekeeper. Which direction?", "@beekeeper an artifact is ready", "@beekeeper you wrote this"]
        posts = [{"gen": index + 1, "member": "alice" if index < 8 else "beekeeper", "body": body}
                 for index, body in enumerate(bodies)]
        mentions = web.beekeeper_mentions(posts)
        self.assertEqual([post["gen"] for post in mentions], [7, 8])
        self.assertEqual([post["question"] for post in mentions], [True, False])
        # Only a human reply to the exact sender/generation marks it replied.
        posts += [{"gen": 10, "member": "bob", "body": "> Reply to alice · Room #7: choose?\n\nI prefer B"},
                  {"gen": 11, "member": "beekeeper", "body": "> Reply to bob · Room #7: choose?\n\n@bob B"}]
        self.assertEqual(len(web.beekeeper_mentions(posts)), 2)
        self.assertFalse(web.beekeeper_mentions(posts)[0]["replied"])
        posts.append({"gen": 12, "member": "beekeeper", "body": "> Reply to alice · Room #7: choose?\n\n@alice B"})
        self.assertEqual([post["gen"] for post in web.beekeeper_mentions(posts)], [8])

    def test_question_survives_room_display_window_and_answer_prompts_member(self):
        web.run_hive("hive", "say", "@beekeeper Which direction?", member="alice")
        for number in range(web.ROOM_ENTRIES + 1):
            web.run_hive("hive", "say", f"Update {number}", member="bob")
        with patch.object(web, "cached_git_projects", return_value=[]):
            snapshot = web.state()
        self.assertNotIn(1, [post["gen"] for post in snapshot["room"]])
        self.assertEqual([post["gen"] for post in snapshot["beekeeper_mentions"]], [1])
        status, report = self.request("/api/room", {"message": "> Reply to alice · Room #1: Which direction?\n\n@alice Go with B"})
        self.assertEqual(status, 200)
        self.assertEqual(report["sent"], ["alice"])
        self.assertEqual(web.beekeeper_mentions(web.room(limit=None)), [])
        status, report = self.request("/api/room-done", {"generation": 1})
        self.assertEqual(status, 200)
        self.assertEqual(web.beekeeper_mentions(web.room(limit=None)), [])
        # Dismissal survives polling/reloading and does not alter shared history.
        history = (self.root / "ROOM.md").read_text()
        status, _ = self.request("/api/room-done", {"generation": 1})
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "ROOM.md").read_text(), history)
        self.assertTrue(web.room_done_path(1).is_file())

    def test_unsent_reply_keeps_the_original_notification(self):
        web.run_hive("hive", "say", "@beekeeper Which direction?", member="alice")
        status, _ = self.request("/api/room", {"message": "> Reply to alice · Room #1: Which direction?\n\n@alice " + "x" * 12000})
        self.assertEqual(status, 400)
        self.assertEqual([p["gen"] for p in web.beekeeper_mentions(web.room(limit=None))], [1])
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "1")

    def test_mark_done_only_affects_the_selected_human_notification(self):
        web.run_hive("hive", "say", "@beekeeper Which direction?", member="alice")
        web.run_hive("hive", "say", "@beekeeper Capture ready", member="bob")
        web.run_hive("hive", "say", "Ordinary update", member="alice")
        status, _ = self.request("/api/room-done", {"generation": 2})
        self.assertEqual(status, 200)
        self.assertEqual([p["gen"] for p in web.beekeeper_mentions(web.room(limit=None))], [1])
        self.assertFalse((self.root / "sent-targets").exists())
        for generation in (3, 999, -1, True, "../ROOM.md", None):
            self.assertEqual(self.request("/api/room-done", {"generation": generation})[0], 400)
        self.assertEqual(self.request("/api/room-done", {"generation": 1}, origin="http://other.local")[0], 403)
        self.assertEqual((self.root / ".room-generation").read_text().strip(), "3")

    def test_fragmented_websocket_input(self):
        class OneByteSocket:
            def __init__(self, data):
                self.data = bytearray(data)

            def recv(self, _):
                if not self.data:
                    return b""
                return bytes([self.data.pop(0)])

        payload = b"hello"
        mask = os.urandom(4)
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.assertEqual(web.read_ws_frame(OneByteSocket(frame)), (1, payload))

    def test_terminal_upgrade_uses_http_1_1(self):
        # Firefox rejects an HTTP/1.0 upgrade even when its other headers are valid.
        # Exercise the actual response on the wire without starting a shell.
        with patch.object(web, "TerminalBridge") as bridge_class:
            bridge_class.return_value.running = False
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host = f"127.0.0.1:{server.server_port}"
                with socket.create_connection(server.server_address, timeout=2) as connection:
                    connection.sendall((
                        "GET /ws/shell HTTP/1.1\r\n"
                        f"Host: {host}\r\nOrigin: http://{host}\r\n"
                        "Connection: Upgrade\r\nUpgrade: websocket\r\n"
                        "Sec-WebSocket-Version: 13\r\n"
                        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
                    ).encode())
                    response = b""
                    while b"\r\n\r\n" not in response:
                        chunk = connection.recv(4096)
                        self.assertTrue(chunk, "connection closed before upgrade headers")
                        response += chunk
                self.assertEqual(response.split(b"\r\n", 1)[0], b"HTTP/1.1 101 Switching Protocols")
                self.assertIn(b"Upgrade: websocket\r\n", response)
                self.assertIn(b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n", response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_terminal_foreground_process_receives_resize(self):
        server_socket, client_socket = socket.socketpair()
        client_socket.settimeout(2)
        script = """
import os
import signal

def resized(*_):
    print('size=' + str(os.get_terminal_size(0).columns), flush=True)

signal.signal(signal.SIGWINCH, resized)
try:
    foreground = os.tcgetpgrp(0) == os.getpgrp()
except OSError:
    foreground = False
print('foreground=' + str(foreground), flush=True)
print('identity=' + os.environ.get('HIVE_MEMBER', ''), flush=True)
print('ready', flush=True)
while True:
    signal.pause()
"""
        bridge = web.TerminalBridge([sys.executable, "-u", "-c", script], "steer", server_socket,
                                    cwd=str(self.root), actor="beekeeper-mina")

        def receive_until(marker):
            data = b""
            while marker not in data:
                chunk = client_socket.recv(4096)
                self.assertTrue(chunk, "terminal closed before expected output")
                data += chunk
            return data

        try:
            bridge.start()
            output = receive_until(b"ready")
            self.assertIn(b"foreground=True", output)
            self.assertIn(b"identity=beekeeper-mina", output)
            web.set_winsize(bridge.master_fd, 25, 132)
            self.assertIn(b"size=132", receive_until(b"size=132"))
        finally:
            bridge.stop()
            server_socket.shutdown(socket.SHUT_RDWR)
            server_socket.close()
            client_socket.close()
            if bridge.proc:
                bridge.proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
