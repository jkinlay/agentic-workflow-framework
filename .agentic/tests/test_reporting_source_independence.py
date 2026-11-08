"""Installed tests must build Git fixtures or explicitly require source history."""
import ast
from pathlib import Path
import re
import unittest


def checkout_dependencies(source):
    """Find checkout clone/blob reads reachable without a source-only gate.

    Inspect command arguments, rather than comments or string matches. Follow
    local helper calls so a history reader is safe only when all its callers
    are gated. Disposable repositories do not refer to the checkout root.
    """
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    roots = {'ROOT', 'repository_root', '__file__'}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if '__file__' in ast.unparse(node.value) and 'parents' in ast.unparse(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                roots.update(target.id for target in targets if isinstance(target, ast.Name))

    def owner(node):
        while node in parents:
            node = parents[node]
            if node in functions:
                return node
        return None

    def name(node):
        return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''

    def gated(node):
        while node is not None:
            for decorator in getattr(node, 'decorator_list', ()):
                if isinstance(decorator, ast.Call) and name(decorator.func) == 'skip_unless_source_repo':
                    return True
            node = parents.get(node)
        return False

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    callers = {function: [] for function in functions}
    for call in calls:
        for function in functions:
            if name(call.func) == function.name:
                callers[function].append(owner(call))

    def protected(function, seen=frozenset()):
        if function is None or function in seen:
            return False
        if gated(function):
            return True
        incoming = callers[function]
        return bool(incoming) and all(protected(caller, seen | {function}) for caller in incoming)

    def checkout(node):
        return any((isinstance(part, ast.Name) and part.id in roots)
                   or (isinstance(part, ast.Attribute) and part.attr in roots)
                   or (isinstance(part, ast.Constant) and part.value == 'repository_root')
                   for part in ast.walk(node))

    violations = []
    for call in calls:
        # subprocess argv and the local git/command wrappers used by fixtures.
        argv = call.args[0].elts if call.args and isinstance(call.args[0], (ast.List, ast.Tuple)) else call.args
        literals = [part.value for part in argv if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        shell = ''
        if call.args and isinstance(call.args[0], (ast.Constant, ast.JoinedStr)):
            shell = ' '.join(part.value for part in ast.walk(call.args[0])
                             if isinstance(part, ast.Constant) and isinstance(part.value, str))
            operation = re.search(r'\bgit\s+(clone|show)\b', shell)
            if operation:
                literals += ['git', operation[1]]
        if not any(operation in literals for operation in ('clone', 'show')):
            continue
        if not ('git' in literals or name(call.func) in ('git', 'git_run', 'git_runner', 'command', 'run')):
            continue
        # A subprocess without cwd/-C reads history from the test runner's
        # checkout too. Fixture wrappers instead choose their own repository.
        implicit_checkout = ('show' in literals and 'git' in literals
                             and isinstance(call.func, ast.Attribute)
                             and isinstance(call.func.value, ast.Name)
                             and call.func.value.id == 'subprocess'
                             and '-C' not in literals and '--git-dir' not in literals
                             and not re.search(r'\s(?:-C|--git-dir)(?:\s|=)', shell)
                             and not any(keyword.arg == 'cwd' for keyword in call.keywords))
        if not checkout(call) and not implicit_checkout:
            continue
        if not protected(owner(call)):
            violations.append((call.lineno, owner(call).name if owner(call) else '<module>'))
    return violations


class CheckoutDependencyGuardTests(unittest.TestCase):
    def test_installed_tests_do_not_clone_checkout_or_read_its_history_ungated(self):
        directory = Path(__file__).resolve().parent
        violations = []
        for path in sorted(directory.rglob('*.py')):
            for line, function in checkout_dependencies(path.read_text(encoding='utf-8')):
                violations.append(f'{path.relative_to(directory).as_posix()}:{line}: {function}')
        self.assertEqual(violations, [], 'Build a disposable Git fixture or gate source history: ' + ', '.join(violations))

    def test_guard_detects_clone_and_history_helpers_and_requires_every_caller_gated(self):
        clone = "subprocess.run(['git', 'clone', '--shared', str(ROOT), str(project)])"
        history = "subprocess.run(['git', 'show', 'abc123:file'], cwd=ROOT)"
        self.assertTrue(checkout_dependencies('def probe():\n    ' + clone))
        self.assertTrue(checkout_dependencies('def history():\n    ' + history))
        self.assertTrue(checkout_dependencies("def history():\n    subprocess.run(['git', 'show', 'abc123:file'])"))
        self.assertTrue(checkout_dependencies("def probe():\n    subprocess.run(['git', 'clone', str(Path(__file__).resolve().parents[2]), str(project)])"))
        self.assertTrue(checkout_dependencies("def probe():\n    subprocess.run(f'git clone {ROOT} {project}', shell=True)"))
        self.assertTrue(checkout_dependencies("def history():\n    subprocess.run('git show abc123:file', shell=True)"))
        gated = ('def history():\n    ' + history + '\n'
                 '@source_only.skip_unless_source_repo("needs source history")\n'
                 'def test_history():\n    history()\n')
        self.assertEqual(checkout_dependencies(gated), [])
        self.assertTrue(checkout_dependencies(gated + 'def test_unguarded():\n    history()\n'))
        self.assertEqual(checkout_dependencies('@skip_unless_source_repo()\nclass Cases:\n'
                                              '    def test_history(self):\n        ' + history), [])

    def test_disposable_repository_is_independent_and_comments_are_not_gates(self):
        fixture = ("def test_fixture():\n"
                   "    subprocess.run(['git', 'init', str(project)])\n"
                   "    subprocess.run(['git', '-C', str(project), 'commit', '-m', 'fixture'])\n"
                   "    subprocess.run(['git', 'clone', str(project), str(copy)])\n"
                   "    subprocess.run(['git', '-C', str(copy), 'show', 'HEAD:file'])\n")
        self.assertEqual(checkout_dependencies(fixture), [])
        self.assertTrue(checkout_dependencies('# skip_unless_source_repo\n'
            "def test_bad():\n    subprocess.run(['git', 'clone', str(ROOT), str(project)])\n"))


if __name__ == '__main__':
    unittest.main()
