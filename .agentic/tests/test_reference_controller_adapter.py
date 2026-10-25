import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from test_continuous_controller_planning import planning_fixture_inputs, planning_resource_inputs
from agentic import ValidationError
from source_only import skip_unless_source_repo
from agentic.canonical import canonical, fingerprint, sha256
from agentic.contracts import Contracts
from agentic.continuous_controller import ContinuousControllerStore, overlap_allowance_command, production_controller_cycle, production_jira_lifecycle, production_merge_observed, validate_replan_trigger_observation
from agentic.lifecycle import definition
from agentic.policy import policy_hash
NOW = '2026-10-09T08:00:00Z'

def _ticket(path='tracked.txt'):
	return {'ticket': 'AWF-32', 'priority': 1, 'disposition': 'ELIGIBLE', 'actor': 'controller', 'reason': 'eligible', 'next_action': 'continue', 'resume_trigger': 'completion', 'paths': [path], 'dependencies_satisfied': True, 'budget_available': True, 'cap_available': True, 'review_independent': True, 'exact_tuple': 'base:a/head:b/tree:c/contract:d/review:e', 'activity': 'implement', 'verification_gate': 'PENDING', 'reviewer_completion': {'required': 1, 'completed': 0, 'acceptable': 0, 'failed': 0, 'stale': 0, 'outstanding': 1}, 'open_findings': 0, 'jira_status': 'Ready'}

def _issue(value=None, *, number=32, stage='implementation', body=None):
	value = _ticket() if value is None else value
	return {'id': 10000 + number, 'number': number, 'node_id': 'issue-node-' + str(number), 'state': 'open', 'labels': [{'name': 'awf-controller'}, {'name': 'awf-stage:' + stage}], 'body': json.dumps(value) if body is None else body}

def _dispatch_payload(name='isolation', role='writer'):
	return {'dispatch_schema_version': 2, 'dispatch_id': name, 'stream': 'A', 'ticket': 'AWF-51', 'exact_tuple': 'tuple', 'dispatch_nonce': name + '-nonce', 'prepared_at': NOW, 'begun_at': NOW, 'paths': ['fixture.txt'], 'actor': role, 'next_action': 'verify isolation', 'role': role, 'lifecycle_stage': 'implementation' if role == 'writer' else 'independent_review', 'lane_start_waivers': []}

class FakeClock:

	def __init__(self, value=NOW):
		self.value = value
		self.ticks = 0

	def now(self):
		return self.value

	def monotonic(self):
		self.ticks += 1
		return self.ticks

class AdvancingClock(FakeClock):

	def now(self):
		value = self.value
		self.value = '2026-10-09T08:00:01Z' if value == NOW else NOW
		return value

class FakeRunner:

	def __init__(self, *, inventory=None, partial=False, bad_repo=False, exit_code=0, error=None):
		raw_inventory = inventory or [_ticket()]
		self.inventory = [item if isinstance(item, dict) and 'node_id' in item else _issue(item, number=32 + index) for index, item in enumerate(raw_inventory)]
		self.partial = partial
		self.bad_repo = bad_repo
		self.exit_code = exit_code
		self.error = error
		self.calls = []
		self.launches = []
		self.prompts = []
		self.observations = []

	def run(self, argv, *, cwd, env, timeout, max_bytes):
		self.calls.append((list(argv), Path(cwd), dict(env)))
		if argv and argv[0] == 'git':
			result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=True)
			return result.stdout
		if self.error:
			raise RuntimeError(self.error)
		if self.exit_code:
			return {'returncode': self.exit_code, 'stdout': '{}'}
		path = argv[-1]
		if 'issues?' in path:
			if self.partial:
				return {'items': self.inventory, 'complete': False, 'next_page': 2}
			return {'items': self.inventory, 'complete': True, 'next_page': None}
		if path == 'repos/example/project':
			return {'id': 999 if self.bad_repo else 101, 'full_name': 'example/project'}
		if path == 'user':
			return {'id': 1001, 'login': 'smoke-user'}
		if path == 'hosts':
			return {'hosts': {'github.com': [{'active': True, 'profile': 'smoke'}]}}
		if 'rulesets' in path:
			return [[{'id': 1, 'enforcement': 'active', 'conditions': {'ref_name': {'include': ['~ALL'], 'exclude': []}}}]]
		if '/collaborators/' in path:
			return {'permission': 'push'}
		raise AssertionError('unexpected gh path: ' + path)

	def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path, launch_nonce):
		self.launches.append((list(argv), Path(cwd), dict(env)))
		self.prompts.append(stdin)
		Path(handoff_path).write_text(json.dumps({'pid': 1234, 'launch_nonce': launch_nonce, 'status': 'LAUNCHED'}), encoding='utf-8')
		terminal_path = Path(terminal_proof_path)
		startup_path = terminal_path.with_name(terminal_path.name.removesuffix('.terminal.json') + '.startup.json')
		return {'pid': 1234, 'launch_nonce': launch_nonce, 'handoff_path': str(handoff_path), 'startup_proof_path': str(startup_path), 'terminal_proof_path': str(terminal_proof_path), 'status': 'LAUNCHED'}

	def observe(self, record, *, timeout):
		self.observations.append(dict(record))
		return {'status': 'COMPLETED', 'returncode': 0, 'terminal': True, 'pid': record['pid'], 'launch_nonce': record['launch_nonce'], 'session_id': 'session-' + record['dispatch_id']}

	def observe_startup(self, record, *, timeout):
		return self.observe(record, timeout=timeout)

class FakeHttp:

	def __init__(self, *, mismatch=False):
		self.calls = []
		self.issue_reads = 0
		self.mismatch = mismatch

	def request(self, method, url, *, headers, body, timeout, max_bytes):
		self.calls.append((method, url, dict(headers), body))
		if self.mismatch:
			return (200, json.dumps({'accountId': 'foreign'}))
		if url.endswith('/rest/api/3/myself'):
			return (200, json.dumps({'accountId': 'controller'}))
		if url.endswith('/_edge/tenant_info'):
			return (200, json.dumps({'cloudId': 'cloud-1', 'siteUrl': 'https://jira.example.invalid'}))
		if '/rest/api/3/project/EX' in url:
			return (200, json.dumps({'id': 'project-1', 'key': 'EX'}))
		if '/rest/api/3/search?' in url:
			if 'startAt=0' in url:
				issue = {'id': '100', 'fields': {'issuetype': {'name': 'Task'}, 'status': {'statusCategory': {'key': 'done'}}}}
				return (200, json.dumps({'issues': [issue], 'total': 2}))
			issue = {'id': '101', 'fields': {'issuetype': {'name': 'Task'}, 'status': {'statusCategory': {'key': 'new'}}}}
			return (200, json.dumps({'issues': [issue], 'total': 2}))
		if url.endswith('?fields=key'):
			return (200, json.dumps({'id': '100', 'key': 'EX-42'}))
		if '/rest/api/3/issue/' in url and method == 'GET':
			self.issue_reads += 1
			if 'expand=changelog' in url:
				status = 'Done' if '/issue/100?' in url else 'In Progress'
				return (200, json.dumps({'id': '100', 'fields': {'status': {'id': status}}, 'changelog': {'histories': [{'created': NOW, 'author': {'accountId': 'controller'}, 'items': [{'field': 'status', 'to': status, 'toString': status}]}]}}))
			status = 'Ready' if self.issue_reads == 1 else 'In Progress'
			return (200, json.dumps({'id': '100', 'fields': {'status': {'id': status}}}))
		if method == 'POST' and '/transitions' in url:
			self.transition_bodies = getattr(self, 'transition_bodies', [])
			self.transition_bodies.append(json.loads(body))
			self.asserted_transition_id = self.transition_bodies[-1]['transition']['id']
			return (204, '')
		raise AssertionError('unexpected Jira request: ' + method + ' ' + url)

class ForeignActorHttp(FakeHttp):

	def request(self, method, url, **kwargs):
		status, body = super().request(method, url, **kwargs)
		if 'expand=changelog' in url:
			value = json.loads(body)
			value['changelog']['histories'][0]['author']['accountId'] = 'foreign-actor'
			body = json.dumps(value)
		return (status, body)

class AdvancingPageHttp(FakeHttp):

	def request(self, method, url, **kwargs):
		status, body = super().request(method, url, **kwargs)
		if '/rest/api/3/search?' in url:
			value = json.loads(body)
			value['total'] = 2 if 'startAt=0' in url else 3
			body = json.dumps(value)
		return (status, body)

def _base_config(root):
	codex = root / 'codex.exe'
	gh = root / 'gh.exe'
	codex.write_bytes(b'codex fixture')
	gh.write_bytes(b'gh fixture')
	worktree = root / 'worktree'
	worktree.mkdir()
	isolated_home = root / 'isolated-codex-home'
	isolated_home.mkdir(mode=448)
	auth = isolated_home / 'auth.json'
	auth.write_text('{"fixture":"auth-material"}\n', encoding='utf-8')
	if os.name != 'nt':
		auth.chmod(384)
	return {'codex': {'executable': {'path': str(codex), 'sha256': sha256(codex.read_bytes())}, 'roles': {'writer': {'model': 'writer-model', 'reasoning_effort': 'high', 'sandbox': 'workspace-write'}, 'critic': {'model': 'critic-model', 'reasoning_effort': 'medium', 'sandbox': 'read-only'}}, 'isolated_home': str(isolated_home), 'windows_sandbox': 'elevated', 'worktree_root': str(worktree), 'run_record_directory': str(root / 'runs'), 'timeout_seconds': 20}, 'github': {'host': 'https://github.com', 'repository': 'example/project', 'repository_id': 101, 'project_id': 'project-1', 'scope_sha256': 'a' * 64, 'base_branch': 'main', 'branch_pattern': 'codex/{ticket}-{slug}', 'expected_actor_id': 1001, 'auth_profile': 'smoke', 'executable': {'path': str(gh), 'sha256': sha256(gh.read_bytes())}, 'page_size': 100}, 'jira': {'enabled': True, 'cloud_id': 'cloud-1', 'site': 'https://jira.example.invalid', 'provider_project_id': 'project-1', 'project_key': 'EX', 'controller_actor_id': 'controller', 'token_env': 'AWF_JIRA_TOKEN', 'merged_status_id': 'Done', 'merged_transition_id': 'done-transition', 'page_size': 1}, 'outbox': {'directory': str(root / 'outbox')}}

def _overlap_run(case, ns, heads, base_sha, contents, *, merged=None, real=False, binary=False):
	original = list(heads)
	seen = {}
	tree_refs = {}
	blob_contents = {}
	original_run = ns['_SubprocessRunner'].run

	def response(body, etag):
		return {'headers': {'ETag': etag, 'Date': 'Fri, 09 Oct 2026 08:00:00 GMT'}, 'body': body}

	def run(runner, argv, *, cwd, env, timeout, max_bytes, stdin=None):
		path = argv[-1]
		match = __import__('re').search('/pulls/(11|12)$', path)
		if match:
			number = int(match.group(1)); index = number - 11; ticket = 'AWF-' + str(32 + index)
			return response({'number': number, 'state': 'open', 'merged': False, 'head': {'sha': heads[index], 'ref': 'codex/' + ticket + '-' + ticket.lower(), 'repo': {'id': 101}}, 'base': {'repo': {'id': 101}}}, '"pr-%s-%s"' % (number, heads[index]))
		if path == 'repos/example/project/compare/' + original[1] + '...' + original[0]:
			return response({'merge_base_commit': {'sha': base_sha}}, '"merge-base"')
		if '/git/commits/' in path:
			ref = path.rsplit('/', 1)[1]
			tree_sha = format((int(ref[0], 16) + 4) % 16, 'x') * 40
			tree_refs[tree_sha] = (ref, 'root')
			return response({'sha': ref, 'tree': {'sha': tree_sha}}, '"commit-' + ref[0] + '"')
		if '/git/trees/' in path:
			tree_sha = path.rsplit('/', 1)[1]
			ref, level = tree_refs[tree_sha]
			entries = []
			if level == 'root':
				subtree_sha = format((int(ref[0], 16) + 12) % 16, 'x') * 40
				tree_refs[subtree_sha] = (ref, 'src')
				entries.append({'path': 'src', 'mode': '040000', 'type': 'tree', 'sha': subtree_sha})
			elif ref in contents:
				blob_sha = format((int(ref[0], 16) + 8) % 16, 'x') * 40
				blob_contents[blob_sha] = contents[ref]
				entries.append({'path': 'shared.py', 'mode': '100644', 'type': 'blob', 'sha': blob_sha})
			return response({'sha': tree_sha, 'truncated': False, 'tree': entries}, '"tree-' + ref[0] + '-' + level + '"')
		if '/git/blobs/' in path:
			blob_sha = path.rsplit('/', 1)[1]
			return response({'sha': blob_sha, 'encoding': 'base64', 'content': base64.b64encode(blob_contents[blob_sha]).decode('ascii')}, '"blob-' + blob_sha[0] + '"')
		if '/contents/src/shared.py?ref=' in path:
			ref = path.rsplit('=', 1)[1]
			if ref not in contents:
				return response({'message': 'Not Found'}, '"missing-' + ref[0] + '"')
			return response({'type': 'file', 'sha': {'0': 'a', '1': 'b', '2': 'c'}[ref[0]] * 40, 'encoding': 'base64', 'content': base64.b64encode(contents[ref]).decode('ascii')}, '"blob-' + ref[0] + '"')
		if 'graphql' in argv:
			case.assertEqual(['--input', '-'], argv[-2:]); case.assertIsNotNone(stdin)
			request = json.loads(stdin); fields = request['variables']
			case.assertEqual(('example/project', 'codex/AWF-33-awf-33', original[1]), (fields['repository'], fields['branch'], fields['head']))
			if 'contents' in fields:
				resolved = base64.b64decode(fields['contents'], validate=True)
				if all((ref in contents for ref in (base_sha, *original))) and not binary:
					case.assertIn(b'value = 1', resolved); case.assertIn(b'value = 2', resolved)
				seen.update(action='addition', resolved=resolved)
			else:
				case.assertIn('deletions:', request['query'])
				resolved = None
				seen.update(action='deletion', resolved=None)
			seen.update(argv=list(argv), stdin=stdin)
			if real:
				helper = 'import json,sys; body=json.load(sys.stdin); assert len(body["variables"]["contents"])>1000000; json.dump({"data":{"createCommitOnBranch":{"commit":{"oid":"' + merged + '"}}}},sys.stdout)'
				result = original_run(runner, [sys.executable, '-B', '-c', helper, *argv[1:]], cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes, stdin=stdin)
			else:
				result = {'data': {'createCommitOnBranch': {'commit': {'oid': merged}}}}
			if resolved is None:
				contents.pop(merged, None)
			else:
				contents[merged] = resolved
			heads[1] = merged
			return result
		if merged and path == 'repos/example/project/compare/' + original[1] + '...' + merged:
			if binary:
				return {'files': [{'filename': 'src/shared.py', 'status': 'modified', 'sha': '9' * 40}]}
			patch_text = '@@ -1 +0,0 @@\n-old' if seen.get('action') == 'deletion' else '@@ -1 +1 @@\n-old\n+resolved'
			return {'files': [{'filename': 'src/shared.py', 'patch': patch_text}]}
		raise AssertionError('unexpected default-runner path: ' + path)
	return run, seen

def _overlap_gate(heads):
	return {'gate_id': 'g' * 64, 'repository_id': '101', 'cycle_id': 'cycle-1', 'path': 'src/shared.py', 'tickets': ['AWF-32', 'AWF-33'], 'pull_requests': [11, 12], 'heads': list(heads)}

def _overlap_payload(gate):
	return {**gate, 'execution_id': 'e' * 64, 'conflict_sha256': 'c' * 64}

def _load_real(path, config_path):
	import importlib.util
	spec = importlib.util.spec_from_file_location('test_controller_loader', ROOT / '.agentic/scripts/continuous_controller.py')
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return (module._load_reviewed_adapters(path, sha256(path.read_bytes()), config_path), module)

class ReferenceControllerAdapterTests(unittest.TestCase):

	def setUp(self):
		self.temp = tempfile.TemporaryDirectory(prefix='awf32-adapter-')
		self.root = Path(self.temp.name)
		self.config = _base_config(self.root)
		self.adapter = ROOT / '.agentic/adapters/reference_controller_adapter.py'

	def tearDown(self):
		self.temp.cleanup()

	def test_legacy_adapter_payload_is_refused_before_runner_launch(self):
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		legacy = dict(_dispatch_payload('legacy-call'))
		legacy.pop('dispatch_schema_version')
		legacy.pop('lane_start_waivers')
		with self.assertRaisesRegex(ValidationError, 'legacy|version'):
			adapters['dispatch_ticket'](legacy)
		with self.assertRaisesRegex(ValidationError, 'legacy|version'):
			adapters['observe_dispatch']({**legacy, 'reconcile_nonce': 'r', 'reconcile_at': NOW})
		self.assertEqual(runner.launches, [])

	def test_launched_state_survives_restart_as_nonterminal(self):
		class RunningRunner(FakeRunner):
			def observe_startup(self, record, *, timeout):
				return {'status': 'RUNNING', 'terminal': False, 'pid': record['pid'], 'launch_nonce': record['launch_nonce'], 'session_id': 'session-' + record['dispatch_id']}
		runner = RunningRunner()
		payload = _dispatch_payload('durable-launched')
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		receipt = adapters['dispatch_ticket'](payload)
		self.assertEqual((receipt['dispatch_state'], receipt['terminal']), ('LAUNCHED', False))
		restarted = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': RunningRunner(), '_clock': FakeClock()})
		observed = restarted['observe_dispatch'](payload)
		self.assertEqual((observed['dispatch_state'], observed['terminal']), ('LAUNCHED', False))

	def test_replan_trigger_requires_trusted_immutable_provider_artifact(self):
		prior='1'*64
		nonterminal=['dispatch-1']

		class TriggerRunner(FakeRunner):
			def __init__(self,*,actor_id=7001,updated_at='2026-10-09T07:59:00Z',comment_body=''):
				super().__init__()
				self.actor_id=actor_id;self.updated_at=updated_at;self.comment_body=comment_body;self.comment_reads=0
			def run(self,argv,*,cwd,env,timeout,max_bytes):
				path=argv[-1]
				if '/issues/comments/9002' in path:
					self.comment_reads+=1
					return {'headers':{'ETag':'"replan"','Date':'Fri, 09 Oct 2026 08:00:00 GMT'},'body':{'id':9002,'node_id':'comment-node-9002','user':{'id':self.actor_id,'login':'owner'},'author_association':'OWNER','created_at':'2026-10-09T07:59:00Z','updated_at':self.updated_at,'body':self.comment_body}}
				return super().run(argv,cwd=cwd,env=env,timeout=timeout,max_bytes=max_bytes)

		config={**self.config,'merge_gate':{'trusted_owner_ids':[7001]}}
		runner=TriggerRunner()
		adapters=__import__('runpy').run_path(str(self.adapter))['build_adapters']({**config,'_runner':runner,'_clock':FakeClock()})
		next_inventory=adapters['observe_inventory']()['inventory_sha256']
		event={'repository_id':'101','round_id':'round-2','prior_inventory_sha256':prior,'next_inventory_sha256':next_inventory,'nonterminal_dispatch_ids':nonterminal}
		body='AWF-ROUND-REPLAN 1\n'+canonical(event).decode('utf-8')+'\n'
		runner.comment_body=body
		request={'comment_id':9002,'repository_id':'101','prior_inventory_sha256':prior,'next_inventory_sha256':next_inventory,'nonterminal_dispatch_ids':nonterminal}
		raw=adapters['observe_replan_trigger'](request)
		trigger=validate_replan_trigger_observation(config,request,raw,now=NOW)
		self.assertEqual((trigger['source'],trigger['round_id'],trigger['provider_artifact']['comment_id']),('github.com','round-2',9002))
		self.assertEqual(runner.comment_reads,1)
		wrong_context='AWF-ROUND-REPLAN 1\n'+canonical({**event,'next_inventory_sha256':'3'*64}).decode('utf-8')+'\n'
		for bad_runner in (TriggerRunner(actor_id=7999,comment_body=body),TriggerRunner(updated_at=NOW,comment_body=body),TriggerRunner(comment_body=wrong_context)):
			with self.subTest(actor_id=bad_runner.actor_id,updated_at=bad_runner.updated_at,body=bad_runner.comment_body),self.assertRaises(ValidationError):
				bad=__import__('runpy').run_path(str(self.adapter))['build_adapters']({**config,'_runner':bad_runner,'_clock':FakeClock()})
				bad['observe_inventory']()
				raw=bad['observe_replan_trigger'](request)
				validate_replan_trigger_observation(config,request,raw,now=NOW)

	def test_loader_executes_real_module_and_wrong_pin_refuses(self):
		config_path = self.root / 'config.json'
		config_path.write_text(json.dumps(self.config), encoding='utf-8')
		adapters, loader = _load_real(self.adapter, config_path)
		self.assertEqual(set(adapters), {'observe_issue_identity', 'observe_inventory', 'observe_reservations', 'observe_replan_trigger', 'dispatch_ticket', 'observe_dispatch', 'observe_dispatch_quiescence', 'deliver_status', 'observe_publication', 'observe_provider_identity', 'read_current_status', 'write_transition', 'read_transition', 'reconcile_merged_ticket', 'fetch_scope_page', 'observe_accepted_project_config', 'observe_closeout_record', 'observe_jira_reconciliation', 'observe_overlap_allowance', 'observe_overlap_conflict', 'observe_overlap_sync_inventory', 'sync_writer', 'reconcile_overlap_sync', 'observe_host_resources', 'verify_host_observation', 'observe_planning_backlog', 'verify_planning_backlog', 'observe_planning_events', 'verify_planning_events', 'verify_planning_owner'})
		loader._require_adapters(adapters, {'observe_provider_identity', 'observe_issue_identity', 'read_current_status', 'write_transition', 'read_transition'})
		with self.assertRaisesRegex(ValidationError, 'SHA-256 pin'):
			loader._load_reviewed_adapters(self.adapter, '0' * 64, config_path)

	def test_overlap_observer_derives_comment_pr_heads_files_and_freshness(self):
		request = {'repository_id': '101', 'cycle_id': 'cycle-1', 'comment_id': 9001, 'path': 'src/shared.py', 'expires_at': '2026-10-09T08:30:00Z', 'pull_requests': [{'number': 11, 'ticket': 'AWF-32', 'head_sha': '1' * 40, 'changed_files': ['src/left.py', 'src/shared.py']}, {'number': 12, 'ticket': 'AWF-33', 'head_sha': '2' * 40, 'changed_files': ['src/right.py', 'src/shared.py']}]}

		class HeaderRunner(FakeRunner):

			def response(self, body, etag):
				return {'headers': {'ETag': etag, 'Date': 'Fri, 09 Oct 2026 08:00:00 GMT'}, 'body': body}

			def run(self, argv, *, cwd, env, timeout, max_bytes):
				path = argv[-1]
				if '/issues/comments/9001' in path:
					return self.response({'id': 9001, 'node_id': 'comment-node-9001', 'user': {'id': 7001, 'login': 'owner'}, 'author_association': 'OWNER', 'created_at': '2026-10-09T07:55:00Z', 'updated_at': '2026-10-09T07:55:00Z', 'body': overlap_allowance_command(request)}, '"comment"')
				match = __import__('re').search('/pulls/(11|12)$', path)
				if match:
					number = int(match.group(1))
					item = request['pull_requests'][number - 11]
					slug = item['ticket'].lower()
					return self.response({'number': number, 'state': 'open', 'merged': False, 'changed_files': 2, 'head': {'sha': item['head_sha'], 'ref': 'codex/' + item['ticket'] + '-' + slug, 'repo': {'id': 101}}, 'base': {'repo': {'id': 101}}}, '"pr-%s"' % number)
				match = __import__('re').search('/pulls/(11|12)/files', path)
				if match:
					number = int(match.group(1))
					files = request['pull_requests'][number - 11]['changed_files']
					return self.response([{'filename': name} for name in files], '"files-%s"' % number)
				return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)

			def observe_overlap_conflict(self, gate):
				return {'gate': gate, 'status': 'observed'}

			def sync_writer(self, payload):
				return {'payload': payload, 'status': 'executed'}
		runner = HeaderRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		observed = adapters['observe_overlap_allowance'](request, 'PLAN')
		self.assertEqual((101, 7001, 'comment-node-9001'), (observed['repository_id'], observed['comment']['actor_id'], observed['comment']['node_id']))
		self.assertEqual(['1' * 40, '2' * 40], [item['head_sha'] for item in observed['pull_requests']])
		self.assertEqual(request['pull_requests'][0]['changed_files'], observed['pull_requests'][0]['changed_files'])
		self.assertTrue(observed['observation_etag'])
		self.assertEqual('observed', adapters['observe_overlap_conflict']({'gate_id': 'g'})['status'])
		self.assertEqual('executed', adapters['sync_writer']({'execution_id': 'e'})['status'])

	def test_default_runner_exposes_production_overlap_operations(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		runner = namespace['_SubprocessRunner']()
		self.assertTrue(callable(getattr(runner, 'observe_overlap_conflict', None)))
		self.assertTrue(callable(getattr(runner, 'sync_writer', None)))

	def test_default_runner_treats_mergeable_different_blobs_as_no_conflict(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		contents = {base_sha: b'one\ntwo\nthree\nfour\nfive\n', heads[0]: b'ONE\ntwo\nthree\nfour\nfive\n', heads[1]: b'one\ntwo\nthree\nfour\nFIVE\n'}
		run, _mutation = _overlap_run(self, namespace, heads, base_sha, contents)
		gate = _overlap_gate(heads)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			decision = adapters['observe_overlap_conflict'](gate)
		self.assertEqual('NO_CONFLICT', decision['status'])

	def test_default_runner_resolves_add_add_conflict_to_left_addition(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		merge_sha = '3' * 40
		contents = {heads[0]: b'left addition\n', heads[1]: b'right addition\n'}
		run, mutation = _overlap_run(self, namespace, heads, base_sha, contents, merged=merge_sha)
		gate = _overlap_gate(heads)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			self.assertEqual('CONFIRMED_CONFLICT', adapters['observe_overlap_conflict'](gate)['status'])
			receipt = adapters['sync_writer'](_overlap_payload(gate))
		self.assertEqual('addition', mutation['action'])
		self.assertEqual(b'left addition\n', mutation['resolved'])
		self.assertEqual(['1' * 40, merge_sha], receipt['post_sync_heads'])

	def test_default_runner_resolves_modify_delete_conflict_with_deletion(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		merge_sha = '3' * 40
		contents = {base_sha: b'value = 0\n', heads[1]: b'value = 2\n'}
		run, mutation = _overlap_run(self, namespace, heads, base_sha, contents, merged=merge_sha)
		gate = _overlap_gate(heads)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			self.assertEqual('CONFIRMED_CONFLICT', adapters['observe_overlap_conflict'](gate)['status'])
			receipt = adapters['sync_writer'](_overlap_payload(gate))
		self.assertEqual('deletion', mutation['action'])
		self.assertIsNone(mutation['resolved'])
		self.assertIn('-old', receipt['diff'])

	def test_default_runner_resolves_delete_modify_conflict_with_addition(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		merge_sha = '3' * 40
		contents = {base_sha: b'value = 0\n', heads[0]: b'value = 1\n'}
		run, mutation = _overlap_run(self, namespace, heads, base_sha, contents, merged=merge_sha)
		gate = _overlap_gate(heads)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			self.assertEqual('CONFIRMED_CONFLICT', adapters['observe_overlap_conflict'](gate)['status'])
			receipt = adapters['sync_writer'](_overlap_payload(gate))
		self.assertEqual('addition', mutation['action'])
		self.assertEqual(b'value = 1\n', mutation['resolved'])
		self.assertEqual(['1' * 40, merge_sha], receipt['post_sync_heads'])

	def _exercise_true_conflict(self, *, near_limit):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		merge_sha = '3' * 40
		maximum = namespace['MAX_OUTPUT']
		tail = b'unchanged\n' * ((maximum - 4096) // len(b'unchanged\n')) if near_limit else b''
		contents = {base_sha: b'value = 0\n' + tail, heads[0]: b'value = 1\n' + tail, heads[1]: b'value = 2\n' + tail}
		run, mutation = _overlap_run(self, namespace, heads, base_sha, contents, merged=merge_sha, real=near_limit)
		gate = _overlap_gate(heads)
		payload = _overlap_payload(gate)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			self.assertEqual('CONFIRMED_CONFLICT', adapters['observe_overlap_conflict'](gate)['status'])
			receipt = adapters['sync_writer'](payload)
			replay = adapters['sync_writer'](payload)
			reconciled = adapters['reconcile_overlap_sync'](payload)
			provider_heads = adapters['observe_overlap_sync_inventory'](gate)
		self.assertEqual(['1' * 40, merge_sha], receipt['post_sync_heads'])
		self.assertEqual(receipt, replay); self.assertEqual(receipt, reconciled)
		self.assertEqual(receipt['post_sync_heads'], provider_heads['heads'])
		self.assertIn('src/shared.py', receipt['changed_files']); self.assertIn('+resolved', receipt['diff'])
		if near_limit:
			self.assertGreater(len(contents[base_sha]), maximum - 8192)
			self.assertIsNotNone(mutation['stdin'])
			self.assertLess(max(map(len, mutation['argv'])), 32768)
			self.assertFalse(any('contents=' in argument for argument in mutation['argv']))
			self.assertGreater(len(mutation['resolved']), maximum - 8192)
		else:
			self.assertIsNotNone(mutation['argv'])

	def test_default_runner_resolves_true_conflict_and_replays_critic_diff(self):
		self._exercise_true_conflict(near_limit=False)

	def test_default_runner_resolves_binary_true_conflict_with_head_bound_receipt(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		heads = ['1' * 40, '2' * 40]
		base_sha = '0' * 40
		merge_sha = '3' * 40
		contents = {
			base_sha: b'\x00base-version',
			heads[0]: b'\x00left-version',
			heads[1]: b'\x00right-version',
		}
		run, mutation = _overlap_run(
			self, namespace, heads, base_sha, contents, merged=merge_sha, binary=True)
		gate = _overlap_gate(heads)
		with patch.object(namespace['_SubprocessRunner'], 'run', run):
			adapters = namespace['build_adapters']({**self.config, '_clock': FakeClock()})
			self.assertEqual('CONFIRMED_CONFLICT', adapters['observe_overlap_conflict'](gate)['status'])
			receipt = adapters['sync_writer'](_overlap_payload(gate))
		self.assertEqual(b'\x00left-version', mutation['resolved'])
		self.assertEqual(['1' * 40, merge_sha], receipt['post_sync_heads'])
		self.assertIn('Binary overlap resolution evidence', receipt['diff'])
		self.assertIn('left-head=' + '1' * 40, receipt['diff'])
		self.assertIn('right-head=' + '2' * 40, receipt['diff'])
		self.assertIn(hashlib.sha256(b'\x00left-version').hexdigest(), receipt['diff'])
		self.assertIn(hashlib.sha256(b'\x00right-version').hexdigest(), receipt['diff'])

	def test_true_conflict_near_limit_uses_stdin_not_process_argv(self):
		self._exercise_true_conflict(near_limit=True)

	def test_cycle_runs_end_to_end_through_real_adapter_and_strips_credentials(self):
		repository = self.root / 'repository'
		repository.mkdir()
		(repository / 'tracked.txt').write_bytes(b'fixture\n')
		subprocess.run(['git', 'init', '-q', str(repository)], check=True)
		subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'fixture@example.invalid'], check=True)
		subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Fixture'], check=True)
		subprocess.run(['git', '-C', str(repository), 'add', 'tracked.txt'], check=True)
		subprocess.run(['git', '-C', str(repository), 'commit', '-qm', 'fixture'], check=True)
		runner = FakeRunner(inventory=[_ticket('tracked.txt')])
		clock = FakeClock()
		cfg = {**self.config, '_runner': runner, '_clock': clock}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		inventory_binding = {'project_id': 'project-1', 'repository_id': '101', 'scope_sha256': 'a' * 64}
		head = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
		tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
		state = ContinuousControllerStore(self.root / 'state.sqlite3', ['A'], worktree_roots=[self.root / 'worktree'])
		planning_inputs = {**planning_resource_inputs(1), **planning_fixture_inputs([_ticket('tracked.txt')], state, 1, binding=inventory_binding, now=NOW)}
		with patch.dict(os.environ, {'GH_TOKEN': 'secret-gh', 'GITHUB_TOKEN': 'secret-github', 'AWF_JIRA_TOKEN': 'secret-jira', 'ATLASSIAN_API_TOKEN': 'secret-atlassian', 'JIRA_API_KEY': 'secret-jira-key', 'EXAMPLE_CLIENT_SECRET': 'secret-generic'}, clear=False):
			result = production_controller_cycle(state, controller_config={'dispatch_enabled': True, 'auto_dispatch': True, 'auto_request_critic': True, 'auto_resume_amendments': True}, now=NOW, host_capacity=1, **planning_inputs, inventory_binding=inventory_binding, repository_root=repository, repository_head_sha=head, repository_tree_sha=tree, publication_config=cfg, **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations', 'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
		self.assertEqual(result['status_delivery']['status'], 'DELIVERED')
		self.assertEqual(len(result['dispatch_receipts']), 1, result)
		self.assertTrue(runner.launches)
		self.assertEqual(len(runner.observations), 1)
		self.assertEqual(runner.observations[0]['status'], 'LAUNCHED')
		child_env = runner.launches[0][2]
		for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'AWF_JIRA_TOKEN', 'ATLASSIAN_API_TOKEN', 'JIRA_API_KEY', 'EXAMPLE_CLIENT_SECRET'):
			self.assertNotIn(name, child_env.keys())
		self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', runner.launches[0][0])
		self.assertIn('Ticket: AWF-32', runner.prompts[0])
		outbox = self.root / 'outbox' / 'controller-status.jsonl'
		self.assertEqual(len(outbox.read_text(encoding='utf-8').splitlines()), 1)
		self.assertEqual(json.loads(outbox.read_text(encoding='utf-8').splitlines()[0])['delivery_id'], result['status_delivery']['delivery_id'])

	def test_cycle_dispatches_second_stream_before_first_terminal_completion(self):

		class ConcurrentRunner(FakeRunner):

			def observe_startup(self, record, *, timeout):
				self.observations.append(dict(record))
				return {'status': 'RUNNING', 'terminal': False, 'pid': record['pid'], 'launch_nonce': record['launch_nonce'], 'session_id': 'session-' + record['dispatch_id']}

			def observe(self, record, *, timeout):
				raise AssertionError('terminal observation would serialize dispatch')
		repository = self.root / 'repository'
		repository.mkdir()
		for name in ('first.txt', 'second.txt'):
			(repository / name).write_bytes(b'fixture\n')
		subprocess.run(['git', 'init', '-q', str(repository)], check=True)
		subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'fixture@example.invalid'], check=True)
		subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Fixture'], check=True)
		subprocess.run(['git', '-C', str(repository), 'add', 'first.txt', 'second.txt'], check=True)
		subprocess.run(['git', '-C', str(repository), 'commit', '-qm', 'fixture'], check=True)
		first = _ticket('first.txt')
		second = {**_ticket('second.txt'), 'ticket': 'AWF-33'}
		runner = ConcurrentRunner(inventory=[first, second])
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		binding = {'project_id': 'project-1', 'repository_id': '101', 'scope_sha256': 'a' * 64}
		head = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
		tree = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
		state = ContinuousControllerStore(self.root / 'state.sqlite3', ['A', 'B'], worktree_roots=[self.root / 'worktree'])
		planning_inputs = {**planning_resource_inputs(2), **planning_fixture_inputs([first, second], state, 2, binding=binding, now=NOW)}
		result = production_controller_cycle(state, controller_config={'dispatch_enabled': True, 'auto_dispatch': True, 'auto_request_critic': True, 'auto_resume_amendments': True}, now=NOW, host_capacity=2, **planning_inputs, inventory_binding=binding, repository_root=repository, repository_head_sha=head, repository_tree_sha=tree, publication_config=self.config, **{name: adapters[name] for name in ('observe_inventory', 'observe_reservations', 'dispatch_ticket', 'observe_dispatch', 'deliver_status', 'observe_publication')})
		self.assertEqual(len(result['dispatch_receipts']), 2, result)
		self.assertEqual(len(runner.launches), 2)
		self.assertEqual(len(runner.observations), 2)

	def test_dispatch_reports_startup_sandbox_configuration_failure(self):

		class StartupFailureRunner(FakeRunner):

			def observe_startup(self, record, *, timeout):
				raise ValidationError('helper_unknown_error: setup refresh had errors; os error 32; example.invalid/user-app-file')
		runner = StartupFailureRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
			adapters['dispatch_ticket'](_dispatch_payload('startup-config-failure'))
		record = json.loads((self.root / 'runs' / 'startup-config-failure.json').read_text(encoding='utf-8'))
		self.assertEqual(record['diagnostic'], 'SANDBOX_SETUP_CONFIG_ERROR')
		self.assertEqual(record['status'], 'LAUNCHED')

	def test_dispatch_waits_past_old_window_for_startup_configuration_failure(self):

		class DelayedStartupFailureRunner(FakeRunner):

			def observe_startup(self, record, *, timeout):
				self.observations.append(dict(record))
				if timeout <= 1:
					return {'status': 'RUNNING', 'terminal': False}
				raise ValidationError('helper_unknown_error: setup refresh had errors; invalid transport; example.invalid/delayed-user-app-file')
		runner = DelayedStartupFailureRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
			adapters['dispatch_ticket'](_dispatch_payload('delayed-startup-config-failure'))
		record = json.loads((self.root / 'runs' / 'delayed-startup-config-failure.json').read_text(encoding='utf-8'))
		self.assertEqual(record['diagnostic'], 'SANDBOX_SETUP_CONFIG_ERROR')
		self.assertEqual(len(runner.observations), 1)

	def test_runner_reports_delayed_durable_startup_configuration_failure(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		startup_proof = self.root / 'delayed.startup.json'
		runner = namespace['_SubprocessRunner']()

		class RunningProcess:

			def poll(self):
				return None
		runner._processes[1234] = RunningProcess()
		record = {'pid': 1234, 'launch_nonce': 'delayed-nonce', 'startup_proof_path': str(startup_proof), 'terminal_proof_path': str(self.root / 'delayed.terminal.json')}

		def write_failure():
			startup_proof.write_text(json.dumps({'pid': 1234, 'launch_nonce': 'delayed-nonce', 'status': 'FAILED', 'terminal': False, 'diagnostic': 'SANDBOX_SETUP_CONFIG_ERROR', 'config_source': 'AWF isolated CODEX_HOME/config.toml'}), encoding='utf-8')
		timer = threading.Timer(0.05, write_failure)
		timer.start()
		try:
			with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
				runner.observe_startup(record, timeout=1)
		finally:
			timer.join()

	def test_inventory_uses_controller_time_and_critic_route_is_reachable(self):
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': AdvancingClock()})
		observation = adapters['observe_inventory'](NOW)
		self.assertEqual(observation['observed_at'], NOW)
		payload = {'dispatch_schema_version': 2, 'dispatch_id': 'critic-1', 'stream': 'A', 'ticket': 'AWF-32', 'exact_tuple': 'tuple', 'dispatch_nonce': 'critic-nonce', 'prepared_at': NOW, 'begun_at': NOW, 'paths': ['a.py'], 'actor': 'critic', 'next_action': 'review', 'role': 'critic', 'lifecycle_stage': 'independent_review', 'lane_start_waivers': [], 'overlap_sync_evidence': [{'gate_id': 'gate-1', 'post_sync_heads': ['3' * 40, '2' * 40], 'diff': 'diff --git a/a.py b/a.py\n+resolved\n', 'diff_sha256': hashlib.sha256(b'diff --git a/a.py b/a.py\n+resolved\n').hexdigest()}]}
		receipt = adapters['dispatch_ticket'](payload)
		self.assertEqual((receipt['role'], receipt['lifecycle_stage'], receipt['session_id']), ('critic', 'independent_review', 'session-critic-1'))
		argv = runner.launches[-1][0]
		self.assertEqual(argv[argv.index('--sandbox') + 1], 'read-only')
		self.assertIn('critic-model', argv)
		self.assertIn('Mandatory overlap-sync diff evidence', runner.prompts[-1])
		self.assertIn('+resolved', runner.prompts[-1])
		self.assertIn('3' * 40, runner.prompts[-1])
		incomplete = dict(payload)
		incomplete['dispatch_id'] = 'critic-incomplete'
		incomplete['dispatch_nonce'] = 'critic-incomplete-nonce'
		incomplete['overlap_sync_evidence'] = [{'gate_id': 'gate-1', 'diff_sha256': 'd' * 64}]
		with self.assertRaisesRegex(ValidationError, 'verified diff|post-sync heads'):
			adapters['dispatch_ticket'](incomplete)
		adapters['dispatch_ticket'](_dispatch_payload('probe-1', 'probe'))
		probe_argv = runner.launches[-1][0]
		self.assertEqual(probe_argv[probe_argv.index('--sandbox') + 1], 'read-only')
		self.assertIn('critic-model', probe_argv)
		self.assertIn('windows.sandbox="elevated"', probe_argv)

	def test_dispatch_rejects_reused_writer_critic_session_from_observed_startup(self):

		class ReusedSessionRunner(FakeRunner):

			def observe_startup(self, record, *, timeout):
				return {'status': 'RUNNING', 'terminal': False, 'pid': record['pid'], 'launch_nonce': record['launch_nonce'], 'session_id': 'reused-session'}
		runner = ReusedSessionRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		first = adapters['dispatch_ticket'](_dispatch_payload('fresh-writer', 'writer'))
		self.assertEqual(first['session_id'], 'reused-session')
		with self.assertRaisesRegex(ValidationError, 'fresh|session|independ'):
			adapters['dispatch_ticket'](_dispatch_payload('reused-critic', 'critic'))

	def test_dispatch_rejects_mismatched_observed_startup_identity(self):

		class MismatchedSessionRunner(FakeRunner):

			def observe_startup(self, record, *, timeout):
				return {'status': 'RUNNING', 'terminal': False, 'pid': record['pid'] + 1, 'launch_nonce': record['launch_nonce'], 'session_id': 'observed-session'}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': MismatchedSessionRunner(), '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'identity|process|startup'):
			adapters['dispatch_ticket'](_dispatch_payload('mismatched-startup'))

	def test_poisoned_user_config_is_not_loaded_by_writer_or_critic(self):
		user_home = self.root / 'user-home'
		user_codex = user_home / '.codex'
		user_codex.mkdir(parents=True)
		poison = 'notify = ["fixture-notify"]\n[mcp_servers.missing]\ncommand = "/missing/example.invalid/mcp-server"\n[plugins."fixture@example.invalid"]\nenabled = true\n'
		user_config = user_codex / 'config.toml'
		user_config.write_text(poison, encoding='utf-8')
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		ambient = {'HOME': str(user_home), 'USERPROFILE': str(user_home), 'CODEX_HOME': str(user_codex)}
		with patch.dict(os.environ, ambient, clear=False):
			adapters['dispatch_ticket'](_dispatch_payload('poison-writer'))
			adapters['dispatch_ticket'](_dispatch_payload('poison-critic', 'critic'))
		clean_home = self.root / 'second-clean-home'
		clean_home.mkdir()
		with patch.dict(os.environ, {'HOME': str(clean_home), 'USERPROFILE': str(clean_home), 'CODEX_HOME': str(clean_home / '.codex')}, clear=False):
			adapters['dispatch_ticket'](_dispatch_payload('clean-writer'))
		expected_home = str(Path(self.config['codex']['isolated_home']).resolve())
		self.assertEqual([launch[2]['CODEX_HOME'] for launch in runner.launches], [expected_home, expected_home, expected_home])
		self.assertEqual(runner.launches[0][0], runner.launches[2][0])
		for argv, _, _ in runner.launches:
			effective = ' '.join(argv)
			self.assertNotIn('fixture-notify', effective)
			self.assertNotIn('mcp_servers', effective)
			self.assertNotIn('plugins', effective)
			self.assertNotIn('missing', effective)
		self.assertEqual(user_config.read_text(encoding='utf-8'), poison)
		managed = Path(expected_home) / 'config.toml'
		managed_text = managed.read_text(encoding='utf-8')
		self.assertNotIn('notify', managed_text)
		self.assertNotIn('mcp_servers', managed_text)
		self.assertNotIn('plugins', managed_text)

	def test_clean_user_config_does_not_create_invalid_transport(self):
		user_home = self.root / 'clean-user-home'
		user_codex = user_home / '.codex'
		user_codex.mkdir(parents=True)
		(user_codex / 'config.toml').write_text('model = "user-model"\n', encoding='utf-8')
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with patch.dict(os.environ, {'CODEX_HOME': str(user_codex)}, clear=False):
			adapters['dispatch_ticket'](_dispatch_payload('clean-user'))
		argv, _, env = runner.launches[0]
		self.assertEqual(env['CODEX_HOME'], str(Path(self.config['codex']['isolated_home']).resolve()))
		self.assertFalse(any(('mcp_servers.' in item for item in argv)))
		self.assertIn('writer-model', argv)

	def test_auth_stays_in_isolated_home_and_never_enters_run_records(self):
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		adapters['dispatch_ticket'](_dispatch_payload('auth-isolation'))
		isolated_home = Path(self.config['codex']['isolated_home'])
		self.assertTrue((isolated_home / 'auth.json').is_file())
		self.assertEqual(runner.launches[0][2]['CODEX_HOME'], str(isolated_home.resolve()))
		for path in (self.root / 'runs').rglob('*'):
			if path.is_file():
				self.assertNotIn('auth-material', path.read_text(encoding='utf-8'))
		self.assertNotIn('auth-material', repr(runner.launches))
		self.assertNotIn('auth-material', repr(runner.prompts))

	def test_missing_auth_and_non_awf_isolated_config_fail_before_launch(self):
		runner = FakeRunner()
		isolated_home = Path(self.config['codex']['isolated_home'])
		(isolated_home / 'auth.json').unlink()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'auth is unavailable'):
			adapters['dispatch_ticket'](_dispatch_payload('missing-auth'))
		self.assertEqual(runner.launches, [])
		(isolated_home / 'auth.json').write_text('{"fixture":"restored"}\n', encoding='utf-8')
		if os.name != 'nt':
			(isolated_home / 'auth.json').chmod(384)
		(isolated_home / 'config.toml').write_text('[mcp_servers.fixture]\ncommand = "/missing/example.invalid/server"\n', encoding='utf-8')
		if os.name != 'nt':
			(isolated_home / 'config.toml').chmod(384)
		with self.assertRaisesRegex(ValidationError, 'contains non-AWF settings'):
			adapters['dispatch_ticket'](_dispatch_payload('foreign-config'))
		self.assertEqual(runner.launches, [])

	def test_windows_sandbox_setup_error_names_isolated_config_source(self):

		class LockedUserAppRunner(FakeRunner):

			def launch(self, *args, **kwargs):
				raise ValidationError('helper_unknown_error: setup refresh had errors; os error 32; AppData/Local/example.invalid/node_repl.exe')
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': LockedUserAppRunner(), '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
			adapters['dispatch_ticket'](_dispatch_payload('locked-user-app'))

	def test_detached_sandbox_diagnostic_names_isolated_config_source(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		proof = self.root / 'sandbox.terminal.json'
		proof.write_text(json.dumps({'pid': 1234, 'launch_nonce': 'nonce', 'status': 'COMPLETED', 'returncode': 1, 'terminal': True, 'diagnostic': 'SANDBOX_SETUP_CONFIG_ERROR'}), encoding='utf-8')
		record = {'pid': 1234, 'launch_nonce': 'nonce', 'terminal_proof_path': str(proof)}
		with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
			namespace['_SubprocessRunner']().observe(record, timeout=1)

	@unittest.skipUnless(os.name == 'nt', 'Windows venv process identity regression')
	def test_real_runner_windows_venv_reports_terminal_sandbox_diagnostic(self):
		script = Path(self.config['codex']['worktree_root']) / 'exec'
		script.write_text("import sys\nsys.stderr.buffer.write(b'x' * (1024 * 1024 + 4096))\nsys.stderr.write('helper_unknown_error: setup refresh had errors; winerror 32\\n')\nraise SystemExit(1)\n", encoding='utf-8')
		config = json.loads(json.dumps(self.config))
		executable = Path(sys.executable)
		config['codex']['executable'] = {'path': str(executable), 'sha256': sha256(executable.read_bytes())}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](config)
		with self.assertRaisesRegex(ValidationError, 'sandbox setup.*AWF isolated CODEX_HOME/config.toml'):
			adapters['dispatch_ticket'](_dispatch_payload('windows-venv-sandbox'))
		record = json.loads((self.root / 'runs' / 'windows-venv-sandbox.json').read_text(encoding='utf-8'))
		handoff = json.loads(Path(record['handoff_path']).read_text(encoding='utf-8'))
		proof = json.loads(Path(record['terminal_proof_path']).read_text(encoding='utf-8'))
		self.assertEqual(record['pid'], handoff['pid'])
		self.assertEqual(record['pid'], proof['pid'])
		self.assertEqual(proof['diagnostic'], 'SANDBOX_SETUP_CONFIG_ERROR')
		self.assertEqual(proof['config_source'], 'AWF isolated CODEX_HOME/config.toml')
		self.assertEqual(record['config_source'], 'AWF isolated CODEX_HOME/config.toml')
		launch_spec = json.loads(Path(record['handoff_path']).with_suffix('.launch.json').read_text(encoding='utf-8'))
		self.assertEqual(launch_spec['max_stderr_bytes'], 1024 * 1024)

	def test_prepared_launch_crash_is_observed_without_replay(self):

		class CrashAfterLaunch(FakeRunner):

			def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path, launch_nonce):
				super().launch(argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin, handoff_path=handoff_path, terminal_proof_path=terminal_proof_path, launch_nonce=launch_nonce)
				Path(terminal_proof_path).write_text(json.dumps({'pid': 1234, 'launch_nonce': launch_nonce, 'status': 'COMPLETED', 'returncode': 0, 'terminal': True, 'session_id': 'session-crash-1'}), encoding='utf-8')
				raise RuntimeError('simulated persistence interruption')
		runner = CrashAfterLaunch()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		payload = _dispatch_payload('crash-1')
		with self.assertRaises(ValidationError):
			adapters['dispatch_ticket'](payload)

		class RestartObserver(FakeRunner):

			def observe(self, record, *, timeout):
				self.observed_record = dict(record)
				return json.loads(Path(record['terminal_proof_path']).read_text(encoding='utf-8'))
		restarted = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': RestartObserver(), '_clock': FakeClock()})
		receipt = restarted['observe_dispatch']({**payload, 'reconcile_nonce': 'r', 'reconcile_at': NOW})
		self.assertEqual(receipt['status'], 'ACCEPTED')
		self.assertEqual(len(runner.launches), 1)

	def test_nonzero_or_unproven_detached_outcome_fails_closed(self):

		class BadOutcome(FakeRunner):

			def observe(self, record, *, timeout):
				return {'status': 'COMPLETED', 'returncode': 7, 'terminal': True}
		runner = BadOutcome()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		payload = _dispatch_payload('bad-1')
		with self.assertRaisesRegex(ValidationError, 'terminal'):
			adapters['dispatch_ticket'](payload)

	def test_restart_observer_reads_durable_terminal_proof_without_replay(self):
		runner = FakeRunner()
		cfg = {**self.config, '_runner': runner, '_clock': FakeClock()}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		payload = _dispatch_payload('restart-proof')
		adapters['dispatch_ticket'](payload)
		record_path = self.root / 'runs' / 'restart-proof.json'
		record = json.loads(record_path.read_text(encoding='utf-8'))
		Path(record['terminal_proof_path']).write_text(json.dumps({'pid': 1234, 'launch_nonce': record['launch_nonce'], 'status': 'COMPLETED', 'returncode': 0, 'terminal': True, 'session_id': record['session_id']}), encoding='utf-8')

		class RestartRunner(FakeRunner):

			def observe(self, record, *, timeout):
				proof = json.loads(Path(record['terminal_proof_path']).read_text(encoding='utf-8'))
				self.observed_record = dict(record)
				return proof
		restarted_runner = RestartRunner()
		restarted = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': restarted_runner, '_clock': FakeClock()})
		receipt = restarted['observe_dispatch']({**payload, 'reconcile_nonce': 'r2', 'reconcile_at': NOW})
		self.assertEqual(receipt['status'], 'ACCEPTED')
		self.assertEqual(len(runner.launches), 1)
		self.assertEqual(len(restarted_runner.launches), 0)

	def test_detached_runner_persists_nonce_identity_before_process_launch(self):
		namespace = __import__('runpy').run_path(str(self.adapter))
		runner_class = namespace['_SubprocessRunner']
		handoff = self.root / 'runner.handoff.json'
		proof = self.root / 'runner.terminal.json'
		observed = []
		watcher_argv = []

		class StdinProbe:

			def write(self, value):
				self.value = value

			def close(self):
				pass

		class Process:
			pid = 4321
			stdin = StdinProbe()

			def poll(self):
				return None

		def fake_popen(argv, **kwargs):
			observed.append(handoff.exists())
			watcher_argv.append(list(argv))
			return Process()
		with patch.dict(os.environ, {'ATLASSIAN_API_TOKEN': 'secret-atlassian', 'JIRA_API_KEY': 'secret-jira-key', 'EXAMPLE_CLIENT_SECRET': 'secret-generic'}, clear=False):
			child_env = namespace['_safe_env']('AWF_JIRA_TOKEN')
		with patch('subprocess.Popen', side_effect=fake_popen):
			result = runner_class().launch(['fake-bin/codex', 'exec'], cwd=self.root, env=child_env, timeout=5, stdin='task', handoff_path=str(handoff), terminal_proof_path=str(proof), launch_nonce='nonce')
		self.assertEqual(observed, [True])
		self.assertEqual(result['pid'], 4321)
		self.assertEqual(json.loads(handoff.read_text(encoding='utf-8')), {'launch_nonce': 'nonce', 'pid': 4321, 'status': 'LAUNCHED'})
		self.assertEqual(watcher_argv[0][0], getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable)
		launch_spec = json.loads(handoff.with_suffix('.launch.json').read_text(encoding='utf-8'))
		for name in ('ATLASSIAN_API_TOKEN', 'JIRA_API_KEY', 'EXAMPLE_CLIENT_SECRET'):
			self.assertNotIn(name, launch_spec['env'].keys())

	def test_jira_transition_history_between_attempt_and_receipt_is_accepted(self):

		class RealisticTransitionHttp(FakeHttp):

			def __init__(self):
				super().__init__()
				self.transition_posted = False

			def request(self, method, url, **kwargs):
				if method == 'POST' and '/transitions' in url:
					self.transition_posted = True
				return super().request(method, url, **kwargs)

		class MutationClock(FakeClock):

			def __init__(self, transport):
				super().__init__()
				self.transport = transport

			def now(self):
				if self.transport.transition_posted:
					return '2026-10-09T08:00:02Z'
				return NOW
		http = RealisticTransitionHttp()
		clock = MutationClock(http)
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_http_transport': http, '_clock': clock})
		project_config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		project_config['jira'].update(cloud_id='cloud-1', site='https://jira.example.invalid', provider_project_id='project-1', project_key='EX', controller_actor_id='controller')
		contract = json.loads((ROOT / '.agentic/examples/evidence-bundle.json').read_text())['contract']
		store = ContinuousControllerStore(self.root / 'jira-timing.sqlite3', ['A'], worktree_roots=[self.root / 'worktree'])
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False):
			result = production_jira_lifecycle(store, config=project_config, contract=contract, event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True}, binding={'issue_id': 'EX-42'}, issue_type='LEAF', state='DISPATCHED', producer_id='controller', run_id='00000000-0000-0000-0000-000000000001', now=NOW, evidence=[], transition_id='3', read_current_status=adapters['read_current_status'], write_transition=adapters['write_transition'], read_transition=adapters['read_transition'], observe_provider_identity=adapters['observe_provider_identity'], observe_issue_identity=adapters['observe_issue_identity'])
		self.assertIn('record', result, result)
		self.assertEqual(result['record']['status'], 'SUCCEEDED')

	def test_jira_old_history_entry_cannot_prove_current_transition(self):

		class OldHistory(FakeHttp):

			def request(self, method, url, **kwargs):
				status, body = super().request(method, url, **kwargs)
				if 'expand=changelog' in url:
					value = json.loads(body)
					value['changelog']['histories'][0]['created'] = '2020-01-01T00:00:00Z'
					body = json.dumps(value)
				return (status, body)
		http = OldHistory()
		cfg = {**self.config, '_http_transport': http, '_clock': FakeClock()}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False):
			with self.assertRaisesRegex(ValidationError, 'current transition|operation'):
				adapters['read_transition']({'binding': {'issue_id': '10001'}, 'to_status_id': 'In Progress', 'jira_provider': {'cloud_id': 'cloud-1', 'project_id': 'project-1', 'controller_actor_id': 'controller'}, 'operation_id': 'op'}, {'observed_at': NOW})

	def test_github_slurped_pages_are_bounded(self):

		class TooManyPages(FakeRunner):

			def run(self, argv, *, cwd, env, timeout, max_bytes):
				if 'issues?' in argv[-1]:
					return [[] for _ in range(21)]
				return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
		runner = TooManyPages()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'page'):
			adapters['observe_inventory'](NOW)

	def test_ruleset_slurped_pages_are_bounded(self):

		class TooManyRulesetPages(FakeRunner):

			def run(self, argv, *, cwd, env, timeout, max_bytes):
				if 'rulesets' in argv[-1]:
					rule = {'id': 1, 'enforcement': 'active', 'conditions': {'ref_name': {'include': ['~ALL'], 'exclude': []}}}
					return [[rule] for _ in range(21)]
				return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': TooManyRulesetPages(), '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'page'):
			adapters['observe_publication']({'ticket': 'AWF-32'})

	def test_ruleset_pagination_time_bound_uses_adapter_bound(self):

		class SlowRulesClock(FakeClock):

			def monotonic(self):
				self.ticks += 121
				return self.ticks
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': FakeRunner(), '_clock': SlowRulesClock()})
		with self.assertRaisesRegex(ValidationError, 'time bound'):
			adapters['observe_publication']({'ticket': 'AWF-32'})

	def test_github_pagination_time_bound_uses_adapter_bound(self):

		class SlowClock(FakeClock):

			def monotonic(self):
				self.ticks += 121
				return self.ticks
		runner = FakeRunner()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': SlowClock()})
		with self.assertRaisesRegex(ValidationError, 'time bound'):
			adapters['observe_inventory'](NOW)

	def test_authenticated_stage_derives_role_and_forged_code_needed_blocks_only_ticket(self):
		valid = _ticket()
		forged = {**_ticket(), 'ticket': 'AWF-89', 'code_needed': False}
		runner = FakeRunner(inventory=[_issue(valid, number=32, stage='independent-review'), _issue(forged, number=89, stage='implementation')])
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		tickets = adapters['observe_inventory'](NOW)['tickets']
		accepted = next((item for item in tickets if item['ticket'] == 'AWF-32'))
		refused = next((item for item in tickets if item['ticket'] == 'AWF-89'))
		self.assertEqual((accepted['dispatch_role'], accepted['lifecycle_stage']), ('critic', 'independent_review'))
		self.assertEqual(refused['disposition'], 'BLOCKED')
		self.assertIn('code_needed', refused['reason'])

	def test_malformed_ticket_body_blocks_ticket_but_valid_sibling_survives(self):
		runner = FakeRunner(inventory=[_issue(_ticket(), number=32), _issue(number=89, body='{not-json')])
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		tickets = adapters['observe_inventory'](NOW)['tickets']
		self.assertEqual(len(tickets), 2)
		self.assertEqual(tickets[0]['disposition'], 'ELIGIBLE')
		self.assertEqual(tickets[1]['disposition'], 'BLOCKED')
		self.assertIn('malformed ticket record', tickets[1]['reason'].lower())

	def test_non_object_json_body_blocks_ticket_but_valid_sibling_survives(self):
		for body in ('[{}]', '7', 'true', 'false', 'null', '"ticket text"'):
			with self.subTest(body=body):
				runner = FakeRunner(inventory=[_issue(_ticket(), number=32), _issue(number=89, body=body)])
				adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
				tickets = adapters['observe_inventory'](NOW)['tickets']
				self.assertEqual(len(tickets), 2)
				self.assertEqual(tickets[0]['disposition'], 'ELIGIBLE')
				self.assertEqual(tickets[1]['disposition'], 'BLOCKED')
				self.assertIn('malformed ticket record', tickets[1]['reason'].lower())

	def test_unhashable_disposition_blocks_ticket_but_valid_sibling_survives(self):
		malformed = {**_ticket(), 'ticket': 'AWF-89', 'disposition': []}
		runner = FakeRunner(inventory=[_issue(_ticket(), number=32), _issue(malformed, number=89)])
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		tickets = adapters['observe_inventory'](NOW)['tickets']
		self.assertEqual(len(tickets), 2)
		self.assertEqual(tickets[0]['disposition'], 'ELIGIBLE')
		self.assertEqual(tickets[1]['ticket'], 'AWF-89')
		self.assertEqual(tickets[1]['disposition'], 'BLOCKED')
		self.assertIn('malformed ticket record', tickets[1]['reason'].lower())

	def test_corrupt_authenticated_inventory_blocks_entire_cycle(self):
		corrupt = _issue(_ticket(), number=32)
		corrupt.pop('node_id')
		runner = FakeRunner(inventory=[corrupt])
		runner.inventory = [corrupt]
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'inventory|provider|identity'):
			adapters['observe_inventory'](NOW)

	def test_foreign_inventory_endpoint_is_rejected_before_consumption(self):
		runner = FakeRunner()
		cfg = json.loads(json.dumps(self.config))
		cfg['github']['inventory_endpoint'] = 'repos/foreign/repository/issues?state=open'
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**cfg, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'inventory endpoint'):
			adapters['observe_inventory'](NOW)

	def test_foreign_rules_endpoint_is_rejected_before_consumption(self):
		runner = FakeRunner()
		cfg = json.loads(json.dumps(self.config))
		cfg['github']['rules_endpoint'] = 'repos/foreign/repository/rulesets'
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**cfg, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'rules endpoint'):
			adapters['observe_publication']({'ticket': 'AWF-32'})
		self.assertFalse(any(('repos/foreign/repository/rulesets' in call[0][-1] for call in runner.calls)))

	def test_doc_example_defines_all_runtime_values_and_no_raw_placeholders(self):
		doc = (ROOT / '.agentic/docs/34-CONTINUOUS-CONTROLLER.md').read_text(encoding='utf-8')
		section = doc[doc.index('## Reference adapter (AWF-32)'):]
		for name in ('$NOW', '$HEAD', '$TREE', '$ADAPTER_PIN', '$CONTROLLER_ACTOR', '$TRANSITION_ID'):
			self.assertRegex(section, '(?m)^\\s*\\$' + name[1:] + '\\s*=')
		self.assertNotIn('<STATE_DIR>', section)
		self.assertNotIn('<WORKTREE_ROOT>', section)
		self.assertNotIn('--producer-id CONTROLLER_ACTOR', section)
		self.assertNotIn('--transition-id TRANSITION_ID', section)
		self.assertIn('$AWF_CODEX_HOME', section)
		self.assertIn('isolated_home', section)
		self.assertIn('windows_sandbox', section)
		self.assertNotIn('mcp_servers.', section)

	def test_github_pagination_requires_provider_evidence_and_binds_page_size(self):

		class Paged(FakeRunner):

			def run(self, argv, *, cwd, env, timeout, max_bytes):
				self.calls.append((list(argv), Path(cwd), dict(env)))
				path = argv[-1]
				if 'issues?' in path:
					return [[self.inventory[0]], []]
				return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
		runner = Paged()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		adapters['observe_inventory'](NOW)
		issue_call = next((call[0] for call in runner.calls if 'issues?' in call[0][-1]))
		self.assertIn('--paginate', issue_call)
		self.assertIn('per_page=100', issue_call[-1])

	def test_default_branch_only_ruleset_does_not_authorize_a_feature_ref(self):

		class DefaultOnly(FakeRunner):

			def run(self, argv, *, cwd, env, timeout, max_bytes):
				if 'rulesets' in argv[-1]:
					return [[{'id': 1, 'enforcement': 'active', 'conditions': {'ref_name': {'include': ['~DEFAULT_BRANCH'], 'exclude': []}}}]]
				return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)
		runner = DefaultOnly()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		observation = adapters['observe_publication']({'ticket': 'AWF-32'})
		self.assertEqual(observation['rules']['state'], 'UNOBSERVED')

	@skip_unless_source_repo()
	def test_generated_adapter_example_is_idempotent_and_complete(self):
		sys.path.insert(0, str(ROOT / 'scripts'))
		try:
			generated = __import__('runpy').run_path(str(ROOT / 'scripts/generate_examples.py'), run_name='awf32_generator')
		finally:
			sys.path.pop(0)
		expected = generated['controller_adapter_config']()
		stored = json.loads((ROOT / '.agentic/examples/reference-controller-adapter.json').read_text())
		self.assertEqual(stored, expected)
		self.assertIn('expected_actor_id', stored['github'])
		self.assertIn('auth_profile', stored['github'])
		self.assertIn('merged_transition_id', stored['jira'])


	def _identity_adapters(self, http, clock=None):
		return __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_http_transport': http, '_clock': clock or FakeClock()})

	def _identity_lifecycle(self, adapters, issue='EX-42'):
		config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		config['jira'].update({k: self.config['jira'][k] for k in ('cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id')})
		store = ContinuousControllerStore(self.root / 'identity.sqlite', ['A'], worktree_roots=[self.root / 'worktree'])
		binding = {'issue_id': issue}
		return production_jira_lifecycle(store, config=config, contract={}, event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True}, binding=binding, issue_type='LEAF', state='DISPATCHED', producer_id='controller', run_id='00000000-0000-0000-0000-000000000001', now=NOW, evidence=[], transition_id='3', **{k: adapters[k] for k in ('observe_provider_identity', 'observe_issue_identity', 'read_current_status', 'write_transition', 'read_transition')})

	def test_issue_identity_returns_provider_key_and_completion_time(self):
		clock = FakeClock()
		class CompletingHttp(FakeHttp):
			def request(self, method, url, **kwargs):
				result = super().request(method, url, **kwargs)
				if url.endswith('?fields=key'):
					clock.value = '2026-10-09T08:00:02Z'
				return result
		http = CompletingHttp()
		observer = self._identity_adapters(http, clock)['observe_issue_identity']
		with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'):
			result = observer('EX-42')
		self.assertEqual({'issue_key': 'EX-42', 'observed_at': clock.value}, result)
		self.assertEqual([url.split('invalid')[-1] for _, url, _, _ in http.calls], ['/rest/api/3/myself', '/rest/api/3/project/EX', '/_edge/tenant_info', '/rest/api/3/issue/EX-42?fields=key'])
		self.assertTrue(all(call[0] == 'GET' for call in http.calls))

	def test_provider_alias_is_rejected_before_status_or_intent(self):
		http = FakeHttp()
		adapters = self._identity_adapters(http)
		observer = adapters['observe_issue_identity']
		with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'):
			self.assertEqual('EX-42', observer('OLD-42')['issue_key'])
			with self.assertRaisesRegex(ValidationError, 'canonical issue key'):
				self._identity_lifecycle(adapters, 'OLD-42')
		self.assertEqual(0, http.issue_reads)
		self.assertTrue(all(call[0] == 'GET' for call in http.calls))
		store = ContinuousControllerStore(self.root / 'identity.sqlite', ['A'])
		self.assertEqual([], store.jira_operations('OLD-42'))
		self.assertEqual([], store.jira_operations('EX-42'))

	def test_issue_identity_rejects_malformed_input_without_http(self):
		http = FakeHttp()
		observer = self._identity_adapters(http)['observe_issue_identity']
		for issue in ('2001', 2001, 'ex-42', ' EX-42', 'EX-42 ', 'EX-0', 'EX-042', 'EX-42/x', 'EX-42?x'):
			with self.subTest(issue=issue), self.assertRaises(ValidationError):
				observer(issue)
		self.assertEqual([], http.calls)

	def test_issue_identity_verifies_provider_before_issue_get(self):
		for field, value in (('cloudId', 'foreign'), ('siteUrl', 'https://foreign.invalid'), ('id', 'foreign'), ('key', 'OTHER'), ('accountId', 'foreign')):
			with self.subTest(field=field):
				class WrongProvider(FakeHttp):
					def request(self, method, url, **kwargs):
						status, body = super().request(method, url, **kwargs)
						obj = json.loads(body)
						if field in obj:
							obj[field] = value
						return status, json.dumps(obj)
				http = WrongProvider()
				observer = self._identity_adapters(http)['observe_issue_identity']
				with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'), self.assertRaises(ValidationError):
					observer('EX-42')
				self.assertFalse(any('/issue/' in call[1] for call in http.calls))

	def test_issue_identity_rejects_invalid_provider_payload(self):
		for payload in ({}, [], {'key': None}, {'key': 100}, {'key': '100'}, {'key': 'ex-42'}, {'fields': {'key': 'EX-42'}}):
			with self.subTest(payload=payload):
				class InvalidKey(FakeHttp):
					def request(self, method, url, **kwargs):
						result = super().request(method, url, **kwargs)
						return (200, json.dumps(payload)) if url.endswith('?fields=key') else result
				http = InvalidKey()
				observer = self._identity_adapters(http)['observe_issue_identity']
				with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'), self.assertRaises(ValidationError):
					observer('EX-42')
				self.assertTrue(all(call[0] == 'GET' for call in http.calls))
		observer = self._identity_adapters(FakeHttp(), FakeClock('bad-time'))['observe_issue_identity']
		with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'), self.assertRaises(ValidationError):
			observer('EX-42')

	def test_issue_identity_transport_failures_are_bounded_and_sanitized(self):
		import io
		for raw, status in ((b'x' * (1024 * 1024 + 1), 200), (b'\xff', 200), (b'{}', 403), (b'not-json', 200), (None, 200)):
			with self.subTest(size=None if raw is None else len(raw), status=status):
				namespace = __import__('runpy').run_path(str(self.adapter))
				reads, timeouts = [], []
				class Response(io.BytesIO):
					def read(self, size):
						reads.append(size)
						return super().read(size)
				def urlopen(request, timeout):
					timeouts.append(timeout)
					if raw is None:
						raise RuntimeError('fixture-secret')
					response = Response(raw)
					response.status = status
					return response
				with patch.dict(namespace['_HttpTransport'].request.__globals__, urlopen=urlopen):
					observer = namespace['build_adapters']({**self.config, '_clock': FakeClock()})['observe_issue_identity']
					with patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'), self.assertRaises(ValidationError) as error:
						observer('EX-42')
				self.assertNotIn('fixture-secret', str(error.exception))
				self.assertEqual([30], timeouts)
				self.assertEqual([] if raw is None else [1024 * 1024 + 1], reads)
		observer = self._identity_adapters(FakeHttp())['observe_issue_identity']
		with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValidationError):
			observer('EX-42')

	def test_cli_jira_lifecycle_uses_shipped_factory(self):
		import contextlib
		import io
		adapter_config = self.root / 'adapter.json'
		adapter_config.write_text(json.dumps(self.config), encoding='utf-8')
		_, loader = _load_real(self.adapter, adapter_config)
		for enabled in (True, False):
			with self.subTest(enabled=enabled):
				config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text())
				config['jira'].update({k: self.config['jira'][k] for k in ('cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id')})
				config['jira']['enabled'] = enabled
				files = {'project': config, 'contract': {}, 'facts': {'run_registered': True, 'worktree_verified': True}, 'binding': {'issue_id': 'EX-42'}}
				for name, value in files.items():
					(self.root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
				state = self.root / ('cli-' + str(enabled) + '.sqlite')
				argv = ['--state', str(state), '--stream', 'A', '--worktree-root', str(self.root / 'worktree'), '--project-config', str(self.root / 'project.json'), '--adapter-module', str(self.adapter), '--adapter-sha256', sha256(self.adapter.read_bytes()), '--adapter-config', str(adapter_config), 'jira-lifecycle', '--contract', str(self.root / 'contract.json'), '--facts', str(self.root / 'facts.json'), '--binding', str(self.root / 'binding.json'), '--event', 'WORKER_STARTED', '--lifecycle-state', 'DISPATCHED', '--producer-id', 'controller', '--run-id', '00000000-0000-0000-0000-000000000001', '--now', NOW, '--transition-id', '3']
				http = FakeHttp()
				def request(_transport, *args, **kwargs):
					return http.request(*args, **kwargs)
				def clock_text(_clock):
					return NOW
				original_exec = exec
				def external_boundaries(code, namespace):
					original_exec(code, namespace)
					if namespace.get('__name__') == '_awf_reviewed_controller_adapter':
						namespace['_HttpTransport'].request = request
						namespace['_clock_text'] = clock_text
				output = io.StringIO()
				with patch.dict(loader._load_reviewed_adapters.__globals__, exec=external_boundaries), patch.dict(os.environ, AWF_JIRA_TOKEN='fixture-secret'), contextlib.redirect_stdout(output):
					self.assertEqual(0, loader.main(argv))
				result = json.loads(output.getvalue())
				operations = ContinuousControllerStore(state, ['A']).jira_operations('EX-42')
				if enabled:
					self.assertEqual('SUCCEEDED', result['record']['status'])
					self.assertEqual('EX-42', result['record']['binding']['issue_id'])
					self.assertEqual(1, sum(call[0] == 'POST' for call in http.calls))
					urls = [call[1] for call in http.calls]
					self.assertLess(next(i for i, url in enumerate(urls) if url.endswith('?fields=key')), next(i for i, url in enumerate(urls) if url.endswith('?fields=status')))
					post = next(i for i, call in enumerate(http.calls) if call[0] == 'POST')
					self.assertLess(next(i for i, url in enumerate(urls) if url.endswith('?fields=status')), post)
					self.assertGreater(next(i for i, url in enumerate(urls) if 'expand=changelog' in url), post)
				else:
					self.assertEqual([], http.calls)
					self.assertEqual([], operations)

	def test_jira_lifecycle_is_read_before_one_write_and_readback(self):
		http = FakeHttp()
		clock = FakeClock()
		cfg = {**self.config, '_http_transport': http, '_clock': clock}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		project_config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		project_config['jira'].update(cloud_id='cloud-1', site='https://jira.example.invalid', provider_project_id='project-1', project_key='EX', controller_actor_id='controller')
		contract = json.loads((ROOT / '.agentic/examples/evidence-bundle.json').read_text())['contract']
		binding = {'issue_id': 'EX-42'}
		store = ContinuousControllerStore(self.root / 'jira.sqlite3', ['A'], worktree_roots=[self.root / 'worktree'])
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False):
			result = production_jira_lifecycle(store, config=project_config, contract=contract, event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True}, binding=binding, issue_type='LEAF', state='DISPATCHED', producer_id='controller', run_id='00000000-0000-0000-0000-000000000001', now=NOW, evidence=[], transition_id='3', read_current_status=adapters['read_current_status'], write_transition=adapters['write_transition'], read_transition=adapters['read_transition'], observe_provider_identity=adapters['observe_provider_identity'], observe_issue_identity=adapters['observe_issue_identity'])
		self.assertTrue(result['planned'])
		self.assertEqual(result['record']['status'], 'SUCCEEDED')
		self.assertEqual(http.asserted_transition_id, '3')
		methods = [call[0] for call in http.calls]
		self.assertEqual(methods.count('POST'), 1)
		self.assertLess(methods.index('GET'), methods.index('POST'))
		self.assertNotIn('secret-jira', repr(result))

	def test_jira_readback_uses_provider_history_actor(self):
		http = ForeignActorHttp()
		cfg = {**self.config, '_http_transport': http, '_clock': FakeClock()}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		project_config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text())
		project_config['jira'].update(cloud_id='cloud-1', site='https://jira.example.invalid', provider_project_id='project-1', project_key='EX', controller_actor_id='controller')
		contract = json.loads((ROOT / '.agentic/examples/evidence-bundle.json').read_text())['contract']
		store = ContinuousControllerStore(self.root / 'foreign.sqlite3', ['A'], worktree_roots=[self.root / 'worktree'])
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False):
			result = production_jira_lifecycle(store, config=project_config, contract=contract, event='WORKER_STARTED', facts={'run_registered': True, 'worktree_verified': True}, binding={'issue_id': 'EX-42'}, issue_type='LEAF', state='DISPATCHED', producer_id='controller', run_id='00000000-0000-0000-0000-000000000001', now=NOW, evidence=[], transition_id='3', read_current_status=adapters['read_current_status'], write_transition=adapters['write_transition'], read_transition=adapters['read_transition'], observe_provider_identity=adapters['observe_provider_identity'], observe_issue_identity=adapters['observe_issue_identity'])
		self.assertNotEqual(result.get('record', {}).get('status'), 'SUCCEEDED')
		self.assertIn('writes_stopped', result)

	def test_merge_observed_reconciles_then_pages(self):
		http = FakeHttp()
		clock = FakeClock()
		cfg = {**self.config, '_http_transport': http, '_clock': clock}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		progress = {'jira_enabled': True, 'merged_ticket': '100', 'scope': 'project = EX', 'observed_at': NOW, 'jira_binding': {'cloud_id': 'cloud-1', 'project_id': 'project-1', 'actor_id': 'controller'}, 'count_gate': {}, 'max_pages': 5, 'max_items': 10}
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False), patch('agentic.closeout.require_jira_reconciliation_before_counts', return_value={'status': 'PASS', 'count_allowed': True, 'ticket': '100'}):
			result = production_merge_observed(lifecycle_state='MERGING', lifecycle_facts={'merge_confirmed': True, 'candidate_matched': True}, jira_progress={**progress, 'reconcile_merged_ticket': adapters['reconcile_merged_ticket'], 'fetch_scope_page': adapters['fetch_scope_page']})
		self.assertEqual(result['state'], 'MERGED')
		self.assertEqual(result['jira_progress']['jira_state'], 'COUNTED')
		self.assertEqual(result['jira_progress']['closed'], 1)

	def test_merge_observed_uses_shipped_adapter_closeout_observers(self):
		repository = self.root / 'merge-repository'
		repository.mkdir()

		def git(*args):
			return subprocess.run(['git', '-C', str(repository), *args], check=True, capture_output=True, text=True).stdout.strip()
		git('init', '-q', '--initial-branch=main')
		(repository / 'tracked.txt').write_bytes(b'base\n')
		git('add', 'tracked.txt')
		git('-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'base')
		base = git('rev-parse', 'HEAD')
		git('checkout', '-qb', 'feature')
		(repository / 'tracked.txt').write_bytes(b'candidate\n')
		git('add', 'tracked.txt')
		git('-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'candidate')
		head = git('rev-parse', 'HEAD')
		git('checkout', '-q', 'main')
		git('-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', '-c', 'commit.gpgsign=false', 'merge', '-q', '--no-ff', '-m', 'merge', 'feature')
		merge = git('rev-parse', 'HEAD')
		tree = git('rev-parse', 'HEAD^{tree}')
		blob = git('rev-parse', 'HEAD:tracked.txt')
		project_config = json.loads((ROOT / '.agentic/examples/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
		project_config['github'].update(repository='example/project', repository_id=101, base_branch='main')
		project_config['jira'].update(cloud_id='cloud-1', site='https://jira.example.invalid', provider_project_id='project-1', project_key='EX', controller_actor_id='controller')
		project_bytes = canonical(project_config)
		evidence = self.root / 'observer-evidence'
		closeout_dir = evidence / 'closeout'
		jira_dir = evidence / 'jira'
		closeout_dir.mkdir(parents=True)
		jira_dir.mkdir()
		project_path = evidence / 'PROJECT_CONFIG.yaml'
		project_path.write_bytes(project_bytes)
		binding = {'project_id': project_config['project']['id'], 'repository_id': 101, 'issue_id': '100', 'requirements_hash': 'a' * 64, 'contract_hash': 'b' * 64, 'policy_hash': policy_hash(project_config, definition()), 'candidate_id': 'd' * 64}
		transition_id = '10000000-0000-0000-0000-000000000001'
		merge_result_id = '10000000-0000-0000-0000-000000000002'
		closeout_id = '10000000-0000-0000-0000-000000000003'
		closeout = {'schema_version': 3, 'record_id': closeout_id, 'created_at': NOW, 'producer_id': 'fixture-controller', 'run_id': '10000000-0000-0000-0000-000000000004', 'binding': binding, 'pr_number': 32, 'reviewed_head_sha': head, 'base_sha': base, 'merge_commit_sha': merge, 'merge_tree_sha': tree, 'bound_files': [{'path': 'tracked.txt', 'blob_sha': blob, 'sha256': hashlib.sha256(b'candidate\n').hexdigest()}], 'gate_record_id': '10000000-0000-0000-0000-000000000005', 'review_record_ids': ['10000000-0000-0000-0000-000000000006'], 'jira_transition_record_id': transition_id, 'evidence': ['urn:awf:fixture:closeout']}
		transition = {'schema_version': 3, 'record_id': transition_id, 'created_at': NOW, 'producer_id': 'controller', 'run_id': '10000000-0000-0000-0000-000000000007', 'binding': binding, 'operation_id': '10000000-0000-0000-0000-000000000008', 'merge_result_id': merge_result_id, 'jira_provider': {'cloud_id': 'cloud-1', 'site': 'https://jira.example.invalid', 'project_id': 'project-1', 'project_key': 'EX', 'controller_actor_id': 'controller'}, 'lifecycle_event': 'JIRA_RECONCILED', 'from_status_id': '3', 'to_status_id': 'Done', 'transition_id': 'done-transition', 'status': 'SUCCEEDED', 'evidence': ['urn:awf:fixture:jira-transition'], 'read_back': {'observed_status': 'Done', 'observed_actor': 'controller', 'observed_at': NOW}}
		(closeout_dir / (closeout_id + '.json')).write_bytes(canonical({'record': closeout, 'merge_result_id': merge_result_id, 'merge_observed_at': NOW}))
		(jira_dir / (transition_id + '.json')).write_bytes(canonical({'record': transition}))
		adapter_config = {**self.config, '_http_transport': FakeHttp(), '_clock': FakeClock(), 'observations': {'accepted_project_config': {'path': str(project_path), 'sha256': sha256(project_bytes), 'blob_sha': 'e' * 40, 'acceptance_receipt_id': 'fixture-acceptance'}, 'closeout_directory': str(closeout_dir), 'jira_transition_directory': str(jira_dir)}}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](adapter_config)
		import importlib.util
		spec = importlib.util.spec_from_file_location('test_awf76_controller_loader', ROOT / '.agentic/scripts/continuous_controller.py')
		controller_script = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(controller_script)
		count_gate = controller_script._count_gate_inputs({'closeout_record': closeout}, config=project_config, repository=repository, contracts=Contracts(ROOT / '.agentic/schemas'), adapters=adapters, adapter_sha256=sha256(self.adapter.read_bytes()), observed_at=NOW)
		progress = {'jira_enabled': True, 'merged_ticket': '100', 'scope': 'project = EX', 'observed_at': NOW, 'jira_binding': {'cloud_id': 'cloud-1', 'project_id': 'project-1', 'actor_id': 'controller'}, 'count_gate': count_gate, 'max_pages': 5, 'max_items': 10}
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False):
			result = production_merge_observed(lifecycle_state='MERGING', lifecycle_facts={'merge_confirmed': True, 'candidate_matched': True}, jira_progress={**progress, 'reconcile_merged_ticket': adapters['reconcile_merged_ticket'], 'fetch_scope_page': adapters['fetch_scope_page']})
		self.assertEqual(result['state'], 'MERGED')
		self.assertEqual(result['jira_progress']['jira_state'], 'COUNTED')
		self.assertEqual(result['jira_progress']['closed'], 1)

	def test_jira_advancing_page_time_is_not_counted(self):
		http = AdvancingPageHttp()
		cfg = {**self.config, '_http_transport': http, '_clock': FakeClock()}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		progress = {'jira_enabled': True, 'merged_ticket': '100', 'scope': 'project = EX', 'observed_at': NOW, 'jira_binding': {'cloud_id': 'cloud-1', 'project_id': 'project-1', 'actor_id': 'controller'}, 'count_gate': {}, 'max_pages': 5, 'max_items': 10}
		with patch.dict(os.environ, {'AWF_JIRA_TOKEN': 'secret-jira'}, clear=False), patch('agentic.closeout.require_jira_reconciliation_before_counts', return_value={'status': 'PASS', 'count_allowed': True, 'ticket': '100'}):
			result = production_merge_observed(lifecycle_state='MERGING', lifecycle_facts={'merge_confirmed': True, 'candidate_matched': True}, jira_progress={**progress, 'reconcile_merged_ticket': adapters['reconcile_merged_ticket'], 'fetch_scope_page': adapters['fetch_scope_page']})
		self.assertEqual(result['jira_progress']['jira_state'], 'RECONCILED')

	def test_interrupted_dispatch_is_observed_without_relaunch(self):
		runner = FakeRunner()
		cfg = {**self.config, '_runner': runner, '_clock': FakeClock()}
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters'](cfg)
		payload = _dispatch_payload('d1')
		adapters['dispatch_ticket'](payload)
		receipt = adapters['observe_dispatch']({**payload, 'reconcile_nonce': 'reconcile', 'reconcile_at': NOW})
		self.assertEqual(receipt['status'], 'ACCEPTED')
		self.assertEqual(len(runner.launches), 1)

	def test_fail_closed_config_hash_timeout_partial_page_identity_and_secret(self):
		with self.assertRaises(ValidationError):
			__import__('runpy').run_path(str(self.adapter))['build_adapters']({})
		bad = json.loads(json.dumps(self.config))
		bad['codex']['roles']['writer']['sandbox'] = 'danger-full-access'
		with self.assertRaisesRegex(ValidationError, 'sandbox'):
			__import__('runpy').run_path(str(self.adapter))['build_adapters'](bad)
		runner = FakeRunner(partial=True)
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaises(ValidationError):
			adapters['observe_inventory']()
		bad_repo = FakeRunner(bad_repo=True)
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': bad_repo, '_clock': FakeClock()})
		with self.assertRaisesRegex(ValidationError, 'identity'):
			adapters['observe_publication']({'ticket': 'AWF-32'})
		self.config['github']['executable']['sha256'] = 'f' * 64
		with self.assertRaisesRegex(ValidationError, 'hash'):
			__import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': FakeRunner()})['observe_inventory']()

	def test_nonzero_timeout_and_missing_token_have_sanitized_errors(self):
		runner = FakeRunner(exit_code=2)
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaises(ValidationError):
			adapters['observe_inventory']()
		runner = FakeRunner(error='TOP_SECRET_VALUE')
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_runner': runner, '_clock': FakeClock()})
		with self.assertRaises(ValidationError) as raised:
			adapters['observe_inventory']()
		self.assertNotIn('TOP_SECRET_VALUE', str(raised.exception))
		http = FakeHttp()
		adapters = __import__('runpy').run_path(str(self.adapter))['build_adapters']({**self.config, '_http_transport': http, '_clock': FakeClock()})
		with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValidationError, 'credential'):
			adapters['observe_provider_identity']()

# AWF-205: real factory and authenticated planning contracts.
import ast
from copy import deepcopy
ADAPTER = ROOT / ".agentic/adapters/reference_controller_adapter.py"
CONTROLLER = ROOT / ".agentic/scripts/continuous_controller.py"
EXAMPLE = ROOT / ".agentic/examples/reference-controller-adapter.json"
BINDING = {"project_id": "project-1", "repository_id": "101", "scope_sha256": "a" * 64}

def cycle_requirements(script):
    """Read the actual unconditional CLI cycle set, rather than duplicating it."""
    tree = ast.parse(Path(script).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Attribute)
                and node.test.left.attr == "command"
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "cycle"):
            for statement in node.body:
                if (isinstance(statement, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "names" for t in statement.targets)):
                    return ast.literal_eval(statement.value)
    raise AssertionError("actual cycle callable contract was not found")


class PlanningRunner(FakeRunner):
    """FakeRunner plus open-PR listing, PR files and owner comments."""

    def __init__(self, *, pulls=(), pull_files=None, comments=None, **kwargs):
        super().__init__(**kwargs)
        self.pulls = list(pulls)
        self.pull_files = dict(pull_files or {})
        self.comments = dict(comments or {})

    def run(self, argv, *, cwd, env, timeout, max_bytes):
        path = argv[-1] if argv else ""
        if path.startswith("repos/example/project/pulls?"):
            self.calls.append((list(argv), Path(cwd), dict(env)))
            return {"items": self.pulls, "complete": True, "next_page": None}
        if "/pulls/" in path and "/files" in path:
            self.calls.append((list(argv), Path(cwd), dict(env)))
            number = int(path.split("/pulls/")[1].split("/")[0])
            return {"items": [{"filename": name} for name in self.pull_files[number]],
                    "complete": True, "next_page": None}
        if "/issues/comments/" in path:
            self.calls.append((list(argv), Path(cwd), dict(env)))
            return {"headers": {"ETag": '"owner"', "Date": "Fri, 09 Oct 2026 08:00:00 GMT"},
                    "body": self.comments[int(path.rsplit("/", 1)[1])]}
        return super().run(argv, cwd=cwd, env=env, timeout=timeout, max_bytes=max_bytes)

def project_config():
    from test_planning import config
    value = config(1)
    value["github"]["repository_id"] = BINDING["repository_id"]
    value["github"]["expected_actor_id"] = 7001
    return value

def planning_request(cfg, pin):
    binding = {**BINDING, "repository_head_sha": "a" * 40,
               "project_config_sha256": fingerprint("accepted-project-config", cfg), "cycle_id": "cycle-205"}
    provider = {"provider_id": "reviewed-controller", "implementation_sha256": pin,
                "verifier_implementation_sha256": pin}
    return {"provider": provider, "binding": binding, "observed_at": NOW}

class ReferenceAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf205-adapter-")
        self.root = Path(self.temp.name)
        self.config = _base_config(self.root)
        self.pin = sha256(ADAPTER.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def adapters(self, runner=None, clock=None):
        runner = runner or PlanningRunner()
        return __import__("runpy").run_path(str(ADAPTER))["build_adapters"](
            {**self.config, "_runner": runner, "_clock": clock or FakeClock()})

    def resource_binding(self):
        return {**BINDING, "repository_head_sha": "a" * 40, "repository_tree_sha": "b" * 40,
                "project_config_sha256": "c" * 64, "operating_config_sha256": "d" * 64}

    def test_reference_host_callbacks_produce_verified_resource_observation(self):
        from agentic.planning import produce_planning_resource_observation
        adapters = self.adapters()
        self.assertTrue(all(callable(adapters.get(name)) for name in ('observe_host_resources', 'verify_host_observation')), 'base factory lacks host callbacks')
        observed = produce_planning_resource_observation(
            binding=self.resource_binding(), observed_at=NOW, producer_implementation_sha256=self.pin,
            observe_host_resources=adapters["observe_host_resources"],
            verify_host_observation=adapters["verify_host_observation"])
        expected = min(os.cpu_count(), 1000)
        self.assertEqual((observed["host_capacity"], observed["resources"]),
                         (expected, [{"name": "cpu", "capacity": expected}]))
        self.assertEqual(observed["authentication"]["producer_id"], "reference-host")

    def test_reference_host_callbacks_reject_tampered_or_foreign_evidence(self):
        from agentic.planning import _planning_resource_request
        adapters = self.adapters()
        self.assertTrue(all(callable(adapters.get(name)) for name in ('observe_host_resources', 'verify_host_observation')), 'base factory lacks host callbacks')
        request = _planning_resource_request(self.resource_binding(), NOW, self.pin)
        observed = adapters["observe_host_resources"](deepcopy(request))
        auth = {key: observed[key] for key in ("producer_id", "evidence_id", "evidence_sha256", "request_sha256")}
        self.assertEqual(adapters["verify_host_observation"](deepcopy(auth), deepcopy(request))["status"], "VERIFIED")
        with self.assertRaises(ValidationError):
            self.foreign_adapters()["verify_host_observation"](deepcopy(auth), deepcopy(request))
        with self.assertRaises(ValidationError):
            adapters["verify_host_observation"](deepcopy(auth), {**request, "observed_at": "2026-10-09T08:00:01Z"})
        with self.assertRaises(ValidationError):
            adapters["observe_host_resources"]({**request, "binding": {**request["binding"], "project_id": "foreign"}})
        for key, value in (("producer_id", "foreign"), ("evidence_id", "../host"),
                           ("evidence_sha256", "f" * 64), ("request_sha256", "e" * 64)):
            with self.subTest(authentication=key), self.assertRaises(ValidationError):
                adapters["verify_host_observation"]({**auth, key: value}, deepcopy(request))
        for key in request["binding"]:
            changed = deepcopy(request)
            changed["binding"][key] = "foreign"
            with self.subTest(binding=key), self.assertRaises(ValidationError):
                adapters["verify_host_observation"](deepcopy(auth), changed)
        from agentic.planning import produce_planning_resource_observation
        for key, value in (("host_capacity", observed["host_capacity"] + 1),
                           ("resources", [{"name": "cpu", "capacity": 0}]),
                           ("producer_id", "foreign"),
                           ("observed_at", "2026-10-09T08:00:01Z"),
                           ("request_sha256", "f" * 64)):
            def forged(request, key=key, value=value):
                return {**adapters["observe_host_resources"](request), key: value}
            with self.subTest(claim=key), self.assertRaises(ValidationError):
                produce_planning_resource_observation(
                    binding=self.resource_binding(), observed_at=NOW,
                    producer_implementation_sha256=self.pin,
                    observe_host_resources=forged,
                    verify_host_observation=adapters["verify_host_observation"])
        original = deepcopy(request)
        adapters["observe_host_resources"](request)
        self.assertEqual(original, request)
        evidence = self.root / "runs" / "planning-evidence" / (auth["evidence_id"] + ".json")
        evidence.write_bytes(evidence.read_bytes().replace(b'"host_capacity":', b'"host_capacity": '))
        with self.assertRaises(ValidationError):
            adapters["verify_host_observation"](deepcopy(auth), deepcopy(request))

    def test_backlog_passes_provider_snapshot_reader_with_pr_attribution(self):
        from agentic.planning import tickets_from_provider_snapshot
        runner = PlanningRunner(inventory=[_ticket("tracked.txt")],
                                pulls=[{"number": 7, "head": {"ref": "codex/AWF-32-awf-32", "repo": {"id": 101}}},
                                       {"number": 8, "head": {"ref": "codex/AWF-32-awf-32", "repo": {"id": 999}}}],
                                pull_files={7: ["tracked.txt"]})
        adapters = self.adapters(runner)
        cfg = project_config()
        request = planning_request(cfg, self.pin)
        snapshot = adapters["observe_planning_backlog"](deepcopy(request))
        tickets = tickets_from_provider_snapshot(
            snapshot, cfg, expected_binding=request["binding"], expected_provider=request["provider"],
            now=NOW, verify_provider_snapshot=adapters["verify_planning_backlog"])
        self.assertEqual([(item["key"], item["status"], item["write_paths"], item["open_pr_files"], item["worktree_diff"])
                          for item in tickets], [("AWF-32", "open", ["tracked.txt"], ["tracked.txt"], [])])
        with self.assertRaises(ValidationError):
            self.foreign_adapters()["verify_planning_backlog"](deepcopy(snapshot["authentication"]), deepcopy(request))
        forged = deepcopy(snapshot)
        forged["tickets"][0]["components"][0]["files"] = ["other.txt"]
        with self.assertRaises(ValidationError):
            tickets_from_provider_snapshot(
                forged, cfg, expected_binding=request["binding"], expected_provider=request["provider"],
                now=NOW, verify_provider_snapshot=adapters["verify_planning_backlog"])
        with self.assertRaises(ValidationError):
            adapters["observe_planning_backlog"]({**request, "binding": {**request["binding"], "scope_sha256": "f" * 64}})

    def test_events_match_the_controller_closed_shape_and_verifier(self):
        adapters = self.adapters()
        request = planning_request(project_config(), self.pin)
        observed = adapters["observe_planning_events"](deepcopy(request))
        self.assertEqual(set(observed), {"binding", "observed_at", "events", "authentication"})
        self.assertEqual((observed["binding"], observed["observed_at"], observed["events"]),
                         (request["binding"], NOW, []))
        auth = observed["authentication"]
        self.assertEqual(set(auth), {"evidence_id"})
        verified = adapters["verify_planning_events"](deepcopy(auth), deepcopy(request))
        self.assertEqual(verified, {"status": "VERIFIED", "evidence_id": auth["evidence_id"],
                                    "snapshot": {key: value for key, value in observed.items() if key != "authentication"}})
        with self.assertRaises(ValidationError):
            self.foreign_adapters()["verify_planning_events"](deepcopy(auth), deepcopy(request))
        with self.assertRaises(ValidationError):
            adapters["verify_planning_events"](deepcopy(auth), {**request, "observed_at": "2026-10-09T08:00:01Z"})

    def test_owner_verifier_reauthenticates_an_immutable_github_comment(self):
        from agentic.planning import change_owner_override, owner_override_command, verify_override_state
        cfg = project_config()
        reason = "hold one lane during the release"
        command = owner_override_command(None, "max", 1, reason)
        comment = {"id": 9205, "node_id": "IC_owner_9205", "user": {"id": 7001, "login": "owner"},
                   "created_at": "2026-10-09T07:59:00Z", "updated_at": "2026-10-09T07:59:00Z", "body": command}
        source = {"channel": "github_pr_comment", "comment_id": 9205, "actor_id": 7001, "actor_login": "owner",
                  "created_at": comment["created_at"], "updated_at": comment["updated_at"],
                  "binding": {"current_override_id": None}, "raw_body": command,
                  "raw_body_sha256": sha256(command.encode("utf-8"))}
        adapters = self.adapters(PlanningRunner(comments={9205: comment}))
        state = {}
        change_owner_override(cfg, state, mode="max", value=1, reason=reason, now=NOW,
                              owner_source=source, verify_owner_source=adapters["verify_planning_owner"])
        self.assertEqual(state["planning_override"]["owner_verification"]["source_id"], "github-comment:IC_owner_9205")
        self.assertEqual(verify_override_state(cfg, state, adapters["verify_planning_owner"])["value"], 1)
        edited = self.adapters(PlanningRunner(comments={9205: {**comment, "updated_at": NOW}}))
        with self.assertRaises(ValidationError):
            verify_override_state(cfg, state, edited["verify_planning_owner"])


    def foreign_adapters(self):
        config = deepcopy(self.config)
        config["github"]["project_id"] = "foreign-project"
        return __import__("runpy").run_path(str(ADAPTER))["build_adapters"](
            {**config, "_runner": PlanningRunner(), "_clock": FakeClock()})

    def test_reference_factory_satisfies_cycle_contract(self):
        config = self.root / "load.json"
        config.write_text(json.dumps(self.config), encoding="utf-8")
        adapters, loader = _load_real(ADAPTER, config)
        required = cycle_requirements(CONTROLLER)
        self.assertEqual(13, len(required))
        self.assertEqual(required, set(loader._require_adapters(adapters, required)))

    def test_instantiated_shipped_example_loads_for_cycle(self):
        value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertLessEqual(
            {"executable", "roles", "isolated_home", "windows_sandbox",
             "worktree_root", "run_record_directory"}, set(value["codex"]),
            "shipped example omits required isolation fields")
        # Substitute documented example fields only; never insert a missing key.
        for section in ("codex", "github", "jira", "outbox"):
            for key in list(value[section]):
                if key in self.config[section]:
                    value[section][key] = deepcopy(self.config[section][key])
        config = self.root / "example.json"
        config.write_text(json.dumps(value), encoding="utf-8")
        adapters, loader = _load_real(ADAPTER, config)
        loader._require_adapters(adapters, cycle_requirements(CONTROLLER))

if __name__ == '__main__':
	unittest.main()
