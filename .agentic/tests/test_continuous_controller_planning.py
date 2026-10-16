import hashlib
import json
import tempfile
import sys
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only
from agentic import ValidationError
from agentic.canonical import MAX_DOCUMENT_BYTES, fingerprint, sha256, timestamp
import agentic.installer as installer
from agentic.continuous_controller import ContinuousControllerStore, overlap_allowance_command, production_controller_cycle, validate_overlap_allowance_observation
from test_publication_readiness import publication_config, publication_observation
NOW = '2026-10-15T08:00:00Z'
BINDING = {'project_id': 'project-1', 'repository_id': '101', 'scope_sha256': 'a' * 64}
REPOSITORY = {'repository_root': Path(__file__).resolve().parents[2], 'repository_head_sha': 'a' * 40, 'repository_tree_sha': 'b' * 40}
COUNTS = {'required': 1, 'completed': 0, 'acceptable': 0, 'failed': 0, 'stale': 0, 'outstanding': 1}
OWNER_CONFIG = {'github': {'repository_id': 101, 'expected_actor_id': 7001}, 'merge_gate': {'trusted_owner_ids': [7002]}}

def ticket(name, priority, *, role='writer', stage='implementation'):
	return {'ticket': name, 'priority': priority, 'disposition': 'ELIGIBLE', 'actor': 'controller', 'reason': 'eligible', 'next_action': 'continue ' + name, 'resume_trigger': 'completion', 'paths': ['fixture-' + name.lower() + '.txt'], 'dependencies_satisfied': True, 'budget_available': True, 'cap_available': True, 'review_independent': True, 'exact_tuple': 'base:a/head:b/tree:c/contract:d/review:e', 'activity': 'work ' + name, 'verification_gate': 'PENDING', 'reviewer_completion': dict(COUNTS), 'open_findings': 0, 'jira_status': 'In Progress', 'dispatch_role': role, 'lifecycle_stage': stage}

def observation(items, observed_at=NOW):
	return {'source': 'host_observation', 'observed_at': observed_at, 'binding': dict(BINDING), 'complete': True, 'inventory_sha256': fingerprint('controller-inventory', {'binding': BINDING, 'tickets': items}), 'tickets': items}

def receipt(payload, observed_at=NOW):
	keys = ['dispatch_schema_version', 'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'role', 'lifecycle_stage', 'lane_start_waivers']
	return {key: payload[key] for key in keys} | {'status': 'ACCEPTED', 'dispatch_state': 'TERMINAL', 'terminal': True, 'observed_at': observed_at, 'session_id': 'session-' + payload['ticket']}

def allowance_request(**overrides):
	value = {'repository_id': '101', 'cycle_id': 'cycle-17', 'comment_id': 9001, 'path': 'src/shared.py', 'expires_at': '2026-10-15T08:30:00Z', 'pull_requests': [{'number': 11, 'ticket': 'AWF-101', 'head_sha': '1' * 40, 'changed_files': ['src/left.py', 'src/shared.py']}, {'number': 12, 'ticket': 'AWF-102', 'head_sha': '2' * 40, 'changed_files': ['src/right.py', 'src/shared.py']}]}
	value.update(overrides)
	return value

def allowance_observation(request=None, **overrides):
	request = request or allowance_request()
	provider_time = '2026-10-15T07:59:58Z'
	value = {'source': 'github.com', 'repository_id': 101, 'provider_observed_at': provider_time, 'observation_etag': '"cycle-etag"', 'comment': {'id': request['comment_id'], 'node_id': 'IC_kwDO-provider-comment', 'actor_id': 7002, 'actor_login': 'owner', 'author_association': 'OWNER', 'created_at': '2026-10-15T07:55:00Z', 'updated_at': '2026-10-15T07:55:00Z', 'body': overlap_allowance_command(request), 'etag': '"comment-etag"', 'provider_observed_at': provider_time}, 'pull_requests': [{**item, 'state': 'OPEN', 'merged': False, 'head_repository_id': 101, 'base_repository_id': 101, 'head_ref': 'codex/' + item['ticket'].lower(), 'etag': '"pr-%s"' % item['number'], 'provider_observed_at': provider_time} for item in request['pull_requests']]}
	value.update(overrides)
	return value

def allowance_inventory():
	return [ticket('AWF-101', 1) | {'paths': ['src/left.py', 'src/shared.py']}, ticket('AWF-102', 2) | {'paths': ['src/right.py', 'src/shared.py']}]

def sync_provider_observation(gate, heads, observed_at):
	return {key: deepcopy(gate[key]) for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests')} | {'heads': list(heads), 'provider_observed_at': observed_at, 'etag': '"post-sync-heads"'}

SYNC_HEADS = ['3' * 40, '2' * 40]
SYNC_DIFF = 'diff --git a/src/shared.py b/src/shared.py\n+resolved\n'

def conflict_observation(gate, observed_at, status='CONFIRMED_CONFLICT', etag='"conflict"'):
	keys = ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads')
	return {key: deepcopy(gate[key]) for key in keys} | {
		'status': status, 'provider_observed_at': observed_at, 'etag': etag}

def sync_receipt(payload, observed_at, heads=SYNC_HEADS, diff=SYNC_DIFF):
	return {**payload, 'status': 'COMPLETED', 'post_sync_heads': list(heads), 'diff': diff,
			'diff_sha256': hashlib.sha256(diff.encode('utf-8')).hexdigest(),
			'changed_files': ['src/shared.py'], 'observed_at': observed_at}

def inventory_at_heads(heads, tree='after'):
	items = allowance_inventory()
	for item, head in zip(items, heads):
		item['exact_tuple'] = f'base:a/head:{head}/tree:{tree}'
	return items

class ContinuousControllerPlanningTests(unittest.TestCase):

	def test_managed_source_payload_preserves_install_journal_headroom(self):
		root = Path(__file__).resolve().parents[2]
		captured = {}

		class JournalCaptured(BaseException):
			pass

		def capture_journal(_tree, journal, _update_kind=None):
			if journal['phase'] == 'active' and journal['managed_after_sha256'] is None:
				captured['raw'] = installer._journal_bytes(journal)
				raise JournalCaptured
			raise AssertionError('unexpected journal phase before active intent')

		with tempfile.TemporaryDirectory(prefix='awf150-journal-') as scratch:
			scratch = Path(scratch)
			source = root
			manifest_path = root / installer.MANIFEST
			manifest = (json.loads(manifest_path.read_bytes())
						if manifest_path.is_file() else None)
			if not isinstance(manifest, dict) or manifest.get('format') != 'awf-manifest-1':
				receipt = json.loads((root / installer.INSTALLED).read_bytes())
				manifest_raw = receipt['source_manifest_json'].encode('utf-8')
				self.assertEqual(receipt['source_manifest_sha256'], sha256(manifest_raw))
				manifest = json.loads(manifest_raw)
				source = scratch / 'source'
				(source / installer.MANIFEST).parent.mkdir(parents=True, exist_ok=True)
				(source / installer.MANIFEST).write_bytes(manifest_raw)
				for path in manifest['files']:
					installed_path = root / path
					if installed_path.is_file():
						target = source / path
						target.parent.mkdir(parents=True, exist_ok=True)
						target.write_bytes(installed_path.read_bytes())
			else:
				manifest_raw = manifest_path.read_bytes()
			content = {path: (source / path).read_bytes() for path in manifest['files']
					   if (source / path).is_file()}
			self.assertIn('.agentic/lib/agentic/installer.py', content)

			with patch.object(installer, 'verify_release',
							  return_value=(sha256(manifest_raw), content)), \
					patch.object(installer, '_write_bound_journal_update',
							 side_effect=capture_journal), \
					self.assertRaises(JournalCaptured):
				installer.install(source, scratch / 'destination', sha256(manifest_raw),
							  conflict='backup')

		self.assertLessEqual(len(captured['raw']), installer.INSTALL_JOURNAL_MAX_BYTES)
		self.assertLessEqual(len(captured['raw']) + installer.INSTALL_JOURNAL_HEADROOM_BYTES,
						 MAX_DOCUMENT_BYTES,
			'compact managed-file recovery metadata leaves insufficient journal headroom')

	def setUp(self):
		temporary = tempfile.TemporaryDirectory(prefix='awf89-planning-')
		self.addCleanup(temporary.cleanup)
		self.store = ContinuousControllerStore(Path(temporary.name) / 'controller.sqlite3', ['A', 'B'])
		self.repository_patch = patch('agentic.continuous_controller.canonical_repository_paths', side_effect=lambda items, *_: (items, {'repository_root': '/fixture', 'head_sha': 'a' * 40, 'tree_sha': 'b' * 40, 'git_path_count': 0}))
		self.repository_patch.start()
		self.addCleanup(self.repository_patch.stop)
		self.reservation_patch = patch('agentic.continuous_controller.canonical_reservation_sources', return_value=[])
		self.reservation_patch.start()
		self.addCleanup(self.reservation_patch.stop)
		self.skill_patch = patch('agentic.codex_skill.observe_codex_awf_skills', return_value={'format': 'awf-codex-skill-selection-1', 'expected_version': '1.9.4', 'project_root': '/fixture', 'pinned_skill_path': '/fixture/skill', 'launch_directory': '/fixture', 'visible_skills': ['awf'], 'status': 'PASS', 'problems': []})
		self.skill_patch.start()
		self.addCleanup(self.skill_patch.stop)

	def cycle(self, items, *, capacity=2, dispatch=None):
		calls = []
		dispatch = dispatch or (lambda payload: calls.append(dict(payload)) or receipt(payload))
		result = production_controller_cycle(self.store, now=NOW, host_capacity=capacity, inventory_binding=BINDING, **REPOSITORY, observe_inventory=lambda: observation(items), observe_reservations=lambda request: {}, dispatch_ticket=dispatch, observe_dispatch=lambda payload: self.fail('unexpected reconciliation'), deliver_status=lambda digest: {'delivery_id': digest['delivery_id'], 'status': 'DELIVERED', 'observed_at': NOW}, publication_config=publication_config(), observe_publication=lambda item: publication_observation(item['ticket'], NOW))
		return (calls, result)

	def test_production_cycle_dispatches_mixed_writer_and_critic_per_ticket(self):
		calls, result = self.cycle([ticket('AWF-101', 1, role='writer', stage='implementation'), ticket('AWF-102', 2, role='critic', stage='independent_review')])
		self.assertEqual([(item['ticket'], item['role'], item['lifecycle_stage']) for item in calls], [('AWF-101', 'writer', 'implementation'), ('AWF-102', 'critic', 'independent_review')])
		self.assertEqual(len(result['dispatch_receipts']), 2)

	def test_role_authority_mismatch_fails_closed_independent_of_capacity(self):
		with self.assertRaisesRegex(ValidationError, 'role|lifecycle'):
			self.cycle([ticket('AWF-103', 1, role='critic', stage='implementation')], capacity=2, dispatch=lambda payload: self.fail('mismatched role dispatched'))

	def test_host_capacity_limits_homogeneous_roles_independent_of_role_authority(self):
		calls, result = self.cycle([ticket('AWF-104', 1), ticket('AWF-105', 2)], capacity=1)
		self.assertEqual([(item['ticket'], item['role']) for item in calls], [('AWF-104', 'writer')])
		self.assertEqual(len(result['dispatch_receipts']), 1)

class OverlapAllowanceBoundaryTests(unittest.TestCase):
	def test_lane_start_provider_observation_occurs_outside_write_transaction(self):
		calls = []
		def observer(request, phase):
			if phase == 'LANE_START':
				with self.store.connection() as second:
					second.execute("INSERT OR REPLACE INTO controller_meta VALUES('observer-write','ok')")
			calls.append(phase)
			value = allowance_observation(request)
			for item in value['pull_requests']:
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value
		self._cycle_with_allowance(observe_overlap_allowance=observer)
		self.assertEqual(calls, ['PLAN', 'LANE_START'])

	def test_lane_start_waiver_is_persisted_before_dispatch_begin(self):
		seen = []
		def dispatch(payload):
			with self.store.connection() as db:
				rows = db.execute('SELECT record_json FROM controller_dispatch_waiver WHERE dispatch_id=?', (payload['dispatch_id'],)).fetchall()
			seen.append((payload, [json.loads(row[0]) for row in rows]))
			return receipt(payload)
		self._cycle_with_allowance(dispatch_ticket=dispatch)
		self.assertEqual(len(seen), 2)
		for payload, records in seen:
			self.assertTrue(records and payload['lane_start_waivers'] == [records[0]['waiver_sha256']])
			self.assertEqual(records[0]['binding']['dispatch_id'], payload['dispatch_id'])

	def test_lane_start_waiver_survives_crash_before_receipt(self):
		def crash(payload):
			raise RuntimeError('crash after durable waiver assertion')
		self._cycle_with_allowance(dispatch_ticket=crash)
		reopened = ContinuousControllerStore(self.store.path, ['A', 'B'])
		operations = reopened.prepare_dispatches(NOW)
		self.assertTrue(operations and all(item['status'] == 'UNKNOWN' for item in operations))
		for operation in operations:
			payload = reopened.begin_dispatch_reconciliation(operation['dispatch_id'], NOW)
			self.assertTrue(payload['lane_start_waivers'])
			with reopened.connection() as db:
				digests = [json.loads(row[0])['waiver_sha256'] for row in db.execute('SELECT record_json FROM controller_dispatch_waiver WHERE dispatch_id=?', (operation['dispatch_id'],))]
			self.assertEqual(payload['lane_start_waivers'], digests)

	def test_authenticated_parked_reservation_blocks_start_even_with_allowance(self):
		items = allowance_inventory() + [ticket('AWF-103', 3) | {'paths': ['src/unrelated.py']}]
		reservation = {'schema_version': 1, 'kind': 'parked', 'source': 'github.com', 'repository_id': '101', 'head_sha': 'a' * 40, 'tree_sha': 'b' * 40, 'artifact_id': 'parked-1', 'observed_at': NOW, 'authenticated': True, 'complete': True, 'paths': ['src/shared.py']}
		reservation['evidence_sha256'] = fingerprint('controller-reservation-source', reservation)
		streams = self.store.schedule(items, NOW, 3, [{'path': 'src/shared.py', 'tickets': ['AWF-101', 'AWF-102']}], reservations=[reservation])
		self.assertEqual({item['ticket'] for item in streams if item['state'] == 'WORKING'}, {'AWF-103'})

	def test_authenticated_worktree_diff_blocks_start_even_with_allowance(self):
		items = allowance_inventory()
		reservation = {'schema_version': 1, 'kind': 'worktree_diff', 'source': 'git', 'repository_id': '101', 'head_sha': 'a' * 40, 'tree_sha': 'b' * 40, 'artifact_id': 'diff-1', 'observed_at': NOW, 'authenticated': True, 'complete': True, 'paths': ['src/shared.py']}
		reservation['evidence_sha256'] = fingerprint('controller-reservation-source', reservation)
		streams = self.store.schedule(items, NOW, 2, [{'path': 'src/shared.py', 'tickets': ['AWF-101', 'AWF-102']}], reservations=[reservation])
		self.assertFalse(any(item['state'] == 'WORKING' for item in streams))

	def setUp(self):
		temporary = tempfile.TemporaryDirectory(prefix='awf90-overlap-')
		self.addCleanup(temporary.cleanup)
		self.path = Path(temporary.name) / 'controller.sqlite3'
		self.store = ContinuousControllerStore(self.path, ['A', 'B'])
		for target, value in (
			('agentic.continuous_controller.canonical_repository_paths',
			 lambda items, *_: (items, {'repository_root': '/fixture', 'head_sha': 'a' * 40,
								'tree_sha': 'b' * 40, 'git_path_count': 0})),
			('agentic.codex_skill.observe_codex_awf_skills',
			 {'format': 'awf-codex-skill-selection-1', 'expected_version': '1.9.4',
			  'project_root': '/fixture', 'pinned_skill_path': '/fixture/skill',
			  'launch_directory': '/fixture', 'visible_skills': ['awf'], 'status': 'PASS', 'problems': []}),
			('agentic.continuous_controller.canonical_reservation_sources', lambda *_: [])):
			mock = patch(target, side_effect=value) if callable(value) else patch(target, return_value=value)
			mock.start()
			self.addCleanup(mock.stop)

	def cycle(self, items, *, store=None, now=NOW, capacity=2, observe_inventory=None,
			  dispatch_ticket=None, publication=None, controller_clock=None, **operations):
		store = store or self.store
		observe_inventory = observe_inventory or (
			lambda controller_now=None: observation(items, controller_now or now))
		dispatch_ticket = dispatch_ticket or (lambda payload: self.fail('unexpected dispatch'))
		arguments = {
			'observe_inventory': observe_inventory,
			'observe_reservations': lambda request: {},
			'dispatch_ticket': dispatch_ticket,
			'observe_dispatch': lambda payload: self.fail('unexpected dispatch reconciliation'),
			'deliver_status': lambda digest: {'delivery_id': digest['delivery_id'],
										 'status': 'DELIVERED', 'observed_at': now},
			'publication_config': publication or publication_config(),
			'observe_publication': lambda item: publication_observation(item['ticket'], now),
			'controller_clock': controller_clock or (lambda: now),
		}
		arguments.update(operations)
		return production_controller_cycle(store, now=now, host_capacity=capacity,
			inventory_binding=BINDING, **REPOSITORY, **arguments)

	def validate(self, request=None, observation=None, **kwargs):
		request = request or allowance_request()
		observation = observation or allowance_observation(request)
		expected_repository_id = kwargs.pop('expected_repository_id', '101')
		expected_cycle_id = kwargs.pop('expected_cycle_id', 'cycle-17')
		return validate_overlap_allowance_observation(OWNER_CONFIG, request, observation, allowance_inventory(), now=NOW, expected_repository_id=expected_repository_id, expected_cycle_id=expected_cycle_id, **kwargs)

	def _cycle_with_allowance(self, **overrides):
		cfg = publication_config()
		cfg['github']['repository_id'] = 101
		cfg.setdefault('merge_gate', {})['trusted_owner_ids'] = [7002]
		def observe(request, phase):
			value = allowance_observation(request)
			for item in value['pull_requests']:
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value
		arguments = {'publication': cfg, 'cycle_id': 'cycle-17', 'overlap_allowance_requests': [allowance_request()], 'observe_overlap_allowance': observe, 'dispatch_ticket': lambda payload: receipt(payload)}
		arguments.update(overrides)
		return self.cycle(allowance_inventory(), **arguments)

	def test_canonical_owner_comment_is_the_only_authority_and_replay_is_refused(self):
		request = allowance_request()
		observation = allowance_observation(request)
		allowance = self.validate(request, observation)
		self.assertEqual('github-comment:101:9001:IC_kwDO-provider-comment', allowance['source_id'])
		self.store.register_overlap_allowance(allowance, NOW)
		mutated = deepcopy(observation)
		mutated['comment']['body'] += ' '
		with self.assertRaisesRegex(ValidationError, 'canonical|comment'):
			self.validate(request, mutated)
		edited = deepcopy(observation)
		edited['comment']['updated_at'] = '2026-10-15T07:56:00Z'
		with self.assertRaisesRegex(ValidationError, 'edited|mutated'):
			self.validate(request, edited)
		replay = self.validate(allowance_request(cycle_id='cycle-18'), allowance_observation(allowance_request(cycle_id='cycle-18')), expected_cycle_id='cycle-18')
		with self.assertRaisesRegex(ValidationError, 'replay'):
			self.store.register_overlap_allowance(replay, NOW)
		copied_request = allowance_request(comment_id=9002)
		copied = self.validate(copied_request, allowance_observation(copied_request))
		with self.assertRaisesRegex(ValidationError, 'replay'):
			self.store.register_overlap_allowance(copied, NOW)

	def test_authenticated_automation_actor_is_not_owner_authority(self):
		observed = allowance_observation()
		observed['comment']['actor_id'] = OWNER_CONFIG['github']['expected_actor_id']
		observed['comment']['actor_login'] = 'automation-account'
		with self.assertRaisesRegex(ValidationError, 'configured owner'):
			self.validate(observation=observed)

	def test_foreign_repo_stale_head_file_drift_and_ownership_forgery_fail_closed(self):
		mutations = {}
		foreign = allowance_observation()
		foreign['repository_id'] = 202
		mutations['foreign repository'] = foreign
		stale = allowance_observation()
		stale['pull_requests'][0]['head_sha'] = 'f' * 40
		mutations['stale head'] = stale
		drift = allowance_observation()
		drift['pull_requests'][1]['changed_files'] = ['src/right.py']
		mutations['file drift'] = drift
		forged = allowance_observation()
		forged['pull_requests'][0]['head_ref'] = 'codex/awf-999'
		mutations['ownership forgery'] = forged
		for label, observed in mutations.items():
			with self.subTest(label=label), self.assertRaises(ValidationError):
				self.validate(observation=observed)

	def test_freshness_comes_from_provider_timestamp_and_etag_not_controller_now(self):
		echoed = allowance_observation()
		echoed['provider_observed_at'] = NOW
		echoed['observation_etag'] = None
		echoed['comment']['provider_observed_at'] = NOW
		echoed['comment']['etag'] = None
		for item in echoed['pull_requests']:
			item['provider_observed_at'] = NOW
			item['etag'] = None
		with self.assertRaisesRegex(ValidationError, 'etag|provider'):
			self.validate(observation=echoed)
		stale = allowance_observation()
		old = (timestamp(NOW) - timedelta(minutes=6)).isoformat().replace('+00:00', 'Z')
		stale['provider_observed_at'] = old
		with self.assertRaisesRegex(ValidationError, 'fresh'):
			self.validate(observation=stale)

	def test_cycle_samples_controller_time_after_each_provider_allowance_observation(self):
		cfg = publication_config()
		cfg['github']['repository_id'] = 101
		cfg.setdefault('merge_gate', {})['trusted_owner_ids'] = [7002]
		items = allowance_inventory()
		provider_time = '2026-10-15T08:00:01Z'
		controller_times = iter(('2026-10-15T08:00:02Z', '2026-10-15T08:00:03Z'))
		dispatched = []

		def observe(request, phase):
			value = allowance_observation(request, provider_observed_at=provider_time)
			value['comment']['provider_observed_at'] = provider_time
			for item in value['pull_requests']:
				item['provider_observed_at'] = provider_time
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value

		result = self.cycle(items, publication=cfg, controller_clock=lambda: next(controller_times),
			dispatch_ticket=lambda payload: dispatched.append(payload['ticket']) or receipt(payload),
			cycle_id='cycle-17', overlap_allowance_requests=[allowance_request()],
			observe_overlap_allowance=observe)
		self.assertEqual(['AWF-101', 'AWF-102'], dispatched, result)
		self.assertEqual(['ACTIVE', 'ACTIVE'], [row['status'] for row in result['overlap_allowances']])

	def test_cycle_uses_post_provider_controller_time_for_allowance_expiry(self):
		cfg = publication_config()
		cfg['github']['repository_id'] = 101
		cfg.setdefault('merge_gate', {})['trusted_owner_ids'] = [7002]
		items = allowance_inventory()
		request = allowance_request(expires_at='2026-10-15T08:00:01Z')

		def observe(current, phase):
			value = allowance_observation(current, provider_observed_at='2026-10-15T08:00:01Z')
			value['comment']['provider_observed_at'] = '2026-10-15T08:00:01Z'
			for item in value['pull_requests']:
				item['provider_observed_at'] = '2026-10-15T08:00:01Z'
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value

		result = self.cycle(items, publication=cfg, controller_clock=lambda: '2026-10-15T08:00:02Z',
			cycle_id='cycle-17', overlap_allowance_requests=[request], observe_overlap_allowance=observe)
		expired = [row for row in result['overlap_allowances'] if row['phase'] == 'EXPIRED']
		self.assertEqual(1, len(expired), result)
		self.assertEqual('PENDING', expired[0]['status'])
		self.assertEqual('EXPIRED', self.store.overlap_allowance(expired[0]['allowance_id'])['status'])

	def test_expiry_sync_gate_is_pair_bound_persistent_and_diff_reaches_critic(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		expired_at = '2026-10-15T08:31:00Z'
		gate = self.store.expire_overlap_allowance(allowance['allowance_id'], expired_at)
		restarted = ContinuousControllerStore(self.path, ['A', 'B'])
		self.assertEqual(gate, restarted.overlap_sync_gate(gate['gate_id']))
		no_conflict = conflict_observation(gate, expired_at, 'NO_CONFLICT', '"merge-tree-1"')
		calls = []
		result = restarted.execute_overlap_sync_gate(gate['gate_id'], no_conflict, lambda payload: calls.append(payload), expired_at)
		self.assertEqual('CLEARED', result['status'])
		self.assertEqual([], calls)
		conflict_store = ContinuousControllerStore(self.path.with_name('conflict.sqlite3'), ['A', 'B'])
		conflict_store.register_overlap_allowance(allowance, NOW)
		second = conflict_store.expire_overlap_allowance(allowance['allowance_id'], '2026-10-15T08:32:00Z')
		conflict = conflict_observation(second, '2026-10-15T08:32:00Z', etag='"merge-tree-2"')
		diff, post_sync_heads = SYNC_DIFF, SYNC_HEADS

		def sync(payload):
			calls.append(payload)
			return sync_receipt(payload, '2026-10-15T08:32:01Z')
		synced = conflict_store.execute_overlap_sync_gate(second['gate_id'], conflict, sync, '2026-10-15T08:32:00Z')
		again = conflict_store.execute_overlap_sync_gate(second['gate_id'], conflict, sync, '2026-10-15T08:32:02Z')
		self.assertEqual('SYNCED', synced['status'])
		self.assertEqual(synced, again)
		self.assertEqual(1, len(calls))
		refreshed = allowance_inventory()
		refreshed[0]['exact_tuple'] = 'base:a/head:' + post_sync_heads[0] + '/tree:c'
		refreshed[1]['exact_tuple'] = 'base:a/head:' + post_sync_heads[1] + '/tree:c'
		completed = conflict_store.confirm_overlap_sync_inventory(second['gate_id'], refreshed, inventory_sha256='e' * 64, observed_at='2026-10-15T08:32:02Z', provider_observation=sync_provider_observation(synced, post_sync_heads, '2026-10-15T08:32:02Z'))
		self.assertEqual('EXECUTED', completed['status'])
		evidence = conflict_store.critic_overlap_evidence('AWF-101')
		self.assertEqual(diff, evidence[0]['diff'])
		self.assertEqual(post_sync_heads, evidence[0]['post_sync_heads'])
		self.assertEqual(post_sync_heads[0], evidence[0]['post_sync_inventory'][0]['head_sha'])
		critic = ticket('AWF-101', 1, role='critic', stage='independent_review')
		conflict_store.schedule([critic], '2026-10-15T08:32:02Z', 1)
		dispatch = conflict_store.prepare_dispatches('2026-10-15T08:32:02Z')[0]['payload']
		self.assertEqual(diff, dispatch['overlap_sync_evidence'][0]['diff'])
		forged_store = ContinuousControllerStore(self.path.with_name('forged-head.sqlite3'), ['A', 'B'])
		forged_store.register_overlap_allowance(allowance, NOW)
		forged_gate = forged_store.expire_overlap_allowance(allowance['allowance_id'], '2026-10-15T08:33:00Z')
		forged_conflict = {**conflict, 'gate_id': forged_gate['gate_id']}

		def forged_sync(payload):
			value = sync(payload)
			value['observed_at'] = '2026-10-15T08:33:01Z'
			return value
		forged_store.execute_overlap_sync_gate(forged_gate['gate_id'], forged_conflict, forged_sync, '2026-10-15T08:33:00Z')
		forged_inventory = allowance_inventory()
		forged_inventory[0]['exact_tuple'] = 'base:' + post_sync_heads[0] + '/head:' + '9' * 40 + '/tree:c'
		forged_inventory[1]['exact_tuple'] = 'base:a/head:' + post_sync_heads[1] + '/tree:c'
		with self.assertRaisesRegex(ValidationError, 'post-sync PR head'):
			forged_store.confirm_overlap_sync_inventory(forged_gate['gate_id'], forged_inventory, inventory_sha256='f' * 64, observed_at='2026-10-15T08:33:02Z', provider_observation=sync_provider_observation(forged_gate, post_sync_heads, '2026-10-15T08:33:02Z'))

	def test_unknown_sync_gate_reconciles_retained_receipt_without_repeating_mutation(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		executed_at = '2026-10-15T08:32:00Z'
		gate = self.store.expire_overlap_allowance(allowance['allowance_id'], executed_at)
		decision = conflict_observation(gate, executed_at)
		diff, post_sync_heads = SYNC_DIFF, SYNC_HEADS
		mutation_calls = []
		retained = {}

		def durable_completion(payload):
			mutation_calls.append(deepcopy(payload))
			retained['receipt'] = sync_receipt(payload, '2026-10-15T08:32:01Z')
			return deepcopy(retained['receipt'])

		with patch.object(self.store, '_finish_overlap_sync_receipt', side_effect=SystemExit('controller crashed after durable completion')):
			with self.assertRaises(SystemExit):
				self.store.execute_overlap_sync_gate(gate['gate_id'], decision, durable_completion, executed_at)
		self.assertEqual('IN_FLIGHT', self.store.overlap_sync_gate(gate['gate_id'])['status'])
		restarted = ContinuousControllerStore(self.path, ['A', 'B'])
		self.assertEqual('UNKNOWN', restarted.overlap_sync_gate(gate['gate_id'])['status'])
		post_sync = allowance_inventory()
		post_sync[0]['exact_tuple'] = 'base:a/head:' + post_sync_heads[0] + '/tree:after'
		post_sync[1]['exact_tuple'] = 'base:a/head:' + post_sync_heads[1] + '/tree:after'
		reconcile_calls = []

		def reconcile(payload):
			reconcile_calls.append(deepcopy(payload))
			return deepcopy(retained['receipt'])

		result = self.cycle(post_sync, store=restarted, now='2026-10-15T08:32:02Z', capacity=1,
			dispatch_ticket=lambda payload: receipt(payload, '2026-10-15T08:32:02Z'),
			overlap_allowance_requests=[], reconcile_overlap_sync=reconcile,
			observe_overlap_sync_inventory=lambda current: sync_provider_observation(
				current, post_sync_heads, '2026-10-15T08:32:02Z'))
		self.assertEqual(1, len(mutation_calls))
		self.assertEqual(1, len(reconcile_calls))
		self.assertEqual(mutation_calls[0], reconcile_calls[0])
		self.assertEqual('EXECUTED', restarted.overlap_sync_gate(gate['gate_id'])['status'])
		self.assertFalse(any((error['operation'] == 'overlap_sync' for error in result['errors'])), result)

	def test_expiry_defers_sync_while_bound_writer_session_is_not_terminal(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		active = allowance_inventory()
		for item, head in zip(active, ['1' * 40, '2' * 40]):
			item['exact_tuple'] = 'base:a/head:' + head + '/tree:before'
		self.store.schedule(active, NOW, 2, overlap_allowances=[{'path': 'src/shared.py', 'tickets': ['AWF-101', 'AWF-102']}])
		for pending in self.store.prepare_dispatches(NOW):
			payload = self.store.begin_dispatch(pending['dispatch_id'], NOW)
			launched = receipt(payload) | {'dispatch_state': 'LAUNCHED', 'terminal': False}
			self.store.finish_dispatch(pending['dispatch_id'], launched, NOW)
		expired_at = '2026-10-15T08:31:00Z'
		sync_calls = []

		def conflict(gate):
			return conflict_observation(gate, expired_at)

		def sync(payload):
			sync_calls.append(deepcopy(payload))
			return sync_receipt(payload, expired_at)
		result = self.cycle(active, now=expired_at, overlap_allowance_requests=[],
			observe_overlap_conflict=conflict, sync_writer=sync)
		self.assertEqual([], sync_calls, result)
		pending_gate = self.store.unresolved_overlap_sync_gates()[0]
		self.assertEqual('PENDING', pending_gate['status'])
		post_sync = deepcopy(active)
		post_sync[0]['exact_tuple'] = 'base:a/head:' + '3' * 40 + '/tree:after'
		inventory_calls = []

		def observe_after_quiescence():
			items = active if not inventory_calls else post_sync
			inventory_calls.append(deepcopy(items))
			return observation(items, expired_at)

		def terminal(payload):
			return {'dispatch_id': payload['dispatch_id'], 'ticket': payload['ticket'], 'exact_tuple': payload['exact_tuple'], 'session_id': 'session-' + payload['ticket'], 'status': 'QUIESCENT', 'terminal': True, 'observed_at': expired_at}
		resumed = self.cycle(active, now=expired_at, observe_inventory=observe_after_quiescence,
			overlap_allowance_requests=[], observe_overlap_conflict=conflict, sync_writer=sync,
			observe_dispatch_quiescence=terminal,
			observe_overlap_sync_inventory=lambda gate: sync_provider_observation(
				gate, ['3' * 40, '2' * 40], expired_at))
		self.assertEqual(1, len(sync_calls), resumed)
		self.assertEqual('EXECUTED', self.store.overlap_sync_gate(pending_gate['gate_id'])['status'])

	def test_cycle_sweeps_persisted_expiry_without_request_resubmission(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		restarted = ContinuousControllerStore(self.path, ['A', 'B'])
		expired_at = '2026-10-15T08:31:00Z'
		items = allowance_inventory()
		result = self.cycle(items, store=restarted, now=expired_at, overlap_allowance_requests=[])
		swept = [item for item in result['overlap_allowances'] if item['phase'] == 'SWEEP_EXPIRED']
		self.assertEqual(1, len(swept))
		self.assertEqual('PENDING', swept[0]['status'])
		self.assertEqual('EXPIRED', restarted.overlap_allowance(allowance['allowance_id'])['status'])
		self.assertEqual('PENDING', restarted.overlap_sync_gate(swept[0]['gate_id'])['status'])
		self.assertTrue(all((stream['state'] != 'WORKING' for stream in result['streams'])))

	def test_cycle_refreshes_inventory_after_sync_and_dispatches_post_sync_tuple(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		restarted = ContinuousControllerStore(self.path, ['A', 'B'])
		expired_at = '2026-10-15T08:31:00Z'
		pre_sync = allowance_inventory()
		for item, head in zip(pre_sync, ['1' * 40, '2' * 40]):
			item['dispatch_role'] = 'critic'
			item['lifecycle_stage'] = 'independent_review'
			item['exact_tuple'] = 'base:a/head:' + head + '/tree:before'
		post_sync = deepcopy(pre_sync)
		post_sync_heads = SYNC_HEADS
		post_sync[0]['exact_tuple'] = 'base:a/head:' + post_sync_heads[0] + '/tree:after'
		diff = SYNC_DIFF
		inventory_calls = []
		dispatched = []

		def observe_inventory():
			items = pre_sync if not inventory_calls else post_sync
			inventory_calls.append(deepcopy(items))
			return observation(items, expired_at)

		def observe_conflict(gate):
			return conflict_observation(gate, expired_at)

		def sync(payload):
			return sync_receipt(payload, expired_at)
		result = self.cycle(pre_sync, store=restarted, now=expired_at, capacity=1,
			observe_inventory=observe_inventory,
			dispatch_ticket=lambda payload: dispatched.append(deepcopy(payload)) or receipt(payload, expired_at),
			overlap_allowance_requests=[], observe_overlap_conflict=observe_conflict, sync_writer=sync,
			observe_overlap_sync_inventory=lambda gate: sync_provider_observation(gate, post_sync_heads, expired_at))
		self.assertEqual(2, len(inventory_calls))
		self.assertEqual(1, len(dispatched), result)
		self.assertEqual(post_sync[0]['exact_tuple'], dispatched[0]['exact_tuple'])
		evidence = dispatched[0]['overlap_sync_evidence'][0]
		self.assertEqual(diff, evidence['diff'])
		self.assertEqual(post_sync_heads, evidence['post_sync_heads'])

	def test_production_cycle_rechecks_at_plan_and_lane_start(self):
		cfg = publication_config()
		cfg['github']['repository_id'] = 101
		cfg.setdefault('merge_gate', {})['trusted_owner_ids'] = [7002]
		items = allowance_inventory()
		phases, dispatched = ([], [])

		def observe(request, phase):
			phases.append(phase)
			value = allowance_observation(request)
			for item in value['pull_requests']:
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value
		result = self.cycle(items, publication=cfg,
			dispatch_ticket=lambda payload: dispatched.append(payload['ticket']) or receipt(payload),
			cycle_id='cycle-17', overlap_allowance_requests=[allowance_request()],
			observe_overlap_allowance=observe)
		self.assertEqual(['PLAN', 'LANE_START'], phases)
		self.assertEqual(['AWF-101', 'AWF-102'], dispatched, result)
		self.assertEqual(['ACTIVE', 'ACTIVE'], [row['status'] for row in result['overlap_allowances']])

	def test_lane_start_reobserves_inventory_and_blocks_path_ownership_drift(self):
		cfg = publication_config()
		cfg['github']['repository_id'] = 101
		cfg.setdefault('merge_gate', {})['trusted_owner_ids'] = [7002]
		planned = allowance_inventory()
		drifted = deepcopy(planned)
		drifted[1]['paths'] = ['src/right.py']
		inventory_calls, dispatched = ([], [])

		def observe_inventory(controller_now=None):
			items = planned if not inventory_calls else drifted
			inventory_calls.append(deepcopy(items))
			return observation(items, controller_now or NOW)

		def observe_allowance(request, _phase):
			value = allowance_observation(request)
			for item in value['pull_requests']:
				item['head_ref'] = 'codex/' + item['ticket'] + '-' + item['ticket'].lower()
			return value

		result = self.cycle(planned, publication=cfg, observe_inventory=observe_inventory,
			dispatch_ticket=lambda payload: dispatched.append(payload['ticket']) or receipt(payload),
			cycle_id='cycle-17', overlap_allowance_requests=[allowance_request()],
			observe_overlap_allowance=observe_allowance)
		self.assertEqual(2, len(inventory_calls), result)
		self.assertEqual([], dispatched, result)
		self.assertTrue(any(row['phase'] == 'LANE_START' and row['status'] == 'REFUSED'
							for row in result['overlap_allowances']), result)

	def test_post_expiry_provider_reads_use_post_read_controller_times(self):
		allowance = self.validate()
		self.store.register_overlap_allowance(allowance, NOW)
		restarted = ContinuousControllerStore(self.path, ['A', 'B'])
		cycle_now = '2026-10-15T08:31:00Z'
		conflict_time = '2026-10-15T08:31:10Z'
		post_sync_time = '2026-10-15T08:31:20Z'
		pre_sync = allowance_inventory()
		for item, head in zip(pre_sync, ['1' * 40, '2' * 40]):
			item['dispatch_role'] = 'critic'
			item['lifecycle_stage'] = 'independent_review'
			item['exact_tuple'] = 'base:a/head:' + head + '/tree:before'
		post_sync_heads = SYNC_HEADS
		post_sync = deepcopy(pre_sync)
		post_sync[0]['exact_tuple'] = 'base:a/head:' + post_sync_heads[0] + '/tree:after'
		inventory_calls, clock_calls, dispatched = ([], [], [])

		def observe_inventory(controller_now=None):
			items = pre_sync if not inventory_calls else post_sync
			inventory_calls.append(deepcopy(items))
			return observation(items, controller_now or cycle_now)

		def controller_clock():
			value = [conflict_time, post_sync_time][len(clock_calls)]
			clock_calls.append(value)
			return value

		def observe_conflict(gate):
			return conflict_observation(gate, conflict_time, etag='"conflict-after-cycle"')

		diff = SYNC_DIFF

		def sync(payload):
			return sync_receipt(payload, conflict_time)

		result = self.cycle(pre_sync, store=restarted, now=cycle_now, capacity=1,
			controller_clock=controller_clock, observe_inventory=observe_inventory,
			dispatch_ticket=lambda payload: dispatched.append(deepcopy(payload)) or receipt(payload, post_sync_time),
			deliver_status=lambda digest: {'delivery_id': digest['delivery_id'],
										 'status': 'DELIVERED', 'observed_at': post_sync_time},
			overlap_allowance_requests=[], observe_overlap_conflict=observe_conflict, sync_writer=sync,
			observe_overlap_sync_inventory=lambda gate: sync_provider_observation(
				gate, post_sync_heads, post_sync_time))
		gate = restarted.unresolved_overlap_sync_gates()
		self.assertEqual([], gate, result)
		self.assertEqual([conflict_time, post_sync_time], clock_calls, result)
		self.assertEqual(2, len(inventory_calls), result)
		self.assertEqual(1, len(dispatched), result)
		self.assertEqual(diff, dispatched[0]['overlap_sync_evidence'][0]['diff'])
if __name__ == '__main__':
	unittest.main()
