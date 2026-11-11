"""Installed tests must not depend on unshipped source scripts without a gate."""
import ast
from pathlib import Path
import unittest


# Retained in installed copies, where scripts/ cannot supply its own inventory.
# The source-tree check below requires this inventory to stay complete.
SOURCE_SCRIPT_MODULES = frozenset('''
ac42_e2e bootstrap_project build_release build_skill_distribution
check_installed_self_test_shards plan_installed_self_test probe_installed_self_test_isolation
generate_contracts generate_examples generate_interaction generate_prompts
generate_review_loop generate_rules_activation installed_self_test_capture installed_self_test_fixture
manifest_merge_driver publish_catalog publish_release qualify_upgrade
release_hygiene release_modes release_review run_publication_pre_controller
run_installed_self_test_shard run_windows_unittests safe_materialize validate_archive validate_git_checkout
'''.split())


def source_script_dependencies(sources, modules=SOURCE_SCRIPT_MODULES):
    """Find imports executed before a source gate, including deferred helpers.

    A class decorator cannot protect a module-level import. Deferred imports
    must have a gated enclosing test/class or exclusively gated helper callers.
    Follow callers across test support modules as well as within each module.
    """
    trees = {name: ast.parse(source) for name, source in sources.items()}
    parents = {child: parent for tree in trees.values() for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    functions = {}
    owners = {}
    imports = {}

    def terminal(node):
        return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''

    def owner(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return None

    def gated(node):
        while node is not None:
            if any(isinstance(d, ast.Call) and terminal(d.func) == 'skip_unless_source_repo'
                   for d in getattr(node, 'decorator_list', ())):
                return True
            node = parents.get(node)
        return False

    def conditional_import(node):
        child = node
        while child in parents:
            parent = parents[child]
            if (isinstance(parent, ast.If) and terminal(parent.test) == 'AWF_SOURCE_REPOSITORY'
                    and child in parent.body):
                return True
            child = parent
        return False

    for name, tree in trees.items():
        imports[name] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[name, node.name] = node
                owners[node] = name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports[name][alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports[name][alias.asname or alias.name] = (alias.name, None)

    callers = {node: [] for node in owners}
    for name, tree in trees.items():
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            target = None
            if isinstance(call.func, ast.Name):
                target = functions.get((name, call.func.id))
                module, symbol = imports[name].get(call.func.id, (None, None))
                target = target or functions.get((module, symbol))
            elif isinstance(call.func, ast.Attribute):
                if isinstance(call.func.value, ast.Name) and call.func.value.id in ('self', 'cls'):
                    target = functions.get((name, call.func.attr))
                else:
                    module, _ = imports[name].get(terminal(call.func.value), (None, None))
                    target = functions.get((module, call.func.attr))
            if target is not None:
                callers[target].append(owner(call))

    def protected(function, seen=frozenset()):
        if function is None or function in seen:
            return False
        if gated(function):
            return True
        incoming = callers[function]
        return bool(incoming) and all(protected(caller, seen | {function}) for caller in incoming)

    violations = []
    for name, tree in trees.items():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = ([alias.name for alias in node.names] if node.module == 'scripts'
                         else [node.module.removeprefix('scripts.').split('.')[0]])
            elif isinstance(node, ast.Import):
                names = [alias.name.removeprefix('scripts.').split('.')[0] for alias in node.names]
            for module in sorted(set(names) & modules):
                deferred = conditional_import(node)
                if deferred:
                    # An import condition avoids a loader error, but its users
                    # must also be skipped rather than fail with an unset name.
                    aliases = {alias.asname or alias.name.split('.')[0]
                               for alias in node.names}
                    uses = [part for part in ast.walk(tree) if isinstance(part, ast.Name)
                            and isinstance(part.ctx, ast.Load) and part.id in aliases]
                    deferred = all(conditional_import(use) or protected(owner(use)) for use in uses)
                if not deferred and not protected(owner(node)):
                    violations.append((name, node.lineno, module))
    return sorted(violations)


class SourceScriptDependencyGuardTests(unittest.TestCase):
    def test_top_level_import_is_not_protected_by_a_later_class_gate(self):
        source = ('from qualify_upgrade import inventory\n'
                  '@source_only.skip_unless_source_repo("needs comparator")\n'
                  'class Cases:\n    def test_compare(self): pass\n')
        self.assertEqual(source_script_dependencies({'case': source}), [('case', 1, 'qualify_upgrade')])

    def test_deferred_imports_require_every_caller_to_be_gated(self):
        helper = 'def snapshot():\n    import qualify_upgrade\n'
        caller = ('from helper import snapshot\n'
                  '@skip_unless_source_repo("needs source comparator")\n'
                  'class Cases:\n    def test_compare(self): snapshot()\n')
        self.assertEqual(source_script_dependencies({'helper': helper, 'case': caller}), [])
        self.assertTrue(source_script_dependencies({'helper': helper, 'case': caller +
                        'def test_installed(): snapshot()\n'}))

    def test_conditional_import_and_qualified_script_names(self):
        self.assertEqual(source_script_dependencies({'case':
            'if AWF_SOURCE_REPOSITORY:\n    import qualify_upgrade\n'}), [])
        for source in ('if True:\n    import qualify_upgrade\n',
                       'if AWF_SOURCE_REPOSITORY:\n    import qualify_upgrade\n'
                       'def test_installed(): qualify_upgrade.inventory()\n',
                       'import scripts.qualify_upgrade\n', 'from scripts import build_release\n',
                       '# skip_unless_source_repo\nimport build_release\n'):
            with self.subTest(source=source):
                self.assertTrue(source_script_dependencies({'case': source}))

    def test_inventory_covers_source_scripts_when_present(self):
        root = Path(__file__).resolve().parents[2]
        shipped = {path.stem for path in (root / '.agentic').rglob('*.py')}
        source = {path.stem for path in (root / 'scripts').glob('*.py')} - shipped
        self.assertLessEqual(source, SOURCE_SCRIPT_MODULES)

    def test_installed_tests_do_not_import_unshipped_scripts_without_a_source_gate(self):
        directory = Path(__file__).resolve().parent
        sources = {'.'.join(path.relative_to(directory).with_suffix('').parts):
                   path.read_text(encoding='utf-8') for path in sorted(directory.rglob('*.py'))}
        violations = source_script_dependencies(sources)
        self.assertEqual(violations, [], 'Move shared helpers into installed code or defer imports '
                         'behind source_only.skip_unless_source_repo: ' + ', '.join(
                             f'{name}:{line}: {module}' for name, line, module in violations))


if __name__ == '__main__':
    unittest.main()
