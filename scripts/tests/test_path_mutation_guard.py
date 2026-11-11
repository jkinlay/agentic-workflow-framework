"""Every shipped pathname mutation needs an exact, justified review entry."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name('path_mutation_allowlist.json')
ROOTS = ('.agentic/lib/agentic', 'scripts', 'global/awf-portable/scripts')
METHODS = {'unlink', 'rmdir', 'rmtree', 'chmod', 'chown', 'utime',
           'lchmod', 'setxattr', 'removexattr', 'copystat', 'copymode',
           'TemporaryDirectory', 'NamedTemporaryFile', 'mkdtemp', 'copy2', 'copyfile', 'copytree',
           'DeleteFileW', 'DeleteFileA', 'RemoveDirectoryW', 'RemoveDirectoryA',
           'MoveFileW', 'MoveFileA', 'MoveFileExW', 'MoveFileExA', 'ReplaceFileW', 'ReplaceFileA',
           'SetFileAttributesW', 'SetFileAttributesA', 'SetFileSecurityW', 'SetFileSecurityA',
           'SetNamedSecurityInfoW', 'SetNamedSecurityInfoA'}


def product_modules(root):
    for directory in ROOTS:
        for path in sorted((root / directory).rglob('*.py')):
            relative = path.relative_to(root)
            if 'tests' not in relative.parts:
                yield path


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
        return ast.unparse(node)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = name(node.func)
        leaf = function.rsplit('.', 1)[-1]
        if (leaf in METHODS or function in ('os.remove', 'os.unlink', 'os.rmdir',
                'os.replace', 'os.rename', 'shutil.move')
                or leaf == 'rename'):
            encoded = ast.dump(node, annotate_fields=True, include_attributes=False).encode()
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
        expected[row['path'], row['fingerprint']] += 1
    return dict(unreviewed=list((actual - expected).elements()), stale=list((expected - actual).elements()))


class PathMutationGuardTests(unittest.TestCase):
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
