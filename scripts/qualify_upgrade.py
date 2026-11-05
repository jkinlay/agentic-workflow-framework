"""Disposable upgrade qualification and external preservation evidence; never live rollout.

Reports contain digests rather than owner file contents. Keep reports and allowed
delta files in approved external storage. This tool grants no release authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import tempfile
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import load_yaml, loads
from agentic.installer import (install, CONFIG, INSTALLED, PROVENANCE, CODEOWNERS,
                               managed, verify_release)
from agentic.safeio import Tree, run_bounded
from agentic.child_process import child_env

FORMAT = 'awf-upgrade-preservation-1'
EXCLUDED = {'.git', '.agentic/.venv'}


def _permissions(path):
    """Retain only a digest of ownership and access control, never raw SIDs."""
    if os.name != 'nt':
        info = path.stat()
        raw = json.dumps([info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]).encode()
        return {'verified': True, 'method': 'posix-owner-group-mode',
                'sha256': hashlib.sha256(raw).hexdigest()}
    method = 'windows-owner-group-dacl-sddl'
    try:
        import ctypes
        from ctypes import wintypes
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get = advapi.GetFileSecurityW
        get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get.restype = wintypes.BOOL
        convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                           ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
        convert.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        needed = wintypes.DWORD()
        flags = 0x1 | 0x2 | 0x4  # OWNER, GROUP, DACL; no SACL privilege required.
        get(str(path), flags, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError('Security descriptor unavailable')
        descriptor = ctypes.create_string_buffer(needed.value)
        if not get(str(path), flags, descriptor, needed.value, ctypes.byref(needed)):
            raise OSError('Security descriptor unavailable')
        sddl = wintypes.LPWSTR()
        if not convert(descriptor, 1, flags, ctypes.byref(sddl), None):
            raise OSError('Security descriptor conversion unavailable')
        try:
            digest = hashlib.sha256(sddl.value.encode('utf-8')).hexdigest()
        finally:
            kernel.LocalFree(ctypes.cast(sddl, ctypes.c_void_p))
        return {'verified': True, 'method': method, 'sha256': digest}
    except (OSError, AttributeError):
        return {'verified': False, 'method': method, 'diagnostic': 'permission capture unavailable'}


def inventory(project, *, evidence_kind='SYNTHETIC_OWNER_CUSTOMIZATION'):
    """Hash a quiescent disposable project without opening any database for write.

    Runtime/Git trees are excluded explicitly. Ledgers (including WAL/SHM),
    reservations, archives, receipts, owner files and permissions are included.
    Exclusive ownership is required; this is not a snapshot of a live writer.
    """
    project = Path(project).resolve(strict=True)
    files = {}
    directories = {'/': {'mode': stat.S_IMODE(project.stat().st_mode),
                         'permissions': _permissions(project)}}

    def incomplete(error):
        raise ValueError('Preservation inventory incomplete: directory enumeration failed') from error

    for directory, dirs, names in os.walk(project, followlinks=False, onerror=incomplete):
        parent = Path(directory)
        for name in [*dirs, *names]:
            path = parent / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0):
                raise ValueError('Linked/reparse inventory member refused')
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError('Hard-linked inventory member refused')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('Non-regular inventory member refused')
        dirs[:] = sorted(name for name in dirs
                         if (parent / name).relative_to(project).as_posix() not in EXCLUDED)
        for name in dirs:
            path = parent / name
            directories[path.relative_to(project).as_posix() + '/'] = {
                'mode': stat.S_IMODE(path.stat().st_mode), 'permissions': _permissions(path)}
        for name in sorted(names):
            path = parent / name
            relative = path.relative_to(project).as_posix()
            if relative == '.git':  # Only the actual root worktree pointer.
                continue
            raw = path.read_bytes()
            files[relative] = {'sha256': hashlib.sha256(raw).hexdigest(),
                               'mode': stat.S_IMODE(path.stat().st_mode), 'size': len(raw),
                               'permissions': _permissions(path)}
    config = load_yaml((project / '.agentic/PROJECT_CONFIG.yaml').read_bytes())
    github = config['github']
    identity = {key: github.get(key) for key in ('repository', 'repository_id', 'base_branch')}
    if any(value is None or value == '' for value in identity.values()):
        raise ValueError('Complete repository identity is required')
    return {'format': FORMAT, 'evidence_kind': evidence_kind,
            'identity': identity, 'files': files, 'directories': directories,
            'excluded_paths': sorted(EXCLUDED),
            'execution_authority': False}


def compare(before, after, allowed_delta):
    """An allowed delta binds both exact file records, including modes and sizes."""
    if before.get('format') != FORMAT or after.get('format') != FORMAT:
        raise ValueError('Unsupported preservation inventory')
    violations = []
    if before.get('excluded_paths') != sorted(EXCLUDED) or after.get('excluded_paths') != sorted(EXCLUDED):
        violations.append('inventory exclusions missing or unsupported')
    if before['identity'] != after['identity']:
        violations.append('repository identity changed')
    if before['evidence_kind'] != after['evidence_kind']:
        violations.append('evidence kind changed')
    changes = {}
    before_entries = {**before['files'], **before['directories']}
    after_entries = {**after['files'], **after['directories']}
    for label, entries in (('before', before_entries), ('after', after_entries)):
        if '/' not in entries:
            violations.append(label + ' root permissions missing')
        for path, entry in sorted(entries.items()):
            permission = entry.get('permissions') or {}
            digest = permission.get('sha256')
            if (permission.get('verified') is not True or permission.get('method') not in
                    ('posix-owner-group-mode', 'windows-owner-group-dacl-sddl') or
                    not isinstance(digest, str) or len(digest) != 64 or
                    any(c not in '0123456789abcdef' for c in digest)):
                violations.append(label + ' permissions unverified: ' + path)
    for path in sorted(before_entries.keys() | after_entries.keys()):
        old, new = before_entries.get(path), after_entries.get(path)
        if old != new:
            changes[path] = {'before': old, 'after': new}
            if allowed_delta.get(path) != changes[path]:
                violations.append('unapproved delta: ' + path)
    for path in sorted(allowed_delta.keys() - changes.keys()):
        violations.append('unused delta: ' + path)
    return {'format': 'awf-upgrade-comparison-1', 'preserved': not violations,
            'changes': changes, 'violations': violations, 'execution_authority': False,
            'qualification_status': 'NOT_QUALIFIED'}


def _json_identity(value):
    # Python equality conflates True/1 and 1/1.0; preservation must not.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _creation_state(project, relative, *, directory=False, readonly=False):
    """Measure Tree.write/mkdir inheritance in the disposable parent, then remove it.

    Names grant no permission exception. In particular, Windows DACL inheritance
    depends on the actual nearest existing parent and the number of new levels.
    These short-lived probes precede the upgrade, never run against live adopters,
    and must leave the preservation inventory unchanged.
    """
    parent = (project / relative.rstrip('/')).parent
    missing = 0
    while not parent.exists():
        missing += 1
        parent = parent.parent
    # For a directory, the leaf adds one more inherited directory level.
    depth = missing + int(directory)
    temporary_root = None
    probe = None
    try:
        current = parent
        if depth:
            temporary_root = parent / ('.awf-permission-probe-' + uuid.uuid4().hex)
            temporary_root.mkdir()  # Installer mkdir mode/umask, not mkdtemp's 0700.
            current = temporary_root
            for index in range(depth - 1):
                current = current / str(index)
                current.mkdir()
        if directory:
            probe = current
        else:
            fd, name = tempfile.mkstemp(prefix='.awf-permission-probe-', dir=current)
            os.close(fd)  # Same 0600 create mode as Tree.write.
            probe = Path(name)
            if readonly:
                probe.chmod(stat.S_IREAD)
        return {'mode': stat.S_IMODE(probe.stat().st_mode), 'permissions': _permissions(probe)}
    finally:
        if probe is not None and not directory:
            probe.chmod(0o600)
            probe.unlink()
        if temporary_root is not None:
            # Only our known empty probe chain; never recursively remove data.
            current = probe if directory else probe.parent
            while current != temporary_root:
                previous = current.parent
                current.rmdir()
                current = previous
            temporary_root.rmdir()


def prepare_upgrade(source, project, pin, before):
    """Derive a closed migration policy from verified release and baseline bytes.

    This is deliberately the 1.9.5 -> 1.9.6 policy. A future state/configuration
    migration needs its own reviewed rule; observed deltas cannot register one.
    Private policy inputs include parsed owner configuration, never report them.
    """
    from agentic.upgrade import (identify_installation, load_known_versions,
                                 state_archive_plan, apply_chain)
    project = Path(project).resolve(strict=True)
    if inventory(project, evidence_kind=before['evidence_kind']) != before:
        raise ValueError('Baseline changed before migration policy derivation')
    with Tree(source) as tree:
        verified_pin, content = verify_release(tree, pin, allow_source_checkout=True)
        manifest_raw = tree.read('MANIFEST.json')
    manifest = json.loads(manifest_raw)
    config_raw = (project / CONFIG).read_bytes()
    receipt_raw = (project / INSTALLED).read_bytes()
    receipt = loads(receipt_raw.decode('utf-8'))
    versions = load_known_versions(content['.agentic/upgrade/known-versions.json'])
    with Tree(project) as tree:
        identity = identify_installation(tree, versions, config_raw, receipt_raw)
    target = manifest['template_version']
    if (identity['version'], target) != ('1.9.5', '1.9.6'):
        raise ValueError('No approved preservation migration for this release pair')
    immutable = {path: digest for path, digest in manifest['files'].items()
                 if managed(path) and path not in {CONFIG, PROVENANCE, CODEOWNERS}}
    state = {path: (project / path).read_bytes() for path in before['files']
             if path.startswith('.agentic-state/')}
    operating = project / 'OPERATING_CONFIG.yaml'
    bundle, steps = apply_chain(versions, identity['version'], config_raw,
        operating.read_bytes() if operating.exists() else None, receipt_raw,
        (project / PROVENANCE).read_bytes(), state,
        {'source_manifest_sha256': verified_pin, '_source_manifest_json': manifest_raw.decode('utf-8'),
         '_immutable_files': immutable})
    migrations = [action for step in steps for action in step['state_migrations']
                  if action['action'] == 'migrated_schema']
    archives, archive_actions = state_archive_plan(bundle.state, target_version=target)
    files = {path: {'sha256': digest, 'size': len(content[path]), 'rule': 'target-manifest',
                    'manifest_sha256': verified_pin}
             for path, digest in immutable.items()}
    for path, raw in bundle.state.items():
        if raw == state[path]:
            continue
        action = next((item for item in migrations if item['path'] == path), None)
        if action is None or path.startswith('.agentic-state/archive/'):
            raise ValueError('No recorded state migration for changed baseline path')
        files[path] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw),
                       'rule': 'recorded-state-migration', 'migration': action,
                       'source_sha256': before['files'][path]['sha256']}
    for path, raw in archives.items():
        files[path] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw),
                       'rule': 'upgrade-archive', 'target_version': target,
                       'archive_manifest_sha256': hashlib.sha256(
                           archives[f'.agentic-state/archive/upgrade-to-{target}/manifest.json']).hexdigest()}
        action = next((item for item in archive_actions if item['archive'] == path), None)
        if action:
            files[path]['source'] = action['path']
            files[path]['source_sha256'] = before['files'][action['path']]['sha256']
    # Receipts/provenance have cohort-specific fields, validated structurally below.
    outputs = set(files) | {CONFIG, INSTALLED, PROVENANCE}
    directories = {'.agentic-install/', '.agentic-install/objects/'}
    for path in outputs:
        directories.update(parent.as_posix() + '/' for parent in Path(path).parents
                           if parent.as_posix() != '.')
    creation = {}
    cache = {}
    for path in sorted(outputs | directories):
        old = before['directories' if path.endswith('/') else 'files'].get(path)
        if old is not None:
            continue
        parent = (project / path.rstrip('/')).parent
        depth = 0
        while not parent.exists():
            depth += 1
            parent = parent.parent
        readonly = path in archives
        key = (parent, depth, path.endswith('/'), readonly)
        if key not in cache:
            cache[key] = _creation_state(project, path, directory=path.endswith('/'), readonly=readonly)
        creation[path] = cache[key]
    if inventory(project, evidence_kind=before['evidence_kind']) != before:
        raise ValueError('Permission probes changed the disposable baseline')
    return {'manifest_sha256': verified_pin, 'manifest_raw': manifest_raw.decode('utf-8'),
            'baseline_sha256': hashlib.sha256(_json_identity(before).encode('utf-8')).hexdigest(),
            'target_version': target, 'source_version': identity['version'],
            'files': files, 'immutable': immutable, 'directories': directories,
            'removed': set(identity['immutable_files']) - set(immutable),
            'old_immutable': identity['immutable_files'], 'config': load_yaml(config_raw),
            'receipt': receipt, 'creation': creation, 'started_at': time.time(),
            'archive_actions': archive_actions, 'state_migrations': migrations}


def compare_upgrade(before, after, policy, project, *, allowed_delta=None):
    """Audit every change against the pre-upgrade policy, never a prefix waiver."""
    project = Path(project)
    approved = {}
    delta = {}
    failures = []
    if hashlib.sha256(_json_identity(before).encode('utf-8')).hexdigest() != policy['baseline_sha256']:
        failures.append('migration policy baseline binding mismatch')
    old_entries = {**before['files'], **before['directories']}
    new_entries = {**after['files'], **after['directories']}

    def approve(path, evidence):
        old, new = old_entries.get(path), new_entries.get(path)
        if old != new:
            delta[path] = {'before': old, 'after': new}
            approved[path] = evidence

    def bound_bytes(path):
        raw = (project / path).read_bytes()
        entry = after['files'].get(path, {})
        if entry.get('sha256') != hashlib.sha256(raw).hexdigest() or entry.get('size') != len(raw):
            raise ValueError('Post-upgrade bytes differ from inventory')
        return raw

    def expected_state(path, *, preserve=False):
        old = old_entries.get(path)
        expected = ({key: old[key] for key in ('mode', 'permissions')} if old is not None
                    else policy['creation'].get(path))
        if not preserve and path in policy['files'] and policy['files'][path]['rule'] == 'upgrade-archive':
            # Prior archives (including already-existing target archives) never change.
            if old is not None:
                return new_entries.get(path) == old
        new = new_entries.get(path)
        return bool(expected and new and all(new.get(key) == value for key, value in expected.items()))

    for path, expected in sorted(policy['files'].items()):
        new = after['files'].get(path)
        if (not new or any(new.get(key) != expected[key] for key in ('sha256', 'size'))
                or not expected_state(path)):
            failures.append('expected migration content or permissions mismatch: ' + path)
            continue
        # No prior archive is eligible for a change, even to otherwise valid bytes.
        if path.startswith('.agentic-state/archive/') and path in before['files']:
            continue
        approve(path, {**expected, 'permissions_rule': 'baseline-identical' if path in before['files']
                      else 'installer-create-inheritance',
                      'expected_permissions': new['permissions'], 'expected_mode': new['mode']})
    for path in sorted(policy['removed']):
        if path not in new_entries and before['files'].get(path, {}).get('sha256') == policy['old_immutable'][path]:
            approve(path, {'rule': 'managed-removal', 'previous_sha256': policy['old_immutable'][path],
                           'absent_from_target_manifest_sha256': policy['manifest_sha256']})
        else:
            failures.append('managed removal mismatch: ' + path)
    config_valid = False
    try:
        config = load_yaml(bound_bytes(CONFIG))
        expected_config = json.loads(_json_identity(policy['config']))
        expected_config['template']['expected_workflow_version'] = policy['target_version']
        config_valid = (_json_identity(config) == _json_identity(expected_config)
                        and expected_state(CONFIG, preserve=True))
    except (ValueError, KeyError, TypeError, OSError):
        pass
    if config_valid:
        approve(CONFIG, {'rule': 'project-config-version-only',
                        'field': 'template.expected_workflow_version',
                        'from': policy['source_version'], 'to': policy['target_version'],
                        'other_values_identical': True, 'permissions_identical': True})
    else:
        failures.append('project configuration changed beyond approved version migration: ' + CONFIG)
    try:
        expected_receipt = {
            'template_version': policy['target_version'],
            'source_manifest_sha256': policy['manifest_sha256'],
            'immutable_files': policy['immutable'], 'source_manifest_json': policy['manifest_raw'],
            'initial_config_sha256': after['files'][CONFIG]['sha256'],
            'mutable_paths': [CONFIG, CODEOWNERS], 'install_id': policy['receipt']['install_id']}
        if '.gitignore' in before['files']:
            expected_receipt['mutable_paths'].append('.gitignore')
        if 'project_id' in policy['receipt']:
            expected_receipt['project_id'] = policy['receipt']['project_id']
        actual = loads(bound_bytes(INSTALLED).decode('utf-8'))
        if (not config_valid or _json_identity(actual) != _json_identity(expected_receipt)
                or not expected_state(INSTALLED)):
            raise ValueError('Invalid receipt')
        approve(INSTALLED, {'rule': 'installed-receipt', 'manifest_sha256': policy['manifest_sha256'],
                            'install_id_preserved': True, 'project_id_preserved': True,
                            'config_sha256': expected_receipt['initial_config_sha256'],
                            'immutable_membership_and_hashes_exact': True})
    except (ValueError, KeyError, TypeError, OSError):
        failures.append('installed receipt content or permissions mismatch: ' + INSTALLED)
    try:
        actual = load_yaml(bound_bytes(PROVENANCE))
        operation_at = actual['installation']['operation_at']
        moment = datetime.strptime(operation_at, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
        if moment.strftime('%Y-%m-%dT%H:%M:%S.%fZ') != operation_at:
            raise ValueError('Noncanonical operation time')
        timestamp = moment.timestamp()
        if not policy['started_at'] - 1 <= timestamp <= time.time():
            raise ValueError('Operation time outside this upgrade')
        expected = {'template': {'name': 'generic-agentic-development-workflow',
                                'version': policy['target_version'], 'schema_revision': 3},
                    'installation': {'install_id': policy['receipt']['install_id'], 'last_operation': 'upgrade',
                                     'operation_at': operation_at, 'source_manifest_sha256': policy['manifest_sha256'],
                                     'profile': 'manual_reference'}}
        if _json_identity(actual) != _json_identity(expected) or not expected_state(PROVENANCE):
            raise ValueError('Invalid provenance')
        approve(PROVENANCE, {'rule': 'upgrade-provenance', 'manifest_sha256': policy['manifest_sha256'],
                             'install_id_preserved': True, 'operation': 'upgrade',
                             'operation_at_within_cohort': True})
    except (ValueError, KeyError, TypeError, OSError):
        failures.append('upgrade provenance content or permissions mismatch: ' + PROVENANCE)
    for path in sorted(policy['directories'] - before['directories'].keys()):
        if expected_state(path):
            approve(path, {'rule': 'required-output-parent' if path != '.agentic-install/objects/'
                          else 'empty-recovery-object-store',
                          'permissions_rule': 'installer-mkdir-inheritance',
                          'expected_permissions': policy['creation'][path]['permissions'],
                          'expected_mode': policy['creation'][path]['mode']})
    result = compare(before, after, delta)
    # External exact deltas may constrain this policy; they cannot widen it.
    for path, record in (allowed_delta or {}).items():
        if delta.get(path) != record:
            failures.append('external delta is not an approved migration: ' + path)
    result['violations'].extend(failures)
    result['preserved'] = not result['violations']
    result['approved_changes'] = approved
    result['migration'] = {'source_version': policy['source_version'], 'target_version': policy['target_version'],
                           'manifest_sha256': policy['manifest_sha256'],
                           'baseline_sha256': policy['baseline_sha256'],
                           'agents_ownership': 'managed: exact target manifest bytes',
                           'state_migrations': policy['state_migrations'],
                           'archive_actions': policy['archive_actions'],
                           'recovery_objects': 'must be absent after successful commit'}
    return result


def write_report(output, report, *, project):
    output = Path(output).resolve()
    for excluded in (ROOT.resolve(), Path(project).resolve()):
        if output == excluded or excluded in output.parents:
            raise ValueError('Report output must be outside source and disposable project')
    # Exclusive creation retains prior evidence; never overwrite a report.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')


def plan(source, project, expected_manifest_sha256):
    before = inventory(project)
    result = install(Path(source), Path(project), expected_manifest_sha256,
                     mode='upgrade', configure=True, discover=False, dry_run=True)
    after = inventory(project)
    preservation = compare(before, after, {})
    if not preservation['preserved']:
        raise ValueError('Non-writing plan mutated the disposable project')
    # Installer diagnostics can carry host paths. Retain a sanitized projection.
    return {'status': 'PLAN', 'before': before, 'after': after,
            'preservation': preservation, 'source_manifest_sha256': result['source_manifest_sha256'],
            'upgrade': result.get('upgrade'), 'execution_authority': False,
            'qualification_status': 'NOT_QUALIFIED'}


def checked_bootstrap(result, project, pin):
    """Validate actual bootstrap subprocess receipts, including canonical argv."""
    runtime = project / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    script = project / '.agentic/scripts/workflow.py'
    checks = result.get('post_install_checks', [])
    if len(checks) != 2 or result.get('status') != 'CONFIGURED':
        raise ValueError('Bootstrap did not establish CONFIGURED with two installed checks')
    for check, action in zip(checks, ('verify-installation', 'validate-config')):
        expected = [str(runtime), '-B', '-I', str(script), '--root', str(project), action]
        if check.get('command') != expected or check.get('exit_code') != 0 or check.get('diagnostic') is not None:
            raise ValueError('Canonical installed check command or exit failed')
    integrity = checks[0].get('output') or {}
    validation = checks[1].get('output') or {}
    if integrity.get('integrity_valid') is not True or integrity.get('source_manifest_sha256') != pin:
        raise ValueError('Installed integrity/source binding failed')
    policy = result.get('configuration', {}).get('policy_sha256')
    if (validation.get('status') != 'ACCEPTED' or not isinstance(policy, str) or
            len(policy) != 64 or validation.get('policy_sha256') != policy or
            result.get('pre_install_configuration', {}).get('policy_sha256') != policy or
            result.get('source_manifest_sha256') != pin):
        raise ValueError('Installed configuration/policy binding failed')
    return {'status': 'CONFIGURED', 'source_manifest_sha256': pin, 'policy_sha256': policy,
            'checks': [{'action': action, 'exit_code': check['exit_code'],
                        'integrity_valid': check['output'].get('integrity_valid'),
                        'status': check['output'].get('status'),
                        'output_sha256': hashlib.sha256(json.dumps(check['output'], sort_keys=True).encode()).hexdigest()}
                       for action, check in zip(('verify-installation', 'validate-config'), checks)],
            'installed_hashes': {path: hashlib.sha256((project / path).read_bytes()).hexdigest()
                                 for path in (CONFIG, INSTALLED, PROVENANCE)}}


def qualify(source, work_dir, wheelhouse=None, *, allowed_delta=None):
    """Build only a disposable source projection, then bootstrap authentic 1.9.5.

    Neither the checkout manifests nor historical release bytes are rewritten.
    The fresh directory and synthetic repository identity exclude live adopters.
    """
    import build_release
    sys.path.insert(0, str(ROOT / '.agentic/tests'))
    from upgrade_195_fixtures import materialize_published, PUBLISHED_PIN
    source = Path(source).resolve(strict=True)
    work_dir = Path(work_dir).resolve()
    if work_dir == source or source in work_dir.parents:
        raise ValueError('Qualification work directory must be outside source')
    work_dir.mkdir()  # Refuse reuse, including a previous failed attempt.
    candidate = work_dir / 'candidate'
    candidate.mkdir()
    with Tree(source) as tree:
        for relative in build_release.release_paths(tree):
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(build_release.release_bytes(tree, relative))
        for relative in build_release.SOURCE_CONFIGS.values():
            template = candidate / relative
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_bytes(tree.read(relative))
    report = {'status': 'NOT_QUALIFIED', 'execution_authority': False,
              'evidence_kind': 'SYNTHETIC_OWNER_CUSTOMIZATION', 'stages': []}

    def execute(stage, command, timeout=600):
        started = time.monotonic()
        child = run_bounded(command, cwd=work_dir, env=child_env(), timeout=timeout,
                            stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
        report['stages'].append({'stage': stage, 'exit_code': child.returncode,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout_sha256': hashlib.sha256(child.stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(child.stderr).hexdigest()})
        # Raw diagnostics remain in the external disposable cohort, never upstream.
        (work_dir / (stage + '.stdout')).write_bytes(child.stdout)
        (work_dir / (stage + '.stderr')).write_bytes(child.stderr)
        return child

    built = execute('candidate_manifest', [sys.executable, '-B',
                    str(candidate / 'scripts/build_release.py'), '--manifest-only'])
    if built.returncode != 0:
        report['failed_stage'] = 'candidate_manifest'
        return report
    target_pin = hashlib.sha256((candidate / 'MANIFEST.json').read_bytes()).hexdigest()
    report['source_manifest_sha256'] = target_pin
    historical = materialize_published(source, work_dir / 'published')
    project = work_dir / 'project'
    migration_policy = None
    for stage, release, pin, mode in (('baseline', historical, PUBLISHED_PIN, 'install'),
                                     ('upgrade', candidate, target_pin, 'upgrade')):
        wheels = work_dir / (stage + '-wheels')
        wheels.mkdir()
        download = [sys.executable, '-B', '-m', 'pip', 'download', '--require-hashes',
            '--only-binary=:all:', '--timeout', '30', '--retries', '1', '--dest', str(wheels),
            '-r', str(release / '.agentic/requirements.lock')]
        if wheelhouse is not None:
            download.extend(['--find-links', str(Path(wheelhouse).resolve(strict=True))])
        downloaded = execute(stage + '_wheels', download, timeout=180)
        if downloaded.returncode != 0:
            report['failed_stage'] = stage + '_wheels'
            return report
        command = [sys.executable, '-B', str(release / 'scripts/bootstrap_project.py'),
            '--dest', str(project), '--expected-manifest-sha256', pin, '--mode', mode,
            '--github-repo', 'example-org/example-repo', '--repository-id', '101',
            '--default-branch', 'main', '--test-command', 'python -m unittest',
            '--codeowner', '@fixture-owner']
        command.extend(['--runtime-wheelhouse', str(wheels)])
        child = execute(stage, command)
        if child.returncode != 0:
            report['failed_stage'] = stage
            return report
        try:
            report[stage] = checked_bootstrap(json.loads(child.stdout), project, pin)
        except (ValueError, KeyError, TypeError) as error:
            report['failed_stage'] = stage
            report['diagnostic'] = str(error)
            return report
        if stage == 'baseline':
            owner = project / 'PROJECT_INSTRUCTIONS.md'
            if not owner.exists():
                with owner.open('xb') as stream:
                    stream.write(b'Synthetic owner customization; not adopter evidence.\n')
            report['before'] = inventory(project)
            migration_policy = prepare_upgrade(candidate, project, target_pin, report['before'])
        else:
            report['after'] = inventory(project)
            report['preservation'] = compare_upgrade(report['before'], report['after'],
                migration_policy, project, allowed_delta=allowed_delta)
            if report['preservation']['preserved'] is not True:
                report['failed_stage'] = 'preservation'
                return report
    report['status'] = 'QUALIFIED_DISPOSABLE_UPGRADE'
    report['active'] = False
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    snap = sub.add_parser('snapshot')
    snap.add_argument('--project', type=Path, required=True)
    snap.add_argument('--output', type=Path, required=True)
    planner = sub.add_parser('plan')
    planner.add_argument('--project', type=Path, required=True)
    planner.add_argument('--source', type=Path, required=True)
    planner.add_argument('--expected-manifest-sha256', required=True)
    planner.add_argument('--output', type=Path, required=True)
    diff = sub.add_parser('compare')
    diff.add_argument('--project', type=Path, required=True, help='disposable project, for output isolation')
    diff.add_argument('--before', type=Path, required=True)
    diff.add_argument('--after', type=Path, required=True)
    diff.add_argument('--allowed-delta', type=Path, required=True)
    diff.add_argument('--output', type=Path, required=True)
    runtime = sub.add_parser('installed')
    runtime.add_argument('--source', type=Path, required=True)
    runtime.add_argument('--work-dir', type=Path, required=True, help='new external disposable directory')
    runtime.add_argument('--wheelhouse', type=Path, help='optional local wheel seed; pip verifies each release lock')
    runtime.add_argument('--output', type=Path, required=True)
    runtime.add_argument('--allowed-delta', type=Path,
                         help='optional exact constraints; cannot widen the derived migration policy')
    args = parser.parse_args(argv)
    if args.command == 'installed':
        delta = json.loads(args.allowed_delta.read_bytes()) if args.allowed_delta else None
        report = qualify(args.source, args.work_dir, args.wheelhouse, allowed_delta=delta)
        write_report(args.output, report, project=args.work_dir)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'QUALIFIED_DISPOSABLE_UPGRADE' else 1
    if args.command == 'snapshot':
        report = inventory(args.project)
    elif args.command == 'plan':
        report = plan(args.source, args.project, args.expected_manifest_sha256)
    else:
        report = compare(*(json.loads(path.read_bytes()) for path in
                           (args.before, args.after, args.allowed_delta)))
    write_report(args.output, report, project=args.project)
    return 1 if report.get('preserved') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
