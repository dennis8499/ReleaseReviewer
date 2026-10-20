"""Real promisor reads must stay offline even when Git ignores NO_LAZY_FETCH."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MERGE = (ROOT / 'skills/merge-reviewer').is_dir()
if MERGE:
    from tests.fixtures import api, workspace
else:
    from support import context_helper, evidence
    import frozen_evidence
    api = context_helper.git
import review_runtime


class ProtocolFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='review-protocol-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'repo'
        environment = {'GITLAB_WORKSPACE_RUNTIME_DIR': str(self.root/'runtime'),
                       'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,
                       'GIT_TERMINAL_PROMPT':'0','NO_PROXY':'127.0.0.1,localhost'}
        self.patch = mock.patch.dict(os.environ, environment)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.git(self.root,'init','-b','main',str(self.repo))
        self.git(self.repo,'config','user.name','Offline Fixture')
        self.git(self.repo,'config','user.email','offline@example.invalid')
        self.git(self.repo,'config','commit.gpgsign','false')
        self.git(self.repo,'config','core.hooksPath',str(self.root/'no-hooks'))
        (self.repo/'payload.txt').write_bytes(b'fixed local evidence\n')
        self.git(self.repo,'add','payload.txt')
        self.git(self.repo,'commit','-m','fixture')

    def git(self, repo, *args):
        result = subprocess.run(['git','-C',str(repo),*args],capture_output=True,
                                env={**os.environ,'GIT_ALLOW_PROTOCOL':'file:http'},timeout=30)
        self.assertEqual(0,result.returncode,result.stderr.decode('utf-8',errors='replace'))
        return result.stdout.decode('utf-8').strip()

    def test_local_wrappers_override_inherited_and_custom_protocol_allowance(self):
        completed = subprocess.CompletedProcess([],0,b'',b'')
        unsafe = {'GIT_ALLOW_PROTOCOL':'http','GIT_NO_LAZY_FETCH':'0'}
        with mock.patch.dict(os.environ,unsafe), mock.patch.object(api.subprocess,'run',return_value=completed) as calls:
            api.run_git(self.repo,['cat-file','-e','HEAD'],env=unsafe)
            api.run_git_capture(self.repo,['cat-file','-e','HEAD'],env=unsafe)
            self.assertTrue(api.git_ok(self.repo,['cat-file','-e','HEAD']))
            if not MERGE:
                evidence.command({'repo':str(self.repo)},['cat-file','-e','HEAD'],env=unsafe)
        for call in calls.call_args_list:
            self.assertEqual('',call.kwargs['env']['GIT_ALLOW_PROTOCOL'])
            self.assertEqual('1',call.kwargs['env']['GIT_NO_LAZY_FETCH'])

    def test_real_http_promisor_missing_blob_never_contacts_transport(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_error(404,'fixture intentionally has no Git endpoint')
            def log_message(self,*_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join,5)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/promisor.git'
        self.git(self.repo,'remote','add','promisor',url)
        self.git(self.repo,'config','remote.promisor.promisor','true')
        self.git(self.repo,'config','remote.promisor.partialclonefilter','blob:none')
        missing = self.git(self.repo,'rev-parse','HEAD:payload.txt')
        blob = self.repo/'.git/objects'/missing[:2]/missing[2:]
        self.assertTrue(blob.is_file())
        blob.chmod(stat.S_IREAD|stat.S_IWRITE)
        blob.unlink()
        # The unguarded control must demonstrate a real HTTP lazy-fetch attempt.
        control = subprocess.run(['git','-C',str(self.repo),'cat-file','-p',missing],capture_output=True,
                                 env={**os.environ,'GIT_NO_LAZY_FETCH':'0','GIT_ALLOW_PROTOCOL':'http'},timeout=30)
        self.assertNotEqual(0,control.returncode)
        self.assertTrue(requests,'Fixture must demonstrate actual lazy-fetch HTTP traffic')
        requests.clear()
        # A caller-supplied env is intentionally complete, including PATH.
        # Git may be installed outside the POSIX fallback /bin:/usr/bin.
        unsafe = {**os.environ,'GIT_ALLOW_PROTOCOL':'http','GIT_NO_LAZY_FETCH':'0'}
        real_run = subprocess.run
        def legacy_git(*args,**kwargs):
            # Exercise the fallback on new Git too: emulate old Git ignoring this variable.
            kwargs['env'] = dict(kwargs['env'])
            kwargs['env'].pop('GIT_NO_LAZY_FETCH',None)
            return real_run(*args,**kwargs)
        with mock.patch.dict(os.environ,unsafe), mock.patch.object(api.subprocess,'run',side_effect=legacy_git):
            with self.assertRaises(api.ReviewContextError):
                api.run_git(self.repo,['show','HEAD:payload.txt'],env=unsafe)
            code,_out,_error = api.run_git_capture(self.repo,['show','HEAD:payload.txt'],env=unsafe)
            self.assertNotEqual(0,code)
            self.assertFalse(api.git_ok(self.repo,['cat-file','-e',missing]))
            with self.assertRaises((ValueError,api.ReviewContextError)):
                review_runtime.batch_blobs(lambda data:api.run_git(self.repo,['cat-file','--batch'],
                    binary=True,input_bytes=data,env=unsafe),[(missing,missing)],'sha1')
            if MERGE:
                with mock.patch.object(workspace,'evidence_repository',return_value=self.repo/'.git'):
                    frozen = workspace.source_environment(self.root,{'id':'repo-000','snapshot':{}},
                        {'objects':'repos/repo-000/snapshot.git/objects'},api)
                self.assertEqual('',frozen['GIT_ALLOW_PROTOCOL'])
            else:
                with mock.patch.object(frozen_evidence,'repository',return_value=self.repo/'.git'):
                    frozen = frozen_evidence.environment(self.root,{})
                self.assertEqual('',frozen['GIT_ALLOW_PROTOCOL'])
                with self.assertRaises(ValueError):
                    evidence.command({'repo':str(self.repo)},['show','HEAD:payload.txt'],env=unsafe)
                with self.assertRaises(ValueError):
                    review_runtime.batch_blobs(lambda data:evidence.command({'repo':str(self.repo)},
                        ['cat-file','--batch'],input_bytes=data,env=unsafe),[(missing,missing)],'sha1')
                with self.assertRaises(ValueError):
                    evidence.command({'repo':str(self.repo),'snapshot_policy':frozen_evidence.POLICY},
                        ['cat-file','blob',missing],env=frozen)
        self.assertEqual([],requests,'Local object operations must never send an HTTP request')
        self.assertFalse(blob.exists(),'Missing objects must remain missing and fail closed')

    def test_explicit_fetch_preserves_protocol_policy_and_uses_shared_lease(self):
        remote = self.root/'remote.git'
        self.git(self.root,'clone','--bare',str(self.repo),str(remote))
        with mock.patch.dict(os.environ,{'GIT_ALLOW_PROTOCOL':'http'}):
            with mock.patch.object(api.git_network_lease,'run',wraps=api.git_network_lease.run) as network:
                api.run_git(self.repo,['fetch','--no-tags',remote.as_uri(),
                            'refs/heads/main:refs/remotes/fixture/main'],env={'GIT_ALLOW_PROTOCOL':'file'})
        self.assertEqual(1,network.call_count)
        self.assertEqual('file',network.call_args.kwargs['env']['GIT_ALLOW_PROTOCOL'])
        self.assertEqual(self.git(self.repo,'rev-parse','main'),
                         api.run_git(self.repo,['rev-parse','refs/remotes/fixture/main']).strip())
        self.assertFalse((self.root/'runtime/git-network.lock').exists())

    def test_evidence_custom_environment_preserves_non_network_semantics(self):
        supplied = {'GIT_ALLOW_PROTOCOL':'http','GIT_NO_LAZY_FETCH':'0',
                    'GIT_NO_REPLACE_OBJECTS':'0','FIXTURE_CUSTOM_ENV':'preserve'}
        original = dict(supplied)
        completed = subprocess.CompletedProcess([],0,b'fixture',b'')
        with mock.patch.object(evidence.subprocess,'run',return_value=completed) as calls:
            evidence.command({'repo':str(self.repo)},['cat-file','-e','HEAD'],env=supplied)
        self.assertEqual(original,supplied)
        expected={**original,'GIT_ALLOW_PROTOCOL':'','GIT_NO_LAZY_FETCH':'1'}
        self.assertEqual(expected,calls.call_args.kwargs['env'])


if __name__ == '__main__':
    unittest.main()
