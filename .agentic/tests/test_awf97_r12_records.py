"""PR2 complete publication records; offline fixtures, no repository mutation."""
import ast
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agentic import ValidationError
from agentic.canonical import canonical, sha256
from agentic import merge_train as train, review_loop as loop
from test_owner_publication import OwnerPublicationTests, NOW
from test_merge_train_publication import MergeTrainPublicationTests

CAP = 1_048_576
ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / '.agentic/adapters/reference_controller_adapter.py'


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def fit(value, size, encode=encoded, field='pad'):
    value[field] = ''
    value[field] = 'x' * (size - len(encode(value)))
    assert len(encode(value)) == size
    return value


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='r12-records-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ns = runpy.run_path(str(ADAPTER))

    def test_detached_record_counts_final_lf_before_temp_creation(self):
        path = self.root / 'record.json'
        encode = lambda v: canonical(v) + b'\n'
        value = fit({'unicode': '\u00e9"\\\n'}, CAP, encode)
        self.ns['_write_record'](path, value)
        original = path.read_bytes()
        self.assertEqual(len(original), CAP)
        for exists in (True, False):
            if not exists:
                path.unlink()
            with patch.object(Path, 'write_bytes', side_effect=AssertionError('write before refusal')) as write:
                with self.assertRaises(ValidationError):
                    self.ns['_write_record'](path, {**value, 'pad': value['pad'] + 'x'})
                write.assert_not_called()
            self.assertEqual(path.read_bytes() if exists else None, original if exists else None)

    def test_detached_record_read_is_bounded_before_decode(self):
        class Reader(io.BytesIO):
            def read(self, size=-1):
                self_case.assertEqual(size, CAP + 1)
                return super().read(size)
        self_case = self
        with patch.object(Path, 'open', return_value=Reader(b'x' * (CAP + 1))), patch('json.loads') as parse:
            with self.assertRaises(ValidationError):
                self.ns['_read_record'](self.root / 'record')
            parse.assert_not_called()

    def watcher_prefix(self):
        tree = ast.parse(self.ns['_SubprocessRunner']._WATCHER)
        # Execute the shipped imports, launch-spec loader and durable definition;
        # all later launch and proof writes remain excluded for this unit seam.
        stop = next(i for i, node in enumerate(tree.body)
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'pid' for t in node.targets))
        return compile(ast.Module(body=tree.body[:stop], type_ignores=[]), '<shipped-watcher>', 'exec')

    def test_watcher_launch_spec_is_bounded_before_handoff_or_child(self):
        path = self.root / 'spec.json'
        path.write_bytes(b' ' * (CAP + 1))
        with patch.object(sys, 'argv', ['watcher', str(path)]), patch('json.loads') as loads, patch('json.load') as load, patch('tempfile.mkstemp') as temp, patch('subprocess.Popen') as child:
            with self.assertRaises((ValueError, ValidationError)):
                exec(self.watcher_prefix(), {})
            loads.assert_not_called()
            load.assert_not_called()
            temp.assert_not_called()
            child.assert_not_called()

    def test_watcher_durable_caps_escaped_complete_bytes(self):
        spec = self.root / 'spec.json'
        spec.write_bytes(b'{}')
        scope = {}
        with patch.object(sys, 'argv', ['watcher', str(spec)]):
            exec(self.watcher_prefix(), scope)
        path = self.root / 'proof.json'
        value = fit({'text': '\u00e9"\\\t\n'}, CAP)
        scope['durable'](str(path), value)
        self.assertEqual(path.read_bytes(), encoded(value))
        for exists in (True, False):
            if not exists:
                path.unlink()
            with patch('tempfile.mkstemp') as temp:
                with self.assertRaises((ValueError, ValidationError)):
                    scope['durable'](str(path), {**value, 'pad': value['pad'] + 'x'})
                temp.assert_not_called()
            self.assertEqual(path.read_bytes() if exists else None, encoded(value) if exists else None)

    def test_detached_startup_and_terminal_bindings_survive_boundary_change(self):
        runner = self.ns['_SubprocessRunner']()
        session = 'inert-r12-session'
        record = runner.launch([sys.executable, '-B', '-c', 'print(\' {"type":"thread.started","thread_id":"' + session + '"}\')'],
            cwd=self.root, env=dict(os.environ), timeout=10, stdin='',
            handoff_path=str(self.root / 'run.handoff.json'), terminal_proof_path=str(self.root / 'run.terminal.json'), launch_nonce='nonce-r12')
        terminal = runner.observe(record, timeout=10)
        startup = self.ns['_read_record'](Path(record['startup_proof_path']))
        handoff = self.ns['_read_record'](Path(record['handoff_path']))
        for proof in (terminal, startup, handoff):
            self.assertEqual(proof['pid'], record['pid'])
            self.assertEqual(proof['launch_nonce'], 'nonce-r12')
        self.assertEqual(terminal['session_id'], session)
        self.assertEqual(startup['session_id'], session)
        self.assertEqual(self.ns['_SubprocessRunner']().observe(record, timeout=0), terminal)

    def test_journal_complete_bytes_and_bounded_recovery(self):
        candidate = {'publication_candidate_id': 'c', 'batch_id': 'b', 'generation': {'generation': 1}, 'batch_head_sha': 'h'}
        for status in ('ACTIVE', 'ABANDONED'):
            for operation in ('INTENT', 'UNKNOWN', 'DONE', 'RECONCILED'):
                value = fit({'format': 'awf-merge-train-publication-journal-1', 'publication_candidate_id': 'c', 'batch_id': 'b', 'generation': 1, 'expected_head': 'h', 'status': status, 'operations': [{'status': operation}]}, CAP)
                path = self.root / 'journal.json'
                train._save_publication_journal(path, value)
                self.assertEqual(path.read_bytes(), encoded(value))
                self.assertEqual(train._load_publication_journal(path, candidate), value)
                before = path.read_bytes()
                with self.assertRaises(ValidationError):
                    train._save_publication_journal(path, {**value, 'pad': value['pad'] + 'x'})
                self.assertEqual(path.read_bytes(), before)
                path.write_bytes(before + b' ')
                with patch('json.loads') as parse:
                    with self.assertRaises(ValidationError):
                        train._load_publication_journal(path, candidate)
                    parse.assert_not_called()
                self.assertEqual(path.read_bytes(), before + b' ')
        path = self.root / 'absent' / 'journal.json'
        with self.assertRaises(ValidationError):
            train._save_publication_journal(path, {'pad': 'x' * CAP})
        self.assertFalse(path.parent.exists())


class OwnerBoundaries(unittest.TestCase):
    def setUp(self):
        self.fixture = OwnerPublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture

    def test_owner_authorized_envelope_boundary_is_atomic(self):
        f = self.f
        request = copy.deepcopy(f.request)
        request['streams'][0]['stream_state']['pad'] = ''
        complete = {**request, 'host_authorization': f.authorization}
        request['streams'][0]['stream_state']['pad'] = 'x' * (CAP - len(canonical(complete)))
        self.assertEqual(len(canonical({**request, 'host_authorization': f.authorization})), CAP)
        f.store.prepare(f.config, request, f.authorization, now=NOW)
        with f.store.connection() as db:
            before = list(db.iterdump())
        request['streams'][0]['stream_state']['pad'] += 'x'
        with patch.object(f.store, 'transaction', side_effect=AssertionError('transaction before refusal')):
            with self.assertRaises(ValidationError):
                f.store.prepare(f.config, request, f.authorization, now=NOW)
        with f.store.connection() as db:
            self.assertEqual(list(db.iterdump()), before)

    def test_owner_snapshot_bounds_each_json_artifact_before_parse(self):
        f = self.f
        for column, table in (('request_json', 'owner_publication_batch'), ('remote_json', 'owner_publication_pr'), ('receipt_json', 'owner_publication_pr')):
            with self.subTest(column=column):
                with f.store.connection() as db:
                    old = db.execute(f'SELECT {column} FROM {table} WHERE batch_id=?', (f.batch,)).fetchone()[0]
                    db.execute(f'UPDATE {table} SET {column}=? WHERE batch_id=?', (json.dumps('\u00e9' * (CAP // 2), ensure_ascii=False), f.batch))
                import agentic.owner_publication as owner
                parsed = []
                original = owner.loads
                def parse(raw):
                    parsed.append(raw)
                    self.assertLessEqual(len(raw.encode()), CAP, 'oversize passed to parser')
                    return original(raw)
                with patch.object(owner, 'loads', side_effect=parse):
                    with self.assertRaises(ValidationError):
                        f.store.snapshot(f.batch, f.config)
                with f.store.connection() as db:
                    db.execute(f'UPDATE {table} SET {column}=? WHERE batch_id=?', (old, f.batch))

    def test_owner_begin_pr_does_not_materialize_unbounded_unused_json(self):
        f = self.f
        f.store.record_heads(f.batch, f.config, f.heads(f.prepared), now=NOW)
        queries = []
        connection = f.store.connection
        @contextmanager
        def traced():
            with connection() as db:
                db.set_trace_callback(queries.append)
                yield db
        with patch.object(f.store, 'connection', traced):
            self.assertTrue(f.store.begin_pr(f.batch, 'A', now=NOW))
        selects = [q.lower() for q in queries if q.lower().startswith('select')]
        self.assertTrue(selects)
        self.assertTrue(all('*' not in q and 'receipt_json' not in q and 'remote_json' not in q for q in selects))

    def test_owner_duplicate_receipt_reader_rechecks_transaction_bytes(self):
        f = self.f
        f.store.record_heads(f.batch, f.config, f.heads(f.prepared), now=NOW)
        f.store.begin_pr(f.batch, 'A', now=NOW)
        operation = f.store.snapshot(f.batch, f.config)['operations'][0]
        receipt = f.receipt({**f.request['streams'][0], 'operation_id': operation['operation_id'], 'repository_id': 101, 'base_branch': 'main'})
        transaction = f.store.transaction
        @contextmanager
        def raced():
            with f.store.connection() as db:
                db.execute("UPDATE owner_publication_pr SET receipt_json=? WHERE batch_id=? AND stream='B'", (json.dumps({'number': 99, 'pad': 'x' * CAP}), f.batch))
            with transaction() as db:
                yield db
        with patch.object(f.store, 'transaction', raced):
            with self.assertRaises(ValidationError):
                f.store.finish_pr(f.batch, f.config, 'A', receipt, now=NOW)
        with f.store.connection() as db:
            self.assertEqual(db.execute("SELECT state FROM owner_publication_pr WHERE stream='A'").fetchone()[0], 'PR_IN_FLIGHT')


class JournalRecovery(unittest.TestCase):
    def test_oversize_provider_result_retains_intent_without_replay(self):
        f = MergeTrainPublicationTests()
        f.setUp()
        self.addCleanup(f.tearDown)
        registry = f.publisher_registry['fixture-publisher']
        create = registry['create_draft_pr']
        registry['create_draft_pr'] = lambda *a: {**create(*a), 'padding': 'x' * CAP}
        candidate = f._candidate()
        with self.assertRaises(ValidationError):
            f._publish(candidate, mark_ready=True)
        path = f.root / 'journal.json'
        journal = json.loads(path.read_bytes())
        self.assertEqual(journal['operations'][-1]['status'], 'INTENT')
        self.assertEqual([c[0] for c in f.provider_calls], ['push_batch_ref', 'create_draft_pr'])
        f._publish(candidate, mark_ready=True)
        self.assertEqual([c[0] for c in f.provider_calls], ['push_batch_ref', 'create_draft_pr', 'mark_draft_pr_ready'])


class LoopArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='r12-loop-')
        self.addCleanup(self.tmp.cleanup)
        self.store = loop.LoopStore(self.tmp.name)
        self.addCleanup(self.store.close)
        self.config = {'key': '12:0', 'repository_id': 12, 'repository': 'fixture/repo', 'pr': 0, 'config_hash': 'c', 'max_agent_runs': 2}
        loop.reserve_first_draft(self.store, self.config)

    def envelope(self, name, value):
        return json.dumps({'format': 'awf-loop-publication-artifact-1', 'kind': name, 'value': value}, sort_keys=True).encode()

    def test_loop_publication_artifacts_count_outer_envelope(self):
        for name in ('first_draft_worker_receipt', 'first_draft_publication', 'first_draft_publication_plan'):
            state = self.store.get('12:0')
            value = fit({'text': '\u00e9"\\\n'}, CAP, lambda v: self.envelope(name, v))
            state[name] = value
            self.store.save(state)
            self.assertEqual(self.store.get('12:0')[name], value)
            persisted = self.store.db.execute('SELECT state FROM prs').fetchone()[0]
            self.assertLess(len(persisted), 10000, 'publication artifact remains embedded')
            before = list(self.store.db.iterdump())
            state[name] = {**value, 'pad': value['pad'] + 'x'}
            with self.assertRaises(ValidationError):
                self.store.save(state)
            self.assertEqual(list(self.store.db.iterdump()), before)
            state[name] = None
            self.store.save(state)

    def test_loop_artifact_hydration_retry_and_legacy_refusal(self):
        receipt = {'outcome': 'CHANGED', 'changes': ['a'], 'tested_tree': 'a' * 40, 'ignored_untracked': [], 'summary': 'ok'}
        loop.record_first_draft_worker_receipt(self.store, self.config, receipt)
        loop.record_first_draft_worker_receipt(self.store, self.config, receipt)
        with self.assertRaises(ValidationError):
            loop.record_first_draft_worker_receipt(self.store, self.config, {**receipt, 'summary': 'changed'})
        row = self.store.db.execute('SELECT state FROM prs').fetchone()[0]
        self.assertNotEqual(json.loads(row)['first_draft_worker_receipt'], receipt)
        self.store.db.execute('DELETE FROM publication_artifacts')
        with self.assertRaises(ValidationError):
            self.store.get('12:0')
        state = json.loads(row)
        state['first_draft_worker_receipt'] = receipt
        self.store.db.execute('UPDATE prs SET state=?', (json.dumps(state),))
        self.assertEqual(self.store.get('12:0')['first_draft_worker_receipt'], receipt)
        state['first_draft_worker_receipt']['summary'] = 'x' * CAP
        raw = json.dumps(state)
        self.store.db.execute('UPDATE prs SET state=?', (raw,))
        with self.assertRaises(ValidationError):
            self.store.get('12:0')
        self.assertEqual(self.store.db.execute('SELECT state FROM prs').fetchone()[0], raw)

    def test_loop_report_failure_retains_committed_evidence(self):
        state = self.store.get('12:0')
        state['first_draft_worker_receipt'] = {'summary': 'retained'}
        with patch.object(loop.Tree, 'write', side_effect=OSError('report failure')):
            with self.assertRaises(OSError):
                self.store.save(state)
        self.assertEqual(self.store.get('12:0')['first_draft_worker_receipt'], {'summary': 'retained'})
        self.assertLess(len(self.store.db.execute('SELECT state FROM prs').fetchone()[0]), 10000)

    def test_complete_first_draft_uses_bounded_artifact_path(self):
        config = self.config
        publication = {'base': 'b', 'head': 'h', 'head_tree': 't', 'branch': 'codex/test', 'body': 'body\n', 'body_sha256': sha256(b'body\n'), 'title': 'test', 'ignored_untracked': []}
        loop.record_first_draft_publication(self.store, config, publication)
        loop.confirm_first_draft_publication(self.store, config, publication)
        bound = {**config, 'pr': 7, 'key': '12:7'}
        public = {k: v for k, v in bound.items() if k not in {'key', 'config_hash'}}
        bound['config_hash'] = sha256((json.dumps(public, indent=2, ensure_ascii=False) + '\n').encode())
        snapshot = {'head': 'h', 'base': 'b', 'head_ref': 'codex/test', 'repository_id': 12, 'pr': 7}
        result = loop.complete_first_draft(self.store, bound, snapshot)
        self.assertEqual(result['first_draft_publication'], publication)
        self.assertEqual(self.store.get('12:7')['first_draft_publication'], publication)
        with self.assertRaises(ValidationError):
            self.store.get('12:0')
        persisted = json.loads(self.store.db.execute('SELECT state FROM prs').fetchone()[0])
        self.assertNotEqual(persisted['first_draft_publication'], publication)


class DeliveryAndOverlap(RecordTests):
    def adapters(self):
        from test_reference_controller_adapter import _base_config, FakeClock
        config = _base_config(self.root)
        return self.ns['build_adapters']({**config, '_clock': FakeClock()}), config

    def test_delivery_complete_record_boundaries(self):
        adapters, config = self.adapters()
        from test_reference_controller_adapter import NOW as CLOCK_NOW
        from agentic.canonical import fingerprint
        digest = {'delivery_id': 'first', 'text': '\u00e9"\\\n', 'pad': ''}
        def row(d):
            return {'delivery_id': d['delivery_id'], 'digest_sha256': fingerprint('controller-delivery', d), 'digest': d, 'observed_at': CLOCK_NOW}
        digest['pad'] = 'x' * (CAP - len(encoded(row(digest))))
        self.assertEqual(len(encoded(row(digest))), CAP)
        adapters['deliver_status'](digest)
        adapters['deliver_status'](digest)
        path = Path(config['outbox']['directory']) / 'controller-status.jsonl'
        before = path.read_bytes()
        self.assertEqual(len(before), CAP)
        oversized = {**digest, 'delivery_id': 'other', 'pad': digest['pad'] + 'x'}
        with self.assertRaises(ValidationError):
            adapters['deliver_status'](oversized)
        self.assertEqual(path.read_bytes(), before)
        other = {'delivery_id': 'small', 'pad': 'y'}
        adapters['deliver_status'](other)
        self.assertGreater(path.stat().st_size, CAP)
        adapters['deliver_status'](other)
        self.assertEqual(len(path.read_bytes().splitlines()), 2)
        corrupt = b' ' * (CAP + 1) + b'\n'
        path.write_bytes(corrupt)
        with patch('json.loads') as parse:
            with self.assertRaises(ValidationError):
                adapters['deliver_status'](other)
            parse.assert_not_called()
        self.assertEqual(path.read_bytes(), corrupt)

    def test_overlap_receipt_overflow_retains_inflight_without_replay(self):
        from test_reference_controller_adapter import _base_config, FakeClock, _overlap_run, _overlap_gate, _overlap_payload
        config = _base_config(self.root)
        heads = ['1' * 40, '2' * 40]
        run, mutation = _overlap_run(self, self.ns, heads, '0' * 40,
            {heads[0]: b'left', heads[1]: b'right'}, merged='3' * 40)
        def overflowing(runner, argv, **kwargs):
            result = run(runner, argv, **kwargs)
            if isinstance(result, dict) and 'files' in result:
                # A valid <=512 KiB diff expands beyond the complete JSON cap.
                result['files'][0]['patch'] = '\x01' * 200000
            return result
        payload = _overlap_payload(_overlap_gate(heads))
        with patch.object(self.ns['_SubprocessRunner'], 'run', overflowing):
            adapters = self.ns['build_adapters']({**config, '_clock': FakeClock()})
            with self.assertRaises(ValidationError):
                adapters['sync_writer'](payload)
            path = self.root / 'runs' / ('overlap-sync-' + payload['execution_id'] + '.json')
            original = path.read_bytes()
            self.assertEqual(json.loads(original)['status'], 'IN_FLIGHT')
            self.assertIn('argv', mutation)
            with patch.object(self.ns['_SubprocessRunner'], 'run', side_effect=AssertionError('replayed')):
                with self.assertRaises(ValidationError):
                    adapters['sync_writer'](payload)
                with self.assertRaises(ValidationError):
                    adapters['reconcile_overlap_sync'](payload)
            self.assertEqual(path.read_bytes(), original)

class ArtifactIntegrity(LoopArtifacts):
    def test_artifact_corruption_and_wrong_kind_are_refused_unchanged(self):
        state = self.store.get('12:0')
        state['first_draft_worker_receipt'] = {'summary': 'original'}
        self.store.save(state)
        raw = self.store.db.execute('SELECT state FROM prs').fetchone()[0]
        ref = json.loads(raw)['first_draft_worker_receipt']
        digest, body = self.store.db.execute('SELECT digest,body FROM publication_artifacts').fetchone()
        for corrupt in ('{"changed":true}', 'x' * (CAP + 1)):
            self.store.db.execute('UPDATE publication_artifacts SET body=?', (corrupt,))
            before = list(self.store.db.iterdump())
            with self.assertRaises(ValidationError):
                self.store.get('12:0')
            self.assertEqual(list(self.store.db.iterdump()), before)
        self.store.db.execute('UPDATE publication_artifacts SET body=?', (body,))
        modified = json.loads(raw)
        modified['first_draft_worker_receipt'] = {**ref, 'kind': 'first_draft_publication'}
        self.store.db.execute('UPDATE prs SET state=?', (json.dumps(modified),))
        with self.assertRaises(ValidationError):
            self.store.get('12:0')

    def test_loop_history_above_record_cap_is_not_blanket_limited(self):
        state = self.store.get('12:0')
        state['history'] = [{'summary': 'x' * 1000} for _ in range(1100)]
        state['first_draft_worker_receipt'] = {'summary': 'small'}
        self.store.save(state)
        self.assertEqual(self.store.get('12:0')['history'], state['history'])
        self.assertGreater(len(self.store.db.execute('SELECT state FROM prs').fetchone()[0]), CAP)


class Abandonment(RecordTests):
    def test_full_journal_freeze_drift_remains_abandoned_on_restart(self):
        candidate = {'publication_candidate_id': 'c', 'batch_id': 'b', 'generation': {'generation': 1, 'generation_id': 'g', 'target_branch': 'main', 'target_sha': 't', 'sources': [], 'provider_identity': {}}, 'batch_branch': 'batch/b', 'batch_head_sha': 'h'}
        path = self.root / 'journal.json'
        journal = train._load_publication_journal(path, candidate)
        fit(journal, CAP)
        train._save_publication_journal(path, journal)
        with patch.object(train, '_registered_observation', return_value=({}, None, None)):
            with self.assertRaises(ValidationError):
                train._require_current_publication(candidate, 'observer', {}, now=NOW, max_age_seconds=300, intent_path=path, journal=journal)
        self.assertEqual(train._load_publication_journal(path, candidate)['status'], 'ABANDONED')
