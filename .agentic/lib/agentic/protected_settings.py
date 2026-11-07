"""Protected operating values are read from the locally fetched default branch.

Git metadata must remain outside worker write authority. Repository rules and
the owner govern merging. Reads never fetch; the controller refreshes separately.
"""
from copy import deepcopy
from pathlib import Path
import re

from . import ValidationError
from .budgeting import fresh_settings
from .canonical import canonical, load_yaml

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
    if common.exists():
        backlink = admin / 'gitdir'
        shared = (admin / common.read_text(encoding='utf-8').strip()).resolve(strict=True)
        if (admin.parent != shared / 'worktrees' or not backlink.is_file() or
                Path(backlink.read_text(encoding='utf-8').strip()).resolve(strict=True) != marker.resolve(strict=True)):
            raise ValidationError('Linked worktree Git metadata needs a matching common-directory gitfile backlink')
        return shared
    raise ValidationError('Linked Git metadata needs a matching worktree backlink')


def _git(root, *arguments, optional=False, maximum=MAX_BYTES):
    from .git_isolation import run_isolated_git
    return run_isolated_git(Path(root), list(arguments), maximum=maximum,
                            timeout=30, label='Default-branch settings Git read', missing_ok=optional)


def _blob(root, commit, path):
    # ls-tree distinguishes an absent path from a present but unavailable blob.
    entry = _git(root, 'ls-tree', commit, '--', path)
    if not entry:
        return None
    fields = entry.split()
    if len(fields) != 4 or fields[1] != b'blob':
        raise ValidationError('Default-branch operating path is not a regular blob')
    object_id = fields[2].decode('ascii')
    try:
        size = int(_git(root, 'cat-file', '-s', object_id).strip())
        if size == 0:
            raise ValidationError('Default-branch file is empty: ' + path)
        if size > MAX_BYTES:
            raise ValidationError('Default-branch file exceeds its 1 MiB bound: ' + path)
        raw = _git(root, 'cat-file', 'blob', object_id)
    except ValidationError as exc:
        raise ValidationError('Default-branch blob unavailable or invalid (' + path + '): ' + str(exc)
                              + '; missing partial-clone objects must be fetched explicitly') from exc
    if len(raw) != size:
        raise ValidationError('Default-branch blob changed while reading')
    return raw


def _default_ref(root):
    # Never read branch identity from working PROJECT_CONFIG. origin/HEAD wins.
    symbolic = _git(root, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD', optional=True)
    committed = None
    head = _git(root, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', optional=True)
    if head:
        raw = _blob(root, head.decode('ascii').strip(), '.agentic/PROJECT_CONFIG.yaml')
        if raw is not None:
            configuration = load_yaml(raw)
            github = configuration.get('github', {}) if isinstance(configuration, dict) else {}
            committed = github.get('base_branch') if isinstance(github, dict) else None
    valid = isinstance(committed, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', committed)
    ref = symbolic.decode('ascii').strip() if symbolic else None
    if ref is not None and (not ref.startswith('refs/remotes/origin/') or
                            not re.fullmatch(r'refs/remotes/origin/[A-Za-z0-9][A-Za-z0-9_./-]*', ref)):
        raise ValidationError('Invalid origin/HEAD default-branch reference')
    observation = 'Default branch from origin/HEAD' if ref else 'Default branch from committed HEAD PROJECT_CONFIG'
    if ref and valid and ref != 'refs/remotes/origin/' + committed:
        observation += '; origin/HEAD and committed github.base_branch disagree; origin/HEAD takes precedence'
    return ref or ('refs/remotes/origin/' + committed if valid else None), observation


def _fetch_time(metadata):
    from datetime import datetime, timezone
    path = metadata / 'FETCH_HEAD'
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.is_file() else None


def resolve_protected(root, governance, working):
    """Read immutable fetched policy; non-repositories invoke no external tool."""
    if not isinstance(working, dict):
        raise ValidationError('Operating settings must be an object; restore OPERATING_CONFIG.yaml')
    value = deepcopy(working)
    needed = any(name in working and canonical(working[name]) != canonical(defaults())
                 for name, defaults in PROTECTED_DOMAINS)
    commit = ref = fetched_at = None
    observation, branch_observation = 'no fetched default branch', 'No repository'
    try:
        metadata = _metadata(root)
        if metadata is not None:
            # Git resolves both loose/packed refs and reftable; storage is opaque.
            # Admit the executable even when the repository has no default ref.
            from .git_isolation import native_git_executable
            native_git_executable(Path(root))
            ref, branch_observation = _default_ref(root)
            raw_commit = _git(root, 'rev-parse', '--verify', '--quiet', ref + '^{commit}', optional=True) if ref else None
            commit = raw_commit.decode('ascii').strip() if raw_commit else None
            fetched_at = _fetch_time(metadata)
        if commit:
            if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
                raise ValidationError('Invalid default-branch commit')
            raw = _blob(root, commit, 'OPERATING_CONFIG.yaml')
            observation = 'file absent at default-branch commit; no accepted policy' if raw is None else 'accepted default-branch file'
            if raw is None:
                for name, defaults in PROTECTED_DOMAINS:
                    if name in value:
                        value[name] = defaults()
                source = 'fresh defaults' if 'budgeting' in value else 'legacy retained'
            else:
                accepted = load_yaml(raw)
                from .operating import validate_operating
                validate_operating(accepted, governance)
                for name, _defaults in PROTECTED_DOMAINS:
                    if name in accepted:
                        value[name] = deepcopy(accepted[name])
                    else:
                        value.pop(name, None)
                source = 'default branch' if 'budgeting' in accepted else 'legacy retained; default branch'
        else:
            if needed:
                raise ValidationError('Nondefault protected settings refused: no fetched default branch policy; open a PR and fetch after owner merge')
            source = 'fresh defaults' if 'budgeting' in working else 'legacy retained'
    except (OSError, ValueError, UnicodeError, ValidationError) as exc:
        raise ValidationError('Protected default-branch settings unavailable: ' + str(exc)) from exc
    differences = [{'path': name, 'working': working.get(name), 'effective': value.get(name)}
                   for name, _defaults in PROTECTED_DOMAINS if working.get(name) != value.get(name)]
    next_action = ('Working protected values differ; open a PR for the requested change, then fetch after owner merge'
                   if differences else 'Change protected settings through a PR; fetch after owner merge')
    if differences and commit:
        head = _git(root, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', optional=True)
        if head:
            older = head.decode('ascii').strip()
            # Bounded ancestry check also works for detached and shallow checkouts.
            ancestor = _git(root, 'merge-base', '--is-ancestor', older, commit, optional=True)
            if older != commit and ancestor is not None:
                next_action = 'Fetched policy is newer; update the checkout to the fetched default branch before proposing further changes'
            elif older != commit:
                history = _git(root, 'rev-list', '--max-count=32', commit, optional=True)
                for candidate in (history or b'').decode('ascii').splitlines()[1:]:
                    try:
                        prior = _blob(root, candidate, 'OPERATING_CONFIG.yaml')
                        prior = load_yaml(prior) if prior is not None else None
                    except (ValidationError, ValueError, OSError):
                        continue  # Historical guidance cannot invalidate current accepted policy.
                    if isinstance(prior, dict) and all(prior.get(name) == working.get(name) for name, _ in PROTECTED_DOMAINS):
                        next_action = 'Working settings match an older default-branch commit; update the checkout before proposing further changes'
                        break
    return value, dict(source=source, commit=commit, ref=ref, differences=differences,
        policy_observation=observation, branch_observation=branch_observation, fetched_at=fetched_at,
        visibility='Locally fetched policy only; a newer merge is not visible until fetched. FETCH_HEAD time, when present, is the last fetch of any ref.',
        next_action=next_action)


def refresh_default_policy(root):
    """Controller-only bounded fetch; failure retains the last known policy ref.

    Uses the checkout's existing Git credentials/configuration. Read-only show
    never calls this function. Explicit atomic refspecs avoid configured mapping
    steering and cannot update unrelated refs or invoke automatic maintenance.
    """
    import os
    from .child_process import child_env
    from .git_isolation import native_git_executable
    from .safeio import run_bounded
    try:
        if _metadata(root) is None:
            return dict(status='UNOBSERVED', reason='No repository; no policy fetch attempted')
        ref, _observation = _default_ref(root)
        if not ref:
            return dict(status='UNOBSERVED', reason='No default-branch name; no policy fetch attempted')
        branch = ref.removeprefix('refs/remotes/origin/')
        environment = child_env({k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')})
        environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never',
                           GIT_NO_REPLACE_OBJECTS='1', GIT_ALLOW_PROTOCOL='file:https:http:ssh')
        result = run_bounded([native_git_executable(Path(root)), '--no-replace-objects',
            '-c', 'safe.directory=' + Path(root).resolve().as_posix(),
            '-c', 'core.hooksPath=' + os.devnull, '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            '-C', str(root), 'fetch', '--atomic', '--no-tags', '--no-recurse-submodules',
            '--refmap=', 'origin', '+refs/heads/' + branch + ':' + ref],
            cwd=Path(root), env=child_env(environment, strip_extra=[]), timeout=30,
            stdout_limit=65536, stderr_limit=65536)
        if result.returncode:
            return dict(status='UNAVAILABLE', ref=ref,
                        reason='Default-branch fetch failed; retained last known policy; retry next controller cycle')
        return dict(status='FETCHED', ref=ref,
                    reason='Fetched default branch in controller checkout before loading policy')
    except (OSError, ValueError, ValidationError, TimeoutError) as exc:
        return dict(status='UNAVAILABLE', reason='Default-branch fetch unavailable (' + type(exc).__name__
                    + '); retained last known policy; retry next controller cycle')


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
