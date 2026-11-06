"""Read-only operational visibility; no real network, member or transport."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"share"))
import hive_inspect as inspect
import hive_messages as messages
import hive_prompt
from hive_runtime import atomic,load


class Inspection(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        for name in ('alice','bob','beekeeper'):(self.root/'members'/name/'state').mkdir(parents=True)
        messages.initialize(self.root)
        self.state=self.root/'members/bob/state'
        atomic(self.root/'telemetry/members/bob.json',{'pid':11,'status':'idle','last_activity':'2000-01-01T00:00:00+00:00'})
        self.web={'members':[{'member':'bob','running':True,'pid':22,'state':'idle'}]}

    def snapshot(self,**options):
        return inspect.snapshot(self.root,web=self.web,**options)

    def test_previous_process_state_is_unknown_and_reading_is_side_effect_free(self):
        row=messages.post(self.root,'alice','new request',channel='dm',kind='request',recipients=['bob'])
        messages.post(self.root,'bob','accepted; work is still ongoing',kind='info')
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report=self.snapshot();bob=report['members'][0]
        self.assertTrue(report['read_only']);self.assertEqual(bob['state'],'unknown')
        self.assertEqual(bob['telemetry_binding'],'previous-process')
        self.assertEqual(bob['latest_requests'][0]['handoff'],'not recorded; unknown')
        self.assertFalse(bob['latest_requests'][0]['request_closed'])
        self.assertIn('accepted',bob['last_post']['text'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_same_process_long_working_state_survives_old_activity_time(self):
        atomic(self.root/'telemetry/members/bob.json',{'pid':22,'status':'working','last_activity':'2000-01-01T00:00:00+00:00'})
        self.assertEqual(self.snapshot()['members'][0]['state'],'working')

    def test_high_cursor_is_not_a_request_receipt(self):
        row=messages.post(self.root,'alice','new request',channel='dm',kind='request',recipients=['bob'])
        value=messages.state(self.root,'bob');value['cursor']=999999;messages.save_state(self.root,'bob',value)
        self.assertEqual(self.snapshot()['members'][0]['latest_requests'][0]['handoff'],'not recorded; unknown')

    def test_exact_submission_proof_does_not_invent_a_ledger_receipt(self):
        row=messages.post(self.root,'alice','new request',channel='dm',kind='request',recipients=['bob'])
        journal={'ids':[row['id']],'hash':'a'*64,'receipt_before':'','session_id':'fixture','stage':'acknowledged'}
        atomic(self.state/'prompt-delivery.json',journal)
        atomic(self.state/'journal-prompt-receipt.json',{'binding':hive_prompt.receipt_binding(journal),'event':'NativeUserMessage','receipt':'1000 '+'a'*64})
        request=self.snapshot()['members'][0]['latest_requests'][0]
        self.assertEqual(request['submission'],'proven in journal session')
        self.assertEqual(request['handoff'],'not recorded; unknown')
        self.assertFalse(request['request_closed'])

    def test_receipt_and_request_closure_are_separate_fields(self):
        row=messages.post(self.root,'alice','new request',channel='dm',kind='request',recipients=['bob'])
        messages.receipt(self.root,'bob',[row['id']])
        request=self.snapshot()['members'][0]['latest_requests'][0]
        self.assertEqual(request['handoff'],'recorded');self.assertFalse(request['request_closed'])

    def test_closed_null_journal_is_a_candidate_only_not_an_ack(self):
        row=messages.post(self.root,'alice','old request',channel='dm',kind='request',recipients=['bob'])
        atomic(self.state/'prompt-delivery.json',{'ids':[row['id']],'hash':'a'*64,'session_id':None,'stage':'pasted'})
        messages.post(self.root,'alice','cancel',channel='dm',kind='decision',reply_to=row['id'],resolution='cancel',recipients=['bob'])
        report=self.snapshot();delivery=report['members'][0]['delivery']
        self.assertTrue(delivery['closed_request_retirement_candidate'])
        self.assertEqual(load(self.state/'prompt-delivery.json')['stage'],'pasted')
        self.assertEqual(delivery['review_reason'],'missing journal session identity')

    def test_acknowledged_journal_is_not_a_retirement_candidate(self):
        self.test_closed_null_journal_is_a_candidate_only_not_an_ack()
        value=load(self.state/'prompt-delivery.json');value['stage']='acknowledged';atomic(self.state/'prompt-delivery.json',value)
        self.assertFalse(self.snapshot()['members'][0]['delivery']['closed_request_retirement_candidate'])

    def test_unavailable_web_does_not_invent_process_liveness(self):
        report=inspect.snapshot(self.root,member='bob',health={'ok':False,'error':'unavailable'})
        self.assertIsNone(report['members'][0]['running'])
        self.assertEqual(report['members'][0]['state'],'unknown')

    def test_recent_requests_are_bounded(self):
        for index in range(8):messages.post(self.root,'alice','request '+str(index),channel='dm',kind='request',recipients=['bob'])
        self.assertEqual(len(self.snapshot(limit=2)['members'][0]['latest_requests']),2)

    def test_corrupt_member_file_is_reported_without_mutation(self):
        (self.state/'prompt-delivery.json').write_text('{bad')
        report=self.snapshot()
        self.assertTrue(report['errors'])
        self.assertEqual((self.state/'prompt-delivery.json').read_text(),'{bad')

    def test_web_fetch_uses_get_and_reports_errors(self):
        with patch('hive_inspect.urllib.request.urlopen',side_effect=urllib.error.URLError('fixture')) as fetch:
            value,health=inspect.web_snapshot('http://fixture')
        fetch.assert_called_once_with('http://fixture/api/state',timeout=10)
        self.assertEqual(value,{})
        self.assertFalse(health['ok'])

    def test_render_explains_unknown_activity_and_never_claims_task_completion(self):
        text=inspect.render(self.snapshot())
        self.assertIn('previous process',text)
        self.assertIn('no message, acknowledgment or recovery attempted',text)


if __name__=='__main__':unittest.main()
