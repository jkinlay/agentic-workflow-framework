"""Offline delivery safety; no platform qualification is implied."""
from contextlib import contextmanager
import importlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agentic import ValidationError


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def binding(platform='slack'):
    return dict(project='fixture-project', repository='fixture-repository', scope_revision='scope-1',
                platform=platform, destination='fixture-channel', revision='binding-1', secret_ref='env:AWF_DELIVERY_TEST')


class Provider:
    def __init__(self, store=None):
        self.calls = 0
        self.observation = {'status': 'UNKNOWN'}
        self.store = store
        self.during_send = None

    def send(self, intent, attempt_id):
        self.calls += 1
        if self.during_send:
            self.during_send()
        return {'status': 'ACCEPTED', 'http_status': 202}

    def reconcile(self, intent, attempts):
        return (dict(observed_at='2026-11-05T12:00:00Z', observation_id='fixture-readback')
                | self.observation | dict(delivery_id=intent['delivery_id'],
                    destination=intent['binding']['destination'], platform=intent['binding']['platform'],
                    payload_sha256=intent['payload_sha256'], attempt_ids=attempts))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.api = importlib.import_module('agentic.reporting.delivery')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(str(Path(self.temp.name) / 'controller.sqlite'))
        with self.db.transaction() as db:
            self.api.install_delivery_schema(db)
        self.store = self.api.DeliveryOutbox(self.db)
        self.intent = self.api.delivery_intent('report-1', binding(), 'payload')
        self.store.enqueue(self.intent)

    def test_acceptance_and_local_receipt_never_prove_channel_delivery(self):
        p = Provider()
        p.observation = dict(status='VISIBLE', evidence_kind='LOCAL_RECEIPT', message_id='1355517523.000005')
        result = self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(p.calls, 1)
        self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(p.calls, 1)

    def test_two_attempt_ceiling_survives_restart_and_binding_change(self):
        p = Provider()
        p.observation = dict(status='ABSENT', terminal=True, evidence_kind='CHANNEL_READBACK')
        self.assertEqual(self.store.advance(self.intent['delivery_id'], p)['status'], 'ABSENT')
        restarted = self.api.DeliveryOutbox(self.db)
        self.assertEqual(restarted.advance(self.intent['delivery_id'], p)['status'], 'FAILED')
        restarted.advance(self.intent['delivery_id'], p)
        self.assertEqual(p.calls, 2)
        changed = binding()
        changed['revision'] = 'binding-2'
        with self.assertRaises(ValidationError):
            restarted.enqueue(self.api.delivery_intent('report-1', changed, 'payload'))

    def test_visible_requires_exact_destination_payload_and_stable_readback(self):
        p = Provider()
        p.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id='1355517523.000005',
                             observed_at='2026-11-05T12:00:00Z')
        result = self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(result['status'], 'DELIVERED')
        self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(p.calls, 1)
        self.assertEqual(len(result['history']), 2)

    def test_timeout_then_discovered_message_does_not_repost(self):
        p = Provider()
        def timeout(*args):
            p.calls += 1
            raise TimeoutError('secret must not be retained')
        p.send = timeout
        self.assertEqual(self.store.advance(self.intent['delivery_id'], p)['status'], 'UNKNOWN')
        p.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id='1355517523.000006',
                             observed_at='2026-11-05T12:00:00Z')
        result = self.api.DeliveryOutbox(self.db).advance(self.intent['delivery_id'], p)
        self.assertEqual(result['status'], 'DELIVERED')
        self.assertEqual(p.calls, 1)
        self.assertNotIn('secret', str(result))

    def test_competing_sender_and_platform_independence(self):
        p = Provider()
        other = self.api.DeliveryOutbox(self.db)
        p.during_send = lambda: self.assertEqual(other.advance(self.intent['delivery_id'], Provider())['status'], 'UNKNOWN')
        self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(p.calls, 1)
        teams = self.api.delivery_intent('report-1', binding('teams'), 'payload')
        self.store.enqueue(teams)
        q = Provider()
        q.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id='1616990032035',
                             observed_at='2026-11-05T12:00:00Z')
        self.assertEqual(other.advance(teams['delivery_id'], q)['status'], 'DELIVERED')
        self.assertEqual(other.get(self.intent['delivery_id'])['status'], 'UNKNOWN')

    def test_missing_binding_refused_before_provider(self):
        with self.assertRaises(ValidationError):
            self.api.delivery_intent('report-1', {}, 'payload')

    def test_wrong_destination_and_nonterminal_absence_never_retry(self):
        p = Provider()
        original = p.reconcile
        p.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id='1355517523.000005',
                             observed_at='2026-11-05T12:00:00Z')
        p.reconcile = lambda intent, attempts: dict(original(intent, attempts), destination='foreign')
        self.assertEqual(self.store.advance(self.intent['delivery_id'], p)['status'], 'UNKNOWN')
        p.reconcile = original
        p.observation = dict(status='ABSENT', terminal=False, evidence_kind='CHANNEL_READBACK')
        self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(p.calls, 1)

    def test_crash_reservation_consumes_attempt_before_any_network_effect(self):
        p = Provider()
        def crash(*args):
            raise KeyboardInterrupt()
        p.send = crash
        with self.assertRaises(KeyboardInterrupt):
            self.store.advance(self.intent['delivery_id'], p)
        self.assertEqual(len(self.store.get(self.intent['delivery_id'])['attempts']), 1)
        restarted = self.api.DeliveryOutbox(self.db)
        q = Provider()
        restarted.advance(self.intent['delivery_id'], q)
        self.assertEqual(q.calls, 0)
        q.observation = dict(status='ABSENT', terminal=True, evidence_kind='CHANNEL_READBACK')
        restarted.advance(self.intent['delivery_id'], q)
        self.assertEqual(restarted.advance(self.intent['delivery_id'], q)['status'], 'FAILED')
        self.assertEqual(q.calls, 1)

    def test_concurrent_threads_reserve_only_one_send(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        entered, release = Event(), Event()
        p, q = Provider(), Provider()
        p.during_send = lambda: (entered.set(), release.wait(5))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.store.advance, self.intent['delivery_id'], p)
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(self.api.DeliveryOutbox(self.db).advance, self.intent['delivery_id'], q)
                self.assertEqual(second.result(timeout=5)['status'], 'UNKNOWN')
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual((p.calls, q.calls), (1, 0))

    def test_absence_needs_retained_readback_identity(self):
        p = Provider()
        p.observation = dict(status='ABSENT', terminal=True, evidence_kind='CHANNEL_READBACK')
        original = p.reconcile
        def incomplete(intent, attempts):
            value = original(intent, attempts)
            value.pop('observation_id')
            return value
        p.reconcile = incomplete
        self.assertEqual(self.store.advance(self.intent['delivery_id'], p)['status'], 'UNKNOWN')

    def test_intent_cannot_be_deleted_to_reset_ceiling(self):
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as db:
            db.execute('DELETE FROM controller_report_delivery')
        with self.assertRaises(sqlite3.IntegrityError), self.db.transaction() as db:
            db.execute("UPDATE controller_report_delivery SET intent_json='{}'")


if __name__ == '__main__':
    unittest.main()
