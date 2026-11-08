from __future__ import annotations
import hashlib
import inspect
import json
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
from .canonical import MAX_DOCUMENT_BYTES, _pairs, canonical, loads, now_text, timestamp, validate_value
from .controller_state import STATE_SCHEMA_VERSION, configure_database, protected_state_path, restrict_state_permissions
from .reporting.policy import policy_cycle
STREAM_STATES = {'WORKING', 'PAUSED_INPUT', 'BLOCKED', 'COMPLETE'}
TICKET_STATES = {'ELIGIBLE', 'PAUSED_INPUT', 'BLOCKED', 'COMPLETE'}
LIFECYCLE_ROLES = {'implementation': 'writer', 'independent_review': 'critic', 'amendment': 'writer'}
DEFAULT_STATUS_CADENCE_SECONDS = 10 * 60
STATE_APPLICATION_ID = 1096238641
DISPATCH_SCHEMA_VERSION = 2
REPLAN_TRIGGER_SCHEMA_VERSION = 2
PRODUCTION_PLANNING_MAX_BYTES = 4 * 1024 * 1024
PRODUCTION_PLANNING_MAX_EVENTS = 4000
PRODUCTION_PLANNING_RECOVERY_MAX_BYTES = 16 * 1024 * 1024
PRODUCTION_PLANNING_SEEN_RETENTION = 4096
PRODUCTION_PLANNING_SCHEMA_VERSION = 2
DDL = '\nCREATE TABLE IF NOT EXISTS controller_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS stream_status(\n stream_id TEXT PRIMARY KEY,state TEXT NOT NULL,ticket TEXT,actor TEXT NOT NULL,\n reason TEXT NOT NULL,next_action TEXT NOT NULL,resume_trigger TEXT NOT NULL,\n exact_tuple TEXT NOT NULL,activity TEXT NOT NULL,verification_gate TEXT NOT NULL,\n reviewer_completion TEXT NOT NULL,open_findings INTEGER NOT NULL,\n jira_status TEXT NOT NULL,paths TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_outbox(\n delivery_id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,\n observed_at TEXT NOT NULL,payload_json TEXT NOT NULL,acknowledged_at TEXT);\nCREATE TABLE IF NOT EXISTS controller_dispatch(\n dispatch_id TEXT PRIMARY KEY,stream_id TEXT NOT NULL,ticket TEXT NOT NULL,\n exact_tuple TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,\n receipt_json TEXT,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_dispatch_waiver(\n waiver_id TEXT PRIMARY KEY,dispatch_id TEXT NOT NULL,allowance_id TEXT NOT NULL,\n record_json TEXT NOT NULL,created_at TEXT NOT NULL,\n UNIQUE(dispatch_id,allowance_id));\nCREATE TABLE IF NOT EXISTS controller_replan_seal(\n seal_id TEXT PRIMARY KEY,trigger_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL,\n record_json TEXT NOT NULL,created_at TEXT NOT NULL,consumed_at TEXT);\nCREATE TABLE IF NOT EXISTS controller_ticket_authority(\n ticket TEXT NOT NULL,exact_tuple TEXT NOT NULL,dispatch_role TEXT NOT NULL,\n lifecycle_stage TEXT NOT NULL,observed_at TEXT NOT NULL,\n PRIMARY KEY(ticket,exact_tuple));\nCREATE TABLE IF NOT EXISTS controller_overlap_allowance(\n allowance_id TEXT PRIMARY KEY,source_id TEXT NOT NULL UNIQUE,repository_id TEXT NOT NULL,\n cycle_id TEXT NOT NULL,path TEXT NOT NULL,tickets_json TEXT NOT NULL,\n pull_requests_json TEXT NOT NULL,heads_json TEXT NOT NULL,expires_at TEXT NOT NULL,\n status TEXT NOT NULL,record_json TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS controller_overlap_sync_gate(\n gate_id TEXT PRIMARY KEY,allowance_id TEXT NOT NULL,status TEXT NOT NULL,\n record_json TEXT NOT NULL,updated_at TEXT NOT NULL);\nCREATE INDEX IF NOT EXISTS controller_overlap_gate_allowance\n ON controller_overlap_sync_gate(allowance_id,status);\nCREATE TABLE IF NOT EXISTS controller_jira_operation(\n operation_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,event TEXT NOT NULL,\n intent_sha256 TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,\n operation_json TEXT,readback_json TEXT,updated_at TEXT NOT NULL);\nCREATE INDEX IF NOT EXISTS controller_jira_issue ON controller_jira_operation(issue_id,status);\n'

BATCH_JIRA_DDL = '''CREATE TABLE IF NOT EXISTS controller_batch_jira_operation(
 operation_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,event TEXT NOT NULL,
 intent_sha256 TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,
 operation_json TEXT,readback_json TEXT,updated_at TEXT NOT NULL)'''

def _stopped_batch_jira_operation(db, issue_id):
	# Older controller stores need not have run T11 or created its separate table.
	if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controller_batch_jira_operation'").fetchone() is None:
		return None
	return db.execute("SELECT * FROM controller_batch_jira_operation WHERE issue_id=? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED') ORDER BY rowid LIMIT 1", (issue_id,)).fetchone()

# Complete statements: trigger bodies must never enter the semicolon-split DDL.
BATCH_AUDIT_DDL = (
	'''CREATE TABLE IF NOT EXISTS controller_batch_audit(
	 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
	 repository_id INTEGER NOT NULL, batch_id TEXT NOT NULL,
	 record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
	 UNIQUE(repository_id,batch_id))''',
	'''CREATE TRIGGER IF NOT EXISTS controller_batch_audit_no_update
	 BEFORE UPDATE ON controller_batch_audit BEGIN
	 SELECT RAISE(ABORT, 'Batch audit records are immutable'); END''',
	'''CREATE TRIGGER IF NOT EXISTS controller_batch_audit_no_delete
	 BEFORE DELETE ON controller_batch_audit BEGIN
	 SELECT RAISE(ABORT, 'Batch audit records are immutable'); END''',
	# REPLACE deletes do not reliably fire delete triggers without recursive_triggers.
	# Guard both uniqueness surfaces before SQLite can perform that deletion.
	'''CREATE TRIGGER IF NOT EXISTS controller_batch_audit_no_replace
	 BEFORE INSERT ON controller_batch_audit
	 WHEN EXISTS(SELECT 1 FROM controller_batch_audit
	 WHERE (repository_id=NEW.repository_id AND batch_id=NEW.batch_id)
	 OR sequence=NEW.sequence) BEGIN
	 SELECT RAISE(ABORT, 'Batch audit identity already exists'); END''',
)


class JiraTransitionNotAttempted(ValidationError):

	"""Reviewed adapter proof: this invocation failed before invoking the POST."""


def _require(condition, message):
	if not condition:
		raise ValidationError(message)

def _planning_state(state, *, migrate=False):
	"""Validate retention metadata without discarding unfamiliar planning fields."""
	from .planning import RECOMPUTE_TRIGGERS, verify_plan_record
	_require(isinstance(state, dict) and {'pending', 'seen', 'overrides', 'candidate', 'observation'} <= set(state), 'Malformed production planning state')
	version = state.get('schema_version', 1)
	_require(type(version) is int and version in (1, PRODUCTION_PLANNING_SCHEMA_VERSION), 'Unsupported production planning format version')
	sequence = state.get('cycle_sequence', 0) if version == 1 else state.get('cycle_sequence')
	_require(type(sequence) is int and 0 <= sequence <= 2**53 - 1, 'Invalid production planning cycle sequence')
	_require(isinstance(state['seen'], dict) and isinstance(state['pending'], list) and isinstance(state['overrides'], dict), 'Malformed production planning collections')
	_require(state['candidate'] is None or isinstance(state['candidate'], dict), 'Malformed retained planning candidate')
	if state['candidate'] is not None:
		verify_plan_record(state['candidate'])
	for key in ('observation', 'candidate_observation'):
		value = state.get(key)
		_require(value is None or isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, 'Malformed planning observation')
	if 'candidate_source_ids' in state:
		_require(isinstance(state['candidate_source_ids'], list) and all(isinstance(source, str) and 0 < len(source) <= 256 for source in state['candidate_source_ids']), 'Malformed retained candidate sources')
	for source, entry in state['seen'].items():
		_require(isinstance(source, str) and 0 < len(source) <= 256, 'Invalid retained planning source identity')
		if version == 1:
			digest, age = entry, 0
		else:
			_require(isinstance(entry, list) and len(entry) == 2, 'Malformed retained planning digest and age')
			digest, age = entry
		_require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) is not None, 'Invalid retained planning digest')
		_require(type(age) is int and 0 <= age <= sequence, 'Invalid retained planning age')
	for event in state['pending']:
		_require(isinstance(event, dict) and set(event) == {'source_id', 'trigger', 'override'} and isinstance(event['source_id'], str) and 0 < len(event['source_id']) <= 256, 'Malformed pending planning event')
		_require(isinstance(event['trigger'], str) and event['trigger'] in RECOMPUTE_TRIGGERS and (event['override'] is None or isinstance(event['override'], dict)), 'Malformed pending planning trigger or override')
	if migrate:
		if version == 1:
			state['seen'] = {source: [digest, 0] for source, digest in state['seen'].items()}
		state['schema_version'] = PRODUCTION_PLANNING_SCHEMA_VERSION
		state['cycle_sequence'] = sequence
	return state

def _planning_eviction_order(state):
	pinned = {event['source_id'] for event in state['pending']}
	return sorted((source for source in state['seen'] if source not in pinned),
		key=lambda source: (state['seen'][source][1] if isinstance(state['seen'][source], list) else 0, source))

def _encode_production_planning(state, *, recovery=False):
	"""Retain pending sources and store exactly the canonical bytes checked here."""
	_planning_state(state)
	eligible = _planning_eviction_order(state)
	count = min(max(0, len(state['seen']) - PRODUCTION_PLANNING_SEEN_RETENTION), len(eligible))
	for source in eligible[:count]:
		del state['seen'][source]
	encoded = canonical(state)
	if recovery and len(encoded) > PRODUCTION_PLANNING_MAX_BYTES:
		# Account for each canonical entry and its comma, then encode the final
		# reduced state once more. This avoids quadratic re-encoding during repair.
		size = len(encoded)
		for source in eligible[count:]:
			entry_size = len(canonical({source: state['seen'][source]})) - 2
			size -= entry_size + (1 if len(state['seen']) > 1 else 0)
			del state['seen'][source]
			if size <= PRODUCTION_PLANNING_MAX_BYTES:
				break
		encoded = canonical(state)
	_require(len(encoded) <= PRODUCTION_PLANNING_MAX_BYTES,
		"Production planning state exceeds 4 MiB; pending/preserved fields cannot fit. Stop cycles and run planning-state prune on the primary controller database; if refused, reach a round boundary or reduce the provider event window")
	return encoded

def prune_production_planning_state(path, *, worktree_roots=(), forget_sources=()):
	"""Repair only the existing primary planning key. Operators must stop cycles."""
	_require(Path(path).is_file(), 'Planning recovery requires an existing controller database')
	path = protected_state_path(path, worktree_roots)
	_require(isinstance(forget_sources, (list, tuple)) and all(isinstance(source, str) and source for source in forget_sources), 'Invalid source identities to forget')
	# mode=rw prevents a missing file from becoming a new database. No ordinary
	# constructor, migration, journal configuration, adapter, or provider runs.
	db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10, isolation_level=None)
	try:
		db.execute('BEGIN IMMEDIATE')
		_require(db.execute('PRAGMA application_id').fetchone()[0] == STATE_APPLICATION_ID,
			'Planning recovery requires the primary controller database identity')
		_require(db.execute('PRAGMA user_version').fetchone()[0] == STATE_SCHEMA_VERSION,
			'Planning recovery requires the established controller schema version')
		_require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Controller state integrity check failed')
		row = db.execute("SELECT length(CAST(value AS BLOB)) FROM controller_meta WHERE key='production_planning'").fetchone()
		_require(row is not None, "Missing controller_meta['production_planning']; recovery does not initialize state")
		before_bytes = row[0]
		_require(before_bytes <= PRODUCTION_PLANNING_RECOVERY_MAX_BYTES, 'Planning recovery input exceeds the finite 16 MiB allowance')
		raw = db.execute("SELECT value FROM controller_meta WHERE key='production_planning'").fetchone()[0]
		try:
			state = json.loads(raw, object_pairs_hook=_pairs)
			validate_value(state)
		except (ValueError, RecursionError) as exc:
			raise ValidationError(f'Invalid recovery JSON: {exc}') from exc
		_planning_state(state)
		before_count = len(state['seen'])
		pinned = {event['source_id'] for event in state['pending']}
		_require(not pinned.intersection(forget_sources), 'Cannot forget a pending planning source')
		for source in forget_sources:
			state['seen'].pop(source, None)
		encoded = _encode_production_planning(state, recovery=True)
		db.execute("UPDATE controller_meta SET value=? WHERE key='production_planning'", (encoded.decode('utf-8'),))
		db.execute('COMMIT')
		return {'status': 'PRUNED', 'before_bytes': before_bytes, 'after_bytes': len(encoded),
			'retained': len(state['seen']), 'removed': before_count - len(state['seen'])}
	except BaseException:
		if db.in_transaction:
			db.execute('ROLLBACK')
		raise
	finally:
		db.close()

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
OVERLAP_PROVIDER_CLOCK_SKEW_SECONDS = 5
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

def replan_trigger_command(event):
	required = {'repository_id', 'round_id', 'prior_inventory_sha256', 'next_inventory_sha256', 'nonterminal_dispatch_ids'}
	_require(isinstance(event, dict) and set(event) == required, 'Round-boundary replan event is malformed')
	_require(isinstance(event['repository_id'], str) and event['repository_id'].isdecimal(), 'Round-boundary replan repository identity is invalid')
	_require(isinstance(event['round_id'], str) and event['round_id'].strip() == event['round_id'] and event['round_id'] and len(event['round_id']) <= 256 and not any(ord(char) < 32 for char in event['round_id']), 'Round-boundary replan identity is invalid')
	_require(all(isinstance(event[key], str) and re.fullmatch('[0-9a-f]{64}', event[key]) is not None for key in ('prior_inventory_sha256', 'next_inventory_sha256')), 'Round-boundary replan inventory binding is invalid')
	ids = event['nonterminal_dispatch_ids']
	_require(isinstance(ids, list) and ids == sorted(ids) and len(ids) == len(set(ids)) and ids and all(isinstance(value, str) and value.strip() == value and value for value in ids), 'Round-boundary replan dispatch identities are not canonical')
	return 'AWF-ROUND-REPLAN 1\n' + canonical(event).decode('utf-8') + '\n'

def validate_replan_trigger_observation(config, request, observation, *, now):
	from .canonical import fingerprint
	timestamp(now)
	required_request = {'comment_id', 'repository_id', 'prior_inventory_sha256', 'next_inventory_sha256', 'nonterminal_dispatch_ids'}
	_require(isinstance(request, dict) and set(request) == required_request and type(request['comment_id']) is int and request['comment_id'] > 0, 'Replan trigger observation request is malformed')
	event_context = {key: deepcopy(request[key]) for key in required_request if key != 'comment_id'}
	_require(isinstance(config, dict) and isinstance(config.get('merge_gate'), dict), 'Replan trigger needs reviewed owner configuration')
	trusted = config['merge_gate'].get('trusted_owner_ids')
	_require(isinstance(trusted, list) and trusted and all(type(value) is int and value > 0 for value in trusted) and len(trusted) == len(set(trusted)), 'Replan trigger has no configured trusted owner identities')
	required_observation = {'source', 'repository_id', 'provider_observed_at', 'observation_etag', 'comment'}
	_require(isinstance(observation, dict) and set(observation) == required_observation and observation['source'] == 'github.com' and str(observation['repository_id']) == request['repository_id'], 'Replan trigger provider observation names a foreign repository')
	_provider_fresh({'provider_observed_at': observation['provider_observed_at'], 'etag': observation['observation_etag']}, now, 'Replan trigger aggregate')
	comment = observation['comment']
	comment_fields = {'id', 'node_id', 'actor_id', 'actor_login', 'author_association', 'created_at', 'updated_at', 'body', 'etag', 'provider_observed_at'}
	_require(isinstance(comment, dict) and set(comment) == comment_fields and comment['id'] == request['comment_id'] and isinstance(comment['node_id'], str) and comment['node_id'].strip() and type(comment['actor_id']) is int and comment['actor_id'] > 0 and isinstance(comment['actor_login'], str) and comment['actor_login'].strip() and comment['author_association'] in {'OWNER', 'MEMBER', 'COLLABORATOR'}, 'Replan trigger comment provider identity is malformed')
	_provider_fresh(comment, now, 'Replan trigger comment')
	_require(comment['actor_id'] in set(trusted), 'Replan trigger comment author is not a configured owner')
	created, updated, current = timestamp(comment['created_at']), timestamp(comment['updated_at']), timestamp(now)
	_require(created == updated, 'Replan trigger comment was edited or mutated')
	_require(created <= current and (current - created).total_seconds() <= OVERLAP_OBSERVATION_MAX_AGE_SECONDS, 'Replan trigger comment is stale or future-dated')
	prefix = 'AWF-ROUND-REPLAN 1\n'
	body = comment['body']
	_require(isinstance(body, str) and body.startswith(prefix) and body.endswith('\n'), 'Replan trigger comment is not the canonical round event')
	event = loads(body[len(prefix):-1])
	_require(replan_trigger_command(event) == body, 'Replan trigger comment bytes are not canonical')
	_require(all(event[key] == event_context[key] for key in event_context), 'Replan trigger comment does not bind the current round boundary')
	artifact = {'comment_id': comment['id'], 'node_id': comment['node_id'], 'actor_id': comment['actor_id'], 'actor_login': comment['actor_login'], 'created_at': comment['created_at'], 'etag': comment['etag'], 'provider_observed_at': comment['provider_observed_at']}
	artifact['artifact_sha256'] = fingerprint('controller-replan-provider-artifact', artifact)
	trigger_id = fingerprint('controller-replan-trigger-id', {'repository_id': request['repository_id'], 'comment_id': comment['id'], 'node_id': comment['node_id']})
	trigger = {'schema_version': REPLAN_TRIGGER_SCHEMA_VERSION, 'source': 'github.com', 'trigger_id': trigger_id, **event, 'observed_at': now, 'provider_artifact': artifact, 'owner_policy_sha256': fingerprint('controller-replan-owner-policy', {'trusted_owner_ids': sorted(trusted)})}
	trigger['trigger_sha256'] = fingerprint('controller-replan-trigger', trigger)
	return trigger

def _provider_fresh(value, now, label):
	_require(isinstance(value, dict) and isinstance(value.get('provider_observed_at'), str) and isinstance(value.get('etag'), str) and value['etag'].strip() and (len(value['etag']) <= 512), label + ' needs a provider timestamp and ETag')
	observed = timestamp(value['provider_observed_at'])
	current = timestamp(now)
	age = (current - observed).total_seconds()
	_require(-OVERLAP_PROVIDER_CLOCK_SKEW_SECONDS <= age <= OVERLAP_OBSERVATION_MAX_AGE_SECONDS, label + ' is not a fresh provider observation')

def _post_provider_controller_time(clock, previous):
	_require(callable(clock), 'Provider observation needs a post-observation controller clock')
	observed = clock()
	_require(isinstance(observed, str), 'Post-observation controller time is malformed')
	_require(timestamp(observed) >= timestamp(previous), 'Controller clock moved backwards during provider observation')
	return observed

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

def _reservation_record(value):
	from .canonical import fingerprint
	required = {'schema_version', 'kind', 'source', 'repository_id', 'head_sha', 'tree_sha', 'artifact_id', 'observed_at', 'authenticated', 'complete', 'paths', 'evidence_sha256'}
	_require(isinstance(value, dict) and set(value) == required, 'Reservation source is partial or has unknown fields')
	_require(value['schema_version'] == 1 and value['kind'] in {'parked', 'worktree_diff'}, 'Reservation source kind or version is unsupported')
	_require(value['source'] == ('github.com' if value['kind'] == 'parked' else 'git'), 'Reservation source is not authoritative for its kind')
	_require(value['authenticated'] is True and value['complete'] is True, 'Reservation source is not authenticated and complete')
	_require(isinstance(value['artifact_id'], str) and value['artifact_id'].strip() == value['artifact_id'] and value['artifact_id'], 'Reservation artifact identity is invalid')
	_require(isinstance(value['repository_id'], str) and value['repository_id'].isdecimal(), 'Reservation repository identity is invalid')
	_require(re.fullmatch('[0-9a-f]{40}', value['head_sha']) is not None and re.fullmatch('[0-9a-f]{40}', value['tree_sha']) is not None, 'Reservation head/tree binding is invalid')
	timestamp(value['observed_at'])
	_require(isinstance(value['paths'], list) and value['paths'] and value['paths'] == sorted(value['paths']) and len(value['paths']) == len(set(value['paths'])), 'Reservation paths must be a sorted nonempty unique list')
	paths = []
	for path in value['paths']:
		canonical_path = _exact_overlap_path(path)
		_require(unicodedata.normalize('NFC', canonical_path) == canonical_path, 'Reservation path is not NFC canonical')
		paths.append(canonical_path)
	seed = {key: deepcopy(item) for key, item in value.items() if key != 'evidence_sha256'}
	_require(value['evidence_sha256'] == fingerprint('controller-reservation-source', seed), 'Reservation source digest is missing or mismatched')
	return {**deepcopy(value), 'paths': paths}

def canonical_reservation_sources(sources, repository_root, head_sha, tree_sha, repository_id, now, *, inventory_sha256=None):
	"""Authenticate and Git-canonicalize parked and dirty-worktree reservations."""
	from .canonical import fingerprint
	envelope = None
	if isinstance(sources, dict):
		required = {'schema_version', 'source', 'repository_id', 'head_sha', 'tree_sha', 'inventory_sha256', 'observed_at', 'authenticated', 'complete', 'sources', 'observation_sha256'}
		_require(set(sources) == required and sources['schema_version'] == 1 and sources['source'] == 'host_observation' and sources['authenticated'] is True and sources['complete'] is True, 'Reservation observation envelope is not authenticated and complete')
		_require(re.fullmatch('[0-9a-f]{64}', sources['inventory_sha256'] or '') is not None, 'Reservation observation inventory binding is malformed')
		_require(inventory_sha256 is None or sources['inventory_sha256'] == inventory_sha256, 'Reservation observation is bound to a different inventory')
		seed = {key: deepcopy(value) for key, value in sources.items() if key != 'observation_sha256'}
		_require(sources['observation_sha256'] == fingerprint('controller-reservation-observation', seed), 'Reservation observation envelope digest is mismatched')
		envelope = sources
		sources = sources['sources']
	_require(isinstance(sources, list), 'Reservation sources must be a list')
	root = Path(repository_root).resolve(strict=True)
	_require(str(repository_id).isdecimal(), 'Reservation repository identity is invalid')
	_require(_git(root, 'rev-parse', 'HEAD').decode('ascii', 'strict').strip() == head_sha and _git(root, 'rev-parse', 'HEAD^{tree}').decode('ascii', 'strict').strip() == tree_sha, 'Reservation source repository tuple is stale')
	tracked_records = _git(root, 'ls-files', '-s', '-z').decode('utf-8', 'strict').split('\x00')
	modes = {}
	for record in tracked_records:
		if not record:
			continue
		metadata, name = record.split('\t', 1)
		mode, _object_id, stage = metadata.split(' ')
		_require(stage == '0', 'Reservation Git inventory contains an unmerged path')
		modes[name] = mode
	tracked = list(modes)
	untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '-z').decode('utf-8', 'strict').split('\x00')
	known = {name.casefold(): name for name in [*tracked, *untracked] if name}
	_require(len(known) == len([name for name in [*tracked, *untracked] if name]), 'Repository has case-folded path aliases')
	dirty = set(_git(root, 'diff', '--name-only', '-z', 'HEAD', '--').decode('utf-8', 'strict').split('\x00'))
	dirty.update(name for name in untracked if name)
	dirty.discard('')
	current = timestamp(now)
	if envelope is not None:
		_require(envelope['repository_id'] == str(repository_id) and envelope['head_sha'] == head_sha and envelope['tree_sha'] == tree_sha, 'Reservation observation envelope names a foreign or stale repository tuple')
		age = (current - timestamp(envelope['observed_at'])).total_seconds()
		_require(-OVERLAP_PROVIDER_CLOCK_SKEW_SECONDS <= age <= OVERLAP_OBSERVATION_MAX_AGE_SECONDS, 'Reservation observation envelope is stale')
	result = []
	for raw in sources:
		value = _reservation_record(raw)
		_require(value['repository_id'] == str(repository_id) and value['head_sha'] == head_sha and value['tree_sha'] == tree_sha, 'Reservation source names a foreign or stale repository tuple')
		age = (current - timestamp(value['observed_at'])).total_seconds()
		_require(-OVERLAP_PROVIDER_CLOCK_SKEW_SECONDS <= age <= OVERLAP_OBSERVATION_MAX_AGE_SECONDS, 'Reservation source observation is stale')
		for path in value['paths']:
			_require(known.get(path.casefold()) == path, 'Reservation path is not the exact Git-canonical path')
			_require(modes.get(path) not in {'120000', '160000'}, 'Reservation path is a Git link or nested repository')
			candidate = root / path
			for component in (candidate, *candidate.parents):
				if component == root.parent:
					break
				_require(not _reparse(component), 'Reservation path traverses a link, junction, or reparse point')
			if candidate.exists():
				metadata = candidate.stat()
				identity = (getattr(metadata, 'st_dev', None), getattr(metadata, 'st_ino', None))
				_require(all((type(item) is int for item in identity)) and identity[1] != 0, 'Reservation path has no reliable physical identity')
				aliases = []
				for other in known.values():
					other_path = root / other
					if other_path.exists() and not _reparse(other_path):
						other_metadata = other_path.stat()
						if (getattr(other_metadata, 'st_dev', None), getattr(other_metadata, 'st_ino', None)) == identity:
							aliases.append(other)
				_require(aliases == [path], 'Reservation path has a physical Git alias')
		if value['kind'] == 'worktree_diff':
			_require(set(value['paths']) == dirty, 'Worktree-diff reservation is partial or differs from the authenticated Git observation')
		result.append(value)
	_require(len({item['artifact_id'] for item in result}) == len(result), 'Reservation artifact identity is replayed')
	if envelope is not None:
		worktree_records = [item for item in result if item['kind'] == 'worktree_diff']
		_require(len(worktree_records) == (1 if dirty else 0), 'Reservation observation omits or duplicates the complete worktree diff')
	return result

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

def _validated_overlap_sync_receipt(gate, payload, receipt, not_before):
	required_receipt = set(payload) | {'status', 'post_sync_heads', 'diff', 'diff_sha256', 'changed_files', 'observed_at'}
	diff = receipt.get('diff') if isinstance(receipt, dict) else None
	diff_bytes = diff.encode('utf-8') if isinstance(diff, str) else b''
	_require(isinstance(receipt, dict) and set(receipt) == required_receipt and all((receipt[key] == payload[key] for key in payload)) and (receipt['status'] == 'COMPLETED') and isinstance(receipt['post_sync_heads'], list) and (len(receipt['post_sync_heads']) == len(gate['heads'])) and all((isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head) is not None for head in receipt['post_sync_heads'])) and (receipt['post_sync_heads'] != gate['heads']) and isinstance(diff, str) and diff.strip() and (len(diff_bytes) <= OVERLAP_SYNC_DIFF_MAX_BYTES) and (receipt['diff_sha256'] == hashlib.sha256(diff_bytes).hexdigest()) and isinstance(receipt['changed_files'], list) and (receipt['changed_files'] == sorted(receipt['changed_files'])) and (len(receipt['changed_files']) == len(set(receipt['changed_files']))) and (gate['path'] in receipt['changed_files']) and (timestamp(receipt['observed_at']) >= timestamp(not_before)), 'Overlap synchronization executor receipt is missing or mismatched')
	for path in receipt['changed_files']:
		_exact_overlap_path(path)
	return deepcopy(receipt)

class ContinuousControllerStore:
	@staticmethod
	def _validated_batch_audit(record):
		"""Validate the closed T8 envelope; its digest is consistency, not authority."""
		_require(isinstance(record, dict) and set(record) == {'candidate', 'record_sha256'},
			'Batch audit envelope is malformed')
		# Detach before inspecting or retaining any caller-owned nested containers.
		record = loads(canonical(record).decode())
		candidate = record['candidate']
		_require(isinstance(candidate, dict) and set(candidate) == {
			'repository_id', 'batch_id', 'base_sha', 'head_sha', 'tree_sha', 'sources'},
			'Batch audit candidate is malformed')
		_require(type(candidate['repository_id']) is int and candidate['repository_id'] > 0,
			'Batch audit repository identity must be a positive integer')
		batch_id = candidate['batch_id']
		_require(isinstance(batch_id, str) and batch_id and batch_id.strip() == batch_id
			and len(batch_id) <= 256 and not any(ord(char) < 32 or ord(char) == 127 for char in batch_id),
			'Batch audit batch identity is invalid')
		def git_sha(value):
			return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None
		_require(all(git_sha(candidate[key]) for key in ('base_sha', 'head_sha', 'tree_sha')),
			'Batch audit candidate Git identity is invalid')
		sources = candidate['sources']
		_require(isinstance(sources, list) and sources, 'Batch audit requires source pull requests')
		seen = set()
		for source in sources:
			_require(isinstance(source, dict) and set(source) == {'pr_number', 'head_sha'},
				'Batch audit source is malformed')
			pr_number = source['pr_number']
			_require(type(pr_number) is int and pr_number > 0 and pr_number not in seen
				and git_sha(source['head_sha']), 'Batch audit source identity is invalid or duplicated')
			seen.add(pr_number)
		digest = record['record_sha256']
		_require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) is not None
			and digest == hashlib.sha256(canonical(candidate)).hexdigest(),
			'Batch audit candidate digest mismatch')
		return record

	@classmethod
	def _batch_audit_from_row(cls, row):
		_require(isinstance(row['record_json'], str), 'Stored batch audit bytes are invalid')
		record = cls._validated_batch_audit(loads(row['record_json']))
		_require(canonical(record).decode() == row['record_json']
			and type(row['repository_id']) is int
			and row['repository_id'] == record['candidate']['repository_id']
			and row['batch_id'] == record['candidate']['batch_id']
			and row['record_sha256'] == record['record_sha256'],
			'Stored batch audit identity or canonical bytes mismatch')
		return record

	def append_batch_audit(self, record):
		"""Retain one immutable controller-local record per repository/batch.

		This bounded ledger neither proves AWF-91 placement nor creates records
		for operational batches automatically. No retention purge is provided.
		"""
		record = self._validated_batch_audit(record)
		candidate = record['candidate']
		encoded = canonical(record).decode()
		with self.transaction() as db:
			# A damaged identity column must not make a retained record disappear
			# from the retry lookup and permit a second logical copy.
			for stored in db.execute('SELECT * FROM controller_batch_audit ORDER BY sequence'):
				self._batch_audit_from_row(stored)
			row = db.execute('SELECT * FROM controller_batch_audit WHERE repository_id=? AND batch_id=?',
				(candidate['repository_id'], candidate['batch_id'])).fetchone()
			if row is not None:
				prior = self._batch_audit_from_row(row)
				_require(row['record_json'] == encoded, 'Batch audit identity cannot be rewritten')
				return prior
			db.execute('INSERT INTO controller_batch_audit(repository_id,batch_id,record_sha256,record_json) VALUES(?,?,?,?)',
				(candidate['repository_id'], candidate['batch_id'], record['record_sha256'], encoded))
		return record

	def batch_audit_records(self):
		"""Read detached, validated records in their durable append order."""
		with self.connection() as db:
			return [self._batch_audit_from_row(row) for row in
				db.execute('SELECT * FROM controller_batch_audit ORDER BY sequence')]

	def production_planning_state(self):
		with self.connection() as db:
			row = db.execute("SELECT value FROM controller_meta WHERE key='production_planning'").fetchone()
		if row and len(row[0].encode('utf-8')) > MAX_DOCUMENT_BYTES:
			raise ValidationError("controller_meta['production_planning'] exceeds 8 MiB; ordinary cycle loading cannot repair it. Stop cycles and run planning-state prune on the primary controller database")
		state = loads(row[0]) if row else {'pending': [], 'seen': {}, 'overrides': {}, 'candidate': None, 'observation': None}
		return _planning_state(state, migrate=True)

	def _save_production_planning(self, state):
		encoded = _encode_production_planning(state)
		with self.transaction() as db:
			db.execute("INSERT OR REPLACE INTO controller_meta VALUES('production_planning',?)", (encoded.decode('utf-8'),))

	def prune_production_planning(self, *, forget_sources=()):
		return prune_production_planning_state(self.path, worktree_roots=self.worktree_roots, forget_sources=forget_sources)

	def plan_production_cycle(self, state_store, tickets, config, *, backlog, events,
			verify_owner_source, resource_observation, expected_resource_binding,
			expected_resource_producer_sha256, operating_snapshot, verify_host_observation,
			now, boundary_ready, overlap_allowances=(),
			expected_overlap_binding=None, verify_overlap_allowance=None):
		"""Journal authenticated observations and recover the two-store append window."""
		from . import planning
		from .canonical import fingerprint
		state = self.production_planning_state()
		_require(state['cycle_sequence'] < 2**53 - 1, 'Production planning cycle sequence exhausted')
		state['cycle_sequence'] += 1
		planning.verify_override_state(config, state['overrides'], verify_owner_source)
		for event in events:
			_require(isinstance(event, dict) and set(event) == {'source_id', 'trigger', 'override'}, 'Planning event must be a closed observation')
			_require(isinstance(event['source_id'], str) and 0 < len(event['source_id']) <= 256, 'Planning event source identity is required')
			_require(event['trigger'] in planning.RECOMPUTE_TRIGGERS, 'Unknown planning event trigger')
			digest = fingerprint('controller-planning-event', event)
			prior = state['seen'].get(event['source_id'])
			_require(prior is None or prior[0] == digest, f"Planning event source identity was reused with changed bytes: {event['source_id']}")
			if prior is not None:
				prior[1] = state['cycle_sequence']
				continue
			if event['override'] is not None:
				_require(event['trigger'] == 'override_changed', 'Only an override event may carry an owner chain')
				incoming = event['override']
				planning.verify_override_state(config, incoming, verify_owner_source)
				old = state['overrides'].get('planning_override_history', [])
				new = incoming.get('planning_override_history', [])
				_require(len(new) > len(old) and new[:len(old)] == old, 'Planning owner chain replay or rollback')
				state['overrides'] = deepcopy(incoming)
			state['seen'][event['source_id']] = [digest, state['cycle_sequence']]
			state['pending'].append(deepcopy(event))
		# Content changes are controller-derived only after independent authentication.
		observed = fingerprint('controller-planning-input', {'backlog': backlog,
			'configuration': config, 'overrides': state['overrides'],
			'resources': {k: resource_observation[k] for k in ('binding', 'host_capacity', 'resources')},
			'operating': operating_snapshot.operating_hash,
			'overlap_allowances': overlap_allowances})
		if state['observation'] != observed and not state['pending']:
			trigger = 'project_start' if state['observation'] is None else 'files_changed'
			state['pending'].append({'source_id': observed, 'trigger': trigger, 'override': None})
		if len(events) > PRODUCTION_PLANNING_MAX_EVENTS:
			# Check staged bytes first, but refuse the batch before any durable save.
			_encode_production_planning(state)
			raise ValidationError('Production planning cycle exceeds 4,000 events; reduce the provider event window')
		self._save_production_planning(state)
		if not boundary_ready:
			return None
		# Verify the retained candidate against the durable history before any retry.
		candidate = state['candidate']
		if candidate is not None:
			planning.verify_plan_record(candidate)
			history = state_store.planning_history()
			matches = [record for record in history if record['plan_id'] == candidate['plan_id']]
			_require(not matches or matches == [candidate], 'Durable plan conflicts with the retained append candidate')
			if not matches:
				planning.append_plan_record(state_store, candidate)
			# Acknowledgement processes only the inputs used by this candidate.
			# Legacy candidates have no fingerprint and require a fresh plan.
			state['observation'] = state.pop('candidate_observation', None)
			state['candidate'] = None
			# Legacy retained candidates covered only the first pending event.
			consumed = set(state.pop('candidate_source_ids',
				[event['source_id'] for event in state['pending'][:1]]))
			state['pending'] = [event for event in state['pending'] if event['source_id'] not in consumed]
			self._save_production_planning(state)
		# Recovery may consume the last event while authenticated inputs have
		# changed. Append their plan before returning anything for admission.
		if state['observation'] != observed and not state['pending']:
			state['pending'].append({'source_id': observed, 'trigger': 'files_changed', 'override': None})
		while state['pending']:
			trigger = state['pending'][0]['trigger']
			# All event effects and source fingerprints were retained above.
			# Coalesce this trigger across the entire queue, including interleaved sources.
			source_ids = [event['source_id'] for event in state['pending'] if event['trigger'] == trigger]
			# Seal a fresh candidate; the durable history check below accepts an
			# identical plan without appending it again and refuses conflicting bytes.
			working = deepcopy(state['overrides'])
			working['planning_history'] = []
			working['current_plan'] = None
			result = planning.recompute_plan(backlog, config, working, now=now,
				trigger=trigger, resource_observation=resource_observation,
				expected_resource_binding=expected_resource_binding,
				expected_resource_producer_sha256=expected_resource_producer_sha256,
				operating_snapshot=operating_snapshot, observed_at=now,
				verify_host_observation=verify_host_observation, verify_owner_source=verify_owner_source,
				overlap_allowances=overlap_allowances, expected_overlap_binding=expected_overlap_binding,
				verify_overlap_allowance=verify_overlap_allowance)
			candidate = result['record']
			state['candidate'] = candidate
			state['candidate_observation'] = observed
			state['candidate_source_ids'] = source_ids
			self._save_production_planning(state)
			history = state_store.planning_history()
			matches = [record for record in history if record['plan_id'] == candidate['plan_id']]
			_require(not matches or matches == [candidate], 'Planning append candidate conflicts with immutable history')
			if not matches:
				planning.append_plan_record(state_store, candidate)
			state['observation'] = state.pop('candidate_observation')
			state['candidate'] = None
			consumed = set(state.pop('candidate_source_ids'))
			state['pending'] = [event for event in state['pending'] if event['source_id'] not in consumed]
			self._save_production_planning(state)
		record = state_store.current_plan()
		_require(record is not None, 'Production dispatch requires a durable verified plan')
		planning.verify_plan_record(record)
		return record

	def recompute_durable_plan(self, state_store, tickets, config, *, legacy_state=None,
			migration_version=1, **planning_inputs):
		"""Seal through planning.py, then durably append before returning a plan.

		The AWF-91 store owns plan history; controller-private tables never hold
		copies. Explicit legacy input is migrated once, even when it is empty.
		A restart without legacy input reads the durable history directly.
		"""
		from .store import Store
		from .planning import recompute_plan, append_plan_record
		_require(isinstance(state_store, Store), 'Durable planning requires the AWF-91 state store')
		if legacy_state is not None:
			state_store.migrate_planning_history(legacy_state, version=migration_version)
		else:
			_require(type(migration_version) is int and migration_version == 1,
				'Planning history migration version mismatch')
		state = deepcopy(legacy_state) if legacy_state is not None else {}
		state['planning_history'] = state_store.planning_history()
		state['current_plan'] = state_store.current_plan()
		result = recompute_plan(tickets, config, state, **planning_inputs)
		if result['status'] == 'PLANNED':
			append_plan_record(state_store, result['record'])
		return result

	def __init__(self, path, stream_ids, cadence_seconds=DEFAULT_STATUS_CADENCE_SECONDS, worktree_roots=(), periodic_status_enabled=True, migrate_cadence=False, migrate_streams=False):
		self.worktree_roots = tuple((Path(root).resolve(strict=False) for root in worktree_roots))
		self.path = protected_state_path(path, self.worktree_roots)
		_require(isinstance(stream_ids, (list, tuple)) and stream_ids and (len(stream_ids) == len(set(stream_ids))) and all((isinstance(item, str) and item.strip() for item in stream_ids)), 'Configured stream identities must be unique nonempty strings')
		_require(type(cadence_seconds) is int and cadence_seconds > 0, 'Status cadence must be positive seconds')
		_require(type(periodic_status_enabled) is bool, 'Periodic status enablement must be boolean')
		_require(type(migrate_cadence) is bool, 'Cadence migration choice must be boolean')
		_require(type(migrate_streams) is bool, 'Stream migration choice must be boolean')
		self.periodic_status_enabled = periodic_status_enabled
		self.path.parent.mkdir(parents=True, exist_ok=True)
		with self._initialization_connection() as db:
			self._validate_database_before_initialization(db)
			db.execute('BEGIN IMMEDIATE')
			try:
				if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controller_dispatch'").fetchone() is not None:
					self._migrate_dispatch_rows(db, apply=False)
				for statement in DDL.split(';'):
					if statement.strip():
						db.execute(statement)
				for statement in BATCH_AUDIT_DDL:
					db.execute(statement)
				from .reporting.delivery import install_delivery_schema
				install_delivery_schema(db)
				self._migrate_dispatch_rows(db)
				configured = canonical(sorted(stream_ids)).decode()
				old = db.execute("SELECT value FROM controller_meta WHERE key='streams'").fetchone()
				if old is not None and old[0] != configured and migrate_streams:
					recorded = sorted(row[0] for row in db.execute('SELECT stream_id FROM stream_status'))
					_require(recorded and old[0] == canonical(recorded).decode() and set(recorded) < set(stream_ids),
						'Stream migration only permits adding streams to the recorded set')
					db.execute("UPDATE controller_meta SET value=? WHERE key='streams'", (configured,))
				else:
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
			configure_database(db, self.path, STATE_APPLICATION_ID)
		restrict_state_permissions(self.path)

	@staticmethod
	def _validate_database_before_initialization(db):
		"""Validate existing state without changing its journal or identity bytes."""
		try:
			integrity = db.execute('PRAGMA quick_check').fetchone()
			_require(integrity is not None and integrity[0] == 'ok', 'Controller state integrity check failed')
			observed_app = db.execute('PRAGMA application_id').fetchone()[0]
			observed_version = db.execute('PRAGMA user_version').fetchone()[0]
			uninitialized = observed_app == 0 and observed_version == 0
			_require(uninitialized or observed_app == STATE_APPLICATION_ID, 'Controller state application identity is wrong or tampered')
			_require(uninitialized or observed_version == STATE_SCHEMA_VERSION, 'Controller state schema version is unsupported or tampered')
		except sqlite3.DatabaseError as exc:
			raise ValidationError('Controller state is unreadable or corrupt') from exc

	@staticmethod
	def _migrate_dispatch_rows(db, *, apply=True):
		"""Atomically upgrade the closed 1.9.4 dispatch JSON shape to v2."""
		rows = db.execute('SELECT dispatch_id,stream_id,ticket,exact_tuple,status,payload_json,receipt_json FROM controller_dispatch ORDER BY rowid').fetchall()
		migrations = []
		legacy_base = {'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'paths', 'actor', 'next_action', 'dispatch_nonce', 'prepared_at', 'role', 'lifecycle_stage'}
		for row in rows:
			payload = loads(row['payload_json'])
			if payload.get('dispatch_schema_version') == DISPATCH_SCHEMA_VERSION:
				_require(isinstance(payload.get('lane_start_waivers'), list) and payload['lane_start_waivers'] == sorted(payload['lane_start_waivers']) and len(payload['lane_start_waivers']) == len(set(payload['lane_start_waivers'])), 'Versioned dispatch has no canonical lane-start waiver binding')
				_require(all((payload.get(key) == row[column] for key, column in (('dispatch_id', 'dispatch_id'), ('stream', 'stream_id'), ('ticket', 'ticket'), ('exact_tuple', 'exact_tuple')))), 'Versioned dispatch identity differs from its row')
				_require(row['status'] in {'PENDING', 'IN_FLIGHT', 'UNKNOWN', 'LAUNCHED', 'TERMINAL', 'CANCELLED'}, 'Versioned dispatch has an unsupported durable state')
				if row['status'] in {'LAUNCHED', 'TERMINAL'}:
					receipt = loads(row['receipt_json']) if row['receipt_json'] is not None else None
					expected = row['status']
					_require(isinstance(receipt, dict) and receipt.get('dispatch_schema_version') == DISPATCH_SCHEMA_VERSION and receipt.get('dispatch_state') == expected and receipt.get('terminal') is (expected == 'TERMINAL'), 'Versioned dispatch receipt differs from its durable state')
				elif row['status'] in {'PENDING', 'IN_FLIGHT', 'UNKNOWN'}:
					_require(row['receipt_json'] is None, 'Unaccepted versioned dispatch has an unexpected receipt')
				continue
			_require('dispatch_schema_version' not in payload, 'Dispatch row has an unsupported schema version')
			status = row['status']
			_require(status in {'PENDING', 'IN_FLIGHT', 'UNKNOWN'}, f'Legacy {status} dispatch requires an explicit operator migration')
			allowed = set(legacy_base)
			if status in {'IN_FLIGHT', 'UNKNOWN'}:
				allowed.add('begun_at')
			allowed.add('codex_skill_selection')
			if status == 'UNKNOWN':
				allowed.update({'_stream_before_unknown', 'reconcile_nonce', 'reconcile_at'})
			_require(legacy_base <= set(payload) and set(payload) <= allowed, f'Legacy {status} dispatch payload is ambiguous and was not migrated')
			_require(payload['dispatch_id'] == row['dispatch_id'] and payload['stream'] == row['stream_id'] and payload['ticket'] == row['ticket'] and payload['exact_tuple'] == row['exact_tuple'], f'Legacy {status} dispatch identity is mismatched')
			_require((status == 'PENDING') == ('begun_at' not in payload), f'Legacy {status} dispatch begin state is ambiguous')
			_require(row['receipt_json'] is None, f'Legacy {status} dispatch has an ambiguous receipt')
			migrated = deepcopy(payload)
			migrated['dispatch_schema_version'] = DISPATCH_SCHEMA_VERSION
			migrated['lane_start_waivers'] = []
			migrations.append((canonical(migrated).decode(), row['dispatch_id']))
		if migrations:
			prior_inventory = db.execute("SELECT value FROM controller_meta WHERE key='last_inventory_sha256'").fetchone()
			_require(prior_inventory is not None and re.fullmatch('[0-9a-f]{64}', prior_inventory[0]) is not None, 'Legacy non-terminal dispatch has no durable prior inventory binding; explicit boundary recovery is required')
		if apply:
			for encoded, dispatch_id in migrations:
				db.execute('UPDATE controller_dispatch SET payload_json=? WHERE dispatch_id=?', (encoded, dispatch_id))
			db.execute("INSERT OR REPLACE INTO controller_meta VALUES('dispatch_schema_version',?)", (str(DISPATCH_SCHEMA_VERSION),))

	@contextmanager
	def _initialization_connection(self):
		db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
		try:
			db.row_factory = sqlite3.Row
			yield db
		finally:
			db.close()

	@contextmanager
	def connection(self):
		with self._initialization_connection() as db:
			configure_database(db, self.path, STATE_APPLICATION_ID)
			yield db

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
			rows = db.execute('SELECT * FROM controller_dispatch WHERE ticket IN (' + placeholders + ") AND status IN ('PENDING','IN_FLIGHT','UNKNOWN','LAUNCHED') ORDER BY rowid", tuple(gate['tickets'])).fetchall()
			observations = []
			blockers = []
			for row in rows:
				if row['status'] == 'LAUNCHED':
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
			_require(row is not None and row['status'] in {'LAUNCHED', 'TERMINAL'}, 'Only a launched dispatch can become quiescent')
			receipt = loads(row['receipt_json'])
			_require(timestamp(observation['observed_at']) >= timestamp(now) and timestamp(observation['observed_at']) >= timestamp(receipt['observed_at']), 'Dispatch quiescence observation predates the cycle or accepted session')
			_require(observation['dispatch_id'] == dispatch_id and observation['ticket'] == row['ticket'] and (observation['exact_tuple'] == row['exact_tuple']) and (observation['session_id'] == receipt['session_id']), 'Dispatch quiescence proof differs from the accepted session')
			payload = loads(row['payload_json'])
			prior = payload.get('terminal_observation')
			_require(prior is None or prior == observation, 'Dispatch quiescence replay differs from retained proof')
			payload['terminal_observation'] = deepcopy(observation)
			terminal_receipt = deepcopy(receipt)
			terminal_receipt.update(dispatch_state='TERMINAL', terminal=True, observed_at=observation['observed_at'])
			db.execute("UPDATE controller_dispatch SET status='TERMINAL',payload_json=?,receipt_json=?,updated_at=? WHERE dispatch_id=?", (canonical(payload).decode(), canonical(terminal_receipt).decode(), observation['observed_at'], dispatch_id))
			return deepcopy(observation)

	def overlap_sync_gate(self, gate_id):
		with self.connection() as db:
			row = db.execute('SELECT record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			return None if row is None else loads(row['record_json'])

	def _finish_overlap_sync_receipt(self, gate_id, payload, receipt, *, expected_status, not_before):
		from .canonical import fingerprint
		with self.transaction() as db:
			row = db.execute('SELECT status,record_json FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(row is not None and row['status'] == expected_status, 'Overlap synchronization gate changed during receipt processing')
			gate = loads(row['record_json'])
			_require(gate.get('execution') == payload, 'Overlap synchronization execution identity changed before receipt processing')
			receipt = _validated_overlap_sync_receipt(gate, payload, receipt, not_before)
			evidence = {'gate_id': gate_id, 'allowance_id': gate['allowance_id'], 'tickets': gate['tickets'], 'pull_requests': gate['pull_requests'], 'heads': gate['heads'], 'pre_sync_heads': gate['heads'], 'post_sync_heads': receipt['post_sync_heads'], 'path': gate['path'], 'diff': receipt['diff'], 'diff_sha256': receipt['diff_sha256'], 'changed_files': receipt['changed_files'], 'executor_receipt_sha256': fingerprint('controller-overlap-sync-receipt', receipt)}
			gate['status'] = 'SYNCED'
			gate['execution'] = deepcopy(receipt)
			gate['critic_evidence'] = evidence
			db.execute("UPDATE controller_overlap_sync_gate SET status='SYNCED',record_json=?,updated_at=? WHERE gate_id=?", (canonical(gate).decode(), receipt['observed_at'], gate_id))
			revision = int(self._meta(db, 'revision')) + 1
			db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return deepcopy(gate)

	def reconcile_overlap_sync_gate(self, gate_id, receipt_reader, now):
		timestamp(now)
		with self.connection() as db:
			row = db.execute('SELECT status,record_json,updated_at FROM controller_overlap_sync_gate WHERE gate_id=?', (gate_id,)).fetchone()
			_require(row is not None, 'Unknown overlap synchronization gate')
			gate = loads(row['record_json'])
			if row['status'] in {'CLEARED', 'SYNCED', 'EXECUTED'}:
				return gate
			_require(row['status'] == 'UNKNOWN', 'Only an unknown overlap synchronization outcome can be reconciled')
			payload = gate.get('execution')
			_require(isinstance(payload, dict) and payload.get('gate_id') == gate_id, 'Unknown overlap synchronization gate has no decided execution identity')
			not_before = row['updated_at']
		_require(callable(receipt_reader), 'Unknown overlap synchronization outcome needs a receipt-only reconciler')
		receipt = receipt_reader(deepcopy(payload))
		return self._finish_overlap_sync_receipt(gate_id, payload, receipt, expected_status='UNKNOWN', not_before=not_before)

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
			return self._finish_overlap_sync_receipt(gate_id, payload, receipt, expected_status='IN_FLIGHT', not_before=now)
		except Exception:
			with self.transaction() as db:
				gate['status'] = 'UNKNOWN'
				db.execute("UPDATE controller_overlap_sync_gate SET status='UNKNOWN',record_json=?,updated_at=? WHERE gate_id=? AND status='IN_FLIGHT'", (canonical(gate).decode(), now, gate_id))
			raise

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

	def seal_round_boundary_replan(self, trigger, now):
		from .canonical import fingerprint
		timestamp(now)
		required = {'schema_version', 'source', 'trigger_id', 'repository_id', 'prior_inventory_sha256', 'next_inventory_sha256', 'round_id', 'observed_at', 'nonterminal_dispatch_ids', 'provider_artifact', 'owner_policy_sha256', 'trigger_sha256'}
		_require(isinstance(trigger, dict) and set(trigger) == required, 'Replan trigger is partial or malformed')
		_require(trigger['schema_version'] == REPLAN_TRIGGER_SCHEMA_VERSION and trigger['source'] == 'github.com', 'Replan trigger is not an authenticated provider artifact')
		for key in ('trigger_id', 'repository_id', 'round_id'):
			_require(isinstance(trigger[key], str) and trigger[key].strip() == trigger[key] and trigger[key], 'Replan trigger identity is invalid')
		_require(trigger['repository_id'].isdecimal(), 'Replan trigger repository identity is invalid')
		_require(all((re.fullmatch('[0-9a-f]{64}', trigger[key]) is not None for key in ('prior_inventory_sha256', 'next_inventory_sha256'))), 'Replan trigger inventory binding is invalid')
		_require(isinstance(trigger['nonterminal_dispatch_ids'], list) and trigger['nonterminal_dispatch_ids'] == sorted(trigger['nonterminal_dispatch_ids']) and len(trigger['nonterminal_dispatch_ids']) == len(set(trigger['nonterminal_dispatch_ids'])), 'Replan trigger dispatch set is not sorted and unique')
		_require(timestamp(trigger['observed_at']) == timestamp(now), 'Replan trigger is not fresh for this round boundary')
		artifact = trigger['provider_artifact']
		artifact_fields = {'comment_id', 'node_id', 'actor_id', 'actor_login', 'created_at', 'etag', 'provider_observed_at', 'artifact_sha256'}
		_require(isinstance(artifact, dict) and set(artifact) == artifact_fields and type(artifact['comment_id']) is int and artifact['comment_id'] > 0 and type(artifact['actor_id']) is int and artifact['actor_id'] > 0 and all(isinstance(artifact[key], str) and artifact[key].strip() for key in ('node_id', 'actor_login', 'created_at', 'etag', 'provider_observed_at')), 'Replan trigger provider artifact is malformed')
		artifact_seed = {key: deepcopy(value) for key, value in artifact.items() if key != 'artifact_sha256'}
		_require(artifact['artifact_sha256'] == fingerprint('controller-replan-provider-artifact', artifact_seed), 'Replan trigger provider artifact digest is mismatched')
		timestamp(artifact['created_at'])
		timestamp(artifact['provider_observed_at'])
		_require(trigger['trigger_id'] == fingerprint('controller-replan-trigger-id', {'repository_id': trigger['repository_id'], 'comment_id': artifact['comment_id'], 'node_id': artifact['node_id']}), 'Replan trigger identity differs from its provider artifact')
		_require(re.fullmatch('[0-9a-f]{64}', trigger['owner_policy_sha256'] or '') is not None, 'Replan trigger owner policy binding is malformed')
		seed = {key: deepcopy(value) for key, value in trigger.items() if key != 'trigger_sha256'}
		_require(trigger['trigger_sha256'] == fingerprint('controller-replan-trigger', seed), 'Replan trigger digest is mismatched')
		with self.transaction() as db:
			binding = db.execute("SELECT value FROM controller_meta WHERE key='repository_binding'").fetchone()
			if binding is not None:
				_require(str(loads(binding[0]).get('repository_id', trigger['repository_id'])) == trigger['repository_id'], 'Replan trigger names a foreign repository')
			nonterminal = [dict(row) for row in db.execute("SELECT dispatch_id,ticket,stream_id AS stream,exact_tuple,status FROM controller_dispatch WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN','LAUNCHED') ORDER BY dispatch_id")]
			observed = [row['dispatch_id'] for row in nonterminal]
			_require(observed and observed == trigger['nonterminal_dispatch_ids'], 'Replan trigger does not bind every current non-terminal dispatch')
			prior_inventory = db.execute("SELECT value FROM controller_meta WHERE key='last_inventory_sha256'").fetchone()
			if prior_inventory is not None:
				_require(prior_inventory[0] == trigger['prior_inventory_sha256'], 'Replan trigger prior inventory digest is stale')
			prior = db.execute('SELECT record_json FROM controller_replan_seal WHERE trigger_id=?', (trigger['trigger_id'],)).fetchone()
			if prior is not None:
				_require(loads(prior['record_json'])['trigger'] == trigger, 'Replan trigger identity was replayed with different evidence')
				return loads(prior['record_json'])
			seal_id = fingerprint('controller-replan-seal', trigger)
			record = {'schema_version': 1, 'seal_id': seal_id, 'status': 'SEALED', 'trigger': deepcopy(trigger), 'nonterminal': nonterminal, 'sealed_at': now}
			db.execute('INSERT INTO controller_replan_seal VALUES(?,?,?,?,?,NULL)', (seal_id, trigger['trigger_id'], 'SEALED', canonical(record).decode(), now))
			return deepcopy(record)

	def replan_context(self):
		with self.connection() as db:
			prior = db.execute("SELECT value FROM controller_meta WHERE key='last_inventory_sha256'").fetchone()
			rows = [dict(row) for row in db.execute("SELECT dispatch_id,ticket,stream_id AS stream,exact_tuple,status FROM controller_dispatch WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN','LAUNCHED') ORDER BY dispatch_id")]
			return {'prior_inventory_sha256': None if prior is None else prior[0], 'nonterminal': rows}

	@staticmethod
	def _round_boundary_seal(db, tickets, inventory_sha256):
		inventory = {item['ticket']: item for item in tickets}
		nonterminal = list(db.execute("SELECT dispatch_id,ticket,stream_id,exact_tuple,status FROM controller_dispatch WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN','LAUNCHED') ORDER BY dispatch_id"))
		prior_inventory = db.execute("SELECT value FROM controller_meta WHERE key='last_inventory_sha256'").fetchone()
		inventory_changed = prior_inventory is not None and inventory_sha256 is not None and prior_inventory[0] != inventory_sha256
		boundary = [row for row in nonterminal if inventory_changed or row['ticket'] not in inventory or inventory[row['ticket']]['exact_tuple'] != row['exact_tuple'] or inventory[row['ticket']]['disposition'] != 'ELIGIBLE']
		if not boundary:
			return (False, None, False)
		if inventory_sha256 is None:
			return (True, None, False)
		current = [{'dispatch_id': row['dispatch_id'], 'ticket': row['ticket'], 'stream': row['stream_id'], 'exact_tuple': row['exact_tuple'], 'status': row['status']} for row in nonterminal]
		ids = [row['dispatch_id'] for row in current]
		for row in db.execute("SELECT seal_id,record_json FROM controller_replan_seal WHERE status='SEALED' ORDER BY rowid"):
			record = loads(row['record_json'])
			trigger = record['trigger']
			if trigger['next_inventory_sha256'] == inventory_sha256 and trigger['nonterminal_dispatch_ids'] == ids and record.get('nonterminal') == current:
				return (True, row['seal_id'], False)
		for row in db.execute("SELECT seal_id,record_json FROM controller_replan_seal WHERE status='CONSUMED' ORDER BY rowid DESC"):
			record = loads(row['record_json'])
			admission = record.get('admission')
			if not isinstance(admission, dict) or admission.get('inventory_sha256') != inventory_sha256:
				continue
			intent_ids = admission.get('successor_dispatch_ids')
			if not isinstance(intent_ids, list) or intent_ids != sorted(intent_ids) or len(intent_ids) != len(set(intent_ids)):
				continue
			observed_ids = [item['dispatch_id'] for item in db.execute("SELECT dispatch_id FROM controller_dispatch WHERE dispatch_id IN (%s) ORDER BY dispatch_id" % ','.join('?' for _ in intent_ids), intent_ids)] if intent_ids else []
			if observed_ids == intent_ids:
				return (True, row['seal_id'], True)
		return (True, None, False)

	def schedule(self, inventory, now, host_capacity, overlap_allowances=(), *, reservations=(), inventory_sha256=None):
		timestamp(now)
		_require(type(host_capacity) is int and host_capacity >= 0, 'Observed host capacity must be nonnegative')
		tickets = [_ticket(item) for item in inventory]
		_require(len(tickets) == len({item['ticket'] for item in tickets}), 'Scoped inventory has duplicate tickets')
		validated_reservations = [_reservation_record(item) for item in reservations]
		with self.transaction() as db:
			boundary, seal_id, admitted = self._round_boundary_seal(db, tickets, inventory_sha256)
			if boundary and seal_id is None:
				return self._snapshot(db)
			if admitted:
				return self._snapshot(db)
			self._schedule(db, tickets, now, host_capacity, overlap_allowances, validated_reservations)
			if seal_id is not None:
				intent_ids = self._create_dispatch_intents(db, now)
				row = db.execute("SELECT record_json FROM controller_replan_seal WHERE seal_id=? AND status='SEALED'", (seal_id,)).fetchone()
				_require(row is not None, 'Authenticated replan seal changed during successor admission')
				record = loads(row['record_json'])
				record.update(status='CONSUMED', consumed_at=now, admission={'inventory_sha256': inventory_sha256, 'successor_dispatch_ids': intent_ids})
				db.execute("UPDATE controller_replan_seal SET status='CONSUMED',record_json=?,consumed_at=? WHERE seal_id=? AND status='SEALED'", (canonical(record).decode(), now, seal_id))
			if inventory_sha256 is not None:
				_require(re.fullmatch('[0-9a-f]{64}', inventory_sha256) is not None, 'Inventory digest is malformed')
				db.execute("INSERT OR REPLACE INTO controller_meta VALUES('last_inventory_sha256',?)", (inventory_sha256,))
			return self._snapshot(db)

	def round_boundary_ready(self, inventory, inventory_sha256):
		tickets = [_ticket(item) for item in inventory]
		with self.connection() as db:
			boundary, seal_id, admitted = self._round_boundary_seal(db, tickets, inventory_sha256)
			return (not boundary) or seal_id is not None or admitted

	def _schedule(self, db, tickets, now, host_capacity, overlap_allowances=(), reservations=()):
		rows = db.execute('SELECT * FROM stream_status ORDER BY stream_id').fetchall()
		inventory = {item['ticket']: item for item in tickets}
		reserved_paths = [(item['kind'] + ':' + item['artifact_id'], item['paths']) for item in reservations]
		for item in tickets:
			prior = db.execute('SELECT dispatch_role,lifecycle_stage FROM controller_ticket_authority WHERE ticket=? AND exact_tuple=?', (item['ticket'], item['exact_tuple'])).fetchone()
			_require(prior is None or (prior['dispatch_role'], prior['lifecycle_stage']) == (item['dispatch_role'], item['lifecycle_stage']), 'Ticket dispatch authority changed without a new exact tuple')
			db.execute('INSERT OR IGNORE INTO controller_ticket_authority VALUES(?,?,?,?,?)', (item['ticket'], item['exact_tuple'], item['dispatch_role'], item['lifecycle_stage'], now))
		reserved_dispatches, capacity_intents, unknown_dispatches = ({}, [], {})
		for intent in db.execute("SELECT dispatch_id,ticket,stream_id,exact_tuple,status FROM controller_dispatch WHERE status IN ('PENDING','IN_FLIGHT','UNKNOWN','LAUNCHED')"):
			reserved_dispatches.setdefault(intent['ticket'], set()).add(intent['stream_id'])
			if intent['status'] in {'IN_FLIGHT', 'UNKNOWN', 'LAUNCHED'}:
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
			reservation_blocked = item is not None and any((_overlap(item['paths'], paths) for _source, paths in reserved_paths))
			if prerequisites and intent_matches_stream and (not reservation_blocked) and (len(working) < host_capacity) and (not any((_blocking_overlap(item['ticket'], item['paths'], held_ticket, paths, overlap_allowances) for held_ticket, paths in held_paths))):
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
				if prerequisites and slots > 0 and (not any((_overlap(item['paths'], paths) for _source, paths in reserved_paths))) and (not any((_blocking_overlap(item['ticket'], item['paths'], held_ticket, paths, overlap_allowances) for held_ticket, paths in held_paths))):
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
				if any((_overlap(item['paths'], paths) for _source, paths in reserved_paths)):
					reason.append('authenticated parked or worktree-diff reservation')
				blocked_item = {**item, 'reason': 'Blocked by ' + ', '.join(reason), 'next_action': 'Re-evaluate eligibility without weakening governance', 'resume_trigger': 'all named prerequisites become current'}
				self._update_stream(db, row['stream_id'], 'BLOCKED', blocked_item, now)
			elif paused:
				self._update_stream(db, row['stream_id'], 'PAUSED_INPUT', paused.pop(0), now)
			elif blocked:
				self._update_stream(db, row['stream_id'], 'BLOCKED', blocked.pop(0), now)
			else:
				self._update_stream(db, row['stream_id'], 'COMPLETE', None, now)

	def finish_and_refill(self, stream_id, ticket, inventory, now, host_capacity, overlap_allowances=(), *, reservations=(), inventory_sha256=None):
		timestamp(now)
		tickets = [_ticket(item) for item in inventory]
		repeated = next((item for item in tickets if item['ticket'] == ticket), None)
		_require(repeated is None or repeated['disposition'] == 'COMPLETE', 'Completed ticket remains dispatch-eligible; record its terminal lifecycle state before refill')
		with self.transaction() as db:
			row = db.execute('SELECT * FROM stream_status WHERE stream_id=?', (stream_id,)).fetchone()
			_require(row is not None and row['state'] == 'WORKING' and (row['ticket'] == ticket), "Completed run does not match the stream's active ticket")
			self._update_stream(db, stream_id, 'COMPLETE', None, now)
			boundary, seal_id, admitted = self._round_boundary_seal(db, tickets, inventory_sha256)
			_require(not boundary or seal_id is not None, 'Round-boundary refill requires an authenticated durable replan seal')
			_require(not admitted, 'Round-boundary refill admission was already consumed')
			self._schedule(db, tickets, now, host_capacity, overlap_allowances, [_reservation_record(item) for item in reservations])
			if seal_id is not None:
				intent_ids = self._create_dispatch_intents(db, now)
				seal = db.execute("SELECT record_json FROM controller_replan_seal WHERE seal_id=? AND status='SEALED'", (seal_id,)).fetchone()
				_require(seal is not None, 'Authenticated replan seal changed during successor admission')
				record = loads(seal['record_json'])
				record.update(status='CONSUMED', consumed_at=now, admission={'inventory_sha256': inventory_sha256, 'successor_dispatch_ids': intent_ids})
				db.execute("UPDATE controller_replan_seal SET status='CONSUMED',record_json=?,consumed_at=? WHERE seal_id=? AND status='SEALED'", (canonical(record).decode(), now, seal_id))
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

	def digest(self, now, *, calendar=None):
		current = timestamp(now)
		with self.transaction() as db:
			pending = db.execute("SELECT payload_json FROM controller_outbox WHERE acknowledged_at IS NULL AND kind<>'REPORT' ORDER BY rowid LIMIT 1").fetchone()
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
			from .review_batching import ReviewBatchStore
			reviews = ReviewBatchStore.view_from_db(db, now, calendar or {})
			all_complete = all((s['state'] == 'COMPLETE' for s in streams))
			if all_complete and (not change_due) and not (regular_due and reviews['owner_alerts']):
				return None
			if not regular_due and (not change_due):
				return None
			kind = 'REGULAR' if regular_due else 'CHANGE'
			from .canonical import fingerprint
			body = {'schema_version': 3, 'observed_at': now, 'kind': kind, 'cadence_seconds': cadence, 'all_complete': all_complete, 'streams': streams, 'unresolved_dispatches': unresolved_dispatches, 'review_batching': reviews}
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

	@staticmethod
	def _create_dispatch_intents(db, now):
		from .canonical import fingerprint
		created = []
		for row in db.execute("SELECT * FROM stream_status WHERE state='WORKING' ORDER BY stream_id"):
			prior = db.execute("SELECT * FROM controller_dispatch WHERE stream_id=? AND ticket=? AND exact_tuple=? AND status<>'CANCELLED' ORDER BY rowid DESC LIMIT 1", (row['stream_id'], row['ticket'], row['exact_tuple'])).fetchone()
			if prior is not None:
				continue
			payload = {'dispatch_schema_version': DISPATCH_SCHEMA_VERSION, 'stream': row['stream_id'], 'ticket': row['ticket'], 'exact_tuple': row['exact_tuple'], 'paths': loads(row['paths']), 'actor': row['actor'], 'next_action': row['next_action'], 'dispatch_nonce': str(uuid.uuid4()), 'prepared_at': now, 'lane_start_waivers': []}
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
			created.append(dispatch_id)
		return sorted(created)

	def prepare_dispatches(self, now):
		timestamp(now)
		with self.transaction() as db:
			self._create_dispatch_intents(db, now)
			return [dict(row) | {'payload': loads(row['payload_json'])} for row in db.execute("SELECT * FROM controller_dispatch WHERE status IN ('PENDING','UNKNOWN','LAUNCHED') ORDER BY stream_id,dispatch_id")]

	def bind_dispatch_waivers(self, allowance_records, now):
		from .canonical import fingerprint
		timestamp(now)
		_require(isinstance(allowance_records, list), 'Lane-start waiver binding needs validated allowance records')
		by_ticket = {}
		for record in allowance_records:
			_require(isinstance(record, dict) and record.get('schema_version') == 1 and isinstance(record.get('binding'), dict), 'Lane-start waiver source is malformed')
			for ticket in record['binding'].get('tickets', []):
				by_ticket.setdefault(ticket, []).append(record)
		with self.transaction() as db:
			for row in db.execute("SELECT dispatch_id,payload_json FROM controller_dispatch WHERE status='PENDING' ORDER BY dispatch_id"):
				payload = loads(row['payload_json'])
				digests = []
				for allowance in by_ticket.get(payload['ticket'], []):
					binding = allowance['binding']
					if binding['path'] not in payload['paths']:
						continue
					waiver_binding = {'dispatch_id': row['dispatch_id'], 'ticket': payload['ticket'], 'stream': payload['stream'], 'exact_tuple': payload['exact_tuple'], 'allowance_id': allowance['allowance_id'], 'path': binding['path'], 'tickets': binding['tickets']}
					waiver = {'schema_version': 1, 'binding': waiver_binding, 'persisted_at': now, 'observation_sha256': allowance['provider_binding_sha256']}
					waiver['waiver_sha256'] = fingerprint('controller-dispatch-waiver', waiver)
					waiver_id = fingerprint('controller-dispatch-waiver-id', waiver_binding)
					prior = db.execute('SELECT record_json FROM controller_dispatch_waiver WHERE waiver_id=?', (waiver_id,)).fetchone()
					if prior is not None:
						retained = loads(prior['record_json'])
						_require(retained.get('binding') == waiver_binding, 'Dispatch waiver scope changed before launch')
						waiver = retained
					else:
						db.execute('INSERT INTO controller_dispatch_waiver VALUES(?,?,?,?,?)', (waiver_id, row['dispatch_id'], allowance['allowance_id'], canonical(waiver).decode(), now))
					digests.append(waiver['waiver_sha256'])
				payload['lane_start_waivers'] = sorted(digests)
				db.execute('UPDATE controller_dispatch SET payload_json=?,updated_at=? WHERE dispatch_id=?', (canonical(payload).decode(), now, row['dispatch_id']))

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
			_require(payload.get('dispatch_schema_version') == DISPATCH_SCHEMA_VERSION, 'Dispatch payload schema version is not current')
			waivers = sorted(loads(item['record_json'])['waiver_sha256'] for item in db.execute('SELECT record_json FROM controller_dispatch_waiver WHERE dispatch_id=? ORDER BY allowance_id', (dispatch_id,)))
			_require(payload.get('lane_start_waivers') == waivers, 'Dispatch lane-start waiver binding is missing or mismatched')
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
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Codex skill selection must precede dispatch begin')
			payload = loads(row['payload_json'])
			if 'dispatch_schema_version' not in payload:
				compatibility_fields = {'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at'}
				_require(set(payload) == compatibility_fields and row['receipt_json'] is None and payload['dispatch_id'] == row['dispatch_id'] and payload['stream'] == row['stream_id'] and payload['ticket'] == row['ticket'] and payload['exact_tuple'] == row['exact_tuple'], 'Pre-versioned dispatch skill-selection payload is ambiguous and was not migrated')
				payload.update(dispatch_schema_version=DISPATCH_SCHEMA_VERSION, lane_start_waivers=[])
			else:
				_require(payload.get('dispatch_schema_version') == DISPATCH_SCHEMA_VERSION, 'Dispatch payload schema version is not current')
			prior = payload.get('codex_skill_selection')
			_require(prior is None or prior == encoded, 'Codex skill selection changed for a durable dispatch intent')
			payload['codex_skill_selection'] = encoded
			db.execute('UPDATE controller_dispatch SET payload_json=?,updated_at=? WHERE dispatch_id=?', (canonical(payload).decode(), now, dispatch_id))
		return encoded

	def finish_dispatch(self, dispatch_id, receipt, now, *, reconcile=False):
		timestamp(now)
		bound = {'dispatch_schema_version', 'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'role', 'lifecycle_stage', 'lane_start_waivers'}
		if reconcile:
			bound |= {'reconcile_nonce', 'reconcile_at'}
		required = bound | {'status', 'dispatch_state', 'terminal', 'observed_at', 'session_id'}
		_require(isinstance(receipt, dict) and set(receipt) == required and (receipt['status'] == 'ACCEPTED') and receipt['dispatch_state'] in {'LAUNCHED', 'TERMINAL'} and (receipt['terminal'] is (receipt['dispatch_state'] == 'TERMINAL')), 'Dispatch receipt is not an exact versioned readback')
		_require(isinstance(receipt['session_id'], str) and receipt['session_id'].strip() and (len(receipt['session_id']) <= 512) and (not any((ord(char) < 32 for char in receipt['session_id']))), 'Dispatch receipt has no observed session identity')
		observed_at = timestamp(receipt['observed_at'])
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			expected = {'UNKNOWN', 'LAUNCHED'} if reconcile else {'IN_FLIGHT'}
			_require(row is not None and row['status'] in expected, 'Dispatch receipt does not match the durable operation state')
			payload = loads(row['payload_json'])
			_require(all((receipt[key] == payload[key] for key in bound)), 'Dispatch receipt differs from the durable intent')
			boundary = timestamp(payload['reconcile_at'] if reconcile else payload['begun_at'])
			_require(observed_at >= boundary, 'Dispatch receipt is stale relative to the durable intent or reconciliation')
			for accepted in db.execute("SELECT dispatch_id,receipt_json FROM controller_dispatch WHERE status IN ('LAUNCHED','TERMINAL') AND dispatch_id<>?", (dispatch_id,)):
				prior = loads(accepted['receipt_json'])
				_require(prior.get('session_id') != receipt['session_id'], 'Dispatch session identity is not fresh or writer/critic independent')
			durable_state = receipt['dispatch_state']
			db.execute('UPDATE controller_dispatch SET status=?,receipt_json=?,updated_at=? WHERE dispatch_id=?', (durable_state, canonical(receipt).decode(), now, dispatch_id))
			if reconcile and row['status'] == 'UNKNOWN':
				self._restore_unknown_stream(db, dispatch_id, payload, now)
				revision = int(self._meta(db, 'revision')) + 1
				db.execute("UPDATE controller_meta SET value=? WHERE key='revision'", (str(revision),))
			return receipt

	def mark_dispatch_unknown(self, dispatch_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?', (dispatch_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN', 'LAUNCHED'}, 'Only an in-flight, launched, or reconciling dispatch can report an unknown outcome')
			if row['status'] == 'IN_FLIGHT':
				db.execute("UPDATE controller_dispatch SET status='UNKNOWN',updated_at=? WHERE dispatch_id=?", (now, dispatch_id))
			if row['status'] != 'LAUNCHED':
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
			_require(row is not None and row['status'] in {'UNKNOWN', 'LAUNCHED'}, 'Only an unknown or launched dispatch can be reconciled')
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

	def prepare_jira_operation(self, record, now, *, identity=None):
		timestamp(now)
		from .canonical import fingerprint
		_require(isinstance(record, dict) and isinstance(record.get('operation_id'), str) and isinstance(record.get('binding'), dict) and isinstance(record['binding'].get('issue_id'), str), 'Jira operation intent is malformed')
		immutable_intent = {key: value for key, value in record.items() if key != 'created_at'}
		intent_sha256 = fingerprint('controller-jira-operation', immutable_intent)
		encoded = canonical(record).decode()
		with self.transaction() as db:
			_require(_stopped_batch_jira_operation(db, record['binding']['issue_id']) is None, 'A durable batch Jira operation is unresolved or failed; writes remain stopped')
			prior = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			if prior is None:
				db.execute('INSERT INTO controller_jira_operation VALUES(?,?,?,?,?,?,NULL,NULL,?)', (record['operation_id'], record['binding']['issue_id'], record['lifecycle_event'], intent_sha256, 'PENDING', encoded, now))
				if identity is not None:
					_pin_jira_identity(db, record['operation_id'], intent_sha256, identity)
			else:
				_require(prior['intent_sha256'] == intent_sha256, 'Jira operation identity collided with a different durable intent')
			row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			return dict(row) | {'record': loads(row['record_json'])}

	def begin_jira_operation(self, operation_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] == 'PENDING', 'Jira operation is not pending')
			_require(_stopped_batch_jira_operation(db, row['issue_id']) is None, 'A durable batch Jira operation is unresolved or failed; writes remain stopped')
			db.execute("UPDATE controller_jira_operation SET status='IN_FLIGHT',updated_at=? WHERE operation_id=?", (now, operation_id))
			return loads(row['record_json'])

	def mark_jira_unknown(self, operation_id, now):
		timestamp(now)
		with self.transaction() as db:
			row = db.execute('SELECT status FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN'}, 'Only an in-flight or unknown Jira operation can remain unknown')
			db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE operation_id=?", (now, operation_id))

	def recover_jira_operations(self, now, *, issue_id=None):
		"""Abandon interrupted claims; production supplies the exclusively owned issue."""
		timestamp(now)
		with self.transaction() as db:
			claims = db.execute("SELECT operation_id FROM controller_jira_operation WHERE status='IN_FLIGHT'" +
				(' AND issue_id=?' if issue_id is not None else ''), (issue_id,) if issue_id is not None else ()).fetchall()
			for claim in claims:
				with _jira_claim_ownership(self, claim['operation_id']) as abandoned:
					_require(abandoned, 'Jira claim owner is still live; recovery cannot take over')
			if issue_id is None:
				db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE status='IN_FLIGHT'", (now,))
			else:
				db.execute("UPDATE controller_jira_operation SET status='UNKNOWN',updated_at=? WHERE status='IN_FLIGHT' AND issue_id=?", (now, issue_id))

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

	def finish_batch_jira_operation(self, operation_id, status, observation, now, operation=None):
		timestamp(now)
		_require(status in {'SUCCEEDED', 'FAILED', 'UNKNOWN'}, 'Invalid batch Jira operation result')
		with self.transaction() as db:
			row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
			_require(row is not None and row['status'] in {'IN_FLIGHT', 'UNKNOWN'}, 'Batch Jira result does not match durable state')
			db.execute('UPDATE controller_batch_jira_operation SET status=?,operation_json=?,readback_json=?,updated_at=? WHERE operation_id=?', (status, canonical(operation).decode() if operation is not None else row['operation_json'], canonical(observation).decode() if observation is not None else None, now, operation_id))


def _require_canonical_issue(issue, *, observe_issue_identity=None, now):
	"""Reject aliases; never translate an input into another lock/stop identity.

	The historical T11 fixture accepts canonical keys syntactically. Reviewed
	production entrypoints additionally resolve each key through the provider.
	"""
	_require(isinstance(issue, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*', issue) is not None,
		'Jira input must be a canonical issue key')
	if observe_issue_identity is not None:
		_require(callable(observe_issue_identity), 'Canonical issue observer is unavailable')
		observed = observe_issue_identity(issue)
		_require(isinstance(observed, dict) and set(observed) == {'issue_key', 'observed_at'} and
			observed['issue_key'] == issue, 'Jira input differs from the provider canonical issue key')
		from .canonical import fresh
		fresh(observed['observed_at'], now, 1800)
	return issue


def batch_operation_request(request):
	"""A locator/operation only; evidence and registrations never come from JSON."""
	_require(isinstance(request, dict) and set(request) == {'operation', 'repository_id', 'batch_id'},
		'Batch command accepts only a retained candidate locator and operation')
	_require(request['operation'] in {'load', 'schedule', 'publish', 'ready', 'ci', 'eject'},
		'Unsupported batch operation; no merge or target push authority')
	_require(type(request['repository_id']) is int and request['repository_id'] > 0 and
		isinstance(request['batch_id'], str) and request['batch_id'].strip() == request['batch_id'] and
		bool(request['batch_id']) and len(request['batch_id']) <= 256,
		'Invalid retained batch locator')
	return deepcopy(request)


def production_batch_operation(*, config, request, coordinator):
	"""Delegate explicit operations to W1's reviewed retained coordinator only."""
	request = batch_operation_request(request)
	_require(request['repository_id'] == config.get('github', {}).get('repository_id'),
		'Batch repository differs from accepted configuration')
	_require(callable(coordinator), 'Batch command requires the reviewed W1 coordinator')
	candidate = coordinator(deepcopy(request))
	from .closeout import validate_retained_batch_candidate
	validate_retained_batch_candidate(candidate, repository_id=request['repository_id'], batch_id=request['batch_id'])
	return deepcopy(candidate)


def production_batch_closeout(store, *, config, request, coordinator,
		merge_observer_id, merge_observer_registry, observe_issue_identity,
		observe_provider_identity, observe_issue_policy, now, read_current_status, write_transition,
		read_transition, observe_jira_operation_identity=None):
	"""Observe a retained W1 candidate on exact main, then use only T11.

	No publication/audit journal is created here. Coordinator registrations and
	observer registries are runtime objects supplied by reviewed adapter code.
	Caller-supplied JSON cannot authenticate a merge or a candidate.
	"""
	request = batch_operation_request(request)
	_require(request['operation'] == 'load', 'Batch closeout requires a retained load')
	candidate = production_batch_operation(config=config, request=request, coordinator=coordinator)
	from .closeout import assemble_retained_batch_closeout
	batch, observed_merge = assemble_retained_batch_closeout(candidate, config=config,
		merge_observer_id=merge_observer_id, merge_observer_registry=merge_observer_registry, now=now)
	# Disabled Jira forbids even identity lookups. Counting still requires the
	# independent closeout gate; a T11 complete result never grants authority.
	policy_context = {}
	if config.get('jira', {}).get('enabled') is True:
		from .provider_identity import require_jira_write_identity
		from .canonical import fresh
		from .integration_queue import _evidence_max_age
		_require(all(callable(adapter) for adapter in (observe_provider_identity, observe_issue_identity, observe_issue_policy)),
			'Batch Jira needs reviewed provider, canonical issue and policy observers')
		require_jira_write_identity(config, observe_provider_identity())
		for member in batch['constituents']:
			issue = member['binding']['issue_id']
			_require_canonical_issue(issue, observe_issue_identity=observe_issue_identity, now=now)
			policy = observe_issue_policy(issue)
			_require(isinstance(policy, dict) and set(policy) == {'issue_key', 'issue_type', 'snapshot', 'observed_at'} and
				policy['issue_key'] == issue, 'Batch Jira policy observation differs from canonical issue')
			fresh(policy['observed_at'], now, _evidence_max_age(config))
			policy_context[issue] = deepcopy({key: policy[key] for key in ('issue_type', 'snapshot')})
	# Check retained identity again after external observations, before T11 can
	# prepare any intents; W1 remains the sole authority for retained bytes.
	_require(production_batch_operation(config=config, request=request, coordinator=coordinator) == candidate,
		'Retained batch changed during closeout observation')
	def guarded_write(record):
		require_jira_write_identity(config, observe_provider_identity())
		_require_canonical_issue(record['binding']['issue_id'], observe_issue_identity=observe_issue_identity, now=now)
		policy = _observe_jira_write_policy(config, record['binding']['issue_id'], observe_issue_policy, now)
		_require(policy['issue_type'] == 'LEAF', 'Epics are never transitioned')
		from .jira_lifecycle import owner_closure_required
		_require(not owner_closure_required(config, policy['snapshot']), 'Owner closure is required before the Done write')
		require_jira_binding_identity(store, config, record['binding'], observe_jira_operation_identity)
		return write_transition(record)
	result = production_batch_jira_reconciliation(store, config=config, role='controller',
		batch=batch, observed_merge=observed_merge, now=now, read_current_status=read_current_status,
		write_transition=guarded_write, read_transition=read_transition, policy_context=policy_context,
		observe_issue_identity=observe_issue_identity, observe_jira_operation_identity=observe_jira_operation_identity)
	return {'batch_id': candidate['batch_id'], 'generation': candidate['generation'],
		'constituents': deepcopy(batch['constituents']), 'jira': result,
		'count_allowed': False, 'execution_authority': False}


def _batch_jira_inputs(config, batch, observed_merge, policy_context, now):
	"""Validate the complete fixture envelope before persisting or calling out."""
	def text(value):
		return isinstance(value, str) and value and value.strip() == value and len(value) <= 256 and not any(ord(c) < 32 for c in value)
	def sha(value):
		return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None
	_require(isinstance(config, dict) and isinstance(config.get('jira'), dict), 'Batch Jira configuration is missing')
	jira = config['jira']
	_require(jira.get('enabled') is True and all(text(jira.get(key)) for key in
		('cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id')), 'Batch Jira provider identity is incomplete')
	_require(isinstance(config.get('github'), dict) and text(config['github'].get('base_branch')), 'Batch target branch is missing')
	_require(isinstance(jira.get('status_map'), dict) and text(jira['status_map'].get('done')), 'Batch Jira Done mapping is missing')
	_require(isinstance(jira.get('lifecycle_writes', {}), dict) and
		all(type(value) is bool for value in jira.get('lifecycle_writes', {}).values()), 'Batch lifecycle switches must be boolean')
	start = timestamp(now)
	_require(isinstance(batch, dict) and set(batch) == {'batch_id', 'pr_number', 'merge_commit_sha', 'constituents'}, 'Malformed batch reconciliation input')
	_require(text(batch['batch_id']) and type(batch['pr_number']) is int and batch['pr_number'] > 0 and sha(batch['merge_commit_sha']), 'Invalid batch identity')
	_require(isinstance(observed_merge, dict) and set(observed_merge) == {'batch_id', 'target_branch', 'merge_commit_sha', 'observed_at'}, 'Malformed batch merge observation')
	_require(observed_merge['batch_id'] == batch['batch_id'] and observed_merge['merge_commit_sha'] == batch['merge_commit_sha'] and
		observed_merge['target_branch'] == config['github']['base_branch'], 'Batch commit has not been observed on its configured target')
	_require(timestamp(observed_merge['observed_at']) <= start, 'Batch merge observation is in the future')
	members = batch['constituents']
	_require(isinstance(members, list) and 0 < len(members) <= 1000, 'Batch needs a bounded nonempty ordered constituent list')
	issues, prs = set(), set()
	for member in members:
		_require(isinstance(member, dict) and set(member) == {'binding', 'pr_number', 'reviewed_head_sha', 'paths', 'closeout_kind', 'batch_id', 'merge_commit_sha'}, 'Malformed batch constituent')
		binding = member['binding']
		_require(isinstance(binding, dict) and set(binding) == {'issue_id'} and text(binding['issue_id']), 'Invalid constituent issue binding')
		_require(binding['issue_id'] not in issues and type(member['pr_number']) is int and member['pr_number'] > 0 and
			member['pr_number'] != batch['pr_number'] and member['pr_number'] not in prs, 'Duplicate or invalid constituent identity')
		issues.add(binding['issue_id'])
		prs.add(member['pr_number'])
		_require(sha(member['reviewed_head_sha']) and member['batch_id'] == batch['batch_id'] and
			member['merge_commit_sha'] == batch['merge_commit_sha'] and member['closeout_kind'] == 'merged-via-batch', 'Constituent differs from the observed batch')
		paths = member['paths']
		_require(isinstance(paths, list) and paths and all(isinstance(path, str) for path in paths) and
			len(paths) == len(set(paths)), 'Constituent paths must be unique')
		for path in paths:
			_require(_owned_path(path) == path and not path.endswith(('/*', '/**')), 'Constituent path is not canonical')
	_require(isinstance(policy_context, dict) and set(policy_context) <= issues, 'Batch policy context names a foreign issue')
	for facts in policy_context.values():
		_require(isinstance(facts, dict) and set(facts) <= {'issue_type', 'owner_closure_required', 'snapshot'}, 'Malformed constituent policy context')
		_require(facts.get('issue_type', 'LEAF') in {'LEAF', 'EPIC'} and type(facts.get('owner_closure_required', False)) is bool, 'Invalid constituent policy facts')
		if 'snapshot' in facts:
			snapshot = facts['snapshot']
			_require(isinstance(snapshot, dict) and set(snapshot) <= {'issue_id', 'project_key', 'epic_ids', 'component_ids', 'summary', 'labels'} and isinstance(snapshot.get('summary'), str) and
				isinstance(snapshot.get('labels', []), list) and all(isinstance(label, str) for label in snapshot.get('labels', [])), 'Invalid owner-closure snapshot')
	return {'config': config, 'batch': batch, 'observed_merge': observed_merge, 'policy_context': policy_context}


@contextmanager
def _batch_jira_ownership(store, *, issue_id=None):
	suffix = '.t11.lock' if issue_id is None else '.jira-issue-' + hashlib.sha256(issue_id.encode('utf-8')).hexdigest() + '.lock'
	with _jira_lock_ownership(store, suffix) as owned:
		yield owned


@contextmanager
def _jira_claim_ownership(store, operation_id):
	suffix = '.jira-claim-' + hashlib.sha256(operation_id.encode('utf-8')).hexdigest() + '.lock'
	with _jira_lock_ownership(store, suffix) as owned:
		yield owned


@contextmanager
def _jira_lock_ownership(store, suffix):
	"""Nonblocking, process-owned lock on protected controller storage.

	Never unlink the lock file: contenders must always lock the same inode.
	OS closure releases ownership even if a callback terminates the process.
	"""
	import errno
	path = store.path.with_name(store.path.name + suffix)
	_require(not path.is_symlink(), 'Batch Jira ownership path must not be a symlink')
	descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
	locked = False
	try:
		if os.fstat(descriptor).st_size == 0:
			os.write(descriptor, b'\0')
		os.lseek(descriptor, 0, os.SEEK_SET)
		try:
			if os.name == 'nt':
				import msvcrt
				msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
			else:
				import fcntl
				fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
			locked = True
		except OSError as exc:
			if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
				raise
		yield locked
	finally:
		# Closing the descriptor also releases the lock on both platforms.
		os.close(descriptor)


def _retained_jira_observation(value):
	"""Corrupt SQLite receipt storage is an unknown observation, never proof."""
	if not isinstance(value, str):
		return None
	try:
		observation = loads(value)
		return observation if isinstance(observation, dict) else None
	except (ValidationError, TypeError, ValueError):
		return None


def _jira_record_provider(record):
	provider = deepcopy(record['jira_provider'])
	if set(provider) == {'cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id'}:
		provider['project_id'] = provider.pop('provider_project_id')
	if isinstance(provider.get('site'), str):
		provider['site'] = provider['site'].rstrip('/')
	return provider


def _jira_operation_identity(config, record):
	"""Snapshot invariant authority at intent creation, never from today's config on replay."""
	return {'connector': 'jira', 'jira_provider': _jira_record_provider(record),
		'project_id': config.get('project', {}).get('id'),
		'repository_id': config.get('github', {}).get('repository_id'),
		'issue_id': record['binding']['issue_id']}


def _pin_jira_identity(db, operation_id, intent_sha256, identity):
	value = {'operation_id': operation_id, 'intent_sha256': intent_sha256, 'identity': identity}
	db.execute('INSERT INTO controller_meta VALUES(?,?)', ('jira-identity:' + operation_id, canonical(value).decode()))


def _jira_adoption_identity(config, record, now, operator):
	"""Explicit upgrade baseline, not evidence of the original operation's authority."""
	from .canonical import fingerprint
	accepted = {key: value for key, value in config.items() if key != '_awf_runtime'}
	return {'operation_id': record['operation_id'],
		'intent_sha256': fingerprint('controller-jira-operation', {key: value for key, value in record.items() if key != 'created_at'}),
		'identity': _jira_operation_identity(config, record),
		'provenance': {'kind': 'ADOPTED_LEGACY_SUCCEEDED', 'adopted_at': now,
			'source': 'OPERATOR_SUPPLIED_NOT_OBSERVED', 'operator': operator,
			'accepted_config_sha256': fingerprint('accepted-project-config', accepted)}}


def _jira_resolution_command(config, row):
	return ('jira-operation-resolve --issue ' + row['issue_id'] + ' --operation-id ' + row['operation_id']
		+ ' --expected-intent-sha256 ' + row['intent_sha256']
		+ ' --expected-project-id ' + config['project']['id']
		+ ' --expected-repository-id ' + str(config['github']['repository_id'])
		+ ' --operator OPERATOR --now TIME --disposition ABANDON_NO_REPLAY --reason REASON --provenance REFERENCE')


def adopt_legacy_jira_identity(store, config, *, expected_project_id, expected_repository_id, operator, now):
	"""Explicit upgrade acknowledgement; no provider observation or Jira mutation."""
	timestamp(now)
	_require(isinstance(operator, str) and operator.strip(), 'Identity adoption requires an operator attribution')
	_require(isinstance(expected_project_id, str) and expected_project_id and
		type(expected_repository_id) is int and expected_repository_id > 0 and
		expected_project_id == config.get('project', {}).get('id') and
		expected_repository_id == config.get('github', {}).get('repository_id'),
		'Explicit project/repository identity differs from accepted configuration')
	from .provider_identity import expected_jira_identity
	expected_jira_identity(config)
	baseline = {'project_id': expected_project_id, 'repository_id': expected_repository_id}
	with store.transaction() as db:
		prior = db.execute("SELECT value FROM controller_meta WHERE key='jira-legacy-adoption'").fetchone()
		_require(prior is None or loads(prior['value'])['identity'] == baseline, 'Legacy adoption identity cannot change')
		adopted, skipped = [], []
		for table in ('controller_jira_operation', 'controller_batch_jira_operation'):
			if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
				continue
			for raw in db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall():
				row = dict(raw)
				try:
					record = loads(raw['record_json'])
					from .canonical import fingerprint
					_require(record['operation_id'] == row['operation_id'] and record['binding']['issue_id'] == row['issue_id']
						and fingerprint('controller-jira-operation', {key: value for key, value in record.items() if key != 'created_at'}) == row['intent_sha256'],
						'Durable Jira intent is corrupt')
				except (ValidationError, KeyError, TypeError, ValueError) as exc:
					raise ValidationError(f"{row['issue_id']} operation {row['operation_id']}: corrupt retained intent; restore independently retained bytes") from exc
				key = 'jira-identity:' + row['operation_id']
				pin = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
				if pin is not None:
					try:
						_require(loads(pin['value'])['identity'] == _jira_operation_identity(config, record), 'Existing Jira identity differs from adoption')
					except (ValidationError, KeyError, TypeError, ValueError) as exc:
						raise ValidationError(f"{row['issue_id']} operation {row['operation_id']}: {exc}") from exc
					continue
				if row['status'] in {'UNKNOWN', 'FAILED', 'IN_FLIGHT', 'PENDING', 'HELD'}:
					skipped.append({'issue_id': row['issue_id'], 'operation_id': row['operation_id'],
						'status': row['status'], 'intent_sha256': row['intent_sha256'],
						'reason': row['status'] + ': legacy operation has no healthy success proof; retain receipts and never replay. Restore independent evidence or explicitly abandon.',
						'next_command': _jira_resolution_command(config, row)})
					continue
				try:
					_require_healthy_legacy_jira(row, record)
				except (ValidationError, KeyError, TypeError, ValueError) as exc:
					raise ValidationError(f"{row['issue_id']} operation {row['operation_id']}: {exc}") from exc
				identity = _jira_operation_identity(config, record)
				incoming = _jira_lifecycle_binding_identity(record['binding'], config)
				_require(all(incoming[field] == identity[field] for field in baseline), f"{row['issue_id']} operation {row['operation_id']}: legacy retained binding differs from adoption")
				provider = expected_jira_identity(config)
				_require(identity['jira_provider'] == {key: provider[source] for key, source in (
					('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'project_id'),
					('project_key', 'project_key'), ('controller_actor_id', 'account_id'))}, f"{row['issue_id']} operation {row['operation_id']}: legacy provider differs from adoption")
				value = _jira_adoption_identity(config, record, now, operator)
				db.execute('INSERT INTO controller_meta VALUES(?,?)', (key, canonical(value).decode()))
				adopted.append(row['operation_id'])
		if prior is None:
			db.execute('INSERT INTO controller_meta VALUES(?,?)', ('jira-legacy-adoption', canonical({
				'identity': baseline, 'provenance': {'source': 'OPERATOR_SUPPLIED_NOT_OBSERVED',
				'operator': operator, 'adopted_at': now}}).decode()))
	return {'adopted_operations': adopted, 'skipped_operations': skipped, 'identity': baseline, 'execution_authority': False}


def _jira_operator_resolution(store, config, row):
	"""Validate a local operator disposition; it is never provider success evidence."""
	from .canonical import fingerprint
	with store.connection() as db:
		saved = db.execute('SELECT value FROM controller_meta WHERE key=?',
			('jira-operator-resolution:' + row['operation_id'],)).fetchone()
	if saved is None:
		return None
	value = loads(saved['value'])
	before = value['original_row']
	_require(value['disposition'] == 'ABANDON_NO_REPLAY' and row == dict(before, status='HELD')
		and value['original_row_sha256'] == fingerprint('jira-resolution-original', before),
		'Operator resolution differs from retained operation bytes; ticket remains stopped')
	_require(value['identity'] == _jira_operation_identity(config, loads(row['record_json'])),
		'Operator resolution belongs to a different project/repository/issue or provider identity; ticket remains stopped')
	return value


def resolve_jira_operation(store, config, *, issue, operation_id, expected_intent_sha256,
		expected_project_id, expected_repository_id, operator, now, disposition, reason, provenance):
	"""Explicit protected-state operator action; no network, no inferred success."""
	from .canonical import fingerprint
	from .provider_identity import expected_jira_identity
	timestamp(now)
	_require_canonical_issue(issue, now=now)
	_require(disposition == 'ABANDON_NO_REPLAY' and all(isinstance(item, str) and item.strip()
		for item in (operator, reason, provenance)), 'Resolution requires explicit disposition, operator, reason and provenance')
	_require(expected_project_id == config.get('project', {}).get('id') and
		type(expected_repository_id) is int and expected_repository_id > 0 and
		expected_repository_id == config.get('github', {}).get('repository_id'),
		'Explicit resolution project/repository differs from accepted configuration')
	provider = expected_jira_identity(config)
	with _jira_claim_ownership(store, operation_id) as claim_owned:
		_require(claim_owned, 'Jira claim owner is still live; operator resolution refused')
		with _batch_jira_ownership(store, issue_id=issue) as issue_owned:
			_require(issue_owned, 'Jira issue has a live owner; operator resolution refused')
			matches = [(row, table) for row, table in _jira_ticket_operations(store, issue) if row['operation_id'] == operation_id]
			_require(len(matches) == 1, 'Resolution needs one exact ticket and operation')
			row, table = matches[0]
			record = loads(row['record_json'])
			_require(expected_intent_sha256 == row['intent_sha256'] == fingerprint('controller-jira-operation',
				{key: item for key, item in record.items() if key != 'created_at'}), 'Resolution intent digest differs from retained bytes')
			identity = _jira_operation_identity(config, record)
			incoming = _jira_lifecycle_binding_identity(record['binding'], config)
			_require(all(incoming[key] == identity[key] for key in ('project_id', 'repository_id', 'issue_id'))
				and row['issue_id'] == record['binding']['issue_id'] == issue and record['operation_id'] == operation_id
				and identity['jira_provider'] == {key: provider[source] for key, source in (
					('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'project_id'),
					('project_key', 'project_key'), ('controller_actor_id', 'account_id'))},
				'Resolution identity differs from retained operation')
			prior = _jira_operator_resolution(store, config, row)
			if prior is not None:
				_require((prior['operator'], prior['resolved_at'], prior['reason'], prior['provenance']['reference']) ==
					(operator, now, reason, provenance), 'Operator resolution is immutable')
				return {'resolution': prior, 'execution_authority': False}
			_require(row['status'] in {'UNKNOWN', 'FAILED', 'IN_FLIGHT', 'PENDING', 'HELD'}, 'Only an unresolved or held operation can be explicitly abandoned')
			_require(timestamp(now) >= timestamp(row['updated_at']), 'Operator resolution predates retained operation')
			accepted = {key: item for key, item in config.items() if key != '_awf_runtime'}
			value = {'operation_id': operation_id, 'issue_id': issue, 'intent_sha256': expected_intent_sha256,
				'disposition': disposition, 'operator': operator, 'resolved_at': now, 'reason': reason,
				'identity': identity, 'original_row': row,
				'original_row_sha256': fingerprint('jira-resolution-original', row),
				'provenance': {'source': 'OPERATOR_SUPPLIED_NOT_OBSERVED', 'reference': provenance,
					'accepted_config_sha256': fingerprint('accepted-project-config', accepted)}}
			with store.transaction() as db:
				_require(dict(db.execute(f'SELECT * FROM {table} WHERE operation_id=?', (operation_id,)).fetchone()) == row,
					'Jira operation changed during operator resolution')
				key = 'jira-identity:' + operation_id
				pin = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
				if pin is None:
					db.execute('INSERT INTO controller_meta VALUES(?,?)', (key, canonical({
						'operation_id': operation_id, 'intent_sha256': expected_intent_sha256, 'identity': identity,
						'provenance': {'kind': 'OPERATOR_ABANDONED_LEGACY_STOP', **value['provenance']}}).decode()))
				else:
					_require(loads(pin['value'])['identity'] == identity, 'Retained identity differs from operator resolution')
				db.execute('INSERT INTO controller_meta VALUES(?,?)', ('jira-operator-resolution:' + operation_id, canonical(value).decode()))
				# Preserve every receipt/hash/time byte and retain the complete former row above.
				db.execute(f"UPDATE {table} SET status='HELD' WHERE operation_id=?", (operation_id,))
			return {'resolution': value, 'execution_authority': False}


def _require_healthy_legacy_jira(row, record):
	from .canonical import fingerprint
	_require(row['status'] == 'SUCCEEDED', 'Historical Jira identity is missing; unresolved or failed legacy operations require independently retained historical evidence; never adopt current configuration')
	_require(fingerprint('controller-jira-operation', {key: value for key, value in record.items() if key != 'created_at'}) == row['intent_sha256'], 'Durable Jira intent is corrupt')
	observation = _retained_jira_observation(row['readback_json'])
	provider = _jira_record_provider(record)
	_require(isinstance(observation, dict) and observation.get('issue_id') == row['issue_id']
		and observation.get('jira_provider') == provider
		and observation.get('status', observation.get('status_id')) == record['to_status_id']
		and (observation.get('actor') == provider['controller_actor_id'] or
			row['operation_json'] is None and 'status_id' in observation)
		and timestamp(observation.get('observed_at')) >= timestamp(record['created_at']),
		'Legacy SUCCEEDED row lacks healthy retained readback evidence; identity adoption refused')
	if row['operation_json'] is not None:
		operation = _retained_jira_observation(row['operation_json'])
		_require(isinstance(operation, dict) and set(operation) == {'operation_id', 'issue_id', 'status', 'observed_at', 'jira_provider'}
			and operation['operation_id'] == record['operation_id'] and operation['issue_id'] == row['issue_id']
			and operation['jira_provider'] == provider and operation['status'] == 'ATTEMPTED'
			and timestamp(record['created_at']) <= timestamp(operation['observed_at']) <= timestamp(observation['observed_at']),
			'Legacy SUCCEEDED row lacks a healthy retained operation receipt; identity adoption refused')


def adopted_jira_reconciliation(store, config, request):
	"""Read-only proof for an explicitly adopted, retained successful Done write.

	Provider evidence files cannot opt into this compatibility route. Revalidate
	the protected row, adoption pin and original receipts for each observation.
	"""
	_require(isinstance(store, ContinuousControllerStore), 'Adopted reconciliation needs protected controller state')
	matches = [(row, loads(row['record_json'])) for row, _table in _jira_ticket_operations(store, request['issue_id'])
		if loads(row['record_json']).get('record_id') == request['transition_record_id']]
	if not matches:
		return None
	_require(len(matches) == 1, 'Adopted Jira reconciliation operation is ambiguous')
	row, record = matches[0]
	with store.connection() as db:
		pin = db.execute('SELECT value FROM controller_meta WHERE key=?', ('jira-identity:' + row['operation_id'],)).fetchone()
	if pin is None or 'provenance' not in loads(pin['value']):
		return None
	value = loads(pin['value'])
	provenance = value['provenance']
	if provenance.get('kind') != 'ADOPTED_LEGACY_SUCCEEDED':
		return None
	_require(value['operation_id'] == row['operation_id'] and value['intent_sha256'] == row['intent_sha256']
		and value['identity'] == _jira_operation_identity(config, record),
		'Adopted Jira reconciliation belongs to a different project/repository/issue or provider identity')
	_require_healthy_legacy_jira(row, record)
	_require(row['operation_id'] == record['operation_id'] and row['issue_id'] == record['binding']['issue_id']
		and row['intent_sha256'] == value['intent_sha256']
		and jira_reconciliation_binding_matches(record['binding'], request['binding']) and record['jira_provider'] == request['provider_identity']
		and record['lifecycle_event'] == 'JIRA_RECONCILED' and record['to_status_id'] == request['expected_status_id'],
		'Adopted Jira reconciliation differs from its protected operation')
	operation = _retained_jira_observation(row['operation_json'])
	readback = _retained_jira_observation(row['readback_json'])
	_require(operation is not None, 'Adopted reconciliation requires the original write receipt')
	from .jira_lifecycle import apply_read_back
	completed = apply_read_back(record, readback['status'], readback['actor'], readback['observed_at'])['record']
	return {'record': completed, 'operation': operation, 'readback': readback}


def jira_reconciliation_binding_matches(retained, current):
	"""A current closeout may carry a newer accepted policy for the same candidate."""
	return (isinstance(retained, dict) and isinstance(current, dict) and set(retained) == set(current)
		and {key: value for key, value in retained.items() if key != 'policy_hash'} ==
		{key: value for key, value in current.items() if key != 'policy_hash'})


def validate_jira_owner_closure(value, request, *, now):
	"""An observed Jira closure is evidence, never merge or mutation authorization."""
	_require(isinstance(value, dict) and set(value) == {'record', 'owner_closure'}, 'Malformed observed owner closure')
	record, proof = value['record'], value['owner_closure']
	_require(isinstance(proof, dict) and set(proof) == {'issue_id', 'jira_provider', 'status', 'from_status',
		'actor', 'history_id', 'transition_at', 'observed_at'}, 'Malformed actor-bound owner closure')
	provider = request['provider_identity']
	_require(record['record_id'] == request['transition_record_id']
		and jira_reconciliation_binding_matches(record['binding'], request['binding'])
		and record['binding']['issue_id'] == proof['issue_id'] == request['issue_id']
		and record['jira_provider'] == proof['jira_provider'] == provider
		and record['producer_id'] == provider['controller_actor_id']
		and record['lifecycle_event'] == 'JIRA_RECONCILED' and record['status'] == 'SUCCEEDED'
		and record['to_status_id'] == proof['status'] == request['expected_status_id']
		and record['from_status_id'] == proof['from_status'] != proof['status']
		and isinstance(proof['actor'], str) and proof['actor'].strip() and proof['actor'] != provider['controller_actor_id']
		and isinstance(proof['history_id'], str) and re.fullmatch('[0-9]+', proof['history_id']) is not None
		and record['read_back'] == {'observed_status': proof['status'], 'observed_actor': proof['actor'], 'observed_at': proof['observed_at']}
		and timestamp(proof['transition_at']) <= timestamp(proof['observed_at']) <= timestamp(now)
		and timestamp(record['created_at']) <= timestamp(proof['observed_at']),
		'Owner closure lacks matching actor-bound Done transition evidence')
	return value


def retained_jira_owner_closure(store, config, request):
	if not isinstance(store, ContinuousControllerStore):
		return None
	with store.connection() as db:
		row = db.execute('SELECT value FROM controller_meta WHERE key=?',
			('jira-owner-closure:' + request['transition_record_id'],)).fetchone()
	if row is None:
		return None
	value = loads(row['value'])
	require_jira_binding_identity(store, config, request['binding'])
	return {key: item for key, item in value.items() if key != 'identity'}


def _observe_owner_jira_closeout(store, config, *, binding, provider, producer_id, run_id, now,
		evidence, transition_id, merge_result_id, facts, read_current_status):
	from .jira_lifecycle import transition_record, apply_read_back, EVENT_GUARDS
	reason = 'owner_closure_required: owner closes in Jira; then run jira-lifecycle JIRA_RECONCILED to observe actor-bound Done evidence'
	try:
		_require(all(facts.get(key) is True for key in EVENT_GUARDS['JIRA_RECONCILED']), 'JIRA_RECONCILED requires verified merge and closeout guards')
		with _batch_jira_ownership(store, issue_id=binding['issue_id']) as owned:
			_require(owned, 'Jira issue has a live owner')
			_recover_jira_ticket(store, config=config, issue=binding['issue_id'], provider=provider, now=now)
			current = read_current_status({'issue_id': binding['issue_id'], 'jira_provider': provider,
				'owner_closure_status_id': config['jira']['status_map']['done']})
			proof = current.get('owner_closure')
			_require(isinstance(proof, dict), 'Status alone cannot prove owner closure; actor-bound transition evidence is unavailable')
			record = transition_record(binding, 'JIRA_RECONCILED', proof['from_status'], config['jira']['status_map']['done'],
				transition_id, jira_provider=provider, merge_result_id=merge_result_id, producer_id=producer_id,
				run_id=run_id, now=now, evidence=[*evidence, 'urn:awf:jira-owner-closure:' + proof['history_id'] + ':' + proof['observed_at']])
			record = apply_read_back(record, proof['status'], proof['actor'], proof['observed_at'])['record']
			value = {'record': record, 'owner_closure': proof}
			validate_jira_owner_closure(value, {'transition_record_id': record['record_id'], 'binding': binding,
				'issue_id': binding['issue_id'], 'provider_identity': provider, 'expected_status_id': config['jira']['status_map']['done']}, now=proof['observed_at'])
			with store.transaction() as db:
				key = 'jira-owner-closure:' + record['record_id']
				identity = _jira_operation_identity(config, record)
				prior = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
				if prior is None:
					db.execute('INSERT INTO controller_meta VALUES(?,?)', (key, canonical({**value, 'identity': identity}).decode()))
				else:
					old = loads(prior['value'])
					_require(old.pop('identity', None) == identity, 'Owner closure invariant identity changed')
					_require(all(old['owner_closure'][key] == proof[key] for key in proof if key != 'observed_at'), 'Owner closure observation changed; retain the original evidence')
					value = old
		return {'planned': False, **value, 'writes_stopped': False, 'execution_authority': False}
	except Exception as exc:
		return {'planned': False, 'status': 'HELD', 'reason': reason + '; ' + str(exc), 'writes_stopped': False, 'execution_authority': False}


def _stop_unattempted_jira(store, operation_id, now, *, retryable=False):
	# Persist the local no-write proof and its stop atomically. A crash between
	# those records must not manufacture a permanently unresolved attempt.
	with store.transaction() as db:
		row = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
		_require(row is not None and row['status'] == 'IN_FLIGHT' and row['operation_json'] is None,
			'Only an unattempted Jira claim can be locally stopped')
		key = ('jira-unattempted-retry:' if retryable else 'jira-not-attempted:') + operation_id
		db.execute('INSERT OR REPLACE INTO controller_meta VALUES(?,?)', (key, now))
		db.execute("UPDATE controller_jira_operation SET status=?,updated_at=? WHERE operation_id=?",
			('PENDING' if retryable else 'FAILED', now, operation_id))


@contextmanager
def _unattempted_jira_issue_ownership(store, issue, operation_id, now):
	ownership = _batch_jira_ownership(store, issue_id=issue)
	try:
		owned = ownership.__enter__()
	except OSError:
		_stop_unattempted_jira(store, operation_id, now, retryable=True)
		yield None
		return
	try:
		yield owned
	finally:
		ownership.__exit__(None, None, None)


def _require_jira_operation_identity(store, config, row, record, observer=None):
	key = 'jira-identity:' + row['operation_id']
	with store.connection() as db:
		pin = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
	if pin is None:
		if callable(observer):
			value = observer(deepcopy(record))
		else:
			if row['status'] != 'SUCCEEDED':
				raise ValidationError(f"{row['issue_id']} operation {row['operation_id']} ({row['status']}): legacy identity is missing; stop writers; restore independent historical evidence or run " + _jira_resolution_command(config, row))
			raise ValidationError(f"{row['issue_id']} operation {row['operation_id']}: legacy Jira identity requires the separate continuous_controller.py jira-identity-adopt command with --expected-project-id and --expected-repository-id before further writes")
	else:
		value = loads(pin['value'])
	_require(isinstance(value, dict) and set(value) in ({'operation_id', 'intent_sha256', 'identity'},
		{'operation_id', 'intent_sha256', 'identity', 'provenance'})
		and value['operation_id'] == row['operation_id']
		and (pin is not None or value['intent_sha256'] == row['intent_sha256']),
		'Historical Jira identity evidence from observe_jira_operation_identity is malformed or belongs to another operation')
	identity = value['identity']
	if value.get('provenance', {}).get('kind') == 'OPERATOR_ABANDONED_LEGACY_STOP':
		_require(_jira_operator_resolution(store, config, row) is not None, 'Operator resolution provenance is missing')
	_require(isinstance(identity, dict) and identity.get('jira_provider') == _jira_record_provider(record),
		'Durable Jira intent belongs to a different connector/site/project/actor')
	_require(identity == _jira_operation_identity(config, record),
		'Durable Jira intent belongs to a different project/repository/issue binding')
	if pin is None:
		_require('provenance' not in value, 'Runtime legacy adoption is forbidden; use continuous_controller.py jira-identity-adopt')
		with store.transaction() as db:
			# Add only missing provenance; old rows, receipts and intent hashes stay intact.
			latest = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (row['operation_id'],)).fetchone()
			if latest is None:
				latest = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (row['operation_id'],)).fetchone()
			_require(latest is not None and dict(latest) == row, 'Historical Jira operation changed during identity recovery')
			db.execute('INSERT OR IGNORE INTO controller_meta VALUES(?,?)', (key, canonical(value).decode()))
			_require(db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()['value'] == canonical(value).decode(), 'Historical Jira identity changed during recovery')
	return identity


def _jira_ticket_operations(store, issue):
	with store.connection() as db:
		rows = [(dict(row), 'controller_jira_operation') for row in db.execute(
			'SELECT * FROM controller_jira_operation WHERE issue_id=? ORDER BY rowid', (issue,))]
		if db.execute("SELECT 1 FROM sqlite_master WHERE name='controller_batch_jira_operation'").fetchone():
			rows += [(dict(row), 'controller_batch_jira_operation') for row in db.execute(
				'SELECT * FROM controller_batch_jira_operation WHERE issue_id=? ORDER BY rowid', (issue,))]
	return rows


def jira_assignment_state(store):
	_require(isinstance(store, ContinuousControllerStore), 'Jira identity needs protected controller storage')
	# Recheck containment and aliases at use, including stores reopened by callers.
	protected_state_path(store.path, (*store.worktree_roots, Path(__file__).resolve().parents[3]))
	with store.connection() as db:
		row = db.execute("SELECT value FROM controller_meta WHERE key='intake_assignment_state'").fetchone()
	return loads(row['value']) if row else {}


def save_jira_assignment(store, config, assignment, receipt, expected=None):
	"""Retain existing intake records/receipts in controller storage, not a second journal."""
	from .intake_assignment import _seal
	from .provider_identity import expected_jira_identity
	provider = expected_jira_identity(config)
	identity = {'connector': 'jira', 'jira_provider': {
		key: provider[source] for key, source in (('cloud_id', 'cloud_id'), ('site', 'site'),
		('project_id', 'project_id'), ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))},
		'project_id': config.get('project', {}).get('id'),
		'repository_id': config.get('github', {}).get('repository_id'), 'issue_id': assignment['ticket_key']}
	with store.transaction() as db:
		row = db.execute("SELECT value FROM controller_meta WHERE key='intake_assignment_state'").fetchone()
		state = loads(row['value']) if row else {}
		history = state.setdefault('tier_assignment_history', {}).setdefault(assignment['ticket_key'], [])
		prior = next((item for item in history if item['assignment_id'] == assignment['assignment_id']), None)
		record = deepcopy(prior or assignment)
		if prior is not None:
			_require(prior['jira_identity'] == identity, 'Assignment identity changed during mutation')
		if record.get('jira') is not None and record['jira'] != receipt:
			record.setdefault('jira_receipts', []).append(deepcopy(record['jira']))
		record.update(jira_identity=identity, jira=deepcopy(receipt))
		if expected is not None:
			record['jira_expected'] = deepcopy(expected)
		record = _seal(record)
		if prior is None:
			history.append(record)
		else:
			history[history.index(prior)] = record
		stops = state.setdefault('jira_assignment_write_stops', {})
		if receipt.get('writes_stopped') is True:
			stops[assignment['ticket_key']] = deepcopy(receipt)
		else:
			stops.pop(assignment['ticket_key'], None)
			if receipt.get('read_back_status') == 'VERIFIED':
				state.setdefault('tier_assignments', {})[assignment['ticket_key']] = record
		db.execute('INSERT OR REPLACE INTO controller_meta VALUES(?,?)',
			('intake_assignment_state', canonical(state).decode()))
	return record


def require_jira_ticket_writes_allowed(store, config, binding, *, assignment_id=None, operation_id=None, assignment_route=False,
		observe_jira_operation_identity=None):
	"""The shared ticket admission rule, used before every mutation route."""
	require_jira_binding_identity(store, config, binding, observe_jira_operation_identity)
	state = jira_assignment_state(store)
	stop = state.get('jira_assignment_write_stops', {}).get(binding['issue_id'])
	_require(not stop or stop.get('assignment_id') == assignment_id,
		'Jira assignment writes are stopped; use jira-lifecycle BLOCK with read_assignment recovery; never reissue')
	for row, table in _jira_ticket_operations(store, binding['issue_id']):
		# PENDING has not begun; HELD is a configured no-write decision.
		# Neither is an unresolved attempt, on any mutation route. IN_FLIGHT,
		# UNKNOWN and FAILED require recovery; unknown journal states fail closed.
		_require(row['operation_id'] == operation_id or row['status'] in {'SUCCEEDED', 'PENDING', 'HELD'},
			'Unresolved or failed Jira operation; use jira-lifecycle read_transition recovery; never reissue')


def recover_jira_assignment(store, config, issue, now, read_assignment=None, *, resume_unattempted=False):
	state = jira_assignment_state(store)
	stop = state.get('jira_assignment_write_stops', {}).get(issue)
	if not stop:
		return
	from .intake_assignment import verify_assignment_record
	record = next(item for item in state['tier_assignment_history'][issue]
		if item['assignment_id'] == stop['assignment_id'])
	verify_assignment_record(record)
	if resume_unattempted and record['jira'].get('mutation_attempted') is False and record['jira'].get('write_stage') == 'read_before':
		# Durable local proof: the first read failed before either mutation was called.
		save_jira_assignment(store, config, record, {'read_back_status': 'NOT_ATTEMPTED',
			'assignment_id': record['assignment_id'], 'observed_at': now})
		return
	_require(callable(read_assignment),
		'Jira assignment writes are stopped; use jira-lifecycle BLOCK with read_assignment recovery; never reissue')
	observation = read_assignment(deepcopy(record))
	expected = record.get('jira_expected')
	_require(isinstance(expected, dict) and isinstance(observation, dict) and set(observation) == {
		'assignment_id', 'assignment_sha256', 'issue_id', 'jira_identity', 'labels', 'comment', 'observed_at'}
		and observation['assignment_id'] == record['assignment_id']
		and observation['assignment_sha256'] == record['assignment_sha256']
		and observation['issue_id'] == issue and observation['jira_identity'] == record['jira_identity'],
		'Assignment recovery needs evidence bound to the original assignment and identity')
	_require(timestamp(observation['observed_at']) >= max(timestamp(now), timestamp(record['created_at'])),
		'Assignment recovery evidence is stale')
	comment = observation['comment']
	_require(observation['labels'] == expected['labels'] and isinstance(comment, dict)
		and set(comment) == {'id', 'body', 'author_id'} and type(comment['id']) in (str, int)
		and str(comment['id']).strip() and comment['body'] == expected['comment']
		and comment['author_id'] == record['jira_identity']['jira_provider']['controller_actor_id'],
		'Assignment recovery needs exact labels and controller-authored original comment evidence')
	# Re-read under ownership; rejected observations never replace retained receipts.
	_require(jira_assignment_state(store) == state, 'Assignment changed during recovery')
	save_jira_assignment(store, config, record, {'read_back_status': 'VERIFIED',
		'label': f"tier-{record['tier']}", 'comment_id': str(comment['id']),
		'controller_actor_id': comment['author_id'], 'recovery': observation})


def _require_jira_history_binding(config, binding, incoming, old_binding, identity, *, retain_form):
	"""Compare every retained history using the same invariant and form rules."""
	expected = {'issue_id', 'project_id', 'repository_id'}
	_require(all(incoming[field] == identity[field] for field in expected),
		'Durable Jira intent belongs to a different project/repository/issue binding')
	if set(old_binding) != {'issue_id'}:
		old_identity = _jira_lifecycle_binding_identity(old_binding, config)
		_require(all(old_identity[field] == identity[field] for field in expected),
			'Durable Jira intent belongs to a different project/repository/issue binding')
		if set(binding) != {'issue_id'}:
			# A reviewed policy revision is current admission, not a new ticket identity.
			# Arbitrary hash substitution still refuses; retain the historical bytes.
			if old_identity.get('policy_hash') != incoming.get('policy_hash'):
				from .policy import policy_hash
				from .lifecycle import definition
				_require(incoming.get('policy_hash') == policy_hash(config, definition()),
					'Jira policy binding differs from the accepted reviewed policy')
			_require({key: value for key, value in old_identity.items() if key != 'policy_hash'} ==
				{key: value for key, value in incoming.items() if key != 'policy_hash'},
				'Jira requirements or contract binding changed; reissue the ticket contract')
	# Issue-only adapter calls can observe/recover full history. Batch history
	# is issue-only; it must not prohibit a later full candidate binding.
	if retain_form and set(binding) != {'issue_id'}:
		_require(set(old_binding) == set(binding), 'Jira candidate binding has missing or unrecognized fields')


def require_jira_binding_identity(store, config, binding, observe_jira_operation_identity=None):
	"""Admit incoming authority against configuration and all retained Jira history.

	Candidate revisions cannot change project/repository/issue identity. Issue-only
	adapter bindings obtain that identity from configuration; historical authority
	always comes from the retained pin, including for completed batch operations.
	An absent journal cannot prove that no history exists, so it refuses admission.
	"""
	_require(isinstance(binding, dict) and isinstance(binding.get('issue_id'), str),
		'Jira input must bind a canonical issue key')
	issue = binding['issue_id']
	expected = {field: value for field, value in (
		('issue_id', issue), ('project_id', config.get('project', {}).get('id')),
		('repository_id', config.get('github', {}).get('repository_id')))}
	incoming = _jira_lifecycle_binding_identity(binding, config)
	_require({field: incoming[field] for field in expected} == expected,
		'Durable Jira intent belongs to a different project/repository/issue binding')
	_require(isinstance(store, ContinuousControllerStore), 'Jira identity needs protected controller storage')
	from .intake_assignment import verify_assignment_record
	from .provider_identity import expected_jira_identity
	provider = expected_jira_identity(config)
	with store.connection() as db:
		closures = [loads(row['value']) for row in db.execute(
			"SELECT value FROM controller_meta WHERE key LIKE 'jira-owner-closure:%'")]
	for value in closures:
		_require(isinstance(value, dict) and set(value) == {'identity', 'record', 'owner_closure'},
			'Owner closure history lacks retained invariant identity')
		record, identity = value['record'], value['identity']
		_require(isinstance(identity, dict) and isinstance(record, dict) and isinstance(record.get('binding'), dict),
			'Malformed owner closure identity history')
		if record['binding'].get('issue_id') != issue and identity.get('issue_id') != issue:
			continue
		_require(identity == {'connector': 'jira', **expected, 'jira_provider': {
			key: provider[source] for key, source in (('cloud_id', 'cloud_id'), ('site', 'site'),
			('project_id', 'project_id'), ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))}}
			and identity == _jira_operation_identity(config, record),
			'Owner closure history belongs to a different project/repository/issue binding or provider/controller actor')
		validate_jira_owner_closure({key: item for key, item in value.items() if key != 'identity'},
			{'transition_record_id': record['record_id'], 'binding': record['binding'], 'issue_id': issue,
				'provider_identity': identity['jira_provider'], 'expected_status_id': record['to_status_id']},
			now=value['owner_closure']['observed_at'])
		_require_jira_history_binding(config, binding, incoming, record['binding'], identity, retain_form=True)
	for record in jira_assignment_state(store).get('tier_assignment_history', {}).get(issue, []):
		verify_assignment_record(record)
		identity = record.get('jira_identity')
		_require(isinstance(identity, dict) and all(identity.get(field) == value for field, value in expected.items())
			and identity.get('connector') == 'jira' and identity.get('jira_provider') == {
				key: provider[source] for key, source in (('cloud_id', 'cloud_id'), ('site', 'site'),
				('project_id', 'project_id'), ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))},
			'Durable Jira assignment belongs to a different project/repository/issue binding or provider')
		_require_jira_history_binding(config, binding, incoming,
			record.get('binding', {'issue_id': issue}), identity, retain_form=True)
	for row, table in _jira_ticket_operations(store, issue):
		record = loads(row['record_json'])
		_jira_operator_resolution(store, config, row)
		_require(_jira_record_provider(record) == {
			key: provider[source] for key, source in (('cloud_id', 'cloud_id'), ('site', 'site'),
			('project_id', 'project_id'), ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))},
			'Durable Jira intent belongs to a different connector/site/project/actor')
		identity = _require_jira_operation_identity(store, config, row, record, observe_jira_operation_identity)
		_require_jira_history_binding(config, binding, incoming, record['binding'], identity,
			retain_form=table == 'controller_jira_operation')
	return incoming


def _jira_status_observation(value, issue, provider, floor):
	_require(isinstance(value, dict) and set(value) == {'issue_id', 'status_id', 'observed_at', 'jira_provider'}
		and value['issue_id'] == issue and value['jira_provider'] == provider
		and isinstance(value['status_id'], str) and value['status_id'].strip(), 'Jira status observation is missing or mismatched')
	_require(timestamp(value['observed_at']) >= floor, 'Jira status observation is stale')
	return value


def _recover_jira_ticket(store, *, config, issue, provider, now, read_transition=None,
		current_time=None, observe_jira_operation_identity=None, resume_unattempted=False, read_assignment=None):
	"""One resolution rule for every event and journal: read original transition, never write.

	Call under the issue ownership lock. A reporting/batch caller without a
	recovery observer gets the same durable stop and can use jira-lifecycle.
	Failed observations preserve retained bytes. Ambiguous legacy batch stops do
	not discard valid evidence resolving the original operation.
	"""
	recovered, deferred, unattempted = [], [], []
	legacy_unproven = False
	require_jira_binding_identity(store, config, {'issue_id': issue}, observe_jira_operation_identity)
	recover_jira_assignment(store, config, issue, now, read_assignment, resume_unattempted=resume_unattempted)
	rows = _jira_ticket_operations(store, issue)
	rows = [(row, table) for row, table in rows if _jira_operator_resolution(store, config, row) is None]
	for row, table in rows:
		if row['status'] in {'IN_FLIGHT', 'UNKNOWN', 'FAILED'}:
			with _jira_claim_ownership(store, row['operation_id']) as abandoned:
				_require(abandoned, 'Jira claim owner is still live; recovery cannot take over')
	if callable(read_transition) and any(row['status'] == 'IN_FLIGHT' for row, table in rows) and not any(row['status'] in {'UNKNOWN', 'FAILED'} for row, table in rows):
		# All production transition-recovery callers hold this issue's OS lock.
		# Never abandon another ticket's live claim on a controller restart.
		# Existing failed/unknown outcomes retain every other row until the
		# complete set's transition evidence has passed the resolver below.
		store.recover_jira_operations(now, issue_id=issue)
	rows = _jira_ticket_operations(store, issue)
	rows = [(row, table) for row, table in rows if _jira_operator_resolution(store, config, row) is None]
	for row, table in rows:
		record = loads(row['record_json'])
		from .canonical import fingerprint
		_require(fingerprint('controller-jira-operation', {key: value for key, value in record.items() if key != 'created_at'}) == row['intent_sha256'], 'Durable Jira intent is corrupt')
		_require(_jira_record_provider(record) == provider, 'Durable Jira intent belongs to a different connector/site/project/actor')
		_require_jira_operation_identity(store, config, row, record, observe_jira_operation_identity)
		with store.connection() as db:
			predecessor = db.execute('SELECT value FROM controller_meta WHERE key=?', ('jira-deferred:' + row['operation_id'],)).fetchone()
			no_attempt = db.execute('SELECT value FROM controller_meta WHERE key=?', ('jira-not-attempted:' + row['operation_id'],)).fetchone()
		if predecessor is None and table == 'controller_batch_jira_operation' and row['status'] == 'FAILED' and row['operation_json'] is None:
			# Legacy closeout copied the stopped predecessor's readback before
			# claiming this row. FAILED with no write receipt alone is not proof.
			priors = [old for old, _ in rows if old['operation_id'] != row['operation_id']
				and timestamp(loads(old['record_json'])['created_at']) <= timestamp(record['created_at'])
				and old['status'] in {'IN_FLIGHT', 'UNKNOWN', 'FAILED', 'SUCCEEDED'}
				and row['readback_json'] == canonical(_retained_jira_observation(old['readback_json'])).decode()]
			if len(priors) == 1:
				predecessor = {'value': priors[0]['operation_id']}
		if predecessor is not None:
			deferred.append((row, table, predecessor['value']))
			continue
		if no_attempt is not None:
			_require((resume_unattempted or table == 'controller_batch_jira_operation' and row['readback_json'] is None)
				and (row['status'] == 'FAILED' or table == 'controller_batch_jira_operation' and row['status'] == 'UNKNOWN')
				and row['operation_json'] is None,
				'Jira intent was stopped before its write; use jira-lifecycle BLOCK to resume this locally proven unattempted intent without a Jira mutation')
			unattempted.append((row, table))
			continue
		if row['status'] not in {'IN_FLIGHT', 'UNKNOWN', 'FAILED'}:
			continue
		if table == 'controller_batch_jira_operation' and row['status'] == 'FAILED' and row['operation_json'] is None:
			legacy_unproven = True
			break
		_require(callable(read_transition), 'Unresolved or failed Jira operation; use jira-lifecycle read_transition recovery; suspected external automation conflict; never reissue')
		operation = loads(row['operation_json']) if row['operation_json'] is not None else {
			'operation_id': record['operation_id'], 'issue_id': issue, 'status': 'ATTEMPTED',
			'observed_at': record['created_at'], 'jira_provider': provider}
		_require(isinstance(operation, dict) and set(operation) == {'operation_id', 'issue_id', 'status', 'observed_at', 'jira_provider'}
			and operation['operation_id'] == record['operation_id'] and operation['issue_id'] == issue
			and operation['jira_provider'] == provider and operation['status'] == 'ATTEMPTED', 'Retained Jira operation receipt is missing or mismatched')
		operation_time = timestamp(operation['observed_at'])
		floor = max(timestamp(now), current_time or timestamp(now))
		_require(timestamp(record['created_at']) <= operation_time <= floor, 'Retained Jira operation time is outside its attempt window')
		with store.connection() as db:
			conflict = db.execute('SELECT value FROM controller_meta WHERE key=?', ('jira-conflict:' + row['operation_id'],)).fetchone()
		if conflict is not None:
			floor = max(floor, timestamp(loads(conflict['value'])['observed_at']))
		observation = read_transition(deepcopy(record), deepcopy(operation))
		_require(isinstance(observation, dict) and set(observation) == {'issue_id', 'status', 'actor', 'observed_at', 'jira_provider'}
			and observation['issue_id'] == issue and observation['jira_provider'] == provider
			and observation['actor'] == provider['controller_actor_id'] and observation['status'] == record['to_status_id'],
			'Jira recovery needs complete operation- and actor-bound transition evidence; suspected external automation conflict')
		_require(timestamp(observation['observed_at']) >= floor, 'Jira recovery readback is stale')
		recovered.append((row, table, record, observation))
	if recovered or deferred or unattempted:
		with store.transaction() as db:
			for row, table, record, observation in recovered:
				latest = db.execute(f'SELECT * FROM {table} WHERE operation_id=?', (row['operation_id'],)).fetchone()
				_require(dict(latest) == row, 'Jira operation changed during recovery')
			for row, table, record, observation in recovered:
				# Retain historical success even if a later conflict is recovered.
				if row['status'] == 'FAILED' and row['readback_json'] is not None:
					db.execute('INSERT OR IGNORE INTO controller_meta VALUES(?,?)',
						('jira-prior-readback:' + row['operation_id'], row['readback_json']))
				db.execute(f"UPDATE {table} SET status='SUCCEEDED',readback_json=?,updated_at=? WHERE operation_id=?",
					(canonical(observation).decode(), observation['observed_at'], row['operation_id']))
				if table == 'controller_batch_jira_operation' and row['operation_json'] is None:
					db.execute('INSERT OR REPLACE INTO controller_meta VALUES(?,?)',
						('jira-recovered-readback:' + row['operation_id'], canonical({
							'operation_id': row['operation_id'], 'intent_sha256': row['intent_sha256'],
							'observation': observation}).decode()))
			for row, table in unattempted:
				_require(dict(db.execute(f'SELECT * FROM {table} WHERE operation_id=?', (row['operation_id'],)).fetchone()) == row, 'Unattempted Jira intent changed during recovery')
				db.execute(f"UPDATE {table} SET status='PENDING',updated_at=? WHERE operation_id=?", (now, row['operation_id']))
				db.execute('DELETE FROM controller_meta WHERE key=?', ('jira-not-attempted:' + row['operation_id'],))
			for row, table, predecessor in deferred:
				_require(dict(db.execute(f'SELECT * FROM {table} WHERE operation_id=?', (row['operation_id'],)).fetchone()) == row, 'Deferred Jira intent changed during recovery')
				prior = db.execute('SELECT status FROM controller_jira_operation WHERE operation_id=? UNION ALL SELECT status FROM controller_batch_jira_operation WHERE operation_id=?', (predecessor, predecessor)).fetchone()
				locally_resumed = any(old['operation_id'] == predecessor for old, _ in unattempted)
				_require(prior is not None and (prior['status'] == 'SUCCEEDED' or locally_resumed), 'Deferred Jira intent still needs original transition evidence')
				db.execute(f"UPDATE {table} SET status='PENDING',updated_at=? WHERE operation_id=?", (now, row['operation_id']))
				db.execute('DELETE FROM controller_meta WHERE key=?', ('jira-deferred:' + row['operation_id'],))
	# Preserve valid original recovery even when a legacy batch stop needs the owner.
	_require(not legacy_unproven,
		'Legacy batch FAILED row has no proof of an unattempted write; owner must restore independently retained journal receipts before jira-lifecycle recovery; never infer from status or reissue')
	return [(record, observation) for row, table, record, observation in recovered]


def production_batch_jira_reconciliation(store, *, config, role, batch, observed_merge, now,
		read_current_status, write_transition, read_transition, policy_context=None, observe_issue_identity=None,
		observe_jira_operation_identity=None):
	"""Serialize T11 invocations; reject aliases before any durable intent."""
	arguments = dict(config=config, role=role, batch=batch, observed_merge=observed_merge,
		now=now, read_current_status=read_current_status, write_transition=write_transition,
		read_transition=read_transition, policy_context=policy_context,
		observe_jira_operation_identity=observe_jira_operation_identity)
	if config.get('jira', {}).get('enabled') is False or role != 'controller':
		return _production_batch_jira_reconciliation(store, **arguments)
	_require(isinstance(store, ContinuousControllerStore), 'Batch Jira needs protected controller storage')
	if not callable(observe_issue_identity):
		return {'status': 'REFUSED', 'reason': 'Jira canonical issue observer is unavailable',
			'writes_stopped': True, 'complete': False, 'count_allowed': False,
			'reconciled': [], 'remaining': [], 'conflict': None, 'held': [], 'execution_authority': False}
	validated = _batch_jira_inputs(deepcopy(config), deepcopy(batch), deepcopy(observed_merge),
		deepcopy(policy_context) if policy_context is not None else {}, now)
	# Dispatch only the snapshot validated before any observer can mutate inputs.
	arguments.update(validated)
	for member in validated['batch']['constituents']:
		_require_canonical_issue(member['binding']['issue_id'], observe_issue_identity=observe_issue_identity, now=now)
	with _batch_jira_ownership(store) as ownership:
		return _production_batch_jira_reconciliation(store, **arguments, ownership=ownership)


def _production_batch_jira_reconciliation(store, *, config, role, batch, observed_merge, now,
		read_current_status, write_transition, read_transition, policy_context=None, ownership=False,
		observe_jira_operation_identity=None):
	"""Bounded T11 callback seam; deliberately unwired to CLI/live adapters.

	Role strings and supplied merge dictionaries are fixture inputs, not authenticated
	provider authority. Progress uses dedicated controller-private storage, separate
	from T8's closed immutable audit envelope. No global operation recovery occurs.
	"""
	base = {'execution_authority': False, 'count_allowed': False, 'complete': False,
		'reconciled': [], 'remaining': [], 'conflict': None, 'held': []}
	if config.get('jira', {}).get('enabled') is False or role != 'controller':
		return {**base, 'reason': 'Disabled Jira or non-controller role; no callbacks'}
	_require(isinstance(store, ContinuousControllerStore), 'Batch Jira needs protected controller storage')
	_require(all(callable(callback) for callback in (read_current_status, write_transition, read_transition)), 'Batch Jira callbacks are missing')
	# Isolate both caller-owned inputs and every callback argument from durable intent.
	inputs = _batch_jira_inputs(deepcopy(config), deepcopy(batch), deepcopy(observed_merge),
		deepcopy(policy_context) if policy_context is not None else {}, now)
	config, batch, policy_context = inputs['config'], inputs['batch'], inputs['policy_context']
	# Admit the complete batch before preparing any intent or writing any issue.
	for member in batch['constituents']:
		require_jira_binding_identity(store, config, member['binding'], observe_jira_operation_identity)
		for row, table in _jira_ticket_operations(store, member['binding']['issue_id']):
			_require(_jira_operator_resolution(store, config, row) is None or row['event'] != 'JIRA_RECONCILED',
				f"{row['issue_id']} operation {row['operation_id']}: operator abandoned this closeout event; batch must never replay it or count status as success; owner closes in Jira, then jira-lifecycle JIRA_RECONCILED observes closure")
	from .canonical import fingerprint
	from .jira_lifecycle import owner_closure_required, planned_write
	# Freshness is validated above on every invocation. Only immutable merge
	# identity belongs in the durable input pin and intent fingerprint.
	del inputs['observed_merge']['observed_at']
	encoded = canonical(inputs).decode()
	pin = 't11-batch:' + batch['batch_id']
	with store.connection() as db:
		prior = db.execute('SELECT value FROM controller_meta WHERE key=?', (pin,)).fetchone()
	if prior is not None:
		retained = loads(prior['value'])
		if isinstance(retained, dict) and isinstance(retained.get('observed_merge'), dict):
			retained['observed_merge'].pop('observed_at', None)
		_require(canonical(retained).decode() == encoded, 'Batch reconciliation input substitution refused')
		# Keep pre-fix pins and their exact intent digests readable on recovery.
		encoded = prior['value']
	records = []
	for member in batch['constituents']:
		issue = member['binding']['issue_id']
		operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, pin + ':' + issue))
		records.append({**member, 'operation_id': operation_id, 'created_at': now,
			'lifecycle_event': 'JIRA_RECONCILED', 'to_status_id': config['jira']['status_map']['done'],
			'jira_provider': {key: config['jira'][source] for key, source in (
				('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'provider_project_id'),
				('project_key', 'project_key'), ('controller_actor_id', 'controller_actor_id'))},
			'closing_comment': f"Done: PR #{member['pr_number']}, reviewed head {member['reviewed_head_sha']}, merge commit {member['merge_commit_sha']}.",
			'batch_input_sha256': hashlib.sha256(encoded.encode()).hexdigest()})
	for record in records:
		record['jira_provider']['site'] = record['jira_provider']['site'].rstrip('/')
	# Pin all ordered inputs and prepare every intent in one immediate transaction.
	# No new constructor/schema migration is needed for this controller-only seam.
	with store.transaction() as db:
		db.execute(BATCH_JIRA_DDL)
		prior = db.execute('SELECT value FROM controller_meta WHERE key=?', (pin,)).fetchone()
		_require(prior is None or prior['value'] == encoded, 'Batch reconciliation input substitution refused')
		if not ownership and prior is None:
			return {**base, 'remaining': [member['binding']['issue_id'] for member in batch['constituents']], 'reason': 'Batch Jira invocation is active'}
		if prior is None:
			db.execute('INSERT INTO controller_meta VALUES(?,?)', (pin, encoded))
		for record in records:
			intent = fingerprint('controller-jira-operation', {key: value for key, value in record.items() if key != 'created_at'})
			row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			if row is None:
				db.execute('INSERT INTO controller_batch_jira_operation VALUES(?,?,?,?,?,?,NULL,NULL,?)',
					(record['operation_id'], record['binding']['issue_id'], 'JIRA_RECONCILED', intent, 'PENDING', canonical(record).decode(), now))
				_pin_jira_identity(db, record['operation_id'], intent, _jira_operation_identity(config, record))
			else:
				stored = loads(row['record_json'])
				# Recognize the shipped legacy provider-key spelling for replay,
				# preserving original record bytes and their original digest.
				normalized = deepcopy(stored)
				normalized['jira_provider'] = _jira_record_provider(stored)
				_require(fingerprint('controller-jira-operation', {key: value for key, value in normalized.items() if key != 'created_at'}) == intent
					and row['issue_id'] == record['binding']['issue_id'] and
					row['event'] == 'JIRA_RECONCILED', 'Batch Jira intent collision')
				_require(isinstance(stored, dict) and fingerprint('controller-jira-operation',
					{key: value for key, value in stored.items() if key != 'created_at'}) == row['intent_sha256'] and
					canonical(stored).decode() == row['record_json'], 'Batch Jira stored intent is corrupt')
				record.clear()
				record.update(stored)

	def conflict(issue, observation=None, reason='Suspected external automation conflict; never reissue'):
		observation = observation if isinstance(observation, dict) else {}
		return {'issue_id': issue, 'observed_status': observation.get('status', observation.get('status_id')),
			'observed_actor': observation.get('actor'), 'observed_at': observation.get('observed_at'), 'reason': observation.get('reason', reason)}

	from .provider_identity import expected_jira_identity
	expected = expected_jira_identity(config)
	provider = {key: expected[key] for key in ('cloud_id', 'site', 'project_id', 'project_key')}
	provider['controller_actor_id'] = expected['account_id']
	def receipt(value, issue, time_floor, kind, expected_operation_id=None):
		_require(isinstance(value, dict) and value.get('issue_id') == issue, 'Batch Jira receipt names a foreign issue')
		fields = {'issue_id', 'observed_at', 'jira_provider'} | (
			{'status_id'} if kind == 'before' else {'status', 'operation_id'} if kind == 'write' else {'status', 'actor'})
		_require(set(value) == fields and value.get('jira_provider') == provider, 'Batch Jira receipt provider or shape is mismatched')
		status = value.get('status_id' if kind == 'before' else 'status')
		_require(isinstance(status, str) and status.strip(), 'Batch Jira receipt status is missing')
		_require(timestamp(value.get('observed_at')) >= time_floor, 'Batch Jira receipt is stale')
		if kind == 'write':
			_require(status == 'ATTEMPTED' and value['operation_id'] == expected_operation_id, 'Batch Jira write outcome or operation identity is unknown')
		if kind == 'after':
			_require(isinstance(value.get('actor'), str) and value['actor'], 'Batch Jira readback actor is missing')
		return status, timestamp(value['observed_at'])

	def completion_valid(row, record):
		"""Retained success needs historical proof, independently of restart time."""
		try:
			floor = timestamp(record['created_at'])
			_require(isinstance(row['readback_json'], str), 'Retained Jira readback must be JSON text')
			observation = loads(row['readback_json'])
			if row['operation_json'] is None:
				if isinstance(observation, dict) and 'actor' in observation:
					with store.connection() as db:
						proof = db.execute('SELECT value FROM controller_meta WHERE key=?', ('jira-recovered-readback:' + row['operation_id'],)).fetchone()
					_require(proof is not None and proof['value'] == canonical({
						'operation_id': row['operation_id'], 'intent_sha256': row['intent_sha256'],
						'observation': observation}).decode(), 'Retained Jira recovery proof is missing or mismatched')
					status, _ = receipt(observation, row['issue_id'], floor, 'after')
					_require(observation['actor'] == provider['controller_actor_id'], 'Retained Jira actor is mismatched')
				else:
					status, _ = receipt(observation, row['issue_id'], floor, 'before')
			else:
				_require(isinstance(row['operation_json'], str), 'Retained Jira write receipt must be JSON text')
				operation = loads(row['operation_json'])
				_, floor = receipt(operation, row['issue_id'], floor, 'write', row['operation_id'])
				status, _ = receipt(observation, row['issue_id'], floor, 'after')
				_require(observation['actor'] == provider['controller_actor_id'], 'Retained Jira actor is mismatched')
			_require(status == record['to_status_id'], 'Retained Jira target status is mismatched')
			return True
		except (ValidationError, TypeError, ValueError, KeyError):
			return False

	def retained_observation(row):
		return _retained_jira_observation(row['readback_json'])

	# Inspect the entire retained batch before any status/write/readback callback.
	# A contender may report invalid proof but may never change another owner's row.
	invalid_completion = False
	for record in records:
		with store.connection() as db:
			row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
		if row['status'] != 'SUCCEEDED' or completion_valid(row, record):
			continue
		invalid_completion = True
		if ownership:
			with _batch_jira_ownership(store, issue_id=row['issue_id']) as issue_owned:
				if issue_owned:
					with store.transaction() as db:
						current = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
						if current['status'] == 'SUCCEEDED' and not completion_valid(current, record):
							# Narrow quarantine: retain the immutable intent and both receipt bytes.
							db.execute("UPDATE controller_batch_jira_operation SET status='UNKNOWN',updated_at=? WHERE operation_id=? AND status='SUCCEEDED'", (now, record['operation_id']))

	deferred_stops = {}
	# Only the exclusive OS-lock holder can abandon a prior invocation claim.
	# The lock outlives callbacks but is released on return, exception or process
	# death; no elapsed-time guess can steal an active provider call.
	for record in records if ownership and not invalid_completion else ():
		issue, operation_id = record['binding']['issue_id'], record['operation_id']
		# Shared with the ordinary pre-writer check through durable completion.
		# A live owner is deferred, never converted into a stop by a contender.
		with _batch_jira_ownership(store, issue_id=issue) as issue_owned:
			if not issue_owned:
				continue
			try:
				# Batch execution never clears a stop from a status read. The
				# shared resolver names jira-lifecycle as its recovery admission.
				_recover_jira_ticket(store, config=config, issue=issue, provider=provider, now=now,
					observe_jira_operation_identity=observe_jira_operation_identity)
			except Exception as exc:
				# Preserve existing rows. Claim-race handling below retains its
				# predecessor binding; UNKNOWN predecessors simply defer PENDING.
				with store.connection() as db:
					unresolved = db.execute("SELECT 1 FROM controller_jira_operation WHERE issue_id=? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED') UNION ALL SELECT 1 FROM controller_batch_jira_operation WHERE issue_id=? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED')", (issue, issue)).fetchone()
				if unresolved is None:
					deferred_stops[issue] = {'reason': str(exc)}
					continue
			with store.transaction() as db:
				row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
				status = row['status']
				if status == 'IN_FLIGHT':
					db.execute("UPDATE controller_batch_jira_operation SET status='UNKNOWN',updated_at=? WHERE operation_id=? AND status='IN_FLIGHT'", (now, operation_id))
					status = 'UNKNOWN'
				if status == 'PENDING':
					prior_stop = db.execute("SELECT operation_id,status,readback_json FROM controller_batch_jira_operation WHERE issue_id=? AND operation_id<>? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED') UNION ALL SELECT operation_id,status,readback_json FROM controller_jira_operation WHERE issue_id=? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED') LIMIT 1", (issue, operation_id, issue)).fetchone()
					if prior_stop is not None:
						deferred_stops[issue] = _retained_jira_observation(prior_stop['readback_json'])
						if prior_stop['status'] == 'IN_FLIGHT':
							# Retain the existing claim-race stop, bound to the actual
							# predecessor; it is released only after evidence recovery.
							db.execute("UPDATE controller_batch_jira_operation SET status='FAILED',updated_at=? WHERE operation_id=?", (now, operation_id))
							db.execute('INSERT INTO controller_meta VALUES(?,?)', ('jira-deferred:' + operation_id, prior_stop['operation_id']))
						# This intent has attempted nothing. Defer to the original
						# operation's evidence recovery instead of creating a second stop.
						continue
					else:
						db.execute("UPDATE controller_batch_jira_operation SET status='IN_FLIGHT',updated_at=? WHERE operation_id=? AND status='PENDING'", (now, operation_id))
			if status != 'PENDING':
				if status == 'SUCCEEDED':
					continue
				_require(status in {'FAILED', 'UNKNOWN', 'HELD'}, 'Invalid durable batch Jira status')
				continue
			observation, operation, write_attempted = None, None, False
			try:
				facts = policy_context.get(issue, {})
				from .jira_lifecycle import require_jira_issue_scope
				require_jira_issue_scope(config, issue, facts.get('snapshot'))
				owner_hold = facts.get('owner_closure_required', False)
				if 'snapshot' in facts:
					owner_hold = owner_hold or owner_closure_required(config, facts['snapshot'])
				if facts.get('issue_type') == 'EPIC':
					key, reason = None, 'Epics are never transitioned'
				else:
					key, reason = planned_write(config, {'owner_closure_required': owner_hold}, 'JIRA_RECONCILED',
						{'merge_confirmed': True, 'closeout_valid': True})
				if key is None:
					with store.transaction() as db:
						db.execute("UPDATE controller_batch_jira_operation SET status='HELD',readback_json=?,updated_at=? WHERE operation_id=? AND status='IN_FLIGHT'", (canonical({'reason': reason}).decode(), now, operation_id))
					continue
				observation = read_current_status(deepcopy(record['binding']))
				current, current_time = receipt(observation, issue, timestamp(now), 'before')
				if current == record['to_status_id']:
					store.finish_batch_jira_operation(operation_id, 'SUCCEEDED', observation, now)
					continue
				require_jira_ticket_writes_allowed(store, config, record['binding'], operation_id=operation_id,
					observe_jira_operation_identity=observe_jira_operation_identity)
				write_attempted = True
				operation = write_transition(deepcopy(record))
				_, operation_time = receipt(operation, issue, current_time, 'write', operation_id)
				observation = read_transition(deepcopy(record), deepcopy(operation))
				observed, _ = receipt(observation, issue, operation_time, 'after')
				status = 'SUCCEEDED' if observed == record['to_status_id'] and observation['actor'] == config['jira']['controller_actor_id'] else 'FAILED'
				store.finish_batch_jira_operation(operation_id, status, observation, now, operation)
				if status == 'FAILED':
					break
			except Exception:
				# Persistence failure also leaves a durable claim. Do not retry any call.
				try:
					if write_attempted:
						store.finish_batch_jira_operation(operation_id, 'UNKNOWN', observation, now, operation)
					else:
						# Atomically distinguish a failed observation from an attempt.
						# Batch retry/BLOCK may resume this claim without transition proof.
						with store.transaction() as db:
							row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (operation_id,)).fetchone()
							_require(row['status'] == 'IN_FLIGHT' and row['operation_json'] is None, 'Batch pre-write claim changed')
							db.execute('INSERT INTO controller_meta VALUES(?,?)', ('jira-not-attempted:' + operation_id, now))
							db.execute("UPDATE controller_batch_jira_operation SET status='UNKNOWN',readback_json=?,updated_at=? WHERE operation_id=?",
								(canonical(observation).decode() if observation is not None else None, now, operation_id))
				except Exception:
					pass
				break
	result = deepcopy(base)
	with store.connection() as db:
		for record in records:
			row = db.execute('SELECT * FROM controller_batch_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			issue, status = record['binding']['issue_id'], row['status']
			if status == 'SUCCEEDED' and not completion_valid(row, record):
				status = 'UNKNOWN'
			if status == 'SUCCEEDED':
				result['reconciled'].append(issue)
			elif status == 'PENDING':
				if issue in deferred_stops:
					if result['conflict'] is None:
						result['conflict'] = conflict(issue, deferred_stops[issue])
				else:
					result['remaining'].append(issue)
			elif status == 'HELD':
				result['held'].append(issue)
			elif result['conflict'] is None:
				observation = retained_observation(row)
				result['conflict'] = conflict(issue, observation)
	result['complete'] = len(result['reconciled']) == len(records)
	result['writes_stopped'] = result['conflict'] is not None
	return result


def _zero_counts():
	return {'required': 0, 'completed': 0, 'acceptable': 0, 'failed': 0, 'stale': 0, 'outstanding': 0}

def _post_merge_jira_receipt(store, config, issue, now, count_gate, *, observe_provider_identity, observe_issue_identity, observe_issue_policy, read_current_status):
	"""Observe a journaled Done write; progress reporting never issues a mutation.

	The controller's jira-lifecycle command owns reconciliation. A count request
	already references that completed transition through its validated closeout.
	Legacy adapter callbacks must not create a second, unjournaled write route.
	"""
	from .provider_identity import require_jira_write_identity
	from .jira_lifecycle import owner_closure_required
	_require(isinstance(store, ContinuousControllerStore), 'Post-merge reconciliation needs the protected controller journal; run jira-lifecycle first')
	_require(all(callable(adapter) for adapter in (observe_provider_identity, observe_issue_identity, observe_issue_policy, read_current_status)), 'Post-merge reconciliation requires canonical, scope, provider and status observers')
	provider = require_jira_write_identity(config, observe_provider_identity())
	_require_canonical_issue(issue, observe_issue_identity=observe_issue_identity, now=now)
	closeout_binding = count_gate.get('closeout_record', {}).get('binding')
	_require(isinstance(closeout_binding, dict) and closeout_binding.get('issue_id') == issue,
		'Post-merge closeout names a different Jira issue')
	require_jira_binding_identity(store, config, closeout_binding)
	with _batch_jira_ownership(store, issue_id=issue) as owned:
		_require(owned, 'Post-merge Jira issue is owned by another operation')
		policy = _observe_jira_write_policy(config, issue, observe_issue_policy, now)
		_require(policy['issue_type'] == 'LEAF', 'Epics are never reconciled')
		owner_request = {'issue_id': issue, 'binding': closeout_binding, 'provider_identity': provider,
			'transition_record_id': count_gate.get('closeout_record', {}).get('jira_transition_record_id'),
			'expected_status_id': config['jira']['status_map']['done']}
		owner = retained_jira_owner_closure(store, config, owner_request)
		if owner is not None:
			_recover_jira_ticket(store, config=config, issue=issue, provider=provider, now=now)
			validate_jira_owner_closure(owner, owner_request, now=now)
			current = read_current_status({'issue_id': issue, 'jira_provider': deepcopy(provider),
				'owner_closure_status_id': config['jira']['status_map']['done']})
			live_proof = current.pop('owner_closure', None)
			_jira_status_observation(current, issue, provider, timestamp(now))
			_require(isinstance(live_proof, dict) and
				{key: value for key, value in live_proof.items() if key != 'observed_at'} ==
				{key: value for key, value in owner['owner_closure'].items() if key != 'observed_at'},
				'Owner closure differs from the latest actor-bound Jira transition; owner closes in Jira')
			_require(current['status_id'] == config['jira']['status_map']['done'], 'Owner closure is no longer Done; owner closes in Jira')
			proof, record = owner['owner_closure'], owner['record']
			return {'ticket': issue, 'issue_id': issue, 'cloud_id': provider['cloud_id'], 'project_id': provider['project_id'],
				'actor_id': provider['controller_actor_id'], 'status': 'RECONCILED', 'operation_id': record['operation_id'],
				'before_status_id': proof['from_status'], 'after_status_id': proof['status'], 'observed_at': current['observed_at']}
		_require(not owner_closure_required(config, policy['snapshot']), 'owner_closure_required: owner closes in Jira; then run jira-lifecycle JIRA_RECONCILED')
		_recover_jira_ticket(store, config=config, issue=issue, provider=provider, now=now)
		rows = store.jira_operations(issue)
		closeout = count_gate.get('closeout_record', {})
		matches = [row for row in rows if row['status'] == 'SUCCEEDED'
			and row['record']['record_id'] == closeout.get('jira_transition_record_id')
			and jira_reconciliation_binding_matches(row['record']['binding'], closeout.get('binding'))
			and row['record']['lifecycle_event'] == 'JIRA_RECONCILED'
			and row['record']['jira_provider'] == provider
			and row['record']['to_status_id'] == config['jira']['status_map']['done']]
		_require(len(matches) == 1, 'No matching durable reconciliation; run controller jira-lifecycle before post-merge counting')
		row = matches[0]
		record = row['record']
		retained = _retained_jira_observation(row['readback_json'])
		_require(isinstance(retained, dict) and retained.get('issue_id') == issue
			and retained.get('jira_provider') == provider
			and retained.get('status') == record['to_status_id']
			and retained.get('actor') == provider['controller_actor_id'], 'Durable reconciliation readback is missing or mismatched')
		current = None
		try:
			current = read_current_status({'issue_id': issue, 'jira_provider': deepcopy(provider)})
		except Exception as exc:
			# Failure to observe is not evidence against a completed transition.
			# Leave its durable receipt intact so a later progress run can retry.
			raise ValidationError('Post-merge status is unobserved for this run') from exc
		# Validate complete identity, shape and freshness before interpreting status.
		# A malformed/stale read is UNOBSERVED for this run, never a downgrade.
		_jira_status_observation(current, issue, provider, max(timestamp(now), timestamp(retained['observed_at'])))
		if current['status_id'] != record['to_status_id']:
			with store.transaction() as db:
				# Preserve the successful operation/readback; retain conflict separately.
				db.execute('INSERT OR REPLACE INTO controller_meta VALUES(?,?)',
					('jira-conflict:' + record['operation_id'], canonical(current).decode()))
				db.execute("UPDATE controller_jira_operation SET status='FAILED',updated_at=? WHERE operation_id=? AND status='SUCCEEDED'", (now, record['operation_id']))
			raise ValidationError('Post-merge status conflict; suspected external automation conflict; recover through jira-lifecycle transition evidence; never reissue')
	return {'ticket': issue, 'issue_id': issue, 'cloud_id': provider['cloud_id'], 'project_id': provider['project_id'],
		'actor_id': provider['controller_actor_id'], 'status': 'RECONCILED', 'operation_id': record['operation_id'],
		'before_status_id': record['from_status_id'], 'after_status_id': record['to_status_id'], 'observed_at': current['observed_at']}


def post_merge_jira_progress(*, jira_enabled, merged_ticket, scope, observed_at, jira_binding, fetch_scope_page, count_gate, reconcile_merged_ticket=None, store=None, config=None, observe_provider_identity=None, observe_issue_identity=None, observe_issue_policy=None, read_current_status=None, include_epics=False, max_pages=20, max_items=10000, max_bytes=16 * 1024 * 1024, max_seconds=60, clock=None):
	# reconcile_merged_ticket is an inert compatibility keyword for 1.9.5 adapters.
	# It cannot carry journal/merge authority and must never be called; callers
	# reconcile through jira-lifecycle before requesting read-only progress.
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
		_require(isinstance(config, dict) and config.get('jira', {}).get('enabled') is True, 'Post-merge Jira needs enabled reviewed configuration')
		# Name an owner hold even when closeout has no reconciliation record yet.
		if callable(observe_issue_policy):
			from .jira_lifecycle import owner_closure_required
			policy = _observe_jira_write_policy(config, merged_ticket, observe_issue_policy, observed_at)
			transition_id = count_gate.get('closeout_record', {}).get('jira_transition_record_id')
			with store.connection() as db:
				owner = db.execute('SELECT 1 FROM controller_meta WHERE key=?', ('jira-owner-closure:' + str(transition_id),)).fetchone()
			_require(not owner_closure_required(config, policy['snapshot']) or owner is not None,
				'owner_closure_required: owner closes in Jira; then run jira-lifecycle JIRA_RECONCILED')
		validate_count_gate()
		reconciled = _post_merge_jira_receipt(store, config, merged_ticket, observed_at, count_gate,
			observe_provider_identity=observe_provider_identity, observe_issue_identity=observe_issue_identity,
			observe_issue_policy=observe_issue_policy, read_current_status=read_current_status)
	except Exception as exc:
		return {**base, 'jira_state': 'UNOBSERVED', 'reason': f'merge reconciliation failed: {exc}'}
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
	return (deepcopy(tickets), repository_binding, observation)

def production_review_batching_cycle(store, *, calendar, repository_root, config, operating_snapshot,
        binding, clock, load_calendar=None,
        observe_owner_ready=None, observe_review_candidate=None,
        deliver_review_batch=None, observe_review_delivery=None, candidate_limit=None):
	"""Routine presentation only, through trusted injected ports; never transport.

	Discovery returns {binding, observed_at, candidates}; candidates are locators.
	observe_review_candidate(candidate, now) independently collects the exact
	ReviewBatchStore observation, including current lifecycle and final-gate bytes.
	deliver_review_batch(payload) performs one conditional send with the immutable
	batch ID as its idempotency key and returns verified terminal readback or None.
	observe_review_delivery(payload) is receipt-only: unknown means None, never a
	retry. NOT_DELIVERED must also fence any delayed send for that batch identity.
	The host serializes this controller operation and bounds all adapter calls.
	Safety/action alerts and hourly reports use their separate immediate ports.
	"""
	from .review_batching import ReviewBatchStore
	from .operating_calendar import effective_calendar, next_review_presentation, utc_instant
	from .protected_settings import effective_snapshot, require_budget_policy
	from .budgeting import policy_repository_root
	# Supplied snapshots and calendar values are data, never accepted policy.
	repository_root = policy_repository_root(repository_root, operating_snapshot)
	from .reporting.policy import prepared_snapshot
	accepted = prepared_snapshot(repository_root, config, operating_snapshot)
	if accepted is None:
		accepted = effective_snapshot(repository_root, config, operating_snapshot)
	require_budget_policy(accepted)
	calendar = effective_calendar(accepted.config)
	batch_store = ReviewBatchStore(store)
	result = {'enqueued': [], 'unqualified': 0, 'delivery': None, 'errors': [],
		'state': 'UNCONFIGURED', 'merge_authorized': False, 'owner_alerts': []}

	def finish():
		result['status'] = batch_store.view(clock(), calendar)
		result['owner_alerts'] = result['status']['owner_alerts']
		reporting_failures = batch_store.reporting_failure_intents()
		for alert in result['owner_alerts']:
			if alert.get('intent_id') in reporting_failures:
				continue  # Reporting retains FAILED and owns its durable alerts.
			result['errors'].append({'operation': 'review_owner_attention', **deepcopy(alert)})
		return result

	def failed(operation, exc):
		result['errors'].append({'operation': operation, 'state': 'PENDING',
			'reason': str(exc) if isinstance(exc, ValidationError) else type(exc).__name__})

	if observe_owner_ready is not None:
		try:
			_require(callable(observe_owner_ready) and callable(observe_review_candidate),
				'Owner-ready discovery requires a fresh trusted candidate observer')
			now = clock()
			request = {'binding': deepcopy(binding), 'observed_at': now}
			ready = observe_owner_ready(deepcopy(request))
			_require(isinstance(ready, dict) and set(ready) == {'binding', 'observed_at', 'candidates'}
				and ready['binding'] == binding and ready['observed_at'] == now
				and isinstance(ready['candidates'], list), 'Owner-ready discovery is foreign, stale or malformed')
			canonical(ready)
			result['state'] = 'PENDING'
			candidates = ready['candidates']
			if candidate_limit is not None:
				candidates, result['deferred'] = batch_store.discovery_candidates(candidates, candidate_limit)
			for candidate in candidates:
				try:
					_require(isinstance(candidate, dict) and str(candidate.get('repository_id')) == binding['repository_id'],
						'Review candidate repository differs from the controller binding')
					observed_at = clock()
					observation = observe_review_candidate(deepcopy(candidate), observed_at)
					intent = batch_store.enqueue_observed(candidate, observation, observed_at, calendar,
						repository_id=binding['repository_id'])
					if intent is not None:
						result['enqueued'].append(intent)
					else:
						result['unqualified'] += 1
				except Exception as exc:
					failed('review_enqueue', exc)
		except Exception as exc:
			failed('review_discovery', exc)
	# No delivery port means durable collection only, including during the lane E
	# handoff. An absent port consumes no attempt; lane E owns reporting OFF.
	if deliver_review_batch is None and observe_review_delivery is None:
		return finish()
	try:
		_require(all(callable(port) for port in (observe_review_candidate,
			deliver_review_batch, observe_review_delivery)),
			'Review delivery requires candidate, delivery and receipt-only readback ports')
		result['state'] = 'PENDING'

		def reconcile(payload, receipt):
			_require(isinstance(receipt, dict) and receipt.get('batch_id') == payload['batch_id']
				and receipt.get('payload_sha256') == payload['payload_sha256'],
				'Review delivery readback differs from the requested batch')
			batch_store.reconcile(receipt, calendar)
			result['delivery'] = deepcopy(receipt)

		# Restart never resends unresolved identities. Only verified terminal
		# readback may complete them or admit the store's one remaining retry.
		for pending in batch_store.pending_batches(limit=1 if candidate_limit is not None else None):
			if batch_store.release_unsent(pending['batch_id'], clock(), calendar,
					reason='Restart recovered durable preparation with no send started'):
				continue
			try:
				receipt = observe_review_delivery(deepcopy(pending))
				if receipt is not None:
					reconcile(pending, receipt)
			except Exception as exc:
				failed('review_readback', exc)
		excluded = set()
		while True:
			now = clock()
			batch = batch_store.claim(now, calendar,
				lambda candidate: observe_review_candidate(candidate, now), exclude=excluded, max_members=candidate_limit)
			if batch is None:
				break
			now = clock()
			checked = {}
			def reobserve(candidate):
				member = next(item for item in batch['members'] if item['candidate'] == candidate)
				checked[member['intent_id']] = None
				observation = observe_review_candidate(candidate, now)
				checked[member['intent_id']] = deepcopy(observation)
				return observation
			try:
				payload = batch_store.recheck(batch['batch_id'], now, calendar, reobserve)
				# Recheck time after provider reads: cutoff/expiry can pass in flight.
				send_at = clock()
				window = next_review_presentation(send_at, calendar)
				_require(utc_instant(window['presentation_at']) == utc_instant(send_at)
					and all(utc_instant(item['expires_at']) > utc_instant(send_at) for item in checked.values()),
					'Review presentation window closed or evidence expired before delivery')
			except Exception as exc:
				# Nothing has crossed the durable send boundary. Withdraw only
				# freshly invalid members; retry survivors in a new immutable batch.
				batch_store.release_unsent(batch['batch_id'], now, calendar, checked,
					reason='Pre-send check refused: ' + type(exc).__name__)
				failed('review_recheck', exc)
				from .review_batching import _observation_valid
				blocked = {identity for identity, value in checked.items()
					if not _observation_valid(value, next(m['candidate'] for m in batch['members']
						if m['intent_id'] == identity), utc_instant(now))}
				excluded.update(blocked or (m['intent_id'] for m in batch['members']))
				if candidate_limit is not None:
					# Reporting shares a bounded destination deadline. Its durable
					# cursor admits the next candidate on a subsequent cycle.
					break
				continue
			transport_payload = deepcopy(payload)
			cancel_uninvoked = batch_store.begin_delivery(batch['batch_id'], send_at)
			# The fence commit can cross cutoff or evidence expiry. Only this live
			# serialized path knows transport was never invoked; restart stays UNKNOWN.
			try:
				if load_calendar is not None:
					# Test hook only: its return value can never supply policy.
					load_calendar()
				from .operating import read_operating
				current_policy = read_operating(repository_root, config)
				require_budget_policy(current_policy)
				calendar = effective_calendar(current_policy.config)
				send_at = clock()
				window = next_review_presentation(send_at, calendar)
				_require(utc_instant(window['presentation_at']) == utc_instant(send_at)
					and all(utc_instant(item['expires_at']) > utc_instant(send_at) for item in checked.values()),
					'Post-fence presentation window closed or evidence expired')
				batch_store.check_delivery_window(batch['batch_id'], send_at, calendar)
			except Exception as exc:
				cancel_uninvoked(send_at, calendar)
				excluded.update(m['intent_id'] for m in batch['members'])
				failed('review_recheck', exc)
				if candidate_limit is not None:
					# Same bounded destination deadline as the failure path above.
					break
				continue
			# An unavoidable small gap remains from this last calendar/clock check to provider
			# receipt (later policy merges, scheduling/transport latency); no atomic remote clock.
			receipt = deliver_review_batch(transport_payload)
			if receipt is not None:
				reconcile(payload, receipt)
			else:
				result['state'] = 'UNKNOWN'
			# One delivery per cycle; retries wait for a subsequent cycle.
			break
		if batch_store.pending_batches():
			result['state'] = 'UNKNOWN'
	except Exception as exc:
		result['state'] = 'UNKNOWN'
		failed('review_presentation', exc)
	return finish()


@policy_cycle
def production_controller_cycle(store, *, controller_config, now, host_capacity=None, inventory_binding, repository_root, repository_head_sha, repository_tree_sha, observe_inventory, dispatch_ticket, observe_dispatch, deliver_status, publication_config=None, observe_publication=None, dispatch_role=None, cycle_id=None, overlap_allowance_requests=None, observe_overlap_allowance=None, observe_overlap_conflict=None, sync_writer=None, reconcile_overlap_sync=None, observe_dispatch_quiescence=None, observe_overlap_sync_inventory=None, observe_reservations=None, replan_request=None, observe_replan_trigger=None, controller_clock=None, observe_host_resources=None, verify_host_observation=None, planning_resource_producer_sha256=None, project_config_sha256=None, operating_config_sha256=None, planning_state_store=None, planning_config=None, planning_operating_snapshot=None, planning_provider=None, observe_planning_backlog=None, verify_planning_backlog=None, observe_planning_events=None, verify_planning_events=None, verify_planning_owner=None, configure_budgeting=None, budget_ledger_path=None, observe_owner_ready=None, observe_review_candidate=None, deliver_review_batch=None, observe_review_delivery=None, reporting_providers=None, configure_reporting=None, observe_reporting_sources=None, verify_reporting_sources=None, verify_reporting_link=None):
	# Validate before durable recovery or any provider observation. Only bools grant authority.
	_require(isinstance(controller_config, dict), 'controller must be a mapping')
	for key in ('dispatch_enabled', 'auto_dispatch', 'auto_request_critic', 'auto_resume_amendments'):
		_require(type(controller_config.get(key)) is bool, 'controller.' + key + ' must be a boolean')
	controller_config = dict(controller_config)
	_require(isinstance(store, ContinuousControllerStore), 'Production cycle needs a controller store')
	_require(all((callable(adapter) for adapter in (observe_inventory, dispatch_ticket, observe_dispatch, deliver_status))), 'Production controller adapters must be callable')
	timestamp(now)
	controller_clock = now_text if controller_clock is None else controller_clock
	provider_controller_time = now

	def post_provider_time():
		nonlocal provider_controller_time
		provider_controller_time = _post_provider_controller_time(controller_clock, provider_controller_time)
		return provider_controller_time

	_require(dispatch_role is None or dispatch_role in {'writer', 'critic'}, 'Compatibility dispatch role must be writer or critic')
	_require(isinstance(inventory_binding, dict) and set(inventory_binding) == {'project_id', 'repository_id', 'scope_sha256'} and all((isinstance(value, str) and value for value in inventory_binding.values())), 'Production inventory needs immutable project, repository, and scope bindings')

	# The reviewed host adapter supplies evidence, never an operator capacity map.
	# Reuse the single planning producer and its independent evidence verifier.
	_require(callable(observe_host_resources) and callable(verify_host_observation),
		'Production requires an authenticated planning resource observation')
	from .planning import produce_planning_resource_observation
	from .store import Store
	from .planning import tickets_from_provider_snapshot
	from .operating import validate_snapshot
	from .canonical import fingerprint
	_require(isinstance(planning_state_store, Store) and isinstance(planning_config, dict), 'Production requires authenticated planning inputs and a durable planning store')
	_require(all(callable(callback) for callback in (observe_planning_backlog, verify_planning_backlog, observe_planning_events, verify_planning_events)), 'Production requires authenticated planning backlog and event callbacks')
	validate_snapshot(planning_operating_snapshot, planning_config)
	_require(project_config_sha256 == fingerprint('accepted-project-config', planning_config) and operating_config_sha256 == planning_operating_snapshot.operating_hash, 'Planning configuration differs from accepted cycle bindings')
	from .operating_calendar import effective_calendar
	from .budgeting import project_ledger_path, resolve_settings, protected_state_roots
	# All protected domains use the independently selected repository and refusal.
	policy_root = Path(repository_root).resolve(strict=True)
	from .reporting.policy import ReportingPolicy
	report_policy = ReportingPolicy(policy_root, planning_config, planning_operating_snapshot)
	accepted_operating = report_policy.snapshot
	planning_operating_snapshot = accepted_operating
	calendar = effective_calendar(accepted_operating.config)
	operating_config_sha256 = accepted_operating.operating_hash
	resource_binding = {**inventory_binding, 'repository_head_sha': repository_head_sha,
		'repository_tree_sha': repository_tree_sha, 'project_config_sha256': project_config_sha256,
		'operating_config_sha256': operating_config_sha256}
	resources = produce_planning_resource_observation(
		binding=resource_binding, observed_at=now,
		producer_implementation_sha256=planning_resource_producer_sha256,
		observe_host_resources=observe_host_resources,
		verify_host_observation=verify_host_observation)
	_require(host_capacity is None or (type(host_capacity) is int and host_capacity == resources['host_capacity']),
		'Operator capacity differs from authenticated host capacity')
	host_capacity = min(resources['host_capacity'], *(item['capacity'] for item in resources['resources']))
	state_roots = protected_state_roots(*store.worktree_roots, policy_root)
	budget_settings = resolve_settings(accepted_operating.config)
	if configure_budgeting is None:
		configure_budgeting = getattr(dispatch_ticket, 'configure_budgeting', None)
	if callable(configure_budgeting):
		configure_budgeting(path=project_ledger_path(planning_config['project']['id'], path=budget_ledger_path,
			accounting_enabled=budget_settings['accounting_enabled'],
			worktree_roots=state_roots,
			previous_paths=(store.path.with_suffix('.routing.sqlite3'),)),
			governance=planning_config, operating=accepted_operating, repository_root=policy_root, worktree_roots=state_roots)
	else:
		_require(not budget_settings['accounting_enabled'] or 'legacy_behavior' in budget_settings,
			'Enabled budgeting requires a reviewed dispatch budgeting adapter')
	_require(isinstance(cycle_id, str) and cycle_id, 'Production planning requires a bound cycle identity')
	planning_binding = {**inventory_binding, 'repository_head_sha': repository_head_sha,
		'project_config_sha256': project_config_sha256, 'cycle_id': cycle_id}
	planning_request = {'provider': deepcopy(planning_provider), 'binding': deepcopy(planning_binding), 'observed_at': now}
	planning_snapshot = observe_planning_backlog(deepcopy(planning_request))
	backlog = tickets_from_provider_snapshot(planning_snapshot, planning_config,
		expected_binding=planning_binding, expected_provider=planning_provider, now=now,
		verify_provider_snapshot=verify_planning_backlog)
	event_snapshot = observe_planning_events(deepcopy(planning_request))
	_require(isinstance(event_snapshot, dict) and set(event_snapshot) == {'binding', 'observed_at', 'events', 'authentication'}, 'Planning event observation must be closed')
	_require(event_snapshot['binding'] == planning_binding and event_snapshot['observed_at'] == now and isinstance(event_snapshot['events'], list), 'Planning events are foreign, stale or incomplete')
	auth = event_snapshot['authentication']
	_require(isinstance(auth, dict) and set(auth) == {'evidence_id'} and isinstance(auth['evidence_id'], str) and auth['evidence_id'], 'Planning events require independent evidence identity')
	verified = verify_planning_events(deepcopy(auth), deepcopy(planning_request))
	_require(verified == {'status': 'VERIFIED', 'evidence_id': auth['evidence_id'], 'snapshot': {k: deepcopy(v) for k, v in event_snapshot.items() if k != 'authentication'}}, 'Planning events differ from independent authenticated evidence')
	store.recover_dispatches(now)

	def observe_current_reservations(observed_at, current_inventory_sha256):
		if callable(observe_reservations):
			_require(re.fullmatch('[0-9a-f]{64}', current_inventory_sha256 or '') is not None, 'Reservation observation requires the exact current inventory digest')
			raw = observe_reservations({'repository_id': inventory_binding['repository_id'], 'repository_root': str(Path(repository_root).resolve(strict=True)), 'head_sha': repository_head_sha, 'tree_sha': repository_tree_sha, 'inventory_sha256': current_inventory_sha256, 'observed_at': observed_at})
			_require(isinstance(raw, dict), 'Reservation observer must return one authenticated complete envelope')
			return canonical_reservation_sources(raw, repository_root, repository_head_sha, repository_tree_sha, inventory_binding['repository_id'], observed_at, inventory_sha256=current_inventory_sha256)
		# Contract-only mock stores cannot authenticate provider/Git sources. Real
		# protected stores always fail closed, and the production CLI always wires
		# the reviewed reservation observer.
		_require(type(store) is not ContinuousControllerStore, 'Authenticated parked and worktree-diff reservation observation is required')
		return []

	tickets, repository_binding, observation = _observe_controller_inventory(observe_inventory, now=now, inventory_binding=inventory_binding, repository_root=repository_root, repository_head_sha=repository_head_sha, repository_tree_sha=repository_tree_sha)
	store.bind_repository(repository_binding)
	reservations = observe_current_reservations(now, observation['inventory_sha256'])
	if replan_request is not None:
		_require(isinstance(replan_request, dict) and set(replan_request) == {'comment_id'} and type(replan_request['comment_id']) is int and replan_request['comment_id'] > 0, 'Replan request must contain one provider comment locator')
		_require(callable(observe_replan_trigger), 'Replan requires the reviewed authenticated trigger observer')
		context = store.replan_context()
		_require(isinstance(context['prior_inventory_sha256'], str) and context['nonterminal'], 'Replan has no durable prior inventory and non-terminal dispatch set')
		request = {**replan_request, 'repository_id': inventory_binding['repository_id'], 'prior_inventory_sha256': context['prior_inventory_sha256'], 'next_inventory_sha256': observation['inventory_sha256'], 'nonterminal_dispatch_ids': [item['dispatch_id'] for item in context['nonterminal']]}
		raw_trigger = observe_replan_trigger(deepcopy(request))
		trigger_now = post_provider_time()
		trigger = validate_replan_trigger_observation(publication_config, request, raw_trigger, now=trigger_now)
		store.seal_round_boundary_replan(trigger, trigger_now)
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
			plan_now = post_provider_time()
			record = validate_overlap_allowance_observation(publication_config, request, plan_observation, tickets, now=plan_now, expected_repository_id=inventory_binding['repository_id'], expected_cycle_id=cycle_id, allow_expired=True)
			store.register_overlap_allowance(record, plan_now)
			if timestamp(plan_now) >= timestamp(record['expires_at']):
				gate = store.expire_overlap_allowance(record['allowance_id'], plan_now)
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
		if gate['status'] == 'UNKNOWN':
			try:
				_require(callable(reconcile_overlap_sync), 'Unknown overlap synchronization outcome needs a receipt-only reconciler')
				reconciled = store.reconcile_overlap_sync_gate(gate['gate_id'], reconcile_overlap_sync, now)
				allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_RECEIPT_RECONCILIATION', 'status': reconciled['status'], 'gate_id': gate['gate_id']})
				if reconciled['status'] == 'SYNCED':
					newly_synced.append(reconciled)
				continue
			except Exception as exc:
				allowance_blocked_tickets.update(gate['tickets'])
				reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
				allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_RECEIPT_RECONCILIATION', 'status': 'UNKNOWN', 'gate_id': gate['gate_id'], 'reason': reason})
				errors.append({'operation': 'overlap_sync_reconciliation', 'state': 'BLOCKED', 'reason': reason})
				continue
		if gate['status'] == 'IN_FLIGHT':
			allowance_blocked_tickets.update(gate['tickets'])
			reason = 'Overlap synchronization executor outcome is unresolved'
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_DECISION', 'status': gate['status'], 'gate_id': gate['gate_id'], 'reason': reason})
			errors.append({'operation': 'overlap_sync', 'state': 'BLOCKED', 'reason': reason})
			continue
		disabled = next((key for key in ('dispatch_enabled', 'auto_dispatch') if controller_config[key] is not True), None)
		if disabled is not None:
			allowance_blocked_tickets.update(gate['tickets'])
			reason = 'CONTROLLER_AUTOMATION_DISABLED: controller.' + disabled + ' is false'
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'SYNC_DECISION', 'status': 'REFUSED', 'gate_id': gate['gate_id'], 'reason': reason})
			errors.append({'operation': 'overlap_sync', 'state': 'REFUSED', 'switch': 'controller.' + disabled, 'reason': reason})
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
			decision_now = post_provider_time()
			decided = store.execute_overlap_sync_gate(gate['gate_id'], decision, sync_writer, decision_now)
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
			provider_heads_now = post_provider_time()
			completed = store.confirm_overlap_sync_inventory(gate['gate_id'], tickets, inventory_sha256=observation['inventory_sha256'], observed_at=provider_heads_now, provider_observation=provider_heads)
			allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'POST_SYNC_INVENTORY', 'status': completed['status'], 'gate_id': gate['gate_id']})
		except Exception as exc:
			allowance_blocked_tickets.update(gate['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			errors.append({'operation': 'overlap_sync_inventory', 'state': 'BLOCKED', 'reason': reason})
	if newly_synced:
		try:
			reservations = None
			refreshed_tickets, refreshed_binding, refreshed_observation = _observe_controller_inventory(observe_inventory, now=provider_controller_time, inventory_binding=inventory_binding, repository_root=repository_root, repository_head_sha=repository_head_sha, repository_tree_sha=repository_tree_sha)
			store.bind_repository(refreshed_binding)
			tickets = refreshed_tickets
			observation = refreshed_observation
			reservations = observe_current_reservations(provider_controller_time, observation['inventory_sha256'])
			for gate in newly_synced:
				_require(callable(observe_overlap_sync_inventory), 'Post-sync inventory requires a fresh provider PR-head observer')
				provider_heads = observe_overlap_sync_inventory(deepcopy(gate))
				provider_heads_now = post_provider_time()
				completed = store.confirm_overlap_sync_inventory(gate['gate_id'], tickets, inventory_sha256=observation['inventory_sha256'], observed_at=provider_heads_now, provider_observation=provider_heads)
				allowance_audit.append({'allowance_id': gate['allowance_id'], 'phase': 'POST_SYNC_INVENTORY', 'status': completed['status'], 'gate_id': gate['gate_id']})
		except Exception as exc:
			for gate in newly_synced:
				allowance_blocked_tickets.update(gate['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			errors.append({'operation': 'overlap_sync_inventory', 'state': 'BLOCKED', 'reason': reason})
	lane_allowance_plans = allowance_plans
	if allowance_plans:
		try:
			reservations = None
			tickets, lane_binding, observation = _observe_controller_inventory(observe_inventory, now=provider_controller_time, inventory_binding=inventory_binding, repository_root=repository_root, repository_head_sha=repository_head_sha, repository_tree_sha=repository_tree_sha)
			store.bind_repository(lane_binding)
			reservations = observe_current_reservations(provider_controller_time, observation['inventory_sha256'])
		except Exception as exc:
			lane_allowance_plans = []
			for _request, planned in allowance_plans:
				allowance_blocked_tickets.update(planned['binding']['tickets'])
			reason = str(exc) if isinstance(exc, ValidationError) else type(exc).__name__
			errors.append({'operation': 'overlap_allowance_inventory', 'state': 'BLOCKED', 'reason': reason})
	# Preserve the boundary presented by the fresh inventory before policy refusals.
	cancellation_boundary_ready = store.round_boundary_ready(tickets, observation['inventory_sha256'])
	for index, item in enumerate(tickets):
		if item['disposition'] != 'ELIGIBLE':
			continue
		# Canonical production tickets always carry a validated stage. The existing
		# cycle-wide role remains usable by contract-only mock stores.
		stage = item.get('lifecycle_stage', 'independent_review' if dispatch_role == 'critic' else 'implementation')
		path_switch = {'implementation': 'auto_dispatch', 'independent_review': 'auto_request_critic', 'amendment': 'auto_resume_amendments'}[stage]
		disabled = next((key for key in ('dispatch_enabled', path_switch) if controller_config[key] is not True), None)
		if disabled is not None:
			reason = 'CONTROLLER_AUTOMATION_DISABLED: controller.' + disabled + ' is false'
			tickets[index] = {**item, 'disposition': 'BLOCKED', 'reason': reason, 'next_action': 'Enable the reviewed controller switch after installation', 'resume_trigger': 'controller.' + disabled + ' is true'}
			errors.append({'operation': 'dispatch_gate', 'ticket': item['ticket'], 'state': 'REFUSED', 'switch': 'controller.' + disabled, 'reason': reason})
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
	for request, planned in lane_allowance_plans:
		try:
			lane_observation = observe_overlap_allowance(deepcopy(request), 'LANE_START')
			lane_now = post_provider_time()
			current = validate_overlap_allowance_observation(publication_config, request, lane_observation, tickets, now=lane_now, expected_repository_id=inventory_binding['repository_id'], expected_cycle_id=cycle_id)
			store.recheck_overlap_allowance(planned['allowance_id'], current, lane_now)
			active_allowances.append({'allowance_id': current['allowance_id'], 'path': current['binding']['path'], 'tickets': current['binding']['tickets'], 'record': current})
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
	_require(isinstance(reservations, list), 'Authenticated reservations were not refreshed for the inventory being scheduled')
	if not budget_settings['enforcement_enabled']:
		tickets = [{**item, 'budget_available': True} for item in tickets]
	ticket_by_id = {item['ticket']: item for item in tickets}
	round_boundary_ready = store.round_boundary_ready(tickets, observation['inventory_sha256'])
	_require({item['key'] for item in backlog} == {item['ticket'] for item in tickets}, 'Authenticated backlog ticket keys differ from dispatch inventory')
	backlog_by_key = {item['key']: item for item in backlog}
	for item in tickets:
		paths = set(backlog_by_key[item['ticket']]['write_paths']) | set(backlog_by_key[item['ticket']]['open_pr_files']) | set(backlog_by_key[item['ticket']]['worktree_diff'])
		_require(paths == set(item['paths']) - {'MANIFEST.json', 'MANIFEST.md'}, 'Dispatch paths differ from authenticated planning attribution')
	# Only provider-authenticated records that survived the fresh lane-start
	# recheck may influence sealed capacity. Candidate inventory never grants
	# an exception and the planner cannot select its own verifier or context.
	planning_allowances = [deepcopy(item['record']) for item in active_allowances]
	bound_prs = {}
	for request, _record in lane_allowance_plans:
		for pr in request['pull_requests']:
			prior = bound_prs.get(pr['ticket'])
			_require(prior is None or prior == pr, 'Overlap planning PR identities disagree')
			bound_prs[pr['ticket']] = deepcopy(pr)
	overlap_binding = {'repository_id': inventory_binding['repository_id'], 'cycle_id': cycle_id,
		'pull_requests': sorted(bound_prs.values(), key=lambda pr: pr['number'])}
	def verify_planning_allowance(record, binding):
		_require(binding == overlap_binding and record in planning_allowances,
			'Planning allowance was not authenticated by this controller cycle')
		return deepcopy(record)
	plan = store.plan_production_cycle(planning_state_store, tickets, planning_config,
		backlog=backlog, events=event_snapshot['events'], verify_owner_source=verify_planning_owner,
		resource_observation=resources, expected_resource_binding=resource_binding,
		expected_resource_producer_sha256=planning_resource_producer_sha256,
		operating_snapshot=planning_operating_snapshot, verify_host_observation=verify_host_observation,
		now=now, boundary_ready=round_boundary_ready, overlap_allowances=planning_allowances,
		expected_overlap_binding=overlap_binding, verify_overlap_allowance=verify_planning_allowance)
	if plan is not None:
		host_capacity = min(host_capacity, plan['lane_count'])
		admitted = {key for lane in plan['lanes'] for key in lane['ticket_keys']}
		ranked = sorted(admitted, key=lambda key: (-plan['ranking'][key], ticket_by_id[key]['priority'], key))
		priority = {key: index for index, key in enumerate(ranked)}
		for index, item in enumerate(tickets):
			if item['ticket'] in priority:
				tickets[index] = {**item, 'priority': priority[item['ticket']]}
			elif item['disposition'] == 'ELIGIBLE':
				tickets[index] = {**item, 'disposition': 'BLOCKED', 'reason': 'Authenticated durable plan did not admit this ticket'}
	streams = (store.schedule(tickets, now, host_capacity, active_allowances, reservations=reservations, inventory_sha256=observation['inventory_sha256'])
		if plan is not None else store.snapshot())
	# Evaluate and consume any authenticated boundary seal before cancellation.
	# A closed boundary retains pending intents across restarts.
	if cancellation_boundary_ready:
		for operation in store.pending_dispatches():
			current = next((item for item in tickets if item['ticket'] == operation['ticket']), None)
			if current is not None and current['reason'].startswith('CONTROLLER_AUTOMATION_DISABLED:'):
				store.cancel_pending_dispatch(operation['dispatch_id'], now, current['reason'])
	dispatches = []
	operations = []
	if round_boundary_ready:
		store.prepare_dispatches(now)
		store.bind_dispatch_waivers([item['record'] for item in active_allowances], now)
		operations = store.prepare_dispatches(now)
	else:
		# A closed round boundary still permits read-only observation of uncertain launches.
		with store.connection() as db:
			operations = [dict(row) | {'payload': loads(row['payload_json'])} for row in db.execute("SELECT * FROM controller_dispatch WHERE status IN ('UNKNOWN','LAUNCHED') ORDER BY stream_id,dispatch_id")]
	for operation in operations:
		payload, dispatch_id = (operation['payload'], operation['dispatch_id'])
		if operation['status'] == 'PENDING':
			# Scheduling can retain a pre-crash WORKING snapshot at a closed boundary.
			# Revalidate against the current plan and ticket before launching its intent.
			current = next((item for item in tickets if item['ticket'] == payload['ticket']), None)
			admitted = (plan is not None
				and any(payload['ticket'] in lane['ticket_keys'] for lane in plan['lanes'])
				and current is not None and current['disposition'] == 'ELIGIBLE'
				and all(current[key] for key in ('dependencies_satisfied', 'budget_available', 'cap_available', 'review_independent'))
				and current['exact_tuple'] == payload['exact_tuple']
				and payload['ticket'] in publication_reports and any((stream['stream'] == payload['stream'] and stream['state'] == 'WORKING' and (stream['ticket'] == payload['ticket']) and (stream['exact_tuple'] == payload['exact_tuple']) for stream in streams)))
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
		else:
			current = ticket_by_id.get(payload['ticket'])
			if current is None or current['disposition'] == 'COMPLETE' or current['exact_tuple'] != payload['exact_tuple']:
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
	digest = store.digest(now, calendar=calendar)
	delivery = None
	if digest is not None:
		try:
			receipt = deliver_status(digest)
			_require(isinstance(receipt, dict) and {'delivery_id', 'status', 'observed_at'} <= set(receipt)
				and set(receipt) <= {'delivery_id', 'status', 'observed_at', 'evidence_kind', 'channel_delivery'}
				and receipt['delivery_id'] == digest['delivery_id'] and receipt['status'] == 'DELIVERED',
				'Status delivery readback is missing or mismatched')
			if receipt.get('evidence_kind') == 'LOCAL_RECEIPT':
				_require(receipt.get('channel_delivery') is False, 'Local receipt cannot assert channel delivery')
			timestamp(receipt['observed_at'])
			store.acknowledge_digest(digest['delivery_id'], receipt['observed_at'])
			delivery = receipt
		except Exception as exc:
			errors.append({'operation': 'status_delivery', 'delivery_id': digest['delivery_id'], 'state': 'PENDING', 'reason': type(exc).__name__})
	streams = store.snapshot()
	from .reporting.settings import effective_reporting
	from .reporting.runtime import reporting_cycle, ReviewReportingPort, _readmit_reporting
	from .reporting.bounded import DeliveryBudget, SourceBudget, BoundedObserver, MAX_REVIEW_CANDIDATES
	from .reporting.observations import attributed_accounting, collect_sources, collect_review_corrections
	report_binding = dict(project=inventory_binding['project_id'], repository=inventory_binding['repository_id'],
		scope_revision=inventory_binding['scope_sha256'])
	operating_root = planning_operating_snapshot.config
	port = None
	review_retry_first = False
	reporting = {'state': 'UNAVAILABLE', 'coverage': {'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE'}}
	try:
		report_settings = effective_reporting(operating_root, binding=report_binding)
		context = dict(streams=streams, capacity=resources,
			graph=deepcopy(plan['inputs']) if plan is not None else None,
			gates=[{'stream': s['stream'], 'observed_at': s.get('updated_at'),
				'gate': s.get('verification_gate'), 'current': s.get('updated_at') == now} for s in streams],
			coverage={'sources': 'UNAVAILABLE', 'accounting': 'UNAVAILABLE', 'capacity': 'OBSERVED',
				'graph': 'OBSERVED' if plan is not None else 'UNAVAILABLE', 'gates': 'RETAINED; current provider gates unavailable'})
		if report_settings['mode'] != 'off':
			# One observer spans both stages. Source collection has its own
			# bounded allowance; presentation switches to the shared send budget.
			source_review_budget = DeliveryBudget()
			if callable(observe_review_candidate):
				observe_review_candidate = BoundedObserver(observe_review_candidate, source_review_budget)
			if callable(observe_owner_ready):
				observe_owner_ready = source_review_budget.observer(observe_owner_ready)
			if callable(configure_reporting):
				reporting_providers = configure_reporting(planning_operating_snapshot, report_binding, repository_root=policy_root)
			selected = {'teams', 'slack'} if report_settings['mode'] == 'both' else {report_settings['mode']}
			_require(isinstance(reporting_providers, dict) and selected <= reporting_providers.keys(), 'REPORT_PROVIDERS_REQUIRED')
			with SourceBudget():
				# Demand is a scheduling hint only; production discovery still
				# validates freshness, scope and every gate before admission.
				context['review_demand'] = callable(observe_owner_ready)
				if callable(observe_owner_ready):
					try:
						demand = observe_owner_ready({'binding': deepcopy(inventory_binding), 'observed_at': post_provider_time()})
						context['review_demand'] = bool(demand.get('candidates')) if isinstance(demand, dict) else True
					except Exception:
						context['coverage']['review_demand'] = 'UNAVAILABLE'
				try:
					context['review_corrections'] = collect_review_corrections(store, report_binding, now, observe_review_candidate)
					context['coverage']['review_corrections'] = 'OBSERVED'
				except Exception as exc:
					context['coverage']['review_corrections'] = 'UNAVAILABLE'
					errors.append({'operation': 'reporting_corrections', 'state': 'UNAVAILABLE', 'reason': type(exc).__name__})
				try:
					context.update(collect_sources(report_binding, now, observe_reporting_sources, verify_reporting_sources))
					if context.get('records') is not None:
						context['coverage']['sources'] = context.get('source_coverage', {}).get('status', 'VERIFIED')
					if context.get('source_coverage', {}).get('reason'):
						context['coverage']['source_reason'] = context['source_coverage']['reason']
					context['verify_link'] = verify_reporting_link
				except Exception as exc:
					context['coverage']['sources'] = 'STALE_OR_UNAVAILABLE'
					errors.append({'operation': 'reporting_sources', 'state': 'UNAVAILABLE', 'reason': type(exc).__name__})
			context['accounting_summary'] = attributed_accounting(budget_ledger_path, operating_root,
				report_binding['project'], worktree_roots=store.worktree_roots)
			context['coverage']['accounting'] = context['accounting_summary']['status']
		# Reporting does not admit source-supplied usage: the canonical read-only
		# ledger is the accounting boundary; missing token components stay unknown.
		summary = context.get('accounting_summary', {})
		context['accounting'] = dict(mode='on' if budget_settings['accounting_enabled'] else 'off',
			observations=deepcopy(summary.get('observations', [])), enrolled=summary.get('enrolled', []),
			complete_tickets=summary.get('complete_tickets', []))
		# Forecasts may cite canonical observation IDs; source usage/enrollment
		# never enters the ledger projection or turns accounting on.
		source_accounting = (context.get('records') or {}).get('accounting', {})
		if 'forecasts' in source_accounting:
			context['accounting']['forecasts'] = deepcopy(source_accounting['forecasts'])
		# Source observation cannot spend the shared delivery/review allowance.
		report_delivery_budget = DeliveryBudget()
		if report_settings['mode'] != 'off':
			if callable(observe_review_candidate):
				observe_review_candidate.budget = report_delivery_budget
			if callable(observe_owner_ready):
				observe_owner_ready.budget = report_delivery_budget
		reporting = reporting_cycle(store, operating=operating_root, binding=report_binding, now=now,
			providers=reporting_providers, context=context, delivery_budget=report_delivery_budget, defer_delivery=True)
		if reporting_providers is not None:
			# Explicit reporting ownership replaces legacy review delivery ports.
			# OFF collects without claiming/sending; it resolves no credentials.
			deliver_review_batch = observe_review_delivery = None
			if report_settings['mode'] != 'off':
				port = ReviewReportingPort(store, operating_root, report_binding, reporting_providers, post_provider_time,
					observe_candidate=observe_review_candidate, delivery_budget=report_delivery_budget,
					load_operating=report_policy)
				deliver_review_batch, observe_review_delivery = port.deliver_review_batch, port.observe_review_delivery
	except Exception as exc:
		# Invalid bindings and generation failure cannot fall back to a different
		# destination or delay already-authorized dispatch.
		if reporting_providers is not None or configure_reporting is not None or operating_root.get('reporting', {}).get('mode', 'off') != 'off':
			deliver_review_batch = observe_review_delivery = None
		reporting.update(state='REFUSED', reason=str(exc) if re.fullmatch(r'REPORT_[A-Z_]+', str(exc))
			else 'REPORT_CONFIGURATION_OR_PROVIDER_UNAVAILABLE')
		errors.append({'operation': 'reporting', 'state': 'UNAVAILABLE', 'reason': type(exc).__name__})
	if port is not None:
		# Alternate new admissions and historical retry/readback priority.
		# The sole reconciliation slot must rotate along with send admission.
		with store.transaction() as db:
			key = 'report-review-work-turn'
			row = db.execute('SELECT value FROM controller_meta WHERE key=?', (key,)).fetchone()
			review_retry_first = row is not None and row[0] == 'RETRY'
			db.execute('INSERT INTO controller_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
				(key, 'NEW' if review_retry_first else 'RETRY'))
	if reporting.get('state') == 'ENABLED':
		try:
			from .reporting.runtime import drain
			from .review_batching import ReviewBatchStore
			review_waiting = context.get('review_demand', False) or bool(ReviewBatchStore(store).pending_batches())
			with store.connection() as db:
				review_waiting = review_waiting or bool(db.execute("""SELECT 1 FROM controller_reports r
					JOIN controller_report_delivery d ON json_extract(d.intent_json,'$.report_id')=r.report_id
					WHERE r.kind='final-human-review' AND json_extract(d.state_json,'$.status') NOT IN ('DELIVERED','FAILED')
					LIMIT 1""").fetchone())
			# Protected admissions: alerts, hourly, EoD, then review work. Retain
			# one advance/platform for review; unused allowance stays available.
			reporting['deliveries'] = drain(store, operating_root, report_binding, reporting_providers or {},
				budget=report_delivery_budget.reporting_slice() if review_waiting and review_retry_first else report_delivery_budget,
				reserve_review=port is not None, reserve_reconciliation=review_retry_first and review_waiting,
				before_send=lambda: _readmit_reporting(operating_root, report_binding, report_policy))
		except Exception as exc:
			errors.append({'operation': 'reporting_delivery', 'state': 'UNAVAILABLE', 'reason': type(exc).__name__})
	if port is not None:
		from .review_batching import ReviewBatchStore
		resume_batch_ids = {p['batch_id'] for p in ReviewBatchStore(store).pending_batches()}
	def resume_reporting_reviews():
		if port is not None and callable(observe_review_candidate):
			try:
				port.resume(calendar, observe_review_candidate, batch_ids=resume_batch_ids)
			except Exception as exc:
				errors.append({'operation': 'reporting_review_resume', 'state': 'UNKNOWN', 'reason': type(exc).__name__})
	if review_retry_first:
		resume_reporting_reviews()
	# Calendar waits and uncertain review sends never gate dispatch, independent
	# review, amendments, or the separate immediate status/action channel above.
	try:
		if port is not None:
			report_delivery_budget.remaining()
		review_batching = production_review_batching_cycle(store, calendar=calendar,
			repository_root=policy_root, config=planning_config, operating_snapshot=accepted_operating,
			binding=inventory_binding, clock=post_provider_time,
			observe_owner_ready=observe_owner_ready, observe_review_candidate=observe_review_candidate,
			deliver_review_batch=deliver_review_batch, observe_review_delivery=observe_review_delivery,
			candidate_limit=MAX_REVIEW_CANDIDATES if report_settings['mode'] != 'off' else None)
		errors.extend(review_batching['errors'])
	except Exception as exc:
		if isinstance(exc, ValidationError) and str(exc) == 'REPORT_DESTINATION_BUDGET':
			review_batching = {'state': 'DEFERRED', 'reason': str(exc), 'merge_authorized': False}
		else:
			review_batching = {'state': 'UNAVAILABLE', 'merge_authorized': False}
			errors.append({'operation': 'review_batching', 'state': 'PENDING', 'reason': type(exc).__name__})
	# Newly stuck/exhausted reviews alert through the existing immediate status
	# port, even with periodic reports OFF. They never block work or reporting.
	if review_batching.get('owner_alerts'):
		alert = store.digest(post_provider_time(), calendar=calendar)
		if alert is not None:
			try:
				receipt = deliver_status(alert)
				_require(isinstance(receipt, dict) and {'delivery_id', 'status', 'observed_at'} <= set(receipt)
					and set(receipt) <= {'delivery_id', 'status', 'observed_at', 'evidence_kind', 'channel_delivery'}
					and receipt['delivery_id'] == alert['delivery_id'] and receipt['status'] == 'DELIVERED',
					'Status delivery readback is missing or mismatched')
				if receipt.get('evidence_kind') == 'LOCAL_RECEIPT':
					_require(receipt.get('channel_delivery') is False, 'Local receipt cannot assert channel delivery')
				timestamp(receipt['observed_at'])
				store.acknowledge_digest(alert['delivery_id'], receipt['observed_at'])
				delivery = receipt
			except Exception as exc:
				errors.append({'operation': 'review_owner_alert', 'state': 'PENDING', 'reason': type(exc).__name__})
	if not review_retry_first:
		resume_reporting_reviews()
	return {'schema_version': 3, 'observed_at': now, 'streams': streams, 'dispatch_receipts': dispatches, 'status_delivery': delivery, 'publication_readiness': publication_reports, 'overlap_allowances': allowance_audit, 'operating_calendar': calendar, 'reporting': reporting, 'review_batching': review_batching, 'errors': errors, 'execution_authority': False}

def _observe_jira_write_policy(config, issue, observer, now):
	"""Obtain fresh canonical scope evidence from the reviewed adapter."""
	from .canonical import fresh
	from .jira_lifecycle import require_jira_issue_scope
	_require(callable(observer), 'Jira scope observer is unavailable')
	value = deepcopy(observer(issue))
	_require(isinstance(value, dict) and set(value) == {'issue_key', 'issue_type', 'snapshot', 'observed_at'} and
		value['issue_key'] == issue and value['issue_type'] in {'LEAF', 'EPIC'},
		'Jira scope observation differs from the canonical issue')
	fresh(value['observed_at'], now, 1800)
	require_jira_issue_scope(config, issue, value['snapshot'])
	_require(isinstance(value['snapshot'].get('summary'), str), 'Jira owner-closure observation is missing its summary')
	return value


def _jira_lifecycle_binding_identity(binding, config):
	"""Closed candidate-field list; every other recognized field is invariant.

	The issue-only adapter form obtains project/repository from reviewed config.
	Retain the existing batch/lifecycle requirements-only binding form as well
	as the full candidate schema. Neither form permits missing invariant fields,
	and a revision cannot switch forms. Unknown fields never become candidates.
	"""
	invariants = {'project_id', 'repository_id', 'issue_id'}
	candidate_fields = {'candidate_id'}
	hash_fields = {'requirements_hash', 'contract_hash', 'policy_hash'} | candidate_fields
	if set(binding) == {'issue_id'}:
		identity = {'issue_id': binding['issue_id'], 'project_id': config.get('project', {}).get('id'),
			'repository_id': config.get('github', {}).get('repository_id')}
	else:
		_require(set(binding) in (invariants | {'requirements_hash'}, invariants | hash_fields), 'Jira candidate binding has missing or unrecognized fields')
		identity = {field: binding[field] for field in invariants}
		_require(all(isinstance(binding[field], str) and re.fullmatch(r'[0-9a-f]{64}', binding[field]) for field in hash_fields & set(binding)), 'Jira candidate binding has malformed candidate fields')
		identity.update({field: binding[field] for field in (hash_fields - candidate_fields) & set(binding)})
	repository_id = identity['repository_id']
	_require(isinstance(identity['project_id'], str) and identity['project_id'].strip()
		and ((type(repository_id) is int and repository_id > 0)
			or (isinstance(repository_id, str) and re.fullmatch(r'[1-9][0-9]*', repository_id))),
		'Jira binding is missing project/repository identity')
	return identity


def production_jira_lifecycle(store, *, config, contract, event, facts, binding, issue_type, state, producer_id, run_id, now, evidence, transition_id, read_current_status, write_transition, read_transition, observe_provider_identity=None, merge_result_id=None, observe_issue_identity=None, observe_issue_policy=None, observe_jira_operation_identity=None, read_assignment=None):
	_require(isinstance(store, ContinuousControllerStore), 'Jira lifecycle production needs the protected controller store')
	config = deepcopy(config)
	_require(all((callable(adapter) for adapter in (read_current_status, write_transition, read_transition))), 'Jira lifecycle read/write/readback adapters must be callable')
	if config.get('jira', {}).get('enabled', True) is False:
		return {'planned': False, 'reason': 'Jira is disabled; no reads or writes', 'execution_authority': False}
	_require(isinstance(binding, dict), 'Jira input must bind a canonical issue key')
	if not callable(observe_issue_identity):
		return {'planned': False, 'status': 'REFUSED', 'reason': 'Jira canonical issue observer is unavailable',
			'writes_stopped': True, 'execution_authority': False}
	# Keep caller mutation out of intent, stop, ownership and callback identity.
	binding = deepcopy(binding)
	issue = _require_canonical_issue(binding.get('issue_id'), now=now)
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
		_require(isinstance(binding, dict) and isinstance(binding.get('issue_id'), str) and issue, 'Jira lifecycle needs an immutable issue binding')
	except ProviderIdentityError as exc:
		return {'planned': False, 'status': exc.code, 'reason': str(exc), 'expected_identity': exc.expected or expected, 'observed_identity': exc.observed if exc.observed is not None else observed_identity, 'issue_lookups': 0, 'writes_stopped': True, 'reads_allowed': True, 'execution_authority': False}
	except Exception as exc:
		return {'planned': False, 'status': 'IDENTITY_UNOBSERVED', 'reason': 'Jira provider identity observation failed: ' + type(exc).__name__, 'expected_identity': expected, 'observed_identity': None, 'issue_lookups': 0, 'writes_stopped': True, 'reads_allowed': True, 'execution_authority': False}
	_require_canonical_issue(issue, observe_issue_identity=observe_issue_identity, now=now)
	try:
		policy = _observe_jira_write_policy(config, issue, observe_issue_policy, now)
		_require(policy['issue_type'] == issue_type, 'Jira issue type differs from its observed policy')
	except Exception as exc:
		return {'planned': False, 'status': 'SCOPE_REFUSED', 'reason': str(exc), 'writes_stopped': True, 'execution_authority': False}
	adapter_binding = {'issue_id': issue, 'jira_provider': provider}
	start_time = timestamp(now)
	try:
		current = read_current_status(adapter_binding)
		_require(isinstance(current, dict) and set(current) == {'issue_id', 'status_id', 'observed_at', 'jira_provider'} and (current['issue_id'] == issue) and (current['jira_provider'] == provider) and isinstance(current['status_id'], str) and current['status_id'], 'Jira pre-write observation is missing or mismatched')
		current_time = timestamp(current['observed_at'])
		_require(current_time >= start_time, 'Jira pre-write observation predates the lifecycle operation')
	except (TimeoutError, JiraTransitionNotAttempted) as exc:
		return {'planned': False, 'status': 'NOT_ATTEMPTED', 'reason': f'Jira pre-write observation timed out before any POST: {type(exc).__name__}', 'writes_stopped': False, 'execution_authority': False}
	except Exception as exc:
		return {'planned': False, 'status': 'NOT_ATTEMPTED', 'reason': f'Jira pre-write observation failed: {type(exc).__name__}', 'writes_stopped': True, 'execution_authority': False}
	from .jira_lifecycle import planned_write, transition_record, apply_read_back, owner_closure_required
	contract = deepcopy(contract)
	contract['owner_closure_required'] = bool(contract.get('owner_closure_required')) or owner_closure_required(config, policy['snapshot'])
	durable = store.jira_operations(issue)
	_jira_lifecycle_binding_identity(binding, config)
	require_jira_binding_identity(store, config, binding, observe_jira_operation_identity)
	def owner_closeout():
		return _observe_owner_jira_closeout(store, config, binding=binding, provider=provider, producer_id=producer_id,
			run_id=run_id, now=now, evidence=evidence, transition_id=transition_id, merge_result_id=merge_result_id,
			facts=facts, read_current_status=read_current_status)
	for row, table in _jira_ticket_operations(store, issue):
		if _jira_operator_resolution(store, config, row) is not None and row['event'] == event:
			if event == 'JIRA_RECONCILED' and issue_type == 'LEAF' and current['status_id'] == config['jira']['status_map']['done']:
				return owner_closeout()
			return {'planned': False, 'status': 'HELD', 'reason': 'Operator abandoned this lifecycle event; never replay or count it as success',
				'operation_id': row['operation_id'], 'writes_stopped': False, 'execution_authority': False}
	for operation_row in durable:
		_require(operation_row['record'].get('jira_provider') == provider, 'Durable Jira intent belongs to a different connector/site/project/actor')
		old_binding = operation_row['record']['binding']
		_require(set(old_binding) == set(binding), 'Jira candidate binding has missing or unrecognized fields')
	recovered = []
	try:
		if jira_assignment_state(store).get('jira_assignment_write_stops', {}).get(issue) or any(row['status'] in {'IN_FLIGHT', 'UNKNOWN', 'FAILED'} for row, table in _jira_ticket_operations(store, issue)):
			with _batch_jira_ownership(store, issue_id=issue) as owned:
				_require(owned, 'Jira issue is owned by another operation')
				recovered = _recover_jira_ticket(store, config=config, issue=issue, provider=provider,
					now=now, current_time=current_time, read_transition=read_transition,
					observe_jira_operation_identity=observe_jira_operation_identity, resume_unattempted=event == 'BLOCK', read_assignment=read_assignment if event == 'BLOCK' else None)
		else:
			# Completed batch history also binds the next ordinary write's identity.
			_recover_jira_ticket(store, config=config, issue=issue, provider=provider, now=now,
				observe_jira_operation_identity=observe_jira_operation_identity)
	except Exception as exc:
		return {'planned': False, 'reason': 'Jira recovery was not proven successful: ' + str(exc),
			'writes_stopped': True, 'current_observation': current, 'execution_authority': False}
	if recovered and recovered[-1][0]['lifecycle_event'] == event:
		record, observation = recovered[-1]
		return {'planned': False, 'current_observation': current,
			**apply_read_back(record, observation['status'], observation['actor'], observation['observed_at']),
			'execution_authority': False}
	durable = store.jira_operations(issue)
	prior_writes = []
	for operation_row in durable:
		if _jira_operator_resolution(store, config, {key: value for key, value in operation_row.items() if key != 'record'}) is not None:
			continue
		record = dict(operation_row['record'])
		record['status'] = operation_row['status']
		if operation_row['readback_json'] is not None:
			observed = _retained_jira_observation(operation_row['readback_json'])
			if observed is None:
				return {'planned': False, 'reason': 'Durable Jira receipt is corrupt; writes remain stopped', 'writes_stopped': True, 'operation_id': operation_row['operation_id'], 'current_observation': current, 'execution_authority': False}
			record['read_back'] = {'observed_status': observed.get('status_id', observed.get('status')), 'observed_actor': observed.get('actor'), 'observed_at': observed.get('observed_at')}
		prior_writes.append(record)
	if event == 'JIRA_RECONCILED' and issue_type == 'LEAF' and contract['owner_closure_required']:
		if current['status_id'] == config['jira']['status_map']['done']:
			return owner_closeout()
	key, target = planned_write(config, contract, event, facts, issue_type=issue_type, current_status=current['status_id'], prior_writes=prior_writes, state=state)
	if key is None:
		return {'planned': False, 'reason': target, 'writes_stopped': False, 'current_observation': current, 'execution_authority': False}
	record = transition_record(binding, event, current['status_id'], target, transition_id, jira_provider=provider, merge_result_id=merge_result_id, producer_id=producer_id, run_id=run_id, now=now, evidence=evidence)
	intent = store.prepare_jira_operation(record, now, identity=_jira_operation_identity(config, record))
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
	with _jira_claim_ownership(store, record['operation_id']) as claim_owned:
		_require(claim_owned, 'Jira claim is owned by another writer')
		store.begin_jira_operation(record['operation_id'], now)
		# Claim commit deliberately precedes ownership: T11 may have stopped this
		# issue in that interval. Recheck under the lock T11 needs to create a stop,
		# and retain it through write/readback and durable completion. No SQLite
		# transaction spans any provider callback.
		with _unattempted_jira_issue_ownership(store, issue, record['operation_id'], now) as issue_owned:
			if issue_owned is None:
				return {'planned': False, 'status': 'NOT_ATTEMPTED',
					'reason': 'Jira issue lock acquisition failed before any write; retry after repairing protected storage',
					'operation_id': record['operation_id'], 'writes_stopped': False, 'execution_authority': False}
			with store.connection() as db:
				batch_stop = _stopped_batch_jira_operation(db, issue)
				ordinary_stop = db.execute("SELECT 1 FROM controller_jira_operation WHERE issue_id=? AND operation_id<>? AND status IN ('IN_FLIGHT','UNKNOWN','FAILED') LIMIT 1", (issue, record['operation_id'])).fetchone()
			with store.connection() as db:
				own = db.execute('SELECT status FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			if own is None or own['status'] != 'IN_FLIGHT':
				return {'planned': False, 'reason': 'Jira claim invalidated before issue ownership; no write attempted', 'writes_stopped': True, 'execution_authority': False}
			if not issue_owned or batch_stop is not None or ordinary_stop is not None:
				_stop_unattempted_jira(store, record['operation_id'], now)
				return {'planned': False, 'reason': 'Jira issue is owned or durably stopped; no write attempted', 'writes_stopped': True, 'operation_id': record['operation_id'], 'execution_authority': False}
			try:
				policy = _observe_jira_write_policy(config, issue, observe_issue_policy, now)
				_require(policy['issue_type'] == issue_type, 'Jira issue type changed before the write')
				_require(event != 'JIRA_RECONCILED' or not owner_closure_required(config, policy['snapshot']),
					'owner_closure_required: ticket stays In Review until an owner-closure record arrives')
				require_jira_ticket_writes_allowed(store, config, binding, operation_id=record['operation_id'],
					observe_jira_operation_identity=observe_jira_operation_identity)
			except Exception as exc:
				_stop_unattempted_jira(store, record['operation_id'], now)
				return {'planned': False, 'status': 'SCOPE_REFUSED', 'reason': str(exc), 'writes_stopped': True, 'execution_authority': False}
			with store.connection() as db:
				claim = db.execute('SELECT * FROM controller_jira_operation WHERE operation_id=?', (record['operation_id'],)).fetchone()
			if claim is None or claim['status'] != 'IN_FLIGHT' or claim['record_json'] != canonical(record).decode():
				return {'planned': False, 'reason': 'Jira claim is no longer this live IN_FLIGHT claim; no write attempted', 'writes_stopped': True, 'execution_authority': False}
			try:
				try:
					operation = write_transition(record)
				except JiraTransitionNotAttempted as exc:
					_stop_unattempted_jira(store, record['operation_id'], now, retryable=True)
					return {'planned': False, 'status': 'NOT_ATTEMPTED', 'reason': str(exc),
						'operation_id': record['operation_id'], 'writes_stopped': False, 'execution_authority': False}
				_require(isinstance(operation, dict) and set(operation) == {'operation_id', 'issue_id', 'status', 'observed_at', 'jira_provider'} and (operation['operation_id'] == record['operation_id']) and (operation['issue_id'] == issue) and (operation['jira_provider'] == provider) and (operation['status'] == 'ATTEMPTED'), 'Jira write receipt is missing or mismatched')
				operation_time = timestamp(operation['observed_at'])
				_require(operation_time >= current_time, 'Jira write receipt predates the pre-write observation')
				# Persist the actual attempt receipt before the fallible read. A
				# timeout/restart can then recover with this same observer, no write.
				with store.transaction() as db:
					db.execute("UPDATE controller_jira_operation SET operation_json=? WHERE operation_id=? AND status='IN_FLIGHT'", (canonical(operation).decode(), record['operation_id']))
				observation = read_transition(deepcopy(record), deepcopy(operation))
				_require(isinstance(observation, dict) and set(observation) == {'issue_id', 'status', 'actor', 'observed_at', 'jira_provider'} and (observation['issue_id'] == issue) and (observation['jira_provider'] == provider) and (observation['actor'] == provider['controller_actor_id']), 'Jira readback has an invalid or mismatched shape')
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
