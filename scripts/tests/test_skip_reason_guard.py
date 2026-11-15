"""Keep exception and subprocess output (and local paths) out of skip records."""
import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_windows_unittests import inventory


SKIPS = {'skip', 'skipIf', 'skipUnless', 'skipTest', 'SkipTest'}
EXCEPTION, OUTPUT, RESULT = 1, 2, 4


class SkipReasonVisitor(ast.NodeVisitor):
    """Intraprocedural exception/output taint, seeded by handlers/subprocesses.

    Follow local assignments, including reason variables surviving a handler,
    and union branches. Nested scopes inherit captures but shadow local bindings.
    This does not infer helper return values or mutations of container contents.
    """

    def __init__(self):
        self.bindings = {}
        self.findings = []
        self.imports = {}

    def tainted(self, node):
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id, False)
        if isinstance(node, ast.Attribute):
            origin = self.tainted(node.value)
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
            name = self.call_name(node.func)
            if name in {'subprocess.run', 'subprocess.Popen'}:
                return RESULT
            if name in {'subprocess.check_output', 'subprocess.getoutput',
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
            self.imports[item.asname or item.name] = item.name

    def visit_ImportFrom(self, node):
        for item in node.names:
            self.imports[item.asname or item.name] = (node.module or '') + '.' + item.name

    def call_name(self, node):
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return self.call_name(node.value) + '.' + node.attr
        return ''

    def visit_Call(self, node):
        name = self.call_name(node.func)
        short = name.rsplit('.', 1)[-1]
        if short in SKIPS and (short == 'skipTest' or name.startswith('unittest.')
                               or name == 'pytest.skip'):
            position = 1 if short in {'skipIf', 'skipUnless'} else 0
            reason = (node.args[position] if len(node.args) > position else
                      next((item.value for item in node.keywords
                            if item.arg in {'reason', 'msg'}), None))
            if reason is not None and self.tainted(reason):
                self.findings.append(node.lineno)
        self.generic_visit(node)

    def bind(self, target, tainted):
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self.bindings[node.id] = tainted

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            self.bind(target, self.tainted(node.value))

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, self.tainted(node.value))

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.bind(item.optional_vars, self.tainted(item.context_expr))
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
        outcomes = [before]
        for statements in alternatives:
            self.bindings = before.copy()
            for statement in statements:
                self.visit(statement)
            outcomes.append(self.bindings)
        self.bindings = {}
        for outcome in outcomes:
            for name, taint in outcome.items():
                self.bindings[name] = self.bindings.get(name, 0) | taint

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
        # Collect this scope's locals without descending into nested scopes.
        def locals_in(current):
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield current.name
                return
            if isinstance(current, ast.Name) and isinstance(current.ctx, ast.Store):
                yield current.id
            if isinstance(current, ast.ExceptHandler) and current.name:
                yield current.name
            for child in ast.iter_child_nodes(current):
                yield from locals_in(child)
        for statement in node.body:
            for name in locals_in(statement):
                self.bindings[name] = False
        for argument in ast.walk(node.args):
            if isinstance(argument, ast.arg):
                self.bindings[argument.arg] = False
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer

    visit_AsyncFunctionDef = visit_FunctionDef


def exception_skip_lines(source):
    visitor = SkipReasonVisitor()
    visitor.visit(ast.parse(source))
    return sorted(set(visitor.findings))


class SkipReasonGuardTests(unittest.TestCase):
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
