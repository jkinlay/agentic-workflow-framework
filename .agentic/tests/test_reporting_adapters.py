import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters'))
from agentic import ValidationError
from test_reporting_delivery import Provider, binding
from test_reporting_records import request


class AdapterTests(unittest.TestCase):
    def test_both_adapters_default_off_and_wrong_binding_before_secret(self):
        for platform in ('slack', 'teams'):
            module = importlib.import_module(platform + '_reporting_adapter')
            resolved = []
            adapter = module.ReportingAdapter(Provider(), binding(platform),
                resolve_secret=lambda ref: resolved.append(ref) or 'fixture-secret')
            from agentic.reporting.delivery import delivery_intent
            intent = delivery_intent('report-1', binding(platform), 'payload')
            with self.assertRaises(ValidationError):
                adapter.send(intent, 'attempt-1')
            self.assertEqual(resolved, [])
            adapter = module.ReportingAdapter(Provider(), binding(platform), mode=platform,
                resolve_secret=lambda ref: resolved.append(ref) or 'fixture-secret')
            intent['binding']['destination'] = 'foreign'
            with self.assertRaises(ValidationError):
                adapter.send(intent, 'attempt-1')
            self.assertEqual(resolved, [])

    def test_secret_errors_are_fixed_and_provider_errors_redacted(self):
        from agentic.reporting.delivery import delivery_intent
        module = importlib.import_module('slack_reporting_adapter')
        class Transport:
            def send(self, *args):
                raise RuntimeError('fixture-secret')
        adapter = module.ReportingAdapter(Transport(), binding(), mode='slack', resolve_secret=lambda ref: 'fixture-secret')
        with self.assertRaisesRegex(ValidationError, '^REPORT_PROVIDER_UNAVAILABLE$'):
            adapter.send(delivery_intent('report-1', binding(), 'payload'), 'attempt-1')

    def test_exception_traceback_does_not_expose_secret_store_error(self):
        import traceback
        from agentic.reporting.delivery import delivery_intent
        module = importlib.import_module('teams_reporting_adapter')
        def secret_failure(ref):
            raise RuntimeError('DO_NOT_EXPOSE_CREDENTIAL')
        adapter = module.ReportingAdapter(Provider(), binding('teams'), mode='teams', resolve_secret=secret_failure)
        try:
            adapter.send(delivery_intent('report-1', binding('teams'), 'payload'), 'attempt-1')
        except ValidationError:
            self.assertNotIn('DO_NOT_EXPOSE_CREDENTIAL', traceback.format_exc())
        else:
            self.fail('Secret resolution must fail closed')

    def test_rendering_requires_verified_detail_and_never_truncates(self):
        records = importlib.import_module('agentic.reporting.records')
        render = importlib.import_module('agentic.reporting.render')
        record = records.prepare_snapshot(request(), request()['binding'], verify_sources=lambda v, b: v)
        for platform in ('teams', 'slack'):
            text = render.render_channel(record, platform, max_bytes=10000)
            self.assertIn('Unknown', text)
            self.assertIn('scope-1', text)
            self.assertIn('Owner action', text)
            with self.assertRaises(ValidationError):
                render.render_channel(record, platform, max_bytes=10)
            with self.assertRaises(ValidationError):
                render.render_channel(record, platform, detail_url='https://example.invalid/report')
            text = render.render_channel(record, platform, detail_url='https://example.invalid/report',
                                         verify_detail=lambda url, binding: True)
            self.assertIn('https://example.invalid/report', text)

    def test_both_mode_uses_fake_readback_and_resolves_rotation_each_operation(self):
        from agentic.reporting.delivery import delivery_intent
        class Transport:
            def verify_destination(self, approved, secret):
                return secret in {'fixture-first', 'fixture-rotated'}
            def send(self, intent, attempt_id, secret):
                return {'http_status': 202}
            def reconcile(self, intent, attempts, secret):
                p = Provider()
                p.observation = dict(status='VISIBLE', evidence_kind='CHANNEL_READBACK', message_id=('1355517523.000005' if intent['binding']['platform'] == 'slack' else '1616990032035'))
                return p.reconcile(intent, attempts)
        for platform in ('teams', 'slack'):
            module = importlib.import_module(platform + '_reporting_adapter')
            secrets = iter(['fixture-first', 'fixture-rotated'])
            adapter = module.ReportingAdapter(Transport(), binding(platform), mode='both',
                                               resolve_secret=lambda ref: next(secrets))
            intent = delivery_intent('report-1', binding(platform), 'payload')
            self.assertEqual(adapter.send(intent, 'attempt-1')['status'], 'ACCEPTED_UNVERIFIED')
            receipt = adapter.reconcile(intent, ['attempt-1'])
            self.assertEqual(receipt['status'], 'VISIBLE')
            self.assertNotIn('fixture-rotated', str(receipt))


if __name__ == '__main__':
    unittest.main()
