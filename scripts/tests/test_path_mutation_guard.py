"""Every shipped pathname mutation needs an exact, justified review entry."""
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name('path_mutation_allowlist.json')
ROOTS = ('.agentic/lib/agentic', 'scripts', 'global/awf-portable/scripts')
METHODS = {'unlink', 'rmdir', 'rmtree', 'chmod', 'chown', 'utime',
           'lchmod', 'setxattr', 'removexattr', 'copystat', 'copymode',
           'TemporaryDirectory', 'TemporaryFile', 'NamedTemporaryFile', 'mkdtemp', 'copy2', 'copyfile', 'copytree',
           'DeleteFileW', 'DeleteFileA', 'RemoveDirectoryW', 'RemoveDirectoryA',
           'MoveFileW', 'MoveFileA', 'MoveFileExW', 'MoveFileExA', 'ReplaceFileW', 'ReplaceFileA',
           'SetFileAttributesW', 'SetFileAttributesA', 'SetFileSecurityW', 'SetFileSecurityA',
           'SetNamedSecurityInfoW', 'SetNamedSecurityInfoA', 'replace', 'rename_checked',
           'write_bytes', 'write_text', '<dynamic-getattr>'}


def product_modules(root):
    for directory in ROOTS:
        for path in sorted((root / directory).rglob('*.py')):
            relative = path.relative_to(root)
            if 'tests' not in relative.parts:
                yield path


def normalized_ast(node):
    """Stable semantic fields, independent of ast.dump's optional-field display.

    Python 3.13 omits empty optional fields in dumps. Ignore empty fields on all
    versions, retaining every populated field, node type and argument order.
    """
    if isinstance(node, ast.AST):
        return [type(node).__name__, {key: normalized_ast(value)
                for key, value in ast.iter_fields(node) if value is not None and value != []}]
    if isinstance(node, list):
        return [normalized_ast(value) for value in node]
    if isinstance(node, bytes):
        return {'bytes': node.hex()}
    if node is Ellipsis:
        return {'ellipsis': True}
    if isinstance(node, complex):
        return {'complex': repr(node)}
    return node


def mutation_calls(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update({item.asname or item.name: item.name for item in node.names})
        elif isinstance(node, ast.ImportFrom):
            imports.update({item.asname or item.name: (node.module or '') + '.' + item.name
                            for item in node.names})
    def name(node):
        if isinstance(node, ast.Name):
            return imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + '.' + node.attr
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'getattr' and len(node.args) >= 2):
            attribute = node.args[1]
            return name(node.args[0]) + '.' + (attribute.value if isinstance(attribute, ast.Constant)
                and isinstance(attribute.value, str) else '<dynamic-getattr>')
        return ast.unparse(node)
    # Resolve chains of assignment aliases as well as import aliases. Preserve
    # every possible binding: an alias cannot hide a mutator through reassignment.
    aliases = {}
    for _ in range(len(list(ast.walk(tree)))):
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                resolved = name(node.value)
                for target in targets:
                    if isinstance(target, ast.Name) and target.id != resolved:
                        values = aliases.setdefault(target.id, set())
                        if resolved not in values:
                            values.add(resolved)
                            changed = True
                        # Mutating aliases take precedence over nonmutating ones.
                        if resolved == 'os' or resolved.rsplit('.', 1)[-1] in METHODS | {'rename', 'remove', 'CreateFileW', 'CreateFileA', 'open'}:
                            imports[target.id] = resolved
        if not changed:
            break
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)

    def flags(node, seen=frozenset()):
        resolved = name(node)
        if resolved.startswith('os.O_'):
            return getattr(os, resolved[3:], None)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left, right = flags(node.left, seen), flags(node.right, seen)
            return None if left is None or right is None else left | right
        if isinstance(node, ast.Name) and node.id not in seen and node.id in assignments:
            values = [flags(value, seen | {node.id}) for value in assignments[node.id]]
            return None if None in values else __import__('functools').reduce(int.__or__, values, 0)
        return None

    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = name(node.func)
        leaf = function.rsplit('.', 1)[-1]
        overwrite = False
        if function == 'os.open':
            argument = next((keyword.value for keyword in node.keywords if keyword.arg == 'flags'),
                            node.args[1] if len(node.args) > 1 else ast.Constant(None))
            value = flags(argument)
            overwrite = value is None or bool(value & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC | os.O_CREAT | os.O_APPEND))
        elif leaf == 'open':
            mode_index = 1 if function in {'open', 'builtins.open', 'io.open'} else 0
            mode = next((keyword.value for keyword in node.keywords if keyword.arg == 'mode'),
                        node.args[mode_index] if len(node.args) > mode_index else ast.Constant('r'))
            overwrite = (not isinstance(mode, ast.Constant) or not isinstance(mode.value, str)
                         or ('x' not in mode.value and any(flag in mode.value for flag in 'wa+')))
        if leaf in {'CreateFileW', 'CreateFileA'}:
            disposition = node.args[4] if len(node.args) > 4 else None
            overwrite = not isinstance(disposition, ast.Constant) or disposition.value not in {1, 3, 4}
        builder = (leaf == 'create' and isinstance(node.func, ast.Attribute)
                   and isinstance(node.func.value, ast.Call)
                   and name(node.func.value.func) in {'venv.EnvBuilder', '_RuntimeBuilder'})
        if (leaf in METHODS or overwrite or builder or function in ('os.remove', 'os.unlink', 'os.rmdir',
                'os.replace', 'os.rename', 'shutil.move')
                or leaf == 'rename'):
            encoded = json.dumps(normalized_ast(node), sort_keys=True,
                                 separators=(',', ':'), ensure_ascii=True).encode('ascii')
            calls.append(dict(line=node.lineno, call=ast.unparse(node),
                              fingerprint=hashlib.sha256(encoded).hexdigest()))
    return sorted(calls, key=lambda row: row['line'])


def violations(root, allowlist):
    actual = Counter((path.relative_to(root).as_posix(), call['fingerprint'])
        for path in product_modules(root) for call in mutation_calls(path))
    expected = Counter()
    for row in allowlist:
        if not row.get('justification', '').strip() or '\n' in row['justification']:
            raise ValueError('Every allowlist entry requires a one-line justification')
        if row.get('disposition') not in {'SAFE', 'SAFE-UNDER-BOUNDARY', 'FIXED', 'OUT-OF-SCOPE', 'NON-MUTATING'}:
            raise ValueError('Unresolved or unknown mutation disposition: ' + str(row.get('disposition')))
        expected[row['path'], row['fingerprint']] += 1
    return dict(unreviewed=list((actual - expected).elements()), stale=list((expected - actual).elements()))


class PathMutationGuardTests(unittest.TestCase):
    def test_fingerprint_ignores_optional_empty_fields_but_retains_semantics(self):
        call = ast.parse('os.replace(source, destination, src_dir_fd=held)').body[0].value
        same = ast.parse('os.replace(source, destination, src_dir_fd=held)').body[0].value
        same.args[0].synthetic_empty_field = []
        same.args[0]._fields += ('synthetic_empty_field',)
        self.assertEqual(normalized_ast(call), normalized_ast(same))
        for source in ('os.replace(destination, source, src_dir_fd=held)',
                       'os.replace(source, destination, dst_dir_fd=held)',
                       'os.replace(source, destination, src_dir_fd=other)'):
            self.assertNotEqual(normalized_ast(call), normalized_ast(ast.parse(source).body[0].value))

    def test_all_nine_original_publications_have_boundary_dispositions(self):
        expected = {'adoption_config.py:579', 'adoption_config.py:581', 'installer.py:2016',
                    'installer.py:2277', 'safeio.py:1249', 'safeio.py:1251',
                    'install_skill.py:559', 'install_skill.py:562', 'install_skill.py:565'}
        rows = json.loads(ALLOWLIST.read_text(encoding='utf-8'))
        observed = [site for row in rows for site in row.get('former_sites', [])]
        self.assertEqual(expected, set(observed))
        self.assertEqual(9, len(observed))
        for row in rows:
            if row.get('former_sites'):
                self.assertEqual('SAFE-UNDER-BOUNDARY', row['disposition'])

    def assert_injection(self, source, count=1):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'injected.py'
            path.write_text(source, encoding='utf-8')
            self.assertEqual(count, len(mutation_calls(path)), source)

    def test_gap_path_replace(self):
        self.assert_injection('from pathlib import Path\nPath("source").replace("owner")\n')

    def test_gap_temporary_file(self):
        self.assert_injection('from tempfile import TemporaryFile\nTemporaryFile()\n')

    def test_gap_getattr(self):
        self.assert_injection('import os\ngetattr(os, "unlink")("owner")\n')

    def test_gap_assignment_alias(self):
        self.assert_injection('import os\nerase = os.unlink\nerase("owner")\n')

    def test_gap_native_alias(self):
        self.assert_injection('erase = kernel.DeleteFileW\nerase("owner")\n')

    def test_gap_truncating_createfile(self):
        self.assert_injection('kernel.CreateFileW("owner", access, 7, None, 2, flags, None)\n')

    def test_gap_plain_overwrite(self):
        self.assert_injection('from pathlib import Path\nimport builtins as b\nfrom io import open as writer\nopen("owner", "wb")\nPath("owner").open("w")\nPath("owner").write_bytes(b"data")\nPath("owner").write_text("data")\nb.open("file", "wb")\nwriter("file", "w")\n', 6)

    def test_gap_os_open_mutating_flags_and_aliases(self):
        for flag in ('O_WRONLY', 'O_RDWR', 'O_TRUNC', 'O_CREAT', 'O_APPEND'):
            for source in (
                f'import os\nos.open("victim", os.{flag})',
                f'import os as native\nnative.open("victim", native.{flag})',
                f'import os\nnative = os\nnative.open("victim", native.{flag})',
                f'from os import open as writer, {flag} as flag\nwriter("victim", flag)',
                f'import os\nwriter = os.open\nflag = os.{flag}\nwriter("victim", flag)',
                f'import os\nflag = os.{flag} | os.O_RDONLY\nos.open("victim", flags=flag)',
            ):
                with self.subTest(flag=flag, source=source):
                    self.assert_injection(source)
        self.assert_injection('import os\nos.open("victim", os.O_WRONLY | os.O_TRUNC)')
        self.assert_injection('import os\nos.open("victim", os.O_RDONLY)', 0)

    def test_shipped_calls_have_exact_reviewed_allowlist_entries(self):
        result = violations(ROOT, json.loads(ALLOWLIST.read_text(encoding='utf-8')))
        self.assertEqual({'unreviewed': [], 'stale': []}, result)

    def test_scratch_copy_injected_deletion_is_red(self):
        # The injected call exists only in a scratch module, never product code.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ROOTS:
                (root / directory).mkdir(parents=True)
            target = root / 'scripts/synthetic.py'
            target.write_text('import os as native\nfrom os import chmod as mode\nnative.unlink("owner")\nmode("owner", 0o600)\nkernel.SetFileAttributesW("owner", 1)\n')
            result = violations(root, [])
            self.assertEqual(3, len(result['unreviewed']))
            self.assertEqual([], result['stale'])
            target = target.rename(root / 'scripts/test_named_product.py')
            self.assertEqual(3, len(violations(root, [])['unreviewed']),
                             'a product-root module cannot evade review through its filename')
            (root / 'scripts/tests').mkdir()
            target.rename(root / 'scripts/tests/test_synthetic.py')
            self.assertEqual({'unreviewed': [], 'stale': []}, violations(root, []))
