"""AWF-275: only a known old Git may skip the reflog migration test."""
import subprocess
import unittest
from unittest.mock import Mock

import source_only


REASON = (
    "AWF275_REFTABLE_REQUIRES_GIT_2_48: "
    "Git < 2.48 cannot migrate repositories with reflogs"
)


class ReftableVersionGateTests(unittest.TestCase):
    def run_gate(self, output):
        git = Mock(return_value=output)
        try:
            source_only.require_reftable_migration(git)
        finally:
            git.assert_called_once_with('--version')

    def test_below_minimum_skips_with_exact_named_reason(self):
        for version in ('2.40.0', '2.46.0', '2.47.3'):
            with self.subTest(version=version), self.assertRaises(unittest.SkipTest) as caught:
                self.run_gate('git version ' + version)
            self.assertEqual(REASON, str(caught.exception))

    def test_at_and_above_minimum_run(self):
        for version in ('2.48.0', '2.48.1', '2.55.0', '3.0.0'):
            with self.subTest(version=version):
                self.run_gate('git version ' + version + '\n')

    def test_vendor_suffix_is_parsed(self):
        with self.assertRaises(unittest.SkipTest) as caught:
            self.run_gate('git version 2.40.0.windows.1\r\n')
        self.assertEqual(REASON, str(caught.exception))
        self.run_gate('git version 2.48.0.windows.1')

    def test_malformed_or_empty_output_does_not_skip(self):
        for output in ('', ' ', '2.40.0', 'git version unknown', 'git version 2.40',
                       'git version 2.40.x', 'git version 2.40.0garbage',
                       'git version 2.40.0\nunexpected output', None, b'git version 2.40.0'):
            with self.subTest(output=output):
                self.run_gate(output)

    def test_failed_version_query_does_not_skip(self):
        for error in (subprocess.CalledProcessError(1, ['git', '--version'],
                                                   output='git version 2.40.0'),
                      subprocess.TimeoutExpired(['git', '--version'], 1),
                      OSError('unavailable'),
                      UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid')):
            git = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__):
                source_only.require_reftable_migration(git)
                git.assert_called_once_with('--version')

    def test_unknown_version_still_exposes_migration_failure(self):
        for output in ('', 'malformed', subprocess.CalledProcessError(1, ['git', '--version'])):
            def git(*args):
                if args == ('--version',):
                    if isinstance(output, Exception):
                        raise output
                    return output
                raise subprocess.CalledProcessError(1, ['git', *args])

            with self.subTest(output=output):
                source_only.require_reftable_migration(git)
                with self.assertRaises(subprocess.CalledProcessError):
                    git('refs', 'migrate', '--ref-format=reftable')
