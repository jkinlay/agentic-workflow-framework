"""Caller regressions for diagnostic presentation; no live provider activity."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import source_only  # noqa: F401
from agentic import ValidationError, cli
from agentic.interaction import (loop_next_step, next_step, rejected_next_step,
                                 render_markdown)
from agentic.review_loop import _pause_diagnostic

ROOT = Path(__file__).resolve().parents[2]


class DiagnosticCliTests(unittest.TestCase):
    def setUp(self):
        self.secret = 'ghp_' + 'caller_probe_123456789'
        self.local = '/private/fixture space/config.json'
        self.hostile = (f'token={self.secret} "{self.local}"\x00\x1b\n'
                        '# heading [click](https://example.invalid) ``` <script> & '
                        + '\u754c' * 6000)

    def safe(self, text):
        self.assertLessEqual(len(text.encode('utf-8')), 4096)
        self.assertNotIn(self.secret, text)
        self.assertNotIn(self.local, text)
        self.assertFalse(any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text))

    def review(self, format):
        spec = importlib.util.spec_from_file_location(
            'awf_cli_diagnostic_test', ROOT / '.agentic/scripts/review_loop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(module, 'verify_installed', side_effect=ValidationError(self.hostile)), \
                patch.object(module, 'load_config') as config, \
                patch.object(module, 'HostDriver') as driver, \
                redirect_stdout(out), redirect_stderr(err):
            code = module.main(['--config', str(Path(directory) / 'config.json'),
                                '--format', format, 'check'])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), '')
        config.assert_not_called()
        driver.assert_not_called()
        return err.getvalue()

    def test_review_loop_rejection_json_redacts_and_bounds_reason(self):
        value = json.loads(self.review('json'))
        self.assertEqual(value['status'], 'REJECTED')
        self.assertIs(value['merge_authorized'], False)
        self.assertTrue(value['reason'].startswith('DIAGNOSTIC_INVALID: '))
        self.safe(value['reason'])
        self.safe(value['next_step']['action'])

    def test_review_loop_rejection_markdown_escapes_and_bounds_reason(self):
        text = self.review('markdown')
        lines = text.splitlines()
        self.assertEqual(lines[0], 'State: REJECTED')
        self.safe(lines[1])
        self.safe(next(line for line in lines if line.startswith('Next: ')))
        for probe in ('# heading', '[click]', '```', '<script>'):
            self.assertNotIn(probe, text)

    def test_workflow_rejection_json_redacts_and_bounds_reason(self):
        for error in (ValidationError, ValueError):
            with self.subTest(error=error):
                value = self.workflow(error(self.hostile))
                self.assertEqual(value['status'], 'REJECTED')
                self.assertIs(value['execution_authority'], False)
                self.assertTrue(value['reason'].startswith('DIAGNOSTIC_INVALID: '))
                self.safe(value['reason'])

    def test_review_loop_markdown_autolinks_are_inert_before_byte_bound(self):
        # C195-R0-017-001: GFM autolinks need no explicit link delimiters.
        for address, escaped in (
                ('https://example.invalid/approve', 'https&#58;//example.invalid/approve'),
                ('http://example.invalid', 'http&#58;//example.invalid'),
                ('www.example.com', 'www&#46;example.com'),
                ('WWW.example.com', 'WWW&#46;example.com'),
                ('reviewer@example.invalid', 'reviewer&#64;example.invalid')):
            for suffix in ('', ' ' + 'www.example.com ' * 400):
                with self.subTest(address=address, oversized=bool(suffix)):
                    self.hostile = 'failure ' + address + suffix
                    text = self.review('markdown')
                    reason = text.splitlines()[1]
                    action = next(line for line in text.splitlines() if line.startswith('Next: '))
                    for scalar in (reason, action):
                        self.safe(scalar)
                        self.assertNotIn(address, scalar)
                        self.assertIn('failure ' + escaped, scalar)
                        if suffix:
                            self.assertIn('truncated', scalar)

    def test_review_loop_markdown_escapes_inline_tildes_before_byte_bound(self):
        for detail in ('failure ~~approval required~~',
                       'failure ~~approval required~~ ' + '~' * 4000):
            with self.subTest(detail=detail[:40]):
                self.hostile = detail
                text = self.review('markdown')
                reason = text.splitlines()[1]
                action = next(line for line in text.splitlines() if line.startswith('Next: '))
                for scalar in (reason, action):
                    self.safe(scalar)
                    self.assertNotIn('~', scalar)
                    self.assertIn('failure &#126;&#126;approval required&#126;&#126;', scalar)
                    if len(detail) > 4000:
                        self.assertIn('truncated', scalar)

    def test_markdown_preserves_already_inert_s3_url_reason(self):
        reason = _pause_diagnostic('WORKER_FAILURE', 'failure https://example.invalid')
        record = {'phase': 'PAUSED', 'reason': reason}
        before = deepcopy(record)
        text = render_markdown(dict(record, next_step=loop_next_step(record)))
        self.assertEqual(text.splitlines()[1], reason)
        self.assertIn('https://example&#46;invalid', reason)
        self.assertEqual(record, before)
        # A literal domain before an entity can still form an autolink.
        partial = 'failure https://sub.example&#46;invalid'
        text = render_markdown({'status': 'REJECTED', 'reason': partial,
                                'next_step': rejected_next_step(partial)})
        self.assertIn('https&#58;//sub.example&#46;invalid', text.splitlines()[1])

    def workflow(self, error):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, 'verify_installed', side_effect=error), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--root', directory, 'verify-installation'])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), '')
        return json.loads(err.getvalue())

    def test_derived_next_step_does_not_reintroduce_raw_reason(self):
        for step in (rejected_next_step(self.hostile),
                     loop_next_step({'phase': 'PAUSED', 'reason': self.hostile})):
            self.safe(step['action'])
            self.assertEqual(step['owner'], 'controller')
            self.assertEqual(step['trigger'], 'now' if step['action'].find('prerequisite') >= 0
                             else 'after technical reconciliation; continue independent ready work now')
            self.assertIs(step['authorization']['required'], False)
            text = render_markdown({'status': 'REJECTED', 'reason': self.hostile,
                                    'next_step': step})
            self.safe(next(line for line in text.splitlines() if line.startswith('Next: ')))

    def test_routing_preserves_cap_budget_and_inflight(self):
        for prefix in ('REVIEW_CAP_REACHED', 'REVIEW_CAP_PARKED'):
            step = loop_next_step({'phase': 'PAUSED', 'reason': prefix + self.hostile})
            self.assertTrue(step['authorization']['required'])
            self.assertEqual(step['owner'], 'owner through the controller')
        step = loop_next_step({'phase': 'PAUSED', 'reason': 'x' * 5000 + ' budget'})
        self.assertIn('do not reset counters', step['action'])
        identifier = 'evidence-run-0123456789'
        for phase in ('PAUSED', 'REVIEW'):
            step = loop_next_step({'phase': phase, 'reason': self.hostile,
                                   'inflight': {'id': identifier}})
            self.assertIn(identifier, step['action'])
            self.assertFalse(step['authorization']['required'])

    def test_markdown_preserves_authorization_phrase_and_retained_records(self):
        reason = _pause_diagnostic('WORKER_FAILURE', self.hostile)
        record = {'phase': 'PAUSED', 'reason': reason, 'raw_report': self.hostile,
                  'binding': {'id': 'exact-evidence-0123456789'}}
        before = deepcopy(record)
        value = dict(record, next_step=loop_next_step(record))
        text = render_markdown(value)
        self.safe(text.splitlines()[1])
        self.assertIn(reason, text)
        self.assertNotIn('&amp;#', text)
        self.assertEqual(record, before)
        phrase = 'AWF1.2 APPROVE exact-evidence-0123456789 `bytes` & []'
        step = next_step('Obtain owner decision.', authorization={
            'required': True, 'reason': 'Owner decision', 'source': 'policy', 'phrase': phrase})
        value = {'status': 'WAITING', 'summary': 'Ready', 'next_step': step,
                 'evidence_id': record['binding']['id']}
        before = deepcopy(value)
        self.assertIn('\n' + phrase + '\n', render_markdown(value))
        self.assertEqual(value, before)

    def test_workflow_oserror_keeps_access_unavailable_denial(self):
        value = self.workflow(PermissionError(13, 'fixture access denied', '/fixture/item'))
        self.assertEqual(value['status'], 'ACCESS_UNAVAILABLE')
        self.assertFalse(value['execution_authority'])

    def test_markdown_expansion_is_bounded_after_escaping(self):
        for detail in ('&*[]`<>!' * 450, chr(0x754c) * 6000):
            with self.subTest(detail=detail[:10]):
                text = render_markdown({'status': 'REJECTED', 'reason': detail,
                                        'next_step': rejected_next_step(detail)})
                reason = text.splitlines()[1]
                action = next(line for line in text.splitlines() if line.startswith('Next: '))
                self.safe(reason)
                self.safe(action)
                self.assertIn('truncated', reason)
                # Every entity is complete, including the final truncation marker.
                import re
                self.assertNotIn('&', re.sub(r'&#[0-9]+;', '', reason))

    def test_markdown_block_syntax_remains_inert_prose(self):
        for detail in ('---', '- item', '===', '~~~', '1. item', '    indented'):
            with self.subTest(detail=detail):
                value = {'status': 'REJECTED', 'reason': detail,
                         'next_step': rejected_next_step(detail)}
                self.assertNotEqual(render_markdown(value).splitlines()[1], detail)
