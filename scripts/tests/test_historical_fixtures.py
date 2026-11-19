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
    # Scope: git show with a literal or module-level constant revision, plain
    # or annotated. Wider command/revision detection belongs to AWF-309.
    tree = ast.parse(source)
    values = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    values[target.id] = node.value.value
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            values[node.target.id] = node.value.value

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


CONVERTED_REVISIONS = (
    'a3336b8990a9e59985f67bf69d41846b898db57b',
    'ee33a92f0e49c82ad96f3e46ce9c8d2f178f61d6',
    'f71ee70f32737bfb02b9b09b1e2e420529e0cc63',
)
HEX_TOKEN = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{8,40}(?![0-9a-fA-F])')


def unexpected_converted_revision_tokens(path, source):
    # index.json records provenance and is outside this *.py scan.
    # This guard test module needs revision literals in its synthetic regressions.
    if path == 'scripts/tests/test_historical_fixtures.py':
        return []
    # No exemption is needed for historical_fixtures.py: it names no revisions.
    # Only literal commit arguments to these fixture-loader calls are exempt:
    # reporting: historical_bytes; review: self.historical (wraps historical_bytes);
    # recovery: regressions.PreNineCliRegressions.historical (the same wrapper).
    loader_calls = {
        '.agentic/tests/test_reporting_e2_regressions.py': ('historical_bytes', 0, 'commit'),
        '.agentic/tests/test_awf243_review_regressions.py': ('self.historical', 0, 'revision'),
        '.agentic/tests/test_awf243_operator_recovery.py': (
            'regressions.PreNineCliRegressions.historical', 1, 'revision'),
    }
    allowed = []
    lines = source.splitlines(keepends=True)

    def offset(line, column):
        # AST columns count UTF-8 bytes; regex offsets count characters.
        return sum(map(len, lines[:line - 1])) + len(lines[line - 1].encode('utf-8')[:column].decode('utf-8'))

    if path in loader_calls:
        function, position, keyword = loader_calls[path]
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or ast.unparse(node.func) != function:
                continue
            arguments = ([node.args[position]] if len(node.args) > position else [])
            arguments += [item.value for item in node.keywords if item.arg == keyword]
            for argument in arguments:
                if isinstance(argument, ast.Constant) and argument.value in CONVERTED_REVISIONS:
                    allowed.append((offset(argument.lineno, argument.col_offset),
                                    offset(argument.end_lineno, argument.end_col_offset)))
    return [(source.count('\n', 0, match.start()) + 1, match.group())
            for match in HEX_TOKEN.finditer(source)
            if any(revision.startswith(match.group().lower()) for revision in CONVERTED_REVISIONS)
            and not any(start <= match.start() and match.end() <= end for start, end in allowed)]


class HistoricalFixtureTests(unittest.TestCase):
    def test_fixture_paths_stay_within_existing_tracked_path_limit(self):
        # Nested clones on Windows hosts hit the 260-character limit.
        paths = sorted(path.relative_to(ROOT).as_posix()
                       for path in fixtures.FIXTURES.rglob('*') if path.is_file())
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertLessEqual(len(path), 103)

    def test_included_sources_match_pins_and_index_identities_are_complete(self):
        for entry in fixtures.fixture_index()['sources']:
            with self.subTest(commit=entry['commit'], path=entry['path']):
                self.assertRegex(entry['commit'], r'^[0-9a-f]{40}$')
                self.assertRegex(entry['blob'], r'^[0-9a-f]{40}$')
                self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
                self.assertGreater(entry['length'], 0)
                self.assertEqual(entry['fixture'], 'blobs/' + entry['sha256'])
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

    def test_guard_detects_annotated_constant_from_review_reproduction(self):
        source = ('REV: str = "a3336b8990a9e59985f67bf69d41846b898db57b"\n'
                  'subprocess.check_output(["git", "show", '
                  'f"{REV}:.agentic/lib/agentic/continuous_controller.py"])')
        self.assertEqual([(2, CONVERTED_REVISIONS[0])], pinned_git_read_revisions(source))

    def test_tripwire_detects_all_prefixes_in_indirect_commands(self):
        for revision in CONVERTED_REVISIONS:
            for length in range(8, 41):
                pin = revision[:length]
                sources = (
                    "def historical(revision):\n    return git('show', revision + ':file')\nhistorical('" + pin + "')",
                    "command = ['git', 'show', '" + pin + ":file']\nsubprocess.check_output(command)",
                    "subprocess.check_output(['git', 'cat-file', '-p', '" + pin + ":file'])",
                )
                for source in sources:
                    with self.subTest(pin=pin, source=source):
                        self.assertEqual([pin], [token for line, token in
                            unexpected_converted_revision_tokens('other.py', source)])

    def test_tripwire_matches_standalone_tokens_in_any_source_text(self):
        pin = CONVERTED_REVISIONS[0]
        for source in ("# " + pin, "value = '" + pin.upper() + "'", "# x" + pin + "g"):
            with self.subTest(source=source):
                self.assertEqual(1, len(unexpected_converted_revision_tokens('other.py', source)))
        for token in (pin[:7], '0' + pin, pin + 'f', 'deadbeef'):
            self.assertEqual([], unexpected_converted_revision_tokens('other.py', '# ' + token))

    def test_tripwire_exempts_only_fixture_loader_commit_arguments(self):
        pin = CONVERTED_REVISIONS[0]
        for path, call in (
            ('.agentic/tests/test_reporting_e2_regressions.py', 'historical_bytes'),
            ('.agentic/tests/test_awf243_review_regressions.py', 'self.historical'),
            ('.agentic/tests/test_awf243_operator_recovery.py',
             'regressions.PreNineCliRegressions.historical'),
        ):
            prefix = 'self, ' if call.startswith('regressions.') else ''
            source = call + '(' + prefix + repr(pin) + ", 'file')"
            with self.subTest(path=path):
                self.assertEqual([], unexpected_converted_revision_tokens(path, source))
                self.assertEqual([(1, pin)], unexpected_converted_revision_tokens('other.py', source))
                self.assertEqual([(2, pin)], unexpected_converted_revision_tokens(path, source + '\n# ' + pin))
                self.assertEqual([(1, pin)], unexpected_converted_revision_tokens(path, 'value = ' + repr(pin)))
                wrong_argument = call + '(' + prefix + "'HEAD', " + repr(pin) + ')'
                self.assertEqual([(1, pin)], unexpected_converted_revision_tokens(path, wrong_argument))
                self.assertEqual([(1, pin[:8])], unexpected_converted_revision_tokens(path,
                    call + '(' + prefix + repr(pin[:8]) + ", 'file')"))

    def test_converted_revisions_do_not_reappear_outside_fixture_loader_calls(self):
        self.assertEqual(set(CONVERTED_REVISIONS),
                         {entry['commit'] for entry in fixtures.fixture_index()['sources']})
        findings = []
        for folder in (ROOT / '.agentic/tests', ROOT / 'scripts/tests'):
            for path in sorted(folder.rglob('*.py')):
                relative = path.relative_to(ROOT).as_posix()
                findings.extend((relative, line, token) for line, token in
                    unexpected_converted_revision_tokens(relative, path.read_text(encoding='utf-8')))
        self.assertEqual([], findings, 'Converted revision tokens belong only in fixture-loader calls')

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
