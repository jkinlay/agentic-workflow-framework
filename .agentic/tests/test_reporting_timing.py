"""Deadline overhead regressions; no live providers or wall-clock sleeps."""
import threading
import unittest
from unittest.mock import patch

import source_only
from agentic.reporting.bounded import DeliveryBudget


class ReportingTimingTests(unittest.TestCase):
    def test_observer_grace_is_measured_from_entry_within_original_stage_ceiling(self):
        with patch('agentic.reporting.bounded.monotonic', return_value=100):
            budget = DeliveryBudget()
            with budget.checking():
                self.assertEqual(budget.join_deadline(threading.current_thread()), 115)
            # HTTP operations retain their full stage allowance.
            self.assertEqual(budget.join_deadline(threading.current_thread()), 145)
        with patch('agentic.reporting.bounded.monotonic', return_value=129):
            with budget.checking():
                self.assertEqual(budget.join_deadline(threading.current_thread()), 144)

    def test_policy_preparation_is_shared_only_within_cycle_and_post_stays_fresh(self):
        from agentic.reporting import policy
        from test_reporting_policy_review import PolicyReviewTests
        helper = PolicyReviewTests()
        self.addCleanup(helper.doCleanups)
        case = helper.fixture()
        from agentic import operating
        snapshot = operating.read_operating(case.root, case.gov)

        @policy.policy_cycle
        def cycle(change=False):
            current = policy.ReportingPolicy(case.root, case.gov, snapshot)
            with patch.object(policy, '_git', wraps=policy._git) as read:
                shared = policy.ReportingPolicy(case.root, case.gov, current.snapshot)
                self.assertEqual(read.call_count, 0)
                if change:
                    case.publish(case.enabled())
                value = shared()  # The pre-POST admission must still observe Git.
                self.assertGreater(read.call_count, 0)
                return value

        self.assertEqual(cycle()['reporting']['mode'], 'off')
        self.assertEqual(cycle(change=True)['reporting']['mode'], 'both')
        self.assertEqual(cycle()['reporting']['mode'], 'both')
