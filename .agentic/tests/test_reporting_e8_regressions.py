from test_reporting_fixtures import reporting_policy_snapshot
"""Pre-E8: production controller/outbox, synthetic network and observations only."""
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_reporting_e6_regressions as e6
from test_reporting_e5_regressions import legacy_intent
from agentic import ValidationError
from agentic.canonical import timestamp
from agentic.reporting.delivery import DeliveryOutbox


class E8ControllerTests(unittest.TestCase):
    setUp = e6.ControllerTests.setUp
    cycle = e6.ControllerTests.cycle
    status = e6.ControllerTests.status

    def test_five_candidate_retry_consumes_deadline_and_quiesces_before_return(self):
        self.network.latency = .3
        def reject(method, url):
            if method == 'POST':
                raise HTTPError(url, 400, 'Synthetic rejection', {}, None)
        self.network.before_call = reject
        self.helper.listed = [dict(self.helper.current, pull_request=n) for n in range(41, 46)]
        slow = False
        observations = []

        def observe(candidate, now):
            if slow:
                # Three realistic remote observations per candidate, as in the
                # pre-review reproduction. No reporting seam is replaced.
                for _ in range(3):
                    observations.append(time.monotonic())
                    time.sleep(1.6)
            return dict(candidate=deepcopy(candidate), observed_at=now,
                expires_at=(timestamp(now) + timedelta(minutes=5)).isoformat(),
                open=True, merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)

        self.helper.observe_candidate = observe
        # Retain the five-member historical-batch regression. New reporting
        # batches are now capped, but restart must still safely handle a batch
        # formed by the preceding runtime. Seed it through the actual store and
        # reporting port; every assertion below is unchanged.
        from agentic.review_batching import ReviewBatchStore
        from agentic.reporting.runtime import ReviewReportingPort
        batches = ReviewBatchStore(self.helper.store)
        now = '2026-11-10T10:00:00Z'
        self.clock.value = now
        mode = self.settings['mode']
        self.settings['mode'] = 'off'
        with patch.object(self.helper, 'deliver', None), patch.object(self.helper, 'readback', None):
            self.assertEqual(self.cycle('2026-11-10T10:00:00Z')['errors'], [])
        self.settings['mode'] = mode
        for candidate in self.helper.listed:
            batches.enqueue_observed(candidate, observe(candidate, now), now, {}, repository_id='101')
        payload = batches.claim(now, {}, lambda candidate: observe(candidate, now))
        providers = self.adapters['configure_reporting'](reporting_policy_snapshot(self), self.binding,
            repository_root=Path(self.helper.temp.name) / 'reporting-policy')
        port = ReviewReportingPort(self.helper.store, {'reporting': self.settings}, self.binding,
                                   providers, lambda: now, observe_candidate=observe)
        self.assertIsNone(port.deliver_review_batch(payload))
        review = next(r for r in self.status()['reports'] if r['kind'] == 'final-human-review')
        initial = [d for d in self.status()['deliveries'] if d['report_id'] == review['report_id']]
        self.assertEqual(len(review['review_batch']['members']), 5)
        self.assertEqual({d['status'] for d in initial}, {'ABSENT'})
        self.assertTrue(all(len(d['attempts']) == 1 for d in initial))
        self.helper.listed = []
        self.network.before_call = None
        slow = True
        started = time.monotonic()
        self.cycle('2026-11-10T10:00:01Z')
        elapsed = time.monotonic() - started
        alive = [t for t in threading.enumerate() if t.name.startswith('awf-report-')]
        states = deepcopy(self.status()['deliveries'])
        posts = deepcopy(self.network.posts)
        # Inspect after the old worker would have started its late POST too.
        time.sleep(6)
        late_posts = self.network.posts != posts
        late_states = self.status()['deliveries'] != states
        for thread in alive:
            thread.join(60)
        self.assertLess(elapsed, 45)
        self.assertTrue(observations)
        self.assertEqual(alive, [])
        self.assertFalse(late_posts)
        self.assertFalse(late_states)
        final = [d for d in states if d['report_id'] == review['report_id']]
        self.assertTrue(all(len(d['attempts']) == 1 for d in final))

    def test_public_persistence_refuses_exact_opaque_credential_in_every_intent_field(self):
        providers = self.adapters['configure_reporting'](reporting_policy_snapshot(self), self.binding,
            repository_root=Path(self.helper.temp.name) / 'reporting-policy')
        outbox = DeliveryOutbox(self.helper.store)
        for destination in self.settings['bindings']:
            provider = providers[destination['platform']]
            base = legacy_intent('safe-report', destination, 'safe payload')
            base['thread_id'] = '1793902283.424109' if destination['platform'] == 'slack' else '1793902283424'
            fields = [(key,) for key in base if key != 'binding'] + [('binding', key) for key in destination]
            for field in fields:
                intent = deepcopy(base)
                target = intent['binding'] if len(field) == 2 else intent
                original = target[field[-1]]
                # Numeric IDs and closed platform values are opaque too. Each
                # synthetic credential exactly matches the attacked field.
                secret = original if field[-1] in {'platform', 'thread_id', 'delivery_id', 'payload_sha256'} else 'OpaqueE8SyntheticValue9'
                target[field[-1]] = secret
                if field == ('binding', 'secret_ref'):
                    target['secret_ref'] = 'env:' + secret
                # Keep the envelope valid: refusal must be for the configured
                # secret, not for a stale content hash or identity mismatch.
                intent = dict(legacy_intent(intent['report_id'], intent['binding'], intent['payload']),
                              thread_id=intent['thread_id'])
                env_name = intent['binding']['secret_ref'][4:]
                with self.subTest(platform=destination['platform'], field=field), \
                     patch.dict(os.environ, {env_name: secret}):
                    # Factory uses the same configured reference as admission.
                    if field != ('binding', 'secret_ref'):
                        self.assertEqual(provider.resolve_secret(destination['secret_ref']), secret)
                    before = self.helper.path.read_bytes()
                    for transactional in (False, True):
                        with self.subTest(transactional=transactional), self.assertRaisesRegex(
                                ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                            if transactional:
                                prepared = outbox.prepare_enqueue(intent)
                                with self.helper.store.transaction() as db:
                                    outbox.enqueue_in_transaction(db, intent, prepared=prepared)
                            else:
                                outbox.enqueue(intent)
                        self.assertEqual(self.helper.path.read_bytes(), before)
        self.assertEqual(self.network.posts, [])

    def test_public_binding_validation_precedes_resolution_and_custom_resolver_refuses_exact_value(self):
        outbox = DeliveryOutbox(self.helper.store)
        destination = deepcopy(self.settings['bindings'][0])
        intent = legacy_intent('safe-report', destination, 'safe payload')

        def forbidden(reference):
            self.fail('Invalid binding must refuse before credential resolution')

        for invalid in ({}, dict(destination, platform='foreign'), dict(destination, revision='')):
            value = dict(intent, binding=invalid)
            with self.assertRaisesRegex(ValidationError, 'REPORT_BINDING_|REPORT_PLATFORM_'):
                outbox.enqueue(value, resolve_secret=forbidden)
            with self.assertRaisesRegex(ValidationError, 'REPORT_BINDING_|REPORT_PLATFORM_'):
                prepared = outbox.prepare_enqueue(value, resolve_secret=forbidden)
                with self.helper.store.transaction() as db:
                    outbox.enqueue_in_transaction(db, value, prepared=prepared)
        destination['secret_ref'] = 'host:report-sender'
        opaque = 'OpaqueCustomE8SyntheticValue9'
        intent = legacy_intent('safe-report', destination, 'Contains ' + opaque)
        resolved = []

        def resolver(reference):
            resolved.append(reference)
            return opaque

        before = self.helper.path.read_bytes()
        with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
            outbox.enqueue(intent, resolve_secret=resolver)
        with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
            prepared = outbox.prepare_enqueue(intent, resolve_secret=resolver)
            with self.helper.store.transaction() as db:
                outbox.enqueue_in_transaction(db, intent, prepared=prepared)
        self.assertEqual(resolved, ['host:report-sender', 'host:report-sender'])
        self.assertEqual(self.helper.path.read_bytes(), before)
        self.assertNotIn(opaque.encode(), before)

    def test_noncooperative_observer_is_durably_cancelled_and_cannot_send_on_return(self):
        # Time the real cancellation path against a clean Git fixture, as the
        # other bounded-delivery probes do. Unrelated source edits otherwise
        # add worktree-reservation scans to the fixed 47-second assertion.
        clean = Path(self.helper.temp.name) / 'noncooperative-repository'
        from test_reporting_fixtures import build_reporting_repository, prepare_reporting_repository
        build_reporting_repository(clean)
        prepare_reporting_repository(self, clean)
        self.repository['repository_root'] = clean
        self.network.latency = .3
        self.network.reject = True
        self.settings['mode'] = 'slack'
        self.helper.listed = [deepcopy(self.helper.current)]
        self.cycle('2026-11-10T10:00:00Z')
        self.helper.listed = []
        self.network.reject = False
        original = self.helper.observe_candidate
        blocked, release = threading.Event(), threading.Event()
        observed_timeouts = []

        def observe(candidate, now):
            if threading.current_thread().name.startswith('awf-report-'):
                from agentic.reporting.bounded import source_timeout
                observed_timeouts.append(source_timeout())
                blocked.set()
                # Deliberately broken observation port; it exceeds its timeout.
                release.wait(60)
            else:
                time.sleep(.3)
            return original(candidate, now)

        self.helper.observe_candidate = observe
        started = time.monotonic()
        alive = []
        try:
            result = self.cycle('2026-11-10T10:00:01Z')
            elapsed = time.monotonic() - started
            alive = [t for t in threading.enumerate() if t.name.startswith('awf-report-')]
            states, posts = deepcopy(self.status()['deliveries']), deepcopy(self.network.posts)
            with self.helper.store.connection() as db:
                records = [row[0] for row in db.execute(
                    "SELECT value FROM controller_meta WHERE key LIKE 'reporting-worker-timeout:%'")]
        finally:
            release.set()
            for thread in alive:
                thread.join(5)
        self.assertTrue(blocked.is_set())
        self.assertEqual(len(alive), 1)
        self.assertLess(elapsed, 47)  # 45-second stage plus local controller work.
        self.assertTrue(observed_timeouts and all(0 < t <= 15 for t in observed_timeouts))
        self.assertTrue(result['errors'])
        self.assertEqual(len(records), 1)
        self.assertIn('REPORT_DESTINATION_WORKER_NOT_QUIESCENT', records[0])
        self.assertIn('"cancellation_revoked":true', records[0])
        self.assertFalse(any(t.is_alive() for t in alive))
        self.assertEqual(self.network.posts, posts)
        self.assertEqual(self.status()['deliveries'], states)
        e6.ControllerTests.restart(self)
        with self.helper.store.connection() as db:
            self.assertEqual([r[0] for r in db.execute(
                "SELECT value FROM controller_meta WHERE key LIKE 'reporting-worker-timeout:%'")], records)


if __name__ == '__main__':
    unittest.main()
