"""Disposable upgrade qualification and external preservation evidence; never live rollout.

Reports contain digests rather than owner file contents. Keep reports and allowed
delta files in approved external storage. This tool grants no release authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import sys
import time
import tempfile
import uuid
from datetime import datetime, timezone
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic.canonical import load_yaml, loads
from agentic.installer import (install, CONFIG, INSTALLED, PROVENANCE, CODEOWNERS,
                               managed, verify_release)
from agentic.safeio import Tree, run_bounded
from agentic import installer as installation
from agentic.child_process import child_env, isolated_git_env
from agentic.preservation_metadata import (FORMAT, EXCLUDED, inventory,
                                           _permissions, _named_streams)

REQUIRED_CHECKS = ('source_managed_membership', 'historical_state_continuity', 'named_streams')
QUALIFICATION_LEDGER = '.agentic-state/routing/ledger.sqlite'


def source_identity(source):
    """Bind a clean checkout and its existing manifest; never repair the candidate."""
    import build_release
    source = Path(source).resolve(strict=True)

    def git(*arguments):
        result = run_bounded(['git', '--no-replace-objects', '-c', 'core.useReplaceRefs=false',
                             '-c', 'safe.directory=' + source.as_posix(), '-C', str(source),
                              *arguments], cwd=source, env=isolated_git_env(), timeout=30,
                             stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
        if result.returncode:
            raise ValueError('Source Git identity unavailable; use a clean committed candidate')
        return result.stdout.decode('utf-8').strip()

    if git('status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Source is dirty; commit the reviewed candidate and regenerate its manifest before qualification')
    commit, tree_id = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    try:
        with Tree(source) as tree:
            raw = tree.read('MANIFEST.json')
            build_release.verify_virtual_release(tree, raw)
    except (ValueError, OSError) as exc:
        raise ValueError('Source manifest is stale or invalid; regenerate and commit the candidate manifest before qualification') from exc
    return {'commit': commit, 'tree': tree_id, 'manifest_sha256': hashlib.sha256(raw).hexdigest()}


def _streams_verified(entry):
    evidence = entry.get('named_streams') or {}
    expected_method = 'windows-find-streams' if os.name == 'nt' else 'posix-no-windows-streams'
    streams = evidence.get('streams')

    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

    return (evidence.get('verified') is True and evidence.get('method') == expected_method
            and isinstance(streams, dict) and (os.name == 'nt' or not streams)
            and all(digest(name) and isinstance(record, dict) and digest(record.get('sha256'))
                    and type(record.get('size')) is int and record['size'] >= 0
                    for name, record in streams.items()))


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
            if not _streams_verified(entry):
                violations.append(label + ' named streams unverified: ' + path)
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
            'evidence_scope': 'PRESERVATION_COMPARISON_ONLY'}


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
        return {'mode': stat.S_IMODE(probe.stat().st_mode), 'permissions': _permissions(probe),
                'named_streams': _named_streams(probe)}
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


def _ledger_projection(raw, columns=None):
    """Independent read-only SQL oracle. No initializer, validator or migration calls.

    No historical field may change. New columns/tables are outside the historical
    projection; every old column, rowid, value, type and duplicate count survives.
    Only digests leave this function, never accounting contents.
    """
    def quote(name):
        return '"' + name.replace('"', '""') + '"'

    def cell(value):
        if isinstance(value, bytes):
            value = value.hex()
            kind = 'blob'
        elif isinstance(value, float):
            value = value.hex()
            kind = 'float'
        else:
            kind = type(value).__name__
        return [kind, value]

    connection = sqlite3.connect(':memory:')
    try:
        # A fully checkpointed WAL image has no sidecar but retains mode 2.
        # Deserialize an in-memory rollback-mode copy; do not rewrite history.
        memory_raw = raw[:18] + b'\x01\x01' + raw[20:] if raw[18:20] == b'\x02\x02' else raw
        connection.deserialize(memory_raw)
        connection.execute('PRAGMA query_only=ON')
        if columns is None:
            columns = {}
            for name, sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"):
                names = [row[1] for row in connection.execute('PRAGMA table_info(' + quote(name) + ')')]
                if 'WITHOUT ROWID' not in (sql or '').upper():
                    names.insert(0, 'rowid')
                columns[name] = names
        result = {}
        for table, names in sorted(columns.items()):
            available = {row[1] for row in connection.execute('PRAGMA table_info(' + quote(table) + ')')}
            if not set(names) - {'rowid'} <= available:
                raise ValueError('Historical ledger columns missing')
            rows = connection.execute('SELECT ' + ','.join(map(quote, names)) + ' FROM ' + quote(table))
            counts = Counter(hashlib.sha256(_json_identity([cell(v) for v in row]).encode()).hexdigest()
                             for row in rows)
            result[table] = {'columns': names, 'rows': dict(counts)}
        return result
    except sqlite3.DatabaseError as error:
        raise ValueError('Historical ledger continuity unavailable') from error
    finally:
        connection.close()


def _historical_state(state):
    # All non-SQL state (reservations, audit JSON, sidecars, unknown charges,
    # archives) remains byte-identical. There are no field exceptions for 1.9.5.
    return {path: ({'tables': _ledger_projection(raw)} if raw.startswith(b'SQLite format 3\0')
                   else {'sha256': hashlib.sha256(raw).hexdigest()})
            for path, raw in state.items()}


def _check_state_continuity(historical, expected_paths, read):
    failures = []
    if not isinstance(historical, dict) or set(historical) != expected_paths:
        return ['historical state continuity evidence missing']
    for path, expected in sorted(historical.items()):
        try:
            raw = read(path)
            if 'tables' in expected:
                tables = _ledger_projection(raw, {name: record['columns']
                                                 for name, record in expected['tables'].items()})
                valid = all(not (Counter(record['rows']) - Counter(tables[name]['rows']))
                            for name, record in expected['tables'].items())
            else:
                valid = hashlib.sha256(raw).hexdigest() == expected['sha256']
            if not valid:
                raise ValueError('Historical values lost')
        except (ValueError, KeyError, TypeError, OSError):
            failures.append('historical state continuity mismatch: ' + path)
    return failures


def prepare_budget_import(sources, *, project_id):
    """Independent query-only BEFORE evidence, never invoking migration code.

    Preserve privately: contains historical identities/rows. Public reports carry
    only hashes and counts. Inputs must be exclusive, checkpointed snapshots.
    """
    if not isinstance(sources, dict) or not sources or not isinstance(project_id, str) or not project_id:
        raise ValueError('Pinned budget sources and project identity are required')
    baseline = {'format': 'awf-budget-import-continuity-1', 'project_id': project_id, 'sources': {}}
    for label, spec in sorted(sources.items()):
        path = Path(spec['path'])
        if Path(str(path) + '-wal').exists():
            raise ValueError('Budget snapshot must be quiescent and checkpointed')
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec['sha256']:
            raise ValueError('Budget snapshot digest mismatch')
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        try:
            memory_raw = raw[:18] + b'\x01\x01' + raw[20:] if raw[18:20] == b'\x02\x02' else raw
            db.deserialize(memory_raw)
            db.execute('PRAGMA query_only=ON')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            charges = []
            for table in ('reservations', 'model_runs', 'budget_charges'):
                if table not in tables:
                    continue
                if table == 'reservations':
                    identity = db.execute("SELECT value FROM meta WHERE key='project'").fetchone()
                    if identity is None or identity[0] != project_id:
                        raise ValueError('State-store project mismatch')
                for row in db.execute('SELECT rowid AS _source_id,* FROM "' + table + '"'):
                    value = dict(row)
                    source_id = str(value.pop('_source_id'))
                    if table != 'reservations' and value['project_id'] != project_id:
                        raise ValueError('Routing budget project mismatch')
                    charges.append({'table': table, 'source_id': source_id, 'row': value})
            if not tables & {'reservations', 'model_runs', 'budget_charges'}:
                raise ValueError('No recognized historical budget table')
            projection = _ledger_projection(raw)
            auxiliary = ({table: [name for name in item['columns'] if name != 'rowid']
                          for table, item in projection.items()
                          if table not in {'model_runs', 'budget_charges', 'budget_history_imports', 'sqlite_sequence'}}
                         if 'model_runs' in tables or 'budget_charges' in tables else {})
            baseline['sources'][label] = {'sha256': digest, 'tables': projection, 'charges': charges,
                                         'canonical_tables': _ledger_projection(raw, auxiliary)}
        except sqlite3.DatabaseError as exc:
            raise ValueError('Budget snapshot unreadable') from exc
        finally:
            db.close()
    return baseline


def check_budget_import(baseline, sources, canonical):
    """Verify source continuity AND every canonical charge without the importer.

    A matching migration receipt or digest cannot excuse a dropped charge. This
    check compares actual rows, usage/UNKNOWN and dedup identities independently.
    """
    failures = []
    checked = 0
    expected_ids = set()
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    try:
        if (not isinstance(baseline, dict) or baseline.get('format') != 'awf-budget-import-continuity-1'
                or not sources or set(baseline['sources']) != set(sources)):
            raise ValueError('Budget import baseline missing')
        canonical_raw = Path(canonical).read_bytes()
        memory_raw = (canonical_raw[:18] + b'\x01\x01' + canonical_raw[20:]
                      if canonical_raw[18:20] == b'\x02\x02' else canonical_raw)
        db.deserialize(memory_raw)
        db.execute('PRAGMA query_only=ON')
        for label, snapshot in baseline['sources'].items():
            raw = Path(sources[label]['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != snapshot['sha256']:
                failures.append('budget source continuity mismatch: ' + label)
            expected_tables = snapshot['canonical_tables']
            current_tables = _ledger_projection(canonical_raw,
                {table: item['columns'] for table, item in expected_tables.items()})
            if any(Counter(item['rows']) - Counter(current_tables[table]['rows'])
                   for table, item in expected_tables.items()):
                failures.append('imported per-host recovery history differs: ' + label)
            for item in snapshot['charges']:
                old = item['row']
                identity = old['run_id']
                expected_ids.add(identity)
                checked += 1
                current = db.execute('SELECT * FROM model_runs WHERE run_id=?', (identity,)).fetchone()
                if current is None:
                    failures.append('imported budget charge missing: ' + label)
                    continue
                current = dict(current)
                if item['table'] == 'model_runs':
                    valid = all(_json_identity(current.get(k)) == _json_identity(v) for k, v in old.items())
                else:
                    state = item['table'] == 'reservations'
                    expected = {'project_id': baseline['project_id'],
                        'ticket_id': old['ticket'] if state else old['ticket_id'],
                        'reserved_tokens': old['tokens'] if state else old['reserved_tokens'],
                        'actual_tokens': old['actual_tokens']}
                    if state:
                        expected.update(reserved_cost=old['cost'], actual_cost=old['actual_cost'], day=old['day'],
                                        role='UNKNOWN', agent_id='UNKNOWN', created_at='UNKNOWN')
                    valid = all(_json_identity(current.get(k)) == _json_identity(v)
                                for k, v in expected.items())
                    if old['status'] == 'RESERVED':
                        valid = valid and current['status'] == 'reserved'
                    else:
                        valid = valid and current['status'] != 'reserved'
                    if state:
                        request = json.loads(current['request'])
                        valid = (valid and '_budgeting' not in request and '_budget_binding' not in request
                                 and request.get('_legacy_status') == old['status'])
                        overrun = any(old[actual] is not None and old[reserved] is not None
                                      and old[actual] > old[reserved] for actual, reserved in
                                      (('actual_tokens', 'tokens'), ('actual_cost', 'cost')))
                        if old['status'] != 'RESERVED':
                            valid = valid and current['status'] == ('quarantined' if overrun else 'settled')
                # No retrospective zero or refusal-to-count flag is permissible.
                if not valid:
                    failures.append('imported budget charge differs: ' + label)
                proof = db.execute('SELECT * FROM budget_history_imports WHERE source_sha256=? '
                    'AND source_table=? AND source_id=?',
                    (snapshot['sha256'], item['table'], item['source_id'])).fetchone()
                if proof is None or proof['run_id'] != identity:
                    failures.append('budget deduplication proof missing: ' + label)
                else:
                    source_typed = {k: [type(v).__name__, v] for k, v in old.items()}
                    source_digest = hashlib.sha256(_json_identity(source_typed).encode()).hexdigest()
                    if source_digest != proof['source_row_sha256']:
                        failures.append('budget deduplication source-row proof mismatch: ' + label)
                    typed = {k: [type(v).__name__, v] for k, v in current.items()}
                    digest = hashlib.sha256(_json_identity(typed).encode()).hexdigest()
                    if digest != proof['canonical_row_sha256']:
                        failures.append('budget deduplication proof differs from charge: ' + label)
        actual_ids = [r[0] for r in db.execute('SELECT run_id FROM model_runs')]
        if any(actual_ids.count(identity) != 1 for identity in expected_ids):
            failures.append('budget import is not exactly once')
    except (ValueError, KeyError, TypeError, OSError, sqlite3.DatabaseError):
        failures.append('budget import continuity evidence missing or unreadable')
    finally:
        db.close()
    return {'preserved': not failures, 'violations': failures, 'charges_checked': checked,
            'distinct_imported_runs': len(expected_ids), 'execution_authority': False,
            'evidence_scope': 'BUDGET_IMPORT_CONTINUITY_ONLY'}


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
        raise ValueError('BEFORE snapshot does not match --baseline-project; retain an untouched 1.9.5 copy, '
                         'snapshot that copy before apply, then compare with its matching BEFORE report')
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
        raise ValueError('BEFORE baseline must be 1.9.5, not a post-upgrade snapshot; use the untouched '
                         'pre-upgrade copy and its matching BEFORE report; preservation before a snapshot is unproven')
    immutable = {path: digest for path, digest in manifest['files'].items()
                 if managed(path) and path not in {CONFIG, PROVENANCE, CODEOWNERS}}
    collisions = before['files'].keys() & (immutable.keys() - identity['immutable_files'].keys())
    if collisions:
        raise ValueError('Baseline owner collision with newly managed target: ' + ', '.join(sorted(collisions)))
    state = {path: (project / path).read_bytes() for path in before['files']
             if path.startswith('.agentic-state/')}
    historical_state = _historical_state(state)  # Capture BEFORE invoking any migration code.
    operating = project / 'OPERATING_CONFIG.yaml'
    bundle, steps = apply_chain(versions, identity['version'], config_raw,
        operating.read_bytes() if operating.exists() else None, receipt_raw,
        (project / PROVENANCE).read_bytes(), state,
        {'source_manifest_sha256': verified_pin, '_source_manifest_json': manifest_raw.decode('utf-8'),
         '_immutable_files': immutable}, install_id=identity['install_id'])
    exercised = [action for step in steps for action in step['state_migrations']]
    migrations = [action for action in exercised
                  if action['action'] == 'migrated_schema']
    archives, archive_actions = state_archive_plan(bundle.state, target_version=target)
    files = {path: {'sha256': digest, 'size': len(content[path]), 'rule': 'target-manifest',
                    'manifest_sha256': verified_pin}
             for path, digest in immutable.items()}
    # This compatibility record is derived independently of the installer helper.
    # It grants no import/adoption or permission to alter the operating file.
    # Dormant adoption is a separate OFF-only file change in the owner-merged
    # upgrade/settings PR; default-branch settings take effect after fetch.
    operating_raw = operating.read_bytes() if operating.exists() else None
    settings = load_yaml(operating_raw).get('budgeting') if operating_raw is not None else None
    disposition = {'format': 'awf-budget-upgrade-1', 'target_version': '1.9.6',
        'disposition': 'LEGACY_RETAINED' if settings is None else 'EXPLICIT_SETTINGS_RETAINED',
        'operating_sha256': hashlib.sha256(operating_raw).hexdigest() if operating_raw is not None else None,
        'settings': settings,
        'history_import': 'EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED', 'owner_adoption_required': True}
    disposition_raw = (json.dumps(disposition, indent=2, ensure_ascii=False) + '\n').encode()
    disposition_path = '.agentic-state/upgrade/budgeting-1.9.6.json'
    if disposition_path in state and state[disposition_path] != disposition_raw:
        raise ValueError('Conflicting budget compatibility disposition')
    files[disposition_path] = {'sha256': hashlib.sha256(disposition_raw).hexdigest(),
                              'size': len(disposition_raw), 'rule': 'budget-legacy-compatibility-disposition'}
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
    retention_templates = {installation.RETAINED_JOURNAL, installation.DIRECTORY_ADOPTIONS,
                           installation.EVIDENCE_OBJECTS + '/', installation.EVIDENCE_OBJECTS + '/object.json'}
    for path in sorted(outputs | directories | retention_templates):
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
    # Backup membership is derived from the exact originals the installer will
    # replace/delete, never from an allowed-delta prefix or observed backup bytes.
    backup_sources = {path: before['files'][path] for path in
        (set(files) | {CONFIG, INSTALLED, PROVENANCE} | (set(identity['immutable_files']) - set(immutable)))
        if path in before['files'] and (path not in files or
            before['files'][path]['sha256'] != files[path]['sha256'])}
    backup_creation = {}
    placeholder = '.agentic-backup/00000000-0000-4000-8000-000000000000/'
    backup_paths = {placeholder + path for path in backup_sources}
    backup_paths |= {parent.as_posix() + '/' for path in backup_paths for parent in Path(path).parents
                     if parent.as_posix() != '.'}
    for path in sorted(backup_paths):
        if path in before['directories']:
            continue
        parent = (project / path.rstrip('/')).parent
        depth = 0
        while not parent.exists():
            depth += 1
            parent = parent.parent
        key = (parent, depth, path.endswith('/'), False)
        if key not in cache:
            cache[key] = _creation_state(project, path, directory=path.endswith('/'))
        backup_creation[path] = cache[key]
    if inventory(project, evidence_kind=before['evidence_kind']) != before:
        raise ValueError('Backup permission probes changed the baseline; preserve it and restore the untouched BEFORE copy')
    with Tree(project) as tree:
        retention_before = installation._read_retained_record(tree)
        audit_before = installation._directory_adoption_audit(
            installation._exists_read(tree, installation.DIRECTORY_ADOPTIONS), tree=tree)
        evidence_before = {path: tree.read(path) for path in before['files']
                           if path.startswith(installation.EVIDENCE_OBJECTS + '/') or path in {
                               installation.RETAINED_JOURNAL, installation.DIRECTORY_ADOPTIONS}}
        metadata_before = {}
        ownership_before = {}
        for path in backup_sources:
            if os.name == 'nt':
                raw = installation.json_bytes(tree.windows_metadata(path))
                metadata_before[path] = dict(sha256=installation.sha256(raw), size=len(raw))
            else:
                info = tree.inspect(path)
                ownership_before[path] = [info.st_uid, info.st_gid]
    return {'retention_before': retention_before, 'audit_before': audit_before,
            'metadata_before': metadata_before, 'ownership_before': ownership_before,
            'evidence_before': evidence_before, 'runtime_before': before.get('runtime'),
            'manifest_sha256': verified_pin, 'manifest_raw': manifest_raw.decode('utf-8'),
            'baseline_sha256': hashlib.sha256(_json_identity(before).encode('utf-8')).hexdigest(),
            'target_version': target, 'source_version': identity['version'],
            'files': files, 'immutable': immutable, 'directories': directories,
            'removed': set(identity['immutable_files']) - set(immutable),
            'old_immutable': identity['immutable_files'], 'config': load_yaml(config_raw),
            'expected_config_sha256': hashlib.sha256(bundle.project_config).hexdigest(),
            'receipt': receipt, 'creation': creation, 'started_at': time.time(),
            'historical_state': historical_state,
            'archive_actions': archive_actions, 'state_migrations': migrations,
            'backup_sources': backup_sources, 'backup_creation': backup_creation,
            'exercised_state_paths': exercised}


def _fresh_retention_namespace(before, policy, identity):
    """No prior transaction or owner namespace may be rebound as new evidence."""
    namespaces = ['.agentic-backup/' + identity, *(
        '.agentic/.venv.' + kind + '-' + identity for kind in ('staging', 'backup', 'cleanup'))]
    old_entries = {**before['files'], **before['directories']}
    if any(path.rstrip('/') == prefix or path.startswith(prefix + '/')
           for prefix in namespaces for path in old_entries):
        raise ValueError('Retained transaction namespace was not fresh')
    prior = policy.get('retention_before')
    old_ids = {source.get('journal', {}).get('transaction_id')
               for source in (installation._retained_sources(prior) if prior else [])}
    old_ids.update(event['transaction_id'] for event in policy['audit_before']['transactions'])
    if identity in old_ids:
        raise ValueError('Retained transaction identity was already used')


def _authenticate_retained_journal(before, after, policy, project, journal):
    """Derive complete membership from baseline policy, never emitted rows."""
    identity = journal['transaction_id']
    _fresh_retention_namespace(before, policy, identity)
    prior = policy.get('retention_before')
    if journal['format'] != 'awf-install-journal-4' or journal.get('temporary_files') != 'sibling-uuid5-v2':
        raise ValueError('Retention requires a complete compact transaction')
    if any(path in before['files'] for path in installation._transaction_temporary_paths(journal).values()):
        raise ValueError('Transaction temporary namespace was not fresh')
    outputs = set(policy['files']) | {CONFIG, INSTALLED, PROVENANCE}
    paths = {path for path in outputs if before['files'].get(path, {}).get('sha256') !=
             after['files'].get(path, {}).get('sha256')} | set(policy['removed'])
    prefix = '.agentic-backup/' + identity + '/'
    backups = {prefix + path for path in policy['backup_sources']}
    # Stage A requires the exact originals, including both prior receipts.
    paths |= backups
    if {row['path'] for row in journal['files']} != paths:
        raise ValueError('Retained transaction file membership differs from baseline policy')
    originals = paths & before['files'].keys()
    if journal.get('windows_metadata', {}) != {path: policy['metadata_before'][path]
            for path in originals if path in policy['metadata_before']}:
        raise ValueError('Retained transaction Windows metadata membership or references differ')
    if journal.get('posix_ownership', {}) != {path: policy['ownership_before'][path]
            for path in originals if path in policy['ownership_before']}:
        raise ValueError('Retained transaction POSIX ownership membership differs')
    parents = {parent.as_posix() for path in outputs | set(policy['removed']) | backups | {
        installation._recovery_object_path('0' * 64)} for parent in Path(path).parents
        if parent.as_posix() != '.'}
    directories = sorted((path for path in parents if path not in {'.agentic', '.agentic-install'}
                          and path + '/' not in before['directories']),
                         key=lambda path: (path.count('/'), path))
    if [row['path'] for row in journal.get('created_directories', [])] != directories:
        raise ValueError('Retained transaction created-directory membership differs')
    with Tree(project) as tree:
        expected = [dict(path=path, identity=installation._directory_identity(tree, path)
                         if os.name == 'nt' else None) for path in directories]
    if journal['created_directories'] != expected:
        raise ValueError('Retained transaction directory identities differ')
    prior_paths = (prior or {}).get('retained_directories', [])
    eligible = {path for path in prior_paths if path in parents and path + '/' in before['directories']}
    adoptions = journal.get('directory_adoptions', [])
    if {row['path'] for row in adoptions} != eligible:
        raise ValueError('Retained transaction adoption membership differs')
    with Tree(project) as tree:
        for row in adoptions:
            source = next(source for source in installation._retained_sources(prior)
                          if row['path'] in source['retained_directories'])
            if (row['identity'] != installation._directory_identity(tree, row['path']) or
                    row['evidence_sha256'] != installation.sha256(installation.json_bytes(source))):
                raise ValueError('Retained transaction adoption identity or source differs')


def _retention_outputs(before, after, policy, project):
    """Reconstruct exact transaction outputs from the untouched baseline."""
    with Tree(project) as tree:
        current = installation._read_retained_record(tree)
        audit = installation._directory_adoption_audit(
            installation._exists_read(tree, installation.DIRECTORY_ADOPTIONS), tree=tree)
    prior = policy.get('retention_before')
    prior_sources = installation._retained_sources(prior) if prior else []
    sources = installation._retained_sources(current) if current else []
    # An adoption may resolve every directory. Its event retains the sources.
    prior_events = policy.get('audit_before', dict(transactions=[]))['transactions']
    if audit['transactions'][:len(prior_events)] != prior_events:
        raise ValueError('Prior directory adoption history changed')
    events = audit['transactions'][len(prior_events):]
    if len(events) > 1:
        raise ValueError('Unexpected directory adoption transactions')
    if events:
        sources = installation._retained_sources(events[0]['evidence'])
    old_hashes = {installation.sha256(installation.json_bytes(item)) for item in prior_sources}
    added = [item for item in sources if installation.sha256(installation.json_bytes(item)) not in old_hashes]
    if len(added) > 1:
        raise ValueError('Unexpected retained source transactions')
    outputs = {}
    transaction_id = None
    for source in added:
        if source['format'] != 'awf-retained-cleanup-2':
            raise ValueError('New retention lacks committed transaction evidence')
        installation._retained_record_result(source)
        journal = source['journal']
        _authenticate_retained_journal(before, after, policy, project, journal)
        transaction_id = journal['transaction_id']
        runtime = journal['runtime']
        baseline = policy.get('runtime_before')
        if (not baseline or journal['phase'] != 'commit_cleanup' or
                runtime['previous_sha256'] != baseline['sha256'] or
                runtime['new_sha256'] != (after.get('runtime') or {}).get('sha256') or
                source['retained_directories'] != [runtime['backup']]):
            raise ValueError('Runtime retention differs from the exact baseline transaction')
        for row in journal['files']:
            old, new = before['files'].get(row['path']), after['files'].get(row['path'])
            old_digest = (row['old_sha256'] if installation._compact_journal(journal) else
                          installation.sha256(installation.base64.b64decode(row['old'])) if row['old'] else None)
            if (old_digest != (old or {}).get('sha256') or row['new_sha256'] != (new or {}).get('sha256') or
                    row['old_mode'] != (old or {}).get('mode') or row['new_mode'] != (new or {}).get('mode') or
                    (installation._compact_journal(journal) and row['old_size'] != (old or {}).get('size'))):
                raise ValueError('Retained transaction file evidence differs from snapshots')
        if installation._compact_journal(journal):
            for snapshot, field in ((before, 'managed_before_sha256'), (after, 'managed_after_sha256')):
                rows = [dict(path=path, mode=value['mode'], sha256=value['sha256'])
                        for path, value in snapshot['files'].items() if installation._inventory_managed(path)]
                rows.sort(key=lambda row: row['path'].casefold())
                if installation._managed_inventory_sha256(rows) != journal[field]:
                    raise ValueError('Retained transaction managed membership differs from snapshot')
        prefix = runtime['backup']
        expected = {prefix + name if name == '/' else prefix + '/' + name: entry
                    for name, entry in baseline['entries'].items()}
        observed = {path: value for path, value in {**after['files'], **after['directories']}.items()
                    if path == prefix + '/' or path.startswith(prefix + '/')}
        if observed != expected:
            raise ValueError('Retained runtime bytes, membership or metadata differ from baseline')
        size = sum(entry.get('size', 0) for entry in baseline['entries'].values())
        if source['cleanup_pending'] != [dict(path=prefix, size_bytes=size,
                reason=installation._CLEANUP_REASON, owner_action=installation._CLEANUP_OWNER_ACTION)]:
            raise ValueError('Retained runtime diagnostic inventory differs')
        outputs.update(expected)

    class CollectedEvidence:
        def __init__(self): self.files = dict(policy.get('evidence_before', {})); self.written = {}
        def inspect(self, path): return path in self.files or None
        def read(self, path, **kwargs): return self.files[path]
        def write(self, path, raw): self.files[path] = raw; self.written[path] = raw
        write_exclusive = write
    collected = CollectedEvidence()
    for source in added:
        installation._merge_retained_evidence(collected, source)
    merged = installation._read_retained_record(collected)
    if events:
        event = events[0]
        _fresh_retention_namespace(before, policy, event['transaction_id'])
        backup_ids = {path.split('/')[1] for path in after['files'].keys() - before['files'].keys()
                      if path.startswith('.agentic-backup/')}
        if backup_ids != {event['transaction_id']}:
            raise ValueError('Adoption identity differs from the exact upgrade backup namespace')
        if transaction_id is not None and event['transaction_id'] != transaction_id:
            raise ValueError('Adoption/cleanup transaction identities differ')
        eligible = set((prior or {}).get('retained_directories', [])) & {
            path.rstrip('/') for path in policy['directories']}
        if set(event['directories']) != eligible or event['evidence'] != merged:
            raise ValueError('Adoption does not exactly resolve the reused baseline directories')
        expected_audit = dict(format='awf-directory-adoptions-1', transactions=[*prior_events, event])
        installation._write_directory_adoption_audit(collected, expected_audit)
        remaining = [path for path in merged['retained_directories'] if path not in eligible]
        merged = (dict(format='awf-retained-directories-2', records=installation._retained_sources(merged),
                       retained_directories=remaining) if remaining else None)
        if merged:
            collected.write(installation.RETAINED_JOURNAL,
                            installation.json_bytes(installation._pack_retained_record(collected, merged)))
        else:
            collected.files.pop(installation.RETAINED_JOURNAL, None)
            collected.written[installation.RETAINED_JOURNAL] = None
    if current != merged:
        raise ValueError('Unresolved retention inventory or source history changed')
    for path, raw in collected.written.items():
        if raw is None:
            if path in after['files']:
                raise ValueError('Resolved evidence file remains')
            outputs[path] = None
            continue
        entry = after['files'].get(path)
        if not entry or entry['sha256'] != hashlib.sha256(raw).hexdigest() or entry['size'] != len(raw):
            raise ValueError('Retained evidence bytes differ: ' + path)
        template = (installation.EVIDENCE_OBJECTS + '/object.json'
                    if path.startswith(installation.EVIDENCE_OBJECTS + '/') else path)
        expected = ({key: before['files'][path][key] for key in ('mode', 'permissions', 'named_streams')}
                    if path in before['files'] else policy['creation'].get(template))
        if not expected or any(entry.get(key) != value for key, value in expected.items()):
            raise ValueError('Retained evidence metadata differs: ' + path)
        outputs[path] = entry
    evidence_directory = installation.EVIDENCE_OBJECTS + '/'
    if any(path.startswith(evidence_directory) for path in collected.written):
        expected = policy['creation'].get(evidence_directory)
        if after['directories'].get(evidence_directory) != expected:
            raise ValueError('Evidence directory metadata differs')
        outputs[evidence_directory] = expected
    return outputs


def compare_upgrade(before, after, policy, project, *, allowed_delta=None):
    """Audit every change against the pre-upgrade policy, never a prefix waiver."""
    project = Path(project)
    approved = {}
    delta = {}
    failures = []
    try:
        if inventory(project, evidence_kind=after['evidence_kind']) != after:
            failures.append('AFTER inventory differs from current project owner/managed/state bytes or metadata; take a fresh AFTER snapshot')
    except (ValueError, OSError) as exc:
        failures.append('AFTER project inventory refused: ' + str(exc))
    if hashlib.sha256(_json_identity(before).encode('utf-8')).hexdigest() != policy['baseline_sha256']:
        failures.append('migration policy baseline binding mismatch')
    old_entries = {**before['files'], **before['directories']}
    new_entries = {**after['files'], **after['directories']}
    collisions = before['files'].keys() & (policy['immutable'].keys() - policy['old_immutable'].keys())
    checks = {'source_managed_membership': 'FAIL' if collisions else 'PASS'}
    for path in sorted(collisions):
        failures.append('baseline owner collision with newly managed target: ' + path)

    def approve(path, evidence):
        if path in collisions:
            return
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
        expected = ({key: old.get(key) for key in ('mode', 'permissions', 'named_streams')} if old is not None
                    else policy['creation'].get(path))
        if not preserve and path in policy['files'] and policy['files'][path]['rule'] == 'upgrade-archive':
            # Prior archives (including already-existing target archives) never change.
            if old is not None:
                return new_entries.get(path) == old
        new = new_entries.get(path)
        return bool(expected and new and all(new.get(key) == value for key, value in expected.items()))

    historical = policy.get('historical_state')
    expected_paths = {path for path in before['files'] if path.startswith('.agentic-state/')}
    continuity_failures = _check_state_continuity(historical, expected_paths, bound_bytes)
    failures.extend(continuity_failures)
    checks['historical_state_continuity'] = 'FAIL' if continuity_failures else 'PASS'
    checks['named_streams'] = ('PASS' if all(_streams_verified(entry) for entry in
        [*old_entries.values(), *new_entries.values()]) and all(
            old.get('named_streams') == new_entries.get(path, {}).get('named_streams')
            for path, old in old_entries.items() if (old.get('named_streams') or {}).get('streams')) else 'FAIL')
    if checks['named_streams'] != 'PASS':
        failures.append('named stream preservation incomplete or changed')

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
                        and after['files'][CONFIG]['sha256'] == policy['expected_config_sha256']
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
    new_backups = {path for path in new_entries.keys() - old_entries.keys()
                   if path.startswith('.agentic-backup/')}
    if new_backups:
        ids = {path.split('/')[1] for path in new_backups if path != '.agentic-backup/'}
        if len(ids) == 1 and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', next(iter(ids))):
            prefix = '.agentic-backup/' + next(iter(ids)) + '/'
            expected_backups = {prefix + path: original for path, original in policy['backup_sources'].items()}
            expected_dirs = {parent.as_posix() + '/' for path in expected_backups for parent in Path(path).parents
                             if parent.as_posix() != '.'} - before['directories'].keys()
            if prefix not in old_entries and new_backups == expected_backups.keys() | expected_dirs:
                for path in sorted(new_backups):
                    template = path.replace(prefix, '.agentic-backup/00000000-0000-4000-8000-000000000000/', 1)
                    expected = policy['backup_creation'].get(template)
                    entry = new_entries[path]
                    original = expected_backups.get(path)
                    if (expected and all(entry.get(k) == v for k, v in expected.items()) and
                            (original is None or all(entry.get(k) == original[k] for k in ('sha256', 'size')))):
                        approve(path, {'rule': 'exact-upgrade-backup', 'source': path[len(prefix):] if original else None})
    try:
        retained_outputs = _retention_outputs(before, after, policy, project)
        for path, entry in retained_outputs.items():
            if new_entries.get(path) == entry:
                approve(path, {'rule': 'exact-transaction-retention', 'bytes_membership_metadata_exact': True})
    except (ValueError, KeyError, TypeError, OSError) as exc:
        failures.append('retained transaction preservation refused: ' + str(exc))
    # A valid replacement receipt cannot establish retention of the original.
    # Stage A keeps both identity documents in this target's exact upgrade
    # backup, independently of the untouched external rehearsal baseline.
    retained_receipts = {record['source'] for record in approved.values()
                         if record.get('rule') == 'exact-upgrade-backup'}
    missing_receipts = [path for path in (INSTALLED, PROVENANCE)
                        if path in before['files'] and before['files'][path] != after['files'].get(path)
                        and path not in retained_receipts]
    checks['prior_receipts_retained'] = 'FAIL' if missing_receipts else 'PASS'
    failures.extend('prior receipt not retained: ' + path for path in missing_receipts)
    result = compare(before, after, delta)
    # External exact deltas may constrain this policy; they cannot widen it.
    for path, record in (allowed_delta or {}).items():
        if delta.get(path) != record:
            failures.append('external delta is not an approved migration: ' + path)
    result['violations'].extend(failures)
    result['preserved'] = not result['violations']
    result['approved_changes'] = approved
    result['approved_delta'] = delta
    result['remaining_changes'] = {path: record for path, record in result['changes'].items() if path not in delta}
    result['checks'] = checks
    result['migration'] = {'source_version': policy['source_version'], 'target_version': policy['target_version'],
                           'manifest_sha256': policy['manifest_sha256'],
                           'baseline_sha256': policy['baseline_sha256'],
                           'agents_ownership': 'managed: exact target manifest bytes',
                           'state_migrations': policy['state_migrations'],
                           'exercised_state_paths': policy.get('exercised_state_paths', []),
                           'historical_continuity': {'paths_checked': len(expected_paths),
                               'paths': sorted(expected_paths),
                               'sqlite_paths': sorted(path for path, record in (historical or {}).items() if 'tables' in record),
                               'existing_field_exceptions': [],
                               'new_sql_columns_and_tables_outside_historical_projection': True,
                               'baseline_state_sha256': hashlib.sha256(_json_identity(historical).encode()).hexdigest()},
                           'archive_actions': policy['archive_actions'],
                           'recovery_objects': 'must be absent after successful commit'}
    return result


def write_report(output, report, *, project, source=None):
    output = Path(output).resolve()
    excluded_roots = {ROOT.resolve(), Path(project).resolve()}
    if source is not None:
        excluded_roots.add(Path(source).resolve())
    for excluded in excluded_roots:
        if output == excluded or excluded in output.parents:
            raise ValueError('Report output must be outside source and disposable project')
    # Exclusive creation retains prior evidence; never overwrite a report.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')


def plan(source, project, expected_manifest_sha256, *, overrides=None):
    before = inventory(project)
    result = install(Path(source), Path(project), expected_manifest_sha256,
                     mode='upgrade', configure=True, discover=False, dry_run=True, overrides=overrides)
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
    report = {'status': 'NOT_QUALIFIED', 'execution_authority': False,
              'evidence_kind': 'SYNTHETIC_OWNER_CUSTOMIZATION', 'stages': []}
    try:
        report['source_identity'] = source_identity(source)
    except ValueError as error:
        report.update(failed_stage='source_identity', diagnostic=str(error))
        return report
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

    # Preserve the committed manifest pin. A generated projection cannot qualify
    # different bytes under the source checkout's name.
    target_pin = hashlib.sha256((candidate / 'MANIFEST.json').read_bytes()).hexdigest()
    if target_pin != report['source_identity']['manifest_sha256']:
        report['failed_stage'] = 'candidate_manifest'
        return report
    with Tree(candidate) as tree:
        verify_release(tree, target_pin)
    report['source_manifest_sha256'] = target_pin
    historical = materialize_published(source, work_dir / 'published')
    project = work_dir / 'project'
    migration_policy = None
    budget_baseline = None
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
        if mode == 'upgrade':
            command.extend(['--on-conflict', 'backup'])
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
            try:
                budget_baseline = _prepare_qualification_budget(historical, project, work_dir)
            except (ValueError, AssertionError, OSError):
                report['failed_stage'] = 'budget_baseline'
                return report
            # Both genuinely reserved/settled published API history and a legacy
            # schema ledger must enter the upgrade, not only a later import.
            _seed_qualification_state(historical, project, budget_baseline)
            report['before'] = inventory(project)
            try:
                migration_policy = prepare_upgrade(candidate, project, target_pin, report['before'])
            except ValueError:
                report['failed_stage'] = 'migration_policy'
                report['diagnostic'] = 'Preservation migration policy refused; upgrade not executed'
                return report
        else:
            report['after'] = inventory(project)
            report['preservation'] = compare_upgrade(report['before'], report['after'],
                migration_policy, project, allowed_delta=allowed_delta)
            if not exercised_ledger_migration(report['preservation']):
                report['failed_stage'] = 'ledger_migration_coverage'
                return report
            if (report['preservation']['preserved'] is not True or
                    any(report['preservation'].get('checks', {}).get(name) != 'PASS'
                        for name in REQUIRED_CHECKS)):
                report['failed_stage'] = 'preservation'
                return report
    report['budget_import'] = _qualify_budget_import(budget_baseline, project, work_dir, execute)
    if report['budget_import'].get('preserved') is not True:
        report['failed_stage'] = 'budget_import'
        return report
    try:
        if source_identity(source) != report['source_identity']:
            raise ValueError('Source identity changed during qualification')
    except ValueError as error:
        report.update(failed_stage='source_identity', diagnostic=str(error))
        return report
    report['status'] = 'QUALIFIED_DISPOSABLE_UPGRADE'
    report['active'] = False
    return report


def exercised_ledger_migration(preservation):
    migration = preservation.get('migration', {})
    continuity = migration.get('historical_continuity', {})
    return (QUALIFICATION_LEDGER in continuity.get('sqlite_paths', []) and
            any(action.get('path') == QUALIFICATION_LEDGER and action.get('action') == 'migrated_schema'
                for action in migration.get('state_migrations', [])))


def _seed_qualification_state(historical, project, budget_baseline):
    from upgrade_195_fixtures import seed_legacy_accounting
    seed_legacy_accounting(historical, project)
    host = project / '.agentic-state/routing/published-host.sqlite'
    host.write_bytes(budget_baseline['sources']['host']['path'].read_bytes())


def _prepare_qualification_budget(historical, project, work_dir):
    # Historical fixture generation executes ONLY the verified published runtime.
    # It supplies genuine reserve/settle rows, not hand-authored ledger inserts.
    from upgrade_195_fixtures import generate_budget_history
    project_id = load_yaml((project / CONFIG).read_bytes())['project']['id']
    destination = work_dir / 'budget-sources'
    generate_budget_history(historical, destination, project_id)
    sources = {label: {'path': destination / filename,
                       'sha256': hashlib.sha256((destination / filename).read_bytes()).hexdigest()}
               for label, filename in (('state', 'state.sqlite'), ('host', 'host.sqlite'), ('overrun', 'overrun.sqlite'))}
    return {'sources': sources, 'before': prepare_budget_import(sources, project_id=project_id)}


def _qualify_budget_import(baseline, project, work_dir, execute):
    sources = baseline['sources']
    canonical = work_dir / 'budget-canonical/routing.sqlite'
    runtime = project / '.agentic/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentic.upgrade import import_budget_history
sources = json.loads(sys.argv[3])
print(json.dumps(import_budget_history(sys.argv[2], sources,
    project_id=sys.argv[4], worktree_roots=[sys.argv[5]]), sort_keys=True))
'''
    arguments = [str(runtime), '-B', '-I', '-c', code, str(project / '.agentic/lib'), str(canonical),
        json.dumps({label: {**spec, 'path': str(spec['path'])} for label, spec in sources.items()}),
        baseline['before']['project_id'], str(project)]
    for stage in ('budget_import', 'budget_import_replay'):
        child = execute(stage, arguments)
        if child.returncode != 0:
            return {'preserved': False, 'violations': ['Installed budget import command failed'],
                    'failed_stage': stage}
        try:
            receipt = json.loads(child.stdout)
            if receipt['status'] != 'IMPORTED' or receipt['budget_mode_changed'] is not False:
                raise ValueError('Invalid import receipt')
            if stage.endswith('replay') and receipt['imported_rows'] != 0:
                raise ValueError('Import replay charged again')
        except (ValueError, KeyError, TypeError):
            return {'preserved': False, 'violations': ['Installed budget import receipt refused']}
        result = check_budget_import(baseline['before'], sources, canonical)
        if not result['preserved']:
            return result
    result['replay_imported_rows'] = receipt['imported_rows']
    result['baseline_sha256'] = hashlib.sha256(_json_identity(baseline['before']).encode()).hexdigest()
    return result


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
    planner.add_argument('--github-repo')
    planner.add_argument('--repository-id', type=int)
    planner.add_argument('--default-branch')
    diff = sub.add_parser('compare')
    diff.add_argument('--project', type=Path, required=True, help='disposable project, for output isolation')
    diff.add_argument('--before', type=Path, required=True)
    diff.add_argument('--after', type=Path, required=True)
    diff.add_argument('--allowed-delta', type=Path, help='optional constraints; cannot waive preservation')
    diff.add_argument('--source', type=Path, help='verified target release')
    diff.add_argument('--expected-manifest-sha256')
    diff.add_argument('--baseline-project', type=Path, help='retained untouched disposable baseline matching BEFORE')
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
        write_report(args.output, report, project=args.work_dir, source=args.source)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'QUALIFIED_DISPOSABLE_UPGRADE' else 1
    if args.command == 'snapshot':
        report = inventory(args.project)
    elif args.command == 'plan':
        overrides = {key: value for key, value in (
            ('repository', args.github_repo), ('repository_id', args.repository_id),
            ('base_branch', args.default_branch)) if value is not None}
        report = plan(args.source, args.project, args.expected_manifest_sha256, overrides=overrides)
    else:
        before, after = (json.loads(path.read_bytes()) for path in (args.before, args.after))
        delta = json.loads(args.allowed_delta.read_bytes()) if args.allowed_delta else None
        if not all((args.source, args.expected_manifest_sha256, args.baseline_project)):
            report = compare(before, after, {})
            report['violations'].append('Preservation checks NOT_RUN: supply --source, --expected-manifest-sha256 and '
                '--baseline-project (untouched disposable baseline); allowed deltas cannot waive owner or history checks')
            report['preserved'] = False
            report['checks'] = {name: 'NOT_RUN' for name in REQUIRED_CHECKS}
        else:
            policy = prepare_upgrade(args.source, args.baseline_project, args.expected_manifest_sha256, before)
            # This post-hoc comparison proves preservation only. Its unchanged
            # baseline predates the operation, but it cannot attest cohort time.
            policy['started_at'] = 0
            report = compare_upgrade(before, after, policy, args.project, allowed_delta=delta)
    write_report(args.output, report, project=args.project, source=getattr(args, 'source', None))
    return 1 if report.get('preserved') is False else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print('Preservation refused: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
