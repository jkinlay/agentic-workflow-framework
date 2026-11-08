"""Installed-only selection validation and discovery; no source dependencies."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import unittest

FIXTURE_PREREQUISITES = {
    'test_awf36_controller_automation': ('test_awf36_automation_fixtures',),
}
SHARDS = tuple(range(9))
UPGRADE_MATRIX = '.agentic/tests/test_upgrade_matrix.py'


def load_fixture_prerequisites(root, selected_path):
    """Import required installed fixtures before the consumer's cases are loaded."""
    for name in FIXTURE_PREREQUISITES.get(Path(selected_path).stem, ()):
        expected = (Path(root) / '.agentic/tests' / (name + '.py')).resolve()
        if not expected.is_file():
            continue
        module = importlib.import_module(name)
        if Path(module.__file__).resolve() != expected:
            raise ValueError('fixture prerequisite origin mismatch: ' + name)


def assert_isolation(root):
    """Refuse preloaded source AWF or test helpers; never repair sys.modules."""
    root = Path(root).resolve()
    library = root / '.agentic/lib'
    tests = root / '.agentic/tests'
    import agentic
    if not getattr(agentic, '__file__', None) or not Path(agentic.__file__).resolve().is_relative_to(library):
        raise ValueError('source contamination: agentic is outside installed library')
    helper_names = {p.stem for p in tests.rglob('*.py')}
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        origin = getattr(module, '__file__', None)
        if name == 'agentic' or name.startswith('agentic.'):
            if not origin or not Path(origin).resolve().is_relative_to(library):
                raise ValueError('source contamination: ' + name)
        elif name.rsplit('.', 1)[-1] in helper_names:
            if not origin or not Path(origin).resolve().is_relative_to(tests):
                raise ValueError('test/helper contamination: ' + name)
    return 'PASS'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _paths(value):
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise ValueError('module paths must be a list of strings')
    for p in value:
        path = PurePosixPath(p)
        if (not re.fullmatch(r'\.agentic/tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py', p)
                or path.is_absolute() or '..' in path.parts or str(path) != p):
            raise ValueError('invalid installed test path: ' + p)
    if value != sorted(set(value)):
        raise ValueError('module paths must be unique and sorted')
    return set(value)


def validate_selection(raw, expected_digest, inventory, manifest_digest):
    if not re.fullmatch('[0-9a-f]{64}', expected_digest or '') or hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError('selection digest mismatch')
    plan = json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    keys = {'format', 'allocation_policy', 'source_manifest_sha256', 'inventory',
            'shards', 'split', 'zero_test_modules'}
    if (not isinstance(plan, dict) or set(plan) != keys
            or plan['format'] != 'awf-installed-selection-1'
            or plan['allocation_policy'] != 'windows-unittest-shards-9'
            or plan['source_manifest_sha256'] != manifest_digest):
        raise ValueError('invalid selection format or manifest digest')
    if _paths(plan['inventory']) != set(inventory):
        raise ValueError('selection inventory differs from installed inventory')
    if not isinstance(plan['shards'], dict) or set(plan['shards']) != {str(n) for n in SHARDS}:
        raise ValueError('selection must contain all nine shards')
    counts = Counter()
    for paths in plan['shards'].values():
        if not _paths(paths) <= set(inventory):
            raise ValueError('unknown selected module')
        counts.update(paths)
    split = plan['split']
    if not isinstance(split, dict) or set(split) != {UPGRADE_MATRIX} or UPGRADE_MATRIX not in inventory:
        raise ValueError('invalid split module')
    spec = split[UPGRADE_MATRIX]
    if (not isinstance(spec, dict) or set(spec) != {'shards', 'first_half'}
            or spec['shards'] != [1, 8] or any(type(n) is not int for n in spec['shards'])
            or not isinstance(spec['first_half'], list) or not spec['first_half']
            or any(not isinstance(n, str) or not re.fullmatch('test_[A-Za-z0-9_]+', n) for n in spec['first_half'])
            or spec['first_half'] != sorted(set(spec['first_half']))):
        raise ValueError('invalid split metadata')
    if counts != Counter({p: 2 if p == UPGRADE_MATRIX else 1 for p in inventory}):
        raise ValueError('selection has duplicate or missing membership')
    if [n for n in SHARDS if UPGRADE_MATRIX in plan['shards'][str(n)]] != [1, 8]:
        raise ValueError('invalid split owners')
    zero = _paths(plan['zero_test_modules'])
    if not zero <= set(inventory):
        raise ValueError('unknown zero-test module')
    return plan


def installed_inventory(root):
    root = Path(root).resolve()
    receipt = json.loads((root / '.agentic/installed-manifest.json').read_bytes(), object_pairs_hook=_unique_object)
    inventory = sorted(p for p in receipt['immutable_files'] if p.startswith('.agentic/tests/')
                       and Path(p).name.startswith('test_') and p.endswith('.py'))
    actual = sorted(p.relative_to(root).as_posix() for p in (root / '.agentic/tests').rglob('test_*.py'))
    if actual != inventory:
        raise ValueError('installed test inventory differs from receipt')
    _paths(inventory)
    return inventory


def flatten(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


class RecordedSuite(unittest.TestSuite):
    def __init__(self, tests, path, loader):
        super().__init__([tests])
        self.path, self.loader = path, loader

    def run(self, result, debug=False):
        before = (result.testsRun, len(result.errors), len(result.skipped))
        result = super().run(result, debug)
        if before == (result.testsRun, len(result.errors), len(result.skipped)) and self.path not in self.loader.zero_test_modules:
            raise ValueError('empty execution: ' + self.path)
        self.loader.completed.append(self.path)
        return result


class InstalledLoader(unittest.TestLoader):
    def __init__(self, root, plan, shard):
        super().__init__()
        self.root = Path(root).resolve()
        self.plan, self.shard = plan, shard
        self.selected = plan['shards'][str(shard)]
        self.discovered, self.completed = [], []
        self.split_cases, self.split_all_cases, self.zero_test_modules = {}, {}, {}

    def _match_path(self, path, full_path, pattern):
        relative = Path(full_path).relative_to(self.root).as_posix()
        return super()._match_path(path, full_path, pattern) and relative in self.selected

    def _find_test_path(self, full_path, pattern):
        relative = Path(full_path).relative_to(self.root).as_posix()
        if relative in self.selected:
            if not Path(full_path).resolve().is_relative_to(self.root / '.agentic/tests'):
                raise ValueError('test origin outside installed tests')
            load_fixture_prerequisites(self.root, relative)
        tests, recurse = super()._find_test_path(full_path, pattern)
        if relative in self.selected and tests is not None:
            if not tests.countTestCases():
                if relative not in self.plan['zero_test_modules']:
                    raise ValueError('empty discovery: ' + relative)
                self.zero_test_modules[relative] = 0
            if relative in self.plan['split']:
                cases = sorted(flatten(tests), key=lambda case: case.id())
                if any(type(case).__module__ == 'unittest.loader' for case in cases):
                    raise ValueError('split import error or skip carrier')
                names = [case._testMethodName for case in cases]
                first = set(self.plan['split'][relative]['first_half'])
                if len(names) != len(set(names)) or not first <= set(names) or not set(names) - first:
                    raise ValueError('split overlap/omission or empty half')
                self.split_all_cases[relative] = sorted(case.id() for case in cases)
                chosen = [case for case in cases if (case._testMethodName in first) == (self.shard == 1)]
                self.split_cases[relative] = sorted(case.id() for case in chosen)
                tests = unittest.TestSuite(chosen)
            self.discovered.append(relative)
            tests = RecordedSuite(tests, relative, self)
        return tests, recurse

    def reconcile(self):
        if (Counter(self.selected) != Counter(self.discovered)
                or Counter(self.selected) != Counter(self.completed) or self.errors):
            raise ValueError('selected/discovered/completed mismatch or import errors')
