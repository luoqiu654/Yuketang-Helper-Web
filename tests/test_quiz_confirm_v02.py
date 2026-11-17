import os, sys, tempfile, unittest, time, threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from quiz import store
from quiz.workflow import confirm, WorkflowError
from lesson import Lesson

class FakeServer:
    def __init__(self, state='OPEN', accepted=True): self.state, self.accepted, self.calls = state, accepted, 0
    def check(self, quiz): return self.state
    def submit(self, quiz, answer):
        self.calls += 1
        if self.accepted is None: raise TimeoutError()
        return self.accepted

class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        p=patch.object(store, 'DB', Path(self.temp.name)/'test.sqlite3'); p.start(); self.addCleanup(p.stop)
        les=Lesson('a', {'lessonid': 1, 'lessonname': 'course', 'classroomid': 2}, '', 'pro.yuketang.cn', {}, lambda *a: None)
        self.problem={'problemId': 4, 'problemType': 1, 'options': [{'key':'A'},{'key':'B'}], 'result':None}
        store.create('a',les,self.problem)
        self.q=store.get_by_problem('a',1,4)
    def call(self, adapter, answer=['A'], key='key', revision=None):
        return confirm('a', self.q['id'], answer, self.q['revision'] if revision is None else revision, key, adapter)
    def test_success_idempotency(self):
        server=FakeServer(); a=self.call(server); b=self.call(server)
        self.assertEqual(a['status'],'SUBMITTED'); self.assertEqual(b['status'],'SUBMITTED'); self.assertEqual(server.calls,1)
        with self.assertRaises(WorkflowError) as e: self.call(server,['B'])
        self.assertEqual(e.exception.status,409)
    def test_status_and_validation(self):
        for state, code in [('ALREADY_ANSWERED',409), ('EXPIRED',410), ('UNAVAILABLE',503)]:
            with self.subTest(state=state):
                self.setUp(); server=FakeServer(state)
                with self.assertRaises(WorkflowError) as e: self.call(server)
                self.assertEqual(e.exception.status,code); self.assertEqual(server.calls,0)
    def test_invalid_answer_is_rejected(self):
        server=FakeServer()
        with self.assertRaises(WorkflowError) as e: self.call(server,['Z'])
        self.assertEqual(e.exception.status,422)
        self.assertEqual(server.calls,0)
    def test_timeout_never_retries(self):
        server=FakeServer(accepted=None)
        with self.assertRaises(WorkflowError): self.call(server)
        q=store.get('a',self.q['id']); self.assertEqual(q['status'],'FAILED')
        with self.assertRaises(WorkflowError): self.call(server, key='different', revision=q['revision'])
        self.assertEqual(server.calls,1)
    def test_concurrent_claim_once(self):
        server=FakeServer(); barrier=threading.Barrier(3); results=[]
        def worker():
            barrier.wait()
            try: results.append(self.call(server))
            except WorkflowError as ex: results.append(ex.status)
        ts=[threading.Thread(target=worker) for _ in range(2)]
        for t in ts: t.start()
        barrier.wait()
        for t in ts: t.join()
        self.assertEqual(server.calls,1); self.assertEqual(len(results),2)
    def test_expired_and_no_confirmation(self):
        server=FakeServer(); self.assertEqual(server.calls,0)
        with store.connect() as db:
            import json
            data=json.loads(db.execute('SELECT data FROM quizzes').fetchone()['data']); data['deadline_at']=time.time()-1
            db.execute('UPDATE quizzes SET data=?', (json.dumps(data),))
        with self.assertRaises(WorkflowError) as e: self.call(server)
        self.assertEqual(e.exception.status,410); self.assertEqual(server.calls,0)
        self.assertEqual(store.get('a',self.q['id'])['status'],'EXPIRED')
    def test_real_adapter_is_closed(self):
        with self.assertRaises(WorkflowError) as e: confirm('a',self.q['id'],['A'],self.q['revision'],'real')
        self.assertEqual(e.exception.status,503)

if __name__ == '__main__': unittest.main()
