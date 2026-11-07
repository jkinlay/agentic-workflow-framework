"""Real local Git facts for host/usage fixtures; never contact a provider."""
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess

from agentic.canonical import canonical
from agentic.child_process import isolated_git_env


def publish_fixture_settings(case, root, governance, value):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    def git(*args):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=True, capture_output=True)
    if not (root / '.git').exists():
        git('init', '-q', '-b', governance['github']['base_branch'])
    from agentic.operating import _json
    (root / 'OPERATING_CONFIG.yaml').write_bytes(_json(value))
    git('add', 'OPERATING_CONFIG.yaml')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture operating settings', '--allow-empty')
    git('update-ref', 'refs/remotes/origin/' + governance['github']['base_branch'], 'HEAD')


def accepted_fixture_snapshot(case, root, value, governance):
    from agentic.operating import validate_operating
    publish_fixture_settings(case, root, governance, value)
    return replace(validate_operating(value, governance),
        _provenance_json=canonical({'path': str(Path(root) / 'OPERATING_CONFIG.yaml')}))
