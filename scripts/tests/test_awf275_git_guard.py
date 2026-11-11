"""Declare source-history dependencies before running a closed release archive.

Lesson 2.6: tests that git-init their own temporary repositories need no source
Git guard. Trace source-root targets and helper callers, not the word ``git``.
The scan is static and never invokes Git or imports the inspected test modules.
"""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]

# These paths belong to other active writers; remove an exception when its
# owning ticket supplies the committed-Git declaration or archive-safe bytes.
EXCEPTIONS = {
    ('.agentic/tests/test_reporting_e2_regressions.py',
     'ReportingE2Regressions.test_rollback_merge_base_never_consumes_report_as_legacy_digest'):
        'AWF-268 owns the two historical-controller callers.',
    ('.agentic/tests/test_reporting_e2_regressions.py',
     'ReportingE2Regressions.test_pre_release_report_rows_relocate_additively_with_attempts_intact'):
        'AWF-268 owns the two historical-controller callers.',
    ('.agentic/tests/test_upgrade_journal.py', 'UpgradeJournalCrashTests'):
        'AWF-266 owns archive-safe historical upgrade fixtures.',
    ('.agentic/tests/test_awf238_migration_identity.py', 'InstalledMigrationIdentityTests'):
        'AWF-266 owns the published migration fixture producer.',
    ('.agentic/tests/test_awf238_owner_boundary.py', 'LegacyOwnerBoundaryTests'):
        'AWF-266 owns the published owner-boundary fixture producer.',
    ('.agentic/tests/test_awf238_pre_f3.py', 'PreF3Tests'):
        'AWF-266 owns the authentic pre-F3 published fixtures.',
    ('.agentic/tests/test_awf238_pre_review.py', 'PublishedIdentityTests'):
        'AWF-266 owns the published identity fixture producer.',
    ('.agentic/tests/test_awf238_second_look.py', 'SecondLookTests'):
        'AWF-266 owns the published second-look fixtures.',
    ('.agentic/tests/test_awf238_third_look.py', 'ThirdLookTests'):
        'AWF-266 owns the published third-look fixtures.',
    ('.agentic/tests/test_upgrade_195.py', 'Published195UpgradeTests'):
        'AWF-266 owns the authentic published upgrade fixture producer.',
    ('.agentic/tests/test_upgrade_195_preservation.py', 'AuthenticBaselineTests'):
        'AWF-266 owns the authentic published preservation fixtures.',
    ('.agentic/tests/test_upgrade_195_recovery.py', 'PublishedRecoveryTests'):
        'AWF-266 owns the authentic published recovery fixtures.',
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


def source_git_tests(sources):
    """Return test IDs and their declarations, following shared helper names.

    Source-root aliases come from __file__ ancestry or imported ROOT constants.
    Parameterized Git wrappers are followed only at source-bound call sites;
    self.root and temporary-directory locals do not become source roots.
    Shared method aliases (including imported fixture methods) retain their
    dependency. Unsupported dynamic dispatch must be covered by review.
    """
    modules = []
    wrappers = {'git', '_git', 'materialize_commit'}
    source_helpers = set()
    for path, source in sources.items():
        tree = ast.parse(source, filename=path)
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        aliases = {(tree, 'ROOT'): True, (tree, 'SOURCE_ROOT'): True}
        commands = {}
        def scope(node, parents=parents, tree=tree):
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.FunctionDef):
                    return node
            return tree
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                ancestry = any(isinstance(n, ast.Subscript) and terminal(n.value) == 'parents'
                               for n in ast.walk(node.value))
                file_bound = any(terminal(n) == '__file__' for n in ast.walk(node.value))
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[scope(node), target.id] = ancestry and file_bound
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            commands[target.id] = node.value
        def source_root(node, aliases=aliases, scope=scope, parents=parents, tree=tree):
            if node is None:
                return False
            if isinstance(node, ast.Call) and terminal(node.func) in ('str', 'Path') and node.args:
                return source_root(node.args[0], aliases, scope, parents, tree)
            if isinstance(node, ast.Attribute):
                return node.attr in ('ROOT', 'SOURCE_ROOT')
            if not isinstance(node, ast.Name):
                return False
            owner = scope(node)
            return aliases.get((owner, node.id), aliases.get((tree, node.id), False))
        def command(node, commands=commands, seen=frozenset()):
            if isinstance(node, ast.Name):
                node = commands.get(node.id)
            if not isinstance(node, (ast.List, ast.Tuple)) or id(node) in seen:
                return []
            result = []
            for item in node.elts:
                result.extend(command(item.value, commands, seen | {id(node)})
                              if isinstance(item, ast.Starred) else [item])
            return result
        direct = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if terminal(node.func) not in ('run', 'check_output', 'check_call', 'Popen',
                                           'call', 'command', 'run_with_timeout'):
                continue
            args = command(node.args[0])
            if not args or not (isinstance(args[0], ast.Constant) and args[0].value == 'git'
                                or isinstance(args[0], ast.Call) and terminal(args[0].func) == 'which'
                                and args[0].args and isinstance(args[0].args[0], ast.Constant)
                                and args[0].args[0].value == 'git'
                                or terminal(args[0]) == 'git'):
                continue
            if terminal(node.func) not in ('run', 'check_output', 'check_call', 'Popen',
                                           'call', 'command', 'run_with_timeout'):
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, ast.FunctionDef):
                owner = parents.get(owner)
            targets = [kw.value for kw in node.keywords if kw.arg == 'cwd']
            targets += [args[i + 1] for i, arg in enumerate(args[:-1])
                        if isinstance(arg, ast.Constant) and arg.value == '-C']
            # clone reads its source even without -C/cwd.
            if any(isinstance(arg, ast.Constant) and arg.value == 'clone' for arg in args):
                targets += args
            if owner is not None:
                if any(source_root(target) for target in targets):
                    direct.add(owner)
                elif any(terminal(part) in {arg.arg for arg in owner.args.args}
                         for target in targets for part in ast.walk(target)):
                    wrappers.add(owner.name)
        modules.append((path, tree, parents, source_root, direct))

    # Fixed point across modules covers imported historical/closeout helpers,
    # nested wrappers, and class-level method aliases used by fixture importers.
    changed = True
    while changed:
        changed = False
        for _, tree, _, source_root, direct in modules:
            for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                needed = function in direct
                parameters = {arg.arg for arg in function.args.args} - {'self', 'cls'}
                for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                    name = terminal(call.func)
                    needed |= name in source_helpers or (name in wrappers and
                               any(source_root(arg) for arg in call.args))
                    if (name in wrappers and function.name not in wrappers and
                            any(terminal(part) in parameters for arg in call.args
                                for part in ast.walk(arg))):
                        wrappers.add(function.name)
                        changed = True
                if (needed and not function.name.startswith('test_')
                        and function.name not in ('setUp', 'setUpClass', 'setUpModule')
                        and function.name not in source_helpers):
                    source_helpers.add(function.name)
                    changed = True
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and terminal(node.value) in source_helpers:
                    for target in node.targets:
                        if terminal(target) and terminal(target) not in source_helpers:
                            source_helpers.add(terminal(target))
                            changed = True

    results = {}
    for path, tree, parents, source_root, direct in modules:
        tuple_names = {terminal(target) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and isinstance(node.value, ast.Call)
                       and terminal(node.value.func) == 'committed_git_tuple' for target in node.targets}
        def declaration(node):
            decorators = getattr(node, 'decorator_list', [])
            for deco in decorators:
                text = ast.unparse(deco)
                if (isinstance(deco, ast.Call) and terminal(deco.func) == 'skipIf'
                        and 'committed_git_tuple(' in text and 'is None' in text
                        and 'COMMITTED_GIT_REASON' in text):
                    return True
            if not isinstance(node, ast.FunctionDef):
                return False
            for condition in (n for n in ast.walk(node) if isinstance(n, ast.If)):
                text = ast.unparse(condition.test)
                if 'is None' not in text or not ('committed_git_tuple(' in text or
                        any(terminal(n) in tuple_names for n in ast.walk(condition.test))):
                    continue
                for statement in condition.body:
                    for call in (n for n in ast.walk(statement) if isinstance(n, ast.Call)):
                        if terminal(call.func) in ('SkipTest', 'skipTest') and any(
                                terminal(arg) == 'COMMITTED_GIT_REASON' for arg in call.args):
                            return True
            return False
        module_guard = any(isinstance(n, ast.FunctionDef) and n.name == 'setUpModule'
                           and declaration(n) for n in tree.body)
        def needs_source(function):
            return function in direct or any(
                terminal(call.func) in source_helpers or
                (terminal(call.func) in wrappers and any(source_root(arg) for arg in call.args))
                for call in ast.walk(function) if isinstance(call, ast.Call))
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


class SourceGitGuardTests(unittest.TestCase):
    def test_source_git_dependencies_are_declared(self):
        sources = {path.relative_to(ROOT).as_posix(): path.read_text(encoding='utf-8-sig')
                   for path in test_paths(ROOT)}
        self.assertEqual([], missing_declarations(sources))

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
