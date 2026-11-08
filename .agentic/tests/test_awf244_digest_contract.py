"""Closed, additive controller review-status contract regressions."""
from copy import deepcopy
from pathlib import Path
import unittest

import source_only
from agentic import ValidationError
from agentic.canonical import load
from agentic.cli import local_semantics
from agentic.contracts import Contracts
import test_awf244_controller_wiring as fixture
from agentic.review_batching import ReviewBatchStore


ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-11-09T07:00:00Z'


class DigestContractTests(unittest.TestCase):
    setUp = fixture.ControllerBatchingTests.setUp

    def validate(self, digest):
        Contracts(ROOT / '.agentic/schemas').validate('controller-status-digest', digest)
        local_semantics('controller-status-digest', digest)

    def populated(self):
        reviews = ReviewBatchStore(self.store)
        reviews.enqueue(fixture.candidate(), NOW, {})
        def observe(candidate):
            return dict(candidate=candidate,
            observed_at=NOW, expires_at='2026-11-09T07:01:00Z', open=True,
            merged=False, readiness='READY_FOR_OWNER_AUTHORIZATION', final_gate_valid=True)
        batch = reviews.claim(NOW, {}, observe)
        reviews.recheck(batch['batch_id'], NOW, {}, observe)
        reviews.begin_delivery(batch['batch_id'], NOW)
        return self.store.digest(NOW)

    def test_legacy_digest_without_review_batching_still_validates(self):
        self.validate(load(ROOT / '.agentic/examples/controller-status-digest.json'))
        digest = self.store.digest(NOW)
        del digest['review_batching']
        self.validate(digest)

    def test_empty_runtime_review_view_validates(self):
        self.validate(self.store.digest(NOW))

    def test_populated_runtime_unknown_review_view_validates(self):
        self.validate(self.populated())

    def test_intent_states_and_exhaustion_alert_validate(self):
        digest = self.populated()
        review = digest['review_batching']
        item = review['intents'][0]
        review['unresolved_batches'] = []
        review['owner_alerts'] = [dict(intent_id=item['intent_id'], state='EXHAUSTED',
            reason='Two confirmed negative deliveries', retry_at='2026-11-10T07:00:00Z')]
        for state in ('PENDING', 'CLAIMED', 'DELIVERED', 'WITHDRAWN', 'EXHAUSTED'):
            with self.subTest(state=state):
                item['state'] = state
                self.validate(digest)
        item.update(scheduled_at=None, retry_at=NOW)
        self.validate(digest)

    def test_review_objects_reject_extra_missing_and_wrongly_typed_fields(self):
        digest = self.populated()
        intent = digest['review_batching']['intents'][0]
        digest['review_batching']['owner_alerts'].append(dict(intent_id=intent['intent_id'],
            state='EXHAUSTED', reason='Two confirmed negative deliveries', retry_at=None))
        self.validate(digest)
        paths = [('review_batching',), ('review_batching', 'intents', 0),
                 ('review_batching', 'intents', 0, 'candidate'),
                 ('review_batching', 'unresolved_batches', 0),
                 ('review_batching', 'owner_alerts', 0),
                 ('review_batching', 'owner_alerts', 1)]
        for path in paths:
            target = digest
            for key in path:
                target = target[key]
            for field in ('extra', *target):
                for operation in ('wrong_type', 'missing') if field != 'extra' else ('extra',):
                    with self.subTest(path=path, field=field, operation=operation):
                        changed = deepcopy(digest)
                        node = changed
                        for key in path:
                            node = node[key]
                        if operation == 'missing':
                            del node[field]
                        else:
                            node[field] = {'untyped': 'refused'}
                        with self.assertRaises(ValidationError):
                            self.validate(changed)

    def test_review_values_reject_invalid_ids_states_times_and_counts(self):
        digest = self.populated()
        for field, values in dict(intent_id=['bad'], state=['UNKNOWN'], ready_at=['bad'],
                changed_at=['bad'], scheduled_at=['bad'], retry_at=['bad'],
                attempts=[-1, True, '1'], held=[0, 'true']).items():
            for value in values:
                with self.subTest(field=field, value=value):
                    changed = deepcopy(digest)
                    changed['review_batching']['intents'][0][field] = value
                    with self.assertRaises(ValidationError):
                        self.validate(changed)


if __name__ == '__main__':
    unittest.main()
