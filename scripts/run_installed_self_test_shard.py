#!/usr/bin/env python3
"""Build a manifest-only Linux fixture and launch one isolated installed shard."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from installed_self_test_fixture import (prepare_project, assert_unborn_head,
                                         assert_committed_head_skips)
from installed_self_test_capture import run_bounded
from agentic.child_process import child_env

ROOT = Path(__file__).resolve().parents[1]
DEADLINE_SECONDS = 50 * 60
CLEANUP_RESERVE_SECONDS = 30


def failure_report(path, shard, reason):
    """Never replace a real report, including partial or malformed evidence."""
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(dict(scope='installed-shard', status='FAILED', suite_complete=False,
                           release_qualified=False, shard=shard, shard_count=9,
                           selected_modules=[], discovered_modules=[], completed_modules=[],
                           tests=dict(skipped=[]), reason=reason), stream)
    except FileExistsError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--shard', type=int, choices=range(9), required=True)
    parser.add_argument('--selection-file', type=Path, required=True)
    parser.add_argument('--expected-selection-sha256', required=True)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + DEADLINE_SECONDS
    artifacts = args.artifacts.resolve()
    project = args.work_dir.resolve() / 'project'
    selection = args.selection_file.resolve()
    report_path = artifacts / 'self-test.json'

    def remaining(cap):
        seconds = deadline - time.monotonic() - CLEANUP_RESERVE_SECONDS
        if seconds <= 0:
            raise TimeoutError('installed shard overall deadline exhausted')
        return min(cap, seconds)

    def setup_run(command, *, timeout, env=None, **kwargs):
        return subprocess.run(command, timeout=remaining(timeout), env=child_env(env), **kwargs)

    try:
        artifacts.mkdir(parents=True, exist_ok=True)
        if report_path.exists():
            raise ValueError('self-test report already exists')
        # child_env strips provider controls; PYTHON* needs its stricter isolated
        # process rule, including mixed-case names inherited on Windows hosts.
        env = child_env({key: value for key, value in os.environ.items()
                         if not key.upper().startswith('PYTHON')})
        with (artifacts / 'installer.log').open('a', encoding='utf-8') as log:
            with redirect_stdout(log), redirect_stderr(log):
                remaining(DEADLINE_SECONDS)
                interpreter = prepare_project(ROOT, project, run=setup_run, env=env, bin_dir='bin')
        # No commit: this probe must remain immediately before the installed run.
        assert_unborn_head(project, run=setup_run, env=env)
        done = run_bounded(
            [str(interpreter), '-B', '-I', str(project / '.agentic/scripts/self_test.py'),
             '--checks-only', '--report', str(report_path), '--shard', str(args.shard),
             '--selection-file', str(selection),
             '--expected-selection-sha256', args.expected_selection_sha256],
            cwd=project, env=env, capture_output=True, text=True,
            timeout=remaining(DEADLINE_SECONDS), evidence_parent=artifacts / 'capture')
        if done.returncode != 0:
            raise ValueError(f'installed self-test exited {done.returncode}')
        report = json.loads(report_path.read_bytes())
        if (report.get('status') != 'SHARD_PASS' or report.get('scope') != 'installed-shard'
                or type(report.get('shard')) is not int or report['shard'] != args.shard
                or report.get('suite_complete') is not False or report.get('release_qualified') is not False):
            raise ValueError('installed self-test did not pass this shard')
        tests = report.get('tests', {})
        if tests.get('failures') != 0 or tests.get('errors') != 0 or not isinstance(tests.get('skipped'), list):
            raise ValueError('installed self-test reported failures, errors or missing skips')
        for item in tests['skipped']:
            if (not isinstance(item, dict) or not isinstance(item.get('test'), str) or not item['test'].strip()
                    or not isinstance(item.get('reason'), str) or not item['reason'].strip()):
                raise ValueError('installed self-test reported an unnamed skip')
        assert_committed_head_skips(report)
        print(json.dumps(tests['skipped'], sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AssertionError, subprocess.SubprocessError) as exc:
        failure_report(report_path, args.shard, str(exc))
        print(f'Linux installed shard FAILED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
