"""Prompt guards and interrupted retries; no sockets, processes or live input."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "share"))
import hive_prompt as prompt
from hive_runtime import atomic


class Terminal:
    def __init__(self, root):
        self.root = root
        self.message = ""
        self.draft = ""
        self.calls = []
        self.enters = 0
        self.pastes = 0
        self.ack_delay = 0
        self.ticks = 0
        self.no_ack = False
        self.drop_enter = False
        self.fail_paste = False
        self.fail_load = False
        self.fail_enter = False
        self.dialog = False
        self.dialog_after_paste = False
        self.collapsed = False
        self.mode = 0
        self.pane = "%7"
        self.resize = False
        self.before_capture = False
        self.busy_after_load = False

    def acknowledge(self):
        self.draft = ""
        (self.root / "members/bob/state/last-submitted-prompt").write_text(f"fixture-{self.ticks} {prompt.prompt_hash(self.message)}\n")

    def run(self, args, **kwargs):
        self.calls.append(args)
        out = ""
        if args[0] == "sleep":
            self.ticks += 1
            if self.ack_delay and self.enters and self.ticks >= self.ack_delay:
                self.acknowledge()
        elif args[1] == "display-message":
            row = len(self.draft.split("\n")) if self.draft else 1
            out = f"{row} {self.mode} {self.pane}\n"
            if self.resize and self.before_capture:
                out = f"{row + 1} {self.mode} {self.pane}\n"
        elif args[1] == "capture-pane":
            self.before_capture = True
            if self.dialog or (self.dialog_after_paste and self.pastes):
                out = "Transcript\nPermission required\n"
            else:
                lines = self.draft.split("\n") if self.draft else ["Ask Codex to do anything"]
                out = "Transcript\n› " + lines[0] + "\n"
                out += "".join(("  " + line if line else "") + "\n" for line in lines[1:])
        elif args[1] == "load-buffer":
            self.message = kwargs["input"]
            if self.busy_after_load:
                atomic(self.root / "telemetry/members/bob.json", {"session_id":"fixture-session", "status":"working"})
            if self.fail_load:
                raise subprocess.CalledProcessError(1, args)
        elif args[1] == "paste-buffer":
            self.pastes += 1
            self.draft += "[Pasted text 100 chars]" if self.collapsed else self.message
            if self.fail_paste:
                raise subprocess.CalledProcessError(1, args)
        elif args[1] == "send-keys":
            self.enters += 1
            if self.fail_enter:
                raise subprocess.CalledProcessError(1, args)
            if self.drop_enter and self.enters == 1:
                pass
            elif self.ack_delay:
                self.draft = ""
            elif not self.no_ack:
                self.acknowledge()
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, out, "")


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "members/bob/state").mkdir(parents=True)
        atomic(self.root / "telemetry/members/bob.json", {"session_id":"fixture-session", "status":"idle"})
        self.terminal = Terminal(self.root)
        self.message = "HIVE messages\n[hive-message id=fixture-one]\n    first\n\n    second\n\n"

    def send(self, message=None):
        return prompt.send(self.root, "bob", message or self.message, run=self.terminal.run)

    def stage(self):
        return json.loads((self.root / "members/bob/state/prompt-delivery.json").read_text())["stage"]

    def test_original_blank_row_failure_now_one_paste_one_enter(self):
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.assertEqual(self.stage(), "acknowledged")

    def test_blank_rows_require_owned_composer(self):
        rows = ["old transcript", "› current", "", "  more", ""]
        self.assertIsNone(prompt.composer(rows, 4))
        self.assertTrue(prompt.matches(prompt.composer(rows, 4, owned=True), "current\n\nmore\n\n"))
        self.assertIsNone(prompt.composer(["› old", "dialog", ""], 2, owned=True))

    def test_wraps_nbsp_and_entire_draft_matching(self):
        self.assertTrue(prompt.matches(["short", " wrapped", "line"], "short wrapped\nline"))
        self.assertEqual(prompt.composer(["❯\u00a0hello"], 0), ["hello"])
        self.assertFalse(prompt.matches(["short"], "short more"))
        self.assertFalse(prompt.matches(["short more"], "short"))
        self.assertFalse(prompt.matches(["short", "USER EXTRA"], "short"))

    def test_unrelated_draft_is_untouched(self):
        self.terminal.draft = "USER DRAFT"
        self.assertEqual(self.send()[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))
        self.assertEqual(self.terminal.draft, "USER DRAFT")

    def test_dialog_and_copy_mode_are_untouched(self):
        self.terminal.dialog = True
        self.assertEqual(self.send()[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))
        self.terminal.dialog = False
        self.terminal.mode = 1
        self.assertEqual(self.send()[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))

    def test_dialog_after_paste_no_enter(self):
        self.terminal.dialog_after_paste = True
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))
        self.terminal.dialog_after_paste = False
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_delayed_ack_gets_one_enter(self):
        self.terminal.ack_delay = 12
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_reset_without_ack_is_pending_and_retry_does_not_repaste(self):
        self.terminal.ack_delay = 9999
        self.assertEqual(self.send()[0], 2)
        self.assertEqual(self.stage(), "accepted")
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.terminal.acknowledge()
        self.assertEqual(self.send()[0], 0)
        self.assertEqual(self.stage(), "acknowledged")

    def test_same_ids_are_not_delivered_twice(self):
        self.assertEqual(self.send()[0], 0)
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_explicit_repeated_direct_prompt_is_allowed(self):
        self.assertEqual(self.send("continue")[0], 0)
        self.terminal.ticks += 1
        self.assertEqual(self.send("continue")[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (2, 2))

    def test_fail_between_paste_and_enter_resumes_owned_draft(self):
        self.terminal.fail_paste = True
        self.assertEqual(self.send()[0], 2)
        self.assertEqual(self.stage(), "pasted")
        self.terminal.fail_paste = False
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_fail_at_enter_no_repaste(self):
        self.terminal.fail_enter = True
        self.assertEqual(self.send()[0], 2)
        self.terminal.fail_enter = False
        self.assertEqual(self.send()[0], 0)
        self.assertEqual(self.terminal.pastes, 1)

    def test_owned_text_edited_by_user_is_not_submitted(self):
        self.terminal.fail_paste = True
        self.assertEqual(self.send()[0], 2)
        self.terminal.draft += "USER ADDITION"
        self.terminal.fail_paste = False
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_swallowed_enter_retried_only_while_owned_text_remains(self):
        self.terminal.drop_enter = True
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 2))

    def test_session_change_blocks_retry(self):
        self.terminal.fail_paste = True
        self.assertEqual(self.send()[0], 2)
        atomic(self.root / "telemetry/members/bob.json", {"session_id":"different", "status":"idle"})
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_collapsed_paste_bound_to_own_attempt(self):
        self.terminal.collapsed = True
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.terminal.draft = "[Pasted text 100 chars]"
        self.assertEqual(self.send("another message")[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_cursor_change_between_reads_blocks_input(self):
        self.terminal.resize = True
        self.assertEqual(self.send()[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))

    def test_idle_drain_rechecks_working_state_before_input(self):
        atomic(self.root / "telemetry/members/bob.json", {"session_id":"fixture-session", "status":"working"})
        self.assertEqual(prompt.send(self.root, "bob", self.message, run=self.terminal.run, require_idle=True)[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))

    def test_working_race_after_buffer_load_is_queued(self):
        self.terminal.busy_after_load = True
        self.assertEqual(prompt.send(self.root, "bob", self.message, run=self.terminal.run, require_idle=True)[0], 3)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (0, 0))
        self.assertEqual(self.stage(), "queued")

    def test_failure_before_paste_can_retry_without_uncertainty(self):
        self.terminal.fail_load = True
        self.assertEqual(self.send()[0], 3)
        self.assertEqual(self.stage(), "queued")
        self.terminal.fail_load = False
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_stale_matching_hash_is_not_a_new_ack(self):
        (self.root / "members/bob/state/last-submitted-prompt").write_text("old " + prompt.prompt_hash(self.message))
        self.terminal.no_ack = True
        self.assertEqual(self.send()[0], 2)
        self.assertNotEqual(self.stage(), "acknowledged")

    def test_leading_and_single_trailing_blank_lines(self):
        self.assertEqual(self.send("\n\ntext\n")[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_pane_change_blocks_owned_retry(self):
        self.terminal.fail_paste = True
        self.assertEqual(self.send()[0], 2)
        self.terminal.fail_paste = False
        self.terminal.pane = "%8"
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))


if __name__ == "__main__":
    unittest.main()
