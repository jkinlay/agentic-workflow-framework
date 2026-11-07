"""Real local Git facts for host/usage fixtures; never contact a provider."""
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess

from agentic.canonical import canonical
from agentic.child_process import isolated_git_env


def publish_fixture_settings(case, root, governance, value, *, preserve_checkout=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    def git(*args):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=True, capture_output=True)
    from agentic.operating import _json
    if ((root / '.git').exists() and (root / 'OPERATING_CONFIG.yaml').is_file()
            and (root / 'OPERATING_CONFIG.yaml').read_bytes() == _json(value)
            and (root / '.agentic/PROJECT_CONFIG.yaml').is_file()
            and (root / '.agentic/PROJECT_CONFIG.yaml').read_bytes() == _json(governance)):
        observed = subprocess.run([shutil.which('git'), '-C', str(root), 'show',
            'refs/remotes/origin/HEAD:OPERATING_CONFIG.yaml'], env=isolated_git_env(), capture_output=True)
        if observed.returncode == 0 and observed.stdout == _json(value):
            return  # Existing real default-ref bytes already represent this fixture.
    previous_head = (git('rev-parse', 'HEAD').stdout.decode().strip()
                     if preserve_checkout and (root / '.git').exists() else None)
    if not (root / '.git').exists():
        git('init', '-q', '-b', governance['github']['base_branch'])
    (root / 'OPERATING_CONFIG.yaml').write_bytes(_json(value))
    (root / '.agentic').mkdir(exist_ok=True)
    (root / '.agentic/PROJECT_CONFIG.yaml').write_bytes(_json(governance))
    git('add', 'OPERATING_CONFIG.yaml', '.agentic/PROJECT_CONFIG.yaml')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixture operating settings', '--allow-empty')
    git('update-ref', 'refs/remotes/origin/' + governance['github']['base_branch'], 'HEAD')
    git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/' + governance['github']['base_branch'])
    if previous_head:
        # A fetched settings merge advances policy, not the controller checkout.
        # Retain working proposed bytes and its original ownership tuple.
        git('reset', '--mixed', '-q', previous_head)


def accepted_fixture_snapshot(case, root, value, governance):
    from agentic.operating import validate_operating
    publish_fixture_settings(case, root, governance, value)
    return replace(validate_operating(value, governance),
        _provenance_json=canonical({'path': str(Path(root) / 'OPERATING_CONFIG.yaml')}))


def fixture_repository(root):
    """Observe the real disposable repository tuple, independent of source index."""
    from test_continuous_controller import REPOSITORY
    policy_root = Path(root)
    root = policy_root.parent / 'reservation-repository'
    if not (root / '.git').exists():
        from agentic.canonical import load_yaml
        publish_fixture_settings(None, root,
            load_yaml((policy_root / '.agentic/PROJECT_CONFIG.yaml').read_bytes()),
            load_yaml((policy_root / 'OPERATING_CONFIG.yaml').read_bytes()))
    def git(*args):
        return subprocess.run([shutil.which('git'), '-C', str(root), *args],
            env=isolated_git_env(), check=True, capture_output=True).stdout.decode().strip()
    head, tree = git('rev-parse', 'HEAD', 'HEAD^{tree}').splitlines()
    return dict(REPOSITORY, repository_root=root, repository_head_sha=head, repository_tree_sha=tree)


def fixture_reservations(root, request):
    """Observe actual disposable worktree changes, retaining reservation validation."""
    from agentic.canonical import fingerprint
    root = Path(root).parent / 'reservation-repository'
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
