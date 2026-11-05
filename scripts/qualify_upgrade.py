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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import load_yaml
from agentic.installer import install, CONFIG, INSTALLED, PROVENANCE
from agentic.safeio import Tree, run_bounded
from agentic.child_process import child_env

FORMAT = 'awf-upgrade-preservation-1'
EXCLUDED = {'.git', '.venv', '__pycache__'}


def inventory(project, *, evidence_kind='SYNTHETIC_OWNER_CUSTOMIZATION'):
    """Hash a quiescent disposable project without opening any database for write.

    Runtime/Git trees are excluded explicitly. Ledgers (including WAL/SHM),
    reservations, archives, receipts, owner files and permissions are included.
    Exclusive ownership is required; this is not a snapshot of a live writer.
    """
    project = Path(project).resolve(strict=True)
    files = {}
    directories = {}
    for directory, dirs, names in os.walk(project, followlinks=False):
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
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED)
        for name in dirs:
            path = parent / name
            directories[path.relative_to(project).as_posix() + '/'] = {
                'mode': stat.S_IMODE(path.stat().st_mode)}
        for name in sorted(names):
            path = parent / name
            relative = path.relative_to(project).as_posix()
            if name == '.git':  # Linked worktree pointer, never project evidence.
                continue
            raw = path.read_bytes()
            files[relative] = {'sha256': hashlib.sha256(raw).hexdigest(),
                               'mode': stat.S_IMODE(path.stat().st_mode), 'size': len(raw)}
    config = load_yaml((project / '.agentic/PROJECT_CONFIG.yaml').read_bytes())
    github = config['github']
    identity = {key: github.get(key) for key in ('repository', 'repository_id', 'base_branch')}
    if any(value is None or value == '' for value in identity.values()):
        raise ValueError('Complete repository identity is required')
    return {'format': FORMAT, 'evidence_kind': evidence_kind,
            'identity': identity, 'files': files, 'directories': directories,
            'excluded_directory_names': sorted(EXCLUDED),
            'execution_authority': False}


def compare(before, after, allowed_delta):
    """An allowed delta binds both exact file records, including modes and sizes."""
    if before.get('format') != FORMAT or after.get('format') != FORMAT:
        raise ValueError('Unsupported preservation inventory')
    violations = []
    if before['identity'] != after['identity']:
        violations.append('repository identity changed')
    if before['evidence_kind'] != after['evidence_kind']:
        violations.append('evidence kind changed')
    changes = {}
    before_entries = {**before['files'], **before['directories']}
    after_entries = {**after['files'], **after['directories']}
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


def qualify(source, work_dir, wheelhouse=None):
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
    args = parser.parse_args(argv)
    if args.command == 'installed':
        report = qualify(args.source, args.work_dir, args.wheelhouse)
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
