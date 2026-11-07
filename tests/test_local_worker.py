import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import local_worker as worker
from store import SpaceError, Store


class LocalWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        bot = self.store.create_bot("Worker tests")
        self.space = self.store.create_space(bot["id"], "Test")['id']
        self.project = self.store.project_directory(self.space, create=True)

    def tearDown(self):
        self.temp.cleanup()

    def handler(self, source):
        target = self.project / 'handler.py'
        target.write_text(source)
        return target

    def action(self, request='one', kind='stats'):
        return self.store.action(self.space, kind, {'input': 3}, request, expected_revision=0)

    def wait(self, event, expected=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = worker.status(self.store, self.space, event['id'])
            if expected is None and result['status'] in {'completed', 'failed', 'needs_reconcile'}:
                return result
            if result['status'] == expected:
                return result
            time.sleep(.01)
        self.fail('Worker did not reach expected status: ' + repr(result))

    def register(self, source):
        return worker.register(self.store, self.space, self.handler(source), ['stats'])

    def test_snapshot_result_patch_and_private_output(self):
        self.register('''import json,sys,os
x=json.load(sys.stdin)
assert x['refs']['expected_revision']==x['snapshot']['revision']==0
assert os.getcwd()==x['refs']['project_dir']
assert 'SECRET_CANARY' not in os.environ
print(json.dumps({'result':{'count':x['event']['payload']['input']},'patch':{'count':3}}))
''')
        event = self.action()
        with patch.dict(os.environ, {'SECRET_CANARY':'must not be inherited'}):
            response = worker.dispatch(self.store, self.space, event)
            self.assertEqual(response['delivery'], 'local')
            final = self.wait(event)
        self.assertEqual(final['status'], 'completed')
        self.assertNotIn('output', final)
        self.assertEqual(self.store.detail(self.space)['state'], {'count':3})
        self.assertEqual(self.store.event(self.space,event['id'])['result'], {'count':3})
        self.assertEqual(worker.read_output(self.store,self.space,event['id'])['output']['patch'], {'count':3})

    def test_parallel_duplicate_dispatch_executes_once(self):
        self.register('''import json,sys,pathlib,time
x=json.load(sys.stdin)
p=pathlib.Path(x['refs']['project_dir'])/'runs'
with p.open('a') as f:f.write(x['refs']['event_id']+'\\n')
time.sleep(.1)
print('{"result":"ok"}')
''')
        event=self.action()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:worker.dispatch(Store(self.temp.name),self.space,event),range(8)))
        self.assertTrue(all(result['accepted'] for result in results))
        self.assertEqual(self.wait(event)['status'],'completed')
        self.assertEqual((self.project/'runs').read_text().splitlines(),[event['id']])

    def test_unregistered_type_never_executes(self):
        self.register("raise RuntimeError('must not execute')")
        event=self.action(kind='unapproved')
        self.assertEqual(worker.dispatch(self.store,self.space,event)['delivery'],'manual')
        self.assertEqual(worker.status(self.store,self.space,event['id'])['status'],'not_dispatched')

    def test_symlink_or_outside_handler_is_rejected(self):
        outside=Path(self.temp.name)/'outside.py';outside.write_text('pass')
        for target in (outside,self.project/'escape.py'):
            if target.name=='escape.py':target.symlink_to(outside)
            with self.assertRaises(SpaceError):worker.register(self.store,self.space,target,['stats'])
        directory=self.project/'linked';directory.symlink_to(Path(self.temp.name),target_is_directory=True)
        with self.assertRaises(SpaceError):worker.register(self.store,self.space,directory/'outside.py',['stats'])

    def test_symlink_swap_after_registration_fails_closed(self):
        self.register("print('{\"result\":true}')")
        outside=Path(self.temp.name)/'outside.py';outside.write_text("raise RuntimeError('outside')")
        (self.project/'handler.py').unlink();(self.project/'handler.py').symlink_to(outside)
        event=self.action();worker.dispatch(self.store,self.space,event)
        self.assertEqual(self.wait(event)['code'],'worker_unavailable')

    def test_timeout_and_stderr_are_structured_and_not_echoed(self):
        self.register("import sys,time;print('PRIVATE_CANARY',file=sys.stderr);time.sleep(2)")
        event=self.action()
        with patch.object(worker,'TIMEOUT_SECONDS',.15):
            worker.dispatch(self.store,self.space,event); result=self.wait(event)
        self.assertEqual(result['code'],'worker_timeout')
        self.assertNotIn('PRIVATE_CANARY',json.dumps(result))
        self.assertEqual(self.store.event(self.space,event['id'])['status'],'failed')

    def test_streaming_output_limit(self):
        self.register("import sys;sys.stdout.write('x'*(1024*1024+8192));sys.stdout.flush()")
        event=self.action();worker.dispatch(self.store,self.space,event)
        self.assertEqual(self.wait(event)['code'],'worker_output_limit')

    def test_cas_conflict_retains_result_and_emits_durable_failure(self):
        self.register("import time;time.sleep(.25);print('{\"result\":{\"answer\":42},\"patch\":{\"note\":\"worker\"}}')")
        event=self.action();worker.dispatch(self.store,self.space,event)
        self.wait(event,'running');self.store.patch_state(self.space,{'note':'user'},0)
        result=self.wait(event)
        self.assertEqual(result['status'],'needs_reconcile')
        self.assertEqual(self.store.detail(self.space)['state'],{'note':'user'})
        self.assertTrue(result['output_available'])
        self.assertEqual(worker.read_output(self.store,self.space,event['id'])['output']['result'],{'answer':42})
        self.assertTrue(any(e['type']=='worker.needs_reconcile' for e in self.store.events(self.space)))
        self.assertTrue(worker.dispatch(self.store,self.space,event)['replayed'])

    def test_recovery_only_dispatches_never_claimed_actions(self):
        self.register("print('{\"result\":true}')")
        first=self.action('claimed');second=self.action('new')
        with self.store.connect(write=True) as db:
            db.execute("INSERT INTO local_worker_runs(event_id,space_id,status,revision,created_at) VALUES (?,?,?,?,?)",
                       (first['id'],self.space,'running',0,'old'))
        worker.recover(self.store)
        self.assertEqual(self.wait(second)['status'],'completed')
        self.assertEqual(worker.status(self.store,self.space,first['id'])['status'],'running')

    def test_max_two_concurrent_children(self):
        self.register('''import json,sys,pathlib,fcntl,time
x=json.load(sys.stdin);p=pathlib.Path(x['refs']['project_dir']);lock=(p/'lock').open('a+')
def update(delta):
 fcntl.flock(lock,fcntl.LOCK_EX)
 state=json.loads((p/'count').read_text()) if (p/'count').exists() else {'active':0,'peak':0,'runs':0}
 state['active']+=delta;state['peak']=max(state['peak'],state['active']);state['runs']+=int(delta>0)
 (p/'count').write_text(json.dumps(state));fcntl.flock(lock,fcntl.LOCK_UN)
update(1);time.sleep(.2);update(-1)
print('{"result":true}')
''')
        events=[self.action(str(i)) for i in range(4)]
        for event in events:worker.dispatch(self.store,self.space,event)
        for event in events:self.assertEqual(self.wait(event)['status'],'completed')
        data=json.loads((self.project/'count').read_text())
        self.assertEqual(data,{'active':0,'peak':2,'runs':4})

    def test_bounded_queue_leaves_unclaimed_action_retryable(self):
        self.register("import time;time.sleep(.2);print('{\"result\":true}')")
        first=self.action('a');second=self.action('b')
        with patch.object(worker,'_CAPACITY',threading.BoundedSemaphore(1)):
            worker.dispatch(self.store,self.space,first)
            self.assertEqual(worker.dispatch(self.store,self.space,second)['code'],'worker_queue_full')
            self.assertEqual(worker.status(self.store,self.space,second['id'])['status'],'not_dispatched')
            self.wait(first);worker.dispatch(self.store,self.space,second);self.wait(second)

    def test_recovery_filters_types_before_backlog_limit(self):
        self.register("print('{\"result\":true}')")
        for number in range(201):self.action('unapproved-'+str(number),kind='unapproved')
        eligible=self.action('eligible')
        worker.recover(self.store)
        self.assertEqual(self.wait(eligible)['status'],'completed')

    def test_shutdown_kills_owned_process_group_and_stops_dispatch(self):
        self.handler('''import json,sys,os,pathlib,subprocess,time
x=json.load(sys.stdin)
child=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
(pathlib.Path(x['refs']['project_dir'])/'pids.json').write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(30)
''')
        code='''import json,sys,time,subprocess
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import local_worker as w
from store import Store
s=Store(sys.argv[2]);space=sys.argv[3];project=s.project_directory(space)
w.register(s,space,project/'handler.py',['stats'])
e=s.action(space,'stats',{},'shutdown');w.dispatch(s,space,e)
deadline=time.monotonic()+5
while not (project/'pids.json').exists():
 if time.monotonic()>deadline:raise SystemExit('owned child did not start')
 time.sleep(.01)
pids=json.loads((project/'pids.json').read_text())
w.shutdown();w.shutdown()
deadline=time.monotonic()+2
while True:
 running=[]
 for pid in pids:
  state=subprocess.run(['/bin/ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
  if state and not state.startswith('Z'):running.append(pid)
 if not running or time.monotonic()>deadline:break
 time.sleep(.02)
assert not running
assert w.dispatch(s,space,e)['code']=='worker_stopped'
print(json.dumps({'group_stopped':True,'launches_stopped':True}))
'''
        result=subprocess.run(['/usr/bin/python3','-I','-B','-c',code,str(Path(__file__).resolve().parents[1]/'bridge'),self.temp.name,self.space],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'group_stopped':True,'launches_stopped':True})


if __name__=='__main__':unittest.main()
