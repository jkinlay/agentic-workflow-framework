"""Shared reporting-only platform port; no SDK, inbound chat or live transport.

Transport must implement verify_destination(binding, secret),
send(intent, attempt_id, secret), reconcile(intent, attempts, secret).
Reconciliation returns the delivery.py channel-evidence envelope. The trusted
host owns approval, secret store, transport registration and effective mode.
"""
from copy import deepcopy

from .. import ValidationError
from .delivery import delivery_intent, require, validate_binding


class PlatformReportingAdapter:
    platform = None
    max_payload_bytes = 0

    def __init__(self, transport, approved_binding, *, resolve_secret, mode='off'):
        self.binding = validate_binding(approved_binding)
        require(self.binding['platform'] == self.platform, 'REPORT_PLATFORM_MISMATCH')
        require(mode in {'off', 'slack', 'teams', 'both'}, 'REPORT_MODE_INVALID')
        self.transport = transport
        self.resolve_secret = resolve_secret
        self.mode = mode

    def _admit(self, intent):
        require(self.mode in {self.platform, 'both'}, 'REPORTING_OFF')
        require(isinstance(intent, dict) and intent.get('binding') == self.binding, 'REPORT_DESTINATION_MISMATCH')
        require(intent == delivery_intent(intent['report_id'], intent['binding'], intent['payload']),
                'REPORT_INTENT_MISMATCH')
        require(len(intent['payload'].encode('utf-8')) <= self.max_payload_bytes, 'REPORT_PAYLOAD_TOO_LARGE')
        # Identity and mode admission always precede secret resolution.
        try:
            secret = self.resolve_secret(self.binding['secret_ref'])
        except Exception:
            raise ValidationError('REPORT_SECRET_UNAVAILABLE') from None
        require(isinstance(secret, str) and bool(secret), 'REPORT_SECRET_UNAVAILABLE')
        require(secret not in intent['payload'], 'REPORT_PAYLOAD_CONTAINS_SECRET')
        return secret

    def send(self, intent, attempt_id):
        secret = self._admit(intent)
        try:
            require(self.transport.verify_destination(deepcopy(self.binding), secret) is True,
                    'REPORT_DESTINATION_UNVERIFIED')
            # No response bodies, tokens or credentials escape this boundary.
            raw = self.transport.send(deepcopy(intent), attempt_id, secret)
            from .delivery import DeliveryOutbox
            safe = DeliveryOutbox._rejection(raw, intent, attempt_id)
            if safe is not None and secret not in str(safe):
                safe.update(delivery_id=intent['delivery_id'], destination=self.binding['destination'],
                            platform=self.platform, payload_sha256=intent['payload_sha256'])
                return safe
        except Exception:
            raise ValidationError('REPORT_PROVIDER_UNAVAILABLE') from None
        return dict(status='ACCEPTED_UNVERIFIED')

    def reconcile(self, intent, attempts):
        secret = self._admit(intent)
        try:
            require(self.transport.verify_destination(deepcopy(self.binding), secret) is True,
                    'REPORT_DESTINATION_UNVERIFIED')
            raw = self.transport.reconcile(deepcopy(intent), list(attempts), secret)
            # Keep an allowlisted receipt only; redact even an echoed credential
            # in a normally safe message identity. Never retain raw SDK objects.
            from .delivery import DeliveryOutbox
            safe = DeliveryOutbox._observation(raw, intent, attempts)
            if secret in str(safe):
                return dict(status='UNKNOWN')
            if safe['status'] == 'DELIVERED':
                safe['status'] = 'VISIBLE'
            safe.update(delivery_id=intent['delivery_id'], destination=self.binding['destination'],
                        platform=self.platform, payload_sha256=intent['payload_sha256'], attempt_ids=list(attempts))
            return safe
        except Exception:
            return dict(status='UNKNOWN')
