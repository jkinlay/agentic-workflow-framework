"""Historical sources stay byte-exact and independent of Git object retention."""
import ast
import hashlib
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/tests'))
import historical_fixtures as fixtures


PIN = re.compile(r'(?<![0-9a-f])[0-9a-f]{4,40}:')
# AWF-309 will convert this receipt reader. This commit is an ancestor of
# origin/main and the release tags, so CI can fetch it.
PINNED_GIT_READ_EXCEPTIONS = {
    ('.agentic/tests/test_upgrade_receipts.py',
     '1f5f146352ff0a8222a6be1ef9186e4609b11033'),
}


def pinned_git_reads(source):
    return sorted({line for line, revision in pinned_git_read_revisions(source)})


def pinned_git_read_revisions(source):
    """Inspect command arguments, not quoted Python used as scanner test data."""
    tree = ast.parse(source)
    values = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    values[target.id] = node.value.value

    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id, '')
        if isinstance(node, ast.FormattedValue):
            return text(node.value)
        if isinstance(node, ast.JoinedStr):
            return ''.join(text(item) for item in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return text(node.left) + text(node.right)
        return ''

    findings = []

    def record(line, parts):
        findings.extend((line, match.group()[:-1])
                        for part in parts for match in PIN.finditer(part))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
        if name in {'git', 'git_run', 'run_git'}:
            parts = [text(argument) for argument in node.args]
            if 'show' in parts and any(PIN.search(part) for part in parts):
                record(node.lineno, parts)
        for argument in node.args:
            if isinstance(argument, (ast.List, ast.Tuple)):
                parts = [text(item) for item in argument.elts]
                if 'git' in parts and 'show' in parts and any(PIN.search(part) for part in parts):
                    record(node.lineno, parts)
            elif isinstance(argument, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                command = text(argument)
                if (name in {'run', 'check_output', 'check_call', 'Popen', 'getoutput'}
                        and re.search(r'\bgit\s+.*\bshow\s+', command) and PIN.search(command)):
                    record(node.lineno, [command])
    return sorted(set(findings))


def unexpected_pinned_git_reads(path, source):
    return [(line, revision) for line, revision in pinned_git_read_revisions(source)
            if (path, revision) not in PINNED_GIT_READ_EXCEPTIONS]


class HistoricalFixtureTests(unittest.TestCase):
    def test_included_sources_match_pins_and_index_identities_are_complete(self):
        for entry in fixtures.fixture_index()['sources']:
            with self.subTest(commit=entry['commit'], path=entry['path']):
                self.assertRegex(entry['commit'], r'^[0-9a-f]{40}$')
                self.assertRegex(entry['blob'], r'^[0-9a-f]{40}$')
                self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
                self.assertGreater(entry['length'], 0)
                self.assertIn(entry['fixture'], ('blobs/' + entry['sha256'],
                    '../upgrades/blobs/' + entry['sha256']))
                # Shared upgrade blobs are excluded from source ZIPs. Their
                # existing receipt tests retain their source/HEAD guards and
                # verify these same pins through historical_bytes on every use.
                if entry['fixture'].startswith('blobs/'):
                    raw = fixtures.historical_bytes(entry['commit'], entry['path'])
                    self.assertEqual(entry['sha256'], hashlib.sha256(raw).hexdigest())

    def test_corrupt_bytes_and_each_incorrect_pin_fail_never_skip(self):
        entry = fixtures.fixture_index()['sources'][0]
        index_read = Path.read_bytes
        raw = fixtures.historical_bytes(entry['commit'], entry['path'])
        def corrupt(path):
            return raw[:-1] + bytes([raw[-1] ^ 1]) if path.name == entry['sha256'] else index_read(path)
        with patch.object(Path, 'read_bytes', corrupt):
            with self.assertRaisesRegex(AssertionError, 'integrity mismatch'):
                fixtures.historical_bytes(entry['commit'], entry['path'])
        for field in ('blob', 'sha256', 'length'):
            changed = {**entry, field: 0 if field == 'length' else '0' * len(entry[field])}
            with self.subTest(field=field), patch.object(fixtures, 'fixture_index', return_value={'sources': [changed]}):
                with self.assertRaisesRegex(AssertionError, 'integrity mismatch'):
                    fixtures.historical_bytes(entry['commit'], entry['path'])

    def test_missing_source_fails_never_skips(self):
        with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
            fixtures.historical_bytes('0' * 40, 'absent')

    def test_guard_detects_pinned_commands_but_ignores_current_and_quoted_sources(self):
        for pin in ('abcd1234', 'a' * 40):
            self.assertEqual([1], pinned_git_reads("run(['git', 'show', '" + pin + ":file'])"))
            self.assertEqual([1], pinned_git_reads("run('git show " + pin + ":file')"))
            self.assertEqual([1], pinned_git_reads("git('show', '" + pin + ":file')"))
        self.assertEqual([2], pinned_git_reads("REV = 'abcd1234'\nrun(['git', 'show', f'{REV}:file'])"))
        self.assertEqual([], pinned_git_reads("run(['git', 'show', 'HEAD:file'])"))
        self.assertEqual([], pinned_git_reads('source = "run([\'git\', \'show\', \'abcd1234:file\'])"'))

    def test_tests_do_not_read_pinned_revisions_from_git(self):
        findings = []
        observed_exceptions = set()
        for folder in (ROOT / '.agentic/tests', ROOT / 'scripts/tests'):
            for path in sorted(folder.rglob('*.py')):
                relative = path.relative_to(ROOT).as_posix()
                source = path.read_text(encoding='utf-8')
                observed_exceptions.update((relative, revision)
                    for line, revision in pinned_git_read_revisions(source)
                    if (relative, revision) in PINNED_GIT_READ_EXCEPTIONS)
                findings.extend(f'{relative}:{line}'
                                for line, revision in unexpected_pinned_git_reads(relative, source))
        self.assertEqual(PINNED_GIT_READ_EXCEPTIONS, observed_exceptions,
                         'AWF-309: remove the stale pinned Git read exception after conversion')
        self.assertEqual([], findings, 'Vendor historical sources and record them in the fixture index')

    def test_receipt_exception_is_detected_in_real_tree_and_tolerated(self):
        path = '.agentic/tests/test_upgrade_receipts.py'
        revision = '1f5f146352ff0a8222a6be1ef9186e4609b11033'
        source = (ROOT / path).read_text(encoding='utf-8')
        self.assertEqual({(path, revision)}, PINNED_GIT_READ_EXCEPTIONS)
        reads = pinned_git_read_revisions(source)
        self.assertEqual([revision], [pin for line, pin in reads])
        self.assertEqual(pinned_git_reads(source), [line for line, pin in reads])
        self.assertEqual([], unexpected_pinned_git_reads(path, source))

    def test_receipt_exception_rejects_different_revision_in_same_path(self):
        path = '.agentic/tests/test_upgrade_receipts.py'
        revision = 'a' * 40
        source = "REV = '" + revision + "'\nrun(['git', 'show', f'{REV}:file'])"
        self.assertEqual([(2, revision)], pinned_git_read_revisions(source))
        self.assertEqual([(2, revision)], unexpected_pinned_git_reads(path, source))

    def test_receipt_exception_rejects_same_revision_in_different_path(self):
        revision = '1f5f146352ff0a8222a6be1ef9186e4609b11033'
        source = "run(['git', 'show', '" + revision + ":file'])"
        self.assertEqual([(1, revision)], pinned_git_read_revisions(source))
        self.assertEqual([(1, revision)], unexpected_pinned_git_reads('other.py', source))


if __name__ == '__main__':
    unittest.main()
