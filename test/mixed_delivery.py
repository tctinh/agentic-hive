"""Actual guarded sender and hook wrapper over a synthetic terminal only."""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE/"share"))
import hive_messages as messages
from hive_runtime import atomic


class MixedDelivery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="hive-mixed-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for member in ("alice","bob","beekeeper"):
            (self.root/"members"/member/"state").mkdir(parents=True)
        messages.initialize(self.root)
        atomic(self.root/"members/bob/state/launch.json",{"harness":Path(sys.executable).name,"dir":str(self.root)})
        atomic(self.root/"telemetry/members/bob.json",{"harness":Path(sys.executable).name,"session_id":"synthetic-session","status":"idle"})
        fake=self.root/"bin"
        fake.mkdir()
        terminal=fake/"tmux"
        terminal.write_text('#!'+sys.executable+'''\nimport hashlib,json,os,subprocess,sys,time
from pathlib import Path
root=Path(os.environ["HIVE_ROOT"]);state=root/"members/bob/state";command=sys.argv[1]
if command=="has-session":raise SystemExit(0)
if command=="list-panes":print(os.environ["HIVE_TEST_AGENT_PID"])
elif command=="display-message":print("1 0 %synthetic")
elif command=="capture-pane":
 text="[Pasted text 8 lines]" if (root/"pasted").exists() and not (root/"cleared").exists() else "Ask Codex to do anything"
 print("› "+text+"\\n› "+text)
elif command=="load-buffer":
 (root/"terminal-prompt").write_text(sys.stdin.read());(root/"cleared").unlink(missing_ok=True)
elif command=="paste-buffer":(root/"pasted").touch()
elif command=="send-keys":
 (root/"enters").open("a").write("Enter\\n")
 (root/"cleared").touch()
 if os.environ.get("HIVE_TEST_UNCONFIRMED")!="1":
  prompt=(root/"terminal-prompt").read_text()
  (state/"last-submitted-prompt").write_text(str(time.time_ns())+" "+hashlib.sha256(prompt.strip().encode()).hexdigest()+"\\n")
  (state/"last-submitted-prompt.json").write_text(json.dumps({"receipt":(state/"last-submitted-prompt").read_text().strip(),"session_id":"synthetic-session","event":"UserPromptSubmit"}))
  if os.environ.get("HIVE_TEST_CALLBACK")=="1":
   if os.environ.get("HIVE_TEST_NEW_REQUEST")=="1":
    sys.path.insert(0,os.environ["HIVE_SHARE"])
    import hive_messages
    hive_messages.post(root,"alice","Arrived while the sender waited",channel="dm",kind="request",recipients=["bob"],message_id="during-confirmation")
   result=subprocess.run([os.environ["HIVE_TEST_HOOK"],"claude","UserPromptSubmit"],input=json.dumps({"prompt":prompt,"cwd":str(root),"session_id":"synthetic-session"}),capture_output=True,text=True,check=True)
   (root/"callback.json").write_text(result.stdout)
''')
        terminal.chmod(0o755)
        sleep=fake/"sleep"
        sleep.write_text('#!/usr/bin/env bash\nexit 0\n')
        sleep.chmod(0o755)
        self.env=dict(os.environ,HIVE_ROOT=str(self.root),HIVE_MEMBER="bob",HIVE_USER=pwd.getpwuid(os.getuid()).pw_name,
                      HIVE_SHARE=str(SOURCE/"share"),HIVE_MEMBER_BIN=str(SOURCE/"bin/hive-member"),
                      HIVE_TEST_HOOK=str(SOURCE/"bin/hive-hook"),HIVE_TEST_AGENT_PID=str(os.getpid()),
                      HIVE_DELIVERY_CACHE="off",HIVE_QUIET_MINUTES="0",PATH=str(fake)+":"+str(SOURCE/"bin")+":"+os.environ["PATH"])

    def post(self, identity, **kwargs):
        return messages.post(self.root,"alice","Work for "+identity,channel="dm",kind="request",recipients=["bob"],message_id=identity,**kwargs)

    def dispatch(self,row,**environment):
        with patch.dict(os.environ,dict(self.env,**environment)):
            return messages.dispatch(self.root,row)

    def hook(self):
        run=subprocess.run([str(SOURCE/"bin/hive-hook"),"claude","PostToolUse"],env=self.env,
                           input=json.dumps({"cwd":str(self.root),"session_id":"synthetic-session"}),text=True,capture_output=True,timeout=5)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertFalse((self.root/"members/bob/state/hook-delivery-error.json").exists(),run.stderr)
        if not run.stdout:return []
        context=json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"]
        return re.findall(r"^\[hive-message id=([^\]]+)\]",context,re.M)

    def test_confirmed_guarded_sender_does_not_replay_retained_hook_offer(self):
        request=self.post("first")
        messages.prepare(self.root,"bob")
        self.assertEqual(self.dispatch(request)["sent"],["bob"])
        value=messages.state(self.root,"bob")
        self.assertIn("first",value["request_receipts"])
        self.assertEqual(json.loads((self.root/"members/bob/state/prompt-delivery.json").read_text())["stage"],"acknowledged")
        self.assertFalse((self.root/"callback.json").exists())
        self.assertNotIn("first",self.hook())

    def test_partial_confirmation_preserves_remaining_request(self):
        first=self.post("first");self.post("second")
        old=messages.prepare(self.root,"bob")
        self.assertEqual(old["ids"],["first","second"])
        self.assertEqual(self.dispatch(first)["sent"],["bob"])
        self.assertEqual(messages.state(self.root,"bob")["request_receipts"],["first"])
        self.assertEqual(self.hook(),["second"])
        self.assertEqual(set(messages.state(self.root,"bob")["request_receipts"]),{"first","second"})
        self.assertEqual(self.hook(),[])

    def test_confirmed_out_of_order_seen_request_does_not_replay(self):
        messages.post(self.root,"alice","Earlier passive context",message_id="gap")
        request=self.post("urgent",priority="urgent")
        old=messages.prepare(self.root,"bob",max_messages=1)
        self.assertEqual(old["ids"],["urgent"])
        self.assertEqual(self.dispatch(request)["sent"],["bob"])
        value=messages.state(self.root,"bob")
        self.assertEqual(value["cursor"],0)
        self.assertIn("urgent",value["seen"])
        self.assertEqual(self.hook(),["gap"])
        self.assertEqual(self.hook(),[])

    def test_existing_confirmed_receipt_revalidates_offer_on_restart(self):
        self.post("first");self.post("second")
        old=messages.prepare(self.root,"bob")
        value=messages.state(self.root,"bob")
        messages.advance(messages.records(self.root),value,["first"])
        messages.save_state(self.root,"bob",value)  # Reproduce the inherited saved state.
        self.assertEqual(messages.state(self.root,"bob")["offered"],old)
        self.assertEqual(self.hook(),["second"])
        self.assertEqual(set(messages.state(self.root,"bob")["request_receipts"]),{"first","second"})

    def test_uncertain_sender_retains_offer_until_actual_hook_handoff(self):
        request=self.post("first")
        old=messages.prepare(self.root,"bob")
        self.assertIn("bob",self.dispatch(request,HIVE_TEST_UNCONFIRMED="1")["unconfirmed"])
        value=messages.state(self.root,"bob")
        self.assertEqual(value["offered"],old)
        self.assertEqual(value["request_receipts"],[])
        self.assertEqual(self.hook(),["first"])
        self.assertIn("first",messages.state(self.root,"bob")["request_receipts"])
        self.assertNotEqual(json.loads((self.root/"members/bob/state/prompt-delivery.json").read_text())["stage"],"acknowledged")
        self.assertEqual(self.hook(),[])

    def test_durable_receipt_wins_when_cursor_and_seen_lag(self):
        first=self.post("first");self.post("second")
        messages.prepare(self.root,"bob")
        value=messages.state(self.root,"bob")
        value["request_receipts"]=["first"]
        messages.save_state(self.root,"bob",value)
        self.assertEqual(self.dispatch(first)["sent"],[])
        self.assertEqual(self.hook(),["second"])
        value=messages.state(self.root,"bob")
        self.assertEqual(value["cursor"],2)
        self.assertEqual(value["seen"],[])
        self.assertEqual(set(value["request_receipts"]),{"first","second"})
        self.assertEqual(self.hook(),[])

    def test_callback_and_new_work_during_confirmation_lose_no_receipts(self):
        first=self.post("first");self.post("second")
        messages.prepare(self.root,"bob")
        self.assertEqual(self.dispatch(first,HIVE_TEST_CALLBACK="1",HIVE_TEST_NEW_REQUEST="1")["sent"],["bob"])
        callback=json.loads((self.root/"callback.json").read_text())["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("[hive-message id=first]",callback)
        self.assertIn("[hive-message id=second]",callback)
        self.assertIn("[hive-message id=during-confirmation]",callback)
        self.assertEqual(set(messages.state(self.root,"bob")["request_receipts"]),{"first","second","during-confirmation"})
        self.assertEqual(self.hook(),[])

    def test_unrelated_confirmation_keeps_nonoverlapping_offer(self):
        self.post("first")
        old=messages.prepare(self.root,"bob")
        later=self.post("later")
        self.assertEqual(self.dispatch(later)["sent"],["bob"])
        self.assertEqual(messages.state(self.root,"bob")["offered"],old)
        self.assertEqual(self.hook(),["first"])
        self.assertEqual(set(messages.state(self.root,"bob")["request_receipts"]),{"first","later"})

    def test_inflight_hook_offer_retires_without_losing_unconfirmed_remainder(self):
        first=self.post("first");self.post("second")
        old=messages.prepare(self.root,"bob")
        self.assertEqual(self.dispatch(first)["sent"],["bob"])
        with self.assertRaisesRegex(ValueError,"offered batch changed"):
            messages.accept(self.root,"bob",old)
        self.assertNotIn("second",messages.state(self.root,"bob")["request_receipts"])
        self.assertEqual(self.hook(),["second"])


if __name__=="__main__":
    unittest.main()
