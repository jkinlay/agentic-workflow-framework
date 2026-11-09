"""Shared manifest-only fixture for source-side installed acceptance harnesses."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.agentic/lib'))
from agentic.installer import install
from agentic.runtime_commands import installed_paths

INSTALL_TIMEOUT_SECONDS = 180


def prepare_project(root, project, *, run, env, bin_dir=None, installer=install,
                    paths=installed_paths):
    """Install only manifest members, retaining the adopter's unborn-repo case."""
    pin = hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest()
    project.mkdir(parents=True)
    installer(root, project, pin, mode='install', discover=False)
    if (project / 'scripts').exists():
        raise AssertionError('source scripts were installed')
    if (project / 'MANIFEST.json').exists():
        raise AssertionError('source manifest was installed')
    (project / 'MANIFEST.json').write_text('{"adopter": "owned"}\n', encoding='utf-8')
    if (project / '.gitattributes').exists():
        raise AssertionError('root .gitattributes must be absent')
    if bin_dir is None:
        _, interpreter, _ = paths(project)
    else:
        # Explicit POSIX construction remains testable on a Windows writer host.
        interpreter = project / '.agentic/.venv' / bin_dir / 'python'
    base = os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable)
    run([base, '-B', '-m', 'venv', '--copies', str(project / '.agentic/.venv')],
        check=True, capture_output=True, env=env, timeout=600)
    run([str(interpreter), '-B', '-m', 'pip', 'install', '--disable-pip-version-check',
         '--require-hashes', '--only-binary=:all:', '-r',
         str(project / '.agentic/requirements.lock')],
        check=True, capture_output=True, env=env, timeout=1200)
    run(['git', '-c', 'user.name=AWF Test', '-c', 'user.email=awf@example.invalid',
         '-C', str(project), 'init', '-q', '-b', 'main'],
        check=True, capture_output=True, env=env, timeout=INSTALL_TIMEOUT_SECONDS)
    return interpreter


def assert_unborn_head(project, *, run, env):
    head = run(['git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'],
               capture_output=True, text=True, check=False, env=env,
               timeout=INSTALL_TIMEOUT_SECONDS)
    if head.returncode == 0:
        raise AssertionError('fixture unexpectedly has a commit')


def assert_committed_head_skips(report):
    """The unborn fixture must retain its specific skips in each owning shard."""
    skipped = report['tests']['skipped']
    selected = report['selected_modules']
    for module in ('test_continuous_controller', 'test_heavy_validation'):
        if f'.agentic/tests/{module}.py' in selected:
            matching = [item for item in skipped if module in item['test']]
            if len(matching) != 1:
                raise AssertionError(f'{module}: missing or duplicate committed-HEAD skip')
            if 'committed HEAD' not in matching[0]['reason']:
                raise AssertionError(f'{module}: wrong skip reason')
