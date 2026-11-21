"""OpenCode and Antigravity adapters: composers, hook translation, config merges."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "share"))
import hive_config
import hive_prompt as prompt

RULE = "╹" + "▀" * 70


class OpenCodeComposer(unittest.TestCase):
    def screen(self, *box, indent="  ", sidebar=""):
        rows = ["   Session title", ""]
        rows += [indent + "┃" + (("  " + line) if line else "") for line in box]
        rows += [indent + RULE, indent + "~/project:main          ctrl+p commands"]
        if sidebar:
            rows[2] = rows[2].ljust(len(indent) + 75) + sidebar
        return rows

    def test_placeholder_is_empty(self):
        rows = self.screen("", 'Ask anything… "Fix broken tests"', "", "Build · Claude · medium", indent=" " * 33)
        parts = prompt.composer(rows, 3)
        self.assertEqual(parts, ['Ask anything… "Fix broken tests"'])
        self.assertTrue(prompt.empty(parts))

    def test_session_composer_is_empty(self):
        rows = self.screen("", "", "", "Build · Claude · medium")
        self.assertTrue(prompt.empty(prompt.composer(rows, 3)))

    def test_draft_and_wrap_match(self):
        rows = self.screen("", "[hive-message id=a1]", "line two", "", "Build · Claude · medium")
        parts = prompt.composer(rows, 4)
        self.assertFalse(prompt.empty(parts))
        self.assertTrue(prompt.matches(parts, "[hive-message id=a1]\nline two"))

    def test_sidebar_is_not_input(self):
        rows = self.screen("", "", "", "Build · Claude · medium", sidebar="Context")
        self.assertTrue(prompt.empty(prompt.composer(rows, 3)))

    def test_collapsed_paste(self):
        rows = self.screen("", "[Pasted ~3 lines]", "", "Build · Claude · medium")
        self.assertTrue(prompt.collapsed(prompt.composer(rows, 3)))

    def test_footer_row_is_not_a_composer(self):
        rows = self.screen("", "", "", "Build · Claude · medium")
        self.assertIsNone(prompt.composer(rows, 5))


class AntigravityComposer(unittest.TestCase):
    SEP = "─" * 80

    def test_mode_placeholders_are_empty(self):
        for text in ("Auto mode: edits & safe commands auto-approved (shift+tab to cycle)",
                     "Plan mode: research & plan only (shift+tab to cycle)",
                     "Accept-edits mode: file edits auto-approved (shift+tab to cycle)"):
            rows = [self.SEP, "> " + text, self.SEP, "? for shortcuts"]
            self.assertTrue(prompt.empty(prompt.composer(rows, 1)), text)

    def test_bare_prompt_is_empty(self):
        rows = [self.SEP, ">", self.SEP, "? for shortcuts"]
        self.assertTrue(prompt.empty(prompt.composer(rows, 1)))

    def test_multiline_draft(self):
        rows = [self.SEP, "> [hive-message id=a1]", "  line two", self.SEP]
        parts = prompt.composer(rows, 2)
        self.assertTrue(prompt.matches(parts, "[hive-message id=a1]\nline two"))
        self.assertFalse(prompt.empty(parts))


class Config(unittest.TestCase):
    def test_plugin_entry_replaces_older_hive_paths(self):
        current = {"entries": [{"path": "/old/share/agentic-hive/antigravity"}, {"path": "~/mine"}], "inherits": []}
        result = hive_config.plugin_entries(current, "/usr/share/agentic-hive/antigravity")
        self.assertEqual(result["entries"], [{"path": "~/mine"}, {"path": "/usr/share/agentic-hive/antigravity"}])
        self.assertEqual(result["inherits"], [])
        self.assertEqual(hive_config.plugin_entries(result, "/usr/share/agentic-hive/antigravity"), result)

    def test_trust_preserves_settings(self):
        result = hive_config.trusted({"trustedWorkspaces": ["/a"], "theme": "x"}, "/b")
        self.assertEqual(result, {"trustedWorkspaces": ["/a", "/b"], "theme": "x"})
        self.assertEqual(hive_config.trusted(result, "/b"), result)


class AntigravityHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.env = dict(os.environ, HIVE_ROOT=str(self.root), HIVE_USER=subprocess.check_output(["id", "-un"], text=True).strip(),
                        HIVE_SHARE=str(REPO / "share"), PATH=f"{REPO / 'bin'}:{os.environ['PATH']}",
                        HIVE_AGY_SESSION="agy_test", HIVE_MEMBER="ant")
        self.env.pop("HIVE_BIN_DIR", None)
        subprocess.run([REPO / "bin/hive", "init"], env=self.env, check=True, capture_output=True)
        for member in ("ant", "peer"):
            subprocess.run([REPO / "bin/hive", "join", member], env=self.env, check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, event, payload, member="ant"):
        env = dict(self.env, HIVE_MEMBER=member)
        out = subprocess.run(["sh", REPO / "share/antigravity/agentic-hive/hook.sh", event], input=json.dumps(payload),
                             env=env, capture_output=True, text=True, check=True,
                             cwd=REPO / "share/antigravity/agentic-hive").stdout
        return json.loads(out)

    def telemetry(self):
        return json.loads((self.root / "telemetry/members/ant.json").read_text())

    def test_lifecycle(self):
        base = {"conversationId": "c-1", "cwd": self.tmp.name, "lastUserInput": "hello"}
        start = self.hook("SessionStart", base)
        texts = [s["systemMessage"]["systemMessage"] for s in start["injectSteps"]]
        self.assertIn("Agentic Hive", texts[0])
        self.assertTrue(any("member: ant" in t for t in texts))
        self.hook("PreInvocation", dict(base, invocationNum=0))
        state = self.telemetry()
        self.assertEqual((state["last_event"], state["session_id"], state["conversation_id"]),
                         ("UserPromptSubmit", "agy_test", "c-1"))
        receipt = json.loads((self.root / "members/ant/state/last-submitted-prompt.json").read_text())
        self.assertEqual(receipt["session_id"], "agy_test")
        self.assertEqual(self.hook("PostToolUse", dict(base, toolCall={"name": "run_command"})), {})
        self.assertEqual(self.telemetry()["last_tool"], "run_command")
        subprocess.run([REPO / "bin/hive", "say", "peer news"], env=dict(self.env, HIVE_MEMBER="peer"), check=True, capture_output=True)
        after = self.hook("PreInvocation", dict(base, invocationNum=1))
        self.assertIn("peer news", after["injectSteps"][0]["systemMessage"]["systemMessage"])
        self.assertEqual(self.hook("Stop", base), {})
        self.assertEqual(self.telemetry()["status"], "idle")

    def test_outside_member_session(self):
        self.assertEqual(self.hook("Stop", {}, member=""), {})


if __name__ == "__main__":
    unittest.main()
