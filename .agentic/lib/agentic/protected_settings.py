"""Protected operating values are read from the locally fetched default branch.

Git metadata must remain outside worker write authority. Repository rules and
the owner govern merging; this reader neither authenticates merges nor fetches.
"""
from copy import deepcopy
from pathlib import Path
import re
import shutil
import subprocess

from . import ValidationError
from .budgeting import fresh_settings
from .canonical import canonical, load_yaml
from .child_process import child_env, isolated_git_env

PROTECTED_DOMAINS = (('budgeting', fresh_settings),)
MAX_BYTES = 1024 * 1024


def unprotected_hash(value):
    from .canonical import fingerprint
    return fingerprint('operating-unprotected', {k: v for k, v in value.items()
                       if k not in dict(PROTECTED_DOMAINS)})


def audit_matches(value, event):
    """Keep unprotected audit binding when protected bytes change through Git.

    Pre-1.9.6 records have no projection hash. They may match their exact raw
    values, the legacy absent block or fresh defaults; arbitrary edits still fail.
    """
    from .canonical import fingerprint
    if event.get('unprotected_after_hash') is not None:
        return event['unprotected_after_hash'] == unprotected_hash(value)
    if event['after_hash'] == fingerprint('operating', value):
        return True
    legacy, fresh = deepcopy(value), deepcopy(value)
    for name, defaults in PROTECTED_DOMAINS:
        legacy.pop(name, None)
        fresh[name] = defaults()
    return any(event['after_hash'] == fingerprint('operating', candidate) for candidate in (legacy, fresh))


def _metadata(root):
    marker = Path(root) / '.git'
    if not marker.exists():
        return None
    if marker.is_dir():
        return marker
    text = marker.read_text(encoding='utf-8').strip()
    if not text.startswith('gitdir: ') or '\n' in text:
        raise ValidationError('Unreadable default-branch Git metadata')
    admin = (Path(root) / text[8:]).resolve(strict=True)
    common = admin / 'commondir'
    return (admin / common.read_text(encoding='utf-8').strip()).resolve(strict=True) if common.exists() else admin


def _git(root, *arguments):
    executable = shutil.which('git')
    if executable is None:
        raise ValidationError('Default-branch settings require Git; restore Git and fetch the default branch')
    try:
        result = subprocess.run([executable, '--no-replace-objects', '-c',
            'safe.directory=' + Path(root).resolve().as_posix(), '-C', str(root), *arguments],
            env=child_env(isolated_git_env()), capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError('Cannot read default-branch settings (' + type(exc).__name__ + '); restore Git access and fetch') from exc
    if result.returncode:
        raise ValidationError('Cannot read default-branch settings; restore the remote-tracking ref and OPERATING_CONFIG.yaml, then fetch')
    return result.stdout


def resolve_protected(root, governance, working):
    """Return effective values and provenance; never execute Git for fresh setup.

    Once remote metadata exists it must be read even if the working file says
    OFF: otherwise a worker could disable an accepted ON policy with defaults.
    """
    if not isinstance(working, dict):
        raise ValidationError('Operating settings must be an object; restore OPERATING_CONFIG.yaml')
    value = deepcopy(working)
    needed = any(name in working and canonical(working[name]) != canonical(defaults())
                 for name, defaults in PROTECTED_DOMAINS)
    try:
        metadata = _metadata(root)
        remote_metadata = metadata is not None and (
            (metadata / 'refs/remotes').exists() or (metadata / 'packed-refs').exists())
        commit = ref = None
        if remote_metadata:
            branch = governance.get('github', {}).get('base_branch')
            if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', branch):
                raise ValidationError('Invalid configured default-branch name')
            ref = 'refs/remotes/origin/' + branch
            commit = _git(root, 'rev-parse', '--verify', ref + '^{commit}').decode('ascii').strip()
            if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
                raise ValidationError('Invalid default-branch commit')
            object_name = commit + ':OPERATING_CONFIG.yaml'
            size = int(_git(root, 'cat-file', '-s', object_name).strip())
            if not 0 < size <= MAX_BYTES:
                raise ValidationError('Default-branch operating file exceeds its 1 MiB bound')
            raw = _git(root, 'cat-file', 'blob', object_name)
            if len(raw) != size:
                raise ValidationError('Default-branch operating file changed while reading')
            accepted = load_yaml(raw)
            # Validate the entire file, so missing/malformed bytes never look OFF.
            from .operating import validate_operating
            validate_operating(accepted, governance)
            for name, _defaults in PROTECTED_DOMAINS:
                if name in accepted:
                    value[name] = deepcopy(accepted[name])
                else:
                    value.pop(name, None)
            source = 'default branch'
        else:
            if needed:
                raise ValidationError('Nondefault protected settings require Git and a fetched default-branch remote-tracking ref; open a PR and fetch after merge')
            source = 'fresh defaults' if 'budgeting' in working else 'legacy retained'
    except (OSError, ValueError, UnicodeError, ValidationError) as exc:
        raise ValidationError('Protected default-branch settings unavailable: ' + str(exc)) from exc
    differences = [{'path': name, 'working': working.get(name), 'effective': value.get(name)}
                   for name, _defaults in PROTECTED_DOMAINS if working.get(name) != value.get(name)]
    return value, dict(source=source, commit=commit, ref=ref, differences=differences,
        next_action=('Working protected values differ; open a PR for the requested change, then fetch after owner merge'
                     if differences else 'Change protected settings through a PR; fetch after owner merge'))


def effective_snapshot(root, governance, snapshot):
    from dataclasses import replace
    from .operating import validate_operating
    value, report = resolve_protected(root, governance, snapshot.config)
    result = validate_operating(value, governance)
    return replace(result, last_change_id=snapshot.last_change_id,
        _provenance_json=canonical({**snapshot.provenance, 'protected_settings': report}))


def repository_rules_warning():
    # No network dependency on the show/default/OFF paths. A host may separately
    # attach a fresh rules observation; absence never claims applied controls.
    return dict(status='UNOBSERVED', warning='Repository rules UNOBSERVED; the owner and host rules govern merge. Offer the shipped ruleset.')
