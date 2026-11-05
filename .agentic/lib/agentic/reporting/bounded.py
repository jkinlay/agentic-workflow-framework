"""Controller-owned bounded network workers; no queue, scheduler or service.

One outstanding operation per database/platform, also across store instances.
A timed-out operation cannot be cancelled safely. Keep late readback evidence
for the same intent/attempts; reconcile after a late send, never launch a
competing operation while it may still send. A restart also reconciles first.
"""
import threading
from time import monotonic

from .. import ValidationError
from ..canonical import fingerprint

DESTINATION_SECONDS = .25
MAX_ADVANCES = 4
_lock = threading.Lock()
_active = {}


class DeliveryBudget:
    def __init__(self):
        self.deadlines = {}
        self.counts = {}

    def admit(self, platform):
        if self.deadlines.get(platform, float('inf')) <= monotonic():
            return False
        count = self.counts.get(platform, 0)
        if count >= MAX_ADVANCES:
            return False
        self.counts[platform] = count + 1
        return True

    def provider(self, store, platform, provider):
        budget = self
        key = (str(store.path.resolve()), platform)

        class BoundedProvider:
            def call(self, operation, *args):
                deadline = budget.deadlines.setdefault(platform, monotonic() + DESTINATION_SECONDS)
                identity = fingerprint('bounded-report-operation', [operation, list(args)])
                with _lock:
                    prior = _active.get(key)
                    if prior is not None:
                        if not prior['done'].is_set():
                            raise ValidationError('REPORT_DESTINATION_BUSY')
                        del _active[key]
                        if operation == 'reconcile' and prior['identity'] == identity:
                            if prior['result'].get('failed'):
                                raise ValidationError('REPORT_PROVIDER_UNAVAILABLE')
                            return prior['result'].get('value')
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise ValidationError('REPORT_DESTINATION_TIME_BUDGET')
                    done, result = threading.Event(), {}
                    _active[key] = dict(done=done, identity=identity, result=result)

                    def run():
                        try:
                            result['value'] = getattr(provider, operation)(*args)
                        except Exception:
                            result['failed'] = True
                        finally:
                            done.set()

                    threading.Thread(target=run, name='awf-report-' + platform, daemon=True).start()
                if not done.wait(max(0, deadline - monotonic())):
                    raise ValidationError('REPORT_DESTINATION_TIME_BUDGET')
                with _lock:
                    if _active.get(key, {}).get('done') is done:
                        del _active[key]
                if result.get('failed'):
                    raise ValidationError('REPORT_PROVIDER_UNAVAILABLE')
                return result.get('value')

            def send(self, *args):
                return self.call('send', *args)

            def reconcile(self, *args):
                return self.call('reconcile', *args)

        return BoundedProvider()
