import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from source_only import COMMITTED_GIT_REASON,committed_git_tuple
from agentic.continuous_controller import ContinuousControllerStore,canonical_repository_paths,canonical_reservation_sources,production_controller_cycle as real_production_controller_cycle,production_jira_lifecycle,production_merge_observed,post_merge_jira_progress
from agentic import continuous_controller
from agentic.contracts import Contracts,schema_inventory
from agentic import ValidationError
from agentic.canonical import fingerprint,sha256
from agentic.lifecycle import definition
from agentic.policy import policy_hash
from test_publication_readiness import publication_config,publication_observation
from test_continuous_controller_planning import planning_fixture_inputs
from agentic.cli import local_semantics
ROOT=Path(__file__).resolve().parents[2]
NOW='2026-10-02T10:00:00Z'
BINDING={'cloud_id':'cloud-1','project_id':'project-1','actor_id':'actor-1'}
COUNTS={'required':1,'completed':0,'acceptable':0,'failed':0,'stale':0,'outstanding':1}
INVENTORY_BINDING={'project_id':'project-1','repository_id':'101','scope_sha256':'a'*64}
SOURCE_GIT_TUPLE=committed_git_tuple(ROOT)
REPOSITORY={'repository_root':ROOT,'repository_head_sha':SOURCE_GIT_TUPLE[0]if SOURCE_GIT_TUPLE else '0'*40,'repository_tree_sha':SOURCE_GIT_TUPLE[1]if SOURCE_GIT_TUPLE else '0'*40}
LEGACY_194_DDL='''
CREATE TABLE controller_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE stream_status(
 stream_id TEXT PRIMARY KEY,state TEXT NOT NULL,ticket TEXT,actor TEXT NOT NULL,
 reason TEXT NOT NULL,next_action TEXT NOT NULL,resume_trigger TEXT NOT NULL,
 exact_tuple TEXT NOT NULL,activity TEXT NOT NULL,verification_gate TEXT NOT NULL,
 reviewer_completion TEXT NOT NULL,open_findings INTEGER NOT NULL,
 jira_status TEXT NOT NULL,paths TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE controller_outbox(
 delivery_id TEXT PRIMARY KEY,kind TEXT NOT NULL,revision INTEGER NOT NULL,
 observed_at TEXT NOT NULL,payload_json TEXT NOT NULL,acknowledged_at TEXT);
CREATE TABLE controller_dispatch(
 dispatch_id TEXT PRIMARY KEY,stream_id TEXT NOT NULL,ticket TEXT NOT NULL,
 exact_tuple TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,
 receipt_json TEXT,updated_at TEXT NOT NULL);
CREATE TABLE controller_ticket_authority(
 ticket TEXT NOT NULL,exact_tuple TEXT NOT NULL,dispatch_role TEXT NOT NULL,
 lifecycle_stage TEXT NOT NULL,observed_at TEXT NOT NULL,
 PRIMARY KEY(ticket,exact_tuple));
CREATE TABLE controller_overlap_allowance(
 allowance_id TEXT PRIMARY KEY,source_id TEXT NOT NULL UNIQUE,repository_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,path TEXT NOT NULL,tickets_json TEXT NOT NULL,
 pull_requests_json TEXT NOT NULL,heads_json TEXT NOT NULL,expires_at TEXT NOT NULL,
 status TEXT NOT NULL,record_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE controller_overlap_sync_gate(
 gate_id TEXT PRIMARY KEY,allowance_id TEXT NOT NULL,status TEXT NOT NULL,
 record_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX controller_overlap_gate_allowance
 ON controller_overlap_sync_gate(allowance_id,status);
CREATE TABLE controller_jira_operation(
 operation_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,event TEXT NOT NULL,
 intent_sha256 TEXT NOT NULL,status TEXT NOT NULL,record_json TEXT NOT NULL,
 operation_json TEXT,readback_json TEXT,updated_at TEXT NOT NULL);
CREATE INDEX controller_jira_issue ON controller_jira_operation(issue_id,status);
'''

def setUpModule():
	if SOURCE_GIT_TUPLE is None:
		raise unittest.SkipTest(COMMITTED_GIT_REASON)

def production_controller_cycle(*args,**kwargs):
	"""Existing controller cases use explicit synthetic passing publication facts."""
	from test_continuous_controller_planning import planning_resource_inputs
	for name,value in planning_resource_inputs(kwargs["host_capacity"]).items():
		kwargs.setdefault(name,value)
	kwargs.setdefault('controller_config', {'dispatch_enabled': True, 'auto_dispatch': True, 'auto_request_critic': True, 'auto_resume_amendments': True})
	kwargs.setdefault('publication_config',publication_config())
	kwargs.setdefault('observe_publication',lambda item:publication_observation(item['ticket'],kwargs['now']))
	kwargs.setdefault('observe_reservations',reservation_observation)
	return real_production_controller_cycle(*args,**kwargs)

def inventory_observation(now,tickets):
	return{'source':'host_observation','observed_at':now,'binding':dict(INVENTORY_BINDING),'complete':True,'inventory_sha256':fingerprint('controller-inventory',{'binding':INVENTORY_BINDING,'tickets':tickets}),'tickets':tickets}

def reservation_observation(request):
	changed=subprocess.check_output(['git','-C',str(ROOT),'diff','--name-only','-z','HEAD','--']).decode('utf-8').split('\x00')
	untracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files','--others','--exclude-standard','-z']).decode('utf-8').split('\x00')
	paths=sorted(set(path for path in [*changed,*untracked]if path))
	sources=[]
	if paths:
		diff={'schema_version':1,'kind':'worktree_diff','source':'git','repository_id':request['repository_id'],'head_sha':request['head_sha'],'tree_sha':request['tree_sha'],'artifact_id':fingerprint('controller-worktree-diff',{'head_sha':request['head_sha'],'tree_sha':request['tree_sha'],'paths':paths}),'observed_at':request['observed_at'],'authenticated':True,'complete':True,'paths':paths}
		diff['evidence_sha256']=fingerprint('controller-reservation-source',diff)
		sources.append(diff)
	envelope={'schema_version':1,'source':'host_observation','repository_id':request['repository_id'],'head_sha':request['head_sha'],'tree_sha':request['tree_sha'],'inventory_sha256':request['inventory_sha256'],'observed_at':request['observed_at'],'authenticated':True,'complete':True,'sources':sources}
	envelope['observation_sha256']=fingerprint('controller-reservation-observation',envelope)
	return envelope

def dispatch_receipt(payload,observed_at):
	keys=['dispatch_schema_version','dispatch_id','stream','ticket','exact_tuple','dispatch_nonce','prepared_at','begun_at','role','lifecycle_stage','lane_start_waivers']
	if 'reconcile_nonce' in payload:
		keys+=['reconcile_nonce','reconcile_at']
	return{key:payload[key]for key in keys}|{'status':'ACCEPTED','dispatch_state':'TERMINAL','terminal':True,'observed_at':observed_at,'session_id':'session-'+payload['dispatch_id']}

def jira_current(binding,status,observed_at):
	return{'issue_id':binding['issue_id'],'status_id':status,'observed_at':observed_at,'jira_provider':binding['jira_provider']}

def configured_jira_provider(config):
	jira=config['jira']
	return{'cloud_id':jira['cloud_id'],'site':jira['site'],'project_id':jira['provider_project_id'],'project_key':jira['project_key'],'controller_actor_id':jira['controller_actor_id']}

def observe_jira_provider(config,**overrides):
	value=configured_jira_provider(config)
	value.update(overrides)
	return value

def ticket(name,priority,disposition='ELIGIBLE',paths=None,**overrides):
	value={'ticket':name,'priority':priority,'disposition':disposition,'actor':'worker-'+name,'reason':'eligible scoped action' if disposition=='ELIGIBLE' else 'specific wait condition','next_action':'continue '+name,'resume_trigger':'named prerequisite becomes current','paths':paths or['.agentic/tests/fixtures/'+name.lower()+'.txt'],'dependencies_satisfied':True,'budget_available':True,'cap_available':True,'review_independent':True,'exact_tuple':'base:a/head:b/tree:c/contract:d/review:e','activity':'implement '+name,'verification_gate':'PENDING','reviewer_completion':dict(COUNTS),'open_findings':0,'jira_status':'In Progress','dispatch_role':'writer','lifecycle_stage':'implementation'}
	value.update(overrides)
	return value

def seal_replan(store, trigger_id, items, observed_at):
	context = store.replan_context()
	next_sha = fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': items})
	trigger = replan_trigger(trigger_id, context['prior_inventory_sha256'] or ('b' * 64), next_sha, [item['dispatch_id'] for item in context['nonterminal']], observed_at)
	store.seal_round_boundary_replan(trigger, observed_at)
	return next_sha

def replan_trigger(name,prior_sha,next_sha,dispatch_ids,observed_at):
	comment_id=int(fingerprint('test-replan-comment',name)[:12],16)
	artifact={'comment_id':comment_id,'node_id':'comment-node-'+name,'actor_id':7001,'actor_login':'owner','created_at':observed_at,'etag':'"'+name+'"','provider_observed_at':observed_at}
	artifact['artifact_sha256']=fingerprint('controller-replan-provider-artifact',artifact)
	trigger={'schema_version':2,'source':'github.com','trigger_id':fingerprint('controller-replan-trigger-id',{'repository_id':INVENTORY_BINDING['repository_id'],'comment_id':comment_id,'node_id':artifact['node_id']}),'repository_id':INVENTORY_BINDING['repository_id'],'prior_inventory_sha256':prior_sha,'next_inventory_sha256':next_sha,'round_id':'round-'+name,'observed_at':observed_at,'nonterminal_dispatch_ids':sorted(dispatch_ids),'provider_artifact':artifact,'owner_policy_sha256':fingerprint('controller-replan-owner-policy',{'trusted_owner_ids':[7001]})}
	trigger['trigger_sha256']=fingerprint('controller-replan-trigger',trigger)
	return trigger

class ContinuousControllerTests(unittest.TestCase):
	def _legacy_dispatch_store(self, path, rows, *, prior_inventory_sha256=None):
		db=sqlite3.connect(path)
		try:
			db.executescript(LEGACY_194_DDL)
			db.execute('PRAGMA application_id=1096238641')
			db.execute('PRAGMA user_version=1')
			for key,value in (('streams',json.dumps(['A','B','C'],separators=(',',':'))),('cadence_seconds','600'),('revision','0'),('last_regular_digest',''),('last_digest_revision','-1')):
				db.execute('INSERT INTO controller_meta VALUES(?,?)',(key,value))
			if prior_inventory_sha256 is not None:
				db.execute("INSERT INTO controller_meta VALUES('last_inventory_sha256',?)", (prior_inventory_sha256,))
			for stream in ('A','B','C'):
				db.execute('INSERT INTO stream_status VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(stream,'COMPLETE',None,'controller','No scoped work has been scheduled','Observe the scoped backlog','an eligible scoped ticket appears','UNOBSERVED','No active action','UNOBSERVED',json.dumps({'required':0,'completed':0,'acceptable':0,'failed':0,'stale':0,'outstanding':0},separators=(',',':')),0,'UNOBSERVED','[]',NOW))
			for status, payload in rows:
				db.execute('INSERT INTO controller_dispatch VALUES(?,?,?,?,?,?,NULL,?)', (payload['dispatch_id'], payload['stream'], payload['ticket'], payload['exact_tuple'], status, json.dumps(payload, sort_keys=True, separators=(',', ':')), NOW))
			db.commit()
		finally:
			db.close()

	def _database_before_image(self,path):
		db=sqlite3.connect(path)
		try:
			return {'application_id':db.execute('PRAGMA application_id').fetchone()[0],'user_version':db.execute('PRAGMA user_version').fetchone()[0],'dump':list(db.iterdump())}
		finally:
			db.close()

	def _legacy_payload(self, name, status, stream='A'):
		payload = {'dispatch_id': name, 'stream': stream, 'ticket': 'AWF-' + name, 'exact_tuple': 'tuple-' + name, 'paths': ['src/' + name + '.py'], 'actor': 'worker', 'next_action': 'continue', 'dispatch_nonce': name + '-nonce', 'prepared_at': NOW, 'role': 'writer', 'lifecycle_stage': 'implementation'}
		if status in {'IN_FLIGHT', 'UNKNOWN'}:
			payload['begun_at'] = NOW
		return payload

	def test_legacy_194_dispatch_rows_are_migrated_or_explicitly_refused(self):
		with tempfile.TemporaryDirectory(prefix='awf91-legacy-') as scratch:
			path = Path(scratch) / 'controller.sqlite3'
			rows = [(state, self._legacy_payload(state.lower(), state, chr(65 + index))) for index, state in enumerate(('PENDING', 'IN_FLIGHT', 'UNKNOWN'))]
			self._legacy_dispatch_store(path, rows, prior_inventory_sha256='b' * 64)
			reopened = ContinuousControllerStore(path, ['A', 'B', 'C'])
			with reopened.connection() as db:
				migrated = [(row['status'], json.loads(row['payload_json'])) for row in db.execute('SELECT status,payload_json FROM controller_dispatch ORDER BY rowid')]
			self.assertEqual([state for state, _ in migrated], ['PENDING', 'IN_FLIGHT', 'UNKNOWN'])
			self.assertTrue(all(payload['dispatch_schema_version'] == 2 for _, payload in migrated))
			self.assertEqual(reopened.replan_context()['prior_inventory_sha256'], 'b' * 64)

	def test_legacy_nonterminal_without_inventory_binding_is_atomically_refused(self):
		with tempfile.TemporaryDirectory(prefix='awf91-legacy-boundary-') as scratch:
			path = Path(scratch) / 'controller.sqlite3'
			rows = [(state, self._legacy_payload('boundary-' + state.lower(), state, chr(65 + index))) for index, state in enumerate(('PENDING', 'IN_FLIGHT', 'UNKNOWN'))]
			self._legacy_dispatch_store(path, rows)
			before = self._database_before_image(path)
			with self.assertRaisesRegex(ValidationError, '(?i)legacy.*prior inventory|prior inventory.*legacy'):
				ContinuousControllerStore(path, ['A', 'B', 'C'])
			self.assertEqual(self._database_before_image(path), before)

	def test_legacy_194_mixed_migration_is_atomic(self):
		with tempfile.TemporaryDirectory(prefix='awf91-legacy-atomic-') as scratch:
			path = Path(scratch) / 'controller.sqlite3'
			rows = [('PENDING', self._legacy_payload('valid-a', 'PENDING', 'A')), ('IN_FLIGHT', self._legacy_payload('valid-b', 'IN_FLIGHT', 'B')), ('UNKNOWN', self._legacy_payload('ambiguous', 'UNKNOWN', 'C'))]
			rows[-1][1]['unexpected_legacy_field'] = True
			self._legacy_dispatch_store(path, rows)
			before=self._database_before_image(path)
			before_bytes=path.read_bytes()
			with self.assertRaisesRegex(ValidationError, '(?i)legacy.*UNKNOWN|UNKNOWN.*legacy'):
				ContinuousControllerStore(path, ['A', 'B', 'C'])
			self.assertEqual(self._database_before_image(path),before)
			self.assertEqual(path.read_bytes(),before_bytes)

	def test_legacy_194_late_initialization_refusal_preserves_complete_before_image(self):
		with tempfile.TemporaryDirectory(prefix='awf91-legacy-late-refusal-') as scratch:
			path=Path(scratch)/'controller.sqlite3'
			self._legacy_dispatch_store(path,[('PENDING',self._legacy_payload('valid','PENDING','A'))], prior_inventory_sha256='b' * 64)
			before=self._database_before_image(path)
			with self.assertRaisesRegex(ValidationError,'Configured stream set changed'):
				ContinuousControllerStore(path,['A','B'])
			self.assertEqual(self._database_before_image(path),before)

	def test_reservation_sources_are_authenticated_and_git_canonical(self):
		with tempfile.TemporaryDirectory(prefix='awf91-reservations-') as scratch:
			repository = Path(scratch) / 'repo'; repository.mkdir()
			subprocess.run(['git', 'init', '-q', str(repository)], check=True)
			subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'fixture@example.invalid'], check=True)
			subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Fixture'], check=True)
			(repository / 'shared.py').write_text('base\n', encoding='utf-8')
			subprocess.run(['git', '-C', str(repository), 'add', 'shared.py'], check=True)
			subprocess.run(['git', '-C', str(repository), 'commit', '-qm', 'fixture'], check=True)
			head = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
			tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
			(repository / 'shared.py').write_text('dirty\n', encoding='utf-8')
			parked = {'schema_version': 1, 'kind': 'parked', 'source': 'github.com', 'repository_id': '101', 'head_sha': head, 'tree_sha': tree, 'artifact_id': 'parked-1', 'observed_at': NOW, 'authenticated': True, 'complete': True, 'paths': ['shared.py']}
			parked['evidence_sha256'] = fingerprint('controller-reservation-source', parked)
			diff = {'schema_version': 1, 'kind': 'worktree_diff', 'source': 'git', 'repository_id': '101', 'head_sha': head, 'tree_sha': tree, 'artifact_id': 'diff-1', 'observed_at': NOW, 'authenticated': True, 'complete': True, 'paths': ['shared.py']}
			diff['evidence_sha256'] = fingerprint('controller-reservation-source', diff)
			accepted = canonical_reservation_sources([parked, diff], repository, head, tree, '101', NOW)
			self.assertEqual([item['kind'] for item in accepted], ['parked', 'worktree_diff'])
			inventory_sha256 = 'd' * 64
			envelope = {'schema_version': 1, 'source': 'host_observation', 'repository_id': '101', 'head_sha': head, 'tree_sha': tree, 'inventory_sha256': inventory_sha256, 'observed_at': NOW, 'authenticated': True, 'complete': True, 'sources': [parked, diff]}
			envelope['observation_sha256'] = fingerprint('controller-reservation-observation', envelope)
			self.assertEqual(len(canonical_reservation_sources(envelope, repository, head, tree, '101', NOW, inventory_sha256=inventory_sha256)), 2)
			with self.assertRaisesRegex(ValidationError, 'different inventory'):
				canonical_reservation_sources(envelope, repository, head, tree, '101', NOW, inventory_sha256='e' * 64)
			for mutation in ({'authenticated': False}, {'repository_id': '999'}, {'head_sha': '0' * 40}, {'paths': ['../shared.py']}, {'paths': ['SHARED.py']}):
				bad = dict(parked); bad.update(mutation); bad['evidence_sha256'] = fingerprint('controller-reservation-source', {key: value for key, value in bad.items() if key != 'evidence_sha256'})
				with self.assertRaises(ValidationError):
					canonical_reservation_sources([bad], repository, head, tree, '101', NOW)

	def test_pending_replan_is_sealed_before_round_boundary_refill(self):
		with tempfile.TemporaryDirectory(prefix='awf91-replan-') as scratch:
			path = Path(scratch) / 'controller.sqlite3'
			store = ContinuousControllerStore(path, ['A'])
			store.schedule([ticket('AWF-OLD', 1)], NOW, 1)
			old = store.prepare_dispatches(NOW)[0]
			next_inventory = [ticket('AWF-NEXT', 1)]
			next_sha = fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': next_inventory})
			self.assertFalse(store.schedule(next_inventory, NOW, 1, inventory_sha256=next_sha)[0]['ticket'] == 'AWF-NEXT')
			trigger=replan_trigger('trigger-1','b'*64,next_sha,[old['dispatch_id']],NOW)
			store.seal_round_boundary_replan(trigger, NOW)
			reopened = ContinuousControllerStore(path, ['A'])
			self.assertEqual(reopened.schedule(next_inventory, NOW, 1, inventory_sha256=next_sha)[0]['ticket'], 'AWF-NEXT')
			self.assertTrue(any(item['payload']['ticket'] == 'AWF-NEXT' for item in reopened.prepare_dispatches(NOW)))

	def test_consumed_replan_seal_persists_successor_intent_before_restart(self):
		with tempfile.TemporaryDirectory(prefix='awf91-replan-crash-') as scratch:
			path=Path(scratch)/'controller.sqlite3'
			store=ContinuousControllerStore(path,['A'])
			store.schedule([ticket('AWF-OLD',1)],NOW,1)
			store.prepare_dispatches(NOW)
			next_inventory=[ticket('AWF-NEXT',1)]
			next_sha=seal_replan(store,'crash-after-schedule',next_inventory,'2026-10-02T10:00:01Z')
			store.schedule(next_inventory,'2026-10-02T10:00:01Z',1,inventory_sha256=next_sha)
			# Simulate process loss before production_controller_cycle reaches prepare_dispatches.
			reopened=ContinuousControllerStore(path,['A'])
			pending=reopened.pending_dispatches()
			self.assertTrue(any(item['payload']['ticket']=='AWF-NEXT' for item in pending),pending)
			self.assertTrue(reopened.round_boundary_ready(next_inventory,next_sha))

	def test_all_nonterminal_states_block_refill_until_authenticated_replan(self):
		for status in ('PENDING', 'IN_FLIGHT', 'UNKNOWN', 'LAUNCHED'):
			with self.subTest(status=status), tempfile.TemporaryDirectory(prefix='awf91-replan-state-') as scratch:
				path = Path(scratch) / 'controller.sqlite3'
				store = ContinuousControllerStore(path, ['A'])
				store.schedule([ticket('AWF-OLD', 1)], NOW, 1); operation = store.prepare_dispatches(NOW)[0]
				with store.transaction() as db:
					db.execute('UPDATE controller_dispatch SET status=? WHERE dispatch_id=?', (status, operation['dispatch_id']))
				next_inventory = [ticket('AWF-NEXT', 1)]; next_sha = fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': next_inventory})
				invalid=replan_trigger('trigger-'+status+'-invalid','b'*64,next_sha,[operation['dispatch_id']],NOW)
				invalid['provider_artifact']['artifact_sha256']='0'*64
				invalid['trigger_sha256']=fingerprint('controller-replan-trigger',{key:value for key,value in invalid.items() if key!='trigger_sha256'})
				with self.assertRaises(ValidationError): store.seal_round_boundary_replan(invalid,NOW)
				valid=replan_trigger('trigger-'+status,'b'*64,next_sha,[operation['dispatch_id']],NOW)
				store.seal_round_boundary_replan(valid,NOW)
				self.assertEqual(store.schedule(next_inventory, NOW, 1, inventory_sha256=next_sha)[0]['ticket'], 'AWF-NEXT')
				with self.assertRaises(ValidationError):
					store.seal_round_boundary_replan(valid,NOW)
				with store.connection() as db:
					self.assertEqual(db.execute("SELECT COUNT(*) FROM controller_replan_seal WHERE status='SEALED'").fetchone()[0],0)

	def test_retained_old_ticket_and_new_successor_require_replan_in_all_nonterminal_states(self):
		for status in ('PENDING', 'IN_FLIGHT', 'UNKNOWN', 'LAUNCHED'):
			with self.subTest(status=status), tempfile.TemporaryDirectory(prefix='awf91-replan-retained-') as scratch:
				path = Path(scratch) / 'controller.sqlite3'
				store = ContinuousControllerStore(path, ['A', 'B'])
				old_ticket = ticket('AWF-OLD', 1)
				prior_sha = fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': [old_ticket]})
				store.schedule([old_ticket], NOW, 2, inventory_sha256=prior_sha)
				operation = store.prepare_dispatches(NOW)[0]
				if status != 'PENDING':
					payload = store.begin_dispatch(operation['dispatch_id'], NOW)
					if status == 'UNKNOWN':
						store.mark_dispatch_unknown(operation['dispatch_id'], NOW)
					elif status == 'LAUNCHED':
						launched = dispatch_receipt(payload, NOW) | {'dispatch_state': 'LAUNCHED', 'terminal': False}
						store.finish_dispatch(operation['dispatch_id'], launched, NOW)
				retained_inventory = [old_ticket, ticket('AWF-NEW', 2)]
				next_sha = fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': retained_inventory})
				store.schedule(retained_inventory, NOW, 2, inventory_sha256=next_sha)
				before_seal = store.prepare_dispatches(NOW)
				self.assertFalse(any(item['payload']['ticket'] == 'AWF-NEW' for item in before_seal), before_seal)
				valid = replan_trigger('retained-' + status, prior_sha, next_sha, [operation['dispatch_id']], NOW)
				store.seal_round_boundary_replan(valid, NOW)
				store.schedule(retained_inventory, NOW, 2, inventory_sha256=next_sha)
				after_seal = store.prepare_dispatches(NOW)
				self.assertTrue(any(item['payload']['ticket'] == 'AWF-NEW' for item in after_seal), after_seal)

	def test_terminal_quiescence_updates_versioned_receipt_before_reopen(self):
		with tempfile.TemporaryDirectory(prefix='awf91-terminal-reopen-') as scratch:
			path = Path(scratch) / 'controller.sqlite3'
			store = ContinuousControllerStore(path, ['A'])
			store.schedule([ticket('AWF-LAUNCHED', 1)], NOW, 1, inventory_sha256='a' * 64)
			operation = store.prepare_dispatches(NOW)[0]
			payload = store.begin_dispatch(operation['dispatch_id'], NOW)
			launched = dispatch_receipt(payload, NOW) | {'dispatch_state': 'LAUNCHED', 'terminal': False}
			store.finish_dispatch(operation['dispatch_id'], launched, NOW)
			observation = {'dispatch_id': operation['dispatch_id'], 'ticket': payload['ticket'], 'exact_tuple': payload['exact_tuple'], 'session_id': launched['session_id'], 'status': 'QUIESCENT', 'terminal': True, 'observed_at': NOW}
			store.confirm_dispatch_quiescence(operation['dispatch_id'], observation, NOW)
			reopened = ContinuousControllerStore(path, ['A'])
			with reopened.connection() as db:
				row = db.execute('SELECT status,receipt_json FROM controller_dispatch WHERE dispatch_id=?', (operation['dispatch_id'],)).fetchone()
			self.assertEqual(row['status'], 'TERMINAL')
			receipt = json.loads(row['receipt_json'])
			self.assertEqual(receipt['dispatch_state'], 'TERMINAL')
			self.assertIs(receipt['terminal'], True)
			self.assertEqual(receipt['observed_at'], observation['observed_at'])

	def test_publication_failure_blocks_before_dispatch_while_other_streams_continue(self):
		calls=[]

		def observe(item):
			result=publication_observation(item['ticket'],NOW)
			if item['ticket']=='EX-1':
				result['push_permitted']=False
			return result
		result=production_controller_cycle(self.store,**planning_fixture_inputs([ticket('EX-1',1),ticket('EX-2',2)], self.store, 3, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=3,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,[ticket('EX-1',1),ticket('EX-2',2)]),observe_publication=observe,dispatch_ticket=lambda payload:calls.append(payload['ticket'])or dispatch_receipt(payload,NOW),observe_dispatch=lambda payload:self.fail('no prior launch'),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW})
		self.assertEqual(calls,['EX-2'])
		blocked=next((row for row in result['streams']if row['ticket']=='EX-1'))
		self.assertEqual(blocked['state'],'BLOCKED')
		self.assertIn('PUSH_PERMISSION',blocked['reason'])
		self.assertEqual(result['errors'][0]['state'],'BLOCKED')

	def test_missing_publication_observer_refuses_worker_launch(self):
		result=production_controller_cycle(self.store,**planning_fixture_inputs([ticket('EX-1',1)], self.store, 3, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=3,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,[ticket('EX-1',1)]),observe_publication=None,dispatch_ticket=lambda payload:self.fail('missing publication facts dispatched work'),observe_dispatch=lambda payload:self.fail('no prior launch'),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW})
		self.assertEqual(result['dispatch_receipts'],[])
		self.assertIn('PUBLICATION_UNOBSERVED',result['errors'][0]['reason'])

	def test_git_child_environment_uses_provider_key_scrubbing_helper(self):
		completed=subprocess.CompletedProcess(args=['git'],returncode=0,stdout=b'ok')
		with patch.dict(os.environ,{'OPENAI_API_KEY':'must-not-reach-child'}),patch.object(continuous_controller.subprocess,'run',return_value=completed)as run:
			self.assertEqual(continuous_controller._git(ROOT,'status'),b'ok')
		child_environment=run.call_args.kwargs['env']
		self.assertNotIn('OPENAI_API_KEY',child_environment)
		self.assertEqual(child_environment['GIT_TERMINAL_PROMPT'],'0')

	def test_schema_inventory_matches_loaded_generated_catalog(self):
		schema_dir=ROOT/'.agentic/schemas'
		contracts=Contracts(schema_dir)
		inventory=schema_inventory(schema_dir)
		self.assertEqual(inventory,set(contracts.schemas))
		self.assertIn('operating-config',inventory)

	def setUp(self):
		self.temporary=tempfile.TemporaryDirectory(prefix='awf-controller-')
		self.addCleanup(self.temporary.cleanup)
		self.path=Path(self.temporary.name)/'controller.sqlite3'
		self.store=ContinuousControllerStore(self.path,['A','B','C'])

	def test_blocked_or_input_dependent_stream_does_not_pause_eligible_work(self):
		inventory=[ticket('EX-1',1,'BLOCKED',reason='dependency unavailable'),ticket('EX-2',2,'PAUSED_INPUT',reason='owner input required'),ticket('EX-3',3)]
		snapshot=self.store.schedule(inventory,NOW,host_capacity=3)
		self.assertEqual({item['state']for item in snapshot},{'WORKING','BLOCKED','PAUSED_INPUT'})
		working=next((item for item in snapshot if item['state']=='WORKING'))
		self.assertEqual(working['ticket'],'EX-3')
		self.assertTrue(all((item['reason']and item['next_action']and item['resume_trigger']for item in snapshot)))

	def test_completed_run_refills_same_transaction_with_next_eligible_action(self):
		first=self.store.schedule([ticket('EX-1',1),ticket('EX-2',2)],NOW,host_capacity=1)
		stream=next((item for item in first if item['state']=='WORKING'))
		self.assertEqual(stream['ticket'],'EX-1')
		after=self.store.finish_and_refill(stream['stream'],'EX-1',[ticket('EX-2',2)],'2026-10-02T10:00:01Z',host_capacity=1)
		refilled=next((item for item in after if item['stream']==stream['stream']))
		self.assertEqual((refilled['state'],refilled['ticket']),('WORKING','EX-2'))
		self.assertNotIn(None,[item['state']for item in after])

	def test_path_capacity_budget_cap_dependency_and_independence_are_fail_closed(self):
		inventory=[ticket('EX-1',1,paths=['src/shared']),ticket('EX-2',2,paths=['SRC/shared/nested.py']),ticket('EX-3',3,budget_available=False),ticket('EX-4',4,cap_available=False),ticket('EX-5',5,dependencies_satisfied=False),ticket('EX-6',6,review_independent=False)]
		snapshot=self.store.schedule(inventory,NOW,host_capacity=2)
		self.assertEqual(sum((item['state']=='WORKING' for item in snapshot)),1)
		self.assertTrue(all((item['state']in{'WORKING','BLOCKED','PAUSED_INPUT','COMPLETE'}for item in snapshot)))

	def test_unresolved_dispatch_ticket_is_reserved_across_streams_after_priority_change(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'global-reservation.sqlite3',['A','B'])
		original=ticket('EX-RESERVED',1)
		store.schedule([original],NOW,host_capacity=1)
		old_intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(old_intent['dispatch_id'],NOW)
		store.mark_dispatch_unknown(old_intent['dispatch_id'],'2026-10-02T10:00:01Z')
		later='2026-10-02T10:00:02Z'
		replacement=ticket('EX-REPLACEMENT',2)
		replanned=[ticket('EX-RESERVED',1,'COMPLETE'),replacement]
		next_sha=seal_replan(store,'reservation-priority',replanned,later)
		store.schedule(replanned,later,host_capacity=2,inventory_sha256=next_sha)
		replacement_intent=next((item for item in store.prepare_dispatches(later)if item['ticket']=='EX-REPLACEMENT'))
		begun=store.begin_dispatch(replacement_intent['dispatch_id'],'2026-10-02T10:00:03Z')
		store.finish_dispatch(replacement_intent['dispatch_id'],dispatch_receipt(begun,'2026-10-02T10:00:04Z'),'2026-10-02T10:00:04Z')
		reprioritized=[ticket('EX-RESERVED',0),ticket('EX-REPLACEMENT',1)]
		snapshot=store.schedule(reprioritized,'2026-10-02T10:00:05Z',host_capacity=2)
		self.assertEqual(next((row for row in snapshot if row['stream']=='A'))['ticket'],'EX-REPLACEMENT')
		self.assertNotEqual(next((row for row in snapshot if row['stream']=='B'))['ticket'],'EX-RESERVED')
		pending=store.prepare_dispatches('2026-10-02T10:00:05Z')
		self.assertEqual([item['ticket']for item in pending],['EX-RESERVED'])
		with store.connection()as db:
			self.assertEqual(db.execute("SELECT COUNT(*) FROM controller_dispatch WHERE ticket='EX-RESERVED'").fetchone()[0],1)
		recon_at='2026-10-02T10:00:06Z'
		recon=store.begin_dispatch_reconciliation(old_intent['dispatch_id'],recon_at)
		store.finish_dispatch(old_intent['dispatch_id'],dispatch_receipt(recon,'2026-10-02T10:00:07Z'),'2026-10-02T10:00:07Z',reconcile=True)
		next_work=ticket('EX-RESERVED',0,exact_tuple='base:a/head:new/tree:new/contract:d/review:e')
		snapshot=store.schedule([next_work,replacement],'2026-10-02T10:00:08Z',host_capacity=2)
		self.assertEqual(next((row for row in snapshot if row['stream']=='B'))['ticket'],'EX-RESERVED')
		created=store.prepare_dispatches('2026-10-02T10:00:08Z')
		self.assertTrue(any((item['ticket']=='EX-RESERVED' and item['stream_id']=='B' for item in created)),repr(created))

	def test_awf5_c195_r1_001_matching_unknown_dispatch_stays_blocked_until_reconciled(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'persistent-unknown.sqlite3',['A'])
		reserved=ticket('EX-UNKNOWN',1)
		store.schedule([reserved],NOW,host_capacity=1)
		intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(intent['dispatch_id'],NOW)
		store.mark_dispatch_unknown(intent['dispatch_id'],'2026-10-02T10:00:01Z')
		for observed_at in('2026-10-02T10:00:02Z','2026-10-02T10:00:03Z'):
			snapshot=store.schedule([reserved],observed_at,host_capacity=1)
			stream=snapshot[0]
			self.assertEqual((stream['state'],stream['ticket']),('BLOCKED','EX-UNKNOWN'))
			self.assertIn(intent['dispatch_id'],stream['reason'])
			self.assertTrue(stream['exact_tuple'])
			self.assertIn('Observe the exact durable dispatch intent',stream['next_action'])
			self.assertIn('fresh observation',stream['resume_trigger'])
		digest=store.digest('2026-10-02T10:00:04Z')
		self.assertFalse(digest['all_complete'])
		self.assertEqual(digest['streams'][0]['state'],'BLOCKED')
		unresolved=next((item for item in digest['unresolved_dispatches']if item['dispatch_id']==intent['dispatch_id']))
		self.assertEqual((unresolved['ticket'],unresolved['status'],unresolved['detached']),('EX-UNKNOWN','UNKNOWN',False))

	def test_pending_and_in_flight_dispatch_tickets_are_also_globally_reserved(self):
		for status in('PENDING','IN_FLIGHT'):
			with self.subTest(status=status):
				path=Path(self.temporary.name)/f'reservation-{status.lower()}.sqlite3'
				store=ContinuousControllerStore(path,['A','B'])
				reserved=ticket('EX-HELD',1)
				replacement=ticket('EX-NEXT',2)
				store.schedule([reserved],NOW,host_capacity=1)
				intent=store.prepare_dispatches(NOW)[0]
				if status=='IN_FLIGHT':
					store.begin_dispatch(intent['dispatch_id'],NOW)
				later='2026-10-02T10:00:01Z'
				replanned=[ticket('EX-HELD',1,'COMPLETE'),replacement]
				next_sha=seal_replan(store,'held-'+status.lower(),replanned,later)
				store.schedule(replanned,later,host_capacity=2 if status=='IN_FLIGHT' else 1,inventory_sha256=next_sha)
				snapshot=store.schedule([ticket('EX-HELD',0),replacement],'2026-10-02T10:00:02Z',host_capacity=2)
				self.assertEqual(next((row for row in snapshot if row['stream']=='A'))['ticket'],'EX-NEXT')
				self.assertNotEqual(next((row for row in snapshot if row['stream']=='B'))['ticket'],'EX-HELD')
				with store.connection()as db:
					rows=db.execute("SELECT stream_id,status FROM controller_dispatch WHERE ticket='EX-HELD'").fetchall()
				self.assertEqual([(row['stream_id'],row['status'])for row in rows],[('A',status)])

	def test_unresolved_dispatches_consume_capacity_but_other_available_slots_continue(self):
		for intent_state in('PENDING','IN_FLIGHT','UNKNOWN'):
			for capacity in(1,2):
				with self.subTest(intent_state=intent_state,capacity=capacity):
					path=Path(self.temporary.name)/f'capacity-{intent_state.lower()}-{capacity}.sqlite3'
					store=ContinuousControllerStore(path,['A','B'])
					store.schedule([ticket('EX-IN-FLIGHT',1)],NOW,host_capacity=1)
					intent=store.prepare_dispatches(NOW)[0]
					if intent_state!='PENDING':
						store.begin_dispatch(intent['dispatch_id'],NOW)
					if intent_state=='UNKNOWN':
						store.mark_dispatch_unknown(intent['dispatch_id'],'2026-10-02T10:00:01Z')
					later='2026-10-02T10:00:02Z'
					replanned=[ticket('EX-IN-FLIGHT',1,'COMPLETE'),ticket('EX-AVAILABLE-1',2),ticket('EX-AVAILABLE-2',3)]
					next_sha=seal_replan(store,'capacity-'+intent_state.lower()+'-'+str(capacity),replanned,later)
					snapshot=store.schedule(replanned,later,host_capacity=capacity,inventory_sha256=next_sha)
					reserved=0 if intent_state=='PENDING' else 1
					self.assertEqual(sum((row['state']=='WORKING' for row in snapshot)),max(0,capacity-reserved))
					self.assertNotIn('EX-IN-FLIGHT',[row['ticket']for row in snapshot])

	def test_orphan_pending_dispatch_blocks_successor_until_replan(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'orphan-pending.sqlite3',['A','B'])
		store.schedule([ticket('EX-8',1)],NOW,host_capacity=1)
		original=store.prepare_dispatches(NOW)[0]
		calls=[]
		blocked_at='2026-10-02T10:00:01Z'
		blocked_cycle=production_controller_cycle(store,**planning_fixture_inputs([ticket('EX-8',1,'BLOCKED',reason='fresh preflight paused this ticket'),ticket('EX-9',2)], store, 1, binding=INVENTORY_BINDING, now=blocked_at),now=blocked_at,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(blocked_at,[ticket('EX-8',1,'BLOCKED',reason='fresh preflight paused this ticket'),ticket('EX-9',2)]),dispatch_ticket=lambda payload:calls.append(payload['ticket'])or dispatch_receipt(payload,blocked_at),observe_dispatch=lambda payload:self.fail('PENDING cancellation must not reconcile'),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':blocked_at})
		self.assertNotIn('EX-8',calls,repr(blocked_cycle))
		with store.connection()as db:
			cancelled=db.execute('SELECT status,payload_json FROM controller_dispatch WHERE dispatch_id=?',(original['dispatch_id'],)).fetchone()
		self.assertEqual(cancelled['status'],'PENDING')
		self.assertNotIn('cancellation_reason',json.loads(cancelled['payload_json']))
		self.assertNotIn('EX-9',[row['ticket']for row in blocked_cycle['streams']if row['state']=='WORKING'])

	def test_pending_dispatch_cancellation_refuses_any_intent_that_reached_begin(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'cancel-inflight.sqlite3',['A'])
		store.schedule([ticket('EX-CANCEL',1)],NOW,host_capacity=1)
		intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(intent['dispatch_id'],NOW)
		with self.assertRaisesRegex(ValidationError,'never-begun pending'):
			store.cancel_pending_dispatch(intent['dispatch_id'],'2026-10-02T10:00:01Z','preflight changed')

	def test_pending_dispatch_preflight_change_waits_for_replan(self):
		for disposition in('PAUSED_INPUT','COMPLETE'):
			with self.subTest(disposition=disposition):
				path=Path(self.temporary.name)/f'cancel-{disposition.lower()}.sqlite3'
				store=ContinuousControllerStore(path,['A'])
				ticket_id='EX-10' if disposition=='PAUSED_INPUT' else 'EX-11'
				store.schedule([ticket(ticket_id,1)],NOW,host_capacity=1)
				intent=store.prepare_dispatches(NOW)[0]
				observed_at='2026-10-02T10:00:01Z'
				result=production_controller_cycle(store,**planning_fixture_inputs([ticket(ticket_id,1,disposition)], store, 1, binding=INVENTORY_BINDING, now=observed_at),now=observed_at,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(observed_at,[ticket(ticket_id,1,disposition)]),dispatch_ticket=lambda payload:self.fail('non-eligible PENDING intent reached host'),observe_dispatch=lambda payload:self.fail('never-begun intent cannot reconcile'),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':observed_at})
				self.assertEqual(next((row for row in result['streams']if row['stream']=='A'))['state'],'WORKING')
				with store.connection()as db:
					status=db.execute('SELECT status FROM controller_dispatch WHERE dispatch_id=?',(intent['dispatch_id'],)).fetchone()[0]
				self.assertEqual(status,'PENDING')

	def test_contradictory_reviewer_counts_fail_before_dispatch_or_delivery(self):
		malformed=ticket('QA-BAD',1,reviewer_completion={'required':2,'completed':1,'acceptable':1,'failed':0,'stale':0,'outstanding':0})
		calls=[]
		with self.assertRaisesRegex(ValidationError,'counts are contradictory'):
			production_controller_cycle(self.store,**planning_fixture_inputs([malformed], self.store, 1, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,[malformed]),dispatch_ticket=lambda payload:calls.append('dispatch'),observe_dispatch=lambda payload:calls.append('observe'),deliver_status=lambda payload:calls.append('deliver'))
		self.assertEqual(calls,[])

	def test_restart_preserves_visible_stream_states_and_has_no_idle_value(self):
		before=self.store.schedule([ticket('EX-1',1),ticket('EX-2',2,'PAUSED_INPUT')],NOW,host_capacity=1)
		restarted=ContinuousControllerStore(self.path,['A','B','C'])
		self.assertEqual(restarted.snapshot(),before)
		self.assertTrue(all((item['state']in{'WORKING','PAUSED_INPUT','BLOCKED','COMPLETE'}for item in restarted.snapshot())))

	def test_default_and_configured_cadence_with_immediate_change_digest(self):
		first=self.store.digest(NOW)
		self.assertEqual((first['kind'],first['cadence_seconds']),('REGULAR',600))
		self.assertEqual(self.store.digest('2026-10-02T10:00:30Z'),first)
		self.store.acknowledge_digest(first['delivery_id'],'2026-10-02T10:00:31Z')
		self.assertIsNone(self.store.digest('2026-10-02T10:01:00Z'))
		self.store.schedule([ticket('EX-1',1)],'2026-10-02T10:02:00Z',host_capacity=1)
		changed=self.store.digest('2026-10-02T10:02:01Z')
		self.assertEqual(changed['kind'],'CHANGE')
		self.assertEqual(len(changed['streams']),3)
		self.assertTrue(all((item['exact_tuple']for item in changed['streams'])))
		self.store.acknowledge_digest(changed['delivery_id'],'2026-10-02T10:02:02Z')
		regular=self.store.digest('2026-10-02T10:10:00Z')
		self.assertEqual(regular['kind'],'REGULAR')
		self.store.acknowledge_digest(regular['delivery_id'],'2026-10-02T10:10:01Z')
		self.store.set_cadence(300)
		self.assertEqual(self.store.digest('2026-10-02T10:20:00Z')['cadence_seconds'],300)

	def test_disabled_periodic_status_suppresses_regular_and_keeps_urgent_stream_changes(self):
		path=Path(self.temporary.name)/'disabled.sqlite3'
		store=ContinuousControllerStore(path,['A','B','C'],periodic_status_enabled=False)
		self.assertIsNone(store.digest(NOW))
		self.assertIsNone(store.digest('2026-10-02T10:20:00Z'))
		store.schedule([ticket('EX-BLOCKED',1,'BLOCKED',reason='dependency blocker evidence'),ticket('EX-INPUT',2,'PAUSED_INPUT',reason='owner input required'),ticket('EX-READY',3,verification_gate='MERGE_READY')],'2026-10-02T10:20:01Z',host_capacity=3)
		digest=store.digest('2026-10-02T10:20:02Z')
		self.assertEqual(digest['kind'],'CHANGE')
		rows={row['ticket']:row for row in digest['streams']if row['ticket']}
		self.assertEqual(rows['EX-BLOCKED']['state'],'BLOCKED')
		self.assertIn('blocker evidence',rows['EX-BLOCKED']['reason'])
		self.assertEqual(rows['EX-INPUT']['state'],'PAUSED_INPUT')
		self.assertIn('input required',rows['EX-INPUT']['reason'])
		self.assertEqual(rows['EX-READY']['verification_gate'],'MERGE_READY')
		store.acknowledge_digest(digest['delivery_id'],'2026-10-02T10:20:03Z')
		self.assertIsNone(store.digest('2026-10-02T10:40:00Z'))

	def test_pending_digest_replays_after_restart_while_periodic_status_is_disabled(self):
		first=self.store.digest(NOW)
		restarted=ContinuousControllerStore(self.path,['A','B','C'],periodic_status_enabled=False)
		self.assertEqual(restarted.digest('2026-10-02T10:20:00Z'),first)

	def test_crashed_dispatch_recovery_queues_immediate_failure_digest_while_disabled(self):
		path=Path(self.temporary.name)/'crashed-dispatch.sqlite3'
		store=ContinuousControllerStore(path,['A'],periodic_status_enabled=False)
		store.schedule([ticket('EX-CRASH',1)],NOW,host_capacity=1)
		operation=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(operation['dispatch_id'],NOW)
		restarted=ContinuousControllerStore(path,['A'],periodic_status_enabled=False)
		restarted.recover_dispatches('2026-10-02T10:00:01Z')
		digest=restarted.digest('2026-10-02T10:00:02Z')
		self.assertEqual(digest['kind'],'CHANGE')
		stream=digest['streams'][0]
		self.assertEqual(stream['state'],'BLOCKED')
		self.assertIn('Dispatch outcome unknown',stream['reason'])
		with restarted.connection()as db:
			status=db.execute('SELECT status FROM controller_dispatch WHERE dispatch_id=?',(operation['dispatch_id'],)).fetchone()[0]
			pending=db.execute('SELECT acknowledged_at FROM controller_outbox WHERE delivery_id=?',(digest['delivery_id'],)).fetchone()[0]
		self.assertEqual(status,'UNKNOWN')
		self.assertIsNone(pending)

	def test_legacy_cadence_requires_and_supports_explicit_migration(self):
		path=Path(self.temporary.name)/'legacy-cadence.sqlite3'
		ContinuousControllerStore(path,['A'],cadence_seconds=300)
		with self.assertRaisesRegex(ValidationError,'explicitly migrate'):
			ContinuousControllerStore(path,['A'])
		migrated=ContinuousControllerStore(path,['A'],migrate_cadence=True)
		self.assertEqual(migrated.digest(NOW)['cadence_seconds'],600)

	def test_generated_digest_contract_accepts_runtime_record(self):
		self.store.schedule([ticket('EX-1',1)],NOW,host_capacity=1)
		digest=self.store.digest(NOW)
		Contracts(ROOT/'.agentic/schemas').validate('controller-status-digest',digest)
		local_semantics('controller-status-digest',digest)
		digest['streams'][0]['reviewer_completion']['outstanding']=99
		with self.assertRaisesRegex(ValidationError,'counts'):
			local_semantics('controller-status-digest',digest)

	def test_running_work_is_revalidated_and_completed_ticket_is_not_redispatched(self):
		self.store.schedule([ticket('EX-1',1)],NOW,host_capacity=1)
		blocked=self.store.schedule([ticket('EX-1',1,dependencies_satisfied=False)],'2026-10-02T10:00:01Z',host_capacity=1)
		self.assertEqual(next((item for item in blocked if item['ticket']=='EX-1'))['state'],'BLOCKED')
		fresh=ContinuousControllerStore(Path(self.temporary.name)/'finished.sqlite3',['A'])
		fresh.schedule([ticket('EX-2',1)],NOW,host_capacity=1)
		with self.assertRaisesRegex(ValidationError,'Completed ticket remains dispatch-eligible'):
			fresh.finish_and_refill('A','EX-2',[ticket('EX-2',1)],'2026-10-02T10:00:01Z',host_capacity=1)

	def test_path_aliases_overlap_and_external_or_traversal_paths_are_rejected(self):
		snapshot=self.store.schedule([ticket('EX-1',1,paths=['src\\shared']),ticket('EX-2',2,paths=['SRC/shared/nested.py'])],NOW,host_capacity=2)
		self.assertEqual(sum((item['state']=='WORKING' for item in snapshot)),1)
		drive_absolute=''.join(('C',':/outside'))
		for path in('../outside','src/../outside',drive_absolute,'//server/share'):
			with self.subTest(path=path),self.assertRaisesRegex(ValidationError,'path'):
				self.store.schedule([ticket('BAD',1,paths=[path])],NOW,host_capacity=1)

	def test_git_inventory_serializes_ntfs_hardlink_directory_claims_and_rejects_junctions(self):
		if os.name!='nt':
			self.skipTest('NTFS alias regression is Windows-specific')
		repository=Path(self.temporary.name)/'alias-repository'
		repository.mkdir()
		subprocess.run(['git','init','-q',str(repository)],check=True)
		subprocess.run(['git','-C',str(repository),'config','user.email','fixture@example.invalid'],check=True)
		subprocess.run(['git','-C',str(repository),'config','user.name','Fixture'],check=True)
		left=repository/'left'
		right=repository/'right'
		left.mkdir()
		right.mkdir()
		source=left/'file.py'
		source.write_text('value = 1\n',encoding='utf-8',newline='\n')
		hardlink=right/'hard.py'
		os.link(source,hardlink)
		subprocess.run(['git','-C',str(repository),'add','left/file.py','right/hard.py'],check=True)
		subprocess.run(['git','-C',str(repository),'commit','-qm','synthetic alias inventory'],check=True)
		head=subprocess.check_output(['git','-C',str(repository),'rev-parse','HEAD'],text=True).strip()
		tree=subprocess.check_output(['git','-C',str(repository),'rev-parse','HEAD^{tree}'],text=True).strip()
		admitted,_binding=canonical_repository_paths([ticket('LEFT',1,paths=['left/**']),ticket('RIGHT',2,paths=['right/*'])],repository,head,tree)
		self.assertIn('left/file.py',admitted[0]['paths'])
		self.assertIn('left/file.py',admitted[1]['paths'])
		snapshot=self.store.schedule(admitted,NOW,host_capacity=2)
		self.assertEqual(sum((item['state']=='WORKING' for item in snapshot)),1)
		self.assertEqual(sum((item['state']=='BLOCKED' for item in snapshot)),1)
		hardlink.unlink()
		subprocess.run(['git','-C',str(repository),'checkout','--','right/hard.py'],check=True)
		alias=repository/'alias'
		made=subprocess.run(['cmd','/c','mklink','/J',str(alias),str(left)],capture_output=True,text=True)
		if made.returncode:
			self.skipTest('NTFS junction creation unavailable on this host')
		with self.assertRaisesRegex(ValidationError,'junction|reparse'):
			canonical_repository_paths([ticket('JUNCTION',1,paths=['alias/file.py'])],repository,head,tree)

	def test_git_inventory_collapses_case_aliases_and_rejects_wrong_tuple(self):
		exact='.agentic/lib/agentic/continuous_controller.py'
		admitted,binding=canonical_repository_paths([ticket('LOWER',1,paths=[exact]),ticket('UPPER',2,paths=[exact.upper()])],ROOT,REPOSITORY['repository_head_sha'],REPOSITORY['repository_tree_sha'])
		self.assertEqual(admitted[0]['paths'],admitted[1]['paths'])
		self.assertEqual(binding['head_sha'],REPOSITORY['repository_head_sha'])
		snapshot=self.store.schedule(admitted,NOW,host_capacity=2)
		self.assertEqual(sum((item['state']=='WORKING' for item in snapshot)),1)
		with self.assertRaisesRegex(ValidationError,'pinned head/tree'):
			canonical_repository_paths([ticket('WRONG',1,paths=[exact])],ROOT,'0'*40,REPOSITORY['repository_tree_sha'])

	def test_digest_outbox_survives_restart_and_clock_rollback_fails_closed(self):
		first=self.store.digest(NOW)
		restarted=ContinuousControllerStore(self.path,['A','B','C'])
		self.assertEqual(restarted.digest('2026-10-02T10:05:00Z'),first)
		restarted.acknowledge_digest(first['delivery_id'],'2026-10-02T10:05:01Z')
		with self.assertRaisesRegex(ValidationError,'clock moved backwards'):
			restarted.digest('2026-10-02T09:59:59Z')

	def test_controller_state_is_rejected_inside_declared_worktree(self):
		worktree=Path(self.temporary.name)/'worktree'
		worktree.mkdir()
		with self.assertRaisesRegex(ValidationError,'outside'):
			ContinuousControllerStore(worktree/'state.sqlite3',['A'],worktree_roots=[worktree])

	def test_controller_state_rejects_wrong_application_or_schema_identity(self):
		for name,application_id,version in(('wrong-app.sqlite3',7,1),('wrong-version.sqlite3',1096238641,99)):
			path=Path(self.temporary.name)/name
			db=sqlite3.connect(path)
			try:
				db.execute(f'PRAGMA application_id={application_id}')
				db.execute(f'PRAGMA user_version={version}')
				db.commit()
			finally:
				db.close()
			with self.subTest(name=name),self.assertRaisesRegex(ValidationError,'identity|version'):
				ContinuousControllerStore(path,['A'])

	def test_production_workflow_cli_exposes_configured_controller(self):
		worktree=Path(self.temporary.name)/'worker'
		worktree.mkdir()
		command=[sys.executable,'-B',str(ROOT/'.agentic/scripts/workflow.py'),'--root',str(ROOT),'controller','--state',str(self.path),'--stream','A','--stream','B','--stream','C','--worktree-root',str(worktree),'--disable-periodic-status','snapshot']
		completed=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=30)
		self.assertEqual(completed.returncode,0,completed.stderr)
		self.assertEqual(len(json.loads(completed.stdout)['streams']),3)

	def test_production_cycle_observes_inventory_dispatches_and_delivers_digest(self):
		inventory=[ticket('EX-1',1),ticket('EX-2',2),ticket('EX-3',3)]
		calls=[]

		def dispatch(payload):
			calls.append(('dispatch',payload['ticket']))
			return dispatch_receipt(payload,NOW)

		def deliver(digest):
			calls.append(('deliver',digest['delivery_id']))
			return{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW}
		result=production_controller_cycle(self.store,**planning_fixture_inputs(inventory, self.store, 3, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=3,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,inventory),dispatch_ticket=dispatch,observe_dispatch=lambda payload:self.fail('no reconciliation'),deliver_status=deliver)
		self.assertEqual(len(result['dispatch_receipts']),3)
		self.assertEqual(result['errors'],[])
		self.assertTrue(all((row['state']=='WORKING' for row in result['streams'])))
		self.assertEqual(sum((kind=='dispatch' for kind,_ in calls)),3)
		self.assertEqual(sum((kind=='deliver' for kind,_ in calls)),1)

	def test_uncertain_dispatch_is_observed_and_never_blindly_reissued(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'unknown.sqlite3',['A'],periodic_status_enabled=False)
		dispatch_calls=[]
		delivered=[]

		def fail_dispatch(payload):
			dispatch_calls.append(payload)
			raise RuntimeError('synthetic uncertain host result')
		first_tickets=[ticket('EX-4',1)]
		first=production_controller_cycle(store,**planning_fixture_inputs(first_tickets, store, 1, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,first_tickets),dispatch_ticket=fail_dispatch,observe_dispatch=lambda payload:self.fail('not yet'),deliver_status=lambda digest:delivered.append(digest)or{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW})
		self.assertEqual(first['errors'][0]['state'],'UNKNOWN')
		self.assertEqual(first['streams'][0]['state'],'BLOCKED')
		self.assertIn('Dispatch outcome unknown',first['streams'][0]['reason'])
		self.assertEqual(delivered[0]['kind'],'CHANGE')
		self.assertIn('Dispatch outcome unknown',delivered[0]['streams'][0]['reason'])
		later='2026-10-02T10:00:01Z'

		def observe(payload):
			return dispatch_receipt(payload,later)
		second_tickets=[ticket('EX-4',1)]
		second=production_controller_cycle(store,**planning_fixture_inputs(second_tickets, store, 1, binding=INVENTORY_BINDING, now=later),now=later,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(later,second_tickets),dispatch_ticket=lambda payload:self.fail('uncertain dispatch was reissued'),observe_dispatch=observe,deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':later})
		self.assertEqual(len(dispatch_calls),1)
		self.assertEqual(second['dispatch_receipts'][0]['status'],'ACCEPTED')

	def test_successful_unknown_reconciliation_clears_matching_blocker_before_change_digest(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'unknown-blocker-clear.sqlite3',['A','B'],periodic_status_enabled=False)
		unknown_ticket=ticket('EX-66',1)
		unrelated_reason='independent dependency remains unavailable'
		unrelated_ticket=ticket('EX-OTHER',2,'BLOCKED',reason=unrelated_reason)
		delivered=[]
		repository_binding={'repository':'example.invalid/repository','head':'a'*40,'tree':'b'*40}
		with patch.object(continuous_controller,'canonical_repository_paths',side_effect=lambda tickets,*_:(tickets,repository_binding)):
			first=production_controller_cycle(store,**planning_fixture_inputs([unknown_ticket,unrelated_ticket], store, 2, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=2,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,[unknown_ticket,unrelated_ticket]),dispatch_ticket=lambda payload:(_ for _ in()).throw(RuntimeError('synthetic uncertain host result')),observe_dispatch=lambda payload:self.fail('first cycle cannot reconcile'),deliver_status=lambda digest:delivered.append(digest)or{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW})
		self.assertEqual(first['errors'][0]['state'],'UNKNOWN')
		unknown=delivered[-1]
		self.assertEqual(unknown['kind'],'CHANGE')
		self.assertEqual(len(unknown['unresolved_dispatches']),1)
		blocked=next((row for row in unknown['streams']if row['stream']=='A'))
		self.assertEqual(blocked['state'],'BLOCKED')
		self.assertIn('Dispatch outcome unknown',blocked['reason'])
		reconciled_at='2026-10-02T10:00:01Z'
		with patch.object(continuous_controller,'canonical_repository_paths',side_effect=lambda tickets,*_:(tickets,repository_binding)):
			second=production_controller_cycle(store,**planning_fixture_inputs([unknown_ticket,unrelated_ticket], store, 2, binding=INVENTORY_BINDING, now=reconciled_at),now=reconciled_at,host_capacity=2,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(reconciled_at,[unknown_ticket,unrelated_ticket]),dispatch_ticket=lambda payload:self.fail('unknown dispatch was reissued'),observe_dispatch=lambda payload:dispatch_receipt(payload,reconciled_at),deliver_status=lambda digest:delivered.append(digest)or{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':reconciled_at})
		self.assertEqual(second['dispatch_receipts'][0]['status'],'ACCEPTED')
		change=delivered[-1]
		self.assertEqual(change['kind'],'CHANGE')
		self.assertEqual(change['unresolved_dispatches'],[])
		matching=next((row for row in change['streams']if row['stream']=='A'))
		self.assertEqual(matching['state'],'WORKING')
		self.assertNotIn('Dispatch outcome unknown',matching['reason'])
		unrelated=next((row for row in change['streams']if row['stream']=='B'))
		self.assertEqual((unrelated['state'],unrelated['reason']),('BLOCKED',unrelated_reason))

	def test_unsuccessful_unknown_reconciliation_keeps_matching_blocker(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'unknown-blocker-retained.sqlite3',['A'],periodic_status_enabled=False)
		unknown_ticket=ticket('EX-UNKNOWN',1)
		store.schedule([unknown_ticket],NOW,host_capacity=1)
		intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(intent['dispatch_id'],NOW)
		unknown_at='2026-10-02T10:00:01Z'
		store.mark_dispatch_unknown(intent['dispatch_id'],unknown_at)
		unknown=store.digest(unknown_at)
		store.acknowledge_digest(unknown['delivery_id'],unknown_at)
		observed_at='2026-10-02T10:00:02Z'
		store.schedule([unknown_ticket],observed_at,host_capacity=1)
		store.begin_dispatch_reconciliation(intent['dispatch_id'],observed_at)
		store.mark_dispatch_unknown(intent['dispatch_id'],observed_at)
		change=store.digest('2026-10-02T10:00:03Z')
		self.assertEqual(change['kind'],'CHANGE')
		self.assertEqual(len(change['unresolved_dispatches']),1)
		matching=change['streams'][0]
		self.assertEqual(matching['state'],'BLOCKED')
		self.assertIn('Dispatch outcome unknown',matching['reason'])

	def test_old_unknown_reconciliation_failure_cannot_clobber_reassigned_stream(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'reassigned-unknown.sqlite3',['A','B'])
		old_ticket=ticket('EX-19',1)
		store.schedule([old_ticket],NOW,host_capacity=1)
		old_intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(old_intent['dispatch_id'],NOW)
		store.mark_dispatch_unknown(old_intent['dispatch_id'],'2026-10-02T10:00:01Z')
		replacement=ticket('EX-20',2)
		allocated_at='2026-10-02T10:00:02Z'
		replanned=[ticket('EX-19',1,'COMPLETE'),replacement]
		next_sha=seal_replan(store,'reassigned-unknown',replanned,allocated_at)
		store.schedule(replanned,allocated_at,host_capacity=2,inventory_sha256=next_sha)
		replacement_intent=next((item for item in store.prepare_dispatches(allocated_at)if item['ticket']=='EX-20'))
		begun=store.begin_dispatch(replacement_intent['dispatch_id'],'2026-10-02T10:00:03Z')
		store.finish_dispatch(replacement_intent['dispatch_id'],dispatch_receipt(begun,'2026-10-02T10:00:04Z'),'2026-10-02T10:00:04Z')
		observed_at='2026-10-02T10:00:05Z'
		delivered=[]

		def fail_reconciliation(payload):
			raise RuntimeError('observation unavailable')
		result=production_controller_cycle(store,**planning_fixture_inputs([ticket('EX-19',1,'COMPLETE'),replacement], store, 2, binding=INVENTORY_BINDING, now=observed_at),now=observed_at,host_capacity=2,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(observed_at,[ticket('EX-19',1,'COMPLETE'),replacement]),dispatch_ticket=lambda payload:self.fail('reassigned Y must not receive a duplicate intent'),observe_dispatch=fail_reconciliation,deliver_status=lambda digest:delivered.append(digest)or{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':observed_at})
		owner=next((row for row in result['streams']if row['stream']=='A'))
		self.assertEqual((owner['state'],owner['ticket'],owner['exact_tuple']),('WORKING','EX-20',replacement['exact_tuple']))
		self.assertEqual(len(delivered),1)
		detached=next((item for item in delivered[0]['unresolved_dispatches']if item['dispatch_id']==old_intent['dispatch_id']))
		self.assertEqual((detached['ticket'],detached['status'],detached['detached']),('EX-19','UNKNOWN',True))
		delivered_owner=next((row for row in delivered[0]['streams']if row['stream']=='A'))
		self.assertEqual((delivered_owner['state'],delivered_owner['ticket'],delivered_owner['exact_tuple']),('WORKING','EX-20',replacement['exact_tuple']))
		self.assertEqual([item['state']for item in result['errors']if item['operation']=='dispatch'],[])
		with store.connection()as db:
			self.assertEqual(db.execute("SELECT COUNT(*) FROM controller_dispatch WHERE ticket='EX-20'").fetchone()[0],1)

	def test_detached_unknown_reconciliation_emits_change_digest_while_periodic_disabled(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'reconciled-unknown.sqlite3',['A','B'],periodic_status_enabled=False)
		old_ticket=ticket('EX-OLD',1)
		store.schedule([old_ticket],NOW,host_capacity=1)
		old_intent=store.prepare_dispatches(NOW)[0]
		store.begin_dispatch(old_intent['dispatch_id'],NOW)
		store.mark_dispatch_unknown(old_intent['dispatch_id'],'2026-10-02T10:00:01Z')
		replacement=ticket('EX-NEW',2)
		replanned=[ticket('EX-OLD',1,'COMPLETE'),replacement]
		next_sha=seal_replan(store,'detached-unknown',replanned,'2026-10-02T10:00:02Z')
		store.schedule(replanned,'2026-10-02T10:00:02Z',host_capacity=2,inventory_sha256=next_sha)
		unresolved=store.digest('2026-10-02T10:00:03Z')
		self.assertEqual(unresolved['kind'],'CHANGE')
		self.assertEqual([(row['ticket'],row['detached'])for row in unresolved['unresolved_dispatches']],[('EX-OLD',True)])
		store.acknowledge_digest(unresolved['delivery_id'],'2026-10-02T10:00:04Z')
		reconcile_at='2026-10-02T10:00:05Z'
		reconciliation=store.begin_dispatch_reconciliation(old_intent['dispatch_id'],reconcile_at)
		store.finish_dispatch(old_intent['dispatch_id'],dispatch_receipt(reconciliation,reconcile_at),reconcile_at,reconcile=True)
		reconciled=store.digest('2026-10-02T10:00:06Z')
		self.assertIsNotNone(reconciled)
		self.assertEqual(reconciled['kind'],'CHANGE')
		self.assertEqual(reconciled['unresolved_dispatches'],[])

	def test_dispatch_rejects_stale_direct_and_cached_restart_receipts(self):
		store=ContinuousControllerStore(Path(self.temporary.name)/'dispatch-stale.sqlite3',['A'])
		payloads=[]
		first=production_controller_cycle(store,**planning_fixture_inputs([ticket('EX-6',1)], store, 1, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(NOW,[ticket('EX-6',1)]),dispatch_ticket=lambda payload:payloads.append(payload)or dispatch_receipt(payload,'2026-10-02T09:59:59Z'),observe_dispatch=lambda payload:self.fail('first cycle cannot reconcile'),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':NOW})
		self.assertEqual(first['errors'][0]['state'],'UNKNOWN')
		cached_receipt=dispatch_receipt(payloads[0],NOW)
		later='2026-10-02T10:00:01Z'
		cached=production_controller_cycle(store,**planning_fixture_inputs([ticket('EX-6',1)], store, 1, binding=INVENTORY_BINDING, now=later),now=later,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(later,[ticket('EX-6',1)]),dispatch_ticket=lambda payload:self.fail('unknown dispatch was reissued'),observe_dispatch=lambda payload:cached_receipt,deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':later})
		self.assertEqual(cached['dispatch_receipts'],[])
		self.assertEqual(cached['errors'][0]['state'],'UNKNOWN')
		fresh='2026-10-02T10:00:02Z'
		accepted=production_controller_cycle(store,**planning_fixture_inputs([ticket('EX-6',1)], store, 1, binding=INVENTORY_BINDING, now=fresh),now=fresh,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda:inventory_observation(fresh,[ticket('EX-6',1)]),dispatch_ticket=lambda payload:self.fail('unknown dispatch was reissued'),observe_dispatch=lambda payload:dispatch_receipt(payload,fresh),deliver_status=lambda digest:{'delivery_id':digest['delivery_id'],'status':'DELIVERED','observed_at':fresh})
		self.assertEqual(accepted['dispatch_receipts'][0]['dispatch_nonce'],payloads[0]['dispatch_nonce'])

	def test_production_cycle_rejects_partial_or_wrong_scope_inventory(self):
		observed=inventory_observation(NOW,[ticket('EX-5',1)])
		for mutate in(lambda value:value.update(complete=False),lambda value:value['binding'].update(project_id='other'),lambda value:value.update(inventory_sha256='0'*64)):
			value=json.loads(json.dumps(observed))
			mutate(value)
			with self.subTest(value=value),self.assertRaises(ValidationError):
				production_controller_cycle(self.store,**planning_fixture_inputs([ticket('EX-5',1)], self.store, 1, binding=INVENTORY_BINDING, now=NOW),now=NOW,host_capacity=1,inventory_binding=INVENTORY_BINDING,**REPOSITORY,observe_inventory=lambda item=value:item,dispatch_ticket=lambda payload:self.fail('invalid inventory dispatched'),observe_dispatch=lambda payload:self.fail('invalid inventory observed'),deliver_status=lambda payload:self.fail('invalid inventory delivered'))

class JiraProgressTests(unittest.TestCase):
	scope='project=QA AND fixVersion=1.9.3'

	def setUp(self):
		self.temporary=tempfile.TemporaryDirectory(prefix='awf-controller-jira-')
		self.addCleanup(self.temporary.cleanup)
		self.store=ContinuousControllerStore(Path(self.temporary.name)/'controller.sqlite3',['A','B','C'])
		gate=patch('agentic.closeout.require_jira_reconciliation_before_counts',return_value={'status':'PASS','count_allowed':True,'ticket':'EX-1'})
		gate.start()
		self.addCleanup(gate.stop)

	def receipt(self,ticket='EX-1',**overrides):
		value={'ticket':ticket,'issue_id':'10001',**BINDING,'status':'RECONCILED','operation_id':str(uuid.UUID(int=2)),'before_status_id':'3','after_status_id':'10002','observed_at':NOW}
		value.update(overrides)
		return value

	def page(self,items,*,cursor=None,complete=True,snapshot='snapshot-1',observed=NOW,include_epics=False):
		scope_hash=fingerprint('jira-progress-scope',{'scope':self.scope,'binding':BINDING,'include_epics':include_epics})
		return{'items':items,'next_cursor':cursor,'complete':complete,'snapshot_id':snapshot,'scope_sha256':scope_hash,'observed_at':observed}

	def call(self,reconcile=None,fetch=None,**overrides):
		args=dict(jira_enabled=True,merged_ticket='EX-1',scope=self.scope,observed_at=NOW,jira_binding=BINDING,count_gate={},reconcile_merged_ticket=reconcile or(lambda ticket:self.receipt(ticket)),fetch_scope_page=fetch or(lambda scope,cursor:self.page([])))
		args.update(overrides)
		return post_merge_jira_progress(**args)

	def test_reconciliation_precedes_complete_paginated_stable_identity_counts(self):
		calls=[]

		def reconcile(ticket_id):
			calls.append(('reconcile',ticket_id))
			return self.receipt(ticket_id)

		def page(scope,cursor):
			calls.append(('count',cursor))
			if cursor is None:
				return self.page([{'id':'1','issue_type':'TASK','status_category':'TERMINAL'},{'id':'2','issue_type':'EPIC','status_category':'NON_TERMINAL'}],cursor='p2',complete=False)
			return self.page([{'id':'3','issue_type':'TASK','status_category':'NON_TERMINAL'}])
		result=self.call(reconcile=reconcile,fetch=page)
		self.assertEqual(calls[0],('reconcile','EX-1'))
		self.assertEqual((result['jira_state'],result['closed'],result['remaining_open']),('COUNTED',1,1))

	def test_routine_merge_observation_uses_reconcile_first_progress_route(self):
		calls=[]
		args=dict(jira_enabled=True,merged_ticket='EX-1',scope=self.scope,observed_at=NOW,jira_binding=BINDING,count_gate={},reconcile_merged_ticket=lambda ticket:calls.append(('reconcile',ticket))or self.receipt(ticket),fetch_scope_page=lambda scope,cursor:calls.append(('count',cursor))or self.page([]))
		with patch('agentic.closeout.require_jira_reconciliation_before_counts',return_value={'status':'PASS','count_allowed':True,'ticket':'EX-1'}):
			result=production_merge_observed(lifecycle_state='MERGING',lifecycle_facts={'merge_confirmed':True,'candidate_matched':True},jira_progress=args)
		self.assertEqual(calls,[('reconcile','EX-1'),('count',None)])
		self.assertEqual((result['state'],result['jira_progress']['jira_state'],result['execution_authority']),('MERGED','COUNTED',False))
		calls.clear()
		with self.assertRaises(ValidationError):
			production_merge_observed(lifecycle_state='MERGING',lifecycle_facts={},jira_progress=args)
		self.assertEqual(calls,[])

	def test_awf76_production_merge_count_requires_validated_closeout_gate(self):
		calls=[]
		args=dict(jira_enabled=True,merged_ticket='EX-1',scope=self.scope,observed_at=NOW,jira_binding=BINDING,reconcile_merged_ticket=lambda ticket:calls.append(('reconcile',ticket))or self.receipt(ticket),fetch_scope_page=lambda scope,cursor:calls.append(('count',cursor))or self.page([]))
		with self.assertRaisesRegex(ValidationError,'closeout'):
			production_merge_observed(lifecycle_state='MERGING',lifecycle_facts={'merge_confirmed':True,'candidate_matched':True},jira_progress=args)
		self.assertEqual([],calls)
		with self.assertRaisesRegex(ValidationError,'closeout'):
			post_merge_jira_progress(**args,count_gate=None)
		self.assertEqual([],calls)

	def test_disabled_jira_performs_no_reads_or_writes(self):
		calls=[]
		result=self.call(jira_enabled=False,reconcile=lambda value:calls.append('write'),fetch=lambda scope,cursor:calls.append('read'))
		self.assertEqual(calls,[])
		self.assertEqual((result['jira_state'],result['closed'],result['remaining_open']),('JIRA_DISABLED','UNOBSERVED','UNOBSERVED'))

	def test_wrong_reconciliation_binding_is_not_retried_or_counted(self):
		for field,value in(('cloud_id','other'),('actor_id','other'),('status','UNKNOWN'),('ticket','EX-2')):
			calls=[]
			result=self.call(reconcile=lambda ticket,f=field,v=value:calls.append(ticket)or self.receipt(ticket,**{f:v}),fetch=lambda scope,cursor:self.fail('count requires proven reconciliation'))
			self.assertEqual(calls,['EX-1'])
			self.assertEqual(result['jira_state'],'UNOBSERVED')

	def test_snapshot_scope_time_category_and_completeness_fail_closed(self):
		bad_final=[self.page([],complete=False),self.page([{'id':'1','issue_type':'TASK','status_category':'UNKNOWN'}])]
		for page in bad_final:
			with self.subTest(page=page):
				result=self.call(fetch=lambda scope,cursor,value=page:value)
				self.assertEqual((result['jira_state'],result['closed']),('RECONCILED','UNOBSERVED'))
		pages=[self.page([],cursor='p2',complete=False),self.page([],snapshot='snapshot-2',observed='2026-10-02T10:00:01Z')]
		result=self.call(fetch=lambda scope,cursor:pages[0]if cursor is None else pages[1])
		self.assertEqual(result['jira_state'],'RECONCILED')

	def test_count_snapshot_must_not_predate_reconciliation(self):
		receipt=self.receipt(observed_at='2026-10-02T10:00:01Z')
		stale=self.page([],observed=NOW)
		result=self.call(reconcile=lambda ticket:receipt,fetch=lambda scope,cursor:stale)
		self.assertEqual((result['jira_state'],result['closed'],result['remaining_open']),('RECONCILED','UNOBSERVED','UNOBSERVED'))
		self.assertIn('incomplete',result['reason'])

	def test_pagination_page_and_item_bounds_are_enforced(self):
		calls=[]

		def endless(scope,cursor):
			calls.append(cursor)
			number=len(calls)
			return self.page([],cursor=f'p{number}',complete=False)
		result=self.call(fetch=endless,max_pages=2)
		self.assertEqual(result['jira_state'],'RECONCILED')
		self.assertEqual(len(calls),2)
		result=self.call(fetch=lambda scope,cursor:self.page([{'id':'1','issue_type':'TASK','status_category':'TERMINAL'},{'id':'2','issue_type':'TASK','status_category':'TERMINAL'}]),max_items=1)
		self.assertEqual(result['closed'],'UNOBSERVED')
		result=self.call(fetch=lambda scope,cursor:self.page([]),max_bytes=8)
		self.assertEqual(result['closed'],'UNOBSERVED')
		ticks=iter([0.0,0.0,2.0])
		result=self.call(fetch=lambda scope,cursor:self.page([]),max_seconds=1,clock=lambda:next(ticks))
		self.assertEqual(result['jira_state'],'RECONCILED')

	def test_epic_inclusion_is_explicit_and_contracts_accept_records(self):
		fetch=lambda scope,cursor:self.page([{'id':'1','issue_type':'EPIC','status_category':'NON_TERMINAL'}],include_epics=True)
		authoritative=self.call(fetch=fetch,include_epics=True)
		self.assertEqual(authoritative['remaining_open'],1)
		contracts=Contracts(ROOT/'.agentic/schemas')
		contracts.validate('jira-progress',authoritative)
		local_semantics('jira-progress',authoritative)
		contracts.validate('jira-progress',self.call(jira_enabled=False))

	def test_production_jira_lifecycle_writes_once_and_requires_readback(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		calls=[]
		binding=bundle['critic']['binding']
		provider=configured_jira_provider(config)
		result=production_jira_lifecycle(self.store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=7)),now=NOW,evidence=['urn:awf:fixture:jira'],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),read_current_status=lambda value:calls.append('read-before')or jira_current(value,'Ready',NOW),write_transition=lambda record:calls.append('write')or{'operation_id':record['operation_id'],'issue_id':binding['issue_id'],'status':'ATTEMPTED','observed_at':NOW,'jira_provider':provider},read_transition=lambda record,operation:calls.append('read-after')or{'issue_id':binding['issue_id'],'status':'In Progress','actor':'fixture-controller','observed_at':NOW,'jira_provider':provider})
		self.assertEqual(calls,['read-before','write','read-after'])
		self.assertEqual((result['record']['status'],result['writes_stopped']),('SUCCEEDED',False))

	def test_disabled_jira_lifecycle_uses_no_adapter(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		config['jira']['enabled']=False
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		calls=[]
		result=production_jira_lifecycle(self.store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=bundle['critic']['binding'],issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=11)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},read_current_status=lambda value:calls.append('read-before'),write_transition=lambda value:calls.append('write'),read_transition=lambda record,operation:calls.append('read-after'))
		self.assertEqual(calls,[])
		self.assertFalse(result['planned'])

	def test_jira_lifecycle_refuses_unbound_or_unobserved_identity_before_issue_lookup(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		calls=[]
		common=dict(config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=bundle['critic']['binding'],issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=17)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},read_current_status=lambda value:calls.append('lookup'),write_transition=lambda value:calls.append('write'),read_transition=lambda record,operation:calls.append('readback'))
		unbound=json.loads(json.dumps(config))
		unbound['jira'].update(cloud_id=None,provider_project_id=None,controller_actor_id=None)
		result=production_jira_lifecycle(self.store,**common|{'config':unbound},observe_provider_identity=lambda:calls.append('identity'))
		self.assertEqual(result['status'],'IDENTITY_UNBOUND')
		self.assertEqual(calls,[])
		result=production_jira_lifecycle(self.store,**common)
		self.assertEqual(result['status'],'IDENTITY_UNOBSERVED')
		self.assertEqual(calls,[])
		result=production_jira_lifecycle(self.store,**common,observe_provider_identity=lambda:(_ for _ in()).throw(ConnectionError('offline')))
		self.assertEqual(result['status'],'IDENTITY_UNOBSERVED')
		self.assertEqual(result['issue_lookups'],0)
		self.assertEqual(calls,[])

	def test_production_jira_lifecycle_rejects_unbound_or_stale_adapter_evidence(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		binding=bundle['critic']['binding']
		writes=[]
		common=dict(config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=8)),now=NOW,evidence=['urn:awf:fixture:jira'],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),write_transition=lambda record:writes.append(record),read_transition=lambda record,operation:self.fail('invalid write cannot be read back'))
		result=production_jira_lifecycle(self.store,**common,read_current_status=lambda value:jira_current(value,'Ready',NOW)|{'issue_id':'other'})
		self.assertTrue(result['writes_stopped'])
		self.assertEqual(writes,[])
		result=production_jira_lifecycle(self.store,**common,read_current_status=lambda value:jira_current(value,'Ready','2026-10-02T09:59:59Z'))
		self.assertTrue(result['writes_stopped'])
		self.assertEqual(writes,[])

	def test_jira_lifecycle_rejects_wrong_connector_site_project_or_actor_before_mutation(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		original=bundle['critic']['binding']
		provider=configured_jira_provider(config)
		for field,replacement in(('cloud_id','other-cloud'),('site','https://other.example.invalid'),('project_id','other-project'),('project_key','OTHER'),('controller_actor_id','other-controller')):
			wrong=dict(provider)
			wrong[field]=replacement
			calls=[]
			with self.subTest(field=field):
				result=production_jira_lifecycle(self.store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=original,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=18)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda wrong=wrong:wrong,read_current_status=lambda value:calls.append('lookup'),write_transition=lambda record:calls.append('write'),read_transition=lambda record,operation:calls.append('readback'))
				self.assertTrue(result['writes_stopped'])
				self.assertEqual(result['status'],'IDENTITY_MISMATCH')
			self.assertEqual(calls,[])
		calls=[]
		result=production_jira_lifecycle(self.store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=original,issue_type='LEAF',state='DISPATCHED',producer_id='other-controller',run_id=str(uuid.UUID(int=19)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),read_current_status=lambda value:calls.append('read'),write_transition=lambda record:calls.append('write'),read_transition=lambda record,operation:calls.append('readback'))
		self.assertEqual(result['status'],'IDENTITY_MISMATCH')
		self.assertEqual(calls,[])

	def test_jira_lifecycle_binds_write_and_readback_receipts_to_provider_identity(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		binding=bundle['critic']['binding']
		provider=configured_jira_provider(config)
		wrong_site=dict(provider)|{'site':'https://second-connector.example.invalid'}
		for stage in('write','readback'):
			store=ContinuousControllerStore(Path(self.temporary.name)/f'jira-wrong-{stage}.sqlite3',['A'])
			result=production_jira_lifecycle(store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=20)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),read_current_status=lambda value:jira_current(value,'Ready',NOW),write_transition=lambda record,stage=stage:{'operation_id':record['operation_id'],'issue_id':binding['issue_id'],'status':'ATTEMPTED','observed_at':NOW,'jira_provider':wrong_site if stage=='write' else provider},read_transition=lambda record,operation,stage=stage:{'issue_id':binding['issue_id'],'status':'In Progress','actor':'other-controller' if stage=='readback' else 'fixture-controller','observed_at':NOW,'jira_provider':provider})
			self.assertTrue(result['writes_stopped'])
			self.assertEqual(store.jira_operations(binding['issue_id'])[0]['status'],'UNKNOWN')

	def test_jira_identity_is_rechecked_after_issue_read_and_before_write(self):
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		binding=bundle['critic']['binding']
		observations=iter([observe_jira_provider(config),observe_jira_provider(config,controller_actor_id='changed-actor')])
		calls=[]
		result=production_jira_lifecycle(self.store,config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=21)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:next(observations),read_current_status=lambda value:calls.append('lookup')or jira_current(value,'Ready',NOW),write_transition=lambda record:calls.append('write'),read_transition=lambda record,operation:calls.append('readback'))
		self.assertEqual(result['status'],'IDENTITY_MISMATCH')
		self.assertEqual(calls,['lookup'])

	def test_jira_intent_survives_crash_after_side_effect_without_reissue(self):
		path=Path(self.temporary.name)/'jira-crash.sqlite3'
		store=ContinuousControllerStore(path,['A'])
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		binding=bundle['critic']['binding']
		external={'status':'Ready','writes':0}

		def observe(_binding,observed_at=NOW):
			return jira_current(_binding,external['status'],observed_at)

		def crash_after_mutation(record):
			external['writes']+=1
			external['status']='In Progress'
			raise SystemExit('synthetic process loss after Jira side effect')
		common=dict(config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=12)),evidence=['urn:awf:fixture:jira'],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),read_transition=lambda record,operation:self.fail('crashed call has no receipt'))
		with self.assertRaisesRegex(SystemExit,'synthetic process loss'):
			production_jira_lifecycle(store,now=NOW,read_current_status=observe,write_transition=crash_after_mutation,**common)
		in_flight=store.jira_operations(binding['issue_id'])
		self.assertEqual((len(in_flight),in_flight[0]['status'],external['writes']),(1,'IN_FLIGHT',1))
		operation_id=in_flight[0]['operation_id']
		restarted=ContinuousControllerStore(path,['A'])
		recovered_at='2026-10-02T10:00:01Z'
		restarted.recover_jira_operations(recovered_at)
		self.assertEqual(restarted.jira_operations(binding['issue_id'])[0]['status'],'UNKNOWN')
		result=production_jira_lifecycle(restarted,now=recovered_at,read_current_status=lambda value:observe(value,recovered_at),write_transition=lambda record:self.fail('unknown side effect was reissued'),**common)
		durable=restarted.jira_operations(binding['issue_id'])
		self.assertEqual((external['writes'],durable[0]['operation_id'],durable[0]['status']),(1,operation_id,'SUCCEEDED'))
		self.assertFalse(result['planned'])

	def test_unknown_jira_intent_without_success_readback_stops_all_retries(self):
		path=Path(self.temporary.name)/'jira-unknown.sqlite3'
		store=ContinuousControllerStore(path,['A'])
		config=json.loads((ROOT/'.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		binding=bundle['critic']['binding']
		common=dict(config=config,contract=bundle['contract'],event='WORKER_STARTED',facts={'run_registered':True,'worktree_verified':True},binding=binding,issue_type='LEAF',state='DISPATCHED',producer_id='fixture-controller',run_id=str(uuid.UUID(int=13)),now=NOW,evidence=[],transition_id='31',observe_issue_identity=lambda key:{'issue_key':'EX-42','observed_at':NOW},observe_provider_identity=lambda:observe_jira_provider(config),read_current_status=lambda value:jira_current(value,'Ready',NOW),read_transition=lambda record,operation:self.fail('no receipt exists'))
		first=production_jira_lifecycle(store,write_transition=lambda record:(_ for _ in()).throw(RuntimeError('lost')),**common)
		self.assertTrue(first['writes_stopped'])
		calls=[]
		second=production_jira_lifecycle(ContinuousControllerStore(path,['A']),write_transition=lambda record:calls.append(record),**common)
		self.assertEqual(calls,[])
		self.assertTrue(second['writes_stopped'])
		self.assertIn('not proven successful',second['reason'])

	def test_workflow_cli_runs_all_production_routes_through_digest_pinned_adapter(self):
		temporary=tempfile.TemporaryDirectory(prefix='awf-controller-cli-')
		self.addCleanup(temporary.cleanup)
		root=Path(temporary.name)
		state_path=root/'controller.sqlite3'
		worktree=root/'worker'
		worktree.mkdir()
		subprocess.run(['git','-C',str(worktree),'init','-q','--initial-branch=main'],check=True)
		subprocess.run(['git','-C',str(worktree),'config','user.email','fixture@example.invalid'],check=True)
		subprocess.run(['git','-C',str(worktree),'config','user.name','fixture'],check=True)
		fixture_path=worktree/'.agentic/tests/fixtures/ex-42.txt'
		fixture_path.parent.mkdir(parents=True)
		fixture_path.write_bytes(b'controller fixture\n')
		subprocess.run(['git','-C',str(worktree),'add',fixture_path.relative_to(worktree)],check=True)
		subprocess.run(['git','-C',str(worktree),'commit','-q','-m','fixture'],check=True)
		inventory=[ticket('EX-42',1)]
		scope='project=QA AND fixVersion=1.9.3'
		jira_binding={'cloud_id':'fixture-cloud','project_id':'fixture-project','actor_id':'fixture-controller'}
		scope_sha256=fingerprint('jira-progress-scope',{'scope':scope,'binding':jira_binding,'include_epics':False})
		bundle=json.loads((ROOT/'.agentic/examples/evidence-bundle.json').read_text())
		bundle['critic']['binding']=dict(bundle['critic']['binding'],issue_id='EX-42')
		project_config_path=ROOT/'.agentic/examples/PROJECT_CONFIG.yaml'
		project_config=json.loads(project_config_path.read_text())
		INVENTORY_BINDING={**globals()['INVENTORY_BINDING'], 'project_id': project_config['project']['id']}
		project_config['controller'].update(dispatch_enabled=True,auto_dispatch=True,auto_request_critic=True,auto_resume_amendments=True)
		project_config_path=root/'project-config.json'
		project_config_path.write_text(json.dumps(project_config),encoding='utf-8')
		project_config_bytes=project_config_path.read_bytes()
		(worktree/'.agentic/PROJECT_CONFIG.yaml').write_bytes(project_config_bytes)
		from agentic.operating import default_operating
		from agentic.model_routing import policy_from_config
		(worktree/'OPERATING_CONFIG.yaml').write_text(json.dumps(default_operating(policy_from_config(project_config))),encoding='utf-8')
		subprocess.run(['git','-C',str(worktree),'add','.agentic/PROJECT_CONFIG.yaml','OPERATING_CONFIG.yaml'],check=True)
		subprocess.run(['git','-C',str(worktree),'commit','-q','-m','fixture governance'],check=True)
		publication=publication_observation('EX-42',NOW)
		publication['identity']['actor_id']=project_config['github']['expected_actor_id']

		def git(*arguments,binary=False):
			completed=subprocess.run(['git','-C',str(worktree),*arguments],check=True,capture_output=True,text=not binary)
			return completed.stdout if binary else completed.stdout.strip()
		merged_head=git('rev-parse','HEAD')
		merged_tree=git('rev-parse','HEAD^{tree}')
		bound_path='.agentic/tests/fixtures/ex-42.txt'
		bound_blob=git('rev-parse',f'HEAD:{bound_path}')
		bound_bytes=git('cat-file','blob',bound_blob,binary=True)
		merge_result_id=str(uuid.UUID(int=24))
		transition_id=str(uuid.UUID(int=25))
		closeout={'schema_version':3,'record_id':str(uuid.UUID(int=20)),'created_at':NOW,'producer_id':'fixture-controller','run_id':str(uuid.UUID(int=21)),'binding':{'project_id':project_config['project']['id'],'repository_id':project_config['github']['repository_id'],'issue_id':'QA-CLI','requirements_hash':'a'*64,'contract_hash':'b'*64,'policy_hash':policy_hash(project_config,definition()),'candidate_id':'c'*64},'pr_number':42,'reviewed_head_sha':merged_head,'base_sha':merged_head,'merge_commit_sha':merged_head,'merge_tree_sha':merged_tree,'bound_files':[{'path':bound_path,'blob_sha':bound_blob,'sha256':sha256(bound_bytes)}],'gate_record_id':str(uuid.UUID(int=22)),'review_record_ids':[str(uuid.UUID(int=23))],'jira_transition_record_id':transition_id,'evidence':['urn:awf:fixture:cli-closeout']}
		transition={'schema_version':3,'record_id':transition_id,'created_at':NOW,'producer_id':'fixture-controller','run_id':str(uuid.UUID(int=26)),'binding':closeout['binding'],'operation_id':str(uuid.UUID(int=27)),'merge_result_id':merge_result_id,'jira_provider':{'cloud_id':'fixture-cloud','site':'https://jira.example.invalid','project_id':'fixture-project','project_key':'EX','controller_actor_id':'fixture-controller'},'lifecycle_event':'JIRA_RECONCILED','from_status_id':'In Review','to_status_id':'Done','transition_id':'31','status':'SUCCEEDED','evidence':['urn:awf:fixture:cli-jira-transition'],'read_back':{'observed_status':'Done','observed_actor':'fixture-controller','observed_at':NOW}}
		planning_inventory={**inventory_observation(NOW,inventory), 'binding': INVENTORY_BINDING, 'inventory_sha256': fingerprint('controller-inventory', {'binding': INVENTORY_BINDING, 'tickets': inventory})}
		reservation_envelope={'schema_version':1,'source':'host_observation','repository_id':INVENTORY_BINDING['repository_id'],'head_sha':merged_head,'tree_sha':merged_tree,'inventory_sha256':planning_inventory['inventory_sha256'],'observed_at':NOW,'authenticated':True,'complete':True,'sources':[]}
		reservation_envelope['observation_sha256']=fingerprint('controller-reservation-observation',reservation_envelope)
		adapter_config={'reservations':reservation_envelope,'inventory':planning_inventory,'publication':publication,'jira_now':NOW,'jira_before':'Ready','jira_after':'In Progress','reconcile_receipt':{'ticket':'QA-CLI','issue_id':'10001',**jira_binding,'status':'RECONCILED','operation_id':str(uuid.UUID(int=9)),'before_status_id':'3','after_status_id':'10002','observed_at':NOW},'jira_page':{'items':[{'id':'10001','issue_type':'TASK','status_category':'TERMINAL'}],'next_cursor':None,'complete':True,'snapshot_id':'snapshot-cli','scope_sha256':scope_sha256,'observed_at':NOW},'project_config_observation':{'raw_bytes_base64':base64.b64encode(project_config_bytes).decode('ascii'),'config_sha256':sha256(project_config_bytes),'policy_sha256':policy_hash(project_config,definition()),'path':'.agentic/PROJECT_CONFIG.yaml','repository':project_config['github']['repository'],'repository_id':project_config['github']['repository_id'],'ref':'refs/heads/'+project_config['github']['base_branch'],'blob_sha':'e'*40,'acceptance':{'status':'ACCEPTED','receipt_id':'cli-acceptance','workflow_version':project_config['template']['expected_workflow_version']}},'closeout_observation':{'record':closeout,'merge_result_id':merge_result_id,'merge_observed_at':NOW},'jira_reconciliation_observation':{'record':transition}}
		adapter_source='def build_adapters(config):\n    now = config["jira_now"]\n    def observe_inventory():\n        return config["inventory"]\n    def observe_reservations(request):\n        return config["reservations"]\n    def observe_publication(item):\n        return config["publication"]\n    def dispatch_ticket(payload):\n        keys = ["dispatch_schema_version", "dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce", "prepared_at", "begun_at", "role", "lifecycle_stage", "lane_start_waivers"]\n        if "reconcile_nonce" in payload:\n            keys += ["reconcile_nonce", "reconcile_at"]\n        return {key: payload[key] for key in keys} | {"status": "ACCEPTED", "dispatch_state": "TERMINAL", "terminal": True, "observed_at": now, "session_id": "session-" + payload["dispatch_id"]}\n    def observe_dispatch(payload):\n        return dispatch_ticket(payload)\n    def deliver_status(digest):\n        return {"delivery_id": digest["delivery_id"], "status": "DELIVERED", "observed_at": now}\n    def observe_provider_identity():\n        return {"cloud_id": "fixture-cloud", "site": "https://jira.example.invalid",\n                "project_id": "fixture-project", "project_key": "EX",\n                "controller_actor_id": "fixture-controller"}\n    def observe_issue_identity(issue):\n        return {"issue_key": "EX-42", "observed_at": now}\n    def read_current_status(binding):\n        return {"issue_id": binding["issue_id"], "status_id": config["jira_before"], "observed_at": now, "jira_provider": binding["jira_provider"]}\n    def write_transition(record):\n        return {"operation_id": record["operation_id"], "issue_id": record["binding"]["issue_id"], "status": "ATTEMPTED", "observed_at": now, "jira_provider": record["jira_provider"]}\n    def read_transition(record, operation):\n        return {"issue_id": record["binding"]["issue_id"], "status": config["jira_after"], "actor": record["producer_id"], "observed_at": now, "jira_provider": record["jira_provider"]}\n    def reconcile_merged_ticket(ticket):\n        value = dict(config["reconcile_receipt"])\n        value["ticket"] = ticket\n        return value\n    def fetch_scope_page(scope, cursor):\n        return config["jira_page"]\n    def observe_accepted_project_config(request):\n        return {"observed_at": now, "result": config["project_config_observation"]}\n    def observe_closeout_record(request):\n        return {"observed_at": now, "result": config["closeout_observation"]}\n    def observe_jira_reconciliation(request):\n        return {"observed_at": now, "result": config["jira_reconciliation_observation"]}\n    return {name: value for name, value in locals().items() if callable(value) and name != "build_adapters"}\n'
		adapter_source=adapter_source.replace('    def observe_inventory():','    resource_evidence = {}\n    def observe_host_resources(request):\n        from agentic.canonical import fingerprint\n        claims = {"observed_at": request["observed_at"], "host_capacity": 1, "resources": [{"name": "cpu", "capacity": 1}]}\n        auth = {"producer_id": "fixture-host", "evidence_id": "fixture-evidence", "evidence_sha256": fingerprint("fixture-resource-evidence", claims), "request_sha256": fingerprint("planning_resource_request", request)}\n        resource_evidence.update({"status": "VERIFIED", **auth, **claims})\n        return {"status": "OBSERVED", **auth, **claims}\n    def verify_host_observation(authentication, request):\n        from agentic.canonical import fingerprint\n        if authentication["request_sha256"] != fingerprint("planning_resource_request", request):\n            return {}\n        return dict(resource_evidence)\n'+'    def observe_inventory():')
		adapter_source=adapter_source.replace('    def observe_inventory():', '    planning_evidence = {}\n    def observe_planning_backlog(request):\n        from copy import deepcopy\n        body = {"format": "awf-backlog-provider-snapshot-1", "complete": True, "provider": request["provider"], "binding": request["binding"], "observed_at": request["observed_at"], "tickets": [{"ticket": {"key": "EX-42", "status": "open", "risk_tier": 3}, "components": [{"kind": kind, "ticket_key": "EX-42", "binding": request["binding"], "observed_at": request["observed_at"], "files": [".agentic/tests/fixtures/ex-42.txt"] if kind == "brief" else []} for kind in ("brief", "open_pr", "worktree_diff")]}]}\n        planning_evidence["backlog"] = deepcopy(body)\n        return {**body, "authentication": {"evidence_id": "backlog"}}\n    def observe_planning_events(request):\n        from copy import deepcopy\n        body = {"binding": request["binding"], "observed_at": request["observed_at"], "events": []}\n        planning_evidence["events"] = deepcopy(body)\n        return {**body, "authentication": {"evidence_id": "events"}}\n    def verify_planning_backlog(auth, request):\n        from copy import deepcopy\n        return {"status": "VERIFIED", "evidence_id": "backlog", "snapshot": deepcopy(planning_evidence["backlog"])}\n    def verify_planning_events(auth, request):\n        from copy import deepcopy\n        return {"status": "VERIFIED", "evidence_id": "events", "snapshot": deepcopy(planning_evidence["events"])}\n    def verify_planning_owner(*args):\n        return {}\n'+'    def observe_inventory():')
		adapter_path=root/'reviewed_adapter.py'
		adapter_path.write_text(adapter_source,encoding='utf-8',newline='\n')
		adapter_config_path=root/'adapter.json'
		adapter_config_path.write_text(json.dumps(adapter_config),encoding='utf-8')
		binding_path=root/'inventory-binding.json'
		binding_path.write_text(json.dumps(INVENTORY_BINDING),encoding='utf-8')
		pin=sha256(adapter_path.read_bytes())
		common=[sys.executable,'-B',str(ROOT/'.agentic/scripts/workflow.py'),'--root',str(ROOT),'controller','--state',str(state_path),'--stream','A','--stream','B','--stream','C','--worktree-root',str(worktree),'--project-config',str(project_config_path),'--adapter-module',str(adapter_path),'--adapter-sha256',pin,'--adapter-config',str(adapter_config_path)]
		cycle=subprocess.run([*common,'cycle','--inventory-binding',str(binding_path),'--repository-root',str(worktree),'--repository-head-sha',merged_head,'--repository-tree-sha',merged_tree,'--now',NOW,'--host-capacity','1'],cwd=ROOT,text=True,capture_output=True,timeout=30)
		self.assertEqual(cycle.returncode,0,cycle.stderr)
		cycle_output=json.loads(cycle.stdout)
		self.assertEqual((len(cycle_output['dispatch_receipts']),cycle_output['status_delivery']['status']),(1,'DELIVERED'))

		def dump(name,value):
			path=root/name
			path.write_text(json.dumps(value),encoding='utf-8')
			return path
		lifecycle=subprocess.run([*common,'jira-lifecycle','--contract',str(dump('contract.json',bundle['contract'])),'--event','WORKER_STARTED','--facts',str(dump('facts.json',{'run_registered':True,'worktree_verified':True})),'--binding',str(dump('binding.json',bundle['critic']['binding'])),'--lifecycle-state','DISPATCHED','--producer-id','fixture-controller','--run-id',str(uuid.UUID(int=10)),'--now',NOW,'--evidence','urn:awf:fixture:jira','--transition-id','31'],cwd=ROOT,text=True,capture_output=True,timeout=30)
		self.assertEqual(lifecycle.returncode,0,lifecycle.stderr)
		lifecycle_output=json.loads(lifecycle.stdout)
		self.assertEqual(lifecycle_output['record']['status'],'SUCCEEDED')
		self.assertEqual(lifecycle_output['current_observation']['status_id'],'Ready')
		progress={'jira_enabled':True,'merged_ticket':'QA-CLI','scope':scope,'observed_at':NOW,'jira_binding':jira_binding,'count_gate':{'closeout_record':closeout}}
		merge=subprocess.run([*common,'merge-observed','--lifecycle-state','MERGING','--lifecycle-facts',str(dump('merge-facts.json',{'merge_confirmed':True,'candidate_matched':True})),'--jira-progress',str(dump('progress.json',progress)),'--repository-root',str(worktree)],cwd=ROOT,text=True,capture_output=True,timeout=30)
		self.assertEqual(merge.returncode,0,merge.stderr)
		merge_output=json.loads(merge.stdout)
		self.assertEqual((merge_output['state'],merge_output['jira_progress']['jira_state']),('MERGED','COUNTED'))
		wrong_pin=subprocess.run([*common[:common.index('--adapter-sha256')+1],'0'*64,'--adapter-config',str(adapter_config_path),'cycle','--inventory-binding',str(binding_path),'--repository-root',str(ROOT),'--repository-head-sha',REPOSITORY['repository_head_sha'],'--repository-tree-sha',REPOSITORY['repository_tree_sha'],'--now',NOW,'--host-capacity','1'],cwd=ROOT,text=True,capture_output=True,timeout=30)
		self.assertNotEqual(wrong_pin.returncode,0)
		self.assertIn('SHA-256 pin',wrong_pin.stderr)
if __name__=='__main__':
	unittest.main()
