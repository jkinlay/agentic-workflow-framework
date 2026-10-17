"""Current-policy fixtures for the byte-preserved parked AWF-36 tests.

Unittest discovery imports this module alongside test_awf36_controller_automation.
Only its setUp is extended; every original test method and assertion still runs.
Run the pair with discovery's ``test_awf36_*.py`` pattern (or import this helper
before loading the parked module in a custom runner). All provider patches are
scoped to each test and removed by addCleanup, including on failure.
"""
from __future__ import annotations

from functools import wraps
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_awf36_controller_automation as parked


_ticket = parked.ticket
_dispatch_receipt = parked.dispatch_receipt
_production_cycle = parked.production_controller_cycle
_set_up = parked.ControllerAutomationTests.setUp


def ticket(name, *, role="writer", stage="implementation"):
    # Current publication policy requires concrete KEY-number ticket identities.
    names = ("MASTER", "IMPLEMENTATION", "REVIEW", "AMENDMENT", "PENDING", "UNKNOWN")
    if name.startswith("AWF-36-"):
        name = "AWF-" + str(360 + names.index(name.removeprefix("AWF-36-")))
    return _ticket(name, role=role, stage=stage)


def dispatch_receipt(payload, observed_at):
    receipt = _dispatch_receipt(payload, observed_at)
    keys = ("dispatch_schema_version", "lane_start_waivers")
    if "reconcile_nonce" in payload:
        keys += ("reconcile_nonce", "reconcile_at")
    receipt.update({key: payload[key] for key in keys})
    receipt.update(dispatch_state="TERMINAL", terminal=True)
    return receipt


@wraps(_production_cycle)
def production_cycle(*args, **kwargs):
    kwargs.setdefault("observe_reservations", lambda request: {})
    return _production_cycle(*args, **kwargs)


@wraps(_set_up)
def set_up(self):
    _set_up(self)
    for fixture_patch in (
        patch.object(parked, "ticket", ticket),
        patch.object(parked, "dispatch_receipt", dispatch_receipt),
        patch.object(parked, "production_controller_cycle", production_cycle),
        patch("agentic.continuous_controller.canonical_reservation_sources", return_value=[]),
    ):
        fixture_patch.start()
        self.addCleanup(fixture_patch.stop)


parked.ControllerAutomationTests.setUp = set_up
