"""Protected operating values are read from the locally fetched default branch.

Git metadata must remain outside worker write authority. Repository rules and
the owner govern merging. Reads never fetch; the controller refreshes separately.
"""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import re

from . import ValidationError
from .budgeting import fresh_settings, validate_settings
from .operating_calendar import fresh_calendar, validate_calendar
from .canonical import canonical, load_yaml

PROTECTED_DOMAINS = (
    ('budgeting', fresh_settings, validate_settings),
    ('operating_calendar', fresh_calendar, validate_calendar),
)
MAX_BYTES = 1024 * 1024


def load_operating_yaml(raw):
    """Refuse ambiguous/invalid operating YAML without publishing its source."""
    try:
        return load_yaml(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        kind = ('duplicate key' if str(exc).startswith('Duplicate mapping key:') else
                'out of range' if str(exc).startswith('Integer exceeds') else
                'wrong type' if str(exc).startswith(('Only JSON values', 'Mapping keys'))
                else 'malformed YAML')
        # The parser's message/cause can contain source lines and arbitrary keys.
        # Suppress only that input-bearing chain, not unrelated programming errors.
        raise ValidationError('OPERATING_CONFIG.yaml: Invalid operating YAML: duplicate keys, malformed YAML or unsupported values; problem: ' + kind + '; correct the file') from None


def _nondefault(working):
    for name, defaults, normalize in PROTECTED_DOMAINS:
        if name in working:
            try:
                if canonical(normalize(working[name])) != canonical(normalize(defaults())):
                    return True
            except ValidationError:
                # Invalid working bytes cannot mask valid accepted policy. They
                # still require policy, and are never printed in differences.
                return True
    return False


def _display_domain(working, name, normalize):
    if name not in working:
        return None
    try:
        normalize(working[name])
    except ValidationError:
        from .operating import _schema
        errors = _schema('operating-config', {name: working[name]})
        detail = [item['path'] + ': ' + item['reason'] for item in errors
                  if item['path'].startswith('$.' + name)]
        return ('REJECTED protected value; OPERATING_CONFIG.yaml: ' +
                ('; '.join(detail) if detail else name + ': not allowed') +
                '; correct the proposed file before opening a PR')
    return deepcopy(working[name])


def unprotected_hash(value):
    from .canonical import fingerprint
    return fingerprint('operating-unprotected', {k: v for k, v in value.items()
                       if k not in {name for name, _, _ in PROTECTED_DOMAINS}})


def audit_matches(value, event):
    """Keep unprotected audit binding when protected bytes change through Git.

    Pre-1.9.6 records have no projection hash. They may match their exact raw
    values, the legacy absent block or fresh defaults; arbitrary edits still fail.
    """
    from .canonical import fingerprint
    projection = event.get('unprotected_after_hash')
    if projection == unprotected_hash(value):
        return True
    # Older projections excluded budgeting only. Keep those exact records and
    # their absent/fresh calendar forms verifiable without rewriting history.
    candidates = [deepcopy(value)]
    for name, defaults, normalize in PROTECTED_DOMAINS:
        expanded = []
        for candidate in candidates:
            absent, fresh = deepcopy(candidate), deepcopy(candidate)
            absent.pop(name, None)
            fresh[name] = defaults()
            expanded.extend((candidate, absent, fresh))
        candidates = expanded
    if projection is not None:
        return any(projection == fingerprint('operating-unprotected',
                   {k: v for k, v in candidate.items() if k != 'budgeting'})
                   for candidate in candidates)
    return any(event['after_hash'] == fingerprint('operating', candidate)
               for candidate in candidates)


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
    # This symbolic ref is the sole identity input. Never parse branch config.
    symbolic = _git(root, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD', optional=True)
    ref = symbolic.decode('utf-8').rstrip('\n') if symbolic else None
    if ref is not None:
        _validate_tracking_ref(root, ref)
    return ref, 'Default branch from origin/HEAD' if ref else 'Default branch unverified: origin/HEAD absent'


def _validate_tracking_ref(root, ref):
    # Share Git's grammar with publication, including UTF-8 and punctuation.
    if not ref.startswith('refs/remotes/origin/'):
        raise ValidationError('Invalid origin/HEAD default-branch reference')
    _git(root, 'check-ref-format', ref)


def _origin_url(root):
    # No global config, includes, remote-name/path ambiguity or working bytes.
    raw = _git(root, 'config', '--local', '--no-includes', '--null', '--get-all',
               'remote.origin.url', optional=True)
    if not raw:
        return None
    urls = raw.decode('utf-8').rstrip('\0').split('\0')
    if len(urls) != 1 or not urls[0].strip():
        raise ValidationError('Configure exactly one remote.origin.url in repository config')
    return urls[0]


def _accepted_url(root, url):
    from urllib.parse import urlsplit, unquote
    if any(c in url for c in ('\n', '\r', '\0')) or url.startswith('-'):
        raise ValidationError('Invalid origin URL; configure an absolute external remote URL')
    local = None
    if url.lower().startswith('file:'):
        parsed = urlsplit(url)
        if parsed.netloc not in ('', 'localhost'):
            local = Path('//' + parsed.netloc + unquote(parsed.path))
        else:
            path = unquote(parsed.path)
            if re.match(r'^/[A-Za-z]:/', path):
                path = path[1:]
            local = Path(path)
    elif Path(url).is_absolute():
        local = Path(url)
    elif re.match(r'^[A-Za-z]:', url):
        raise ValidationError('Drive-relative origin paths refused; configure an absolute external remote URL')
    elif re.match(r'^(https?|ssh)://[^/]+/', url) or re.match(r'^[^/:\s]+:[^\s]+$', url):
        return url
    else:
        raise ValidationError('Relative origin paths refused; configure an absolute external remote URL')
    if not local.is_absolute() or local.resolve().is_relative_to(Path(root).resolve()):
        raise ValidationError('Origin inside this checkout or relative path refused; configure an external remote URL')
    return url


def _fetch_time(metadata):
    from datetime import datetime, timezone
    path = metadata / 'FETCH_HEAD'
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.is_file() else None


def resolve_protected(root, governance, working):
    """Read immutable fetched policy; non-repositories invoke no external tool."""
    if not isinstance(working, dict):
        raise ValidationError('Operating settings must be an object; restore OPERATING_CONFIG.yaml')
    value = deepcopy(working)
    needed = _nondefault(working)
    commit = ref = fetched_at = None
    unverified = False
    budget_admission = True
    remote_configured = False
    remedy = 'The next controller cycle establishes origin/HEAD, or run git remote set-head origin --auto'
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
            if _default_ref(root)[0] != ref:
                raise ValidationError('Default policy changed during read; retry after the controller cycle')
            remote_configured = _origin_url(root) is not None
            unverified = remote_configured and not commit
            if unverified:
                branch_observation = 'Default branch unverified: origin/HEAD absent or its target unavailable; ' + remedy
                tracking = _git(root, 'for-each-ref', '--count=1', '--format=%(refname)', 'refs/remotes/')
                # A dangling identity can result from interrupted namespace
                # cleanup. It is never the fresh, no-policy installation case.
                budget_admission = not (ref or tracking or needed)
            configured = governance.get('github', {}).get('base_branch')
            if ref and configured and ref != 'refs/remotes/origin/' + configured:
                branch_observation += '; origin/HEAD and configured github.base_branch disagree; github.base_branch is not an identity input'
            fetched_at = _fetch_time(metadata)
        if commit:
            if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
                raise ValidationError('Invalid default-branch commit')
            raw = _blob(root, commit, 'OPERATING_CONFIG.yaml')
            observation = 'file absent at default-branch commit; no accepted policy' if raw is None else 'accepted default-branch file'
            if raw is None:
                for name, defaults, normalize in PROTECTED_DOMAINS:
                    if name in value:
                        value[name] = defaults()
                source = 'fresh defaults' if 'budgeting' in value else 'legacy retained'
                sources = {name: 'fresh defaults' if name in value else 'legacy retained'
                           for name, _, _ in PROTECTED_DOMAINS}
            else:
                from .operating import validate_operating
                try:
                    accepted = load_operating_yaml(raw)
                    validate_operating(accepted, governance)
                except ValidationError as exc:
                    raise ValidationError('OPERATING_CONFIG.yaml: ' + str(exc)) from exc
                for name, _defaults, normalize in PROTECTED_DOMAINS:
                    if name in accepted:
                        value[name] = deepcopy(accepted[name])
                    else:
                        value.pop(name, None)
                source = 'default branch' if 'budgeting' in accepted else 'legacy retained; default branch'
                sources = {name: 'default branch' if name in accepted else 'legacy retained; default branch'
                           for name, _, _ in PROTECTED_DOMAINS}
        else:
            if needed and not unverified:
                raise ValidationError('Nondefault protected settings refused: no fetched default branch policy; open a PR and fetch after owner merge')
            source = ('unverified working settings; no accepted default-branch policy' if unverified else
                      'fresh defaults' if 'budgeting' in working else 'legacy retained')
            sources = {name: 'unverified working settings; no accepted default-branch policy' if unverified else
                       'fresh defaults' if name in working else 'legacy retained'
                       for name, _, _ in PROTECTED_DOMAINS}
    except (OSError, ValueError, UnicodeError, ValidationError) as exc:
        raise ValidationError('Protected default-branch settings unavailable: ' + str(exc)) from exc
    # Preserve the established budget-only source label; mixed domains carry
    # their own source in both the summary and the structured report.
    if any(name != 'budgeting' and (name in working or name in value) for name, _, _ in PROTECTED_DOMAINS):
        source = (next(iter(sources.values())) if len(set(sources.values())) == 1 else
                  '; '.join(name + ': ' + label for name, label in sources.items()))
    differences = [{'path': name, 'working': _display_domain(working, name, normalize), 'effective': _display_domain(value, name, normalize)}
                   for name, _defaults, normalize in PROTECTED_DOMAINS if working.get(name) != value.get(name)]
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
                        prior = load_operating_yaml(prior) if prior is not None else None
                    except (ValidationError, ValueError, OSError):
                        continue  # Historical guidance cannot invalidate current accepted policy.
                    if isinstance(prior, dict) and all(prior.get(name) == working.get(name) for name, _, _ in PROTECTED_DOMAINS):
                        next_action = 'Working settings match an older default-branch commit; update the checkout before proposing further changes'
                        break
    return value, dict(source=source, sources=sources, commit=commit, ref=ref, differences=differences,
        policy_observation=observation, branch_observation=branch_observation, fetch_attempt_at=fetched_at,
        budget_admission=budget_admission, remote_configured=remote_configured, unverified=unverified,
        budget_refusal=('Default branch unverified; protected decision refused; ' + remedy)
                       if not budget_admission else None,
        visibility='Locally fetched policy only; a newer merge is not visible until fetched. FETCH_HEAD time records a fetch attempt of any ref, not a successful policy refresh.',
        next_action=remedy if unverified else next_action)


@contextmanager
def _refresh_lock(path):
    """Reclaim old markers only under an OS lock, immune to wall-clock jumps.

    Keep the handle locked through unlink. Openers of the retired inode must
    recheck its identity; they cannot publish after a replacement acquired it.
    Windows needs delete sharing to unlink before releasing the kernel lock.
    """
    import os
    import time
    if os.name == 'nt':
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        # OPEN_ALWAYS atomically identifies a new file via ERROR_ALREADY_EXISTS.
        handle = create(str(path), 0xc0000000, 7, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        created = ctypes.get_last_error() != 183
        stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY), 'r+b')
    else:
        try:
            stream = path.open('x+b')
            created = True
        except FileExistsError:
            stream = path.open('r+b')
            created = False
    claimed = False
    try:
        if os.name == 'nt':
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(stream.fileno())
        if not os.path.samestat(info, path.stat()):
            raise ValidationError('Refresh lock was replaced; retry next cycle')
        # Even a suspended live refresher keeps its kernel lock. Age alone
        # cannot prove death: a clock jump must never permit a second writer.
        replaced = not (created and info.st_size == 0)
        if replaced and time.time() - info.st_mtime <= 600:
            raise ValidationError('Another refresh owns a lock younger than ten minutes')
        stream.seek(0)
        stream.truncate()
        stream.write(b'Controller default-policy refresh\n')
        stream.flush()
        claimed = True
        yield replaced
    finally:
        try:
            if claimed:
                path.unlink()
        finally:
            stream.close()


def refresh_default_policy(root, *, credential_envs=()):
    """Controller-only bounded fetch; failure retains policy or refuses admission.

    Uses the checkout's existing Git credentials/configuration. Read-only show
    never calls this function. Objects-only fetch avoids configured mapping;
    publication removes only namespace conflicts before updating the identity.
    """
    import os
    from .child_process import child_env, safe_adapter_env
    from .git_isolation import native_git_executable
    from .safeio import run_bounded
    # Minimum supported Git: 2.29 (maintenance.auto). Audited bundled Git
    # Documentation/RelNotes: 1.6.6 --no-replace-objects; 1.7.x -c, config
    # --local/--null/--get-all and --no-includes (includes: 1.7.10), fetch
    # --no-tags/--no-recurse-submodules (1.7.4); 1.8.5 -C; 2.8 ls-remote
    # --symref (--get-url predates it); 2.9 core.hooksPath; 2.16 fsmonitor;
    # 2.26 documents empty --refmap=; 2.29 introduces maintenance.
    # Existing plumbing: check-ref-format, for-each-ref --format, rev-parse
    # --verify/--quiet ^{commit}, update-ref --no-deref/-d, symbolic-ref -q.
    # safe.directory is ignored before its later security backports; gc.auto=0
    # also suppresses pre-maintenance GC. No --atomic (2.31) or stdin symrefs.
    # Sources: https://git-scm.com/docs/RelNotes/2.29.0 (and versions above).
    # This is an option audit, not execution against an older Git binary.
    step = 'remote identity'
    stale_lock_replaced = False
    try:
        metadata = _metadata(root)
        if metadata is None:
            return dict(status='UNOBSERVED', reason='No repository; no policy fetch attempted')
        url = _origin_url(root)
        if url is None:
            return dict(status='UNOBSERVED', step=step, reason='No repository-configured remote.origin.url; no remote policy fetch attempted; configure origin to refresh')
        url = _accepted_url(root, url)
        environment = child_env({k: v for k, v in safe_adapter_env(*credential_envs).items()
                                 if not k.upper().startswith('GIT_')})
        environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never',
                           GIT_NO_REPLACE_OBJECTS='1', GIT_ALLOW_PROTOCOL='file:https:http:ssh')
        command = [native_git_executable(Path(root)), '--no-replace-objects',
            '-c', 'safe.directory=' + Path(root).resolve().as_posix(),
            '-c', 'core.hooksPath=' + os.devnull, '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
            '-c', 'core.fsmonitor=false', '-C', str(root)]
        def run(*args, stdin=None):
            result = run_bounded([*command, *args], stdin=stdin,
                cwd=Path(root), env=child_env(environment, strip_extra=[]), timeout=30,
                stdout_limit=65536, stderr_limit=65536)
            if result.returncode:
                raise ValidationError('Git operation failed')
            return result.stdout
        # Pin a stable rewritten transport URL before any network; passing an
        # expanded URL back to Git must not trigger a second, unchecked rewrite.
        for _ in range(4):
            expanded = _accepted_url(root, run('ls-remote', '--get-url', '--', url).decode('utf-8').strip())
            if expanded == url:
                break
            url = expanded
        else:
            raise ValidationError('Origin URL rewrites do not stabilize; configure an unambiguous external URL')
        step = 'refresh ownership'
        lock = metadata / 'awf-default-policy.lock'
        with _refresh_lock(lock) as stale_lock_replaced:
            step = 'default discovery'
            output = run('ls-remote', '--symref', '--', url, 'HEAD').decode('utf-8')
            names = re.findall(r'^ref: (refs/heads/[^\t\n]+)\tHEAD$', output, re.MULTILINE)
            if len(names) != 1:
                raise ValidationError('Remote HEAD must name one branch')
            advertised = re.findall(r'^([0-9a-f]{40,64})\tHEAD$', output, re.MULTILINE)
            if len(advertised) != 1:
                raise ValidationError('Remote HEAD must advertise one commit')
            branch_ref = names[0]
            _git(root, 'check-ref-format', branch_ref)
            ref = 'refs/remotes/origin/' + branch_ref.removeprefix('refs/heads/')
            _validate_tracking_ref(root, ref)
            previous, _ = _default_ref(root)
            step = 'branch fetch'
            # Fetch objects only. Configured refspecs cannot publish policy early.
            run('fetch', '--no-tags', '--no-recurse-submodules',
                '--refmap=', '--', url, branch_ref)
            commit = _git(root, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode('ascii').strip()
            # Another fetch can overwrite FETCH_HEAD; a real push between
            # discovery and fetch also mismatches. Retain policy; next cycle retries.
            step = 'advertised commit check'
            if commit != advertised[0]:
                raise ValidationError('Fetched commit differs from advertised HEAD')
            step = 'tracking namespace cleanup'
            tracking = _git(root, 'for-each-ref', '--format=%(refname)', 'refs/remotes/origin/')
            conflicts = [other for other in tracking.decode('utf-8').split('\n')
                         if other.startswith(ref + '/') or ref.startswith(other + '/')]
            if conflicts and previous is None:
                # Keep evidence of unverified policy even if cleanup removes
                # the last tracking ref. A DIRECT HEAD is not an identity:
                # only symbolic-ref can admit policy. Final publication below
                # replaces this marker with the verified symbolic identity.
                run('update-ref', '--no-deref', 'refs/remotes/origin/HEAD', commit)
            for other in conflicts:
                # Remote refs cannot coexist at these names. Never follow
                # a stale symbolic tracking ref into an unrelated branch.
                run('update-ref', '--no-deref', '-d', other)
            step = 'policy ref transaction'
            run('update-ref', '--no-deref', ref, commit)
            if previous != ref:
                step = 'default identity publication'
                run('symbolic-ref', 'refs/remotes/origin/HEAD', ref)
        return dict(status='FETCHED', ref=ref, previous_ref=previous, name_changed=previous != ref,
                    stale_lock_replaced=stale_lock_replaced,
                    reason='Fetched default branch before loading policy' + ('; default branch name changed' if previous != ref else '')
                    + ('; replaced stale refresh lock' if stale_lock_replaced else ''))
    except (OSError, ValueError, UnicodeError, ValidationError, TimeoutError) as exc:
        remedies = {'remote identity': 'Configure exactly one absolute external remote.origin.url in repository config',
            'default discovery': 'Restore remote access and set its HEAD to an existing branch; retry the controller cycle or git remote set-head origin --auto',
            'refresh ownership': 'Retry the next controller cycle; unlocked refresh locks older than ten minutes are replaced automatically',
            'branch fetch': 'Restore access to the advertised default branch and retry the controller cycle',
            'advertised commit check': 'Fetched commit differs from advertised HEAD; retry the next controller cycle to rediscover and fetch a stable default',
            'tracking namespace cleanup': 'Restore writable controller-owned conflicting tracking refs; retry the controller cycle',
            'policy ref transaction': 'Restore writable controller-owned tracking refs; retry the controller cycle',
            'default identity publication': 'Restore writable controller-owned origin/HEAD; retry the controller cycle'}
        return dict(status='UNAVAILABLE', step=step, stale_lock_replaced=stale_lock_replaced,
                    reason='Default-policy ' + step + ' failed (' + type(exc).__name__
                    + '); retained last known policy or unverified identity; ' + remedies[step]
                    + ('; replaced stale refresh lock' if stale_lock_replaced else ''))


def effective_snapshot(root, governance, snapshot):
    from dataclasses import replace
    from .operating import validate_operating
    value, report = resolve_protected(root, governance, snapshot.config)
    result = validate_operating(value, governance)
    return replace(result, last_change_id=snapshot.last_change_id,
        _provenance_json=canonical({**snapshot.provenance, 'protected_settings': report}))


def budget_snapshot(root, governance, snapshot, *, credential_envs=()):
    """Admission refreshes unresolved identity before trusting even OFF bytes."""
    result = effective_snapshot(root, governance, snapshot)
    refresh = None
    if result.provenance['protected_settings'].get('unverified'):
        refresh = refresh_default_policy(root, credential_envs=credential_envs)
        result = effective_snapshot(root, governance, snapshot)
    require_budget_policy(result, refresh=refresh)
    return result


def require_budget_policy(snapshot, *, refresh=None):
    """Refuse protected decisions when working values could affect the outcome.

    Shared by budget, calendar and reporting consumers. Diagnostic reads remain
    available; this admission bit covers every registered protected domain.
    """
    report = snapshot.provenance.get('protected_settings', {})
    if report.get('budget_admission') is False:
        detail = '; ' + refresh['reason'] if refresh and refresh.get('reason') else ''
        raise ValidationError(report['budget_refusal'] + detail)


def require_mutation_policy(report, working):
    if report.get('unverified') and _nondefault(working):
        raise ValidationError('Nondefault protected settings refused for mutation: default branch unverified; ' + report['next_action'])


def repository_rules_warning():
    # No network dependency on the show/default/OFF paths. A host may separately
    # attach a fresh rules observation; absence never claims applied controls.
    return dict(status='UNOBSERVED', warning='Repository rules UNOBSERVED; the owner and host rules govern merge. Offer the shipped ruleset.')
