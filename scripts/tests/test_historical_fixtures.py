"""Byte-exact fixtures and the owner's four approved live-history readers.

Owner decision, 20 Nov 2026: "All four: documented history (Recommended)".
The allowlist below documents each reader and its exact path/full revision.
These commits are ancestors of main and release tags v1.9.x, so CI's
full-history clone has them; new pinned reads are refused. Fixture-backed
readers remain independent of Git object retention. The converted-revision
tripwire prevents their historical pins returning to live Git reads.

This source-repository guard requires Git history and fails if it is absent,
including shallow clones missing approved commits. Installed copies exclude
scripts/tests, so this guard is not collected there; no skip is added.
"""
import ast
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/tests'))
import historical_fixtures as fixtures


PIN = re.compile(r'(?<![0-9a-f])[0-9a-f]{4,40}:')
# Identities are path + full revision, never whole-file or line exemptions.
LIVE_HISTORY_READERS = {
    ('.agentic/tests/test_upgrade_receipts.py',
     '1f5f146352ff0a8222a6be1ef9186e4609b11033'): {
        'owner_decision_date': '2026-11-20',
        'owner_decision': 'All four: documented history (Recommended)',
        'reason': "The commit is an ancestor of main and release tags v1.9.x, so CI's full-history clone has it.",
        'reads': 'git_blob: 209 immutable manifest paths, MANIFEST.json, .agentic/PROJECT_CONFIG.yaml and .agentic/workflow-version.yaml for historical receipt identity.',
    },
    ('.agentic/tests/upgrade_195_fixtures.py',
     '8ad399943c8f5e4f17db72f3e321b9365ee27782'): {
        'owner_decision_date': '2026-11-20',
        'owner_decision': 'All four: documented history (Recommended)',
        'reason': "The commit is an ancestor of main and release tags v1.9.x, so CI's full-history clone has it.",
        'reads': 'materialize_published: entire raw published v1.9.5 commit tree through publish_release.materialize_commit and git cat-file --batch.',
    },
    ('.agentic/tests/test_twelve_streams.py',
     '8ad399943c8f5e4f17db72f3e321b9365ee27782'): {
        'owner_decision_date': '2026-11-20',
        'owner_decision': 'All four: documented history (Recommended)',
        'reason': "The commit is an ancestor of main and release tags v1.9.x, so CI's full-history clone has it.",
        'reads': 'test_published_195_runtime_retains_original_upper_label_refusal: git archive of .agentic/** and MANIFEST.json for the published runtime refusal.',
    },
    ('.agentic/tests/test_awf242_settings_second_look.py',
     '8ad399943c8f5e4f17db72f3e321b9365ee27782'): {
        'owner_decision_date': '2026-11-20',
        'owner_decision': 'All four: documented history (Recommended)',
        'reason': "The commit is an ancestor of main and release tags v1.9.x, so CI's full-history clone has it.",
        'reads': 'test_legacy_event_chain_survives_merged_block: git archive of .agentic/lib/** and .agentic/schemas/** for the published legacy event chain.',
    },
}


def pinned_git_reads(source):
    return sorted({line for line, revision in pinned_git_read_revisions(source)})


def pinned_git_read_revisions(source):
    return sorted({(line, revision) for line, revision, form in pinned_git_read_details(source)})


def pinned_git_read_details(source):
    """Conservative source-only data flow; never execute quoted Python test data.

    Resolve lexical bindings and calls to local helpers, including defaults.
    Alternative assignments are retained, so a dynamic/unknown value cannot
    erase a known pin. Cycles stop at the binding/call already being inspected.
    The release materializer is an explicit indirect raw-tree Git read sink.
    This is a syntax guard, not an arbitrary Python execution analyser.
    """
    tree = ast.parse(source)
    findings = set()
    revision_token = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{4,40}(?![0-9a-fA-F])')
    runners = {'run', 'check_output', 'check_call', 'Popen', 'getoutput', 'run_bounded'}
    git_helpers = {'git', 'git_run', 'run_git'}

    def nodes(body):
        for node in body:
            yield node
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from nodes(ast.iter_child_nodes(node))

    def name(node):
        return node.id if isinstance(node, ast.Name) else getattr(node, 'attr', '')

    def scope(body, outer=(), parameters=None, active=()):
        local = {}
        functions = {}
        members = list(nodes(body))
        for node in members:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        local.setdefault(target.id, []).append(node.value)
        for parameter, arguments in (parameters or {}).items():
            local.setdefault(parameter, []).extend(arguments)
        environment = ((local, functions), *outer)

        def values(node, seen=(), lookup=None):
            lookup = environment if lookup is None else lookup
            if isinstance(node, (str, tuple)):
                return {node}
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                return {node.value.decode('utf-8', errors='surrogateescape') if isinstance(node.value, bytes) else node.value}
            if isinstance(node, ast.Name):
                for depth, (bindings, unused) in enumerate(lookup):
                    if node.id in bindings:
                        key = (id(bindings), node.id)
                        if key in seen:
                            return {''}
                        return {value for item in bindings[node.id]
                                for value in values(item, (*seen, key), lookup[depth:])} or {''}
            if isinstance(node, ast.FormattedValue):
                return values(node.value, seen, lookup)
            if isinstance(node, (ast.JoinedStr, ast.List, ast.Tuple)):
                parts = {''} if isinstance(node, ast.JoinedStr) else {()}
                for item in (node.values if isinstance(node, ast.JoinedStr) else node.elts):
                    resolved = values(item.value if isinstance(item, ast.Starred) else item, seen, lookup)
                    parts = {left + (right if isinstance(left, str) else
                                     right if isinstance(item, ast.Starred) and isinstance(right, tuple)
                                     else (right,))
                             for left in parts for right in resolved
                             if not isinstance(left, str) or isinstance(right, str)}
                return parts
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return {left + right for left in values(node.left, seen, lookup)
                        for right in values(node.right, seen, lookup) if type(left) is type(right)}
            if isinstance(node, ast.Call):
                function = name(node.func)
                if function in {'which', 'encode', 'decode'}:
                    target = node.args[0] if function == 'which' and node.args else getattr(node.func, 'value', None)
                    return values(target, seen, lookup)
                if function == 'Popen':
                    argument = node.args[0] if node.args else next(
                        (item.value for item in node.keywords if item.arg == 'args'), None)
                    return values(argument, seen, lookup)
            return {''}

        def strings(node):
            return {item for item in values(node) if isinstance(item, str)}

        def record(line, form, parts):
            for part in parts:
                for match in revision_token.finditer(part):
                    findings.add((line, match.group().lower(), form))

        def command(node, argument, helper=False, inputs=()):
            for value in values(argument):
                if isinstance(value, tuple):
                    parts = [item for item in value if isinstance(item, str)]
                elif isinstance(value, str):
                    # Tokenize shell commands only at an actual process call.
                    parts = re.findall(r"[^\s'\"]+", value)
                else:
                    continue
                if not helper and not any(Path(part).name.lower() in {'git', 'git.exe'} for part in parts):
                    continue
                for verb in ('show', 'cat-file', 'archive'):
                    if verb not in parts:
                        continue
                    tail = parts[parts.index(verb) + 1:]
                    # Options can contain hexadecimal format text; only operands
                    # and batch requests carry object identities.
                    operands = [part for part in tail if not part.startswith('-')]
                    batch = verb == 'cat-file' and any(part.startswith('--batch') for part in tail)
                    if batch:
                        if isinstance(value, str):
                            operands += parts[:parts.index(verb)]
                        operands += [part for argument in inputs for part in strings(argument)]
                    record(node.lineno, 'git ' + verb + (' --batch' if batch else ''), operands)

        for node in members:
            if not isinstance(node, ast.Call):
                continue
            function = name(node.func)
            inputs = [item.value for item in node.keywords if item.arg in {'input', 'input_data'}]
            if function in git_helpers:
                command(node, ast.Tuple(elts=list(node.args), ctx=ast.Load()), True, inputs)
            # Preserve the original guard's list/tuple detection at any call,
            # as well as resolving command variables at process runners.
            for argument in node.args:
                if isinstance(argument, (ast.List, ast.Tuple)):
                    command(node, argument, inputs=inputs)
            if function in runners:
                arguments = list(node.args) + [item.value for item in node.keywords if item.arg == 'args']
                for argument in arguments:
                    command(node, argument, inputs=inputs)
            if function == 'communicate' and isinstance(node.func, ast.Attribute):
                command(node, node.func.value, inputs=[*node.args, *inputs])
            if function == 'materialize_commit':
                arguments = list(node.args[1:2]) + [item.value for item in node.keywords if item.arg == 'commit']
                for argument in arguments:
                    record(node.lineno, 'materialize_commit -> git cat-file --batch', strings(argument))
            # Follow positional and keyword arguments into lexically visible
            # helpers. Attribute calls cover self.method without executing it.
            for depth, (unused, available) in enumerate(environment):
                target = available.get(function)
                if target is not None:
                    invoke(target, node, environment[depth:], active, values)
                    break

        for node in members:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                invoke(node, None, environment, active, values)
            elif isinstance(node, ast.ClassDef):
                scope(node.body, environment, active=active)

    def invoke(target, call, environment, active, resolve):
        if id(target) in active:
            return
        positional = [*target.args.posonlyargs, *target.args.args]
        parameters = {argument.arg: [''] for argument in [*positional, *target.args.kwonlyargs]}
        defaults = list(zip(positional[len(positional) - len(target.args.defaults):], target.args.defaults))
        defaults += list(zip(target.args.kwonlyargs, target.args.kw_defaults))
        for argument, default in defaults:
            if default is not None:
                parameters[argument.arg] = list(resolve(default, lookup=environment))
        if call is not None:
            arguments = positional
            if isinstance(call.func, ast.Attribute) and arguments and arguments[0].arg in {'self', 'cls'}:
                arguments = arguments[1:]
            for argument, value in zip(arguments, call.args):
                parameters[argument.arg] = list(resolve(value))
            for keyword in call.keywords:
                if keyword.arg in parameters:
                    parameters[keyword.arg] = list(resolve(keyword.value))
        scope(target.body, environment, parameters, (*active, id(target)))

    scope(tree.body)
    return sorted(findings)


def unexpected_pinned_git_reads(path, source):
    return [(line, revision) for line, revision in pinned_git_read_revisions(source)
            if (path, revision) not in LIVE_HISTORY_READERS]


def assert_live_history_readers_observed(sources):
    observed = {(path, revision) for path, source in sources.items()
                for line, revision in pinned_git_read_revisions(source)
                if (path, revision) in LIVE_HISTORY_READERS}
    stale = set(LIVE_HISTORY_READERS) - observed
    assert not stale, 'Stale live-history reader entries: ' + repr(sorted(stale))


def assert_live_history_ancestry():
    def git(*arguments):
        try:
            return subprocess.run(['git', *arguments], cwd=ROOT, capture_output=True,
                                  text=True, encoding='utf-8', timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AssertionError('Git history is required for this source-repository guard') from error

    assert git('rev-parse', '--git-dir').returncode == 0, (
        'Git history is required for this source-repository guard; installed copies exclude scripts/tests')
    # CI feature clones may expose main only as a remote ref, or only HEAD.
    targets = ['HEAD']
    for target in ('refs/heads/main', 'refs/remotes/origin/main'):
        if git('rev-parse', '--verify', target + '^{commit}').returncode == 0:
            targets.append(target)
    for revision in sorted({revision for path, revision in LIVE_HISTORY_READERS}):
        assert git('cat-file', '-e', revision + '^{commit}').returncode == 0, (
            'Missing approved live-history commit ' + revision + '; CI/source clones must retain full history')
        for target in targets:
            assert git('merge-base', '--is-ancestor', revision, target).returncode == 0, (
                'Approved live-history commit ' + revision + ' must be an ancestor of ' + target)


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
    def test_live_history_allowlist_stale_entry_fails_for_synthetic_sources(self):
        sources = {path: "run(['git', 'archive', '" + revision + "'])"
                   for path, revision in LIVE_HISTORY_READERS}
        assert_live_history_readers_observed(sources)
        for path, revision in LIVE_HISTORY_READERS:
            with self.subTest(path=path):
                changed = {**sources, path: "run(['git', 'archive', 'HEAD'])"}
                with self.assertRaisesRegex(AssertionError, 'Stale live-history reader'):
                    assert_live_history_readers_observed(changed)

    def test_live_history_revisions_are_ancestors_of_main_and_head(self):
        assert_live_history_ancestry()

    def test_missing_live_history_commit_fails_never_skips(self):
        def missing_commit(arguments, **kwargs):
            code = 1 if 'cat-file' in arguments else 0
            return subprocess.CompletedProcess(arguments, code, '', '')

        with patch('subprocess.run', side_effect=missing_commit):
            with self.assertRaisesRegex(AssertionError, 'Missing approved live-history commit.*full history'):
                assert_live_history_ancestry()

    def test_unavailable_git_history_fails_never_skips(self):
        with patch('subprocess.run', side_effect=FileNotFoundError('git')):
            with self.assertRaisesRegex(AssertionError, 'Git history is required'):
                assert_live_history_ancestry()

    def test_nonancestor_live_history_commit_fails_never_skips(self):
        def nonancestor(arguments, **kwargs):
            code = 1 if 'merge-base' in arguments else 0
            return subprocess.CompletedProcess(arguments, code, '', '')

        with patch('subprocess.run', side_effect=nonancestor):
            with self.assertRaisesRegex(AssertionError, 'must be an ancestor of HEAD'):
                assert_live_history_ancestry()

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

    def test_guard_detects_wider_pinned_read_forms(self):
        cases = {
            'cat_file_pretty': (
                "subprocess.check_output(['git', 'cat-file', '-p', 'abcd1234:file'])"
            ),
            'cat_file_blob': (
                "subprocess.run(['git', 'cat-file', 'blob', 'abcd1234:file'])"
            ),
            'cat_file_batch': (
                "subprocess.run(['git', 'cat-file', '--batch'], input=b'abcd1234:file\\n')"
            ),
            'cat_file_batch_format': (
                "subprocess.run(['git', 'cat-file', '--batch=%(objectname)'], input='abcd1234:file\\n')"
            ),
            'cat_file_batch_command': (
                "subprocess.run(['git', 'cat-file', '--batch-command'], input='contents abcd1234:file\\n')"
            ),
            'cat_file_batch_communicate': (
                "proc = subprocess.Popen(['git', 'cat-file', '--batch'], stdin=PIPE)\n"
                "proc.communicate(b'abcd1234:file\\n')"
            ),
            'archive_literal': (
                "subprocess.run(['git', 'archive', 'abcd1234', '.agentic'])"
            ),
            'archive_shell': (
                "subprocess.check_output('git archive abcd1234 .agentic')"
            ),
            'show_parameter': (
                'def historical(revision):\n'
                "    return git('show', revision + ':file')\n"
                "historical('abcd1234')"
            ),
            'cat_file_parameter': (
                'def historical(revision):\n'
                "    return git('cat-file', '-p', revision + ':file')\n"
                "historical(revision='abcd1234')"
            ),
            'archive_parameter': (
                'def historical(revision):\n'
                "    return git('archive', revision)\n"
                "historical('abcd1234')"
            ),
            'show_default': (
                "REV = 'abcd1234'\n"
                'def historical(revision=REV):\n'
                "    return git('show', f'{revision}:file')"
            ),
            'cat_file_default': (
                "def historical(*, revision='abcd1234'):\n"
                "    return git('cat-file', 'blob', f'{revision}:file')"
            ),
            'archive_default': (
                "def historical(revision='abcd1234'):\n"
                "    return git('archive', revision)"
            ),
            'local_alias': (
                'def historical():\n'
                "    revision = 'abcd1234'\n"
                '    alias = revision\n'
                "    return subprocess.run(['git', 'show', f'{alias}:file'])"
            ),
            'constant_fstring': (
                "REV: str = 'abcd1234'\n"
                "OBJECT = f'{REV}:file'\n"
                "subprocess.run(['git', 'show', OBJECT])"
            ),
            'command_variable_keyword': (
                "revision = 'abcd1234'\n"
                "command = ['git', 'show', revision + ':file']\n"
                'subprocess.run(args=command)'
            ),
            'which_git': (
                "subprocess.run([shutil.which('git'), 'archive', 'abcd1234'])"
            ),
            'starred_command': (
                "GIT = ['git', '-C', '.']\n"
                "subprocess.run([*GIT, 'archive', 'abcd1234'])"
            ),
            'parameter_chain': (
                'def read(revision):\n'
                "    return git('show', revision + ':file')\n"
                'def historical(revision):\n'
                '    return read(revision)\n'
                "historical('abcd1234')"
            ),
            'materialize_commit': (
                "REV = 'abcd1234'\n"
                'publish_release.materialize_commit(repository, REV, destination)'
            ),
            'batch_shell_pipe': (
                "subprocess.check_output('echo abcd1234:file | git cat-file --batch')"
            ),
            'batch_encoded_input': (
                "REV = 'abcd1234'\n"
                "subprocess.run(['git', 'cat-file', '--batch'], input=f'{REV}:file\\n'.encode())"
            ),
            'cat_file_local_alias': (
                'def historical():\n'
                "    revision = 'abcd1234'\n"
                '    alias = revision\n'
                "    return subprocess.run(['git', 'cat-file', '-p', f'{alias}:file'])"
            ),
            'archive_local_alias': (
                'def historical():\n'
                "    revision = 'abcd1234'\n"
                '    alias = revision\n'
                "    return subprocess.run(['git', 'archive', alias])"
            ),
            'cat_file_constant_fstring': (
                "REV: str = 'abcd1234'\n"
                "OBJECT = f'{REV}:file'\n"
                "subprocess.run(['git', 'cat-file', 'blob', OBJECT])"
            ),
            'archive_constant_fstring': (
                "REV: str = 'abcd1234'\n"
                "OBJECT = f'{REV}'\n"
                "subprocess.run(['git', 'archive', OBJECT])"
            ),
            'method_parameter': (
                'class Reader:\n'
                '    def historical(self, revision):\n'
                "        return git('show', revision + ':file')\n"
                '    def test_read(self):\n'
                "        self.historical('abcd1234')"
            ),
            'assigned_parameter': (
                'def historical(revision):\n'
                "    revision = 'abcd1234'\n"
                "    return git('archive', revision)"
            ),
            'lexical_constant_alias': (
                "REV = 'abcd1234'\n"
                "OBJECT = f'{REV}:file'\n"
                'def historical():\n'
                "    REV = 'HEAD'\n"
                "    return git('show', OBJECT)"
            ),
        }
        for form, source in cases.items():
            with self.subTest(form=form):
                self.assertEqual(['abcd1234'], sorted({revision for line, revision in
                    pinned_git_read_revisions(source)}))

    def test_tests_do_not_read_pinned_revisions_from_git(self):
        findings = []
        sources = {}
        for folder in (ROOT / '.agentic/tests', ROOT / 'scripts/tests'):
            for path in sorted(folder.rglob('*.py')):
                relative = path.relative_to(ROOT).as_posix()
                source = path.read_text(encoding='utf-8')
                sources[relative] = source
                findings.extend(f'{relative}:{line}'
                                for line, revision in unexpected_pinned_git_reads(relative, source))
        assert_live_history_readers_observed(sources)
        self.assertEqual([], findings, 'New pinned Git reads require fixtures; only documented owner-approved identities are allowed')

    def test_live_history_allowlist_rejects_other_paths_and_revisions(self):
        for path, revision in LIVE_HISTORY_READERS:
            source = "run(['git', 'archive', '" + revision + "'])"
            with self.subTest(path=path, revision=revision):
                self.assertEqual([], unexpected_pinned_git_reads(path, source))
                self.assertEqual([(1, revision)], unexpected_pinned_git_reads('other.py', source))
                changed = source.replace(revision, 'a' * 40)
                self.assertEqual([(1, 'a' * 40)], unexpected_pinned_git_reads(path, changed))

    def test_guard_ignores_unexecuted_source_and_current_revisions_in_new_forms(self):
        for verb in ('show', 'cat-file', 'archive'):
            for revision in ('HEAD', 'main', 'v1.9.5'):
                source = "subprocess.run(['git', '" + verb + "', '" + revision + "'])"
                self.assertEqual([], pinned_git_read_revisions(source))
            source = "source = " + repr("subprocess.run(['git', '" + verb + "', 'abcd1234:file'])")
            self.assertEqual([], pinned_git_read_revisions(source))
        self.assertEqual([], pinned_git_read_revisions("run(['echo', 'git show abcd1234:file'])"))
        self.assertEqual([], pinned_git_read_revisions("run(['echo', 'abcd1234:file'])"))

    def test_guard_retains_alternative_bindings_and_stops_cycles(self):
        source = ("REV = 'abcd1234'\nREV = unknown\n"
                  "alias = REV\nREV = alias\nrun(['git', 'show', REV + ':file'])")
        self.assertIn((5, 'abcd1234'), pinned_git_read_revisions(source))
        self.assertEqual([(1, 'abcd1234')], pinned_git_read_revisions(
            "capture(['git', 'show', 'abcd1234:file'])"))

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

    def test_receipt_reader_is_detected_in_real_tree_and_tolerated(self):
        path = '.agentic/tests/test_upgrade_receipts.py'
        revision = '1f5f146352ff0a8222a6be1ef9186e4609b11033'
        source = (ROOT / path).read_text(encoding='utf-8')
        self.assertEqual({(path, revision)}, {identity for identity in LIVE_HISTORY_READERS
                                          if identity[0] == path})
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
