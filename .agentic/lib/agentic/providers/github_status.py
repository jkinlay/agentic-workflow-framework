"""GitHub adapter for read-only installed/configured/accepted-checkout state."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from urllib.parse import quote

from .. import ValidationError, VERSION
from ..canonical import canonical, load_yaml, loads, now_text, sha256, timestamp
from ..child_process import child_env
from ..configuration import inspect_config
from ..contracts import Contracts
from ..installer import CONFIG, CODEOWNERS, INSTALLED, PROVENANCE, managed, verify_installed
from .github import (GITHUB_REST_API_VERSION, _gh_get, _gh_graphql, branch_name,
                     repository_name)
from ..release_trust import approved_manifest
from ..safeio import Tree, relative_parts

MAX_BYTES = 1024 * 1024
MAX_TOTAL = 8 * MAX_BYTES
MAX_SECONDS = 60
SHA = re.compile(r'[0-9a-f]{40}')
MERGE_IDENTITY_QUERY = '''query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    id
    nameWithOwner
    defaultBranchRef{name target{... on Commit{oid}}}
    pullRequest(number:$number){
      id
      number
      baseRefName
      state
      merged
      mergedAt
      mergeCommit{oid repository{id nameWithOwner}}
    }
  }
}'''


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def blob_sha(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw, usedforsecurity=False).hexdigest()


def object_value(value):
    require(isinstance(value, dict), 'Malformed acceptance observation: expected an object')
    return value


def _gh_graphql(repository, number, deadline, *, gh='gh'):
    """Run the fixed, bounded read-only merge-identity query."""
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'Acceptance observation deadline exhausted')
    owner, name = repository_name(repository).split('/', 1)
    require(type(number) is int and number > 0, 'Use a positive adoption PR number')
    request = canonical({'query': MERGE_IDENTITY_QUERY,
                         'variables': {'owner': owner, 'name': name, 'number': number}})
    require(len(request) <= MAX_BYTES, 'GraphQL acceptance request exceeds byte limit')
    command = [str(gh), 'api', '--hostname', 'github.com', '--method', 'POST', 'graphql', '--input', '-']
    env = dict(os.environ, GH_PROMPT_DISABLED='1', GH_PAGER='cat')
    with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        incoming.write(request)
        incoming.seek(0)
        process = subprocess.Popen(command, stdin=incoming, stdout=out, stderr=err,
                                   env=child_env(env))
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, 'Acceptance observation deadline exhausted')
                require(os.fstat(out.fileno()).st_size <= MAX_BYTES and os.fstat(err.fileno()).st_size <= MAX_BYTES,
                        'GraphQL acceptance response exceeds byte limit')
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            require(time.monotonic() <= deadline and process.returncode == 0,
                    'GraphQL acceptance query did not complete successfully')
            require(os.fstat(err.fileno()).st_size <= MAX_BYTES,
                    'GraphQL acceptance stderr exceeds byte limit')
            out.seek(0)
            raw = out.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, 'GraphQL acceptance response exceeds byte limit')
            return loads(raw.decode('utf-8')), len(raw)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def host_executable(name, root):
    resolved = shutil.which(name)
    require(resolved is not None, 'Trusted host ' + name + ' executable is unavailable')
    path = Path(resolved).resolve()
    require(not path.is_relative_to(root.resolve()) and path.suffix.lower() not in ('.cmd', '.bat', '.ps1'),
            'Host executable cannot be a project checkout file or shell script')
    return str(path)


class Observation:
    def __init__(self, root, gh=None):
        self.root = root
        self.git_exe = host_executable('git', root)
        self.gh = host_executable(gh or 'gh', root)
        self.deadline = time.monotonic() + MAX_SECONDS
        self.total = 0
        self.requests = 0

    def account(self, raw):
        self.total += len(raw)
        require(len(raw) <= MAX_BYTES and self.total <= MAX_TOTAL, 'Acceptance observation exceeds its byte limit')
        return raw

    def get(self, endpoint, *, pr_file_metadata=False):
        self.requests += 1
        require(self.requests <= 20 and time.monotonic() < self.deadline, 'Acceptance observation exceeds its request/time limit')
        value, count = _gh_get(endpoint, self.deadline, gh=self.gh, pr_file_metadata=pr_file_metadata)
        self.total += count
        require(self.total <= MAX_TOTAL, 'Acceptance observation exceeds aggregate byte limit')
        return value

    def graphql(self, repository, number):
        self.requests += 1
        require(self.requests <= 20 and time.monotonic() < self.deadline,
                'Acceptance observation exceeds its request/time limit')
        value, count = _gh_graphql(repository, number, self.deadline, gh=self.gh)
        self.total += count
        require(self.total <= MAX_TOTAL, 'Acceptance observation exceeds aggregate byte limit')
        return value

    def git(self, *arguments):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith('GIT_') and key.upper() not in ('PYTHONPATH', 'PYTHONHOME')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
                   GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1')
        command = [self.git_exe, '--no-replace-objects', '-c', 'core.fsmonitor=false',
                   '-c', 'core.hooksPath=' + os.devnull, '-C', str(self.root), *arguments]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       env=child_env(env))
            try:
                while process.poll() is None:
                    require(time.monotonic() < self.deadline, 'Git acceptance observation timed out')
                    require(os.fstat(out.fileno()).st_size <= MAX_BYTES and os.fstat(err.fileno()).st_size <= MAX_BYTES, 'Git acceptance output exceeds limit')
                    try:
                        process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        pass
                require(time.monotonic() <= self.deadline and process.returncode == 0, 'Read-only Git check failed or timed out: ' + arguments[0])
                require(os.fstat(out.fileno()).st_size <= MAX_BYTES and os.fstat(err.fileno()).st_size <= MAX_BYTES, 'Git acceptance output exceeds limit')
                out.seek(0)
                return self.account(out.read(MAX_BYTES + 1))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)


def installed_bytes(root):
    verify_installed(root)
    with Tree(root) as tree:
        require(tree.inspect(INSTALLED) is not None, 'Release source is not an adopted project installation')
        receipt_raw = tree.read(INSTALLED, maximum=MAX_BYTES)
        receipt = loads(receipt_raw.decode('utf-8'))
        value = {INSTALLED: receipt_raw, CONFIG: tree.read(CONFIG, maximum=MAX_BYTES),
                 PROVENANCE: tree.read(PROVENANCE, maximum=MAX_BYTES)}
        for path in receipt['immutable_files']:
            value[path] = tree.read(path, maximum=MAX_BYTES)
        # CODEOWNERS is project-owned, but if present it must also be accepted.
        for path in (CODEOWNERS, 'CODEOWNERS', 'docs/CODEOWNERS'):
            if tree.inspect(path) is not None:
                value[path] = tree.read(path, maximum=MAX_BYTES)
                break
    return receipt, value


def complete_receipt(receipt):
    source = receipt.get('source_manifest_json')
    require(isinstance(source, str) and sha256(source.encode('utf-8')) == receipt.get('source_manifest_sha256'),
            'Receipt needs its verified complete source manifest; reinstall through the current adoption path')
    manifest = loads(source)
    require(set(manifest) == {'format', 'template_version', 'files'} and manifest['format'] == 'awf-manifest-1'
            and manifest['template_version'] == VERSION and isinstance(manifest['files'], dict), 'Receipt source manifest is invalid')
    expected = {path: digest for path, digest in manifest['files'].items()
                if managed(path) and path not in (CONFIG, PROVENANCE, CODEOWNERS)}
    require(expected == receipt['immutable_files'] and expected, 'Receipt does not cover the complete managed release')
    for path in manifest['files']:
        relative_parts(path)


def tree_entries(value, prefix=''):
    require(isinstance(value, dict) and value.get('truncated') is False and isinstance(value.get('tree'), list),
            'GitHub tree observation is incomplete')
    result = {}
    for entry in value['tree']:
        require(isinstance(entry, dict), 'Malformed GitHub tree entry')
        path = entry.get('path')
        relative_parts(path)
        full = prefix + path
        require(full not in result and isinstance(entry.get('sha'), str) and SHA.fullmatch(entry['sha']),
                'Ambiguous GitHub tree entry')
        result[full] = entry
    return result


def _nul_records(raw):
    require(not raw or raw.endswith(b'\0'), 'Malformed NUL-delimited Git observation')
    return raw.rstrip(b'\0').split(b'\0') if raw else []


def _local_git_entries(observation, paths):
    """Return batched HEAD/index identities and safe checkout-conversion detail."""
    arguments = ('--', *paths)
    heads = {}
    for record in _nul_records(observation.git('ls-tree', '-z', 'HEAD', *arguments)):
        header, seen = record.split(b'\t', 1)
        mode, kind, oid = header.decode('ascii').split(' ')
        path = seen.decode('utf-8')
        require(path not in heads and kind == 'blob' and mode in ('100644', '100755') and SHA.fullmatch(oid),
                'MISSING_PATH: local HEAD has no supported blob at ' + path)
        heads[path] = {'mode': mode, 'oid': oid}

    indexes = {}
    for record in _nul_records(observation.git('ls-files', '--stage', '-z', *arguments)):
        header, seen = record.split(b'\t', 1)
        mode, oid, stage = header.decode('ascii').split(' ')
        path = seen.decode('utf-8')
        require(path not in indexes and stage == '0', 'MISMATCH_INDEX: unsupported index entry at ' + path)
        indexes[path] = (mode, oid)

    dirty = set()
    for record in _nul_records(observation.git('status', '--porcelain=v1', '-z', '--untracked-files=no', *arguments)):
        require(len(record) > 3 and record[2:3] == b' ', 'Malformed Git status observation')
        dirty.add(record[3:].decode('utf-8'))

    eols = {}
    for record in _nul_records(observation.git('ls-files', '--eol', '-z', *arguments)):
        detail, seen = record.split(b'\t', 1)
        eols[seen.decode('utf-8')] = detail.decode('utf-8', errors='strict')

    attrs = {}
    attr_records = _nul_records(observation.git('check-attr', '-z', 'filter', *arguments))
    require(len(attr_records) == len(paths) * 3, 'Malformed Git attribute observation')
    for offset in range(0, len(attr_records), 3):
        path, attribute, value = (item.decode('utf-8', errors='strict') for item in attr_records[offset:offset + 3])
        require(attribute == 'filter' and path not in attrs, 'Malformed Git filter attribute observation')
        attrs[path] = value

    result = {}
    for path in paths:
        require(path in heads, 'MISSING_PATH: local HEAD lacks ' + path)
        require(path in indexes, 'MISSING_PATH: index lacks ' + path)
        mode, oid = heads[path]['mode'], heads[path]['oid']
        require(indexes[path] == (mode, oid), 'MISMATCH_INDEX: index differs from HEAD at ' + path)
        require(path in eols and path in attrs, 'MISSING_PATH: Git metadata lacks ' + path)
        eol = eols[path]
        canonical_oid = None
        external_filter = attrs[path] not in ('unspecified', 'unset')
        is_dirty = path in dirty
        if is_dirty and not external_filter:
            # Git on Windows can report a stat-level CRLF change even though its
            # text conversion recreates the index blob. Use Git's own path rules,
            # but never execute a project-owned clean filter during status.
            canonical_oid = observation.git('hash-object', '--path=' + path, path).decode('ascii').strip()
            require(SHA.fullmatch(canonical_oid), 'MISSING_PATH: working file cannot be canonically hashed at ' + path)
            is_dirty = canonical_oid != oid
        if is_dirty and eol.startswith('i/lf'):
            # A changed attribute file can make status want to run a project-owned
            # clean filter. Do not run it. For an LF index text blob, byte-normalize
            # CRLF solely to distinguish checkout conversion from a content edit.
            with Tree(observation.root) as working:
                working_raw = working.read(path, maximum=MAX_BYTES)
            if blob_sha(working_raw.replace(b'\r\n', b'\n')) == oid:
                is_dirty = False
                canonical_oid = oid
        require(not is_dirty, 'DIRTY_PATH: working tree differs from the index at ' + path)
        conversion = (' w/crlf ' in (' ' + eol + ' ')) or canonical_oid == oid
        result[path] = {'mode': mode, 'oid': oid, 'attribute_conversion': conversion, 'eol': eol}
    return result


def accepted_blobs(observation, repository, head, paths):
    """Compare provider, HEAD and index Git objects; never compare checkout bytes."""
    commit = object_value(observation.get(f'repos/{repository}/git/commits/{head}'))
    require(commit.get('sha') == head and SHA.fullmatch(object_value(commit.get('tree')).get('sha', '')), 'Accepted commit identity mismatch')
    tree = tree_entries(observation.get(f'repos/{repository}/git/trees/{commit["tree"]["sha"]}'))
    groups = {path.split('/', 1)[0] for path in paths if '/' in path}
    for group in sorted(groups):
        entry = tree.get(group)
        require(entry and entry.get('type') == 'tree', 'Accepted checkout lacks directory ' + group)
        tree.update(tree_entries(observation.get(f'repos/{repository}/git/trees/{entry["sha"]}?recursive=1'), group + '/'))
    local_entries = _local_git_entries(observation, tuple(paths))
    conversions = []
    for path in paths:
        entry = tree.get(path)
        require(entry is not None, 'MISSING_PATH: accepted remote tree lacks ' + path)
        require(entry.get('type') == 'blob' and entry.get('mode') in ('100644', '100755'),
                'REMOTE_TREE_MISMATCH: accepted path is not a supported blob: ' + path)
        local = local_entries[path]
        require((entry.get('mode'), entry.get('sha')) == (local['mode'], local['oid']),
                'REMOTE_TREE_MISMATCH: remote accepted tree differs from local HEAD at ' + path)
        if local['attribute_conversion']:
            conversions.append(path)
    return conversions


def receipt_changed(observation, repository, number, raw):
    seen = set()
    receipt = None
    for page in range(1, 6):
        entries = observation.get(f'repos/{repository}/pulls/{number}/files?per_page=100&page={page}',
                                  pr_file_metadata=True)
        require(isinstance(entries, list) and len(entries) <= 100, 'Malformed adoption PR file inventory')
        for entry in entries:
            entry = object_value(entry)
            name = entry.get('filename')
            relative_parts(name)
            require(name not in seen, 'Duplicate adoption PR file inventory entry')
            seen.add(name)
            if name == INSTALLED:
                receipt = entry
        if len(entries) < 100:
            break
    else:
        raise ValidationError('Adoption PR file inventory exceeds the complete observation limit')
    require(receipt and receipt.get('status') in ('added', 'modified') and receipt.get('sha') == blob_sha(raw),
            'Selected adoption PR did not add or modify this exact installation receipt')


def merge_identity(value, repository, repository_node_id, default, head, number, rest_pr):
    """Cross-bind GraphQL's integration commit to the REST observation."""
    require(isinstance(value, dict) and set(value) == {'data'},
            'GraphQL merge-identity observation is incomplete')
    graph_repository = object_value(object_value(value['data']).get('repository'))
    graph_id = graph_repository.get('id')
    require(isinstance(repository_node_id, str) and repository_node_id
            and isinstance(graph_id, str) and graph_id == repository_node_id
            and isinstance(graph_repository.get('nameWithOwner'), str)
            and graph_repository['nameWithOwner'].casefold() == repository.casefold(),
            'GraphQL repository identity differs from the REST observation')
    graph_default = object_value(graph_repository.get('defaultBranchRef'))
    graph_target = object_value(graph_default.get('target'))
    require(graph_default.get('name') == default and graph_target.get('oid') == head,
            'GraphQL default branch differs from the REST observation')
    graph_pr = object_value(graph_repository.get('pullRequest'))
    require(isinstance(rest_pr.get('node_id'), str) and rest_pr['node_id']
            and graph_pr.get('id') == rest_pr['node_id']
            and type(graph_pr.get('number')) is int and graph_pr['number'] == number
            and graph_pr.get('baseRefName') == default
            and graph_pr.get('state') == 'MERGED' and graph_pr.get('merged') is True
            and isinstance(graph_pr.get('mergedAt'), str) and graph_pr['mergedAt'],
            'GraphQL adoption PR identity or merged state differs from REST')
    timestamp(graph_pr['mergedAt'])
    graph_merge = object_value(graph_pr.get('mergeCommit'))
    merge = graph_merge.get('oid')
    merge_repository = object_value(graph_merge.get('repository'))
    require(isinstance(merge, str) and SHA.fullmatch(merge)
            and merge_repository.get('id') == graph_id
            and isinstance(merge_repository.get('nameWithOwner'), str)
            and merge_repository['nameWithOwner'].casefold() == repository.casefold(),
            'GraphQL adoption merge identity is missing or malformed')
    legacy = rest_pr.get('merge_commit_sha')
    if legacy is not None:
        require(isinstance(legacy, str) and SHA.fullmatch(legacy) and legacy == merge,
                'REST and GraphQL adoption merge identities differ')
    return merge


def project_status(root, *, adoption_pr=None, gh=None, release_source=None, expected_manifest_sha256=None,
                   capability_observations=None):
    root = Path(root).absolute()
    result = {'version': VERSION, 'project_state': None, 'integrity_valid': False,
              'configuration': {'status': 'NOT_CHECKED', 'unresolved': []}, 'ci_gate': 'NOT_CONFIGURED',
              'adoption': 'UNOBSERVED', 'accepted_checkout': 'UNOBSERVED', 'execution_authority': False,
              'line': f'AWF {VERSION}: UNVERIFIED', 'next_action': 'Install or recover the verified AWF files.',
              'decision_codes': ['report_installation_unverified'],
              'checks': [], 'github_rest_api_version': GITHUB_REST_API_VERSION,
              'next_command': 'python -B .agentic/scripts/workflow.py status --require-active',
              'observation_limit': 'Read-only trusted-host Git/GitHub observations; not a server-signed attestation or live automation qualification.'}
    from ..activation import activation_summary, build_capability_matrix, check
    receipt = files = config = report = None

    def add(code, stage, state, evidence, remedy):
        result['checks'].append(check(code, stage, state, evidence, remedy))

    # Installation, configuration, operating state, host preflight and release
    # trust are independent stages. Keep every safely observable result.
    try:
        receipt, files = installed_bytes(root)
        result.update(project_state='INSTALLED', integrity_valid=True, line=f'AWF {VERSION}: INSTALLED')
        add('INSTALLATION_INTEGRITY', 'installation', 'PASS',
            'Installed manifest and immutable AWF release bytes are verified',
            'Run workflow.py verify-installation after any installation change')
    except (ValidationError, OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        state = 'UNAVAILABLE' if isinstance(exc, OSError) else 'INVALID'
        add('INSTALLATION_INTEGRITY', 'installation', state,
            'Installation could not be verified: ' + (type(exc).__name__ if isinstance(exc, OSError) else str(exc)),
            'Restore the installed AWF files, then run workflow.py verify-installation')
        result['next_action'] = ((str(exc) if isinstance(exc, ValidationError) else
                                  'Restore access to the installed AWF files') +
                                 '; run workflow.py verify-installation, then workflow.py status again.')
        result['observation_error'] = str(exc.__cause__ or exc)

    if files is not None:
        try:
            with Tree(root) as tree:
                workflow = load_yaml(tree.read('.agentic/workflow.yaml'))
            config = load_yaml(files[CONFIG])
            instructions = root / 'PROJECT_INSTRUCTIONS.md'
            base_report = inspect_config(config, workflow, Contracts(root / '.agentic/schemas'),
                                         project_instructions=instructions.read_text(encoding='utf-8') if instructions.is_file() else None)
            if base_report['status'] == 'ACCEPTED':
                add('PROJECT_CONFIGURATION', 'configuration', 'PASS', 'Project configuration is accepted',
                    'Run workflow.py validate-config after configuration changes')
                result.update(project_state='CONFIGURED', line=f'AWF {VERSION}: CONFIGURED',
                              decision_codes=['report_state_configured'],
                              next_action='Merge the adoption PR, then verify its accepted default checkout.')
            else:
                detail = '; '.join(item['path'] for item in base_report['unresolved'])
                add('PROJECT_CONFIGURATION', 'configuration', 'INVALID', 'Unresolved configuration paths: ' + detail,
                    'Run workflow.py validate-config and correct every reported path')
                result['next_action'] = 'Configure ' + '; '.join(
                    item['path'] + ' using ' + item['flag'] for item in base_report['unresolved'])
                result['decision_codes'] = ['report_state_installed_unconfigured']
            from ..operating_status import with_operating
            report = with_operating(root, config, base_report)
            result.update(configuration=report, ci_gate=report['ci_gate'], operating=report['operating'])
            operating = report['operating']
            if operating['status'] == 'ACCEPTED':
                add('OPERATING_SNAPSHOT', 'operating', 'PASS',
                    'Read-only operating snapshot is accepted at ' + operating['hash'],
                    'Run workflow.py operating show after operating changes')
                stream_suffix = f" — streams {operating['streams']}/{operating['effective_ceiling']}"
                result['line'] += stream_suffix
            elif operating['status'] == 'ACCESS_UNAVAILABLE':
                add('OPERATING_ACCESS', 'operating', 'UNAVAILABLE',
                    'Operating snapshot access is unavailable: ' + str(operating.get('diagnostic')),
                    'Restore read access and run workflow.py operating show')
            else:
                reasons = '; '.join(item.get('reason', 'invalid operating state') for item in operating.get('refusals', []))
                code = 'OPERATING_CONCURRENT_CHANGE' if 'CONCURRENT_CHANGE' in reasons else (
                    'OPERATING_PENDING_JOURNAL' if 'Pending operating transaction' in reasons else 'OPERATING_INVALID')
                add(code, 'operating', 'UNOBSERVED' if code == 'OPERATING_CONCURRENT_CHANGE' else 'INVALID',
                    reasons or 'Operating configuration is invalid',
                    'Run workflow.py operating show and use operating recover only with the independently inspected journal pin')
        except OSError as exc:
            from ..activation import access_diagnostic
            add('CONFIGURATION_ACCESS', 'configuration', 'UNAVAILABLE',
                'Configuration access is unavailable: ' + str(access_diagnostic(exc, '.agentic configuration paths')),
                'Restore read access and run workflow.py validate-config')
            result['observation_error'] = str(exc.__cause__ or exc)
        except (ValidationError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            add('PROJECT_CONFIGURATION', 'configuration', 'INVALID', str(exc),
                'Run workflow.py validate-config and correct every reported path')
            result['observation_error'] = str(exc.__cause__ or exc)
    else:
        add('PROJECT_CONFIGURATION', 'configuration', 'UNOBSERVED', 'Installation verification did not supply configuration bytes',
            'Restore installation integrity, then run workflow.py validate-config')
        add('OPERATING_SNAPSHOT', 'operating', 'UNOBSERVED', 'Configuration was unavailable, so operating policy could not be bound',
            'Restore installation integrity, then run workflow.py operating show')

    try:
        from ..host_preflight import preflight
        result['host_preflight'] = preflight(root)
        add('HOST_PREFLIGHT', 'host', 'PASS', 'Host preflight completed; warning rows remain advisory to activation',
            'Run workflow.py preflight to refresh host diagnostics')
    except OSError as exc:
        add('HOST_PREFLIGHT_ACCESS', 'host', 'UNAVAILABLE', type(exc).__name__,
            'Restore host-tool read/execute access and run workflow.py preflight')
    except (ValidationError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        add('HOST_PREFLIGHT_INVALID', 'host', 'INVALID', str(exc), 'Run workflow.py preflight and correct the named host input')

    trust_ready = False
    if receipt is not None:
        try:
            complete_receipt(receipt)
            digest, trust_basis = approved_manifest(root, release_source=release_source,
                                                    expected_manifest_sha256=expected_manifest_sha256)
            require(digest == receipt['source_manifest_sha256'], 'Installation differs from the independently approved release')
            result['release_trust_basis'] = trust_basis
            trust_ready = True
            add('RELEASE_TRUST', 'release_trust', 'PASS', 'Independent release manifest matches the installation receipt',
                'Refresh the trusted host receipt when changing AWF releases')
        except OSError as exc:
            add('RELEASE_TRUST', 'release_trust', 'UNAVAILABLE', type(exc).__name__,
                'Supply --release-source with --expected-manifest-sha256 or restore the trusted host receipt')
            if any(item['code'] == 'PROJECT_CONFIGURATION' and item['state'] == 'PASS'
                   for item in result['checks']):
                result['next_action'] = ('Supply --release-source with --expected-manifest-sha256 or restore the trusted host receipt; '
                                         'run workflow.py status again.')
            result['observation_error'] = str(exc.__cause__ or exc)
        except (ValidationError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            add('RELEASE_TRUST', 'release_trust', 'UNOBSERVED', str(exc),
                'Supply --release-source with --expected-manifest-sha256 or restore the trusted host receipt')
            if any(item['code'] == 'PROJECT_CONFIGURATION' and item['state'] == 'PASS'
                   for item in result['checks']):
                result['next_action'] = (str(exc) + '; supply --release-source with --expected-manifest-sha256 or restore the '
                                         'trusted host receipt, then run workflow.py status again.')
            result['observation_error'] = str(exc.__cause__ or exc)
    else:
        add('RELEASE_TRUST', 'release_trust', 'UNOBSERVED', 'Installation receipt is unavailable',
            'Restore installation integrity before establishing release trust')

    provider_ready = (config is not None and report is not None and
                      any(item['code'] == 'PROJECT_CONFIGURATION' and item['state'] == 'PASS' for item in result['checks']) and
                      any(item['code'] == 'OPERATING_SNAPSHOT' and item['state'] == 'PASS' for item in result['checks']) and trust_ready)
    if provider_ready:
        try:
            provenance = loads(files[PROVENANCE].decode('utf-8'))
            require(isinstance(receipt.get('install_id'), str) and uuid.UUID(receipt['install_id']).int != 0,
                    'Reconcile the missing or invalid receipt installation UUID before acceptance')
            require(receipt.get('project_id') == config['project']['id']
                    and receipt.get('install_id') == provenance.get('installation', {}).get('install_id'),
                    'Reconcile project/installation identity between configuration, receipt and provenance')
            observation = Observation(root, gh)
            require(Path(observation.git('rev-parse', '--show-toplevel').decode().strip()).resolve() == root.resolve(),
                    'Use the repository root for accepted-checkout verification')
            graft = observation.git('rev-parse', '--git-path', 'info/grafts').decode().strip()
            require(not (root / graft).exists(), 'Remove or reconcile local history grafts before acceptance verification')
            repository = repository_name(config['github']['repository'])
            from .github import origin_repository
            require(origin_repository(observation.git('remote', 'get-url', 'origin').decode().strip()).casefold() == repository.casefold(),
                    'Git origin differs from the configured GitHub repository')
            require(config['github']['host'] == 'https://github.com', 'This acceptance observer supports GitHub.com only')
            meta = object_value(observation.get(f'repos/{repository}'))
            default = branch_name(meta.get('default_branch'))
            require(meta.get('id') == config['github']['repository_id'] and isinstance(meta.get('full_name'), str)
                    and meta['full_name'].casefold() == repository.casefold(),
                    'GitHub repository identity differs from configuration')
            result.update(repository=repository, repository_id=meta['id'], default_branch=default)
            require(config['github']['base_branch'] == default, 'Update $.github.base_branch to the observed default branch ' + default)
            branch = object_value(observation.get(f'repos/{repository}/branches/{quote(default, safe="")}'))
            head = object_value(branch.get('commit')).get('sha', '')
            require(branch.get('name') == default and SHA.fullmatch(head), 'Invalid observed default tip')
            local_branch = observation.git('symbolic-ref', '--quiet', '--short', 'HEAD').decode().strip()
            local_head = observation.git('rev-parse', 'HEAD').decode().strip()
            receipt_commit = observation.git('log', '-1', '--format=%H', '--', INSTALLED).decode().strip()
            require(SHA.fullmatch(receipt_commit), 'Commit the installation receipt through the adoption PR')
            associated = observation.get(f'repos/{repository}/commits/{receipt_commit}/pulls?per_page=100')
            require(isinstance(associated, list) and len(associated) < 100, 'Associated adoption PR inventory is incomplete')
            candidate_items = [item for item in associated if isinstance(item, dict)
                               and object_value(object_value(item.get('base')).get('repo')).get('id') == meta['id']
                               and object_value(item.get('base')).get('ref') == default and type(item.get('number')) is int]
            candidates = [item['number'] for item in candidate_items]
            if adoption_pr is None:
                require(len(candidates) == 1, 'Prepare or identify the receipt-changing adoption PR; use status --adoption-pr NUMBER when ambiguous')
                adoption_pr = candidates[0]
            require(type(adoption_pr) is int and adoption_pr > 0, 'Use a positive adoption PR number')
            require(adoption_pr in candidates, 'Selected PR did not introduce the receipt-changing commit; identify the actual adoption PR')
            pr = object_value(observation.get(f'repos/{repository}/pulls/{adoption_pr}'))
            require(pr.get('number') == adoption_pr and object_value(object_value(pr.get('base')).get('repo')).get('id') == meta['id']
                    and object_value(pr.get('base')).get('ref') == default, 'Adoption PR targets another repository/default branch')
            result['adoption_pr'] = adoption_pr
            if pr.get('merged') is not True:
                action = 'Reopen or replace' if pr.get('state') == 'closed' else 'Merge'
                result.update(adoption='NOT_MERGED', next_action=f'{action} adoption PR #{adoption_pr}, then verify its accepted default checkout.')
                add('ADOPTION_MERGE', 'adoption', 'UNOBSERVED', 'The receipt-changing adoption PR is not merged',
                    f'{action} adoption PR #{adoption_pr}, then run workflow.py status --require-active')
            else:
                require(local_branch == default and local_head == head, 'Check out the observed default branch ' + default + ' at ' + head)
                receipt_changed(observation, repository, adoption_pr, files[INSTALLED])
                merge = merge_identity(observation.graphql(repository, adoption_pr), repository,
                                       meta.get('node_id'), default, head, adoption_pr, pr)
                observation.git('merge-base', '--is-ancestor', receipt_commit, merge)
                observation.git('merge-base', '--is-ancestor', merge, head)
                accepted_receipt = object_value(observation.get(f'repos/{repository}/contents/{INSTALLED}?ref={merge}'))
                require(accepted_receipt.get('type') == 'file' and accepted_receipt.get('path') == INSTALLED
                        and accepted_receipt.get('sha') == blob_sha(files[INSTALLED])
                        and accepted_receipt.get('encoding') == 'base64' and isinstance(accepted_receipt.get('content'), str),
                        'Merged PR does not accept this installation receipt')
                require(base64.b64decode(accepted_receipt.get('content', '').replace('\n', ''), validate=True) == files[INSTALLED],
                        'Merged adoption receipt bytes differ')
                result['adoption'] = 'MERGED_VERIFIED'
                add('ADOPTION_MERGE', 'adoption', 'PASS',
                    'Merge identity accepted through the repository-bound GraphQL observation',
                    'Run workflow.py status after the adoption PR or default branch changes')
                conversions = accepted_blobs(observation, repository, head, files)
                final_meta = object_value(observation.get(f'repos/{repository}'))
                final_branch = object_value(observation.get(f'repos/{repository}/branches/{quote(default, safe="")}'))
                require((final_meta.get('id'), final_meta.get('full_name'), final_meta.get('default_branch')) ==
                        (meta.get('id'), meta.get('full_name'), default)
                        and final_branch.get('name') == default and object_value(final_branch.get('commit')).get('sha') == head,
                        'Repository/default tip changed during observation; re-observe the accepted checkout')
                require(observation.git('rev-parse', 'HEAD').decode().strip() == head
                        and observation.git('symbolic-ref', '--quiet', '--short', 'HEAD').decode().strip() == default,
                        'Local checkout changed during acceptance observation')
                final_receipt, final_files = installed_bytes(root)
                require(final_receipt == receipt and final_files == files, 'Installed files changed during acceptance observation')
                from ..operating import inspect_operating
                require(inspect_operating(root, config) == result['operating'],
                        'Operating configuration changed during observation; run status again for its current snapshot')
                result.update(project_state='ACTIVE', line=f'AWF {VERSION}: ACTIVE' + (
                                  f" — streams {report['operating']['streams']}/{report['operating']['effective_ceiling']}"),
                              accepted_checkout='VERIFIED', accepted_head_sha=head,
                              adoption_acceptance_sha=merge,
                              adoption_acceptance_basis='github_graphql_merge_commit',
                              adoption_merge_sha=merge, adoption_receipt_commit_sha=receipt_commit,
                              merge_identity_source='github_graphql',
                              observed_at=now_text(), next_action=None, decision_codes=['report_active'])
                evidence = 'Remote accepted tree matches local HEAD/index Git objects'
                if conversions:
                    evidence += '; Git attribute conversion observed at ' + ', '.join(conversions)
                add('ACCEPTED_CHECKOUT', 'checkout', 'PASS', evidence,
                    'Run workflow.py status after changing accepted governance paths')
        except (ValidationError, OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, subprocess.SubprocessError) as exc:
            message = str(exc)
            if ('MERGE_IDENTITY_CONFLICT' in message or 'REMOTE_TREE_MISMATCH' in message or
                    'DIRTY_PATH' in message or 'MISMATCH_INDEX' in message or 'MISSING_PATH' in message):
                state, code = 'MISMATCH', ('ACCEPTED_CHECKOUT' if any(
                    token in message for token in ('TREE_', 'DIRTY_', 'INDEX', 'MISSING_PATH')) else 'ADOPTION_MERGE')
            elif 'MERGE_IDENTITY_UNOBSERVED' in message:
                state, code = 'UNOBSERVED', 'ADOPTION_MERGE'
            elif isinstance(exc, OSError):
                state, code = 'UNAVAILABLE', 'PROVIDER_ACCESS'
            else:
                state, code = 'UNOBSERVED', 'PROVIDER_ACCEPTANCE'
            add(code, 'provider', state, message if isinstance(exc, ValidationError) else type(exc).__name__,
                (message if isinstance(exc, ValidationError) else 'Restore access to trusted Git and GitHub CLI') +
                '; run workflow.py status --require-active')
            result['next_action'] = ((message if isinstance(exc, ValidationError) else
                                      'Restore access to valid local AWF files and trusted host tools') +
                                     '; run workflow.py status again after resolving this condition.')
            result['observation_error'] = str(exc.__cause__ or exc)
    else:
        add('PROVIDER_ACCEPTANCE', 'provider', 'UNOBSERVED',
            'Provider acceptance depends on accepted configuration, operating snapshot and release trust',
            'Resolve the earlier blockers, then run workflow.py status --require-active')

    observed_at = result.get('observed_at') or now_text()
    result['capabilities'] = build_capability_matrix(result['project_state'], result['checks'], config,
                                                     capability_observations, observed_at=observed_at)
    result['activation'] = activation_summary(result)
    preflight_next = (result.get('host_preflight') or {}).get('next_action')
    if preflight_next:
        result['next_action'] = ((result['next_action'] + ' ') if result.get('next_action') else '') + 'Host preflight WARN: ' + preflight_next
    return result


def render_status(report):
    lines = [report['line']]
    summary = report.get('activation', {})
    blockers = summary.get('blockers', [])
    if blockers:
        lines.append('Activation blockers:')
        lines.extend(f"- [{item['code']}] {item['state']}: {item['evidence']} Remedy: {item['remedy']}" for item in blockers)
    if report.get('next_action'):
        lines.append('Next: ' + report['next_action'])
    if summary.get('next_command'):
        lines.append('Next command: ' + summary['next_command'])
    return '\n'.join(lines)
