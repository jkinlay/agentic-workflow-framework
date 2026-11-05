"""Read-only upgrade planning and external preservation evidence; never a live upgrader.

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import load_yaml
from agentic.installer import install

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
    args = parser.parse_args(argv)
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
