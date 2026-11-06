"""Shared reporting-only platform port; no SDK, inbound chat or live transport.

Transport must implement verify_destination(binding, secret),
send(intent, attempt_id, secret), reconcile(intent, attempts, secret).
Reconciliation returns the delivery.py channel-evidence envelope. The trusted
host owns approval, secret store, transport registration and effective mode.
"""
from copy import deepcopy
import os
import re

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
        return self.send_reserved(intent, attempt_id, lambda: None)

    def send_reserved(self, intent, attempt_id, start):
        secret = self._admit(intent)
        try:
            require(self.transport.verify_destination(deepcopy(self.binding), secret) is True,
                    'REPORT_DESTINATION_UNVERIFIED')
            from .bounded import sending
            # The transport calls start after payload and HTTP-budget admission,
            # immediately before POST. Verification consumes no attempt.
            with sending(start):
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


class SlackReportingAdapter(PlatformReportingAdapter):
    platform = 'slack'
    max_payload_bytes = 12000


class TeamsReportingAdapter(PlatformReportingAdapter):
    platform = 'teams'
    max_payload_bytes = 20000


def environment_secret(reference):
    """The reference host supports environment references only, never literals."""
    require(isinstance(reference, str) and re.fullmatch(r'env:[A-Za-z_][A-Za-z0-9_-]*', reference),
            'REPORT_SECRET_RESOLVER_REQUIRED')
    return os.environ.get(reference[4:])


def build_reporting_providers(configuration, operating, binding, *, http=None, clock=None):
    """Reviewed construction; project settings select bindings, never code.

    Tenant/workspace and sender identity are project-owned transport bindings.
    Construction validates everything without resolving any credential.
    """
    from .settings import effective_reporting
    from .http_transport import SlackTransport, TeamsTransport
    settings = effective_reporting(operating, binding=binding)
    if settings['mode'] == 'off':
        return {}
    selected = {'teams', 'slack'} if settings['mode'] == 'both' else {settings['mode']}
    require(isinstance(configuration, dict) and selected <= configuration.keys(), 'REPORT_TRANSPORT_BINDING_REQUIRED')
    result = {}
    for destination in settings['bindings']:
        platform = destination['platform']
        if platform not in selected:
            continue
        value = configuration[platform]
        require(isinstance(value, dict) and set(value) == {'context', 'sender'}, 'REPORT_TRANSPORT_BINDING_REQUIRED')
        require(all(isinstance(v, str) and 0 < len(v) <= 256 and not any(ord(c) < 32 for c in v)
                    for v in value.values()), 'REPORT_TRANSPORT_BINDING_REQUIRED')
        require(re.fullmatch(r'env:[A-Za-z_][A-Za-z0-9_-]*', destination['secret_ref']), 'REPORT_SECRET_RESOLVER_REQUIRED')
        require(re.fullmatch(r'[CG][A-Z0-9]+', destination['destination']) if platform == 'slack' else
                len(destination['destination'].split('/')) == 2 and
                all(p and p not in {'.', '..'} for p in destination['destination'].split('/')),
                'REPORT_DESTINATION_INVALID')
        adapter, transport = ((SlackReportingAdapter, SlackTransport) if platform == 'slack'
                              else (TeamsReportingAdapter, TeamsTransport))
        options = dict(context=value['context'], sender=value['sender'], clock=clock)
        if http is not None:
            options['http'] = http
        result[platform] = adapter(transport(**options), destination,
                                  resolve_secret=environment_secret, mode=settings['mode'])
    return result
