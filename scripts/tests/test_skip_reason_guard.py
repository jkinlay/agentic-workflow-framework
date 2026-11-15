"""Keep exception and subprocess output (and local paths) out of skip records."""
import ast
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_windows_unittests import SUITES, inventory
from agentic.installer import release_member


SKIPS = {'skip', 'skipIf', 'skipUnless', 'skipTest', 'SkipTest'}
EXCEPTION, OUTPUT, RESULT = 1, 2, 4
ASSERT_CONTEXT = 8
ASSERTIONS = {'assertRaises', 'assertRaisesRegex', 'assertWarns', 'assertWarnsRegex'}


class SkipReasonVisitor(ast.NodeVisitor):
    """Intraprocedural exception/output taint, including captured tracebacks.

    Follow simple callable aliases and local assignments, including reason
    variables surviving a handler, and union branches. Nested scopes inherit
    captures but shadow local bindings. Assertion contexts carry an identity bit;
    only their .exception attribute introduces taint, not the context itself.
    This does not infer helper return values or mutations of container contents.
    """

    def __init__(self):
        self.bindings = {}
        self.findings = []
        self.names = {}

    def tainted(self, node):
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id, False)
        if isinstance(node, ast.Attribute):
            origin = self.tainted(node.value)
            if origin & ASSERT_CONTEXT:
                return ((origin & ~ASSERT_CONTEXT)
                        | (EXCEPTION if node.attr == 'exception' else 0))
            if origin & RESULT:
                if node.attr in {'stdout', 'stderr', 'output'}:
                    return (origin & ~RESULT) | OUTPUT
                if node.attr in {'returncode', 'args'}:
                    return origin & ~RESULT
            if node.attr in {'errno', 'winerror'} and isinstance(node.value, ast.Name):
                return False
            if (node.attr == '__name__' and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == 'type'
                    and len(node.value.args) == 1 and not node.value.keywords):
                return False
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'getattr' and not node.keywords
                and len(node.args) in {2, 3}
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in {'errno', 'winerror'}
                and (len(node.args) == 2 or isinstance(node.args[2], ast.Constant)
                     and (node.args[2].value is None or type(node.args[2].value) is int))):
            return False
        if isinstance(node, ast.Call):
            names = self.call_names(node.func)
            if names & {'sys.exc_info', 'traceback.format_exc',
                        'traceback.format_exception', 'traceback.format_exception_only'}:
                return EXCEPTION
            if names & {'subprocess.run', 'subprocess.Popen'}:
                return RESULT
            if names & {'subprocess.check_output', 'subprocess.getoutput',
                        'subprocess.getstatusoutput'}:
                return OUTPUT
            if (isinstance(node.func, ast.Attribute) and node.func.attr == 'communicate'
                    and self.tainted(node.func.value) & RESULT):
                return OUTPUT
        result = 0
        for child in ast.iter_child_nodes(node):
            result |= self.tainted(child)
        return result

    def visit_Import(self, node):
        for item in node.names:
            self.names[item.asname or item.name] = {item.name}

    def visit_ImportFrom(self, node):
        for item in node.names:
            self.names[item.asname or item.name] = {(node.module or '') + '.' + item.name}

    def call_names(self, node):
        if isinstance(node, ast.Name):
            return self.names.get(node.id, {node.id})
        if isinstance(node, ast.Attribute):
            # An instance parameter need not have a known qualified name.
            if node.attr == 'skipTest':
                return {'skipTest'}
            return {name + '.' + node.attr for name in self.call_names(node.value)}
        return set()

    def visit_Call(self, node):
        for name in self.call_names(node.func):
            short = name.rsplit('.', 1)[-1]
            if not (short in SKIPS and (short == 'skipTest' or name.startswith('unittest.')
                                       or name == 'pytest.skip')):
                continue
            position = 1 if short in {'skipIf', 'skipUnless'} else 0
            reason = (node.args[position] if len(node.args) > position else
                      next((item.value for item in node.keywords
                            if item.arg in {'reason', 'msg'}), None))
            if reason is not None and self.tainted(reason) & ~ASSERT_CONTEXT:
                self.findings.append(node.lineno)
        self.generic_visit(node)

    def bind(self, target, tainted, names=frozenset()):
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self.bindings[node.id] = tainted
                self.names[node.id] = set(names)

    def visit_Assign(self, node):
        self.visit(node.value)
        taint, names = self.tainted(node.value), self.call_names(node.value)
        for target in node.targets:
            self.bind(target, taint, names if isinstance(target, ast.Name) else ())

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, self.tainted(node.value), self.call_names(node.value)
                      if isinstance(node.target, ast.Name) else ())

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                expression = item.context_expr
                assertion = (isinstance(expression, ast.Call)
                             and isinstance(expression.func, ast.Attribute)
                             and expression.func.attr in ASSERTIONS)
                self.bind(item.optional_vars, ASSERT_CONTEXT if assertion
                          else self.tainted(expression))
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_AugAssign(self, node):
        self.visit(node.value)
        self.bind(node.target, self.tainted(node.target) | self.tainted(node.value))

    def visit_NamedExpr(self, node):
        self.visit_Assign(ast.Assign(targets=[node.target], value=node.value))

    def visit_ExceptHandler(self, node):
        previous = self.bindings.get(node.name, False)
        if node.name:
            self.bindings[node.name] = EXCEPTION
        for statement in node.body:
            self.visit(statement)
        if node.name:
            self.bindings[node.name] = previous

    def branches(self, alternatives):
        before = self.bindings.copy()
        before_names = self.names.copy()
        outcomes = [before]
        name_outcomes = [before_names]
        for statements in alternatives:
            self.bindings = before.copy()
            self.names = before_names.copy()
            for statement in statements:
                self.visit(statement)
            outcomes.append(self.bindings)
            name_outcomes.append(self.names)
        self.bindings = {}
        for outcome in outcomes:
            for name, taint in outcome.items():
                self.bindings[name] = self.bindings.get(name, 0) | taint
        self.names = {}
        for outcome in name_outcomes:
            for name, values in outcome.items():
                self.names.setdefault(name, set()).update(values)

    def visit_If(self, node):
        self.visit(node.test)
        self.branches([node.body, node.orelse])

    def visit_Try(self, node):
        self.branches([node.body + node.orelse] + [[handler] for handler in node.handlers])
        for statement in node.finalbody:
            self.visit(statement)

    visit_TryStar = visit_Try

    def visit_For(self, node):
        self.visit(node.iter)
        self.bind(node.target, self.tainted(node.iter))
        self.branches([node.body, node.orelse])

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self.visit(node.test)
        self.branches([node.body, node.orelse])

    def visit_FunctionDef(self, node):
        for expression in node.decorator_list + node.args.defaults + [
                value for value in node.args.kw_defaults if value is not None]:
            self.visit(expression)
        outer = self.bindings.copy()
        outer_names = self.names.copy()
        # Collect this scope's locals without descending into nested scopes.
        def locals_in(current):
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield current.name
                return
            if isinstance(current, ast.Name) and isinstance(current.ctx, ast.Store):
                yield current.id
            if isinstance(current, ast.ExceptHandler) and current.name:
                yield current.name
            if isinstance(current, (ast.Import, ast.ImportFrom)):
                for item in current.names:
                    yield item.asname or item.name.split('.')[0]
            for child in ast.iter_child_nodes(current):
                yield from locals_in(child)
        for statement in node.body:
            for name in locals_in(statement):
                self.bindings[name] = False
                self.names[name] = set()
        for argument in ast.walk(node.args):
            if isinstance(argument, ast.arg):
                self.bindings[argument.arg] = False
                self.names[argument.arg] = set()
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer
        self.names = outer_names

    visit_AsyncFunctionDef = visit_FunctionDef


def exception_skip_lines(source):
    visitor = SkipReasonVisitor()
    visitor.visit(ast.parse(source))
    return sorted(set(visitor.findings))


def suite_python_files(root):
    """Walk shipped Python in CI roots, including helpers and the installed harness.

    Prune release exclusions and bytecode caches before descending. Never follow
    symlinks (including Windows junctions), even when they name a Python file.
    """
    root = Path(root)
    def walk_error(error):
        raise error
    def included(path):
        return (path.name != '__pycache__' and not path.is_symlink()
                and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400
                and release_member(path.relative_to(root).as_posix())
                and release_member(path.relative_to(root).as_posix() + '/'))
    paths = []
    for suite in SUITES:
        start = root / suite
        if not start.is_dir():
            raise ValueError(f'missing suite root: {suite}')
        parts = Path(suite).parts
        if not all(included(root.joinpath(*parts[:end]))
                   for end in range(1, len(parts) + 1)):
            continue
        for directory, directories, names in os.walk(start, onerror=walk_error,
                                                    followlinks=False):
            parent = Path(directory)
            directories[:] = [name for name in directories if included(parent / name)]
            paths.extend((parent / name).relative_to(root).as_posix()
                         for name in names if name.endswith('.py') and included(parent / name))
    return sorted(paths)


class SkipReasonGuardTests(unittest.TestCase):
    def test_exc_info_taints_derived_messages_and_bindings(self):
        messages = ['info', 'info[1]', 'info[2].tb_frame', 'str(info)',
                    'repr(info[1])', 'format(info)', 'f"{info[1]!r}"',
                    '"failure: " + str(info[1])', '"{}".format(info[1])']
        for producer in ['sys.exc_info', 's.exc_info', 'current_exception']:
            for message in messages:
                with self.subTest(producer=producer, message=message):
                    source = ('import sys\nimport sys as s\n'
                              'from sys import exc_info as current_exception\n'
                              f'info = {producer}()\nalias = {message}\n'
                              'self.skipTest(alias)')
                    self.assertEqual([6], exception_skip_lines(source))
        self.assertEqual([4], exception_skip_lines(
            'import sys\nkind, error, trace = sys.exc_info()\n'
            'reason = repr(error)\nself.skipTest(reason)'))

    def test_exc_info_safe_metadata_literals_and_shadowing(self):
        for message in ['"unavailable"', 'type(info[1]).__name__', 'error.errno',
                        'error.winerror', 'getattr(error, "errno", None)']:
            with self.subTest(message=message):
                self.assertEqual([], exception_skip_lines(
                    'import sys\ninfo = sys.exc_info()\n'
                    'kind, error, trace = info\n' + f'self.skipTest({message})'))
        self.assertEqual([], exception_skip_lines(
            'import sys\ninfo = sys.exc_info()\ndef inner(info):\n'
            '    self.skipTest(str(info))\n'))
        self.assertEqual([], exception_skip_lines(
            'import sys\ndef inner(sys):\n    self.skipTest(sys.exc_info())'))

    def test_traceback_formatters_taint_messages_with_import_aliases(self):
        for formatter in ['format_exc', 'format_exception', 'format_exception_only']:
            for producer in [f'traceback.{formatter}', f'tb.{formatter}', 'formatted']:
                with self.subTest(formatter=formatter, producer=producer):
                    source = ('import traceback\nimport traceback as tb\n'
                              f'from traceback import {formatter} as formatted\n'
                              f'reason = {producer}()\nalias = "".join(reason)\n'
                              'self.skipTest(f"failed: {alias}")')
                    self.assertEqual([6], exception_skip_lines(source))
        self.assertEqual([2], exception_skip_lines(
            'from traceback import format_exc\nself.skipTest(format_exc())'))

    def test_traceback_unrelated_calls_and_literal_reasons_are_safe(self):
        self.assertEqual([], exception_skip_lines(
            'import traceback\nreason = traceback.format_exc()\n'
            'self.skipTest("unavailable")\n'))
        self.assertEqual([], exception_skip_lines(
            'def format_exc():\n    return "literal"\n'
            'self.skipTest(format_exc())\n'))
        self.assertEqual([], exception_skip_lines(
            'from traceback import format_exc as formatted\n'
            'def inner(formatted):\n    self.skipTest(formatted())\n'))
        self.assertEqual([], exception_skip_lines(
            'import traceback\ndef inner(traceback):\n'
            '    self.skipTest(traceback.format_exc())'))

    def test_assertion_context_exception_taints_derived_messages(self):
        for assertion in sorted(ASSERTIONS):
            for receiver in ['self', 'unittest.TestCase', 'u.TestCase', 'Case']:
                for message in ['cm.exception', 'str(cm.exception)',
                                'repr(cm.exception)', 'format(cm.exception)',
                                'f"{cm.exception}"', '"failed: " + str(cm.exception)',
                                'cm.exception.filename']:
                    with self.subTest(assertion=assertion, receiver=receiver, message=message):
                        source = ('import unittest\nimport unittest as u\n'
                                  'from unittest import TestCase as Case\n'
                                  f'with {receiver}.{assertion}(OSError) as cm:\n    pass\n'
                                  f'error = {message}\nself.skipTest(error)')
                        self.assertEqual([7], exception_skip_lines(source))
        self.assertEqual([5], exception_skip_lines(
            'with self.assertRaises(OSError) as cm:\n    pass\n'
            'alias = cm\nreason = str(alias.exception)\nself.skipTest(reason)'))

    def test_assertion_context_itself_other_attributes_and_metadata_stay_safe(self):
        for assertion in sorted(ASSERTIONS):
            for message in ['cm', 'str(cm)', 'cm.expected', 'cm.warning',
                            'type(cm.exception).__name__', 'error.errno', 'error.winerror',
                            '"unavailable"']:
                with self.subTest(assertion=assertion, message=message):
                    self.assertEqual([], exception_skip_lines(
                        f'with self.{assertion}(OSError) as cm:\n    pass\n'
                        'error = cm.exception\n' + f'self.skipTest({message})'))
        self.assertEqual([], exception_skip_lines(
            'with another_context() as cm:\n    pass\nself.skipTest(cm.exception)'))

    def test_skip_callable_simple_aliases_and_decorators(self):
        for binding in ['s = self.skipTest', 'skip_test = self.skipTest\ns = skip_test',
                        's: object = self.skipTest', 's = unittest.skip',
                        's = u.skip', 'from unittest import skip as s',
                        's = unittest.SkipTest']:
            with self.subTest(binding=binding):
                source = ('import unittest\nimport unittest as u\n'
                          'from sys import exc_info\n' + binding + '\ns(exc_info())')
                self.assertEqual([len(source.splitlines())], exception_skip_lines(source))
        self.assertEqual([5], exception_skip_lines(
            'import unittest\nimport sys\nskip = unittest.skip\n'
            'reason = str(sys.exc_info())\n@skip(reason)\ndef example():\n    pass'))
        self.assertEqual([6], exception_skip_lines(
            'import sys\ndef example(self):\n    s = self.skipTest\n'
            '    if flag:\n        s = another_callable\n    s(sys.exc_info())'))
        self.assertEqual([5, 6], exception_skip_lines(
            'import unittest\nimport sys\nskip = unittest.skipIf\n'
            'unless = unittest.skipUnless\nskip(True, sys.exc_info())\n'
            'unless(False, reason=sys.exc_info())'))

    def test_skip_callable_safe_reasons_rebinding_and_scope_shadowing(self):
        self.assertEqual([], exception_skip_lines(
            'import unittest\nimport sys\ns = self.skipTest\ns("unavailable")\n'
            'skip = unittest.skip\n@skip("literal")\ndef example():\n    pass\n'
            's = print\ns(sys.exc_info())'))
        self.assertEqual([], exception_skip_lines(
            'import unittest\nimport sys\nfrom unittest import skip as sk\n'
            's = self.skipTest\ndef inner(s, sk):\n    s(sys.exc_info())\n'
            '    sk(sys.exc_info())\ndef other():\n    s = print\n    s(sys.exc_info())'))

    def test_suite_walk_scans_non_test_helpers_and_installed_harness(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for suite in SUITES:
                (root / suite).mkdir(parents=True)
            helpers = ['.agentic/tests/source_only.py',
                       '.agentic/tests/fixtures/helper.py',
                       'global/awf/tests/helper.py', 'global/awf-portable/tests/helper.py',
                       'scripts/tests/helper.py', 'scripts/tests/test_installed_self_test.py']
            for relative in helpers:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('import sys\nself.skipTest(str(sys.exc_info()))\n',
                                encoding='utf-8')
            walked = suite_python_files(root)
            self.assertEqual(sorted(helpers), walked)
            self.assertEqual({relative: [2] for relative in helpers},
                             {relative: exception_skip_lines(
                                 (root / relative).read_text(encoding='utf-8'))
                              for relative in walked})

    def test_suite_walk_excludes_cache_unshipped_and_non_python_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for suite in SUITES:
                (root / suite).mkdir(parents=True)
            excluded = ['.agentic/tests/__pycache__/helper.py',
                        '.agentic/tests/fixtures/upgrades/helper.py',
                        'scripts/tests/helper.txt', 'outside/helper.py']
            for relative in excluded:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('import sys\nself.skipTest(sys.exc_info())\n', encoding='utf-8')
            self.assertEqual([], suite_python_files(root))

    def test_shipped_helpers_and_tests_do_not_include_exception_text(self):
        findings = [f'{relative}:{line}' for relative in suite_python_files(ROOT)
                    for line in exception_skip_lines((ROOT / relative).read_text(encoding='utf-8'))]
        self.assertFalse(findings, 'Exception text in skip reasons:\n' + '\n'.join(findings))

    def test_subprocess_output_message_expressions_and_import_aliases(self):
        messages = ['result.stderr.strip()',
                    'result.stderr.strip() or result.stdout.strip()',
                    '"unavailable: " + result.stderr.strip()',
                    'result.stderr.decode("utf-8", "replace").strip()',
                    'result.stdout', 'result.output', 'str(result.stderr)',
                    'repr(result.stdout)', 'f"failed: {result.stderr}"',
                    '"failed: %s" % result.stdout',
                    '"failed: {}".format(result.stderr)',
                    'result.stdout if flag else "literal"', 'f"{result}"']
        for producer in ['subprocess.run', 'sp.run', 'run_alias', 'sp.Popen']:
            for message in messages:
                with self.subTest(producer=producer, message=message):
                    source = ('import subprocess\nimport subprocess as sp\n'
                              'from subprocess import run as run_alias\n'
                              'def example():\n'
                              f'    result = {producer}(["command"], capture_output=True)\n'
                              f'    self.skipTest({message})\n')
                    self.assertEqual([6], exception_skip_lines(source))

    def test_subprocess_output_bindings_and_communicate(self):
        sources = [
            'output = sp.check_output(["command"])\nself.skipTest(output)',
            'output = output_alias("command")\nself.skipTest(output.strip())',
            'status, output = sp.getstatusoutput("command")\nself.skipTest(output)',
            'output, error = sp.Popen(["command"]).communicate()\nself.skipTest(error)',
            'with sp.Popen(["command"]) as process:\n'
            '    output, error = process.communicate()\n    self.skipTest(output.decode())',
            'result = sp.run(["command"])\nalias = result\n'
            'reason = str(alias.stderr)\nself.skipTest(reason)',
        ]
        for source in sources:
            with self.subTest(source=source):
                source = ('import subprocess as sp\n'
                          'from subprocess import getoutput as output_alias\n' + source)
                self.assertEqual([len(source.splitlines())], exception_skip_lines(source))

    def test_subprocess_safe_metadata_literals_branches_and_scope_shadowing(self):
        for message in ['f"returncode={result.returncode}"', 'result.args[0]',
                        '"unavailable"']:
            with self.subTest(message=message):
                self.assertEqual([], exception_skip_lines(
                    'import subprocess\nresult = subprocess.run(["command"])\n'
                    f'self.skipTest({message})'))
        self.assertEqual([9], exception_skip_lines(
            'import subprocess\ndef example():\n    reason = "literal"\n'
            '    if flag:\n        result = subprocess.run(["command"])\n'
            '        reason = result.stderr\n    else:\n        reason = "literal"\n'
            '    self.skipTest(reason)\n'))
        self.assertEqual([7], exception_skip_lines(
            'import subprocess\ndef example():\n    if flag:\n'
            '        result = subprocess.run(["command"])\n    else:\n'
            '        result = "literal"\n    self.skipTest(f"{result}")\n'))
        self.assertEqual([], exception_skip_lines(
            'import subprocess\nresult = subprocess.run(["command"])\n'
            'def inner(result):\n    self.skipTest(str(result))\n'
            'def other():\n    result = "literal"\n    self.skipTest(result)\n'))
        self.assertEqual([4], exception_skip_lines(
            'import subprocess\nresult = subprocess.run(["command"])\n'
            'def capture():\n    self.skipTest(result.stdout)\n'))

    def test_shipped_skip_reasons_do_not_include_exception_text(self):
        findings = []
        # Same inventory as CI, including its installed-harness exclusion: the
        # guard scans that file too, even though the ordinary shards omit it.
        for paths in inventory(ROOT).values():
            for relative in paths:
                for line in exception_skip_lines((ROOT / relative).read_text(encoding='utf-8')):
                    findings.append(f'{relative}:{line}')
        self.assertFalse(findings, 'Exception text in skip reasons:\n' + '\n'.join(sorted(findings)))

    def test_exception_bindings_and_message_expressions(self):
        messages = ['odd', 'str(odd)', 'repr(odd)', 'format(odd)', 'f"{odd!r}"',
                    '"failed: " + str(odd)', '"failed: %s" % odd',
                    '"failed: {}".format(odd)', 'odd.filename',
                    'getattr(odd, "filename", None)',
                    'getattr(odd, "errno", str(odd))']
        calls = ['self.skipTest({})', 'unittest.skip({})',
                 'unittest.skipIf(True, {})', 'unittest.skipUnless(False, {})',
                 'u.skip(reason={})', 'skip_alias({})', 'unittest.SkipTest({})']
        for message in messages:
            for call in calls:
                with self.subTest(message=message, call=call):
                    source = ('import unittest\nimport unittest as u\n'
                              'from unittest import skip as skip_alias\n'
                              'try:\n    pass\nexcept OSError as odd:\n'
                              '    ' + call.format(message) + '\n')
                    self.assertEqual([7], exception_skip_lines(source))

    def test_safe_metadata_and_unbound_exception_like_names(self):
        for message in ['"unavailable"', 'type(odd).__name__', 'odd.errno', 'odd.winerror',
                        'getattr(odd, "winerror", None)',
                        'f"{type(odd).__name__} errno={getattr(odd, \'errno\', None)}"']:
            with self.subTest(message=message):
                self.assertEqual([], exception_skip_lines(
                    'try:\n    pass\nexcept OSError as odd:\n    self.skipTest(' + message + ')'))
        self.assertEqual([], exception_skip_lines('error = "literal"\nself.skipTest(error)'))

    def test_reason_aliases_branches_decorators_and_scope_shadowing(self):
        self.assertEqual([8, 9], exception_skip_lines(
            'import unittest\ntry:\n    pass\nexcept OSError as unusual:\n'
            '    alias = unusual\n    if flag:\n        reason = str(alias)\n'
            '    self.skipTest(reason)\n'
            '@unittest.skip(reason)\ndef example():\n    pass\n'))
        self.assertEqual([], exception_skip_lines(
            'try:\n    pass\nexcept OSError as error:\n'
            '    def inner(error):\n        self.skipTest(str(error))\n'
            '    def other():\n        error = "literal"\n        self.skipTest(error)\n'))


if __name__ == '__main__':
    unittest.main()
