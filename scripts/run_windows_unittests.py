#!/usr/bin/env python3
"""Run one of three deterministic Windows unittest module shards.

From the repository root: python -B scripts/run_windows_unittests.py --shard 0
Add --list to guard all allocations and print this shard without importing tests.
Paths are repository-relative POSIX strings sorted by Unicode code point, per
suite; index i belongs to shard i % 3. No inventory is frozen into this script.
Each suite uses a fresh interpreter, the repository cwd and its Linux CI
discovery top-level contract. Selection is logged before any child is launched;
discovery and completed module-suite records must match it exactly.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


SUITES = ('.agentic/tests', 'scripts/tests', 'global/awf/tests',
          'global/awf-portable/tests')
EXPLICIT_TOP = frozenset(SUITES[:2])
SHARDS = (0, 1, 2)
ROOT = Path(__file__).resolve().parents[1]


def inventory(root):
    """Enumerate all test_*.py files, independently of unittest discovery."""
    root = Path(root).resolve()
    modules = {}
    for suite in SUITES:
        start = root / suite
        if not start.is_dir():
            raise ValueError(f'missing suite root: {suite}')
        paths = []
        def walk_error(error):
            raise error
        for directory, _, names in os.walk(start, onerror=walk_error):
            for name in names:
                if name.startswith('test_') and name.endswith('.py'):
                    paths.append((Path(directory) / name).relative_to(root).as_posix())
        if not paths:
            raise ValueError(f'no test modules in suite root: {suite}')
        modules[suite] = sorted(paths)
    return modules


def allocate(modules):
    """Allocate independently within each suite using ordinal path ordering."""
    allocation = {}
    for suite, paths in modules.items():
        if len(paths) != len(set(paths)):
            raise ValueError(f'duplicate inventory paths: {suite}')
        ordered = sorted(paths)
        allocation[suite] = {shard: ordered[shard::3] for shard in SHARDS}
    return allocation


def guard(root, allocation):
    """Re-enumerate disk and reject incomplete, duplicate or invalid ownership."""
    expected = inventory(root)
    if set(allocation) != set(SUITES):
        raise ValueError('suite allocations do not match suite roots')
    for suite in SUITES:
        shards = allocation[suite]
        if (set(shards) != set(SHARDS)
                or any(type(index) is not int for index in shards)):
            raise ValueError(f'invalid shard indices: {suite}')
        counts = Counter(path for paths in shards.values() for path in paths)
        duplicate = sorted(path for path, count in counts.items() if count != 1)
        unknown = sorted(set(counts) - set(expected[suite]))
        missing = sorted(set(expected[suite]) - set(counts))
        if duplicate or unknown or missing:
            raise ValueError(f'{suite}: duplicate={duplicate}, unknown={unknown}, missing={missing}')


def reconcile(selected, executed):
    """Require exactly one execution record for each selected module."""
    if Counter(selected) != Counter(executed) or len(executed) != len(set(executed)):
        raise ValueError(f'execution mismatch: selected={selected}, recorded={executed}')


class RecordedSuite(unittest.TestSuite):
    """Record a module only after its discovered suite has returned."""

    def __init__(self, tests, path, executed):
        super().__init__([tests])
        self.path = path
        self.executed = executed

    def run(self, result, debug=False):
        before = (result.testsRun, len(result.errors), len(result.skipped))
        result = super().run(result, debug)
        after = (result.testsRun, len(result.errors), len(result.skipped))
        if before == after:
            raise ValueError(f'empty execution for selected module: {self.path}')
        self.executed.append(self.path)
        return result


class SelectedLoader(unittest.TestLoader):
    """Filter discovery paths, retaining unittest imports, hooks and fixtures."""

    def __init__(self, root, selected):
        super().__init__()
        self.root = root
        self.selected = set(selected)
        self.discovered = []
        self.executed = []

    def _match_path(self, path, full_path, pattern):
        # TestLoader explicitly provides this seam for alternate matching.
        relative = Path(full_path).relative_to(self.root).as_posix()
        return super()._match_path(path, full_path, pattern) and relative in self.selected

    def _find_test_path(self, full_path, pattern):
        # Wrap the suite returned by real discovery, including its import-error
        # or explicit SkipTest cases. Do not guess dotted module names.
        tests, recurse = super()._find_test_path(full_path, pattern)
        relative = Path(full_path).relative_to(self.root).as_posix()
        if relative in self.selected and tests is not None:
            if tests.countTestCases() == 0:
                raise ValueError(f'empty discovery for selected module: {relative}')
            self.discovered.append(relative)
            tests = RecordedSuite(tests, relative, self.executed)
        return tests, recurse


def run_suite(root, suite, selected):
    """Child entry point: use the suite's existing discovery start/top level."""
    # Match `python -m unittest` from the repository root rather than exposing
    # this script's directory as an extra import location in the child.
    sys.path[0] = str(root)
    loader = SelectedLoader(root, selected)
    if selected:
        kwargs = {'top_level_dir': str(root / suite)} if suite in EXPLICIT_TOP else {}
        tests = loader.discover(str(root / suite), **kwargs)
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        successful = result.wasSuccessful() and not loader.errors
        tests_run = result.testsRun
    else:
        # An empty slice is intentional, unlike a selected module loading zero tests.
        successful, tests_run = True, 0
    reconcile(selected, loader.discovered)
    reconcile(selected, loader.executed)
    print(json.dumps(dict(event='suite_result', suite=suite,
                          discovered=loader.discovered, executed=loader.executed,
                          tests_run=tests_run, successful=successful)), flush=True)
    return 0 if successful else 1


def run_shard(root, shard, allocation):
    """Run every suite even after failures, retaining the first nonzero exit."""
    status = 0
    for suite in SUITES:
        selected = allocation[suite][shard]
        command = [sys.executable, '-B', str(Path(__file__).resolve()),
                   '--root', str(root), '--shard', str(shard), '--_suite', suite]
        try:
            child = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                   check=False)
        except OSError as error:
            print(f'{suite}: child launch failed: {error}', file=sys.stderr)
            status = status or 1
            continue
        print(child.stdout, end='', flush=True)
        print(child.stderr, end='', file=sys.stderr, flush=True)
        status = status or child.returncode
        try:
            records = []
            for line in child.stdout.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get('event') == 'suite_result':
                    records.append(record)
            if len(records) != 1:
                raise ValueError('missing suite_result or duplicate suite_result records')
            record = records[0]
            if record['suite'] != suite:
                raise ValueError('suite_result has the wrong suite')
            reconcile(selected, record['discovered'])
            reconcile(selected, record['executed'])
            if record['successful'] is not True:
                status = status or 1
        except (KeyError, TypeError, ValueError) as error:
            print(f'{suite}: {error}', file=sys.stderr)
            status = status or 1
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='repository root')
    parser.add_argument('--shard', type=int, choices=SHARDS, required=True)
    parser.add_argument('--list', action='store_true', help='guard and list only; no suite execution')
    parser.add_argument('--_suite', choices=SUITES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        allocation = allocate(inventory(root))
        guard(root, allocation)
        if args._suite:
            return run_suite(root, args._suite, allocation[args._suite][args.shard])
        print(json.dumps(dict(event='selected', shard=args.shard,
                              modules={suite: allocation[suite][args.shard] for suite in SUITES})),
              flush=True)
        if args.list:
            return 0
        return run_shard(root, args.shard, allocation)
    except (OSError, ValueError, ImportError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
