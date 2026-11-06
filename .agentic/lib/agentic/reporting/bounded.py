"""Joined destination-stage budgets. No background work survives a cycle.

Each destination gets four advances, one historical reconciliation, 32 HTTP
requests and 30 seconds of admission time. Each HTTP request has a 15 second
maximum timeout; an admitted request may finish after the admission deadline.
Thus the network stage bound is 45 seconds, with both destinations concurrent.
Trusted network ports must honour the HTTP timeout contract.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from time import monotonic

from .. import ValidationError

DESTINATION_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 15
MAX_HTTP_CALLS = 32
MAX_ADVANCES = 4
MAX_RECONCILIATIONS = 1
SOURCE_SECONDS = 30
_source = ContextVar('report_source_budget', default=None)
_current = ContextVar('report_destination_budget', default=None)
_send_start = ContextVar('report_send_start', default=None)


class SourceBudget:
    """Separate cooperative deadline for trusted source collection ports.

    Network ports must use remaining() as their operation timeout. No source
    thread or late result can survive the synchronous collection stage.
    """
    def __init__(self):
        self.deadline = monotonic() + SOURCE_SECONDS

    def remaining(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ValidationError('REPORT_SOURCE_BUDGET')
        return remaining

    def __enter__(self):
        self.token = _source.set(self)
        return self

    def __exit__(self, *args):
        _source.reset(self.token)


def source_timeout():
    budget = _source.get()
    return budget.remaining() if budget is not None else SOURCE_SECONDS


class DeliveryBudget:
    def __init__(self):
        self.deadline = monotonic() + DESTINATION_SECONDS
        self.stage_deadline = self.deadline + HTTP_TIMEOUT_SECONDS
        self.cancelled = False
        self.counts = {}
        self.http_counts = {}
        self.reconciliations = {}
        self.lock = threading.Lock()

    def admit(self, platform, *, reconciliation=False):
        with self.lock:
            if self.cancelled or monotonic() >= self.deadline or self.counts.get(platform, 0) >= MAX_ADVANCES:
                return False
            if reconciliation and self.reconciliations.get(platform, 0) >= MAX_RECONCILIATIONS:
                return False
            self.counts[platform] = self.counts.get(platform, 0) + 1
            if reconciliation:
                self.reconciliations[platform] = self.reconciliations.get(platform, 0) + 1
            return True

    def http(self, platform):
        with self.lock:
            if self.cancelled or monotonic() >= self.deadline or self.http_counts.get(platform, 0) >= MAX_HTTP_CALLS:
                raise ValidationError('REPORT_DESTINATION_BUDGET')
            self.http_counts[platform] = self.http_counts.get(platform, 0) + 1
            return min(HTTP_TIMEOUT_SECONDS, self.deadline - monotonic())

    def cancel(self):
        with self.lock:
            self.cancelled = True

    @contextmanager
    def destination(self, platform):
        token = _current.set((self, platform))
        try:
            yield
        finally:
            _current.reset(token)


def admit_http(method):
    current = _current.get()
    timeout = current[0].http(current[1]) if current else HTTP_TIMEOUT_SECONDS
    if method == 'POST' and _send_start.get() is not None:
        _send_start.get()()
    return timeout


@contextmanager
def sending(start):
    token = _send_start.set(start)
    try:
        yield
    finally:
        _send_start.reset(token)
