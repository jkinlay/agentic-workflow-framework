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

    def owner(node):
        while node in parents:
            node = parents[node]
            if node in functions:
                return node
        return None

    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault((owner(node), target.id), []).append(node.value)

    def name(node):
        return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''

    def gated(node):
        while node is not None:
            for decorator in getattr(node, 'decorator_list', ()):
                if isinstance(decorator, ast.Call) and name(decorator.func) == 'skip_unless_source_repo':
                    return True
            # Preserve an existing inline source-only early skip as well.
            for statement in getattr(node, 'body', ()):
                if (isinstance(statement, ast.If) and isinstance(statement.test, ast.BoolOp)
                        and isinstance(statement.test.op, ast.Or)
                        and any(isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                                and isinstance(test.operand, ast.Name)
                                and test.operand.id == 'AWF_SOURCE_REPOSITORY' for test in statement.test.values)
                        and statement.body and isinstance(statement.body[0], ast.Expr)
                        and isinstance(statement.body[0].value, ast.Call)
                        and name(statement.body[0].value.func) == 'skipTest'):
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

    # Existing modules may admit tests only with their own verified Git tuple.
    # Recognize that exact runtime gate; do not add skips or accept comments.
    tuple_names = {key[1] for key, values in assignments.items() if key[0] is None
                   and any(isinstance(value, ast.Call) and name(value.func) == 'committed_git_tuple'
                           for value in values)}
    module_gated = False
    for function in functions:
        if function.name != 'setUpModule':
            continue
        for statement in function.body:
            if (isinstance(statement, ast.If) and isinstance(statement.test, ast.Compare)
                    and isinstance(statement.test.left, ast.Name) and statement.test.left.id in tuple_names
                    and len(statement.test.ops) == 1 and isinstance(statement.test.ops[0], ast.Is)
                    and isinstance(statement.test.comparators[0], ast.Constant)
                    and statement.test.comparators[0].value is None
                    and statement.body and isinstance(statement.body[0], ast.Raise)
                    and isinstance(statement.body[0].exc, ast.Call)
                    and name(statement.body[0].exc.func) == 'SkipTest'):
                module_gated = True

    def checkout(node):
        return any((isinstance(part, ast.Name) and part.id in roots)
                   or (isinstance(part, ast.Attribute) and part.attr in roots)
                   for part in ast.walk(node))

    def expanded(node, scope, seen=frozenset()):
        # Resolve simple local/module aliases and argv variables conservatively.
        # Multiple assignments are possible values; this is not control-flow analysis.
        if isinstance(node, ast.Name) and node.id not in seen:
            values = assignments.get((scope, node.id), assignments.get((None, node.id), ()))
            if values:
                return ([node] if node.id in roots else []) + [
                    part for value in values for part in expanded(value, scope, seen | {node.id})]
        return [node] + [part for child in ast.iter_child_nodes(node) for part in expanded(child, scope, seen)]

    def git_executable(node, scope):
        value = expanded(node, scope)[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return bool(re.search(r'(?:^|[/\\])git(?:\.exe)?$', value.value))
        return (isinstance(value, ast.Call) and name(value.func) == 'which' and bool(value.args)
                and isinstance(value.args[0], ast.Constant) and value.args[0].value == 'git')

    violations = []
    for call in calls:
        # subprocess argv and the local git/command wrappers used by fixtures.
        parts = [part for argument in call.args for part in expanded(argument, owner(call))]
        keywords = [part for keyword in call.keywords for part in expanded(keyword.value, owner(call))]
        literals = [part.value for part in parts if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        shell = ' '.join(literals)
        commands = expanded(call.args[0], owner(call)) if call.args else []
        explicit_git = any(isinstance(part, ast.Constant) and isinstance(part.value, str)
                           and re.match(r'^(?:[^\s]*[/\\])?git(?:\.exe)?(?:\s|$)', part.value)
                           for part in commands[:1])
        if commands and isinstance(commands[0], (ast.List, ast.Tuple)) and commands[0].elts:
            explicit_git = git_executable(commands[0].elts[0], owner(call))
        if commands and isinstance(commands[0], ast.JoinedStr):
            explicit_git = bool(commands[0].values and isinstance(commands[0].values[0], ast.Constant)
                                and re.match(r'^git(?:\.exe)?\s', commands[0].values[0].value))
        wrapper = name(call.func) in ('git', 'git_run', 'git_runner') or (
            isinstance(call.func, ast.Name) and name(call.func) in ('command', 'run'))
        if not (explicit_git or wrapper):
            continue
        # A subprocess without cwd/-C reads history from the test runner's
        # checkout too. Fixture wrappers instead choose their own repository.
        implicit_checkout = (bool(re.search(r'\bshow\b', shell)) and explicit_git
                             and isinstance(call.func, ast.Attribute)
                             and isinstance(call.func.value, ast.Name)
                             and call.func.value.id == 'subprocess'
                             and '-C' not in literals and '--git-dir' not in literals
                             and not re.search(r'\s(?:-C|--git-dir)(?:\s|=)', shell)
                             and not any(keyword.arg == 'cwd' for keyword in call.keywords))
        if not any(checkout(part) for part in parts + keywords) and not implicit_checkout:
            continue
        history = bool(re.search(r'\b(?:clone|show)\b', shell))
        root_target = '-C' in literals or bool(re.search(r'(?:^|\s)-C(?:\s|=)', shell)) or any(
            keyword.arg == 'cwd' and any(checkout(part) for part in expanded(keyword.value, owner(call)))
            for keyword in call.keywords)
        if not history and not root_target:
            continue
        if not module_gated and not protected(owner(call)):
            violations.append((call.lineno, owner(call).name if owner(call) else '<module>'))
    return violations


class CheckoutDependencyGuardTests(unittest.TestCase):
    def test_shell_string_with_git_directory_is_flagged(self):
        self.assertTrue(checkout_dependencies("def probe():\n    subprocess.run(f'git -C {ROOT} show HEAD:file', shell=True)"))

    def test_argv_variable_is_flagged(self):
        self.assertTrue(checkout_dependencies("def probe():\n    argv = ['git', '-C', str(ROOT), 'show', 'HEAD:file']\n    subprocess.run(argv)"))

    def test_root_alias_clone_is_flagged(self):
        self.assertTrue(checkout_dependencies("def probe():\n    source = ROOT\n    subprocess.run(['git', 'clone', str(source), str(project)])"))

    def test_executable_aliases_paths_and_assigned_root_keep_their_identity(self):
        cases = (
            "GIT = '/usr/bin/git'\ndef probe():\n    subprocess.run([GIT, 'clone', str(ROOT), str(project)])",
            "def probe():\n    subprocess.run(['/usr/bin/git', '-C', str(ROOT), 'status'])",
            "GIT = shutil.which('git')\ndef probe():\n    argv = [GIT, 'show', 'HEAD:file']\n    subprocess.run(argv, cwd=ROOT)",
            "ROOT = Path('/checkout')\ndef probe():\n    subprocess.run('git show HEAD:file', cwd=ROOT)",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertTrue(checkout_dependencies(source))

    def test_root_targets_are_flagged_for_non_history_git_too(self):
        for target in ('ROOT', 'repository_root', 'Path(__file__).resolve().parents[2]'):
            for invocation in ("subprocess.run(['git', '-C', str(target), 'status'])",
                               "subprocess.run('git status', cwd=target, shell=True)"):
                with self.subTest(target=target, invocation=invocation):
                    self.assertTrue(checkout_dependencies('def probe():\n    target = ' + target + '\n    ' + invocation))

    def test_existing_verified_unborn_module_gate_is_recognized(self):
        source = ("SOURCE_GIT_TUPLE = committed_git_tuple(ROOT)\n"
                  "def setUpModule():\n    if SOURCE_GIT_TUPLE is None:\n        raise unittest.SkipTest('needs HEAD')\n"
                  "def test_current():\n    subprocess.run(['git', '-C', str(ROOT), 'diff', 'HEAD'])\n")
        self.assertEqual(checkout_dependencies(source), [])
        self.assertTrue(checkout_dependencies(source.replace('is None', 'is not None')))

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
        self.assertTrue(checkout_dependencies("def history():\n    subprocess.run(['git', 'show', '-s', 'HEAD'])"))
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
