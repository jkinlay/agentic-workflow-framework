import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentic import ValidationError
from agentic.review_first_draft import (enroll_created_pr, publication_scan,
                                         publish_tested_tree,
                                         render_first_draft_body,
                                         run_first_draft,
                                         validate_worker_receipt)
from agentic.gittree import candidate_tree
from agentic.review_loop import LoopStore


class FirstDraftTests(unittest.TestCase):
    def test_body_records_tier_and_worker_pins(self):
        body = render_first_draft_body('Ticket contract', risk_tier='Tier 3',
                                       worker_model='gpt-5.6-luna', reasoning_effort='medium',
                                       branch='codex/awf-30', tested_tree='a' * 40)
        self.assertIn('Risk tier: Tier 3', body)
        self.assertIn('gpt-5.6-luna', body)
        self.assertIn('amendment cycles start at zero', body)

    def test_receipt_rejects_scope_escape(self):
        receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'src/a.py', 'action': 'modified'}],
                   'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
        with self.assertRaises(ValidationError):
            validate_worker_receipt(receipt, allowed_paths={'src/other.py'})

    def test_publication_scan_blocks_first_push_body(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture')
            run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n')
            run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            unsafe_value = '/' + 'home' + '/' + 'fixture' + '/' + 'secret'
            (root / 'a.txt').write_text(unsafe_value + '\n')
            run('add', 'a.txt'); run('commit', '-m', 'candidate')
            head = run('rev-parse', 'HEAD')
            with self.assertRaises(ValidationError):
                publication_scan(root, base, head, 'body')

    def test_publisher_rejects_tree_mismatch_before_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture'); run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n'); run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            (root / 'a.txt').write_text('changed\n')
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'a.txt', 'action': 'modified'}],
                       'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
            class NoPush:
                def run(self, *args):
                    raise AssertionError('publisher must stop before Git mutation')
            with self.assertRaises(ValidationError):
                publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                    commit_message='first draft', git=NoPush(), allowed_paths={'a.txt'})

    def test_publisher_scans_after_commit_but_withholds_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def run(*args):
                return subprocess.run([git, '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            run('config', 'user.name', 'fixture'); run('config', 'user.email', 'fixture@example.invalid')
            (root / 'a.txt').write_text('safe\n'); run('add', 'a.txt'); run('commit', '-m', 'base')
            base = run('rev-parse', 'HEAD')
            (root / 'a.txt').write_text('/'.join(('', 'home', 'fixture', 'secret')) + '\n')
            tree = candidate_tree(root, base, [{'path': 'a.txt', 'action': 'modified'}])
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'a.txt', 'action': 'modified'}],
                       'tested_tree': tree.tested_tree, 'ignored_untracked': list(tree.ignored_untracked), 'summary': 'changed'}
            pushes = []
            class Adapter:
                def run(self, *args):
                    if args[0] == 'push':
                        pushes.append(args)
                        raise AssertionError('scan must withhold push')
                    return run(*args)
            with self.assertRaises(ValidationError):
                publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                    commit_message='first draft', git=Adapter(), allowed_paths={'a.txt'})
            self.assertEqual(pushes, [])

    def test_publisher_commits_exact_tested_tree_before_push(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); remote = root.parent / (root.name + '-remote.git')
            def command(cwd, *args):
                return subprocess.run([git, '-C', str(cwd), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run([git, 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            subprocess.run([git, 'init', '--bare', str(remote)], check=True, capture_output=True)
            command(root, 'config', 'user.name', 'fixture'); command(root, 'config', 'user.email', 'fixture@example.invalid')
            command(root, 'config', 'core.autocrlf', 'false')
            command(root, 'remote', 'add', 'origin', str(remote))
            (root / 'a.txt').write_text('safe\n'); command(root, 'add', 'a.txt'); command(root, 'commit', '-m', 'base')
            base = command(root, 'rev-parse', 'HEAD')
            (root / 'a.txt').write_text('changed\n')
            changes = [{'path': 'a.txt', 'action': 'modified'}]
            tree = candidate_tree(root, base, changes)
            receipt = {'outcome': 'CHANGED', 'changes': changes, 'tested_tree': tree.tested_tree,
                       'ignored_untracked': list(tree.ignored_untracked), 'summary': 'changed'}
            class Adapter:
                def run(self, *args):
                    return command(root, *args)
            published = publish_tested_tree(root, base, 'codex/awf-30', receipt, body='body',
                                            commit_message='first draft', git=Adapter(), allowed_paths={'a.txt'})
            self.assertEqual(published['head_tree'], tree.tested_tree)
            self.assertEqual(command(root, 'rev-parse', 'HEAD^{tree}'), tree.tested_tree)

    def test_enrollment_charges_first_draft_but_not_amendment_cycles(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:30', 'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10}
            snapshot = {'repository_id': 12, 'pr': 30, 'head': 'a' * 40, 'base': 'b' * 40,
                        'head_ref': 'codex/awf-30', 'base_ref': 'main'}
            state = enroll_created_pr(store, config, snapshot)
            self.assertEqual(state['agent_runs'], 1)
            self.assertEqual(state['cycles'], 0)
            store.close()

    def test_first_draft_bridge_observes_published_head_before_enrollment(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:31', 'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10, 'allowed_paths': ['src/a.py']}
            receipt = {'outcome': 'CHANGED', 'changes': [{'path': 'src/a.py', 'action': 'added'}],
                       'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'changed'}
            calls = []
            snapshot = {'repository_id': 12, 'pr': 31, 'head': 'c' * 40, 'base': 'b' * 40,
                        'head_ref': 'codex/awf-30', 'base_ref': 'main'}
            state = run_first_draft(store, config, worker=lambda run_id: receipt,
                                    publisher=lambda value: calls.append('publish') or {'head': 'c' * 40},
                                    observe_pr=lambda value: calls.append('observe') or snapshot)
            self.assertEqual(calls, ['publish', 'observe'])
            self.assertEqual(state['agent_runs'], 1)
            store.close()

    def test_failed_first_draft_is_paused_with_reserved_run(self):
        with tempfile.TemporaryDirectory() as name:
            store = LoopStore(Path(name))
            config = {'key': '12:32', 'repository_id': 12, 'repository': 'fixture/project', 'config_hash': 'x',
                      'initial_findings': [], 'max_agent_runs': 10, 'allowed_paths': ['src/a.py']}
            seen = []
            with self.assertRaisesRegex(RuntimeError, 'worker failed'):
                run_first_draft(store, config,
                    worker=lambda run_id: seen.append(run_id) or (_ for _ in ()).throw(RuntimeError('worker failed')),
                    publisher=lambda value: None, observe_pr=lambda value: None)
            state = store.get('12:0')
            self.assertEqual(state['phase'], 'PAUSED')
            self.assertEqual(state['inflight']['id'], seen[0])
            store.close()
