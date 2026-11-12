"""Declare source-history dependencies before running a closed release archive.

Lesson 2.6: tests that git-init their own temporary repositories need no source
Git guard. Trace source-root targets and helper callers, not the word ``git``.
The scan is static and never invokes Git or imports the inspected test modules.
"""
import ast
from pathlib import Path
import inspect
import subprocess
import re
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]

# AC42 has an explicit archive fallback rather than a test-level skip.
# Every exception must still cover an undeclared source dependency.
EXCEPTIONS = {
    ('scripts/tests/test_ac42_e2e.py',
     'WindowsLauncherPublicationTests.test_merged_tree_ac42_files_have_no_publication_findings'):
        'Explicit committed_git_tuple branch uses released file bytes in archives.',
}


def terminal(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''


def test_paths(root):
    return sorted(set(root.glob('.agentic/tests/**/*.py')) |
                  set(root.glob('scripts/tests/**/*.py')) |
                  set(root.glob('global/*/tests/**/*.py')))


def declarations(tree, source_root):
    def tuple_call(node):
        return (isinstance(node, ast.Call) and terminal(node.func) == 'committed_git_tuple'
                and len(node.args) == 1 and not node.keywords and source_root(node.args[0]))

    tuple_names = {terminal(target) for node in tree.body if isinstance(node, ast.Assign)
                   and tuple_call(node.value) for target in node.targets}

    def missing_tuple(node, allow_alias=False):
        return (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Is) and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value is None
                and (tuple_call(node.left) or (allow_alias and isinstance(node.left, ast.Name)
                                              and node.left.id in tuple_names)))

    def declaration(node):
        decorators = getattr(node, 'decorator_list', [])
        for deco in decorators:
            if (isinstance(deco, ast.Call) and terminal(deco.func) == 'skipIf'
                    and len(deco.args) == 2 and not deco.keywords
                    and missing_tuple(deco.args[0])
                    and terminal(deco.args[1]) == 'COMMITTED_GIT_REASON'):
                return True
            if isinstance(deco, ast.Call) and terminal(deco.func) == 'skip_unless_source_repo':
                return True
        if not isinstance(node, ast.FunctionDef):
            return False
        for condition in (n for n in node.body if isinstance(n, ast.If)):
            test = condition.test
            if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or) and len(test.values) == 2:
                source_absent, test = test.values
                if not (isinstance(source_absent, ast.UnaryOp) and isinstance(source_absent.op, ast.Not)
                        and terminal(source_absent.operand) == 'AWF_SOURCE_REPOSITORY'):
                    continue
            if not missing_tuple(test, allow_alias=node.name == 'setUpModule') or not condition.body:
                continue
            first = condition.body[0]
            call = first.exc if isinstance(first, ast.Raise) else first.value if isinstance(first, ast.Expr) else None
            expected = 'SkipTest' if isinstance(first, ast.Raise) else 'skipTest'
            if (node.name == 'setUpModule' and not isinstance(first, ast.Raise)):
                continue
            if (isinstance(call, ast.Call) and terminal(call.func) == expected
                    and len(call.args) == 1 and not call.keywords
                    and (terminal(call.args[0]) == 'COMMITTED_GIT_REASON' or node.name == 'setUpModule')):
                return True
        return False
    return declaration



def data_calls(tree):
    """Prove data receivers, never infer executors from their spelling.

    Unknown calls fail the proof. Local functions must contain only data
    operations or calls to other proven local functions. Plain record classes
    must have no inheritance, decorators, metaclass or executing constructor;
    instance methods are checked separately. Resolve lexical bindings and all
    assignments so a shadowed fake cannot exempt an executing receiver.

    The real subprocess result records used by existing fixtures are external:
    derive their constructors from the host stdlib AST too (never import a
    scanned test). No executor or fake-name allowlist is involved.
    """
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    bindings = {}
    attribute_writes = []

    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return node
        return tree

    for node in ast.walk(tree):
        owner = scope(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault((owner, node.name), []).append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                            *([node.args.vararg] if node.args.vararg else []),
                            *([node.args.kwarg] if node.args.kwarg else [])]:
                    bindings.setdefault((node, arg.arg), []).append(None)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    bindings.setdefault((owner, target.id), []).append(node.value)
                elif isinstance(target, ast.Attribute):
                    attribute_writes.append(target)
        elif (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
              and not isinstance(parents.get(node), (ast.Assign, ast.AnnAssign))):
            bindings.setdefault((owner, node.id), []).append(None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bindings.setdefault((owner, node.name), []).append(None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                key = alias.asname or alias.name.split('.')[0]
                value = None
                if isinstance(node, ast.Import) and alias.name == 'subprocess':
                    value = subprocess_tree
                elif isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
                    value = subprocess_classes.get(alias.name)
                bindings.setdefault((owner, key), []).append(value)

    def resolve(node, seen=frozenset()):
        if node is None or node in seen:
            return [None]
        seen = seen | {node}
        if isinstance(node, ast.Name):
            owner = scope(node)
            while (owner, node.id) not in bindings and owner is not tree:
                owner = scope(owner)
                # Unqualified names in methods do not inherit class namespaces.
                if isinstance(owner, ast.ClassDef):
                    owner = scope(owner)
            values = bindings.get((owner, node.id), [None])
            return [part for value in values for part in resolve(value, seen)]
        if isinstance(node, ast.Attribute):
            bases = resolve(node.value, seen)
            if any(write.attr == node.attr and any(base in resolve(write.value, seen)
                                                   for base in bases if base is not None)
                   for write in attribute_writes):
                return [None]
            return [next((member for member in base.body
                          if isinstance(member, (ast.FunctionDef, ast.ClassDef))
                          and member.name == node.attr
                          and (base is not subprocess_tree or isinstance(member, ast.ClassDef))), None)
                    if isinstance(base, (ast.Module, ast.ClassDef)) else None for base in bases]
        if isinstance(node, ast.Call):
            return [target if isinstance(target, ast.ClassDef) else None
                    for target in resolve(node.func, seen)]
        return [node]

    def inert(target, seen=frozenset()):
        if target is None or target in seen:
            return False
        seen = seen | {target}
        if isinstance(target, ast.ClassDef):
            if any(target in resolve(write.value) for write in attribute_writes):
                return False
            if (target.decorator_list or target.keywords or
                    any(not isinstance(base, ast.Name) or base.id != 'object'
                        for base in target.bases)):
                return False
            # Local bases could supply executable allocation/attribute hooks.
            if target in parents and target.bases:
                return False
            methods = [n for n in target.body if isinstance(n, ast.FunctionDef)]
            if any(n.name in ('__new__', '__setattr__', '__getattribute__') for n in methods):
                return False
            checked = methods if target in parents else [n for n in methods
                                                          if n.name in ('__init__', '__del__')]
            return all(inert(n, seen) for n in checked)
        if not isinstance(target, ast.FunctionDef) or target.decorator_list:
            return False
        if target not in parents:
            # External constructor bodies cannot resolve names against test locals.
            return not any(isinstance(n, ast.Call) for n in ast.walk(target))
        # Examine every call, including nested functions/lambdas and defaults.
        # No call to subprocess/os/unknown callbacks can pass this proof.
        return all(proven(call.func, seen) for call in ast.walk(target)
                   if isinstance(call, ast.Call))

    def proven(receiver, seen=frozenset()):
        targets = resolve(receiver)
        return bool(targets) and all(inert(target, seen) for target in targets)

    return {call for call in ast.walk(tree) if isinstance(call, ast.Call) and (
        (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
         and call.func.value.id == 'self' and call.func.attr.startswith('assert'))
        or proven(call.func))}


# Parse trusted stdlib definitions once; fixture imports are resolved statically.
subprocess_tree = ast.parse(inspect.getsource(subprocess))
subprocess_classes = {n.name: n for n in subprocess_tree.body if isinstance(n, ast.ClassDef)}


def source_git_tests(sources):
    """Return test IDs and their declarations, following shared helper names.

    Source-root aliases come from __file__ ancestry or imported ROOT constants.
    Parameterized Git wrappers are followed only at source-bound call sites;
    self.root and temporary-directory locals do not become source roots.
    Git-shaped arguments are inspected at every call, independent of its
    callee, including keyword arguments and assigned argv aliases. Proven data
    receivers are exempt; their nested calls are still inspected. Shared
    method aliases (including imported fixture methods) retain their dependency.
    """
    modules = []
    builtin_wrappers = {'git', '_git', 'git_run', 'git_runner', 'materialize_commit'}
    wrappers = set()
    source_helpers = set()
    module_paths = {Path(path).stem: path for path in sources}
    for path, source in sources.items():
        tree = ast.parse(source, filename=path)
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.rsplit('.', 1)[-1] in module_paths:
                        imports[alias.asname or alias.name] = (module_paths[alias.name.rsplit('.', 1)[-1]], '')
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_path = module_paths.get(node.module.rsplit('.', 1)[-1])
                if imported_path:
                    for alias in node.names:
                        imports[alias.asname or alias.name] = (imported_path, alias.name)

        def reference(node, imports=imports, path=path):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in imports:
                imported_path, imported_name = imports[base.id]
                return imported_path, imported_name if base is node else terminal(node)
            return path, terminal(node)

        def scope(node, parents=parents, tree=tree):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node
            return tree
        assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault((scope(node), target.id), []).append(node.value)
        roots = {'ROOT', 'SOURCE_ROOT', 'repository_root', '__file__'}

        # Like lane E's checkout()/expanded(), inspect every child of every
        # argument. Keep possible assignment values, including aliases, without
        # letting a ROOT assignment erase its conventional source identity.
        def expanded(node, assignments=assignments, scope=scope, tree=tree):
            result, pending, seen = [], [node], set()
            while pending:
                part = pending.pop()
                if part is None or part in seen:
                    continue
                seen.add(part)
                result.append(part)
                if isinstance(part, ast.Name):
                    pending.extend(assignments.get((scope(part), part.id),
                                                   assignments.get((tree, part.id), ())))
                pending.extend(ast.iter_child_nodes(part))
            return result

        def source_root(node, assignments=assignments, scope=scope, tree=tree, roots=roots):
            pending, seen = [node], set()
            while pending:
                part = pending.pop()
                if part is None or part in seen:
                    continue
                seen.add(part)
                if isinstance(part, (ast.Name, ast.Attribute)) and terminal(part) in roots:
                    return True
                if isinstance(part, ast.Name):
                    pending.extend(assignments.get((scope(part), part.id),
                                                   assignments.get((tree, part.id), ())))
                elif isinstance(part, ast.Call):
                    # A path conversion retains identity; an arbitrary factory
                    # result (e.g. HostDriver(ROOT).worker) is not a path alias.
                    if terminal(part.func) in ('str', 'Path', 'PurePath', 'resolve',
                                               'absolute', 'as_posix', 'joinpath'):
                        pending.extend(part.args)
                        if isinstance(part.func, ast.Attribute):
                            pending.append(part.func.value)
                elif isinstance(part, ast.Attribute):
                    if part.attr in ('parent', 'parents'):
                        pending.append(part.value)
                else:
                    pending.extend(ast.iter_child_nodes(part))
            return False

        def git_command(node, seen=frozenset(), assignments=assignments, scope=scope, tree=tree):
            if node in seen:
                return False
            seen = seen | {node}
            if isinstance(node, ast.Name):
                values = assignments.get((scope(node), node.id),
                                         assignments.get((tree, node.id), ()))
                return any(git_command(value, seen) for value in values)
            if isinstance(node, (ast.List, ast.Tuple)):
                return bool(node.elts) and git_command(node.elts[0], seen)
            if isinstance(node, ast.Starred):
                return git_command(node.value, seen)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return git_command(node.left, seen)
            if isinstance(node, ast.JoinedStr):
                return bool(node.values) and git_command(node.values[0], seen)
            if isinstance(node, ast.Call) and terminal(node.func) == 'which' and node.args:
                return git_command(node.args[0], seen)
            return (isinstance(node, ast.Constant) and isinstance(node.value, str) and
                    bool(re.match(r'^(?:[^\s]*[/\\])?git(?:\.exe)?(?:\s|$)', node.value)))

        inert_calls = data_calls(tree)
        direct = set()
        local_functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node in inert_calls:
                continue
            targets = [*node.args, *(kw.value for kw in node.keywords)]
            commands = [target for target in targets if git_command(target)]
            if not commands:
                continue
            owner = scope(node)
            explicit_cwd = any(kw.arg == 'cwd' for kw in node.keywords)
            local_function = local_functions.get(terminal(node.func))
            if local_function:
                explicit_cwd |= any(parameter.arg == 'cwd' for parameter, argument in
                                    zip(local_function.args.args, node.args))
            implicit_checkout = False
            # Classify each command separately: one temporary -C argument
            # must not hide another cwd-less history read in the same call.
            for command in commands:
                shell = ' '.join(part.value for part in expanded(command)
                                 if isinstance(part, ast.Constant) and isinstance(part.value, str))
                explicit_directory = explicit_cwd or bool(re.search(
                    r'(?:^|\s)(?:-C|--git-dir)(?:\s|=|$)', shell))
                implicit_checkout |= not explicit_directory and bool(re.search(
                    r'\b(?:show|log|diff|rev-parse|rev-list|reflog|merge-base|ls-tree|ls-files|'
                    r'cat-file|archive|describe|status|show-ref|for-each-ref|blame|grep)\b', shell))
            if owner is not tree:
                if any(source_root(target) for target in targets) or implicit_checkout:
                    direct.add(owner)
                elif any(terminal(part) in {arg.arg for arg in owner.args.args}
                         for target in targets for part in expanded(target)):
                    wrappers.add((path, owner.name))
        declaration = declarations(tree, source_root)
        protected = set()
        module_guard = any(isinstance(n, ast.FunctionDef) and n.name == "setUpModule"
                           and declaration(n) for n in tree.body)
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            ancestor = function
            while ancestor is not None:
                if module_guard or declaration(ancestor):
                    protected.add(function)
                    break
                ancestor = parents.get(ancestor)
        modules.append((path, tree, parents, source_root, direct, reference, protected, inert_calls))

    def wrapper_arguments(call):
        arguments = [*call.args, *(kw.value for kw in call.keywords)]
        if terminal(call.func) not in builtin_wrappers:
            return arguments
        # The first argument is the wrapper's repository. Config values that
        # mention a source script do not read its history. Transfers do read
        # their source even when the destination repository is disposable.
        if any(isinstance(part, ast.Constant) and part.value in ('clone', 'fetch', 'pull')
               for arg in arguments for part in ast.walk(arg)):
            return arguments
        return [*call.args[:1], *(kw.value for kw in call.keywords
                                if kw.arg in ('root', 'repository', 'cwd'))]

    # Fixed point across modules covers imported historical/closeout helpers,
    # nested wrappers, and class-level method aliases used by fixture importers.
    changed = True
    while changed:
        changed = False
        for path, tree, _, source_root, direct, reference, protected, inert_calls in modules:
            for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                needed = function in direct
                parameters = {arg.arg for arg in function.args.args} - {'self', 'cls'}
                for call in (n for n in ast.walk(function) if isinstance(n, ast.Call) and n not in inert_calls):
                    name = terminal(call.func)
                    arguments = wrapper_arguments(call)
                    key = reference(call.func)
                    is_wrapper = key in wrappers or name in builtin_wrappers
                    needed |= key in source_helpers or (is_wrapper and
                               any(source_root(arg) for arg in arguments))
                    if (is_wrapper and (path, function.name) not in wrappers and
                            any(terminal(part) in parameters for arg in arguments
                                for part in ast.walk(arg))):
                        wrappers.add((path, function.name))
                        changed = True
                if (needed and function not in protected and not function.name.startswith('test_')
                        and function.name not in ('setUp', 'setUpClass', 'setUpModule')
                        and (path, function.name) not in source_helpers):
                    source_helpers.add((path, function.name))
                    changed = True
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and reference(node.value) in source_helpers:
                    for target in node.targets:
                        if terminal(target) and (path, terminal(target)) not in source_helpers:
                            source_helpers.add((path, terminal(target)))
                            changed = True

    results = {}
    for path, tree, parents, source_root, direct, reference, protected, inert_calls in modules:
        declaration = declarations(tree, source_root)
        module_guard = any(isinstance(n, ast.FunctionDef) and n.name == 'setUpModule'
                           and declaration(n) for n in tree.body)
        def needs_source(function):
            return function in direct or any(
                reference(call.func) in source_helpers or
                ((reference(call.func) in wrappers or terminal(call.func) in builtin_wrappers)
                 and any(source_root(arg) for arg in wrapper_arguments(call)))
                for call in ast.walk(function) if isinstance(call, ast.Call) and call not in inert_calls)
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                         and n.name.startswith('test_')):
            ancestors = []
            parent = parents.get(function)
            while parent is not None:
                if isinstance(parent, ast.ClassDef):
                    ancestors.insert(0, parent)
                parent = parents.get(parent)
            setup = [n for cls in ancestors for n in cls.body
                     if isinstance(n, ast.FunctionDef) and n.name in ('setUp', 'setUpClass')]
            if not any(needs_source(n) for n in [function, *setup]):
                continue
            name = '.'.join([n.name for n in ancestors] + [function.name])
            guarded = module_guard or declaration(function) or any(declaration(n) for n in setup)
            guarded |= any(declaration(cls) for cls in ancestors)
            results[path, name] = bool(guarded)
    return results


def missing_declarations(sources):
    return sorted(key for key, guarded in source_git_tests(sources).items()
                  if not guarded and key not in EXCEPTIONS and
                  (key[0], key[1].rsplit('.', 1)[0]) not in EXCEPTIONS)


def stale_exceptions(sources):
    """An exception must still cover at least one undeclared source dependency."""
    needed = {key for key, guarded in source_git_tests(sources).items() if not guarded}
    return sorted(key for key in EXCEPTIONS if key not in needed and not any(
        path == key[0] and name.startswith(key[1] + '.') for path, name in needed))


class SourceGitGuardTests(unittest.TestCase):
    def assert_missing(self, body, prefix=''):
        source = prefix + '\nclass Tests:\n    def test_history(self):\n' + ''.join(
            '        ' + line + '\n' for line in body.splitlines())
        self.assertEqual({('test_fixture.py', 'Tests.test_history'): False},
                         source_git_tests({'test_fixture.py': source}))

    def test_assignment_alias_keeps_source_identity(self):
        self.assert_missing("source = ROOT\nsubprocess.run(['git', '-C', str(source), 'show', 'HEAD:LICENSE'])")

    def test_subprocess_import_alias_is_detected(self):
        self.assert_missing("read_output(['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE'])",
                            'from subprocess import check_output as read_output')
        # R2-M1: command arguments carry the dependency, whatever receives them.
        # Exercise sequences, strings, keywords and implicit checkout reads for
        # every callable shape without changing the module's test count.
        forms = {
            'functools.partial': "read_history = functools.partial(subprocess.check_output, ARGV)\nread_history()",
            'lambda': "read_history = lambda command: subprocess.check_output(command)\nread_history(ARGV)",
            'local helper': "read_history(ARGV)",
            'getattr': "getattr(subprocess, 'run')(ARGV)",
            'held argv': "command = ARGV\nforwarded = command\nconsumer(forwarded)",
        }
        commands = (
            "['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE']",
            "('git', '-C', str(ROOT), 'show', 'HEAD:LICENSE')",
            "f'git -C {ROOT} show HEAD:LICENSE'",
            "['git', 'show', 'HEAD:LICENSE']",
            "'git show HEAD:LICENSE'",
            "['git', 'rev-list', 'HEAD']",
            "'git reflog'",
        )
        prefix = ('import functools\n'
                  'def read_history(command):\n    return subprocess.check_output(command)\n')
        for form, body in forms.items():
            for command in commands:
                with self.subTest(form=form, command=command):
                    self.assert_missing(body.replace('ARGV', command), prefix)
                    source = (prefix + '\nclass Tests:\n'
                              '    @unittest.skipIf(committed_git_tuple(ROOT) is None, COMMITTED_GIT_REASON)\n'
                              '    def test_history(self):\n' + ''.join(
                                  '        ' + line + '\n'
                                  for line in body.replace('ARGV', command).splitlines()))
                    self.assertEqual({('test_fixture.py', 'Tests.test_history'): True},
                                     source_git_tests({'test_fixture.py': source}))
            with self.subTest(temporary=form):
                source = prefix + '\ndef test_temporary():\n' + ''.join(
                    '    ' + line + '\n' for line in body.replace(
                        'ARGV', "['git', '-C', str(tmp), 'show', 'HEAD:LICENSE']").splitlines())
                self.assertEqual({}, source_git_tests({'test_fixture.py': source}))
        for body in ("consumer(command=['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE'])",
                     "command = ('git', 'show', 'HEAD:LICENSE')\nconsumer(argv=command)",
                     "consumer(command='git show HEAD:LICENSE')",
                     "consumer(['git', '-C', str(tmp), 'show', 'HEAD:file'], ['git', 'show', 'HEAD:file'])"):
            with self.subTest(keyword=body):
                self.assert_missing(body)

    def test_temporary_init_does_not_exempt_implicit_checkout(self):
        self.assert_missing("subprocess.run(['git', 'init', str(tmp)])\n"
                            "subprocess.run(['git', 'show', 'HEAD:LICENSE'])")

    def test_path_division_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '-C', str(ROOT / 'x'), 'show', 'HEAD:LICENSE'])")

    def test_formatted_root_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '-C', f'{ROOT}', 'show', 'HEAD:LICENSE'])")

    def test_path_method_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '-C', ROOT.as_posix(), 'show', 'HEAD:LICENSE'])")

    def test_git_directory_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '--git-dir', str(ROOT / '.git'), 'show', 'HEAD:LICENSE'])")

    def test_concatenated_argv_keeps_source_identity(self):
        self.assert_missing("argv = ['git'] + ['-C', str(ROOT)] + ['show', 'HEAD:LICENSE']\n"
                            "subprocess.run(argv)")

    def test_shell_check_output_keeps_source_identity(self):
        self.assert_missing("subprocess.check_output(f'git -C {ROOT} show HEAD:LICENSE', shell=True)")

    def test_keyword_wrapper_keeps_source_identity(self):
        self.assert_missing("git(root=ROOT)",
                            "def git(root):\n    return subprocess.run(['git', '-C', str(root), 'show', 'HEAD:LICENSE'])\n")

    def test_temporary_fetch_still_reads_source(self):
        self.assert_missing("subprocess.run(['git', 'init', str(tmp)])\n"
                            "subprocess.run(['git', '-C', str(tmp), 'fetch', str(ROOT), 'HEAD'])")

    def test_parent_chain_root_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE'])",
                            'ROOT = Path(__file__).parent.parent')

    def test_imported_root_assignment_keeps_source_identity(self):
        self.assert_missing("subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE'])",
                            'ROOT = m.ROOT')

    def test_temporary_repository_with_explicit_cwd_remains_exempt(self):
        source = '''
def test_temporary():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / 'repo'
        subprocess.run(['git', 'init', str(root)], check=True)
        (root / 'LICENSE').write_text('fixture', encoding='utf-8')
        subprocess.run(['git', '-C', str(root), 'add', 'LICENSE'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid',
                        'commit', '-m', 'fixture'], check=True)
        assert subprocess.check_output(['git', 'show', 'HEAD:LICENSE'], cwd=root) == b'fixture'
'''
        self.assertEqual({}, source_git_tests({'test_fixture.py': source}))

    def test_declaration_requires_exact_tuple_comparison(self):
        source = '''
class Tests:
    @unittest.skipIf(committed_git_tuple(OTHER) is None or True, COMMITTED_GIT_REASON)
    def test_history(self):
        subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:LICENSE'])
'''
        self.assertEqual({('test_fixture.py', 'Tests.test_history'): False},
                         source_git_tests({'test_fixture.py': source}))

    def test_stale_exception_for_deleted_test_is_rejected(self):
        key = ('test_fixture.py', 'Tests.test_deleted')
        with patch.dict(EXCEPTIONS, {key: 'fixture'}, clear=True):
            self.assertEqual([key], stale_exceptions({'test_fixture.py': 'class Tests: pass'}))

    def test_stale_exception_for_independent_test_is_rejected(self):
        key = ('test_fixture.py', 'test_temporary')
        with patch.dict(EXCEPTIONS, {key: 'fixture'}, clear=True):
            self.assertEqual([key], stale_exceptions({'test_fixture.py': 'def test_temporary(): pass'}))

    def test_stale_exception_for_declared_test_is_rejected(self):
        key = ('test_fixture.py', 'test_history')
        source = ("@unittest.skipIf(committed_git_tuple(ROOT) is None, COMMITTED_GIT_REASON)\n"
                  "def test_history():\n    subprocess.run(['git', 'show', 'HEAD:LICENSE'], cwd=ROOT)\n")
        with patch.dict(EXCEPTIONS, {key: 'fixture'}, clear=True):
            self.assertEqual([key], stale_exceptions({'test_fixture.py': source}))

    def test_live_class_exception_is_retained(self):
        source = "class Tests:\n    def test_history(self):\n        subprocess.run(['git', 'show', 'HEAD:file'], cwd=ROOT)\n"
        with patch.dict(EXCEPTIONS, {('test_fixture.py', 'Tests'): 'fixture'}, clear=True):
            self.assertEqual([], stale_exceptions({'test_fixture.py': source}))
            self.assertEqual([], missing_declarations({'test_fixture.py': source}))

    def test_invalid_declaration_shapes_do_not_cover_history(self):
        for condition in ('committed_git_tuple(OTHER) is None',
                          'committed_git_tuple(ROOT) is None or True',
                          'committed_git_tuple(ROOT) is not None',
                          'committed_git_tuple(ROOT) == None',
                          'committed_git_tuple(ROOT) is None is None'):
            for form in ('decorator', 'inline', 'module'):
                read = "    subprocess.run(['git', 'show', 'HEAD:file'], cwd=ROOT)\n"
                if form == 'decorator':
                    source = '@unittest.skipIf(' + condition + ', COMMITTED_GIT_REASON)\ndef test_history():\n' + read
                else:
                    skip = ('raise unittest.SkipTest' if form == 'module' else 'self.skipTest')
                    gate = '    if ' + condition + ':\n        ' + skip + '(COMMITTED_GIT_REASON)\n'
                    source = ('def setUpModule():\n' + gate + 'def test_history():\n' + read if form == 'module'
                              else 'def test_history(self):\n' + gate + read)
                with self.subTest(condition=condition, form=form):
                    self.assertEqual({('test_fixture.py', 'test_history'): False},
                                     source_git_tests({'test_fixture.py': source}))

    def test_existing_gate_forms_remain_accepted(self):
        read = "    subprocess.run(['git', 'show', 'HEAD:file'], cwd=ROOT)\n"
        forms = (
            '@unittest.skipIf(committed_git_tuple(ROOT) is None, COMMITTED_GIT_REASON)\ndef test_history():\n' + read,
            '@source_only.skip_unless_source_repo("needs source history", ".git")\ndef test_history():\n' + read,
            '@skip_unless_source_repo()\nclass Tests:\n    def test_history(self):\n    ' + read,
            'def test_history(self):\n    if committed_git_tuple(ROOT) is None:\n        self.skipTest(COMMITTED_GIT_REASON)\n' + read,
            'def test_history(self):\n    if not AWF_SOURCE_REPOSITORY or committed_git_tuple(ROOT) is None:\n        self.skipTest(COMMITTED_GIT_REASON)\n' + read,
            'SOURCE_GIT_TUPLE = committed_git_tuple(ROOT)\ndef setUpModule():\n    if SOURCE_GIT_TUPLE is None:\n        raise unittest.SkipTest("needs HEAD")\ndef test_history():\n' + read,
            'def setUpModule():\n    if committed_git_tuple(ROOT) is None:\n        raise unittest.SkipTest(COMMITTED_GIT_REASON)\ndef test_history():\n' + read,
        )
        for source in forms:
            with self.subTest(source=source):
                self.assertTrue(source_git_tests({'test_fixture.py': source}))
                self.assertEqual([], missing_declarations({'test_fixture.py': source}))

    def test_gated_helper_protects_its_callers(self):
        source = ('@skip_unless_source_repo("needs HEAD", ".git")\n'
                  "def history():\n    subprocess.run(['git', 'show', 'HEAD:file'], cwd=ROOT)\n"
                  'def test_history():\n    history()\n')
        self.assertEqual([], missing_declarations({'test_fixture.py': source}))

    def test_helper_names_in_unrelated_modules_do_not_share_dependencies(self):
        sources = {
            'test_source.py': "def history():\n    subprocess.run(['git', 'show', 'HEAD:file'], cwd=ROOT)\n",
            'test_temporary.py': 'def history(): pass\ndef test_independent():\n    history()\n',
        }
        self.assertEqual({}, source_git_tests(sources))
        sources['test_temporary.py'] = 'from test_source import history as read_history\ndef test_history():\n    read_history()\n'
        self.assertEqual({('test_temporary.py', 'test_history'): False}, source_git_tests(sources))

    def test_command_shaped_data_does_not_execute_git(self):
        bare = "def test_data():\n    argv = ['git', 'show', 'HEAD:file']\n"
        self.assertEqual({}, source_git_tests({'test_fixture.py': bare}))
        data = (
            "self.assertIn('git show HEAD:file', script)",
            "self.assertEqual(['git', 'show', 'HEAD:file'], argv)",
            "recorder(['git', 'show', 'HEAD:file'])",
            "Record(['git', 'show', 'HEAD:file'])",
            "record = Record(None)\nrecord.capture(['git', 'show', 'HEAD:file'])",
            "subprocess.CompletedProcess(['git', 'show', 'HEAD:file'], 0)",
        )
        prefix = ("import subprocess\n"
                  "def recorder(argv):\n    return argv\n"
                  "class Record:\n"
                  "    def __init__(self, argv):\n        self.argv = argv\n"
                  "    def capture(self, argv):\n        self.argv = argv\n"
                  "def execute(argv):\n    return subprocess.run(argv)\n")
        for body in data:
            with self.subTest(data=body):
                source = prefix + '\nclass Tests:\n    def test_data(self):\n' + ''.join(
                    '        ' + line + '\n' for line in body.splitlines())
                self.assertEqual({}, source_git_tests({'test_fixture.py': source}))
        # Identical text becomes a dependency when the local receiver executes.
        self.assert_missing("execute('git show HEAD:file')", prefix)
        self.assert_missing("self.assertIn(subprocess.run(['git', 'show', 'HEAD:file']), script)")
        for body, extra in (
                ("recorder('git show HEAD:file')", "def recorder(argv):\n    return executor(argv)\n"),
                ("Record('git show HEAD:file')", "class Record:\n    def __init__(self, argv):\n        subprocess.run(argv)\n"),
                ("record = Record(None)\nrecord.capture('git show HEAD:file')",
                 "class Record:\n    def __init__(self, argv): pass\n"
                 "    def capture(self, argv):\n        os.system(argv)\n"),
                ("recorder = subprocess.run\nrecorder('git show HEAD:file')", ''),
                ("def recorder(argv):\n    subprocess.run(argv)\nrecorder('git show HEAD:file')", ''),
                ("recorder('git show HEAD:file')", "def recorder(argv):\n    callback = subprocess.run\n    return callback(argv)\n"),
                ("Record('git show HEAD:file')", "class Record:\n"
                 "    def __init__(self, argv):\n        self.argv = argv\n"
                 "    def __del__(self):\n        subprocess.run(self.argv)\n"),
                ("record = Record(None)\nrecord('git show HEAD:file')", "class Record:\n"
                 "    def __init__(self, argv): pass\n"
                 "    def __call__(self, argv):\n        subprocess.run(argv)\n"),
                ("executor.capture('git show HEAD:file')", "class Record:\n"
                 "    def capture(self, argv): pass\n"),
                ("subprocess.CompletedProcess = subprocess.run\n"
                 "subprocess.CompletedProcess('git show HEAD:file')", "import subprocess\n"),
                ("Record.__init__ = executor\nRecord('git show HEAD:file')",
                 "class Record:\n    def __init__(self, argv): pass\n"),
                ("subprocess.getoutput('git show HEAD:file')", "import subprocess\n"
                 "def getstatusoutput(command, **kwargs): pass\n"),
                ("receiver = recorder\nif condition:\n    receiver = executor\n"
                 "receiver('git show HEAD:file')", "def recorder(argv): pass\n"),
                ("for recorder in executors:\n    recorder('git show HEAD:file')",
                 "def recorder(argv): pass\n"),
                ("Record().capture('git show HEAD:file')", "execute = subprocess.run\n"
                 "class Record:\n    def execute(argv): pass\n"
                 "    def capture(self, argv):\n        return execute(argv)\n")):
            with self.subTest(executor=body):
                self.assert_missing(body, extra)
        source = ("def test_data():\n    argv = ['git', '-C', str(tmp), 'show', 'HEAD:file']\n"
                  '    completed = subprocess.CompletedProcess(argv, 0)\n'
                  '    assert completed.args == argv\n')
        self.assertEqual({}, source_git_tests({'test_fixture.py': source}))

    def test_source_git_dependencies_are_declared(self):
        sources = {path.relative_to(ROOT).as_posix(): path.read_text(encoding='utf-8-sig')
                   for path in test_paths(ROOT)}
        self.assertEqual([], missing_declarations(sources))
        self.assertEqual([], stale_exceptions(sources))

    def test_removing_a_real_guard_is_detected(self):
        path = 'scripts/tests/test_awf238_source_identity.py'
        source = (ROOT / path).read_text(encoding='utf-8')
        tree = ast.parse(source)
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                        and n.name == 'test_dirty_source_refuses_before_creating_cohort')
        function.decorator_list = []
        self.assertIn((path, 'SourceIdentityTests.' + function.name),
                      missing_declarations({path: ast.unparse(tree)}))
        self.assertEqual([], missing_declarations({path: source}))

    def test_temporary_repository_is_not_a_source_dependency(self):
        source = '''
import subprocess
class Tests:
    def test_temporary(self):
        root = temporary / 'repo'
        subprocess.run(['git', 'init', str(root)])
        subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'])
'''
        self.assertEqual({}, source_git_tests({'test_fixture.py': source}))

    def test_source_alias_and_helper_caller_require_a_guard(self):
        source = '''
import subprocess
source = Path(__file__).resolve().parents[2]
def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args])
class Tests:
    def history(self):
        return git(source, 'show', 'HEAD:file')
    def test_history(self):
        self.history()
'''
        self.assertEqual([('test_alias.py', 'Tests.test_history')],
                         missing_declarations({'test_alias.py': source}))


if __name__ == '__main__':
    unittest.main()
