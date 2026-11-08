#!/usr/bin/env python3
"""Audit a fresh installed process, stopping before any unittest cases run.

Use the installed project and selection produced by the Windows CI setup. The
source parent launches an isolated installed interpreter; the child imports no
source modules. Output and the companion self-test report must be new external
paths. This diagnostic produces no passing shard or suite acceptance evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.agentic/lib'))
from agentic.child_process import child_env


CHILD = r'''
import json,pathlib,runpy,sys
root=pathlib.Path(sys.argv[1]).resolve()
output=pathlib.Path(sys.argv[2]).resolve()
self_report=pathlib.Path(sys.argv[3]).resolve()
arguments=sys.argv[4:]
sys.path.insert(0,str(root/'.agentic/lib'))
from agentic import self_test_shards as policy
original=policy.assert_isolation
snapshots=[]
stopped=False
def audit(project):
    global stopped
    inventory=policy.isolation_inventory(project)
    snapshots.append(dict(stage='before_discovery' if not snapshots else 'after_discovery',
                          root_counts=inventory['root_counts'],path_counts=inventory['path_counts'],
                          allowed_roots=inventory['allowed_roots'],
                          root_boundary_checks=inventory['root_boundary_checks'],
                          refused=inventory['outside_roots'],loaded_modules=inventory['loaded_modules']))
    original(project)
    if len(snapshots)==2:
        stopped=True
        raise KeyboardInterrupt('origin probe stops before unittest execution')
policy.assert_isolation=audit
sys.argv=[str(root/'.agentic/scripts/self_test.py'),'--checks-only','--report',str(self_report),*arguments]
exit_code=1
try:
    runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as exc:
    exit_code=exc.code
finally:
    with output.open('x',encoding='utf-8') as stream:
        json.dump(dict(scope='installed-origin-probe',acceptance_evidence=False,
                       tests_executed=False,stopped_after_discovery=stopped,
                       self_test_exit=exit_code,snapshots=snapshots),stream,indent=2,sort_keys=True)
raise SystemExit(0 if stopped else 1)
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--selection-file', type=Path, required=True)
    parser.add_argument('--expected-selection-sha256', required=True)
    parser.add_argument('--shard', type=int, choices=range(9), default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    project, output = args.project.resolve(), args.output.resolve()
    self_report = output.with_name(output.stem + '-self-test.json')
    if output.is_relative_to(project) or output.exists() or self_report.exists():
        parser.error('Use new output and companion report paths outside the installed project')
    output.parent.mkdir(parents=True, exist_ok=True)
    interpreter = project / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # Match CI cleanup, including controls unknown to this source interpreter.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PYTHON')}
    completed = subprocess.run([str(interpreter), '-B', '-I', '-c', CHILD, str(project), str(output),
                           str(self_report), '--shard', str(args.shard), '--selection-file',
                           str(args.selection_file.resolve()), '--expected-selection-sha256',
                           args.expected_selection_sha256], cwd=project, env=child_env(env), check=False)
    if output.exists():
        report = json.loads(output.read_text(encoding='utf-8'))
        for snapshot in report['snapshots']:
            print('ORIGIN_ROOT_BOUNDARIES ' + json.dumps({'stage': snapshot['stage'],
                'checks': snapshot.get('root_boundary_checks', [])}, sort_keys=True))
            print('ORIGIN_ROOT_COUNTS ' + json.dumps({'stage': snapshot['stage'],
                'counts': snapshot['root_counts'], 'path_counts': snapshot.get('path_counts', {}),
                'allowed_roots': snapshot.get('allowed_roots', {})}, sort_keys=True))
            for module in snapshot['refused']:
                print('ORIGIN_OUTSIDE_ROOTS ' + json.dumps({'stage': snapshot['stage'], **module}, sort_keys=True))
            if not snapshot['refused']:
                print('ORIGIN_OUTSIDE_ROOTS ' + json.dumps({'stage': snapshot['stage'], 'modules': []}))
    elif completed.returncode:
        print('Origin inventory unavailable: installed child did not create the report')
    return completed.returncode


if __name__ == '__main__':
    raise SystemExit(main())
