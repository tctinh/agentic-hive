"""Prompt guards and interrupted retries; no sockets, processes or live input."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.footer = ""
        self.below_cursor = ""

    def acknowledge(self):
        self.draft = ""
        (self.root / "members/bob/state/last-submitted-prompt").write_text(f"{1000000000000000000 + self.ticks} {prompt.prompt_hash(self.message)}\n")

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
                if self.below_cursor and self.pastes:
                    out += self.below_cursor + "\n"
                if self.footer:
                    out += "\n" + self.footer + "\n"
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

    def test_indented_status_footer_one_paste_one_enter(self):
        self.terminal.footer = "  model status · 97% context left"
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.assertEqual(self.stage(), "acknowledged")

    def test_captured_codex_model_footer_and_warnings(self):
        self.terminal.footer = (
            "  GPT-6.1-Sol xhigh · /srv/hive/projects · Work\n"
            "  ? for shortcuts                                     ⚠ 2 warnings · f2 to view")
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_captured_codex_model_footer_without_shortcuts(self):
        self.terminal.footer = (
            "  GPT-6-Astra xhigh · /srv/hive/projects · Learn your collaboration role\n"
            "                                                      ⚠ 2 warnings · f2 to view")
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_model_footer_never_hides_an_added_user_draft(self):
        self.terminal.footer = (
            "  GPT-6.1-Sol xhigh · /srv/hive/projects · Work\n"
            "  ? for shortcuts                                     ⚠ 2 warnings · f2 to view")
        self.terminal.below_cursor = "  USER ADDITION"
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_directory_picker_is_not_a_composer(self):
        rows = ["  Working directory · resume", "",
            "› 1. Use session directory (/srv/hive/projects)",
            "  2. Use current directory (/srv/hive/projects/debter)", "",
            "  enter continue · esc use session · ctrl+c quit"]
        self.assertIsNone(prompt.composer(rows, 2))

    def retained_old_batch(self):
        old_hash = prompt.prompt_hash(self.message)
        atomic(self.root / "members/bob/state/prompt-delivery.json", {
            "hash":old_hash, "ids":["fixture-one"], "session_id":"fixture-session",
            "pane_id":"%7", "receipt_before":"900000000000000000 " + "a" * 64,
            "stage":"pasted"})
        return old_hash

    def test_exact_old_receipt_retires_stale_journal_before_new_batch(self):
        old_hash = self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + old_hash)
        new = "HIVE messages\n[hive-message id=fixture-two]\nnew request"
        self.assertEqual(self.send(new)[0], 0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))
        self.assertEqual(self.stage(),"acknowledged")

    def test_old_receipt_reconciliation_never_submits_unrelated_draft(self):
        old_hash = self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + old_hash)
        self.terminal.draft = "UNRELATED USER DRAFT"
        self.assertEqual(self.send("different request")[0],3)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))
        self.assertEqual(self.terminal.draft,"UNRELATED USER DRAFT")
        self.assertEqual(self.stage(),"acknowledged")

    def test_higher_cursor_alone_cannot_retire_old_journal(self):
        self.retained_old_batch()
        atomic(self.root / "members/bob/state/delivery.json", {
            "cursor":999999})
        self.assertEqual(self.send("different request")[0],2)
        self.assertEqual(self.stage(),"pasted")
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))

    def test_hook_handoff_retires_transport_without_fabricating_prompt_ack(self):
        self.retained_old_batch()
        atomic(self.root / "members/bob/state/delivery.json", {
            "cursor":5, "request_receipts":["fixture-one"]})
        self.assertEqual(self.send("different request")[0],0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))
        retired=json.loads((self.root / "members/bob/state/retired-prompt-delivery.json").read_text())
        self.assertEqual(retired['stage'],'retired')
        self.assertEqual(retired['ids'],['fixture-one'])
        self.assertFalse((self.root / "members/bob/state/journal-prompt-receipt.json").exists())

    def test_hook_handoff_does_not_retire_or_submit_an_occupied_draft(self):
        self.retained_old_batch()
        atomic(self.root / "members/bob/state/delivery.json", {
            "request_receipts":["fixture-one"]})
        self.terminal.draft='UNRELATED OWNER DRAFT'
        self.assertEqual(self.send("different request")[0],3)
        self.assertEqual(self.stage(),'pasted')
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))
        self.assertFalse((self.root / "members/bob/state/retired-prompt-delivery.json").exists())

    def native_history(self, *, role='user', session='fixture-session', timestamp='2026-10-14T09:32:49.205Z', text=None):
        home=self.root/'codex'
        path=home/'sessions'/f'rollout-fixture-session.jsonl'
        path.parent.mkdir(parents=True,exist_ok=True)
        rows=[{'type':'session_meta','payload':{'id':session}},
            {'type':'response_item','timestamp':timestamp,'payload':{
                'type':'message','role':role,'content':[{'type':'input_text','text':self.message if text is None else text}]}}]
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        cache={'session':'fixture-session','path':str(path)}
        atomic(self.root/'members/bob/state/quota-cache.json',cache)
        return home,cache

    def test_exact_native_user_turn_recovers_overwritten_old_receipt(self):
        self.retained_old_batch()
        home,cache=self.native_history()
        atomic(self.root/'telemetry/members/bob.json',{
            'session_id':'fixture-session','status':'idle','harness':'codex'})
        (self.root/'members/bob/state/last-submitted-prompt').write_text('960000000000000000 '+prompt.prompt_hash('later prompt'))
        with patch.dict('os.environ',{'CODEX_HOME':str(home)}):
            self.assertEqual(self.send('different request')[0],0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))
        proof=json.loads((self.root/'members/bob/state/journal-prompt-receipt.json').read_text())
        self.assertEqual(proof['event'],'NativeUserMessage')
        self.assertEqual(proof['binding']['ids'],['fixture-one'])

    def test_native_proof_rejects_quoted_developer_and_other_session_text(self):
        self.retained_old_batch()
        record=json.loads((self.root/'members/bob/state/prompt-delivery.json').read_text())
        cases=({'role':'developer'},{'role':'assistant'}, {'session':'another-session'},
            {'text':'quoted\n'+self.message},{'timestamp':'1990-01-01T00:00:00Z'},
            {'timestamp':'2026-10-14T09:32:49'})
        for case in cases:
            with self.subTest(case=case):
                home,cache=self.native_history(**case)
                self.assertIsNone(prompt.native_receipt(record,cache,home))

    def test_native_proof_requires_cached_session_and_codex_home_boundary(self):
        self.retained_old_batch()
        record=json.loads((self.root/'members/bob/state/prompt-delivery.json').read_text())
        home,cache=self.native_history()
        self.assertIsNotNone(prompt.native_receipt(record,cache,home))
        self.assertIsNone(prompt.native_receipt(record,dict(cache,session='other'),home))
        outside=self.root/'outside';(outside/'sessions').mkdir(parents=True)
        self.assertIsNone(prompt.native_receipt(record,cache,outside))

    def test_native_reconciliation_does_not_submit_an_unrelated_draft(self):
        self.retained_old_batch()
        home,cache=self.native_history()
        atomic(self.root/'telemetry/members/bob.json',{
            'session_id':'fixture-session','status':'idle','harness':'codex'})
        self.terminal.draft='UNRELATED OWNER DRAFT'
        with patch.dict('os.environ',{'CODEX_HOME':str(home)}):
            self.assertEqual(self.send('different request')[0],3)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))

    def test_old_receipt_overwritten_by_new_prompt_stays_unconfirmed(self):
        self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + prompt.prompt_hash("another prompt"))
        atomic(self.root / "members/bob/state/delivery.json", {
            "cursor":999999})
        self.assertEqual(self.send("different request")[0],2)
        self.assertEqual(self.stage(),"pasted")
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))

    def test_old_receipt_different_session_cannot_retire_journal(self):
        old_hash = self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + old_hash)
        atomic(self.root / "telemetry/members/bob.json", {"session_id":"other-session", "status":"idle"})
        self.assertEqual(self.send("different request")[0],2)
        self.assertEqual(self.stage(),"pasted")
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))

    def test_exact_old_ack_does_not_replay_old_batch(self):
        old_hash = self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + old_hash)
        self.assertEqual(self.send()[0],0)
        self.assertEqual(self.stage(),"acknowledged")
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))

    def test_reconciliation_then_interrupted_new_paste_keeps_retry_idempotent(self):
        old_hash = self.retained_old_batch()
        (self.root / "members/bob/state/last-submitted-prompt").write_text("950000000000000000 " + old_hash)
        new = "HIVE messages\n[hive-message id=fixture-two]\nnew request"
        self.terminal.fail_paste = True
        self.assertEqual(self.send(new)[0],2)
        self.terminal.fail_paste = False
        self.assertEqual(self.send(new)[0],0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))

    def test_receipt_evidence_must_be_complete_new_and_exact(self):
        old_hash = self.retained_old_batch()
        record=json.loads((self.root / "members/bob/state/prompt-delivery.json").read_text())
        for receipt in ("", "900000000000000000 " + old_hash,
                        "800000000000000000 " + old_hash,
                        "not-a-number " + old_hash,
                        "950000000000000000 " + "b" * 64):
            with self.subTest(receipt=receipt):
                self.assertFalse(prompt.receipt_acknowledges(record,receipt,"fixture-session"))
        self.assertTrue(prompt.receipt_acknowledges(record,"950000000000000000 " + old_hash,"fixture-session"))
        self.assertFalse(prompt.receipt_acknowledges(dict(record,hash=None),"950000000000000000 " + old_hash,"fixture-session"))
        self.assertFalse(prompt.receipt_acknowledges(dict(record,receipt_before=None),"950000000000000000 " + old_hash,"fixture-session"))

    def test_hook_proof_survives_later_prompt_receipt_overwrite(self):
        self.retained_old_batch()
        with patch("hive_prompt.time.time_ns",return_value=950000000000000000):
            self.assertTrue(prompt.record_receipt(self.root,"bob",{"prompt":self.message,"session_id":"fixture-session"}))
        (self.root / "members/bob/state/last-submitted-prompt").write_text("960000000000000000 " + prompt.prompt_hash("later prompt"))
        self.assertEqual(self.send("new different request")[0],0)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(1,1))

    def test_hook_proof_does_not_authorize_unrelated_draft(self):
        self.retained_old_batch()
        with patch("hive_prompt.time.time_ns",return_value=950000000000000000):
            self.assertTrue(prompt.record_receipt(self.root,"bob",{"prompt":self.message,"session_id":"fixture-session"}))
        self.terminal.draft="OTHER USER DRAFT"
        self.assertEqual(self.send("new different request")[0],3)
        self.assertEqual((self.terminal.pastes,self.terminal.enters),(0,0))
        self.assertEqual(self.terminal.draft,"OTHER USER DRAFT")

    def test_hook_proof_requires_exact_journal_hash_ids_session_and_before(self):
        self.retained_old_batch()
        with patch("hive_prompt.time.time_ns",return_value=950000000000000000):
            for payload in ({"prompt":"wrong body","session_id":"fixture-session"},
                            {"prompt":self.message,"session_id":"other"},
                            {"prompt":self.message}):
                self.assertFalse(prompt.record_receipt(self.root,"bob",payload))
            self.assertTrue(prompt.record_receipt(self.root,"bob",{"prompt":self.message,"session_id":"fixture-session"}))
        proof=json.loads((self.root / "members/bob/state/journal-prompt-receipt.json").read_text())
        record=json.loads((self.root / "members/bob/state/prompt-delivery.json").read_text())
        self.assertTrue(prompt.proof_acknowledges(record,proof,"fixture-session"))
        self.assertFalse(prompt.proof_acknowledges(dict(record,receipt_before="910000000000000000 " + "a"*64),proof,"fixture-session"))
        self.assertFalse(prompt.proof_acknowledges(dict(record,ids=["other-request"]),proof,"fixture-session"))
        self.assertFalse(prompt.proof_acknowledges(record,dict(proof,event="PostToolUse"),"fixture-session"))

    def test_unrelated_later_prompt_does_not_overwrite_prior_exact_proof(self):
        self.retained_old_batch()
        with patch("hive_prompt.time.time_ns",return_value=950000000000000000):
            self.assertTrue(prompt.record_receipt(self.root,"bob",{"prompt":self.message,"session_id":"fixture-session"}))
            original=(self.root / "members/bob/state/journal-prompt-receipt.json").read_bytes()
            self.assertFalse(prompt.record_receipt(self.root,"bob",{"prompt":"another request","session_id":"fixture-session"}))
            self.assertEqual((self.root / "members/bob/state/journal-prompt-receipt.json").read_bytes(),original)

    def test_actual_prompt_hook_records_exact_proof_in_synthetic_root(self):
        import os
        self.retained_old_batch()
        (self.root / ".room-generation").write_text("0\n")
        repo=Path(__file__).resolve().parents[1]
        env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_SHARE=str(repo/"share"),HIVE_BIN_DIR=str(repo/"bin"),HIVE_QUIET_MINUTES="0",PATH=str(repo/"bin")+":"+os.environ["PATH"])
        payload=json.dumps({"prompt":self.message,"session_id":"fixture-session","cwd":str(self.root)})
        subprocess.run([str(repo/"bin/hive-hook"),"claude","UserPromptSubmit"],input=payload,env=env,text=True,capture_output=True,check=True)
        proof=json.loads((self.root / "members/bob/state/journal-prompt-receipt.json").read_text())
        record=json.loads((self.root / "members/bob/state/prompt-delivery.json").read_text())
        self.assertTrue(prompt.proof_acknowledges(record,proof,"fixture-session"))
        self.assertEqual(record['stage'],'pasted')

    def test_footer_with_single_line_and_blank_multiline_drafts(self):
        for message in ("one line", "first\n\nsecond\n\n"):
            with self.subTest(message=message):
                self.terminal.footer = "  100% context left"
                self.assertEqual(self.send(message)[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (2, 2))

    def test_footer_retry_after_interrupted_paste_never_repastes(self):
        self.terminal.footer = "  model status · 97% context left"
        self.terminal.fail_paste = True
        self.assertEqual(self.send()[0], 2)
        self.terminal.fail_paste = False
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

    def test_footer_still_requires_matching_ack(self):
        self.terminal.footer = "  model status · 97% context left"
        self.terminal.ack_delay = 9999
        self.assertEqual(self.send()[0], 2)
        self.assertEqual(self.stage(), "accepted")
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))
        self.terminal.acknowledge()
        self.assertEqual(self.send()[0], 0)

    def test_unknown_below_cursor_text_is_not_dropped_as_footer(self):
        self.terminal.footer = "  unexpected status or another draft"
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_extra_draft_below_cursor_blocks_even_with_real_footer(self):
        self.terminal.footer = "  97% context left"
        self.terminal.below_cursor = "  USER ADDITION"
        self.assertEqual(self.send()[0], 2)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 0))

    def test_footer_like_body_text_is_retained(self):
        message = "first\n\n97% context left\nlast"
        rows = ["Transcript", "› first", "", "  97% context left", "  last", "", "  98% context left"]
        parts = prompt.composer(rows, 1, owned=True, expected=message)
        self.assertTrue(prompt.matches(parts, message))
        self.assertIn("97% context left", parts)
        self.assertNotIn("98% context left", parts)
        self.assertFalse(prompt.matches(prompt.composer(rows, 1, owned=True, expected="first"), "first"))

    def test_footer_requires_bottom_position_and_blank_separation(self):
        for rows, cursor in ((["› first", "  97% context left"], 0),
                             (["› first", "", "  97% context left", "", "", ""], 0)):
            with self.subTest(rows=rows):
                self.assertFalse(prompt.matches(prompt.composer(rows, cursor, owned=True, expected="first"), "first"))

    def test_footer_does_not_allow_shortened_or_mismatched_owned_message(self):
        rows = ["› first", "", "  97% context left"]
        self.assertFalse(prompt.matches(prompt.composer(rows, 0, owned=True, expected="first and more"), "first and more"))
        self.assertFalse(prompt.matches(prompt.composer(rows, 0, owned=True), "first"))

    def test_collapsed_owned_paste_with_footer(self):
        self.terminal.footer = "  ? for shortcuts"
        self.terminal.collapsed = True
        self.assertEqual(self.send()[0], 0)
        self.assertEqual((self.terminal.pastes, self.terminal.enters), (1, 1))

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
