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
    def git(*args, check=True):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=check, capture_output=True)
    if not (root / '.git').exists():
        git('init', '-q', '-b', governance['github']['base_branch'])
    prior = git('rev-parse', '--verify', 'HEAD', check=False)
    prior_head = prior.stdout.strip().decode() if prior.returncode == 0 else None
    from agentic.operating import _json
    (root / 'OPERATING_CONFIG.yaml').write_bytes(_json(value))
    (root / '.agentic').mkdir(exist_ok=True)
    (root / '.agentic/PROJECT_CONFIG.yaml').write_bytes(_json(governance))
    git('add', 'OPERATING_CONFIG.yaml', '.agentic/PROJECT_CONFIG.yaml')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture operating settings', '--allow-empty')
    git('update-ref', 'refs/remotes/origin/' + governance['github']['base_branch'], 'HEAD')
    git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/' + governance['github']['base_branch'])
    if prior_head:
        # A fetched policy merge need not move the controller checkout's HEAD.
        git('update-ref', 'HEAD', prior_head)


def accepted_fixture_snapshot(case, root, value, governance):
    from agentic.operating import validate_operating
    publish_fixture_settings(case, root, governance, value)
    return replace(validate_operating(value, governance),
        _provenance_json=canonical({'path': str(Path(root) / 'OPERATING_CONFIG.yaml')}))


def fixture_repository(root):
    """Observe the real disposable repository tuple, independent of source index."""
    from test_continuous_controller import REPOSITORY
    root = Path(root)
    def git(*args):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=True, capture_output=True).stdout.decode().strip()
    return dict(REPOSITORY, repository_root=root, repository_head_sha=git('rev-parse', 'HEAD'),
                repository_tree_sha=git('rev-parse', 'HEAD^{tree}'))


def fixture_reservations(root, request):
    """Observe actual disposable worktree changes, retaining reservation validation."""
    from agentic.canonical import fingerprint
    root = Path(root)
    def git(*args):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=True, capture_output=True).stdout.decode().split('\0')
    paths = sorted(set(p for p in [*git('diff', '--name-only', '-z', 'HEAD', '--'),
        *git('ls-files', '--others', '--exclude-standard', '-z')] if p))
    sources = []
    if paths:
        value = dict(schema_version=1, kind='worktree_diff', source='git',
            repository_id=request['repository_id'], head_sha=request['head_sha'],
            tree_sha=request['tree_sha'], observed_at=request['observed_at'],
            authenticated=True, complete=True, paths=paths,
            artifact_id=fingerprint('fixture-worktree-paths', paths))
        value['evidence_sha256'] = fingerprint('controller-reservation-source', value)
        sources.append(value)
    envelope = dict(schema_version=1, source='host_observation', repository_id=request['repository_id'],
        head_sha=request['head_sha'], tree_sha=request['tree_sha'], inventory_sha256=request['inventory_sha256'],
        observed_at=request['observed_at'], authenticated=True, complete=True, sources=sources)
    envelope['observation_sha256'] = fingerprint('controller-reservation-observation', envelope)
    return envelope
