from __future__ import annotations
import hashlib
import inspect
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import unicodedata
import uuid
from . import ValidationError
from .child_process import child_env
from .canonical import canonical, loads, now_text, timestamp
from .child_process import child_env
from .controller_state import configure_database, protected_state_path, restrict_state_permissions
STREAM_STATES = {'WORKING', 'PAUSED_INPUT', 'BLOCKED', 'COMPLETE'}
TICKET_STATES = {'ELIGIBLE', 'PAUSED_INPUT', 'BLOCKED', 'COMPLETE'}
LIFECYCLE_ROLES = {'implementation': 'writer', 'independent_review': 'critic'}
DEFAULT_STATUS_CADENCE_SECONDS = 10 * 60
STATE_APPLICATION_ID = 1096238641
DDL = '\nCREATE TABLE IF NOT EXISTS controller_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS stream_status(\n stream_id TEXT PRIMARY KEY,state TEXT NOT NULL,ticket TEXT,actor TEXT NOT NULL,\n reason TEXT NOT NULL,next_action TEXT NOT NULL,resume_trigger TEXT NOT NULL,\n exact_tuple TEXT NOT NULL,activity TEXT NOT NULL,verification_gate TEXT NOT NULL,\n reviewer_completion TEXT NOT NULL,open_findings INTEGER NOT NULL,\n jira_status TEXT NOT NULL,paths TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_outbox(\n delivery_id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,\n observed_at TEXT NOT NULL,payload_json TEXT NOT NULL,acknowledged_at TEXT);\nCREATE TABLE IF NOT EXISTS controller_dispatch(\n dispatch_id TEXT PRIMARY KEY,stream_id TEXT NOT NULL,ticket TEXT NOT NULL,\n exact_tuple TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,\n receipt_json TEXT,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_ticket_authority(\n ticket TEXT NOT NULL,exact_tuple TEXT NOT NULL,dispatch_role TEXT NOT NULL,\n lifecycle_stage TEXT NOT NULL,observed_at TEXT NOT NULL,\n PRIMARY KEY(ticket,exact_tuple));\nCREATE TABLE IF NOT EXISTS controller_overlap_allowance(\n allowance_id TEXT PRIMARY KEY,source_id TEXT NOT NULL UNIQUE,repository_id TEXT NOT NULL,\n cycle_id TEXT NOT NULL,path TEXT NOT NULL,tickets_json TEXT NOT NULL,\n pull_requests_json TEXT NOT NULL,heads_json TEXT NOT NULL,expires_at TEXT NOT NULL,\n status TEXT NOT NULL,record_json TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_overlap_sync_gate(\n gate_id TEXT PRIMARY KEY,allowance_id TEXT NOT NULL,status TEXT NOT NULL,\n record_json TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE INDEX IF NOT EXISTS controller_overlap_gate_allowance\n ON controller_overlap_sync_gate(allowance_id,status);\nCREATE TABLE IF NOT EXISTS controller_jira_operation(\n operation_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,event TEXT NOT NULL,\n intent_sha256 TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,\n operation_json TEXT,readback_json TEXT,updated_at TEXT NOT NULL);\nCREATE INDEX IF NOT EXISTS controller_jira_issue ON controller_jira_operation(issue_id,status);\n'

def _require(condition, message):
	if not condition:
		raise ValidationError(message)

def _reviewer_counts(counts):
	required = {'required', 'completed', 'acceptable', 'failed', 'stale', 'outstanding'}
	_require(isinstance(counts, dict) and set(counts) == required and all((type(number) is int and number >= 0 for number in counts.values())), 'Reviewer completion requires all six nonnegative counts')
	_require(counts['completed'] + counts['outstanding'] == counts['required'] and counts['acceptable'] + counts['failed'] + counts['stale'] == counts['completed'], 'Reviewer completion counts are contradictory')
	return dict(counts)

def validate_ticket_record(value):
	required = {'ticket', 'priority', 'disposition', 'actor', 'reason', 'next_action', 'resume_trigger', 'paths', 'dependencies_satisfied', 'budget_available', 'cap_available', 'review_independent', 'exact_tuple', 'activity', 'verification_gate', 'reviewer_completion', 'open_findings', 'jira_status', 'dispatch_role', 'lifecycle_stage'}
	_require(isinstance(value, dict) and set(value) == required, 'Malformed continuous-controller ticket')
	_require(isinstance(value['ticket'], str) and value['ticket'].strip() and (len(value['ticket']) <= 256) and (not any((ord(char) < 32 for char in value['ticket']))), 'Ticket identity is required')
	_require(type(value['priority']) is int and value['priority'] >= 0, 'Ticket priority must be nonnegative')
	_require(value['disposition'] in TICKET_STATES, 'Unknown ticket scheduling disposition')
	_require(isinstance(value['paths'], list), 'Ticket paths must be a list')
	value = dict(value)
	value['paths'] = [_owned_path(path) for path in value['paths']]
	_require(len(value['paths']) == len(set(value['paths'])), 'Ticket paths must be unique canonical paths')
	for key in ('dependencies_satisfied', 'budget_available', 'cap_available', 'review_independent'):
		_require(type(value[key]) is bool, f'{key} must be observed boolean')
	for key in ('actor', 'reason', 'next_action', 'resume_trigger', 'exact_tuple', 'activity', 'verification_gate', 'jira_status'):
		_require(isinstance(value[key], str) and value[key].strip(), f'{key} is required')
	_require(value['lifecycle_stage'] in LIFECYCLE_ROLES, 'Ticket lifecycle stage is not an authenticated dispatch stage')
	_require(value['dispatch_role'] == LIFECYCLE_ROLES[value['lifecycle_stage']], 'Ticket dispatch role does not match its authenticated lifecycle stage')
	value['reviewer_completion'] = _reviewer_counts(value['reviewer_completion'])
	_require(type(value['open_findings']) is int and value['open_findings'] >= 0, 'Open finding count must be nonnegative')
	return value
_ticket = validate_ticket_record

def _owned_path(path):
	_require(isinstance(path, str) and path and (not any((ord(char) < 32 for char in path))), 'Ticket path must be a nonempty printable repository-relative path')
	value = path.replace('\\', '/').rstrip('/')
	wildcard = ''
	for suffix in ('/**', '/*'):
		if value.endswith(suffix):
			value, wildcard = (value[:-len(suffix)].rstrip('/'), suffix)
			break
	_require(value and (not value.startswith('/')) and (not value.startswith('//')) and (re.match('^[A-Za-z]:', value) is None), 'Ticket path must stay repository-relative; external/absolute paths are not ownership claims')
	parts = value.split('/')
	_require(all((part not in {'', '.', '..'} for part in parts)), 'Ticket path cannot contain empty, dot, or parent traversal segments')
	return '/'.join(parts) + wildcard

def _overlap(first, second):

	def surface(path):
		value = _owned_path(path).casefold()
		for suffix in ('/**', '/*'):
			if value.endswith(suffix):
				value = value[:-len(suffix)].rstrip('/')
		return value
	left, right = ([surface(path) for path in first], [surface(path) for path in second])
	return any((a == b or a.startswith(b + '/') or b.startswith(a + '/') for a in left for b in right))

def _allowance_keys(values):
	result = set()
	for value in values if isinstance(values, (list, tuple)) else ():
		if isinstance(value, dict) and set(value) >= {'path', 'tickets'} and isinstance(value['tickets'], list) and (len(value['tickets']) == 2):
			try:
				result.add((_exact_overlap_path(value['path']), tuple(sorted(value['tickets']))))
			except ValidationError:
				pass
	return result

def _blocking_overlap(first_ticket, first_paths, second_ticket, second_paths, allowances):
	allowed = _allowance_keys(allowances)
	pair = tuple(sorted((first_ticket, second_ticket)))
	collisions = []
	for left in first_paths:
		left_surface = _owned_path(left)
		for right in second_paths:
			right_surface = _owned_path(right)
			left_compare, right_compare = (left_surface.casefold(), right_surface.casefold())
			if left_compare == right_compare or left_compare.startswith(right_compare + '/') or right_compare.startswith(left_compare + '/'):
				collisions.append((left_surface, right_surface))
	if not collisions:
		return False
	return any((left != right or (left, pair) not in allowed for left, right in collisions))
OVERLAP_ALLOWANCE_MAX_SECONDS = 24 * 60 * 60
OVERLAP_OBSERVATION_MAX_AGE_SECONDS = 5 * 60
OVERLAP_SYNC_DIFF_MAX_BYTES = 512 * 1024

def _exact_overlap_path(path):
	_require(isinstance(path, str) and unicodedata.normalize('NFC', path) == path, 'Overlap allowance paths must be NFC UTF-8 text')
	value = _owned_path(path)
	_require(value == path and (not value.endswith(('/*', '/**'))), 'Overlap allowances require one exact canonical Git file path')
	return value

def _exact_tuple_fields(value):
	_require(isinstance(value, str) and value, 'Post-sync inventory exact tuple is missing')
	fields = {}
	for part in value.split('/'):
		_require(':' in part, 'Post-sync inventory exact tuple is malformed')
		name, field_value = part.split(':', 1)
		_require(name and field_value and (name not in fields), 'Post-sync inventory exact tuple is malformed or ambiguous')
		fields[name] = field_value
	_require(re.fullmatch('[0-9a-f]{40}', fields.get('head', '')) is not None, 'Post-sync inventory exact tuple has no structured PR head')
	return fields

def _allowance_request(value, expected_repository_id=None, expected_cycle_id=None):
	required = {'repository_id', 'cycle_id', 'comment_id', 'path', 'expires_at', 'pull_requests'}
	_require(isinstance(value, dict) and set(value) == required, 'Overlap allowance request has missing or unknown fields')
	repository_id = value['repository_id']
	_require(isinstance(repository_id, str) and repository_id.isdecimal() and (int(repository_id) > 0), 'Overlap allowance repository identity is invalid')
	if expected_repository_id is not None:
		_require(repository_id == str(expected_repository_id), 'Overlap allowance names a foreign repository')
	cycle_id = value['cycle_id']
	_require(isinstance(cycle_id, str) and cycle_id.strip() == cycle_id and cycle_id and (len(cycle_id) <= 256) and (not any((ord(char) < 32 for char in cycle_id))), 'Overlap allowance cycle identity is invalid')
	if expected_cycle_id is not None:
		_require(cycle_id == expected_cycle_id, 'Overlap allowance names a different controller cycle')
	_require(type(value['comment_id']) is int and value['comment_id'] > 0, 'Overlap allowance comment identity is invalid')
	path = _exact_overlap_path(value['path'])
	timestamp(value['expires_at'])
	prs = value['pull_requests']
	_require(isinstance(prs, list) and len(prs) == 2, 'Overlap allowance requires exactly two pull requests')
	normalized = []
	for item in prs:
		_require(isinstance(item, dict) and set(item) == {'number', 'ticket', 'head_sha', 'changed_files'}, 'Overlap allowance pull-request binding is malformed')
		_require(type(item['number']) is int and item['number'] > 0 and isinstance(item['ticket'], str) and (item['ticket'].strip() == item['ticket']) and item['ticket'] and (re.fullmatch('[0-9a-f]{40}', item['head_sha']) is not None), 'Overlap allowance pull-request identity is invalid')
		files = item['changed_files']
		_require(isinstance(files, list) and files and (files == sorted(files)) and (len(files) == len(set(files))), 'Overlap allowance changed files must be a sorted unique list')
		files = [_exact_overlap_path(name) for name in files]
		_require(path in files, 'Overlap allowance path is absent from a bound pull request')
		normalized.append({**item, 'changed_files': files})
	_require([item['number'] for item in normalized] == sorted((item['number'] for item in normalized)) and len({item['number'] for item in normalized}) == 2 and (len({item['ticket'] for item in normalized}) == 2), 'Overlap allowance pull-request/ticket pair must be sorted and distinct')
	return {**value, 'path': path, 'pull_requests': normalized}

def overlap_allowance_command(request):
	request = _allowance_request(request)
	scope = {key: deepcopy(request[key]) for key in ('repository_id', 'cycle_id', 'path', 'expires_at', 'pull_requests')}
	return 'AWF-OVERLAP-ALLOWANCE 1\n' + canonical(scope).decode('utf-8') + '\n'

def _provider_fresh(value, now, label):
	_require(isinstance(value, dict) and isinstance(value.get('provider_observed_at'), str) and isinstance(value.get('etag'), str) and value['etag'].strip() and (len(value['etag']) <= 512), label + ' needs a provider timestamp and ETag')
	observed = timestamp(value['provider_observed_at'])
	current = timestamp(now)
	age = (current - observed).total_seconds()
	_require(0 <= age <= OVERLAP_OBSERVATION_MAX_AGE_SECONDS, label + ' is not a fresh provider observation')

def validate_overlap_allowance_observation(config, request, observation, inventory, *, now, expected_repository_id, expected_cycle_id, allow_expired=False):
	from .canonical import fingerprint
	current = timestamp(now)
	request = _allowance_request(request, expected_repository_id, expected_cycle_id)
	_require(isinstance(config, dict) and isinstance(config.get('github'), dict) and isinstance(config.get('merge_gate'), dict), 'Overlap allowance needs reviewed owner configuration')
	required = {'source', 'repository_id', 'provider_observed_at', 'observation_etag', 'comment', 'pull_requests'}
	_require(isinstance(observation, dict) and set(observation) == required and (observation['source'] == 'github.com') and (str(observation['repository_id']) == request['repository_id']), 'Overlap allowance provider observation names a foreign repository')
	_provider_fresh({'provider_observed_at': observation['provider_observed_at'], 'etag': observation['observation_etag']}, now, 'Overlap allowance aggregate')
	comment = observation['comment']
	comment_fields = {'id', 'node_id', 'actor_id', 'actor_login', 'author_association', 'created_at', 'updated_at', 'body', 'etag', 'provider_observed_at'}
	_require(isinstance(comment, dict) and set(comment) == comment_fields and (comment['id'] == request['comment_id']) and isinstance(comment['node_id'], str) and comment['node_id'].strip() and (type(comment['actor_id']) is int) and (comment['actor_id'] > 0) and isinstance(comment['actor_login'], str) and comment['actor_login'].strip() and (comment['author_association'] in {'OWNER', 'MEMBER', 'COLLABORATOR'}), 'Overlap allowance comment provider identity is malformed')
	_provider_fresh(comment, now, 'Overlap allowance comment')
	trusted = set(config['merge_gate'].get('trusted_owner_ids', []))
	_require(comment['actor_id'] in trusted, 'Overlap allowance comment author is not a configured owner')
	created = timestamp(comment['created_at'])
	updated = timestamp(comment['updated_at'])
	expires = timestamp(request['expires_at'])
	_require(created == updated, 'Overlap allowance approval comment was edited or mutated')
	_require(type(allow_expired) is bool, 'Overlap allowance expiry mode must be boolean')
	_require(created <= current and created <= expires <= created + timedelta(seconds=OVERLAP_ALLOWANCE_MAX_SECONDS) and (allow_expired or current < expires), 'Overlap allowance approval is outside its defined time window')
	_require(comment['body'] == overlap_allowance_command(request), 'Overlap allowance comment is not the exact canonical command bytes')
	observed_prs = observation['pull_requests']
	_require(isinstance(observed_prs, list) and len(observed_prs) == 2, 'Overlap allowance did not observe exactly two pull requests')
	ticket_by_name = {item['ticket']: _ticket(item) for item in inventory}
	_require(len(ticket_by_name) == len(inventory), 'Overlap allowance inventory contains duplicate tickets')
	branch_pattern = config['github'].get('branch_pattern', 'codex/{slug}')
	for expected, observed in zip(request['pull_requests'], observed_prs):
		fields = {'number', 'ticket', 'head_sha', 'changed_files', 'state', 'merged', 'head_repository_id', 'base_repository_id', 'head_ref', 'etag', 'provider_observed_at'}
		_require(isinstance(observed, dict) and set(observed) == fields, 'Overlap allowance pull-request observation is malformed')
		_provider_fresh(observed, now, 'Overlap allowance pull request')
		slug = re.sub('[^a-z0-9]+', '-', expected['ticket'].lower()).strip('-')
		expected_ref = branch_pattern.format(ticket=expected['ticket'], slug=slug)
		_require(observed['number'] == expected['number'] and observed['ticket'] == expected['ticket'] and (observed['head_sha'] == expected['head_sha']) and (observed['changed_files'] == expected['changed_files']) and (str(observed['head_repository_id']) == request['repository_id']) and (str(observed['base_repository_id']) == request['repository_id']) and (observed['head_ref'] == expected_ref) and (observed['state'] == 'OPEN') and (observed['merged'] is False), 'Overlap allowance live head, files, repository, or ticket ownership changed')
		ticket = ticket_by_name.get(expected['ticket'])
		_require(ticket is not None and request['path'] in ticket['paths'], 'Overlap allowance ticket-to-path ownership is not current')
	binding = {'repository_id': request['repository_id'], 'cycle_id': request['cycle_id'], 'path': request['path'], 'tickets': [item['ticket'] for item in request['pull_requests']], 'pull_requests': [item['number'] for item in request['pull_requests']], 'heads': [item['head_sha'] for item in request['pull_requests']], 'changed_files': [item['changed_files'] for item in request['pull_requests']]}
	source_id = 'github-comment:' + request['repository_id'] + ':' + str(comment['id']) + ':' + comment['node_id']
	seed = {'binding': binding, 'expires_at': request['expires_at'], 'comment_body_sha256': fingerprint('overlap-comment-body', comment['body'])}
	allowance_id = fingerprint('controller-overlap-allowance', seed)
	record = {'schema_version': 1, 'allowance_id': allowance_id, 'source_id': source_id, 'binding': binding, 'approval': {'actor_id': comment['actor_id'], 'actor_login': comment['actor_login'], 'comment_id': comment['id'], 'comment_node_id': comment['node_id'], 'created_at': comment['created_at'], 'body': comment['body'], 'body_sha256': seed['comment_body_sha256']}, 'expires_at': request['expires_at'], 'provider_binding_sha256': fingerprint('overlap-provider-observation', observation)}
	record['allowance_sha256'] = fingerprint('controller-overlap-allowance-record', record)
	return record

def _reparse(path):
	try:
		metadata = Path(path).lstat()
	except FileNotFoundError:
		return False
	attributes = getattr(metadata, 'st_file_attributes', 0)
	reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)
	return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag) or bool(getattr(Path(path), 'is_junction', lambda: False)())

def _git(root, *args):
	environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_') and key.upper() not in {'PYTHONPATH', 'PYTHONHOME'}}
	environment.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH='1')
	try:
		result = subprocess.run(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.quotePath=false', '-c', 'protocol.file.allow=never', '-c', 'safe.directory=' + str(root), '-C', str(root), *args], capture_output=True, timeout=30, check=False, env=child_env(environment))
	except (OSError, subprocess.SubprocessError) as exc:
		raise ValidationError(f'Git path inventory failed: {type(exc).__name__}') from exc
	_require(result.returncode == 0, 'Git path inventory could not be observed')
	return result.stdout

def canonical_repository_paths(tickets, repository_root, head_sha, tree_sha):
	root_input = Path(repository_root)
	_require(root_input.is_absolute() and root_input.is_dir(), 'Path ownership needs an absolute existing repository worktree')
	for parent in (root_input, *root_input.parents):
		if parent.exists() and _reparse(parent):
			raise ValidationError('Repository worktree traverses a link, junction, or reparse point')
	root = root_input.resolve(strict=True)
	observed_root = Path(_git(root, 'rev-parse', '--show-toplevel').decode('utf-8', 'strict').strip()).resolve(strict=True)
	_require(observed_root == root, 'Pinned repository root differs from the Git worktree')
	_require(isinstance(head_sha, str) and re.fullmatch('[0-9a-f]{40}', head_sha) and isinstance(tree_sha, str) and re.fullmatch('[0-9a-f]{40}', tree_sha), 'Repository ownership needs lowercase full head and tree pins')
	observed_head = _git(root, 'rev-parse', 'HEAD').decode('ascii', 'strict').strip()
	observed_tree = _git(root, 'rev-parse', 'HEAD^{tree}').decode('ascii', 'strict').strip()
	_require((observed_head, observed_tree) == (head_sha, tree_sha), 'Repository ownership tuple differs from the pinned head/tree')
	try:
		records = _git(root, 'ls-files', '-s', '-z').decode('utf-8', 'strict').split('\x00')
	except UnicodeDecodeError as exc:
		raise ValidationError('Git path inventory contains a non-UTF-8 path') from exc
	modes, canonical_names, directory_names = ({}, {}, {})
	for record in records:
		if not record:
			continue
		metadata, name = record.split('\t', 1)
		mode, _object_id, stage = metadata.split(' ')
		_require(stage == '0' and _owned_path(name) == name, 'Git path inventory contains an unsafe or unmerged path')
		modes[name] = mode
		parts = name.split('/')
		for index in range(1, len(parts) + 1):
			prefix = '/'.join(parts[:index])
			bucket = canonical_names.setdefault(prefix.casefold(), set())
			bucket.add(prefix)
			if index < len(parts):
				directory_names.setdefault(prefix.casefold(), set()).add(prefix)
	_require(all((len(names) == 1 for names in canonical_names.values())), 'Git path inventory has case-folded alias collisions')
	names = {key: next(iter(values)) for key, values in canonical_names.items()}
	directories = {key: next(iter(values)) for key, values in directory_names.items()}

	def tracked_identity(name):
		mode = modes[name]
		_require(mode not in {'120000', '160000'}, 'Owned path cannot contain a Git symlink or nested repository')
		candidate = root
		for component in name.split('/'):
			candidate = candidate / component
			if _reparse(candidate):
				raise ValidationError('Owned path traverses a symlink, junction, or reparse point')
		_require(candidate.exists() and candidate.is_file(), 'Tracked ownership surface is missing or not a regular file')
		resolved = candidate.resolve(strict=True)
		_require(resolved.is_relative_to(root), 'Owned path resolves outside the pinned repository')
		metadata = candidate.stat()
		device, inode = (getattr(metadata, 'st_dev', None), getattr(metadata, 'st_ino', None))
		_require(type(device) is int and type(inode) is int and (inode != 0), 'Tracked ownership surface has no reliable physical identity')
		return (device, inode)

	def canonical_path(raw):
		owned = _owned_path(raw)
		wildcard = next((suffix for suffix in ('/**', '/*') if owned.endswith(suffix)), '')
		surface = owned[:-len(wildcard)] if wildcard else owned
		current = root
		for component in surface.split('/'):
			current = current / component
			if current.exists() and _reparse(current):
				raise ValidationError('Owned path traverses a symlink, junction, or reparse point')
		key = surface.casefold()
		if key in names:
			canonical_surface = names[key]
		else:
			parts = surface.split('/')
			canonical_parts, matched_directory = ([], False)
			for index in range(len(parts), 0, -1):
				prefix_key = '/'.join(parts[:index]).casefold()
				if prefix_key in directories:
					canonical_parts = directories[prefix_key].split('/') + parts[index:]
					matched_directory = True
					break
			if not matched_directory:
				_require(len(parts) == 1, 'Owned path is absent from the pinned Git inventory and has no canonical parent')
				canonical_parts = parts
			canonical_surface = '/'.join(canonical_parts)
		_require(not wildcard or canonical_surface.casefold() in directories, 'Wildcard ownership must name a canonical Git directory')
		candidate = root.joinpath(*canonical_surface.split('/'))
		resolved = candidate.resolve(strict=False)
		_require(resolved == root or resolved.is_relative_to(root), 'Owned path resolves outside the pinned repository')
		mode = modes.get(canonical_surface)
		_require(mode not in {'120000', '160000'}, 'Owned path cannot be a Git symlink or nested repository')
		if mode is not None:
			covered = [canonical_surface]
		elif canonical_surface.casefold() in directories:
			prefix = canonical_surface + '/'
			covered = [name for name in modes if name.startswith(prefix)]
		else:
			covered = []
		_require(not wildcard or covered, 'Wildcard ownership has no tracked descendants')
		return (canonical_surface + wildcard, covered)
	prepared, covered_names = ([], set())
	for item in tickets:
		admitted = _ticket(item)
		canonical_claims, covered = ([], [])
		for path in admitted['paths']:
			claim, descendants = canonical_path(path)
			canonical_claims.append(claim)
			covered.extend(descendants)
		_require(len(canonical_claims) == len(set(canonical_claims)), 'Ticket paths collapse to one canonical repository surface')
		covered_names.update(covered)
		prepared.append((admitted, canonical_claims, covered))
	physical_names = {}
	for name in sorted(covered_names, key=lambda value: (value.casefold(), value)):
		identity = tracked_identity(name)
		physical_names.setdefault(identity, []).append(name)
	physical_representative = {name: members[0] for members in physical_names.values() for name in members}
	result = []
	for admitted, canonical_claims, covered in prepared:
		aliases = [physical_representative[name] for name in covered]
		admitted['paths'] = list(dict.fromkeys(canonical_claims + aliases))
		result.append(admitted)
	binding = {'repository_root': str(root), 'head_sha': head_sha, 'tree_sha': tree_sha, 'git_path_count': len(modes)}
	return (result, binding)

class ContinuousControllerStore:

	def __init__(self, path, stream_ids, cadence_seconds=DEFAULT_STATUS_CADENCE_SECONDS, worktree_roots=(), periodic_status_enabled=True, migrate_cadence=False):
		self.worktree_roots = tuple((Path(root).resolve(strict=False) for root in worktree_roots))
		self.path = protected_state_path(path, self.worktree_roots)
		_require(isinstance(stream_ids, (list, tuple)) and stream_ids and (len(stream_ids) == len(set(stream_ids))) and all((isinstance(item, str) and item.strip() for item in stream_ids)), 'Configured stream identities must be unique nonempty strings')
		_require(type(cadence_seconds) is int and cadence_seconds > 0, 'Status cadence must be positive seconds')
		_require(type(periodic_status_enabled) is bool, 'Periodic status enablement must be boolean')
		_require(type(migrate_cadence) is bool, 'Cadence migration choice must be boolean')
		self.periodic_status_enabled = periodic_status_enabled
		self.path.parent.mkdir(parents=True, exist_ok=True)
		with self.connection() as db:
			db.executescript(DDL)
			db.execute('BEGIN IMMEDIATE')
			try:
				configured = canonical(sorted(stream_ids)).decode()
				old = db.execute("SELECT value FROM controller_meta WHERE key='streams'").fetchone()
				_require(old is None or old[0] == configured, 'Configured stream set changed; perform explicit controller migration')
				for key, value in (('streams', configured), ('cadence_seconds', str(cadence_seconds)), ('revision', '0'), ('last_regular_digest', ''), ('last_digest_revision', '-1')):
					db.execute('INSERT OR IGNORE INTO controller_meta VALUES(?,?)', (key, value))
				stored_cadence = int(self._meta(db, 'cadence_seconds'))
				if stored_cadence != cadence_seconds and migrate_cadence:
					db.execute("UPDATE controller_meta SET value=? WHERE key='cadence_seconds'", (str(cadence_seconds),))
				else:
					_require(stored_cadence == cadence_seconds, 'Configured status cadence changed; explicitly migrate the stored cadence')
				for stream in sorted(stream_ids):
					db.execute('INSERT OR IGNORE INTO stream_status VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (stream, 'COMPLETE', None, 'controller', 'No scoped work has been scheduled', 'Observe the scoped backlog', 'an eligible scoped ticket appears', 'UNOBSERVED', 'No active action', 'UNOBSERVED', canonical(_zero_counts()).decode(), 0, 'UNOBSERVED', '[]', now_text()))
				for gate_row in db.execute("SELECT gate_id,record_json FROM controller_overlap_sync_gate WHERE status='IN_FLIGHT'").fetchall():
					gate = loads(gate_row['record_json'])
					gate['status'] = 'UNKNOWN'
					db.execute("UPDATE controller_overlap_sync_gate SET status='UNKNOWN',record_json=? WHERE gate_id=?", (canonical(gate).decode(), gate_row['gate_id']))
				db.execute('COMMIT')
			except BaseException:
				db.execute('ROLLBACK')
				raise
		restrict_state_permissions(self.path)

	@contextmanager
	def connection(self):
		db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
		try:
			db.row_factory = sqlite3.Row
			configure_database(db, self.path, STATE_APPLICATION_ID)
			yield db
		finally:
			db.close()

	@contextmanager
	def transaction(self):
		with self.connection() as db:
			db.execute('BEGIN IMMEDIATE')
			try:
				yield db
				db.execute('COMMIT')
			except BaseException:
				db.execute('ROLLBACK')
				raise

	@staticmethod
	def _meta(db, key):
		row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
		_require(row is not None, f'Missing controller metadata {key}')
		return row[0]

	def set_cadence(self, seconds):
		_require(type(seconds) is int and seconds > 0, 'Status cadence must be positive seconds')
		with self.transaction() as db:
			db.execute("UPDATE controller_meta SET value=? WHERE key='cadence_seconds'", (str(seconds),))

	def bind_repository(self, binding):
		encoded = canonical(binding).decode()
		with self.transaction() as db:
			prior = db.execute("SELECT value FROM controller_meta WHERE key='repository_binding'").fetchone()
			_require(prior is None or prior[0] == encoded, 'Controller repository/worktree binding changed; use an explicit state migration')
			db.execute("INSERT OR IGNORE INTO controller_meta VALUES('repository_binding',?)", (encoded,))

	def register_overlap_allowance(self, record, now):
		from .canonical import fingerprint
		timestamp(now)
		required = {'schema_version', 'allowance_id', 'source_id', 'binding', 'approval', 'expires_at', 'provider_binding_sha256', 'allowance_sha256'}
		_require(isinstance(record, dict) and set(record) == required and (record['schema_version'] == 1) and (record['allowance_sha256'] == fingerprint('controller-overlap-allowance-record', {key: value for key, value in record.items() if key != 'allowance_sha256'})), 'Overlap allowance record is malformed or mutated')
		binding = record['binding']
		_require(isinstance(binding, dict) and set(binding) == {'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads', 'changed_files'}, 'Overlap allowance binding is not closed')
		with self.transaction() as db:
			by_source = db.execute('SELECT allowance_id,record_json FROM controller_overlap_allowance WHERE source_id=?', (record['source_id'],)).fetchone()
			if by_source is not None:
				prior = loads(by_source['record_json'])
				stable = {'schema_version', 'allowance_id', 'source_id', 'binding', 'approval', 'expires_at'}
				_require(all((prior[key] == record[key] for key in stable)), 'Overlap allowance approval comment replayed for different scope')
				db.execute('UPDATE controller_overlap_allowance SET record_json=?,updated_at=? WHERE allowance_id=?', (canonical(record).decode(), now, record['allowance_id']))
				return deepcopy(record)
			by_id = db.execute('SELECT record_json FROM controller_overlap_allowance WHERE allowance_id=?', (record['allowance_id'],)).fetchone()
			_require(by_id is None, 'Overlap allowance canonical approval was replayed from another comment')
			db.execute('INSERT INTO controller_overlap_allowance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (record['allowance_id'], record['source_id'], binding['repository_id'], binding['cycle_id'], binding['path'], canonical(binding['tickets']).decode(), canonical(binding['pull_requests']).decode(), canonical(binding['heads']).decode(), record['expires_at'], 'ACTIVE', canonical(record).decode(), now))
			return deepcopy(record)

	def overlap_allowance(self, allowance_id):
		with self.connection() as db:
			row = db.execute('SELECT status,record_json FROM controller_overlap_allowance WHERE allowance_id=?', (allowance_id,)).fetchone()
			if row is None:
				return None
			return {**loads(row['record_json']), 'status': row['status']}

	def recheck_overlap_allowance(self, allowance_id, record, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status,record_json FROM controller_overlap_allowance WHERE allowance_id=?', (allowance_id,)).fetchone()
			_require(row is not None and row['status'] == 'ACTIVE', 'Overlap allowance is not active at lane start')
			prior = loads(row['record_json'])
			stable = {'schema_version', 'allowance_id', 'source_id', 'binding', 'approval', 'expires_at'}
			_require(all((prior[key] == record.get(key) for key in stable)), 'Overlap allowance live scope changed between plan and lane start')
			_require(timestamp(now) < timestamp(record['expires_at']), 'Overlap allowance expired before lane start')
			refreshed = deepcopy(record)
			db.execute('UPDATE controller_overlap_allowance SET record_json=?,updated_at=? WHERE allowance_id=?', (canonical(refreshed).decode(), now, allowance_id))
			return refreshed

	def expire_overlap_allowance(self, allowance_id, now):
		from .canonical import fingerprint
		current = timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_overlap_allowance WHERE allowance_id=?', (allowance_id,)).fetchone()
			_require(row is not None, 'Cannot expire an unknown overlap allowance')
			record = loads(row['record_json'])
			_require(current >= timestamp(record['expires_at']), 'Overlap allowance has not reached its approved expiry')
			prior = db.execute('SELECT record_json FROM controller_overlap_sync_gate WHERE allowance_id=? ORDER BY rowid DESC LIMIT 1', (allowance_id,)).fetchone()
			if prior is not None:
				return loads(prior['record_json'])
			binding = record['binding']
			sequence = db.execute('SELECT COUNT(*) FROM controller_overlap_sync_gate WHERE allowance_id=?', (allowance_id,)).fetchone()[0]
			gate_id = fingerprint('controller-overlap-sync-gate', {'allowance_id': allowance_id, 'binding': binding, 'expired_at': now, 'sequence': sequence})
			gate = {'schema_version': 1, 'gate_id': gate_id, 'allowance_id': allowance_id, 'repository_id': binding['repository_id'], 'cycle_id': binding['cycle_id'], 'path': binding['path'], 'tickets': binding['tickets'], 'pull_requests': binding['pull_requests'], 'heads': binding['heads'], 'status': 'PENDING', 'expired_at': now, 'decision': None, 'execution': None, 'critic_evidence': None}
			db.execute('INSERT INTO controller_overlap_sync_gate VALUES(?,?,?,?,?)', (gate_id, allowance_id, 'PENDING', canonical(gate).decode(), now))
			db.execute("UPDATE controller_overlap_allowance SET status='EXPIRED',updated_at=? WHERE allowance_id=?", (now, allowance_id))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return deepcopy(gate)

	def expire_due_overlap_allowances(self, now):
		current = timestamp(now)
		with self.connection() as db:
			due = [row['allowance_id'] for row in db.execute("SELECT allowance_id,expires_at FROM controller_overlap_allowance WHERE status='ACTIVE' ORDER BY rowid") if current >= timestamp(row['expires_at'])]
		return [self.expire_overlap_allowance(allowance_id, now) for allowance_id in due]

	def unresolved_overlap_sync_gates(self):
		with self.connection() as db:
			return [loads(row['record_json']) for row in db.execute("SELECT record_json FROM controller_overlap_sync_gate WHERE status IN ('PENDING','SYNCED','IN_FLIGHT','UNKNOWN') ORDER BY rowid")]

	def overlap_sync_quiescence(self, gate_id):
		with self.connection() as db:
			gate_row = db.execute('SELECT record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(gate_row is not None, 'Unknown overlap synchronization gate')
			gate = loads(gate_row['record_json'])
			placeholders = ','.join(('?' for _ in gate['tickets']))
			rows = db.execute('SELECT * FROM controller_dispatch WHERE ticket IN (' + placeholders + ") AND status IN ('PENDING','IN_FLIGHT','UNKNOWN','ACCEPTED') ORDER BY rowid", tuple(gate['tickets'])).fetchall()
			observations = []
			blockers = []
			for row in rows:
				if row['status'] == 'ACCEPTED':
					observations.append({'dispatch_id': row['dispatch_id'], 'payload': loads(row['payload_json']), 'receipt': loads(row['receipt_json'])})
				else:
					blockers.append({'dispatch_id': row['dispatch_id'], 'ticket': row['ticket'], 'status': row['status']})
			terminal = {(row['ticket'], row['exact_tuple']) for row in db.execute('SELECT ticket,exact_tuple FROM controller_dispatch WHERE ticket IN (' + placeholders + ") AND status='TERMINAL'", tuple(gate['tickets']))}
			for stream in db.execute('SELECT stream_id,state,ticket,exact_tuple FROM stream_status ORDER BY stream_id'):
				if stream['ticket'] in gate['tickets'] and stream['state'] != 'COMPLETE' and ((stream['ticket'], stream['exact_tuple']) not in terminal):
					blockers.append({'stream': stream['stream_id'], 'ticket': stream['ticket'], 'status': 'UNRECONCILED_' + stream['state']})
			return {'ready': not observations and (not blockers), 'observations': observations, 'blockers': blockers}

	def confirm_dispatch_quiescence(self, dispatch_id, observation, now):
		timestamp(now)
		required = {'dispatch_id', 'ticket', 'exact_tuple', 'session_id', 'status', 'terminal', 'observed_at'}
		_require(isinstance(observation, dict) and set(observation) == required and (observation['status'] == 'QUIESCENT') and (observation['terminal'] is True), 'Dispatch quiescence observation is malformed')
		timestamp(observation['observed_at'])
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] in {'ACCEPTED', 'TERMINAL'}, 'Only an accepted dispatch can become quiescent')
			receipt = loads(row['receipt_json'])
			_require(timestamp(observation['observed_at']) >= timestamp(now) and timestamp(observation['observed_at']) >= timestamp(receipt['observed_at']), 'Dispatch quiescence observation predates the cycle or accepted session')
			_require(observation['dispatch_id'] == dispatch_id and observation['ticket'] == row['ticket'] and (observation['exact_tuple'] == row['exact_tuple']) and (observation['session_id'] == receipt['session_id']), 'Dispatch quiescence proof differs from the accepted session')
			payload = loads(row['payload_json'])
			prior = payload.get('terminal_observation')
			_require(prior is None or prior == observation, 'Dispatch quiescence replay differs from retained proof')
			payload['terminal_observation'] = deepcopy(observation)
			db.execute("UPDATE controller_dispatch SET status='TERMINAL',payload_json=?,updated_at=? WHERE dispatch_id=?", (canonical(payload).decode(), observation['observed_at'], dispatch_id))
			return deepcopy(observation)

	def overlap_sync_gate(self, gate_id):
		with self.connection() as db:
			row = db.execute('SELECT record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			return None if row is None else loads(row['record_json'])

	def execute_overlap_sync_gate(self, gate_id, observation, sync_writer, now):
		from .canonical import fingerprint
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status,record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(row is not None, 'Unknown overlap synchronization gate')
			gate = loads(row['record_json'])
			if row['status'] in {'CLEARED', 'SYNCED', 'EXECUTED'}:
				return gate
			_require(row['status'] == 'PENDING', 'Overlap synchronization executor outcome is unresolved')
			required = {'gate_id', 'status', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads', 'provider_observed_at', 'etag'}
			_require(isinstance(observation, dict) and set(observation) == required and (observation['status'] in {'NO_CONFLICT', 'CONFIRMED_CONFLICT'}), 'Overlap synchronization conflict decision is malformed')
			for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads'):
				_require(observation[key] == gate[key], 'Overlap synchronization decision is not pair/head bound')
			_provider_fresh(observation, now, 'Overlap synchronization conflict decision')
			decision = deepcopy(observation)
			gate['decision'] = decision
			if decision['status'] == 'NO_CONFLICT':
				gate['status'] = 'CLEARED'
				db.execute("UPDATE controller_overlap_sync_gate SET status='CLEARED',record_json=?,updated_at=? WHERE gate_id=?", (canonical(gate).decode(), now, gate_id))
				return gate
			_require(callable(sync_writer), 'Confirmed overlap conflict requires the decided sync-writer executor')
			execution_id = fingerprint('controller-overlap-sync-execution', {'gate_id': gate_id, 'decision_sha256': fingerprint('controller-overlap-sync-decision', decision)})
			payload = {'gate_id': gate_id, 'execution_id': execution_id, 'repository_id': gate['repository_id'], 'cycle_id': gate['cycle_id'], 'path': gate['path'], 'tickets': gate['tickets'], 'pull_requests': gate['pull_requests'], 'heads': gate['heads'], 'conflict_sha256': fingerprint('controller-overlap-sync-decision', decision)}
			gate['status'] = 'IN_FLIGHT'
			gate['execution'] = payload
			db.execute("UPDATE controller_overlap_sync_gate SET status='IN_FLIGHT',record_json=?,updated_at=? WHERE gate_id=?", (canonical(gate).decode(), now, gate_id))
		try:
			receipt = sync_writer(deepcopy(payload))
		except Exception:
			with self.transaction() as db:
				gate['status'] = 'UNKNOWN'
				db.execute("UPDATE controller_overlap_sync_gate SET status='UNKNOWN',record_json=?,updated_at=? WHERE gate_id=?", (canonical(gate).decode(), now, gate_id))
			raise
		try:
			required_receipt = set(payload) | {'status', 'post_sync_heads', 'diff', 'diff_sha256', 'changed_files', 'observed_at'}
			diff = receipt.get('diff') if isinstance(receipt, dict) else None
			diff_bytes = diff.encode('utf-8') if isinstance(diff, str) else b''
			_require(isinstance(receipt, dict) and set(receipt) == required_receipt and all((receipt[key] == payload[key] for key in payload)) and (receipt['status'] == 'COMPLETED') and isinstance(receipt['post_sync_heads'], list) and (len(receipt['post_sync_heads']) == len(gate['heads'])) and all((isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head) is not None for head in receipt['post_sync_heads'])) and (receipt['post_sync_heads'] != gate['heads']) and isinstance(diff, str) and diff.strip() and (len(diff_bytes) <= OVERLAP_SYNC_DIFF_MAX_BYTES) and (receipt['diff_sha256'] == hashlib.sha256(diff_bytes).hexdigest()) and isinstance(receipt['changed_files'], list) and (receipt['changed_files'] == sorted(receipt['changed_files'])) and (len(receipt['changed_files']) == len(set(receipt['changed_files']))) and (gate['path'] in receipt['changed_files']) and (timestamp(receipt['observed_at']) >= timestamp(now)), 'Overlap synchronization executor receipt is missing or mismatched')
			for path in receipt['changed_files']:
				_exact_overlap_path(path)
		except Exception:
			with self.transaction() as db:
				gate['status'] = 'UNKNOWN'
				db.execute("UPDATE controller_overlap_sync_gate SET status='UNKNOWN',record_json=?,updated_at=? WHERE gate_id=? AND status='IN_FLIGHT'", (canonical(gate).decode(), now, gate_id))
			raise
		with self.transaction() as db:
			current = db.execute('SELECT status FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(current is not None and current['status'] == 'IN_FLIGHT', 'Overlap synchronization gate changed during execution')
			evidence = {'gate_id': gate_id, 'allowance_id': gate['allowance_id'], 'tickets': gate['tickets'], 'pull_requests': gate['pull_requests'], 'heads': gate['heads'], 'pre_sync_heads': gate['heads'], 'post_sync_heads': receipt['post_sync_heads'], 'path': gate['path'], 'diff': receipt['diff'], 'diff_sha256': receipt['diff_sha256'], 'changed_files': receipt['changed_files'], 'executor_receipt_sha256': fingerprint('controller-overlap-sync-receipt', receipt)}
			gate['status'] = 'SYNCED'
			gate['execution'] = deepcopy(receipt)
			gate['critic_evidence'] = evidence
			db.execute("UPDATE controller_overlap_sync_gate SET status='SYNCED',record_json=?,updated_at=? WHERE gate_id=?", (canonical(gate).decode(), receipt['observed_at'], gate_id))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return deepcopy(gate)

	def confirm_overlap_sync_inventory(self, gate_id, inventory, *, inventory_sha256, observed_at, provider_observation):
		timestamp(observed_at)
		_require(isinstance(inventory_sha256, str) and re.fullmatch('[0-9a-f]{64}', inventory_sha256) is not None, 'Post-sync inventory digest is malformed')
		_require(isinstance(inventory, list), 'Post-sync inventory must be a complete ticket list')
		validated_inventory = [_ticket(item) for item in inventory]
		tickets = {item['ticket']: item for item in validated_inventory}
		_require(len(tickets) == len(inventory), 'Post-sync inventory contains duplicate tickets')
		with self.transaction() as db:
			row = db.execute('SELECT status,record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(row is not None, 'Unknown overlap synchronization gate')
			gate = loads(row['record_json'])
			_require(row['status'] in {'SYNCED', 'EXECUTED'}, 'Overlap synchronization has no completed executor receipt')
			evidence = gate.get('critic_evidence')
			_require(isinstance(evidence, dict) and isinstance(evidence.get('post_sync_heads'), list), 'Overlap synchronization post-sync heads are unavailable')
			provider_required = {'gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads', 'provider_observed_at', 'etag'}
			_require(isinstance(provider_observation, dict) and set(provider_observation) == provider_required, 'Post-sync provider head observation is malformed')
			for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests'):
				_require(provider_observation[key] == gate[key], 'Post-sync provider observation differs from the durable gate')
			_require(provider_observation['heads'] == evidence['post_sync_heads'], 'Fresh provider observation does not bind the post-sync PR heads')
			_provider_fresh(provider_observation, observed_at, 'Post-sync provider head observation')
			post_sync_inventory = []
			for ticket_name, head_sha in zip(gate['tickets'], evidence['post_sync_heads']):
				ticket = tickets.get(ticket_name)
				_require(ticket is not None and gate['path'] in ticket['paths'] and (_exact_tuple_fields(ticket['exact_tuple'])['head'] == head_sha), 'Fresh inventory does not bind the post-sync PR head and ownership')
				post_sync_inventory.append({'ticket': ticket_name, 'head_sha': head_sha, 'exact_tuple': ticket['exact_tuple']})
			if row['status'] == 'EXECUTED':
				_require(evidence.get('inventory_sha256') == inventory_sha256 and evidence.get('post_sync_inventory') == post_sync_inventory, 'Post-sync inventory replay differs from completed evidence')
				return gate
			evidence['inventory_sha256'] = inventory_sha256
			evidence['inventory_observed_at'] = observed_at
			evidence['post_sync_inventory'] = post_sync_inventory
			evidence['provider_head_observation'] = deepcopy(provider_observation)
			gate['status'] = 'EXECUTED'
			gate['critic_evidence'] = evidence
			db.execute("UPDATE controller_overlap_sync_gate SET status='EXECUTED',record_json=?,updated_at=? WHERE gate_id=? AND status='SYNCED'", (canonical(gate).decode(), observed_at, gate_id))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return deepcopy(gate)

	def critic_overlap_evidence(self, ticket):
		_require(isinstance(ticket, str) and ticket, 'Critic overlap evidence needs a ticket identity')
		result = []
		with self.connection() as db:
			for row in db.execute("SELECT record_json FROM controller_overlap_sync_gate WHERE status='EXECUTED' ORDER BY rowid"):
				gate = loads(row['record_json'])
				if ticket in gate['tickets']:
					result.append(deepcopy(gate['critic_evidence']))
		return result

	@staticmethod
	def _row_value(stream, state, ticket, now):
		return (ticket.get('ticket') if ticket else None, ticket.get('actor', 'controller') if ticket else 'controller', ticket.get('reason', 'Scoped backlog complete') if ticket else 'Scoped backlog complete', ticket.get('next_action', 'Observe for newly scoped work') if ticket else 'Observe for newly scoped work', ticket.get('resume_trigger', 'new scoped work') if ticket else 'new scoped work', ticket.get('exact_tuple', 'UNOBSERVED') if ticket else 'UNOBSERVED', ticket.get('activity', 'No remaining scoped action') if ticket else 'No remaining scoped action', ticket.get('verification_gate', 'NOT_APPLICABLE') if ticket else 'NOT_APPLICABLE', canonical(ticket.get('reviewer_completion', _zero_counts()) if ticket else _zero_counts()).decode(), ticket.get('open_findings', 0) if ticket else 0, ticket.get('jira_status', 'UNOBSERVED') if ticket else 'UNOBSERVED', canonical(ticket.get('paths', []) if ticket else []).decode(), now)

	def _update_stream(self, db, stream, state, ticket, now):
		_require(state in STREAM_STATES, 'A configured stream cannot enter an unlabelled idle state')
		current = db.execute('SELECT * FROM stream_status WHERE stream_id=?', (stream,)).fetchone()
		values = self._row_value(stream, state, ticket, now)
		comparable = (state, *values[:-1])
		before = (current['state'], current['ticket'], current['actor'], current['reason'], current['next_action'], current['resume_trigger'], current['exact_tuple'], current['activity'], current['verification_gate'], current['reviewer_completion'], current['open_findings'], current['jira_status'], current['paths'])
		if comparable != before:
			db.execute('UPDATE stream_status SET state=?,ticket=?,actor=?,reason=?,next_action=?,resume_trigger=?,exact_tuple=?,activity=?,verification_gate=?,reviewer_completion=?,open_findings=?,jira_status=?,paths=?,updated_at=? WHERE stream_id=?', (state, *values, stream))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))

	@staticmethod
	def _stream_restore_snapshot(state, ticket):
		return {'state': state, 'ticket': ticket['ticket'], 'actor': ticket['actor'], 'reason': ticket['reason'], 'next_action': ticket['next_action'], 'resume_trigger': ticket['resume_trigger'], 'exact_tuple': ticket['exact_tuple'], 'activity': ticket['activity'], 'verification_gate': ticket['verification_gate'], 'reviewer_completion': ticket['reviewer_completion'], 'open_findings': ticket['open_findings'], 'jira_status': ticket['jira_status'], 'paths': ticket['paths']}

	@staticmethod
	def _row_restore_snapshot(row):
		return {'state': row['state'], 'ticket': row['ticket'], 'actor': row['actor'], 'reason': row['reason'], 'next_action': row['next_action'], 'resume_trigger': row['resume_trigger'], 'exact_tuple': row['exact_tuple'], 'activity': row['activity'], 'verification_gate': row['verification_gate'], 'reviewer_completion': loads(row['reviewer_completion']), 'open_findings': row['open_findings'], 'jira_status': row['jira_status'], 'paths': loads(row['paths'])}

	@staticmethod
	def _remember_unknown_stream(db, dispatch_id, snapshot, *, replace):
		operation = db.execute('SELECT payload_json FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
		_require(operation is not None, 'Unknown dispatch is missing from the protected state')
		payload = loads(operation['payload_json'])
		if replace or '_stream_before_unknown' not in payload:
			payload['_stream_before_unknown'] = snapshot
			db.execute('UPDATE controller_dispatch SET payload_json=? WHERE dispatch_id=?', (canonical(payload).decode(), dispatch_id))

	@staticmethod
	def _restore_unknown_stream(db, dispatch_id, payload, now):
		stream = db.execute('SELECT * FROM stream_status WHERE stream_id=?', (payload['stream'],)).fetchone()
		unknown_reason = f"Dispatch outcome unknown for {payload['ticket']} ({dispatch_id}); reconcile before retry"
		if stream is None or stream['ticket'] != payload['ticket'] or stream['exact_tuple'] != payload['exact_tuple'] or (stream['state'] != 'BLOCKED') or (stream['reason'] != unknown_reason):
			return
		restore = payload.get('_stream_before_unknown')
		_require(isinstance(restore, dict) and set(restore) == {'state', 'ticket', 'actor', 'reason', 'next_action', 'resume_trigger', 'exact_tuple', 'activity', 'verification_gate', 'reviewer_completion', 'open_findings', 'jira_status', 'paths'}, 'UNKNOWN dispatch is missing its matching stream restoration state')
		_require(restore['state'] in STREAM_STATES and restore['state'] != 'COMPLETE' and (restore['ticket'] == payload['ticket']) and (restore['exact_tuple'] == payload['exact_tuple']), 'UNKNOWN dispatch stream restoration state is mismatched')
		db.execute('UPDATE stream_status SET state=?,ticket=?,actor=?,reason=?,next_action=?,resume_trigger=?,exact_tuple=?,activity=?,verification_gate=?,reviewer_completion=?,open_findings=?,jira_status=?,paths=?,updated_at=? WHERE stream_id=?', (restore['state'], restore['ticket'], restore['actor'], restore['reason'], restore['next_action'], restore['resume_trigger'], restore['exact_tuple'], restore['activity'], restore['verification_gate'], canonical(_reviewer_counts(restore['reviewer_completion'])).decode(), restore['open_findings'], restore['jira_status'], canonical(restore['paths']).decode(), now, payload['stream']))

	def schedule(self, inventory, now, host_capacity, overlap_allowances=()):
		timestamp(now)
		_require(type(host_capacity) is int and host_capacity >= 0, 'Observed host capacity must be nonnegative')
		tickets = [_ticket(item) for item in inventory]
		_require(len(tickets) == len({item['ticket'] for item in tickets}), 'Scoped inventory has duplicate tickets')
		with self.transaction() as db:
			self._schedule(db, tickets, now, host_capacity, overlap_allowances)
			return self._snapshot(db)

	def _schedule(self, db, tickets, now, host_capacity, overlap_allowances=()):
		rows = db.execute('SELECT * FROM stream_status ORDER BY stream_id').fetchall()
		inventory = {item['ticket']: item for item in tickets}
		for item in tickets:
			prior = db.execute('SELECT dispatch_role,lifecycle_stage FROM controller_ticket_authority WHERE ticket=? AND exact_tuple=?', (item['ticket'], item['exact_tuple'])).fetchone()
			_require(prior is None or (prior['dispatch_role'], prior['lifecycle_stage']) == (item['dispatch_role'], item['lifecycle_stage']), 'Ticket dispatch authority changed without a new exact tuple')
			db.execute('INSERT OR IGNORE INTO controller_ticket_authority VALUES(?,?,?,?,?)', (item['ticket'], item['exact_tuple'], item['dispatch_role'], item['lifecycle_stage'], now))
		reserved_dispatches, capacity_intents, unknown_dispatches = ({}, [], {})
		for intent in db.execute("SELECT dispatch_id,ticket,stream_id,exact_tuple,status FROM controller_dispatch WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN')"):
			reserved_dispatches.setdefault(intent['ticket'], set()).add(intent['stream_id'])
			if intent['status'] in {'IN_FLIGHT', 'UNKNOWN'}:
				capacity_intents.append((intent['stream_id'], intent['ticket']))
			if intent['status'] == 'UNKNOWN':
				unknown_dispatches.setdefault(intent['stream_id'], []).append(intent)
		reserved_tickets = set(reserved_dispatches)
		working, held_paths = ([], [])
		for row in rows:
			item = inventory.get(row['ticket']) if row['state'] == 'WORKING' else None
			prerequisites = item is not None and item['disposition'] == 'ELIGIBLE' and all((item['dependencies_satisfied'], item['budget_available'], item['cap_available'], item['review_independent']))
			intent_streams = reserved_dispatches.get(item['ticket'], set()) if item else set()
			intent_matches_stream = not intent_streams or intent_streams == {row['stream_id']}
			if prerequisites and intent_matches_stream and (len(working) < host_capacity) and (not any((_blocking_overlap(item['ticket'], item['paths'], held_ticket, paths, overlap_allowances) for held_ticket, paths in held_paths))):
				working.append(row)
				held_paths.append((item['ticket'], item['paths']))
				self._update_stream(db, row['stream_id'], 'WORKING', item, now)
		assigned = {row['ticket'] for row in working}
		represented_intents = {(row['stream_id'], row['ticket']) for row in working}
		represented_capacity = len({intent for intent in capacity_intents if intent in represented_intents})
		unresolved_capacity = max(0, len(capacity_intents) - represented_capacity)
		slots = max(0, host_capacity - len(working) - unresolved_capacity)
		eligible = sorted((item for item in tickets if item['ticket'] not in assigned and item['ticket'] not in reserved_tickets and (item['disposition'] == 'ELIGIBLE')), key=lambda item: (item['priority'], item['ticket']))
		paused = sorted((item for item in tickets if item['ticket'] not in assigned and item['ticket'] not in reserved_tickets and (item['disposition'] == 'PAUSED_INPUT')), key=lambda item: (item['priority'], item['ticket']))
		blocked = sorted((item for item in tickets if item['ticket'] not in assigned and item['ticket'] not in reserved_tickets and (item['disposition'] == 'BLOCKED')), key=lambda item: (item['priority'], item['ticket']))
		free_rows = [row for row in rows if row not in working]
		for row in free_rows:
			matching_unknown = next((intent for intent in unknown_dispatches.get(row['stream_id'], []) if row['ticket'] == intent['ticket'] and row['exact_tuple'] == intent['exact_tuple'] and (intent['ticket'] in inventory) and (inventory[intent['ticket']]['disposition'] != 'COMPLETE')), None)
			if matching_unknown is not None:
				item = inventory[matching_unknown['ticket']]
				underlying_state = 'WORKING' if item['disposition'] == 'ELIGIBLE' else item['disposition']
				self._remember_unknown_stream(db, matching_unknown['dispatch_id'], self._stream_restore_snapshot(underlying_state, item), replace=True)
				blocked_item = {**item, 'exact_tuple': matching_unknown['exact_tuple'], 'reason': f"Dispatch outcome unknown for {matching_unknown['ticket']} ({matching_unknown['dispatch_id']}); reconcile before retry", 'next_action': 'Observe the exact durable dispatch intent before dispatching more work on this stream', 'resume_trigger': 'the host returns a fresh observation for this dispatch and its ticket'}
				self._update_stream(db, row['stream_id'], 'BLOCKED', blocked_item, now)
				continue
			selected = None
			for item in eligible:
				prerequisites = item['dependencies_satisfied'] and item['budget_available'] and item['cap_available'] and item['review_independent']
				if prerequisites and slots > 0 and (not any((_blocking_overlap(item['ticket'], item['paths'], held_ticket, paths, overlap_allowances) for held_ticket, paths in held_paths))):
					selected = item
					break
			if selected is not None:
				eligible.remove(selected)
				assigned.add(selected['ticket'])
				held_paths.append((selected['ticket'], selected['paths']))
				slots -= 1
				self._update_stream(db, row['stream_id'], 'WORKING', selected, now)
				continue
			if eligible:
				item = eligible.pop(0)
				reason = []
				if not item['dependencies_satisfied']:
					reason.append('dependencies')
				if not item['budget_available']:
					reason.append('budget')
				if not item['cap_available']:
					reason.append('run or amendment cap')
				if not item['review_independent']:
					reason.append('review independence')
				if slots <= 0:
					reason.append('host capacity')
				if any((_blocking_overlap(item['ticket'], item['paths'], held_ticket, paths, overlap_allowances) for held_ticket, paths in held_paths)):
					reason.append('path ownership')
				blocked_item = {**item, 'reason': 'Blocked by ' + ', '.join(reason), 'next_action': 'Re-evaluate eligibility without weakening governance', 'resume_trigger': 'all named prerequisites become current'}
				self._update_stream(db, row['stream_id'], 'BLOCKED', blocked_item, now)
			elif paused:
				self._update_stream(db, row['stream_id'], 'PAUSED_INPUT', paused.pop(0), now)
			elif blocked:
				self._update_stream(db, row['stream_id'], 'BLOCKED', blocked.pop(0), now)
			else:
				self._update_stream(db, row['stream_id'], 'COMPLETE', None, now)

	def finish_and_refill(self, stream_id, ticket, inventory, now, host_capacity, overlap_allowances=()):
		timestamp(now)
		tickets = [_ticket(item) for item in inventory]
		repeated = next((item for item in tickets if item['ticket'] == ticket), None)
		_require(repeated is None or repeated['disposition'] == 'COMPLETE', 'Completed ticket remains dispatch-eligible; record its terminal lifecycle state before refill')
		with self.transaction() as db:
			row = db.execute('SELECT * FROM stream_status WHERE stream_id=?', (stream_id,)).fetchone()
			_require(row is not None and row['state'] == 'WORKING' and (row['ticket'] == ticket), "Completed run does not match the stream's active ticket")
			self._update_stream(db, stream_id, 'COMPLETE', None, now)
			self._schedule(db, tickets, now, host_capacity, overlap_allowances)
			return self._snapshot(db)

	def _snapshot(self, db):
		result = []
		for row in db.execute('SELECT * FROM stream_status ORDER BY stream_id'):
			result.append({'stream': row['stream_id'], 'state': row['state'], 'ticket': row['ticket'], 'actor': row['actor'], 'reason': row['reason'], 'next_action': row['next_action'], 'resume_trigger': row['resume_trigger'], 'exact_tuple': row['exact_tuple'], 'activity': row['activity'], 'verification_gate': row['verification_gate'], 'reviewer_completion': loads(row['reviewer_completion']), 'open_findings': row['open_findings'], 'jira_status': row['jira_status'], 'updated_at': row['updated_at']})
		_require(all((item['state'] in STREAM_STATES for item in result)), 'Unlabelled stream idle state detected')
		return result

	def snapshot(self):
		with self.connection() as db:
			return self._snapshot(db)

	def digest(self, now):
		current = timestamp(now)
		with self.transaction() as db:
			pending = db.execute('SELECT payload_json FROM controller_outbox WHERE acknowledged_at IS NULL ORDER BY rowid LIMIT 1').fetchone()
			if pending is not None:
				return loads(pending['payload_json'])
			revision = int(self._meta(db, 'revision'))
			last_revision = int(self._meta(db, 'last_digest_revision'))
			last_regular = self._meta(db, 'last_regular_digest')
			cadence = int(self._meta(db, 'cadence_seconds'))
			elapsed = None if not last_regular else (current - timestamp(last_regular)).total_seconds()
			_require(elapsed is None or elapsed >= 0, 'Host clock moved backwards; reconcile cadence before delivery')
			regular_due = self.periodic_status_enabled and (not last_regular or elapsed >= cadence)
			change_due = revision > last_revision and (self.periodic_status_enabled or revision > 0)
			streams = self._snapshot(db)
			unresolved_dispatches = []
			for row in db.execute("SELECT dispatch_id,stream_id,ticket,exact_tuple,status,updated_at FROM controller_dispatch WHERE status='UNKNOWN' ORDER BY stream_id,dispatch_id"):
				owner = db.execute('SELECT ticket,exact_tuple FROM stream_status WHERE stream_id=?', (row['stream_id'],)).fetchone()
				detached = owner is None or owner['ticket'] != row['ticket'] or owner['exact_tuple'] != row['exact_tuple']
				unresolved_dispatches.append({'dispatch_id': row['dispatch_id'], 'stream': row['stream_id'], 'ticket': row['ticket'], 'exact_tuple': row['exact_tuple'], 'status': row['status'], 'detached': detached, 'updated_at': row['updated_at']})
			all_complete = all((s['state'] == 'COMPLETE' for s in streams))
			if all_complete and (not change_due):
				return None
			if not regular_due and (not change_due):
				return None
			kind = 'REGULAR' if regular_due else 'CHANGE'
			from .canonical import fingerprint
			body = {'schema_version': 3, 'observed_at': now, 'kind': kind, 'cadence_seconds': cadence, 'all_complete': all_complete, 'streams': streams, 'unresolved_dispatches': unresolved_dispatches}
			delivery_id = fingerprint('controller-status-digest', {'revision': revision, **body})
			body['delivery_id'] = delivery_id
			db.execute('INSERT INTO controller_outbox VALUES(?,?,?,?,?,NULL)', (delivery_id, kind, revision, now, canonical(body).decode()))
			return body

	def acknowledge_digest(self, delivery_id, delivered_at):
		timestamp(delivered_at)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_outbox WHERE delivery_id=?', (delivery_id,)).fetchone()
			_require(row is not None, 'Unknown controller digest delivery')
			if row['acknowledged_at'] is not None:
				return loads(row['payload_json'])
			_require(timestamp(delivered_at) >= timestamp(row['observed_at']), 'Digest acknowledgement predates its observation')
			db.execute('UPDATE controller_outbox SET acknowledged_at=? WHERE delivery_id=?', (delivered_at, delivery_id))
			prior_revision = int(self._meta(db, 'last_digest_revision'))
			db.execute("UPDATE controller_meta SET value=? WHERE key='last_digest_revision'", (str(max(prior_revision, row['revision'])),))
			if row['kind'] == 'REGULAR':
				db.execute("UPDATE controller_meta SET value=? WHERE key='last_regular_digest'", (row['observed_at'],))
			return loads(row['payload_json'])

	def prepare_dispatches(self, now):
		timestamp(now)
		from .canonical import fingerprint
		with self.transaction() as db:
			for row in db.execute("SELECT * FROM stream_status WHERE state='WORKING' ORDER BY stream_id"):
				prior = db.execute("SELECT * FROM controller_dispatch WHERE stream_id=? AND ticket=? AND exact_tuple=? AND status<>'CANCELLED' ORDER BY rowid DESC LIMIT 1", (row['stream_id'], row['ticket'], row['exact_tuple'])).fetchone()
				if prior is not None:
					continue
				payload = {'stream': row['stream_id'], 'ticket': row['ticket'], 'exact_tuple': row['exact_tuple'], 'paths': loads(row['paths']), 'actor': row['actor'], 'next_action': row['next_action'], 'dispatch_nonce': str(uuid.uuid4()), 'prepared_at': now}
				authority = db.execute('SELECT dispatch_role,lifecycle_stage FROM controller_ticket_authority WHERE ticket=? AND exact_tuple=?', (row['ticket'], row['exact_tuple'])).fetchone()
				_require(authority is not None, 'Scheduled ticket has no durable authenticated dispatch authority')
				payload.update(role=authority['dispatch_role'], lifecycle_stage=authority['lifecycle_stage'])
				if authority['dispatch_role'] == 'critic':
					sync_evidence = []
					for gate_row in db.execute("SELECT record_json FROM controller_overlap_sync_gate WHERE status='EXECUTED' ORDER BY rowid"):
						gate = loads(gate_row['record_json'])
						if row['ticket'] in gate['tickets']:
							sync_evidence.append(gate['critic_evidence'])
					if sync_evidence:
						payload['overlap_sync_evidence'] = sync_evidence
				dispatch_id = fingerprint('controller-dispatch', payload)
				payload['dispatch_id'] = dispatch_id
				db.execute('INSERT OR IGNORE INTO controller_dispatch VALUES(?,?,?,?,? ,?,NULL,?)', (dispatch_id, row['stream_id'], row['ticket'], row['exact_tuple'], 'PENDING', canonical(payload).decode(), now))
			return [dict(row) | {'payload': loads(row['payload_json'])} for row in db.execute("SELECT * FROM controller_dispatch WHERE status IN ('PENDING','UNKNOWN') ORDER BY stream_id,dispatch_id")]

	def pending_dispatches(self):
		with self.connection() as db:
			return [dict(row) | {'payload': loads(row['payload_json'])} for row in db.execute("SELECT * FROM controller_dispatch WHERE status='PENDING' ORDER BY stream_id,dispatch_id")]

	def cancel_pending_dispatch(self, dispatch_id, now, reason):
		timestamp(now)
		_require(isinstance(reason, str) and reason.strip(), 'Pending dispatch cancellation requires a preflight reason')
		with self.transaction() as db:
			row = db.execute('SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Only a never-begun pending dispatch can be cancelled')
			payload = loads(row['payload_json'])
			_require('begun_at' not in payload, 'A dispatch with a host-call begin time cannot be cancelled')
			payload['cancelled_at'] = now
			payload['cancellation_reason'] = reason.strip()
			db.execute("UPDATE controller_dispatch SET status='CANCELLED',payload_json=?,updated_at=? WHERE dispatch_id=? AND status='PENDING'", (canonical(payload).decode(), now, dispatch_id))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return dict(row) | {'status': 'CANCELLED', 'payload': payload}

	def begin_dispatch(self, dispatch_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Dispatch is not pending')
			payload = loads(row['payload_json'])
			_require('begun_at' not in payload, 'Pending dispatch already has a begin time')
			payload['begun_at'] = now
			db.execute("UPDATE controller_dispatch SET status='IN_FLIGHT',payload_json=?,updated_at=? WHERE dispatch_id=?", (canonical(payload).decode(), now, dispatch_id))
			return payload

	def record_dispatch_skill_selection(self, dispatch_id, record, now):
		timestamp(now)
		from . import VERSION
		from .codex_skill import FORMAT
		required = {'format', 'expected_version', 'project_root', 'pinned_skill_path', 'launch_directory', 'visible_skills', 'status', 'problems'}
		_require(isinstance(record, dict) and set(record) == required and (record.get('format') == FORMAT) and (record.get('expected_version') == VERSION) and (record.get('status') in {'PASS', 'REFUSED'}) and isinstance(record.get('visible_skills'), list) and isinstance(record.get('problems'), list), 'Codex skill-selection record is malformed')
		encoded = loads(canonical(record).decode())
		with self.transaction() as db:
			row = db.execute('SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Codex skill selection must precede dispatch begin')
			payload = loads(row['payload_json'])
			prior = payload.get('codex_skill_selection')
			_require(prior is None or prior == encoded, 'Codex skill selection changed for a durable dispatch intent')
			payload['codex_skill_selection'] = encoded
			db.execute('UPDATE controller_dispatch SET payload_json=?,updated_at=? WHERE dispatch_id=?', (canonical(payload).decode(), now, dispatch_id))
		return encoded

	def finish_dispatch(self, dispatch_id, receipt, now, *, reconcile=False):
		timestamp(now)
		bound = {'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'role', 'lifecycle_stage'}
		if reconcile:
			bound |= {'reconcile_nonce', 'reconcile_at'}
		required = bound | {'status', 'observed_at', 'session_id'}
		_require(isinstance(receipt, dict) and set(receipt) == required and (receipt['status'] == 'ACCEPTED'), 'Dispatch receipt is not an exact accepted readback')
		_require(isinstance(receipt['session_id'], str) and receipt['session_id'].strip() and (len(receipt['session_id']) <= 512) and (not any((ord(char) < 32 for char in receipt['session_id']))), 'Dispatch receipt has no observed session identity')
		observed_at = timestamp(receipt['observed_at'])
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			expected = 'UNKNOWN' if reconcile else 'IN_FLIGHT'
			_require(row is not None and row['status'] == expected, 'Dispatch receipt does not match the durable operation state')
			payload = loads(row['payload_json'])
			_require(all((receipt[key] == payload[key] for key in bound)), 'Dispatch receipt differs from the durable intent')
			boundary = timestamp(payload['reconcile_at'] if reconcile else payload['begun_at'])
			_require(observed_at >= boundary, 'Dispatch receipt is stale relative to the durable intent or reconciliation')
			for accepted in db.execute("SELECT dispatch_id,receipt_json FROM controller_dispatch WHERE status='ACCEPTED' AND dispatch_id<>?", (dispatch_id,)):
				prior = loads(accepted['receipt_json'])
				_require(prior.get('session_id') != receipt['session_id'], 'Dispatch session identity is not fresh or writer/critic independent')
			db.execute("UPDATE controller_dispatch SET status='ACCEPTED',receipt_json=?,updated_at=? WHERE dispatch_id=?", (canonical(receipt).decode(), now, dispatch_id))
			if reconcile:
				self._restore_unknown_stream(db, dispatch_id, payload, now)
				revision = int(self._meta(db, 'revision')) + 1
				db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return receipt

	def mark_dispatch_unknown(self, dispatch_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN'}, 'Only an in-flight or reconciling dispatch can report an unknown outcome')
			if row['status'] == 'IN_FLIGHT':
				db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?", (now, dispatch_id))
			self._record_dispatch_unknown(db, dispatch_id, loads(row['payload_json']), now)

	def _record_dispatch_unknown(self, db, dispatch_id, payload, now):
		stream = db.execute('SELECT * FROM stream_status WHERE stream_id=?', (payload['stream'],)).fetchone()
		_require(stream is not None, 'Unknown dispatch stream is missing from the protected state')
		if stream['ticket'] != payload['ticket'] or stream['exact_tuple'] != payload['exact_tuple']:
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return
		self._remember_unknown_stream(db, dispatch_id, self._row_restore_snapshot(stream), replace=False)
		db.execute("UPDATE stream_status SET state='BLOCKED',actor='controller',reason=?,next_action=?,resume_trigger=?,updated_at=? WHERE stream_id=?", (f"Dispatch outcome unknown for {payload['ticket']} ({dispatch_id}); reconcile before retry", 'Observe the exact durable dispatch intent before dispatching more work on this stream', 'the host returns a fresh observation for this dispatch and its ticket', now, payload['stream']))
		revision = int(self._meta(db, 'revision')) + 1
		db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))

	def begin_dispatch_reconciliation(self, dispatch_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] == 'UNKNOWN', 'Only an unknown dispatch can be reconciled')
			payload = loads(row['payload_json'])
			payload['reconcile_nonce'] = str(uuid.uuid4())
			payload['reconcile_at'] = now
			db.execute('UPDATE controller_dispatch SET payload_json=?,updated_at=? WHERE dispatch_id=?', (canonical(payload).decode(), now, dispatch_id))
			return {key: value for key, value in payload.items() if key != '_stream_before_unknown'}

	def recover_dispatches(self, now):
		timestamp(now)
		with self.transaction() as db:
			rows = db.execute("SELECT dispatch_id,payload_json FROM controller_dispatch WHERE status='IN_FLIGHT'").fetchall()
			for row in rows:
				db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?", (now, row['dispatch_id']))
				self._record_dispatch_unknown(db, row['dispatch_id'], loads(row['payload_json']), now)

	def prepare_jira_operation(self, record, now):
		timestamp(now)
		from .canonical import fingerprint
		_require(isinstance(record, dict) and isinstance(record.get('operation_id'), str) and isinstance(record.get('binding'), dict) and isinstance(record['binding'].get('issue_id'), str), 'Jira operation intent is malformed')
		immutable_intent = {key: value for key, value in record.items() if key != 'created_at'}
		intent_sha256 = fingerprint('controller-jira-operation', immutable_intent)
		encoded = canonical(record).decode()
		with self.transaction() as db:
			prior = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			if prior is None:
				db.execute('INSERT INTO controller_jira_operation VALUES(?,?,?,?,?,?,NULL,NULL,?)', (record['operation_id'], record['binding']['issue_id'], record['lifecycle_event'], intent_sha256, 'PENDING', encoded, now))
			else:
				_require(prior['intent_sha256'] == intent_sha256, 'Jira operation identity collided with a different durable intent')
			row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			return dict(row) | {'record': loads(row['record_json'])}

	def begin_jira_operation(self, operation_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Jira operation is not pending')
			db.execute("UPDATE controller_jira_operation SET status='IN_FLIGHT',updated_at=? WHERE operation_id=?", (now, operation_id))
			return loads(row['record_json'])

	def mark_jira_unknown(self, operation_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN'}, 'Only an in-flight or unknown Jira operation can remain unknown')
			db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE operation_id=?", (now, operation_id))

	def recover_jira_operations(self, now):
		timestamp(now)
		with self.transaction() as db:
			db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE status='IN_FLIGHT'", (now,))

	def jira_operations(self, issue_id):
		_require(isinstance(issue_id, str) and issue_id, 'Jira operation query needs an issue identity')
		with self.connection() as db:
			return [dict(row) | {'record': loads(row['record_json'])} for row in db.execute('SELECT * FROM controller_jira_operation WHERE issue_id=? ORDER BY rowid', (issue_id,))]

	def finish_jira_operation(self, operation_id, status, observation, now, operation=None):
		timestamp(now)
		_require(status in {'SUCCEEDED', 'FAILED', 'UNKNOWN'}, 'Invalid Jira operation result')
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN'}, 'Jira operation result does not match durable operation state')
			db.execute('UPDATE controller_jira_operation SET status=?,operation_json=?,readback_json=?,updated_at=? WHERE operation_id=?', (status, canonical(operation).decode() if operation is not None else row['operation_json'], canonical(observation).decode() if observation is not None else None, now, operation_id))
			return loads(row['record_json'])

def _zero_counts():
	return {'required': 0, 'completed': 0, 'acceptable': 0, 'failed': 0, 'stale': 0, 'outstanding': 0}

def post_merge_jira_progress(*, jira_enabled, merged_ticket, scope, observed_at, jira_binding, reconcile_merged_ticket, fetch_scope_page, count_gate, include_epics=False, max_pages=20, max_items=10000, max_bytes=16 * 1024 * 1024, max_seconds=60, clock=None):
	timestamp(observed_at)
	_require(isinstance(merged_ticket, str) and merged_ticket.strip() and isinstance(scope, str) and scope.strip(), 'Jira ticket and scoped query are required')
	_require(type(include_epics) is bool, 'Jira Epic inclusion must be an observed boolean')
	_require(isinstance(jira_binding, dict) and set(jira_binding) == {'cloud_id', 'project_id', 'actor_id'} and all((isinstance(value, str) and value for value in jira_binding.values())), 'Jira count needs immutable cloud, project, and actor bindings')
	_require(type(max_pages) is int and 0 < max_pages <= 100 and (type(max_items) is int) and (0 < max_items <= 100000) and (type(max_bytes) is int) and (0 < max_bytes <= 64 * 1024 * 1024) and isinstance(max_seconds, (int, float)) and (0 < max_seconds <= 300), 'Jira count bounds are invalid')
	from .canonical import fingerprint
	scope_sha256 = fingerprint('jira-progress-scope', {'scope': scope, 'binding': jira_binding, 'include_epics': include_epics})
	base = {'schema_version': 3, 'merged_ticket': merged_ticket, 'scope': scope, 'scope_sha256': scope_sha256, 'include_epics': include_epics, 'snapshot_id': None, 'observed_at': observed_at, 'closed': 'UNOBSERVED', 'remaining_open': 'UNOBSERVED'}
	_require(isinstance(count_gate, dict), 'Post-merge counting requires validated closeout gate inputs')

	def validate_count_gate():
		from .closeout import require_jira_reconciliation_before_counts
		gate = require_jira_reconciliation_before_counts(**count_gate)
		_require(gate.get('status') == 'PASS' and gate.get('count_allowed') is True, 'validated closeout did not permit post-merge counting')
		_require(gate.get('ticket') == merged_ticket, 'validated closeout ticket differs from the reconciled ticket')
		return gate
	if not jira_enabled:
		try:
			validate_count_gate()
		except Exception as exc:
			return {**base, 'jira_state': 'UNOBSERVED', 'reason': f'validated closeout count gate failed: {type(exc).__name__}'}
		return {**base, 'jira_state': 'JIRA_DISABLED', 'reason': 'accepted Jira is disabled; no Jira reads or writes attempted'}
	try:
		reconciled = reconcile_merged_ticket(merged_ticket)
	except Exception as exc:
		return {**base, 'jira_state': 'UNOBSERVED', 'reason': f'merge reconciliation failed: {type(exc).__name__}'}
	required_receipt = {'ticket', 'issue_id', 'cloud_id', 'project_id', 'actor_id', 'status', 'operation_id', 'before_status_id', 'after_status_id', 'observed_at'}
	if not isinstance(reconciled, dict) or set(reconciled) != required_receipt or reconciled.get('ticket') != merged_ticket or (reconciled.get('status') != 'RECONCILED') or any((reconciled.get(key) != jira_binding[key] for key in jira_binding)):
		return {**base, 'jira_state': 'UNOBSERVED', 'reason': 'merge reconciliation is incomplete, unknown, or mismatched'}
	try:
		uuid_value = __import__('uuid').UUID(reconciled['operation_id'])
		_require(str(uuid_value) == reconciled['operation_id'], 'Jira operation identity is not canonical')
		reconciled_at = timestamp(reconciled['observed_at'])
		_require(all((isinstance(reconciled[key], str) and reconciled[key] for key in ('issue_id', 'before_status_id', 'after_status_id'))), 'Jira reconciliation receipt lacks stable issue/status identity')
	except (ValidationError, ValueError, TypeError, AttributeError):
		return {**base, 'jira_state': 'UNOBSERVED', 'reason': 'merge reconciliation receipt is malformed'}
	base['observed_at'] = reconciled['observed_at']
	try:
		validate_count_gate()
	except Exception as exc:
		return {**base, 'jira_state': 'UNOBSERVED', 'reason': f'validated closeout count gate failed: {type(exc).__name__}'}
	items, identities, cursor, seen_cursors = ([], set(), None, set())
	snapshot_id = page_observed_at = None
	import time
	timer = clock or time.monotonic
	started = timer()
	pages = total_bytes = 0
	try:
		while True:
			pages += 1
			if pages > max_pages or timer() - started > max_seconds:
				raise ValidationError('Jira pagination exceeded its request or time bound')
			page = fetch_scope_page(scope, cursor)
			if timer() - started > max_seconds:
				raise ValidationError('Jira pagination exceeded its request or time bound')
			total_bytes += len(canonical(page))
			if total_bytes > max_bytes:
				raise ValidationError('Jira pagination exceeded its byte bound')
			required_page = {'items', 'next_cursor', 'complete', 'snapshot_id', 'scope_sha256', 'observed_at'}
			if not isinstance(page, dict) or set(page) != required_page:
				raise ValidationError('Malformed Jira scope page')
			if page['scope_sha256'] != scope_sha256 or not isinstance(page['snapshot_id'], str) or (not page['snapshot_id']):
				raise ValidationError('Jira page has wrong scope or no stable snapshot')
			if snapshot_id is None:
				snapshot_id = page['snapshot_id']
				base['snapshot_id'] = snapshot_id
			if page['snapshot_id'] != snapshot_id:
				raise ValidationError('Jira snapshot changed during pagination')
			observed_time = timestamp(page['observed_at'])
			if observed_time < reconciled_at:
				raise ValidationError('Jira count snapshot predates merge reconciliation')
			if page_observed_at is None:
				page_observed_at = page['observed_at']
			if page['observed_at'] != page_observed_at:
				raise ValidationError('Jira page observation time changed during pagination')
			if not isinstance(page['items'], list):
				raise ValidationError('Malformed Jira scope items')
			for item in page['items']:
				if not isinstance(item, dict) or set(item) != {'id', 'issue_type', 'status_category'}:
					raise ValidationError('Malformed Jira scope item')
				if item['id'] in identities or item['status_category'] not in {'TERMINAL', 'NON_TERMINAL'}:
					raise ValidationError('Duplicate ticket or unknown terminal status category')
				identities.add(item['id'])
				if include_epics or item['issue_type'] != 'EPIC':
					items.append(item)
				if len(identities) > max_items:
					raise ValidationError('Jira scope exceeded its item bound')
			following = page['next_cursor']
			if following is None:
				if page['complete'] is not True:
					raise ValidationError('Final Jira page did not prove query completeness')
				base['observed_at'] = page['observed_at']
				break
			if page['complete'] is not False:
				raise ValidationError('Non-final Jira page made a contradictory completeness claim')
			if not isinstance(following, str) or not following:
				raise ValidationError('Jira pagination cursor is malformed')
			if following == cursor or following in seen_cursors:
				raise ValidationError('Jira pagination cursor did not advance')
			seen_cursors.add(following)
			cursor = following
	except Exception as exc:
		return {**base, 'jira_state': 'RECONCILED', 'reason': f'scoped Jira count is incomplete: {type(exc).__name__}'}
	return {**base, 'jira_state': 'COUNTED', 'closed': sum((i['status_category'] == 'TERMINAL' for i in items)), 'remaining_open': sum((i['status_category'] == 'NON_TERMINAL' for i in items)), 'reason': 'authoritative complete scoped Jira observation'}

def _observe_controller_inventory(observe_inventory, *, now, inventory_binding, repository_root, repository_head_sha, repository_tree_sha):
	parameters = inspect.signature(observe_inventory).parameters
	accepts_time = 'now' in parameters or 'controller_now' in parameters or any((parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD) and parameter.default is inspect.Parameter.empty for parameter in parameters.values()))
	observation = observe_inventory(now) if accepts_time else observe_inventory()
	_require(isinstance(observation, dict) and set(observation) == {'source', 'observed_at', 'binding', 'complete', 'inventory_sha256', 'tickets'} and (observation['source'] == 'host_observation') and (observation['complete'] is True), 'Production inventory must be an exact authenticated host observation')
	timestamp(observation['observed_at'])
	_require(observation['observed_at'] == now, 'Production inventory observation time differs from this controller cycle')
	from .canonical import fingerprint
	_require(observation['binding'] == inventory_binding and observation['inventory_sha256'] == fingerprint('controller-inventory', {'binding': inventory_binding, 'tickets': observation['tickets']}), 'Production inventory identity or digest differs from the configured scope')
	tickets, repository_binding = canonical_repository_paths(observation['tickets'], repository_root, repository_head_sha, repository_tree_sha)
	return (tickets, repository_binding, observation)

def production_controller_cycle(store, *, now, host_capacity, inventory_binding, repository_root, repository_head_sha, repository_tree_sha, observe_inventory, dispatch_ticket, observe_dispatch, deliver_status, publication_config=None, observe_publication=None, dispatch_role=None, cycle_id=None, overlap_allowance_requests=None, observe_overlap_allowance=None, observe_overlap_conflict=None, sync_writer=None, observe_dispatch_quiescence=None, observe_overlap_sync_inventory=None):
	_require(isinstance(store, ContinuousControllerStore), 'Production cycle needs a controller store')
	_require(all((callable(adapter) for adapter in (observe_inventory, dispatch_ticket, observe_dispatch, deliver_status))), 'Production controller adapters must be callable')
	timestamp(now)
	_require(dispatch_role is None or dispatch_role in {'writer', 'critic'}, 'Compatibility dispatch role must be writer or critic')
	store.recover_dispatches(now)
	_require(isinstance(inventory_binding, dict) and set(inventory_binding) == {'project_id', 'repository_id', 'scope_sha256'} and all((isinstance(value, str) and value for value in inventory_binding.values())), 'Production inventory needs immutable project, repository, and scope bindings')
	tickets, repository_binding, observation = _observe_controller_inventory(observe_inventory, now=now, inventory_binding=inventory_binding, repository_root=repository_root, repository_head_sha=repository_head_sha, repository_tree_sha=repository_tree_sha)
	store.bind_repository(repository_binding)
	from .publication_readiness import require_publication_readiness
	publication_reports, errors = ({}, [])
	allowance_plans, allowance_audit, allowance_blocked_tickets = ([], [], set())
	requests = overlap_allowance_requests or []
	_require(isinstance(requests, list), 'Overlap allowance requests must be a list')
	if requests:
		_require(isinstance(cycle_id, str) and cycle_id.strip() and callable(observe_overlap_allowance) and isinstance(publication_config, dict), 'Overlap allowances require a cycle, reviewed configuration, and provider observer')
	for raw_request in requests:
		named_tickets = []
		if isinstance(raw_request, dict) and isinstance(raw_request.get('pull_requests'), list):
			named_tickets = [item.get('ticket') for item in raw_request['pull_requests'] if isinstance(item, dict) and isinstance(item.get('ticket'), str)]
		try:
			request = _allowance_request(raw_request, inventory_binding['repository_id'], cycle_id)
			plan_observation = observe_overlap_allowance(deepcopy(request), 'PLAN')
			record = validate_overlap_allowance_observation(publication_config, request, plan_observation, tickets, now=now, expected_repository_id=inventory_binding['repository_id'], expected_cycle_id=cycle_id, allow_expired=True)
			store.register_overlap_allowance(record, now)
			if timestamp(now) >= timestamp(record['expires_at']):
				gate = store.expire_overlap_allowance(record['allowance_id'], now)
				allowance_audit.append({'allowance_id': record['allowance_id'], 'phase': 'EXPIRED', 'status': gate['status'], 'gate_id': gate['gate_id']})
			else:
				allowance_plans.append((request, record))
				allowance_audit.append({'allowance_id': record['allowance_id'], 'phase': 'PLAN', 'status': 'ACTIVE'})
		except Exception as exc:
			allowance_blocked_tickets.update(named_tickets)
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			allowance_audit.append({'allowance_id': None, 'phase': 'PLAN', 'status': 'REFUSED', 'reason': reason})
			errors.append({'operation': 'overlap_allowance', 'state': 'BLOCKED', 'reason': reason})
	for gate in store.expire_due_overlap_allowances(now):
		allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SWEEP_EXPIRED', 'status': gate['status'], 'gate_id': gate['gate_id']})
	unresolved = store.unresolved_overlap_sync_gates()
	already_synced = [gate for gate in unresolved if gate['status'] == 'SYNCED']
	newly_synced = []
	for gate in unresolved:
		if gate['status'] == 'SYNCED':
			continue
		if gate['status'] in {'IN_FLIGHT', 'UNKNOWN'}:
			allowance_blocked_tickets.update(gate['tickets'])
			reason = 'Overlap synchronization executor outcome is unresolved'
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_DECISION', 'status': gate['status'], 'gate_id': gate['gate_id'], 'reason': reason})
			errors.append({'operation': 'overlap_sync', 'state': 'BLOCKED', 'reason': reason})
			continue
		try:
			quiescence = store.overlap_sync_quiescence(gate['gate_id'])
			for pending in quiescence['observations']:
				_require(callable(observe_dispatch_quiescence), 'Expired overlap allowance needs terminal proof for accepted dispatches')
				terminal = observe_dispatch_quiescence(deepcopy(pending['payload']))
				store.confirm_dispatch_quiescence(pending['dispatch_id'], terminal, now)
			quiescence = store.overlap_sync_quiescence(gate['gate_id'])
			_require(quiescence['ready'], 'Expired overlap allowance is waiting for both bound lanes to become quiescent: ' + canonical(quiescence['blockers']).decode())
			_require(callable(observe_overlap_conflict), 'Expired overlap allowance needs a conflict observer')
			decision = observe_overlap_conflict(deepcopy(gate))
			decided = store.execute_overlap_sync_gate(gate['gate_id'], decision, sync_writer, now)
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_DECISION', 'status': decided['status'], 'gate_id': gate['gate_id']})
			if decided['status'] == 'SYNCED':
				newly_synced.append(decided)
			elif decided['status'] not in {'CLEARED', 'EXECUTED'}:
				allowance_blocked_tickets.update(gate['tickets'])
		except Exception as exc:
			allowance_blocked_tickets.update(gate['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_DECISION', 'status': 'REFUSED', 'gate_id': gate['gate_id'], 'reason': reason})
			errors.append({'operation': 'overlap_sync', 'state': 'BLOCKED', 'reason': reason})
	for gate in already_synced:
		try:
			_require(callable(observe_overlap_sync_inventory), 'Post-sync inventory requires a fresh provider PR-head observer')
			provider_heads = observe_overlap_sync_inventory(deepcopy(gate))
			completed = store.confirm_overlap_sync_inventory(gate['gate_id'], tickets, inventory_sha256=observation['inventory_sha256'], observed_at=observation['observed_at'], provider_observation=provider_heads)
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'POST_SYNC_INVENTORY', 'status': completed['status'], 'gate_id': gate['gate_id']})
		except Exception as exc:
			allowance_blocked_tickets.update(gate['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			errors.append({'operation': 'overlap_sync_inventory', 'state': 'BLOCKED', 'reason': reason})
	if newly_synced:
		try:
			refreshed_tickets, refreshed_binding, refreshed_observation = _observe_controller_inventory(observe_inventory, now=now, inventory_binding=inventory_binding, repository_root=repository_root, repository_head_sha=repository_head_sha, repository_tree_sha=repository_tree_sha)
			store.bind_repository(refreshed_binding)
			tickets = refreshed_tickets
			observation = refreshed_observation
			for gate in newly_synced:
				_require(callable(observe_overlap_sync_inventory), 'Post-sync inventory requires a fresh provider PR-head observer')
				provider_heads = observe_overlap_sync_inventory(deepcopy(gate))
				completed = store.confirm_overlap_sync_inventory(gate['gate_id'], tickets, inventory_sha256=observation['inventory_sha256'], observed_at=observation['observed_at'], provider_observation=provider_heads)
				allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'POST_SYNC_INVENTORY', 'status': completed['status'], 'gate_id': gate['gate_id']})
		except Exception as exc:
			for gate in newly_synced:
				allowance_blocked_tickets.update(gate['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			errors.append({'operation': 'overlap_sync_inventory', 'state': 'BLOCKED', 'reason': reason})
	for index, item in enumerate(tickets):
		if item['disposition'] != 'ELIGIBLE':
			continue
		try:
			_require(callable(observe_publication) and isinstance(publication_config, dict), 'PUBLICATION_UNOBSERVED: a reviewed publication observer and accepted configuration are required')
			_require(str(publication_config.get('github', {}).get('repository_id')) == inventory_binding['repository_id'], 'Publication repository differs from the controller inventory binding')
			publication_reports[item['ticket']] = require_publication_readiness(publication_config, observe_publication(loads(canonical(item).decode())), ticket=item['ticket'], now=now)
		except Exception as exc:
			reason = str(exc) if isinstance(exc, ValidationError) else 'PUBLICATION_UNOBSERVED: ' + type(exc).__name__
			tickets[index] = {**item, 'disposition': 'BLOCKED', 'reason': reason, 'next_action': 'Resolve the named publication blocker and obtain a fresh observation', 'resume_trigger': 'the exact completion path passes publication readiness'}
			errors.append({'operation': 'publication_readiness', 'ticket': item['ticket'], 'state': 'BLOCKED', 'reason': reason})
	active_allowances = []
	for request, planned in allowance_plans:
		try:
			lane_observation = observe_overlap_allowance(deepcopy(request), 'LANE_START')
			current = validate_overlap_allowance_observation(publication_config, request, lane_observation, tickets, now=now, expected_repository_id=inventory_binding['repository_id'], expected_cycle_id=cycle_id)
			store.recheck_overlap_allowance(planned['allowance_id'], current, now)
			active_allowances.append({'allowance_id': current['allowance_id'], 'path': current['binding']['path'], 'tickets': current['binding']['tickets']})
			allowance_audit.append({'allowance_id': current['allowance_id'], 'phase': 'LANE_START', 'status': 'ACTIVE'})
		except Exception as exc:
			allowance_blocked_tickets.update(planned['binding']['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			allowance_audit.append({'allowance_id': planned['allowance_id'], 'phase': 'LANE_START', 'status': 'REFUSED', 'reason': reason})
			errors.append({'operation': 'overlap_allowance', 'state': 'BLOCKED', 'reason': reason})
	if allowance_blocked_tickets:
		for index, item in enumerate(tickets):
			if item['ticket'] in allowance_blocked_tickets:
				tickets[index] = {**item, 'disposition': 'BLOCKED', 'reason': 'Overlap allowance or post-expiry synchronization gate is not current', 'next_action': 'Restore exact provider-bound allowance or complete its durable sync gate', 'resume_trigger': 'a fresh plan and lane-start recheck pass'}
	ticket_by_id = {item['ticket']: item for item in tickets}
	for operation in store.pending_dispatches():
		payload = operation['payload']
		current = ticket_by_id.get(payload['ticket'])
		if current is not None and current['disposition'] == 'ELIGIBLE' and (payload['ticket'] in publication_reports):
			continue
		if current is None:
			reason = 'Fresh complete inventory no longer contains the pending ticket'
		elif current['disposition'] != 'ELIGIBLE':
			reason = f"Fresh preflight disposition is {current['disposition']}: {current['reason']}"
		else:
			reason = 'Fresh publication preflight did not admit the pending ticket'
		try:
			store.cancel_pending_dispatch(operation['dispatch_id'], now, reason)
		except Exception as exc:
			errors.append({'operation': 'dispatch_cancellation', 'dispatch_id': operation['dispatch_id'], 'state': 'PENDING', 'reason': type(exc).__name__})
	streams = store.schedule(tickets, now, host_capacity, active_allowances)
	dispatches = []
	for operation in store.prepare_dispatches(now):
		payload, dispatch_id = (operation['payload'], operation['dispatch_id'])
		if operation['status'] == 'PENDING':
			admitted = payload['ticket'] in publication_reports and any((stream['stream'] == payload['stream'] and stream['state'] == 'WORKING' and (stream['ticket'] == payload['ticket']) and (stream['exact_tuple'] == payload['exact_tuple']) for stream in streams))
			if not admitted:
				current = next((item for item in tickets if item['ticket'] == payload['ticket']), None)
				if current is None:
					reason = 'Fresh complete inventory no longer contains the pending ticket'
				elif current['disposition'] != 'ELIGIBLE':
					reason = f"Fresh preflight disposition is {current['disposition']}: {current['reason']}"
				elif payload['ticket'] not in publication_reports:
					reason = 'Fresh publication preflight did not admit the pending ticket'
				else:
					reason = 'Fresh schedule did not retain this exact stream, ticket, and tuple'
				try:
					store.cancel_pending_dispatch(dispatch_id, now, reason)
				except Exception as exc:
					errors.append({'operation': 'dispatch_cancellation', 'dispatch_id': dispatch_id, 'state': 'PENDING', 'reason': type(exc).__name__})
				continue
		try:
			if operation['status'] == 'PENDING':
				from .codex_skill import observe_codex_awf_skills
				launch_directory = store.worktree_roots[0] if store.worktree_roots else repository_root
				launch_environment = dict(os.environ)
				selection = observe_codex_awf_skills(launch_directory, launch_directory, launch_environment)
				store.record_dispatch_skill_selection(dispatch_id, selection, now)
				if selection['status'] != 'PASS':
					reason = 'Codex AWF skill selection refused: ' + '; '.join(selection['problems'])
					store.cancel_pending_dispatch(dispatch_id, now, reason)
					errors.append({'operation': 'dispatch', 'dispatch_id': dispatch_id, 'state': 'REFUSED', 'reason': reason})
					continue
				payload = store.begin_dispatch(dispatch_id, now)
				role = payload.get('role')
				lifecycle_stage = payload.get('lifecycle_stage')
				if role is None or lifecycle_stage is None:
					_require(dispatch_role is not None, 'Dispatch intent is missing per-ticket role authority')
					role = dispatch_role
					lifecycle_stage = 'implementation' if role == 'writer' else 'independent_review'
				_require(dispatch_role is None or dispatch_role == role, 'Cycle-wide compatibility role conflicts with per-ticket authority')
				receipt = dispatch_ticket({**payload, 'role': role, 'lifecycle_stage': lifecycle_stage, 'worktree': str(launch_directory)})
				dispatches.append(store.finish_dispatch(dispatch_id, receipt, now))
			else:
				payload = store.begin_dispatch_reconciliation(dispatch_id, now)
				receipt = observe_dispatch(payload)
				dispatches.append(store.finish_dispatch(dispatch_id, receipt, now, reconcile=True))
		except Exception as exc:
			try:
				store.mark_dispatch_unknown(dispatch_id, now)
			except Exception:
				pass
			errors.append({'operation': 'dispatch', 'dispatch_id': dispatch_id, 'state': 'UNKNOWN', 'reason': type(exc).__name__})
	digest = store.digest(now)
	delivery = None
	if digest is not None:
		try:
			receipt = deliver_status(digest)
			_require(isinstance(receipt, dict) and set(receipt) == {'delivery_id', 'status', 'observed_at'} and (receipt['delivery_id'] == digest['delivery_id']) and (receipt['status'] == 'DELIVERED'), 'Status delivery readback is missing or mismatched')
			timestamp(receipt['observed_at'])
			store.acknowledge_digest(digest['delivery_id'], receipt['observed_at'])
			delivery = receipt
		except Exception as exc:
			errors.append({'operation': 'status_delivery', 'delivery_id': digest['delivery_id'], 'state': 'PENDING', 'reason': type(exc).__name__})
	streams = store.snapshot()
	return {'schema_version': 3, 'observed_at': now, 'streams': streams, 'dispatch_receipts': dispatches, 'status_delivery': delivery, 'publication_readiness': publication_reports, 'overlap_allowances': allowance_audit, 'errors': errors, 'execution_authority': False}

def production_jira_lifecycle(store, *, config, contract, event, facts, binding, issue_type, state, producer_id, run_id, now, evidence, transition_id, read_current_status, write_transition, read_transition, observe_provider_identity=None, merge_result_id=None):
	_require(isinstance(store, ContinuousControllerStore), 'Jira lifecycle production needs the protected controller store')
	_require(all((callable(adapter) for adapter in (read_current_status, write_transition, read_transition))), 'Jira lifecycle read/write/readback adapters must be callable')
	if config.get('jira', {}).get('enabled', True) is False:
		return {'planned': False, 'reason': 'Jira is disabled; no reads or writes', 'execution_authority': False}
	from .provider_identity import ProviderIdentityError, expected_jira_identity, require_jira_write_identity
	expected = {'cloud_id': config.get('jira', {}).get('cloud_id'), 'site': config.get('jira', {}).get('site'), 'project_id': config.get('jira', {}).get('provider_project_id'), 'project_key': config.get('jira', {}).get('project_key'), 'account_id': config.get('jira', {}).get('controller_actor_id')}
	observed_identity = None
	try:
		expected_jira_identity(config)
		if not callable(observe_provider_identity):
			raise ProviderIdentityError('IDENTITY_UNOBSERVED', 'Jira provider identity adapter is unavailable', expected=expected)
		observed_identity = observe_provider_identity()
		provider = require_jira_write_identity(config, observed_identity)
		if producer_id != provider['controller_actor_id']:
			raise ProviderIdentityError('IDENTITY_MISMATCH', 'Jira lifecycle producer differs from the configured controller actor', expected=expected, observed=observed_identity)
		_require(isinstance(binding, dict) and isinstance(binding.get('issue_id'), str) and binding['issue_id'], 'Jira lifecycle needs an immutable issue binding')
	except ProviderIdentityError as exc:
		return {'planned': False, 'status': exc.code, 'reason': str(exc), 'expected_identity': exc.expected or expected, 'observed_identity': exc.observed if exc.observed is not None else observed_identity, 'issue_lookups': 0, 'writes_stopped': True, 'reads_allowed': True, 'execution_authority': False}
	except Exception as exc:
		return {'planned': False, 'status': 'IDENTITY_UNOBSERVED', 'reason': 'Jira provider identity observation failed: ' + type(exc).__name__, 'expected_identity': expected, 'observed_identity': None, 'issue_lookups': 0, 'writes_stopped': True, 'reads_allowed': True, 'execution_authority': False}
	adapter_binding = {'issue_id': binding['issue_id'], 'jira_provider': provider}
	store.recover_jira_operations(now)
	start_time = timestamp(now)
	try:
		current = read_current_status(adapter_binding)
		_require(isinstance(current, dict) and set(current) == {'issue_id', 'status_id', 'observed_at', 'jira_provider'} and (current['issue_id'] == binding['issue_id']) and (current['jira_provider'] == provider) and isinstance(current['status_id'], str) and current['status_id'], 'Jira pre-write observation is missing or mismatched')
		current_time = timestamp(current['observed_at'])
		_require(current_time >= start_time, 'Jira pre-write observation predates the lifecycle operation')
	except Exception as exc:
		return {'planned': False, 'reason': f'Jira pre-write observation failed: {type(exc).__name__}', 'writes_stopped': True, 'execution_authority': False}
	from .jira_lifecycle import planned_write, transition_record, apply_read_back
	durable = store.jira_operations(binding['issue_id'])
	for operation_row in durable:
		_require(operation_row['record'].get('binding') == binding and operation_row['record'].get('jira_provider') == provider, 'Durable Jira intent belongs to a different connector/site/project/actor')
		if operation_row['status'] == 'FAILED':
			return {'planned': False, 'reason': 'A durable Jira operation failed; writes remain stopped', 'writes_stopped': True, 'operation_id': operation_row['operation_id'], 'current_observation': current, 'execution_authority': False}
		if operation_row['status'] != 'UNKNOWN':
			continue
		record = operation_row['record']
		if current['status_id'] == record['to_status_id']:
			store.finish_jira_operation(record['operation_id'], 'SUCCEEDED', current, current['observed_at'])
			continue
		store.finish_jira_operation(record['operation_id'], 'UNKNOWN', current, current['observed_at'])
		return {'planned': False, 'reason': 'Unknown durable Jira operation was not proven successful by readback; writes remain stopped', 'writes_stopped': True, 'operation_id': record['operation_id'], 'current_observation': current, 'execution_authority': False}
	durable = store.jira_operations(binding['issue_id'])
	prior_writes = []
	for operation_row in durable:
		record = dict(operation_row['record'])
		record['status'] = operation_row['status']
		if operation_row['readback_json']:
			observed = loads(operation_row['readback_json'])
			record['read_back'] = {'observed_status': observed.get('status_id', observed.get('status')), 'observed_actor': observed.get('actor'), 'observed_at': observed.get('observed_at')}
		prior_writes.append(record)
	key, target = planned_write(config, contract, event, facts, issue_type=issue_type, current_status=current['status_id'], prior_writes=prior_writes, state=state)
	if key is None:
		return {'planned': False, 'reason': target, 'current_observation': current, 'execution_authority': False}
	record = transition_record(binding, event, current['status_id'], target, transition_id, jira_provider=provider, merge_result_id=merge_result_id, producer_id=producer_id, run_id=run_id, now=now, evidence=evidence)
	intent = store.prepare_jira_operation(record, now)
	record = intent['record']
	if intent['status'] == 'SUCCEEDED':
		return {'planned': False, 'reason': 'Durable Jira operation already succeeded', 'operation_id': record['operation_id'], 'current_observation': current, 'execution_authority': False}
	_require(intent['status'] == 'PENDING', 'Jira operation is unresolved; writes remain stopped')
	try:
		latest_identity = observe_provider_identity()
		latest_provider = require_jira_write_identity(config, latest_identity)
		if latest_provider != provider:
			raise ProviderIdentityError('IDENTITY_MISMATCH', 'Jira provider identity moved after the issue pre-read', expected=expected, observed=latest_identity)
	except ProviderIdentityError as exc:
		return {'planned': False, 'status': exc.code, 'reason': str(exc), 'expected_identity': exc.expected or expected, 'observed_identity': exc.observed, 'current_observation': current, 'writes_stopped': True, 'execution_authority': False}
	except Exception as exc:
		return {'planned': False, 'status': 'IDENTITY_UNOBSERVED', 'reason': 'Jira provider identity recheck failed: ' + type(exc).__name__, 'expected_identity': expected, 'observed_identity': None, 'current_observation': current, 'writes_stopped': True, 'execution_authority': False}
	store.begin_jira_operation(record['operation_id'], now)
	try:
		operation = write_transition(record)
		_require(isinstance(operation, dict) and set(operation) == {'operation_id', 'issue_id', 'status', 'observed_at', 'jira_provider'} and (operation['operation_id'] == record['operation_id']) and (operation['issue_id'] == binding['issue_id']) and (operation['jira_provider'] == provider) and (operation['status'] == 'ATTEMPTED'), 'Jira write receipt is missing or mismatched')
		operation_time = timestamp(operation['observed_at'])
		_require(operation_time >= current_time, 'Jira write receipt predates the pre-write observation')
		observation = read_transition(record, operation)
		_require(isinstance(observation, dict) and set(observation) == {'issue_id', 'status', 'actor', 'observed_at', 'jira_provider'} and (observation['issue_id'] == binding['issue_id']) and (observation['jira_provider'] == provider) and (observation['actor'] == provider['controller_actor_id']), 'Jira readback has an invalid or mismatched shape')
		readback_time = timestamp(observation['observed_at'])
		_require(readback_time >= operation_time, 'Jira readback predates the write receipt')
		result = apply_read_back(record, observation['status'], observation['actor'], observation['observed_at'])
		store.finish_jira_operation(record['operation_id'], result['record']['status'], observation, observation['observed_at'], operation)
		return {'planned': True, 'current_observation': current, **result, 'execution_authority': False}
	except Exception as exc:
		try:
			store.mark_jira_unknown(record['operation_id'], now)
		except Exception:
			pass
		return {'planned': True, 'current_observation': current, **apply_read_back(record, None), 'reason': type(exc).__name__, 'execution_authority': False}

def production_post_merge_progress(**adapters):
	_require(isinstance(adapters.get('count_gate'), dict), 'Production post-merge counting requires validated closeout gate inputs')
	return post_merge_jira_progress(**adapters)

def production_merge_observed(*, lifecycle_state, lifecycle_facts, jira_progress):
	_require(lifecycle_state in {'MERGING', 'MERGE_UNKNOWN'}, 'Production merge observation needs a merge-in-progress state')
	_require(isinstance(lifecycle_facts, dict) and isinstance(jira_progress, dict), 'Production merge observation needs lifecycle facts and Jira adapters')
	_require(isinstance(jira_progress.get('count_gate'), dict), 'Production merge observation requires validated closeout gate inputs')
	from .lifecycle import transition
	state = transition(lifecycle_state, 'MERGE_OBSERVED', lifecycle_facts)
	progress = production_post_merge_progress(**jira_progress)
	return {'schema_version': 3, 'state': state, 'jira_progress': progress, 'execution_authority': False}
