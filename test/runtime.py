"""Safety regressions use temporary homes, synthetic logs and mocked sessions."""

import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share"))
from hive_config import update
from hive_runtime import atomic, settings, arguments, quota, freshness

REPO = Path(__file__).resolve().parents[1]


def merge_worker(path, number):
    update(path, {"hooks": {f"Event{number}": [{"command": f"other-hook-{number}"}]}})


class RuntimeTests(unittest.TestCase):
    def test_concurrent_preserving_merges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hooks.json"
            atomic(
                path,
                {
                    "unrelated": {"keep": True},
                    "hooks": {"Stop": [{"command": "company-hook"}]},
                },
            )
            workers = [multiprocessing.Process(target=merge_worker, args=(path, n)) for n in range(12)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
                self.assertEqual(worker.exitcode, 0)
            result = json.loads(path.read_text())
            self.assertEqual(len(result["hooks"]), 13)
            self.assertEqual(result["unrelated"], {"keep": True})

    def test_actual_codex_hook_installer_repeated_and_concurrent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "bin with spaces"
            directory.mkdir()
            hook = directory / "hive-hook"
            hook.write_text("#!/bin/bash\nexit 0\n")
            hook.chmod(0o755)
            codex = root / "codex"
            codex.mkdir()
            path = codex / "hooks.json"
            original = {"other": True, "hooks": {"Stop": [{"hooks": [{"command": "company-hook"}]}]}}
            path.write_text(json.dumps(original))
            path.chmod(0o600)
            source = (REPO / "bin/hive-launch").read_text()
            function = source[source.index("install_codex_hooks()") : source.index("# Inner mode:")]
            env = dict(os.environ, HOME=temp, CODEX_HOME=str(codex), HIVE_BIN_DIR=str(directory))
            command = [
                "bash",
                "-c",
                'set -e; SHARE="$1"; die(){ exit 1; }; ' + function + "\ninstall_codex_hooks",
                "test",
                str(REPO / "share"),
            ]
            processes = [
                subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(8)
            ]
            for process in processes:
                _, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error)
            merged = json.loads(path.read_text())
            self.assertTrue(merged["other"])
            self.assertEqual(len(merged["hooks"]["Stop"]), 2)
            self.assertEqual(merged["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = path.stat().st_mtime_ns
            subprocess.run(command, env=env, check=True, capture_output=True)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_settings_round_trip_excludes_prompts_and_secrets(self):
        saved = settings(
            "codex",
            [
                "--profile",
                "review",
                "-m",
                "gpt-5",
                "-c",
                'model_reasoning_effort="high"',
                "-c",
                "api_key=private",
                "--other",
                "secret",
                "arbitrary prompt",
            ],
        )
        self.assertEqual(saved, {"profile": "review", "model": "gpt-5", "reasoning_effort": "high"})
        self.assertEqual(settings("codex", arguments("codex", saved)), saved)
        self.assertEqual(
            settings(
                "claude",
                arguments(
                    "claude",
                    {"model": "opus", "permission_mode": "plan", "effort": "high"},
                ),
            ),
            {"model": "opus", "permission_mode": "plan", "effort": "high"},
        )
        self.assertEqual(settings("codex", ["--", "--profile", "prompt-text"]), {})

    def test_empty_quota_throttles_attempts_and_does_not_scan_without_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("hive_runtime.os.walk", return_value=[]) as walk:
                quota(root, "alice", {}, root / "home", now=100)
                quota(root, "alice", {}, root / "home", now=101)
                walk.assert_not_called()
            with patch("hive_runtime.os.walk", return_value=[]) as walk:
                quota(root, "alice", {"session_id": "fixture"}, root / "home", now=200)
                quota(root, "alice", {"session_id": "fixture"}, root / "home", now=201)
                self.assertEqual(walk.call_count, 1)

    def test_synthetic_quota_cached_path_and_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "rollout-test.jsonl"
            log.write_text(
                json.dumps(
                    {
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {"primary": {"used_percent": 12, "window_minutes": 300}},
                        }
                    }
                )
                + "\n"
            )
            quota(
                root,
                "alice",
                {"transcript_path": str(log), "session_id": "fixture"},
                root,
                now=100,
            )
            quota(root, "alice", {"session_id": "fixture"}, root, now=131)
            self.assertEqual(
                json.loads((root / "telemetry/quota/codex.json").read_text())["windows"][0]["used_percent"],
                12,
            )

    def test_working_guard_survives_gaps_and_missing_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "members/alice/state").mkdir(parents=True)
            (root / "telemetry/members").mkdir(parents=True)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            tmux = bin_dir / "tmux"
            tmux.write_text(
                '#!/bin/bash\ncase "$1" in has-session) exit 0;; list-panes) echo "$TEST_PID";; kill-session) touch "$HIVE_ROOT/killed";; esac\n'
            )
            tmux.chmod(0o755)
            atomic(
                root / "members/alice/state/launch.json",
                {"harness": "python", "dir": temp},
            )
            env = dict(
                os.environ,
                HIVE_ROOT=temp,
                HIVE_USER=subprocess.check_output(["id", "-un"], text=True).strip(),
                TEST_PID=str(os.getpid()),
                PATH=str(bin_dir) + ":" + os.environ["PATH"],
            )
            for last in ("2000-01-01T00:00:00+00:00", None, "invalid"):
                atomic(
                    root / "telemetry/members/alice.json",
                    {"status": "working", "last_activity": last},
                )
                result = subprocess.run(
                    [str(REPO / "bin/hive-member"), "kill", "alice"],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "killed").exists())
            result = subprocess.run(
                [str(REPO / "bin/hive-member"), "kill", "alice", "--force"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "killed").exists())
            self.assertEqual(
                json.loads((root / "telemetry/members/alice.json").read_text())["status"],
                "ended",
            )
            self.assertEqual(freshness({"last_activity": "2000-01-01T00:00:00+00:00"}), "unknown")

    def test_hook_delivery_lock_returns_without_losing_cursor(self):
        import fcntl

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "members/alice/state").mkdir(parents=True)
            (root / ".room-generation").write_text("2")
            (root / "members/alice/state/last-delivered-generation").write_text("0")
            with (root / "members/alice/state/.deliver.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                start = time.monotonic()
                result = subprocess.run(
                    [str(REPO / "bin/hive-hook"), "claude", "PostToolUse"],
                    input="{}",
                    text=True,
                    capture_output=True,
                    env=dict(os.environ, HIVE_ROOT=temp, HIVE_MEMBER="alice"),
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0)
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(
                    (root / "members/alice/state/last-delivered-generation").read_text(),
                    "0",
                )


if __name__ == "__main__":
    unittest.main()
