"""CI portability and sanitized owner-admission diagnostics."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import source_only  # noqa: F401
from agentic import ValidationError, owner_admission as admission
from agentic.budgeting import ControllerBudgeting, fresh_settings
from agentic.canonical import canonical
from agentic.operating import default_operating, validate_operating
from test_operating import governance


class AdmissionDiagnosticsTests(unittest.TestCase):
    def observe(self, native, **kwargs):
        with patch.object(admission, '_require_stored_login_environment'), \
                patch.object(admission, '_github_executable', return_value=str(native)), \
                patch.object(admission.subprocess, 'run', **kwargs):
            return admission.observe_github_owner({'merge_gate': {'trusted_owner_ids': [1001]}}, native.parent)

    def test_deliberate_login_refusal_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            native = Path(raw) / 'native'
            native.write_bytes(b'fixture')
            for status in (subprocess.CompletedProcess([], 2, '', 'fixture rejection'),
                           subprocess.CompletedProcess([], 0, 'unrecognized status', '')):
                with self.subTest(exit=status.returncode):
                    with self.assertRaisesRegex(ValidationError, 'stored active github.com login was not observed'):
                        self.observe(native, return_value=status)

    def test_system_failures_report_class_without_exception_data(self):
        sensitive = 'fixture-private-account-and-path'
        failures = (OSError(sensitive), ValueError(sensitive), UnicodeError(sensitive),
                    subprocess.TimeoutExpired(sensitive, 30, output=sensitive))
        with tempfile.TemporaryDirectory() as raw:
            native = Path(raw) / 'native'
            native.write_bytes(b'fixture')
            for failure in failures:
                with self.subTest(exception=type(failure).__name__):
                    with self.assertRaises(ValidationError) as caught:
                        self.observe(native, side_effect=failure)
                    self.assertIn(type(failure).__name__, str(caught.exception))
                    self.assertNotIn(sensitive, str(caught.exception))

    def test_invalid_response_does_not_disclose_duplicate_mapping_key(self):
        with tempfile.TemporaryDirectory() as raw:
            native = Path(raw) / 'native'
            native.write_bytes(b'fixture')
            responses = [subprocess.CompletedProcess([], 0,
                'Logged in to github.com account owner (keyring)\nActive account: true', ''),
                subprocess.CompletedProcess([], 0, '{"private-key":1,"private-key":2}', '')]
            with self.assertRaises(ValidationError) as caught:
                self.observe(native, side_effect=responses)
            self.assertIn('(ValidationError)', str(caught.exception))
            self.assertNotIn('private-key', str(caught.exception))

    def test_default_off_nonrepository_needs_no_native_identity_or_owner_database(self):
        config = governance()
        value = default_operating(config['execution']['model_routing'])
        value['budgeting'] = fresh_settings()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / 'project'
            root.mkdir()
            # Exercise both an absent git marker and an initialized unborn one.
            for initialized in (False, True):
                with self.subTest(initialized=initialized):
                    if initialized:
                        (root / '.git').mkdir()
                    snapshot = replace(validate_operating(deepcopy(value), config),
                        _provenance_json=canonical({'path': str(root / 'OPERATING_CONFIG.yaml')}))
                    with patch.object(admission, '_owner_state_home', side_effect=admission.OwnerProfileUnavailable('no profile')), \
                            patch.object(admission, '_git_executable', side_effect=AssertionError('native git lookup')), \
                            patch.object(admission, '_github_executable', side_effect=AssertionError('gh lookup')), \
                            patch.object(admission.ProposalStore, 'transaction', side_effect=AssertionError('owner store write')), \
                            patch.object(admission.subprocess, 'run', side_effect=AssertionError('external command')):
                        context = ControllerBudgeting(None, config, snapshot, object(), worktree_roots=(root,))
                    self.assertFalse(context.settings['accounting_enabled'])
                    self.assertIsNone(context.database)
                    self.assertIsNone(context.registry)
                    self.assertEqual(['.git'] if initialized else [], sorted(p.name for p in root.iterdir()))


@unittest.skipUnless(os.name == 'nt', 'Windows physical executable aliases')
class NativeAliasTests(unittest.TestCase):
    def test_junction_and_case_resolve_same_native_and_cannot_hide_worker_binary(self):
        import _winapi
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            install = base / 'installation'
            install.mkdir()
            native = install / 'gh.exe'
            shutil.copyfile(sys.executable, native)
            alias = base / 'junction'
            _winapi.CreateJunction(str(install), str(alias))
            self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
            for directory in (install, alias, Path(str(install).swapcase())):
                with self.subTest(alias=directory.name), \
                        patch.dict(os.environ, {'PATH': str(directory)}), \
                        patch.object(admission, '_sandbox_group_rights', return_value=0):
                    self.assertEqual(str(native), admission._github_executable(base / 'project'))
                    with self.assertRaisesRegex(ValidationError, 'inside a worker/runtime tree'):
                        admission._github_executable(install)
                    with patch.object(admission, '_sandbox_group_rights', side_effect=lambda p: 2 if p == native else 0):
                        with self.assertRaisesRegex(ValidationError, 'can modify or replace gh'):
                            admission._github_executable(base / 'project')


if __name__ == '__main__':
    unittest.main()
