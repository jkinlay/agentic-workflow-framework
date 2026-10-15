from __future__ import annotations
import base64
from copy import deepcopy
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import quote
from urllib.request import Request, urlopen
from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, load_yaml, now_text, sha256, timestamp
from agentic.continuous_controller import validate_ticket_record
MAX_OUTPUT = 1024 * 1024
MAX_PAGES = 20
MAX_SECONDS = 120
MAX_JIRA_PAGE = 100
_CODEX_CONFIG_SOURCE = 'AWF isolated CODEX_HOME/config.toml'
_CODEX_SANDBOX_ERROR = 'Codex sandbox setup failed; config source: ' + _CODEX_CONFIG_SOURCE + '. The user desktop-app config is not inherited; inspect the isolated home and Codex diagnostics for an unexpected user-app file reference.'
_CREDENTIAL_NAME_MARKERS = ('TOKEN', 'PASSWORD', 'PASSWD', 'SECRET', 'API_KEY', 'APIKEY', 'ACCESS_KEY', 'PRIVATE_KEY', 'CREDENTIAL')
_CREDENTIAL_SERVICE_NAMES = ('JIRA', 'ATLASSIAN')
_TICKET_BODY_FIELDS = {'ticket', 'priority', 'disposition', 'actor', 'reason', 'next_action', 'resume_trigger', 'paths', 'dependencies_satisfied', 'budget_available', 'cap_available', 'review_independent', 'exact_tuple', 'activity', 'verification_gate', 'reviewer_completion', 'open_findings', 'jira_status'}
_STAGE_ROLES = {'implementation': 'writer', 'independent-review': 'critic'}

def _require(condition, message):
	if not condition:
		raise ValidationError(message)

def _text(value, name, *, maximum=4096):
	_require(isinstance(value, str) and 0 < len(value) <= maximum and (not any((ord(char) < 32 for char in value))), name + ' is invalid')
	return value

def _absolute(value, name):
	path = Path(_text(value, name))
	_require(path.is_absolute(), name + ' must be absolute')
	return path

class _Clock:

	def now(self):
		return now_text()

	def monotonic(self):
		return time.monotonic()

class _SubprocessRunner:

	def __init__(self):
		self._processes = {}
		self._overlap_operations = {}

	def bind_overlap_operations(self, **operations):
		_require(all((callable(value) for value in operations.values())), 'Production overlap operations are incomplete')
		self._overlap_operations = dict(operations)

	def observe_overlap_conflict(self, gate):
		operation = self._overlap_operations.get('observe_overlap_conflict')
		_require(callable(operation), 'Production overlap conflict observer is unavailable')
		return operation(gate)

	def sync_writer(self, payload):
		operation = self._overlap_operations.get('sync_writer')
		_require(callable(operation), 'Production overlap sync executor is unavailable')
		return operation(payload)

	def observe_overlap_sync_inventory(self, gate):
		operation = self._overlap_operations.get('observe_overlap_sync_inventory')
		_require(callable(operation), 'Production post-sync PR observer is unavailable')
		return operation(gate)
	_WATCHER = '\nimport json\nimport os\nimport subprocess\nimport sys\nimport tempfile\nimport threading\nimport time\n\nspec_path = sys.argv[1]\nwith open(spec_path, "r", encoding="utf-8") as stream:\n    spec = json.load(stream)\n\ndef durable(path, value):\n    directory = os.path.dirname(path) or "."\n    fd, temporary = tempfile.mkstemp(prefix=".awf-durable-", dir=directory)\n    try:\n        with os.fdopen(fd, "w", encoding="utf-8", newline="\\n") as stream:\n            json.dump(value, stream, sort_keys=True, separators=(",", ":"))\n            stream.write("\\n")\n            stream.flush()\n            os.fsync(stream.fileno())\n        os.replace(temporary, path)\n    finally:\n        try:\n            os.unlink(temporary)\n        except FileNotFoundError:\n            pass\n\npid = os.getpid()\nnonce = spec["launch_nonce"]\nhandoff = spec["handoff_path"]\nproof = spec["terminal_proof_path"]\nstartup_proof = spec["startup_proof_path"]\ndurable(handoff, {"pid": pid, "launch_nonce": nonce, "status": "LAUNCHED"})\nstartup_lock = threading.Lock()\nstartup_recorded = False\nsession_id = None\n\ndef publish_startup(value):\n    global startup_recorded\n    with startup_lock:\n        if startup_recorded:\n            return\n        durable(startup_proof, value)\n        startup_recorded = True\n\ncode = None\nstatus = "COMPLETED"\ndiagnostic = None\ntry:\n    child = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=spec["env"],\n                             shell=False, stdin=subprocess.PIPE,\n                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n    stderr_tail = bytearray()\n    stderr_limit = spec["max_stderr_bytes"]\n    markers = ("setup refresh had errors", "helper_unknown_error", "os error 32",\n               "winerror 32", "invalid transport", "being used by another process")\n\n    def drain_stderr():\n        while True:\n            chunk = child.stderr.read1(8192)\n            if not chunk:\n                return\n            stderr_tail.extend(chunk)\n            if len(stderr_tail) > stderr_limit:\n                del stderr_tail[:-stderr_limit]\n            lowered = bytes(stderr_tail).decode("utf-8", "replace").casefold()\n            if any(marker in lowered for marker in markers):\n                publish_startup({"pid": pid, "launch_nonce": nonce,\n                                 "status": "FAILED", "terminal": False,\n                                 "diagnostic": "SANDBOX_SETUP_CONFIG_ERROR",\n                                 "config_source": spec["config_source"]})\n\n    def drain_stdout():\n        global session_id\n        while True:\n            line = child.stdout.readline(spec["max_output_bytes"] + 1)\n            if not line:\n                return\n            if len(line) > spec["max_output_bytes"]:\n                continue\n            try:\n                event = json.loads(line.decode("utf-8", "strict"))\n            except (UnicodeDecodeError, json.JSONDecodeError):\n                continue\n            # Codex JSON mode emits this only after isolated configuration has\n            # been accepted and the execution thread has been initialized.\n            if (isinstance(event, dict) and event.get("type") == "thread.started"\n                    and isinstance(event.get("thread_id"), str) and event["thread_id"].strip()):\n                session_id = event["thread_id"]\n                publish_startup({"pid": pid, "launch_nonce": nonce,\n                                 "status": "STARTED", "terminal": False,\n                                 "session_id": session_id})\n\n    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)\n    stdout_thread = threading.Thread(target=drain_stdout, daemon=True)\n    stderr_thread.start()\n    stdout_thread.start()\n    try:\n        try:\n            child.stdin.write(spec["stdin"].encode("utf-8"))\n        except BrokenPipeError:\n            pass\n        finally:\n            child.stdin.close()\n        child.wait(timeout=spec["timeout"])\n        code = child.returncode\n        stderr_thread.join()\n        stdout_thread.join()\n        lowered = bytes(stderr_tail).decode("utf-8", "replace").casefold()\n        if code and any(marker in lowered for marker in markers):\n            diagnostic = "SANDBOX_SETUP_CONFIG_ERROR"\n    except subprocess.TimeoutExpired:\n        status = "TIMED_OUT"\n        child.kill()\n        child.wait()\n        stderr_thread.join()\n        stdout_thread.join()\nexcept Exception:\n    status = "FAILED"\nvalue = {"pid": pid, "launch_nonce": nonce, "status": status,\n         "returncode": code, "terminal": True}\nif session_id is not None:\n    value["session_id"] = session_id\nif diagnostic is not None:\n    value["diagnostic"] = diagnostic\n    value["config_source"] = spec["config_source"]\ndurable(proof, value)\npublish_startup(value)\n'

	def run(self, argv, *, cwd, env, timeout, max_bytes, stdin=None):
		_require(stdin is None or isinstance(stdin, str), 'External command stdin is invalid')
		input_bytes = None if stdin is None else stdin.encode('utf-8')
		_require(input_bytes is None or len(input_bytes) <= 2 * MAX_OUTPUT, 'External command stdin exceeded its byte bound')
		try:
			result = subprocess.run(argv, cwd=str(cwd), env=env, shell=False,
				stdin=subprocess.DEVNULL if input_bytes is None else None, input=input_bytes,
				capture_output=True, timeout=timeout)
		except (OSError, subprocess.TimeoutExpired) as exc:
			raise ValidationError('External command failed or timed out') from exc
		if result.returncode != 0:
			raise ValidationError('External command returned a non-zero exit')
		if len(result.stdout) > max_bytes or len(result.stderr) > max_bytes:
			raise ValidationError('External command output exceeded its byte bound')
		try:
			return result.stdout.decode('utf-8', 'strict')
		except UnicodeDecodeError as exc:
			raise ValidationError('External command output was not UTF-8') from exc

	def merge_file(self, current, base, other, *, union=False):
		"""Run Git's bounded three-way file merge without touching a repository."""
		_require(all((isinstance(value, bytes) and len(value) <= MAX_OUTPUT
					  for value in (current, base, other))), 'Overlap merge inputs exceed their byte bound')
		with tempfile.TemporaryDirectory(prefix='awf-overlap-merge-') as directory:
			paths = [Path(directory) / name for name in ('current', 'base', 'other')]
			for path, value in zip(paths, (current, base, other)):
				path.write_bytes(value)
			argv = ['git', 'merge-file', '--stdout']
			if union:
				argv.append('--union')
			argv.extend((str(path) for path in paths))
			try:
				result = subprocess.run(argv, cwd=directory, env=_safe_env(), shell=False,
								capture_output=True, timeout=30)
			except (OSError, subprocess.TimeoutExpired) as exc:
				raise ValidationError('Three-way overlap merge failed or timed out') from exc
		_require(result.returncode in {0, 1}, 'Three-way overlap merge could not evaluate the bound file versions')
		_require(len(result.stdout) <= MAX_OUTPUT and len(result.stderr) <= MAX_OUTPUT,
				 'Three-way overlap merge output exceeded its byte bound')
		return {'status': 'MERGEABLE' if result.returncode == 0 else 'CONFLICT',
				'content': result.stdout}

	def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path, launch_nonce):
		_write_record(Path(handoff_path), {'launch_nonce': launch_nonce, 'status': 'STARTING'})
		spec_path = Path(handoff_path).with_suffix('.launch.json')
		terminal_path = Path(terminal_proof_path)
		startup_path = terminal_path.with_name(terminal_path.name.removesuffix('.terminal.json') + '.startup.json')
		_write_record(spec_path, {'argv': list(argv), 'cwd': str(cwd), 'env': dict(env), 'stdin': stdin, 'timeout': timeout, 'max_output_bytes': MAX_OUTPUT, 'max_stderr_bytes': MAX_OUTPUT, 'config_source': _CODEX_CONFIG_SOURCE, 'launch_nonce': launch_nonce, 'handoff_path': str(handoff_path), 'startup_proof_path': str(startup_path), 'terminal_proof_path': str(terminal_proof_path)})
		try:
			process = subprocess.Popen([sys.executable, '-B', '-c', self._WATCHER, str(spec_path)], cwd=str(cwd), env=env, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
		except OSError as exc:
			raise ValidationError('Codex detached launch failed') from exc
		deadline = time.monotonic() + min(timeout, 10)
		handoff = None
		while time.monotonic() <= deadline:
			try:
				candidate = _read_record(Path(handoff_path))
			except ValidationError:
				candidate = None
			if isinstance(candidate, dict) and candidate.get('status') == 'LAUNCHED' and (candidate.get('launch_nonce') == launch_nonce) and (type(candidate.get('pid')) is int) and (candidate['pid'] > 0):
				handoff = candidate
				break
			time.sleep(0.01)
		_require(handoff is not None, 'Codex detached watcher did not produce a durable launch handoff')
		self._processes[handoff['pid']] = process
		return {'pid': handoff['pid'], 'launch_nonce': launch_nonce, 'handoff_path': str(handoff_path), 'startup_proof_path': str(startup_path), 'terminal_proof_path': str(terminal_proof_path), 'status': 'LAUNCHED'}

	def observe(self, record, *, timeout):
		pid = record.get('pid')
		if not isinstance(pid, int) or pid <= 0:
			raise ValidationError('Detached run record has no process identity')
		proof_path = record.get('terminal_proof_path')
		_require(isinstance(proof_path, str), 'Detached run has no terminal-proof path')
		proof = Path(proof_path)
		if not proof.is_file() and self._processes.get(pid) is None:
			raise ValidationError('Detached Codex terminal outcome was not proven')
		deadline = time.monotonic() + timeout + 2
		while not proof.is_file() and time.monotonic() <= deadline:
			process = self._processes.get(pid)
			if process is not None:
				process.poll()
			time.sleep(0.01)
		if not proof.is_file():
			if self._processes.get(pid) is None:
				raise ValidationError('Detached Codex terminal outcome was not proven')
			raise ValidationError('Detached Codex run timed out before durable terminal proof')
		process = self._processes.pop(pid, None)
		if process is not None:
			try:
				process.wait(timeout=2)
			except subprocess.TimeoutExpired:
				pass
		value = _read_record(proof)
		_require(value.get('pid') == pid and value.get('launch_nonce') == record.get('launch_nonce'), 'Detached terminal proof is bound to a different process')
		if value.get('diagnostic') == 'SANDBOX_SETUP_CONFIG_ERROR':
			raise ValidationError(_CODEX_SANDBOX_ERROR)
		if value.get('status') == 'TIMED_OUT':
			raise ValidationError('Detached Codex run timed out')
		_require(value.get('status') == 'COMPLETED' and value.get('terminal') is True, 'Detached Codex terminal outcome was not proven')
		if value.get('returncode') != 0:
			raise ValidationError('Detached Codex run returned a non-zero exit')
		return value

	def observe_startup(self, record, *, timeout):
		pid = record.get('pid')
		if not isinstance(pid, int) or pid <= 0:
			raise ValidationError('Detached run record has no process identity')
		proof_path = record.get('startup_proof_path')
		_require(isinstance(proof_path, str), 'Detached run has no startup-proof path')
		proof = Path(proof_path)
		process = self._processes.get(pid)
		if not proof.is_file() and process is None:
			raise ValidationError('Detached Codex startup outcome was not proven')
		deadline = time.monotonic() + timeout + 2
		while not proof.is_file() and time.monotonic() <= deadline:
			if process is not None:
				process.poll()
			time.sleep(0.01)
		if not proof.is_file():
			raise ValidationError('Detached Codex startup outcome was not proven')
		value = _read_record(proof)
		_require(value.get('pid') == pid and value.get('launch_nonce') == record.get('launch_nonce'), 'Detached startup proof is bound to a different process')
		if value.get('diagnostic') == 'SANDBOX_SETUP_CONFIG_ERROR':
			if process is not None and hasattr(process, 'wait'):
				try:
					process.wait(timeout=2)
				except subprocess.TimeoutExpired:
					pass
				else:
					self._processes.pop(pid, None)
			raise ValidationError(_CODEX_SANDBOX_ERROR)
		if value.get('status') == 'STARTED' and value.get('terminal') is False:
			return {'status': 'RUNNING', 'terminal': False, 'pid': value['pid'], 'launch_nonce': value['launch_nonce'], 'session_id': value.get('session_id')}
		if value.get('terminal') is True:
			return self.observe(record, timeout=0)
		raise ValidationError('Detached Codex startup outcome was not proven')

	def observe_quiescence(self, record):
		pid = record.get('pid')
		proof_path = record.get('terminal_proof_path')
		_require(isinstance(pid, int) and pid > 0 and isinstance(proof_path, str), 'Detached dispatch has no terminal-proof identity')
		proof = Path(proof_path)
		if proof.is_file():
			return self.observe(record, timeout=0)
		process = self._processes.get(pid)
		if process is not None and process.poll() is None:
			return {'status': 'RUNNING', 'terminal': False, 'pid': pid, 'launch_nonce': record.get('launch_nonce'), 'session_id': record.get('session_id')}
		raise ValidationError('Detached dispatch terminal outcome is unobserved')

class _HttpTransport:

	def request(self, method, url, *, headers, body, timeout, max_bytes):
		request = Request(url, data=body, headers=headers, method=method)
		try:
			with urlopen(request, timeout=timeout) as response:
				raw = response.read(max_bytes + 1)
				status = response.status
		except Exception as exc:
			raise ValidationError('Jira HTTP request failed or timed out') from exc
		if len(raw) > max_bytes:
			raise ValidationError('Jira response exceeded its byte bound')
		try:
			return (status, raw.decode('utf-8', 'strict'))
		except UnicodeDecodeError as exc:
			raise ValidationError('Jira response was not UTF-8') from exc

def _invoke(obj, method, *args, **kwargs):
	function = getattr(obj, method, None)
	_require(callable(function), 'Injected ' + method + ' operation is unavailable')
	try:
		return function(*args, **kwargs)
	except ValidationError:
		raise
	except Exception as exc:
		raise ValidationError('Injected external operation failed or timed out') from exc

def _normalize_command_result(value):
	if isinstance(value, str):
		return value
	if isinstance(value, bytes):
		try:
			return value.decode('utf-8', 'strict')
		except UnicodeDecodeError as exc:
			raise ValidationError('External command output was not UTF-8') from exc
	if isinstance(value, dict):
		if value.get('returncode', 0) != 0:
			raise ValidationError('External command returned a non-zero exit')
		output = value.get('stdout', value.get('output'))
		_require(isinstance(output, (str, bytes)), 'External command output is missing')
		return _normalize_command_result(output)
	if isinstance(value, (tuple, list)) and len(value) >= 2:
		if value[0] != 0:
			raise ValidationError('External command returned a non-zero exit')
		return _normalize_command_result(value[1])
	raise ValidationError('External command returned an unparsable result')

def _json_output(value):
	try:
		if isinstance(value, (dict, list)):
			result = value
		else:
			result = json.loads(value)
	except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
		raise ValidationError('External provider output was not valid JSON') from exc
	_require(isinstance(result, (dict, list)), 'External provider output must be a JSON object or array')
	return result

def _clock_text(clock, value=None):
	value = _invoke(clock, 'now') if value is None else value
	timestamp(value)
	return value

def _safe_env(jira_token_env=None):
	result = dict(os.environ)
	for key in tuple(result):
		normalized = key.upper()
		if key == jira_token_env or any((service in normalized for service in _CREDENTIAL_SERVICE_NAMES)) or any((marker in normalized for marker in _CREDENTIAL_NAME_MARKERS)):
			result.pop(key, None)
	result['GH_PROMPT_DISABLED'] = '1'
	result['GIT_TERMINAL_PROMPT'] = '0'
	return result

def _disjoint(first, second):
	first = first.resolve()
	second = second.resolve()
	return first != second and first not in second.parents and (second not in first.parents)

def _private_mode(path, name, maximum):
	if os.name == 'nt':
		return
	try:
		mode = path.stat().st_mode & 511
	except OSError as exc:
		raise ValidationError(name + ' permissions are unavailable') from exc
	_require(mode & ~maximum == 0, name + ' permissions are too broad')

def _single_link(path, name):
	try:
		links = path.stat().st_nlink
	except OSError as exc:
		raise ValidationError(name + ' identity is unavailable') from exc
	_require(links == 1, name + ' must not be hard-linked')

def _managed_codex_config(windows_sandbox):
	return ('approval_policy = "never"\n\n[sandbox_workspace_write]\nnetwork_access = false\n\n[windows]\nsandbox = "' + windows_sandbox + '"\n').encode('utf-8')

def _write_private(path, data):
	temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
	descriptor = None
	try:
		descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
		with os.fdopen(descriptor, 'wb') as stream:
			descriptor = None
			stream.write(data)
			stream.flush()
			os.fsync(stream.fileno())
		os.replace(temporary, path)
	except OSError as exc:
		raise ValidationError('AWF isolated Codex config could not be made durable') from exc
	finally:
		if descriptor is not None:
			os.close(descriptor)
		try:
			temporary.unlink()
		except OSError:
			pass

def _prepare_codex_home(home, windows_sandbox):
	_require(home.is_dir() and (not home.is_symlink()), 'codex.isolated_home must be an existing dedicated directory')
	_private_mode(home, 'codex.isolated_home', 448)
	auth = home / 'auth.json'
	_require(auth.is_file() and (not auth.is_symlink()), 'Codex auth is unavailable in codex.isolated_home/auth.json')
	_single_link(auth, 'Codex isolated auth')
	_private_mode(auth, 'Codex isolated auth', 384)
	try:
		with auth.open('rb') as stream:
			_require(bool(stream.read(1)), 'Codex isolated auth is empty')
	except OSError as exc:
		raise ValidationError('Codex isolated auth is unreadable') from exc
	expected = _managed_codex_config(windows_sandbox)
	config_path = home / 'config.toml'
	if config_path.exists():
		_require(config_path.is_file() and (not config_path.is_symlink()), 'AWF isolated Codex config source is not a regular file')
		_single_link(config_path, 'AWF isolated Codex config source')
		try:
			actual = config_path.read_bytes()
		except OSError as exc:
			raise ValidationError('AWF isolated Codex config source is unreadable') from exc
		_require(actual == expected, 'AWF isolated Codex config source contains non-AWF settings')
	else:
		_write_private(config_path, expected)
	_private_mode(config_path, 'AWF isolated Codex config', 384)

def _sandbox_setup_failure(value):
	lowered = str(value).casefold()
	return any((marker in lowered for marker in ('setup refresh had errors', 'helper_unknown_error', 'os error 32', 'winerror 32', 'invalid transport', 'being used by another process', 'codex sandbox setup failed')))

def _sha256_file(path):
	try:
		digest = sha256(path.read_bytes())
	except (OSError, ValueError) as exc:
		raise ValidationError('Pinned executable is unreadable') from exc
	return digest

def _executable(value, name):
	_require(isinstance(value, dict) and set(value) == {'path', 'sha256'}, name + ' must contain only path and sha256')
	path = _absolute(value['path'], name + '.path')
	_require(re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None, name + '.sha256 must be a lowercase SHA-256')
	_require(path.suffix.lower() not in {'.cmd', '.bat', '.ps1', '.sh'}, name + ' must pin a native executable')
	return {'path': path, 'sha256': value['sha256']}

def _verify_executable(item):
	_require(item['path'].is_file(), 'Pinned executable is missing')
	_require(_sha256_file(item['path']) == item['sha256'], 'Pinned executable hash mismatch')

def _read_record(path):
	try:
		raw = path.read_bytes()
		_require(len(raw) <= MAX_OUTPUT, 'Detached run record exceeded its byte bound')
		value = json.loads(raw.decode('utf-8'))
	except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
		raise ValidationError('Detached run record is unavailable or invalid') from exc
	_require(isinstance(value, dict), 'Detached run record is not an object')
	return value

def _write_record(path, value):
	data = canonical(value)
	_require(len(data) <= MAX_OUTPUT, 'Detached run record exceeded its byte bound')
	temporary = path.with_suffix('.tmp')
	try:
		temporary.write_bytes(data + b'\n')
		os.replace(temporary, path)
	except OSError as exc:
		raise ValidationError('Detached run record could not be made durable') from exc

def _record_receipt(payload, observed_at, session_id, *, reconcile=False):
	fields = ['dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'role', 'lifecycle_stage']
	if reconcile:
		fields += ['reconcile_nonce', 'reconcile_at']
	result = {key: payload[key] for key in fields}
	result.update(status='ACCEPTED', observed_at=observed_at, session_id=session_id)
	return result

def _worktree(root, candidate):
	root = root.resolve()
	_require(root.is_absolute() and root.is_dir(), 'Declared Codex worktree root is unavailable')
	path = (root if candidate is None else _absolute(candidate, 'worktree')).resolve()
	_require(path == root or root in path.parents, 'Codex worktree is outside the declared root')
	_require(path.is_dir(), 'Codex worktree is unavailable')
	return path

def _blocked_ticket_record(record, parsed, role, stage, reason):
	ticket = parsed.get('ticket') if isinstance(parsed, dict) else None
	if not isinstance(ticket, str) or not ticket.strip() or len(ticket) > 256 or any((ord(char) < 32 for char in ticket)):
		ticket = 'github-issue-' + str(record['number'])
	return {'ticket': ticket, 'priority': record['number'], 'disposition': 'BLOCKED', 'actor': 'controller', 'reason': 'Malformed ticket record: ' + reason, 'next_action': 'Correct the named ticket record without granting authority in its body', 'resume_trigger': 'a fresh authenticated inventory contains a valid ticket record', 'paths': [], 'dependencies_satisfied': False, 'budget_available': False, 'cap_available': False, 'review_independent': False, 'exact_tuple': fingerprint('controller-malformed-ticket', {'provider_id': record['id'], 'provider_node_id': record['node_id'], 'body_sha256': hashlib.sha256(str(record.get('body')).encode('utf-8')).hexdigest()}), 'activity': 'repair malformed ticket record', 'verification_gate': 'REFUSED', 'reviewer_completion': {'required': 0, 'completed': 0, 'acceptable': 0, 'failed': 0, 'stale': 0, 'outstanding': 0}, 'open_findings': 1, 'jira_status': 'UNOBSERVED', 'dispatch_role': role, 'lifecycle_stage': stage}

def _validate_ticket_records(items):
	_require(isinstance(items, list) and len(items) <= 1000, 'GitHub inventory is not a bounded list')
	result, provider_ids, node_ids, numbers, tickets = ([], set(), set(), set(), set())
	for record in items:
		_require(isinstance(record, dict) and {'id', 'number', 'node_id', 'state', 'labels', 'body'} <= set(record), 'GitHub inventory provider record is missing authenticated identity')
		_require(type(record['id']) is int and record['id'] > 0 and (type(record['number']) is int) and (record['number'] > 0) and isinstance(record['node_id'], str) and record['node_id'].strip() and (record['state'] == 'open') and ('pull_request' not in record), 'GitHub inventory provider identity or lifecycle state is corrupt')
		_require(record['id'] not in provider_ids and record['node_id'] not in node_ids and (record['number'] not in numbers), 'GitHub inventory contains duplicate provider identities')
		provider_ids.add(record['id'])
		node_ids.add(record['node_id'])
		numbers.add(record['number'])
		labels = record['labels']
		_require(isinstance(labels, list) and all((isinstance(label, dict) and isinstance(label.get('name'), str) for label in labels)), 'GitHub inventory provider labels are corrupt')
		stage_labels = [label['name'][len('awf-stage:'):] for label in labels if label['name'].startswith('awf-stage:')]
		_require(len(stage_labels) == 1 and stage_labels[0] in _STAGE_ROLES, 'GitHub inventory lacks one authenticated lifecycle-stage label')
		label_stage = stage_labels[0]
		stage = label_stage.replace('-', '_')
		role = _STAGE_ROLES[label_stage]
		parsed, problem = (None, None)
		if not isinstance(record['body'], str):
			problem = 'issue body is not JSON text'
		else:
			try:
				parsed = json.loads(record['body'])
			except (ValueError, json.JSONDecodeError):
				problem = 'issue body is not valid JSON'
		if problem is None and (not isinstance(parsed, dict) or set(parsed) != _TICKET_BODY_FIELDS):
			body_fields = set(parsed) if isinstance(parsed, dict) else set()
			reserved = sorted(body_fields & {'code_needed', 'dispatch_role', 'lifecycle_stage', 'role'})
			problem = 'forged body authority field(s) ' + ', '.join(reserved) if reserved else 'issue body does not match the ticket data contract'
		if problem is None:
			ticket = {**parsed, 'dispatch_role': role, 'lifecycle_stage': stage}
			try:
				validate_ticket_record(ticket)
			except (ValidationError, TypeError) as exc:
				problem = str(exc)
		if problem is not None:
			ticket = _blocked_ticket_record(record, parsed, role, stage, problem)
		_require(ticket['ticket'] not in tickets, 'GitHub inventory contains duplicate ticket identities')
		tickets.add(ticket['ticket'])
		result.append(ticket)
	return result

def _page_items(response, *, key, page_size):
	_require(isinstance(response, dict) and set(response) <= {key, 'complete', 'next_page'}, 'Paged GitHub response has an invalid shape')
	items = response.get(key)
	_require(isinstance(items, list), 'Paged GitHub response has no item list')
	complete = response.get('complete')
	next_page = response.get('next_page')
	if complete is True:
		_require(next_page is None, 'Complete GitHub response has a next page')
	elif complete is False:
		_require(isinstance(next_page, int) and next_page > 0, 'Incomplete GitHub response has no next page')
	else:
		raise ValidationError('GitHub response did not prove completeness')
	return (items, complete is True, next_page)

def _append_page(path, page_number, page_size):
	path = re.sub('([?&])per_page=[^&]*', '\\1', path)
	separator = '&' if '?' in path else '?'
	return path + separator + 'page=' + str(page_number) + '&per_page=' + str(page_size)

def _build(config):
	_require(isinstance(config, dict), 'Reference adapter configuration must be an object')
	for key in ('codex', 'github', 'jira', 'outbox'):
		_require(isinstance(config.get(key), dict), 'Reference adapter configuration is missing ' + key)
	codex = config['codex']
	_require(set(codex) >= {'executable', 'roles', 'isolated_home', 'windows_sandbox', 'worktree_root', 'run_record_directory'}, 'Codex adapter configuration is incomplete')
	codex_executable = _executable(codex['executable'], 'codex.executable')
	worktree_root = _absolute(codex['worktree_root'], 'codex.worktree_root')
	record_dir = _absolute(codex['run_record_directory'], 'codex.run_record_directory')
	isolated_home = _absolute(codex['isolated_home'], 'codex.isolated_home').resolve()
	windows_sandbox = _text(codex['windows_sandbox'], 'codex.windows_sandbox')
	_require(windows_sandbox in {'elevated', 'unelevated'}, 'codex.windows_sandbox is forbidden')
	_require(record_dir != worktree_root and worktree_root not in record_dir.parents, 'codex.run_record_directory must be outside codex.worktree_root')
	_require(_disjoint(isolated_home, worktree_root) and _disjoint(isolated_home, record_dir), 'codex.isolated_home must be outside worktrees and run records')
	_require(isolated_home != (Path.home() / '.codex').resolve(), 'codex.isolated_home must not be the user Codex home')
	roles = codex['roles']
	_require(isinstance(roles, dict) and set(roles) == {'writer', 'critic'}, 'Codex roles must define writer and critic')
	role_config = {}
	for role in ('writer', 'critic'):
		value = roles[role]
		_require(isinstance(value, dict) and set(value) == {'model', 'reasoning_effort', 'sandbox'}, 'Codex role configuration is incomplete')
		model = _text(value['model'], role + '.model')
		effort = _text(value['reasoning_effort'], role + '.reasoning_effort')
		sandbox = _text(value['sandbox'], role + '.sandbox')
		_require(sandbox in {'read-only', 'workspace-write'}, role + '.sandbox is forbidden')
		if role == 'critic':
			_require(sandbox == 'read-only', 'Critic sandbox must be read-only')
		role_config[role] = {'model': model, 'reasoning_effort': effort, 'sandbox': sandbox}
	timeout = codex.get('timeout_seconds', 900)
	_require(type(timeout) is int and 1 <= timeout <= 86400, 'Codex timeout is invalid')
	github = config['github']
	required_github = {'host', 'repository', 'repository_id', 'project_id', 'scope_sha256', 'base_branch', 'branch_pattern', 'expected_actor_id', 'auth_profile', 'executable'}
	_require(required_github <= set(github), 'GitHub adapter configuration is incomplete')
	_require(github['host'] == 'https://github.com', 'Only github.com is supported')
	_require(isinstance(github['repository_id'], int) and github['repository_id'] > 0, 'GitHub repository_id must be positive')
	_require(isinstance(github['expected_actor_id'], int) and github['expected_actor_id'] > 0, 'GitHub expected_actor_id must be positive')
	_text(github['auth_profile'], 'github.auth_profile')
	_text(github['repository'], 'github.repository')
	_text(github['project_id'], 'github.project_id')
	_require(re.fullmatch('[0-9a-f]{64}', github['scope_sha256']) is not None, 'github.scope_sha256 must be a lowercase SHA-256')
	_text(github['base_branch'], 'github.base_branch')
	_text(github['branch_pattern'], 'github.branch_pattern')
	gh_executable = _executable(github['executable'], 'github.executable')
	gh_page_size = github.get('page_size', 100)
	_require(type(gh_page_size) is int and 1 <= gh_page_size <= 100, 'GitHub page_size is invalid')
	jira = config['jira']
	required_jira = {'enabled', 'cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id', 'token_env', 'merged_status_id', 'merged_transition_id'}
	_require(required_jira <= set(jira), 'Jira adapter configuration is incomplete')
	_require(type(jira['enabled']) is bool, 'jira.enabled must be boolean')
	for key in ('cloud_id', 'site', 'provider_project_id', 'project_key', 'controller_actor_id', 'merged_status_id', 'merged_transition_id'):
		_text(jira[key], 'jira.' + key)
	token_env = jira['token_env']
	_require(isinstance(token_env, str) and re.fullmatch('[A-Z][A-Z0-9_]{1,127}', token_env) and (token_env not in {'GH_TOKEN', 'GITHUB_TOKEN'}), 'jira.token_env is invalid')
	site = jira['site'].rstrip('/')
	_require(site.startswith('https://') and '@' not in site, 'jira.site must be a credential-free HTTPS URL')
	outbox = config['outbox']
	_require(set(outbox) == {'directory'}, 'outbox configuration must contain only directory')
	outbox_dir = _absolute(outbox['directory'], 'outbox.directory')
	_require(outbox_dir != worktree_root and worktree_root not in outbox_dir.parents, 'outbox.directory must be outside codex.worktree_root')
	_require(_disjoint(isolated_home, outbox_dir), 'codex.isolated_home must be outside the outbox')
	default_runner = config.get('_runner') is None
	runner = config.get('_runner') or _SubprocessRunner()
	http = config.get('_http_transport') or _HttpTransport()
	clock = config.get('_clock') or _Clock()
	_require(callable(getattr(clock, 'now', None)) and callable(getattr(clock, 'monotonic', None)), 'Clock injection is incomplete')

	def observation_evidence():
		value = config.get('observations')
		required = {'accepted_project_config', 'closeout_directory', 'jira_transition_directory'}
		_require(isinstance(value, dict) and set(value) == required, 'Closeout observer evidence is not configured')
		accepted = value['accepted_project_config']
		_require(isinstance(accepted, dict) and set(accepted) == {'path', 'sha256', 'blob_sha', 'acceptance_receipt_id'}, 'Accepted project configuration evidence is incomplete')
		project_path = _absolute(accepted['path'], 'observations.accepted_project_config.path').resolve()
		closeout_directory = _absolute(value['closeout_directory'], 'observations.closeout_directory').resolve()
		jira_directory = _absolute(value['jira_transition_directory'], 'observations.jira_transition_directory').resolve()
		for path, name in ((project_path, 'accepted project configuration'), (closeout_directory, 'closeout evidence'), (jira_directory, 'Jira transition evidence')):
			_require(_disjoint(path, worktree_root), name + ' must be outside codex.worktree_root')
		_require(project_path.is_file(), 'Accepted project configuration evidence is unavailable')
		_require(closeout_directory.is_dir() and jira_directory.is_dir(), 'Closeout observer evidence directories are unavailable')
		_require(re.fullmatch('[0-9a-f]{64}', accepted['sha256']) is not None, 'Accepted project configuration SHA-256 is invalid')
		_require(re.fullmatch('[0-9a-f]{40}', accepted['blob_sha']) is not None, 'Accepted project configuration blob identity is invalid')
		_text(accepted['acceptance_receipt_id'], 'observations.accepted_project_config.acceptance_receipt_id')
		return (accepted, project_path, closeout_directory, jira_directory)

	def evidence_record(directory, record_id, purpose):
		_require(isinstance(record_id, str) and re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', record_id) is not None, purpose + ' record identity is invalid')
		path = (directory / (record_id + '.json')).resolve()
		_require(path.parent == directory, purpose + ' record path escaped its evidence directory')
		try:
			raw = path.read_bytes()
			_require(len(raw) <= MAX_OUTPUT, purpose + ' record exceeded its byte bound')
			value = json.loads(raw.decode('utf-8', 'strict'))
		except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
			raise ValidationError(purpose + ' record is unavailable or invalid') from exc
		_require(isinstance(value, dict), purpose + ' record is not an object')
		return value

	def observe_accepted_project_config(request):
		_require(request == {'operation': 'ACCEPTED_PROJECT_CONFIG', 'source_identity': {'kind': 'installed_project_configuration', 'path': '.agentic/PROJECT_CONFIG.yaml'}}, 'Accepted project configuration request is invalid')
		accepted, project_path, _closeout_directory, _jira_directory = observation_evidence()
		try:
			raw = project_path.read_bytes()
			_require(len(raw) <= 8 * 1024 * 1024, 'Accepted project configuration exceeded its byte bound')
			text = raw.decode('utf-8', 'strict')
			_require(not text.startswith('\ufeff'), 'Accepted project configuration must not contain a UTF-8 BOM')
			project_config = load_yaml(raw)
		except (OSError, UnicodeError, ValueError, TypeError) as exc:
			raise ValidationError('Accepted project configuration bytes are unavailable or invalid') from exc
		_require(sha256(raw) == accepted['sha256'], 'Accepted project configuration bytes differ from their reviewed digest')
		_require(isinstance(project_config, dict), 'Accepted project configuration is not an object')
		accepted_github = project_config.get('github', {})
		_require(accepted_github.get('host') == github['host'] and accepted_github.get('repository') == github['repository'] and (accepted_github.get('repository_id') == github['repository_id']) and (accepted_github.get('base_branch') == github['base_branch']), 'Accepted project configuration differs from adapter repository identity')
		from agentic.contracts import Contracts
		from agentic.lifecycle import definition
		from agentic.policy import validate_config
		contracts = Contracts(Path(__file__).resolve().parents[1] / 'schemas')
		policy_sha256 = validate_config(project_config, definition(), contracts)
		observed_at = _clock_text(clock)
		return {'observed_at': observed_at, 'result': {'raw_bytes_base64': base64.b64encode(raw).decode('ascii'), 'config_sha256': sha256(raw), 'policy_sha256': policy_sha256, 'path': '.agentic/PROJECT_CONFIG.yaml', 'repository': github['repository'], 'repository_id': github['repository_id'], 'ref': 'refs/heads/' + github['base_branch'], 'blob_sha': accepted['blob_sha'], 'acceptance': {'status': 'ACCEPTED', 'receipt_id': accepted['acceptance_receipt_id'], 'workflow_version': project_config.get('template', {}).get('expected_workflow_version')}}}

	def observe_closeout_record(request):
		required = {'operation', 'provider_identity', 'repository_identity', 'source_identity', 'accepted_policy_sha256', 'record_id', 'record_sha256', 'binding', 'pr_number', 'reviewed_head_sha', 'merge_commit_sha', 'gate_record_id', 'review_record_ids'}
		_require(isinstance(request, dict) and set(request) == required and (request.get('operation') == 'CLOSEOUT_RECORD') and (request.get('source_identity') == 'protected_controller_closeout'), 'Closeout observation request is invalid')
		_accepted, _project_path, closeout_directory, _jira_directory = observation_evidence()
		expected_provider = {'host': github['host'].rstrip('/'), 'repository': github['repository'], 'repository_id': github['repository_id']}
		expected_repository = {'repository': github['repository'], 'repository_id': github['repository_id']}
		_require(request['provider_identity'] == expected_provider and request['repository_identity'] == expected_repository, 'Closeout request differs from adapter repository identity')
		value = evidence_record(closeout_directory, request['record_id'], 'Closeout')
		_require(set(value) == {'record', 'merge_result_id', 'merge_observed_at'}, 'Closeout evidence has a non-closed shape')
		record = value['record']
		_require(isinstance(record, dict) and fingerprint('closeout_record', record) == request['record_sha256'] and (record.get('record_id') == request['record_id']) and (record.get('binding') == request['binding']) and (record.get('binding', {}).get('policy_hash') == request['accepted_policy_sha256']) and (record.get('pr_number') == request['pr_number']) and (record.get('reviewed_head_sha') == request['reviewed_head_sha']) and (record.get('merge_commit_sha') == request['merge_commit_sha']) and (record.get('gate_record_id') == request['gate_record_id']) and (record.get('review_record_ids') == request['review_record_ids']), 'Closeout evidence differs from the exact requested record')
		timestamp(value['merge_observed_at'])
		_text(value['merge_result_id'], 'Closeout merge result identity')
		return {'observed_at': _clock_text(clock), 'result': value}

	def observe_jira_reconciliation(request):
		required = {'operation', 'provider_identity', 'source_identity', 'binding', 'issue_id', 'transition_record_id', 'expected_status_id'}
		_require(isinstance(request, dict) and set(request) == required and (request.get('operation') == 'JIRA_RECONCILIATION') and (request.get('source_identity') == 'jira_issue_transition_and_readback'), 'Jira reconciliation observation request is invalid')
		_accepted, _project_path, _closeout_directory, jira_directory = observation_evidence()
		provider = provider_identity()
		_require(request['provider_identity'] == provider, 'Jira reconciliation request differs from observed provider identity')
		value = evidence_record(jira_directory, request['transition_record_id'], 'Jira transition')
		_require(set(value) == {'record'} and isinstance(value['record'], dict), 'Jira transition evidence has a non-closed shape')
		record = value['record']
		_require(record.get('record_id') == request['transition_record_id'] and record.get('binding') == request['binding'] and (record.get('binding', {}).get('issue_id') == request['issue_id']) and (record.get('jira_provider') == provider) and (record.get('producer_id') == provider['controller_actor_id']) and (record.get('lifecycle_event') == 'JIRA_RECONCILED') and (record.get('status') == 'SUCCEEDED') and (record.get('to_status_id') == request['expected_status_id']), 'Jira transition evidence differs from the requested reconciliation')
		issue = jira_json('GET', '/rest/api/3/issue/' + quote(request['issue_id'], safe='') + '?fields=status&expand=changelog')
		_require(issue.get('id') == request['issue_id'], 'Jira reconciliation readback returned a different issue')
		current_status = issue.get('fields', {}).get('status', {}).get('id')
		_require(current_status == request['expected_status_id'], 'Jira issue is not currently at the configured terminal status')
		read_back = record.get('read_back')
		_require(isinstance(read_back, dict) and read_back.get('observed_status') == current_status and (read_back.get('observed_actor') == provider['controller_actor_id']) and isinstance(read_back.get('observed_at'), str), 'Jira transition record lacks the matching terminal readback')
		matched_history = False
		for history in issue.get('changelog', {}).get('histories', []):
			if not isinstance(history, dict) or not isinstance(history.get('created'), str):
				continue
			actor = history.get('author', {}).get('accountId')
			if actor != provider['controller_actor_id']:
				continue
			for item in history.get('items', []):
				if isinstance(item, dict) and item.get('field') == 'status' and (item.get('to') == current_status or item.get('toString') == current_status) and (timestamp(record['created_at']) <= timestamp(history['created']) <= timestamp(read_back['observed_at'])):
					matched_history = True
		_require(matched_history, 'Jira provider history does not prove the protected reconciliation')
		observed_at = _clock_text(clock)
		_require(timestamp(observed_at) >= timestamp(read_back['observed_at']), 'Jira reconciliation observation predates its protected readback')
		return {'observed_at': observed_at, 'result': value}

	def command(executable, args, *, cwd=None, stdin=None, timeout_override=None):
		_verify_executable(executable)
		argv = [str(executable['path']), *[str(item) for item in args]]
		_require(all((isinstance(item, str) and item for item in argv)), 'External argv is invalid')
		env = _safe_env(token_env)
		kwargs = {}
		if stdin is not None:
			_require(isinstance(stdin, str) and len(stdin.encode('utf-8')) <= 2 * MAX_OUTPUT, 'External command stdin exceeded its byte bound')
			env['AWF_ADAPTER_STDIN_SHA256'] = hashlib.sha256(stdin.encode('utf-8')).hexdigest()
			kwargs['stdin'] = stdin
		result = _invoke(runner, 'run', argv, cwd=cwd or worktree_root, env=env, timeout=timeout if timeout_override is None else timeout_override, max_bytes=MAX_OUTPUT, **kwargs)
		if isinstance(result, (dict, list)):
			return result
		return _normalize_command_result(result)

	def gh_json(path, *, paginate=False):
		_text(path, 'GitHub API path', maximum=2048)
		_require('..' not in path and (not path.startswith('http')), 'GitHub API path is unsafe')
		args = ['api', '--hostname', 'github.com', '--method', 'GET']
		if paginate:
			args += ['--paginate', '--slurp']
		output = command(gh_executable, [*args, path], timeout_override=MAX_SECONDS if paginate else None)
		return _json_output(output)

	def gh_json_etag(path, *, compare=False):
		_text(path, 'GitHub API path', maximum=2048)
		compare_path = re.fullmatch('repos/' + re.escape(github['repository']) +
								'/compare/[0-9a-f]{40}\\.\\.\\.[0-9a-f]{40}', path) is not None
		_require(not path.startswith('http') and ((compare and compare_path) or (not compare and '..' not in path)),
				 'GitHub API path is unsafe')
		output = command(gh_executable, ['api', '--hostname', 'github.com', '--method', 'GET', '--include', path])
		if isinstance(output, dict) and set(output) == {'headers', 'body'}:
			headers, body = (output['headers'], output['body'])
			_require(isinstance(headers, dict), 'GitHub provider headers are malformed')
			lowered = {str(key).casefold(): value for key, value in headers.items()}
			value = _json_output(body)
		else:
			_require(isinstance(output, str), 'GitHub provider response with headers is unavailable')
			normalized = output.replace('\r\n', '\n')
			blocks = normalized.split('\n\n')
			_require(len(blocks) >= 2, 'GitHub provider headers are unavailable')
			header_index = max((index for index, block in enumerate(blocks[:-1]) if block.startswith('HTTP/')))
			header_lines = blocks[header_index].splitlines()[1:]
			lowered = {}
			for line in header_lines:
				if ':' in line:
					key, header_value = line.split(':', 1)
					lowered[key.strip().casefold()] = header_value.strip()
			value = _json_output('\n\n'.join(blocks[header_index + 1:]))
		etag = lowered.get('etag')
		date = lowered.get('date')
		_require(isinstance(etag, str) and etag.strip() and isinstance(date, str), 'GitHub provider Date/ETag freshness proof is missing')
		try:
			provider_date = parsedate_to_datetime(date)
			_require(provider_date.tzinfo is not None, 'GitHub provider Date is not timezone-aware')
			provider_time = provider_date.isoformat().replace('+00:00', 'Z')
			timestamp(provider_time)
		except (TypeError, ValueError, OverflowError) as exc:
			raise ValidationError('GitHub provider Date is invalid') from exc
		return (value, etag, provider_time)

	def github_pages(endpoint, key):
		endpoint = _text(endpoint, 'GitHub endpoint', maximum=2048)
		started = _invoke(clock, 'monotonic')
		values, page = ([], 1)
		for _ in range(MAX_PAGES):
			_require(_invoke(clock, 'monotonic') - started <= MAX_SECONDS, 'GitHub pagination exceeded its time bound')
			response = gh_json(_append_page(endpoint, page, gh_page_size), paginate=True)
			if isinstance(response, list):
				_require(len(response) <= MAX_PAGES, 'GitHub pagination exceeded its page bound')
				_require(all((isinstance(part, list) for part in response)), 'GitHub response did not include pagination evidence')
				values = []
				for part in response:
					values.extend(part)
				_require(len(values) <= 10000, 'GitHub observation exceeded its item bound')
				_require(_invoke(clock, 'monotonic') - started <= MAX_SECONDS, 'GitHub pagination exceeded its time bound')
				return values
			items, complete, next_page = _page_items(response, key=key, page_size=gh_page_size)
			values.extend(items)
			_require(len(values) <= 10000, 'GitHub observation exceeded its item bound')
			if complete:
				_require(_invoke(clock, 'monotonic') - started <= MAX_SECONDS, 'GitHub pagination exceeded its time bound')
				return values
			page = next_page or page + 1
		raise ValidationError('GitHub pagination did not prove completeness')

	def observe_inventory(controller_now=None):
		repository = gh_json('repos/' + github['repository'])
		_require(type(repository.get('id')) is int and repository['id'] == github['repository_id'] and (repository.get('full_name', '').casefold() == github['repository'].casefold()), 'GitHub inventory repository identity mismatch')
		inventory_endpoint = github.get('inventory_endpoint', 'repos/' + github['repository'] + '/issues?state=open')
		expected_prefix = 'repos/' + github['repository'] + '/issues'
		_require(inventory_endpoint == expected_prefix or inventory_endpoint.startswith(expected_prefix + '?'), 'GitHub inventory endpoint is outside the configured repository scope')
		records = github_pages(inventory_endpoint, 'items')
		tickets = _validate_ticket_records(records)
		binding = {'project_id': github['project_id'], 'repository_id': str(github['repository_id']), 'scope_sha256': github['scope_sha256']}
		observed_at = _clock_text(clock, controller_now)
		return {'source': 'host_observation', 'observed_at': observed_at, 'binding': binding, 'complete': True, 'inventory_sha256': fingerprint('controller-inventory', {'binding': binding, 'tickets': tickets}), 'tickets': tickets}

	def observe_publication(item):
		_require(isinstance(item, dict) and isinstance(item.get('ticket'), str), 'Publication observation needs a ticket')
		ticket = item['ticket']
		slug = re.sub('[^a-z0-9]+', '-', ticket.lower()).strip('-')
		branch = github['branch_pattern'].format(ticket=ticket, slug=slug)
		repo = gh_json('repos/' + github['repository'])
		actor = gh_json('user')
		permission = gh_json('repos/' + github['repository'] + '/collaborators/' + quote(str(actor.get('login')), safe='') + '/permission')
		auth = _json_output(command(gh_executable, ['auth', 'status', '--hostname', 'github.com', '--json', 'hosts']))
		hosts = auth.get('hosts') if isinstance(auth, dict) else None
		_require(isinstance(hosts, dict) and isinstance(hosts.get('github.com'), list), 'GitHub authentication profile observation is incomplete')
		active = [entry for entry in hosts['github.com'] if isinstance(entry, dict) and entry.get('active') is True]
		_require(len(active) == 1, 'GitHub authentication profile is not uniquely observed')
		auth_profile = active[0].get('profile') or active[0].get('login')
		_require(isinstance(auth_profile, str) and auth_profile, 'GitHub authentication profile is missing')
		rules_endpoint = github.get('rules_endpoint', 'repos/' + github['repository'] + '/rulesets?includes_parents=true')
		expected_rules_endpoint = 'repos/' + github['repository'] + '/rulesets'
		folded_rules_endpoint = rules_endpoint.casefold() if isinstance(rules_endpoint, str) else ''
		folded_expected_endpoint = expected_rules_endpoint.casefold()
		_require(folded_rules_endpoint == folded_expected_endpoint or folded_rules_endpoint.startswith(folded_expected_endpoint + '?'), 'GitHub rules endpoint is outside the configured repository scope')
		rulesets = github_pages(rules_endpoint, 'items')
		_require(type(repo.get('id')) is int and repo['id'] == github['repository_id'] and (repo.get('full_name', '').casefold() == github['repository'].casefold()), 'GitHub publication repository identity mismatch')
		_require(type(actor.get('id')) is int and isinstance(actor.get('login'), str), 'GitHub actor observation is incomplete')
		permission_name = permission.get('permission')
		allowed = permission_name in {'push', 'maintain', 'admin'}
		ref = 'refs/heads/' + branch
		applicable = []
		for ruleset in rulesets:
			if not isinstance(ruleset, dict) or ruleset.get('enforcement') != 'active':
				continue
			conditions = ruleset.get('conditions', {}).get('ref_name', {})
			includes = conditions.get('include', []) if isinstance(conditions, dict) else []
			excludes = conditions.get('exclude', []) if isinstance(conditions, dict) else []
			_require(isinstance(includes, list) and isinstance(excludes, list), 'GitHub ruleset ref conditions are malformed')
			matches = any((pattern == '~ALL' or (pattern == '~DEFAULT_BRANCH' and ref == 'refs/heads/' + github['base_branch']) or pattern in {branch, ref} or __import__('fnmatch').fnmatchcase(ref, pattern) or __import__('fnmatch').fnmatchcase(branch, pattern) for pattern in includes if isinstance(pattern, str)))
			excluded = any((__import__('fnmatch').fnmatchcase(ref, pattern) or __import__('fnmatch').fnmatchcase(branch, pattern) for pattern in excludes if isinstance(pattern, str)))
			if matches and (not excluded):
				applicable.append(ruleset)
		rules_state = 'ALLOWED' if applicable else 'UNOBSERVED'
		rules_evidence = 'Observed active GitHub ruleset(s) applicable to prospective ' + ref if applicable else 'No complete applicable ruleset observation for prospective ' + ref
		observed_at = _clock_text(clock)
		identity = {'actor_id': actor['id'], 'actor_login': actor['login'], 'auth_profile': auth_profile, 'repository_id': repo['id'], 'repository': github['repository'], 'readable': True, 'protocol': github.get('protocol', 'https'), 'observed_at': observed_at}
		return {'format': 'awf-publication-readiness-1', 'source': 'host_observation', 'host': github['host'], 'observed_at': observed_at, 'ticket': ticket, 'slug': slug, 'branch': branch, 'identity': identity, 'authenticated': True, 'remote_reachable': True, 'rules': {'state': rules_state, 'evidence': rules_evidence}, 'push_permitted': allowed, 'draft_pr_permitted': allowed}

	def observe_overlap_allowance(request, phase):
		from agentic.continuous_controller import overlap_allowance_command
		_require(isinstance(request, dict) and phase in {'PLAN', 'LANE_START'}, 'Overlap allowance observation request is invalid')
		_require(str(request.get('repository_id')) == str(github['repository_id']), 'Overlap allowance request names a foreign repository')
		overlap_allowance_command(request)
		base = 'repos/' + github['repository']
		comment, comment_etag, comment_time = gh_json_etag(base + '/issues/comments/' + str(request['comment_id']))
		_require(isinstance(comment, dict), 'GitHub overlap approval comment is malformed')
		observed_prs, resource_etags, resource_times = ([], [comment_etag], [comment_time])
		candidates = {}
		for expected in request['pull_requests']:
			slug = re.sub('[^a-z0-9]+', '-', expected['ticket'].lower()).strip('-')
			branch = github['branch_pattern'].format(ticket=expected['ticket'], slug=slug)
			_require(branch not in candidates, 'Overlap allowance ticket branches are not unique')
			candidates[branch] = expected['ticket']
		for expected in request['pull_requests']:
			number = expected['number']
			pr, pr_etag, pr_time = gh_json_etag(base + '/pulls/' + str(number))
			_require(isinstance(pr, dict) and type(pr.get('number')) is int and isinstance(pr.get('head'), dict) and isinstance(pr.get('base'), dict), 'GitHub overlap pull request is malformed')
			changed_count = pr.get('changed_files')
			_require(type(changed_count) is int and 1 <= changed_count <= 3000, 'GitHub overlap changed-file count is invalid')
			files, file_etags, file_times = ([], [], [])
			for page in range(1, (changed_count + 99) // 100 + 1):
				page_value, page_etag, page_time = gh_json_etag(base + '/pulls/' + str(number) + '/files?per_page=100&page=' + str(page))
				_require(isinstance(page_value, list) and all((isinstance(item, dict) and isinstance(item.get('filename'), str) for item in page_value)), 'GitHub overlap changed-file page is malformed')
				files.extend((item['filename'] for item in page_value))
				file_etags.append(page_etag)
				file_times.append(page_time)
			_require(len(files) == changed_count and len(files) == len(set(files)), 'GitHub overlap changed-file observation is incomplete or duplicated')
			head = pr['head']
			base_ref = pr['base']
			head_repo = head.get('repo') if isinstance(head.get('repo'), dict) else {}
			base_repo = base_ref.get('repo') if isinstance(base_ref.get('repo'), dict) else {}
			head_ref = head.get('ref')
			ticket = candidates.get(head_ref)
			_require(ticket is not None, 'GitHub overlap PR head does not prove ticket ownership')
			resource_etags.extend([pr_etag, *file_etags])
			resource_times.extend([pr_time, *file_times])
			observed_prs.append({'number': pr['number'], 'ticket': ticket, 'head_sha': head.get('sha'), 'changed_files': sorted(files), 'state': str(pr.get('state', '')).upper(), 'merged': pr.get('merged') is True, 'head_repository_id': head_repo.get('id'), 'base_repository_id': base_repo.get('id'), 'head_ref': head_ref, 'etag': fingerprint('github-overlap-pr-etags', [pr_etag, *file_etags]), 'provider_observed_at': min([pr_time, *file_times])})
		observed_prs.sort(key=lambda item: item['number'])
		return {'source': 'github.com', 'repository_id': github['repository_id'], 'provider_observed_at': min(resource_times), 'observation_etag': fingerprint('github-overlap-observation-etags', resource_etags), 'comment': {'id': comment.get('id'), 'node_id': comment.get('node_id'), 'actor_id': comment.get('user', {}).get('id'), 'actor_login': comment.get('user', {}).get('login'), 'author_association': comment.get('author_association'), 'created_at': comment.get('created_at'), 'updated_at': comment.get('updated_at'), 'body': comment.get('body'), 'etag': comment_etag, 'provider_observed_at': comment_time}, 'pull_requests': observed_prs}

	def live_overlap_heads(gate, expected_heads=None):
		required = {'gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads'}
		_require(isinstance(gate, dict) and required <= set(gate) and (str(gate['repository_id']) == str(github['repository_id'])) and isinstance(gate['tickets'], list) and (len(gate['tickets']) == 2) and isinstance(gate['pull_requests'], list) and (len(gate['pull_requests']) == 2), 'Overlap synchronization gate is malformed or names a foreign repository')
		base = 'repos/' + github['repository']
		heads, etags, times = ([], [], [])
		for number, ticket in zip(gate['pull_requests'], gate['tickets']):
			pr, etag, provider_time = gh_json_etag(base + '/pulls/' + str(number))
			slug = re.sub('[^a-z0-9]+', '-', ticket.lower()).strip('-')
			expected_ref = github['branch_pattern'].format(ticket=ticket, slug=slug)
			head = pr.get('head') if isinstance(pr, dict) else None
			head_repo = head.get('repo') if isinstance(head, dict) else None
			base_ref = pr.get('base') if isinstance(pr, dict) else None
			base_repo = base_ref.get('repo') if isinstance(base_ref, dict) else None
			sha = head.get('sha') if isinstance(head, dict) else None
			_require(isinstance(pr, dict) and type(pr.get('number')) is int and (pr['number'] == number) and isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) and (head.get('ref') == expected_ref) and isinstance(head_repo, dict) and (head_repo.get('id') == github['repository_id']) and isinstance(base_repo, dict) and (base_repo.get('id') == github['repository_id']) and (str(pr.get('state', '')).casefold() == 'open') and (pr.get('merged') is not True), 'Live overlap pull request identity or ticket ownership changed')
			heads.append(sha)
			etags.append(etag)
			times.append(provider_time)
		if expected_heads is not None:
			_require(heads == expected_heads, 'Live overlap pull-request heads differ from the durable gate')
		return {'heads': heads, 'etags': etags, 'times': times}

	def overlap_merge_inputs(gate, expected_heads):
		"""Observe the exact heads, merge base, blobs, and bytes for a three-way decision."""
		live = live_overlap_heads(gate, expected_heads)
		base = 'repos/' + github['repository']
		compare, etag, provider_time = gh_json_etag(
			base + '/compare/' + live['heads'][1] + '...' + live['heads'][0], compare=True)
		merge_base = compare.get('merge_base_commit') if isinstance(compare, dict) else None
		merge_base_sha = merge_base.get('sha') if isinstance(merge_base, dict) else None
		_require(isinstance(merge_base_sha, str) and re.fullmatch('[0-9a-f]{40}', merge_base_sha),
				 'Overlap comparison did not prove a merge-base commit')
		live['etags'].append(etag)
		live['times'].append(provider_time)
		versions, blobs = ({}, {})
		for ref in (merge_base_sha, *live['heads']):
			content, content_etag, content_time = gh_json_etag(
				base + '/contents/' + quote(gate['path'], safe='/') + '?ref=' + ref)
			_require(isinstance(content, dict) and content.get('type') == 'file' and
					 isinstance(content.get('sha'), str) and re.fullmatch('[0-9a-f]{40}', content['sha']) and
					 content.get('encoding') == 'base64' and isinstance(content.get('content'), str),
					 'Overlap path content observation is incomplete')
			try:
				raw = base64.b64decode(''.join(content['content'].split()), validate=True)
			except (ValueError, TypeError) as exc:
				raise ValidationError('Overlap path content is not canonical base64') from exc
			_require(len(raw) <= MAX_OUTPUT, 'Overlap path content exceeded its byte bound')
			versions[ref], blobs[ref] = (raw, content['sha'])
			live['etags'].append(content_etag)
			live['times'].append(content_time)
		return {'live': live, 'merge_base': merge_base_sha, 'versions': versions, 'blobs': blobs}

	def production_overlap_conflict(gate):
		inputs = overlap_merge_inputs(gate, gate['heads'])
		live, heads = inputs['live'], inputs['live']['heads']
		if inputs['blobs'][heads[0]] == inputs['blobs'][heads[1]]:
			status = 'NO_CONFLICT'
		else:
			merged = _invoke(runner, 'merge_file', inputs['versions'][heads[1]],
							 inputs['versions'][inputs['merge_base']], inputs['versions'][heads[0]], union=False)
			_require(isinstance(merged, dict) and set(merged) == {'status', 'content'} and
					 merged['status'] in {'MERGEABLE', 'CONFLICT'} and isinstance(merged['content'], bytes),
					 'Three-way overlap merge returned a malformed decision')
			status = 'NO_CONFLICT' if merged['status'] == 'MERGEABLE' else 'CONFIRMED_CONFLICT'
		return {key: deepcopy(gate[key]) for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads')} | {'status': status, 'provider_observed_at': min(live['times']), 'etag': fingerprint('github-overlap-conflict-etags', live['etags'])}

	def production_overlap_sync_inventory(gate):
		live = live_overlap_heads(gate)
		return {key: deepcopy(gate[key]) for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests')} | {'heads': live['heads'], 'provider_observed_at': min(live['times']), 'etag': fingerprint('github-overlap-post-sync-etags', live['etags'])}

	def production_sync_writer(payload):
		required = {'gate_id', 'execution_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads', 'conflict_sha256'}
		_require(isinstance(payload, dict) and set(payload) == required and re.fullmatch('[0-9a-f]{64}', payload.get('execution_id', '')) and (str(payload['repository_id']) == str(github['repository_id'])), 'Overlap sync execution payload is malformed')
		record_dir.mkdir(parents=True, exist_ok=True)
		record_path = record_dir / ('overlap-sync-' + payload['execution_id'] + '.json')
		if record_path.exists():
			retained = _read_record(record_path)
			_require(retained.get('payload') == payload, 'Overlap sync execution identity was replayed for another payload')
			if retained.get('status') == 'COMPLETED':
				return retained['receipt']
			_require(retained.get('status') == 'PREPARED', 'Overlap sync side effect is unresolved and will not be reissued')
		else:
			_write_record(record_path, {'format': 'awf-overlap-sync-operation-1', 'status': 'PREPARED', 'payload': payload})
		gate = {key: deepcopy(payload[key]) for key in ('gate_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads')}
		inputs = overlap_merge_inputs(gate, payload['heads'])
		before = inputs['live']
		confirmed = _invoke(runner, 'merge_file', inputs['versions'][payload['heads'][1]],
						  inputs['versions'][inputs['merge_base']], inputs['versions'][payload['heads'][0]], union=False)
		_require(isinstance(confirmed, dict) and confirmed.get('status') == 'CONFLICT',
				 'Overlap sync executor requires a current genuine three-way conflict')
		resolved = _invoke(runner, 'merge_file', inputs['versions'][payload['heads'][1]],
						 inputs['versions'][inputs['merge_base']], inputs['versions'][payload['heads'][0]], union=True)
		_require(isinstance(resolved, dict) and set(resolved) == {'status', 'content'} and
				 resolved['status'] in {'MERGEABLE', 'CONFLICT'} and isinstance(resolved['content'], bytes) and
				 resolved['content'], 'Overlap sync resolver did not return reviewable resolved bytes')
		target_ticket = payload['tickets'][1]
		target_slug = re.sub('[^a-z0-9]+', '-', target_ticket.lower()).strip('-')
		target_ref = github['branch_pattern'].format(ticket=target_ticket, slug=target_slug)
		_write_record(record_path, {'format': 'awf-overlap-sync-operation-1', 'status': 'IN_FLIGHT', 'payload': payload})
		mutation = 'mutation($repository:String!,$branch:String!,$head:GitObjectID!,$path:String!,$contents:Base64String!,$message:String!){createCommitOnBranch(input:{branch:{repositoryNameWithOwner:$repository,branchName:$branch},expectedHeadOid:$head,message:{headline:$message},fileChanges:{additions:[{path:$path,contents:$contents}]}}){commit{oid}}}'
		mutation_input = json.dumps({'query': mutation, 'variables': {
			'repository': github['repository'], 'branch': target_ref, 'head': payload['heads'][1],
			'path': payload['path'], 'contents': base64.b64encode(resolved['content']).decode('ascii'),
			'message': 'AWF overlap sync ' + payload['execution_id']}}, sort_keys=True, separators=(',', ':'))
		update = _json_output(command(gh_executable,
			['api', '--hostname', 'github.com', 'graphql', '--input', '-'], stdin=mutation_input))
		created = update.get('data', {}).get('createCommitOnBranch', {}) if isinstance(update, dict) else {}
		merge_sha = created.get('commit', {}).get('oid') if isinstance(created, dict) else None
		_require(isinstance(merge_sha, str) and re.fullmatch('[0-9a-f]{40}', merge_sha), 'GitHub overlap resolution did not return a commit identity')
		after = live_overlap_heads(gate)
		_require(after['heads'][0] == before['heads'][0] and after['heads'][1] == merge_sha and (after['heads'] != before['heads']), 'GitHub overlap merge readback differs from the decided heads')
		compare_path = 'repos/' + github['repository'] + '/compare/' + before['heads'][1] + '...' + after['heads'][1]
		compare = _json_output(command(gh_executable, ['api', '--hostname', 'github.com', '--method', 'GET', compare_path]))
		files = compare.get('files') if isinstance(compare, dict) else None
		_require(isinstance(files, list) and 0 < len(files) < 300, 'Overlap sync comparison does not prove a complete bounded diff')
		normalized = []
		for item in files:
			name = item.get('filename') if isinstance(item, dict) else None
			patch_text = item.get('patch') if isinstance(item, dict) else None
			_require(isinstance(name, str) and isinstance(patch_text, str) and patch_text.strip(), 'Overlap sync comparison contains an unreviewable file')
			normalized.append((name, patch_text))
		normalized.sort(key=lambda item: item[0])
		changed_files = [item[0] for item in normalized]
		_require(len(changed_files) == len(set(changed_files)) and payload['path'] in changed_files, 'Overlap sync diff does not bind the shared path')
		diff = ''.join(('diff --git a/' + name + ' b/' + name + '\n' + patch_text.rstrip('\n') + '\n' for name, patch_text in normalized))
		diff_bytes = diff.encode('utf-8')
		_require(len(diff_bytes) <= 512 * 1024, 'Overlap sync diff exceeded its byte bound')
		receipt = {**payload, 'status': 'COMPLETED', 'post_sync_heads': after['heads'], 'diff': diff, 'diff_sha256': hashlib.sha256(diff_bytes).hexdigest(), 'changed_files': changed_files, 'observed_at': _clock_text(clock)}
		_write_record(record_path, {'format': 'awf-overlap-sync-operation-1', 'status': 'COMPLETED', 'payload': payload, 'receipt': receipt})
		return receipt

	def reconcile_overlap_sync(payload):
		required = {'gate_id', 'execution_id', 'repository_id', 'cycle_id', 'path', 'tickets', 'pull_requests', 'heads', 'conflict_sha256'}
		_require(isinstance(payload, dict) and set(payload) == required and re.fullmatch('[0-9a-f]{64}', payload.get('execution_id', '')) and (str(payload['repository_id']) == str(github['repository_id'])), 'Overlap sync reconciliation payload is malformed')
		record_path = record_dir / ('overlap-sync-' + payload['execution_id'] + '.json')
		_require(record_path.is_file(), 'Overlap sync reconciliation has no retained executor record')
		retained = _read_record(record_path)
		_require(retained.get('format') == 'awf-overlap-sync-operation-1' and retained.get('payload') == payload, 'Overlap sync reconciliation identity differs from the retained operation')
		_require(retained.get('status') == 'COMPLETED' and isinstance(retained.get('receipt'), dict), 'Overlap sync reconciliation has no completed receipt and will not reissue the mutation')
		return deepcopy(retained['receipt'])
	if default_runner:
		runner.bind_overlap_operations(observe_overlap_conflict=production_overlap_conflict, sync_writer=production_sync_writer, observe_overlap_sync_inventory=production_overlap_sync_inventory)

	def observe_overlap_conflict(gate):
		return _invoke(runner, 'observe_overlap_conflict', deepcopy(gate))

	def sync_writer(payload):
		return _invoke(runner, 'sync_writer', deepcopy(payload))

	def observe_overlap_sync_inventory(gate):
		if callable(getattr(runner, 'observe_overlap_sync_inventory', None)):
			return _invoke(runner, 'observe_overlap_sync_inventory', deepcopy(gate))
		return production_overlap_sync_inventory(deepcopy(gate))

	def dispatch_ticket(payload):
		_require(isinstance(payload, dict), 'Dispatch payload is missing')
		required = {'dispatch_id', 'stream', 'ticket', 'exact_tuple', 'dispatch_nonce', 'prepared_at', 'begun_at', 'paths', 'actor', 'next_action', 'role', 'lifecycle_stage'}
		_require(required <= set(payload), 'Dispatch payload is incomplete')
		role = payload['role']
		_require(role in role_config or role == 'probe', 'Dispatch role is not configured')
		expected_stage = 'implementation' if role == 'writer' else 'independent_review'
		_require(payload['lifecycle_stage'] == expected_stage, 'Dispatch role differs from authenticated lifecycle stage')
		worktree = _worktree(worktree_root, payload.get('worktree'))
		_prepare_codex_home(isolated_home, windows_sandbox)
		record_dir.mkdir(parents=True, exist_ok=True)
		record_path = record_dir / (payload['dispatch_id'] + '.json')
		if record_path.exists():
			raise ValidationError('Dispatch already has a durable run record; reconcile by observation')
		selected = role_config['critic' if role == 'probe' else role]
		sandbox = 'read-only' if role in {'critic', 'probe'} else selected['sandbox']
		prompt = 'AWF continuous-controller role=' + role + '\nTicket: ' + payload['ticket'] + '\nAction: ' + payload['next_action'] + '\nPaths: ' + ','.join(payload['paths'])
		if role == 'critic' and payload.get('overlap_sync_evidence') is not None:
			overlap_evidence = payload['overlap_sync_evidence']
			_require(isinstance(overlap_evidence, list) and overlap_evidence, 'Critic overlap-sync evidence must be a nonempty list')
			for item in overlap_evidence:
				diff = item.get('diff') if isinstance(item, dict) else None
				diff_bytes = diff.encode('utf-8') if isinstance(diff, str) else b''
				heads = item.get('post_sync_heads') if isinstance(item, dict) else None
				_require(isinstance(diff, str) and diff.strip() and (item.get('diff_sha256') == hashlib.sha256(diff_bytes).hexdigest()) and isinstance(heads, list) and (len(heads) == 2) and all((isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head) is not None for head in heads)), 'Critic overlap-sync evidence lacks the verified diff or post-sync heads')
			evidence = canonical(overlap_evidence).decode('utf-8')
			prompt += '\nMandatory overlap-sync diff evidence for this review: ' + evidence
		_require(len(prompt.encode('utf-8')) <= MAX_OUTPUT, 'Codex task prompt exceeded its byte bound')
		argv = ['exec', '--ephemeral', '--ignore-user-config', '--sandbox', sandbox, '-c', 'approval_policy="never"', '-c', 'sandbox_workspace_write.network_access=false', '-c', 'windows.sandbox="' + windows_sandbox + '"', '-c', 'model_reasoning_effort=' + selected['reasoning_effort'], '--model', selected['model'], '--cd', str(worktree), '--json', '-']
		launch_nonce = str(uuid.uuid5(uuid.NAMESPACE_URL, 'awf:codex-launch:' + payload['dispatch_nonce']))
		handoff_path = record_path.with_suffix('.handoff.json')
		startup_proof_path = record_path.with_suffix('.startup.json')
		terminal_proof_path = record_path.with_suffix('.terminal.json')
		record = {'format': 'awf-reference-dispatch-1', 'dispatch_id': payload['dispatch_id'], 'dispatch_nonce': payload['dispatch_nonce'], 'stream': payload['stream'], 'ticket': payload['ticket'], 'prepared_at': payload['prepared_at'], 'begun_at': payload['begun_at'], 'role': role, 'lifecycle_stage': payload['lifecycle_stage'], 'argv': argv, 'worktree': str(worktree), 'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(), 'argv_sha256': hashlib.sha256(canonical(argv)).hexdigest(), 'launch_nonce': launch_nonce, 'handoff_path': str(handoff_path), 'startup_proof_path': str(startup_proof_path), 'terminal_proof_path': str(terminal_proof_path), 'status': 'PREPARED', 'created_at': _clock_text(clock)}
		_write_record(record_path, record)
		_verify_executable(codex_executable)
		launch_env = _safe_env(token_env)
		launch_env['CODEX_HOME'] = str(isolated_home)
		launch_env['AWF_DISPATCH_NONCE'] = launch_nonce
		try:
			launched = _invoke(runner, 'launch', [str(codex_executable['path']), *argv], cwd=worktree, env=launch_env, timeout=timeout, stdin=prompt, handoff_path=str(handoff_path), terminal_proof_path=str(terminal_proof_path), launch_nonce=launch_nonce)
		except ValidationError as exc:
			if _sandbox_setup_failure(exc):
				raise ValidationError(_CODEX_SANDBOX_ERROR) from exc
			raise
		_require(isinstance(launched, dict), 'Codex launch returned an invalid result')
		_require(launched.get('status') == 'LAUNCHED' and type(launched.get('pid')) is int and (launched['pid'] > 0), 'Codex launch did not return a durable process identity')
		_require(launched.get('launch_nonce') == launch_nonce, 'Codex launch identity is not nonce-bound')
		_require(launched.get('handoff_path') == str(handoff_path) and launched.get('startup_proof_path') == str(startup_proof_path) and (launched.get('terminal_proof_path') == str(terminal_proof_path)), 'Codex launch did not return durable proof paths')
		record.update({'status': 'LAUNCHED', 'pid': launched['pid'], 'launched_at': _clock_text(clock)})
		_write_record(record_path, record)
		try:
			result = _invoke(runner, 'observe_startup', record, timeout=timeout)
		except ValidationError as exc:
			if _sandbox_setup_failure(exc):
				record['diagnostic'] = 'SANDBOX_SETUP_CONFIG_ERROR'
				record['config_source'] = _CODEX_CONFIG_SOURCE
				record['observed_at'] = _clock_text(clock)
				_write_record(record_path, record)
				raise ValidationError(_CODEX_SANDBOX_ERROR) from exc
			raise
		terminal = isinstance(result, dict) and result.get('status') == 'COMPLETED' and (result.get('returncode') == 0) and (result.get('terminal') is True)
		running = isinstance(result, dict) and result.get('status') == 'RUNNING' and (result.get('terminal') is False)
		if isinstance(result, dict) and result.get('terminal') is True:
			_require(terminal, 'Dispatch terminal outcome was not proven by observation')
		else:
			_require(running, 'Dispatch startup outcome was not proven by observation')
		_require(result.get('pid') == record['pid'] and result.get('launch_nonce') == launch_nonce, 'Dispatch startup observation is bound to a different process identity')
		session_id = result.get('session_id')
		_require(isinstance(session_id, str) and session_id.strip() and (len(session_id) <= 512) and (not any((ord(char) < 32 for char in session_id))), 'Dispatch startup did not prove an observed session identity')
		for candidate in record_dir.glob('*.json'):
			if candidate == record_path:
				continue
			try:
				previous = _read_record(candidate)
			except ValidationError:
				continue
			if previous.get('format') == 'awf-reference-dispatch-1':
				previous_session = previous.get('session_id')
				if previous.get('ticket') == payload['ticket'] and previous.get('role') != role:
					_require(isinstance(previous_session, str) and previous_session.strip(), 'Writer/critic independence lacks prior observed session evidence')
				_require(previous_session != session_id, 'Dispatch session identity is not fresh or writer/critic independent')
		record['status'] = 'COMPLETED' if terminal else 'LAUNCHED'
		record['session_id'] = session_id
		record['startup_observed_at'] = _clock_text(clock)
		if terminal:
			record['observed_at'] = record['startup_observed_at']
		_write_record(record_path, record)
		return _record_receipt(payload, record['startup_observed_at'], session_id)

	def observe_dispatch(payload):
		_require(isinstance(payload, dict) and isinstance(payload.get('dispatch_id'), str), 'Dispatch observation payload is missing')
		path = record_dir / (payload['dispatch_id'] + '.json')
		record = _read_record(path)
		_require(record.get('dispatch_id') == payload['dispatch_id'] and record.get('dispatch_nonce') == payload.get('dispatch_nonce'), 'Dispatch observation is bound to a different nonce')
		_require(record.get('ticket') == payload.get('ticket') and record.get('stream') == payload.get('stream'), 'Dispatch observation identity differs from durable record')
		_require(record.get('role') == payload.get('role') and record.get('lifecycle_stage') == payload.get('lifecycle_stage'), 'Dispatch observation role or lifecycle stage differs from durable record')
		reconcile = 'reconcile_nonce' in payload
		if reconcile:
			_require(record.get('status') in {'PREPARED', 'LAUNCHED', 'COMPLETED'}, 'Interrupted dispatch has no observable durable launch intent')
			if record.get('status') == 'PREPARED':
				handoff = _read_record(Path(record['handoff_path']))
				_require(handoff.get('launch_nonce') == record['launch_nonce'] and type(handoff.get('pid')) is int and (handoff['pid'] > 0), 'Interrupted dispatch has no durable launch handoff')
				record.update({'pid': handoff['pid'], 'status': 'LAUNCHED'})
				_write_record(path, record)
			try:
				result = _invoke(runner, 'observe', record, timeout=timeout)
			except ValidationError as exc:
				if _sandbox_setup_failure(exc):
					raise ValidationError(_CODEX_SANDBOX_ERROR) from exc
				raise
			_require(isinstance(result, dict) and result.get('status') == 'COMPLETED' and (result.get('returncode') == 0) and (result.get('terminal') is True), 'Interrupted dispatch terminal outcome was not proven by observation')
			_require(result.get('pid') == record.get('pid') and result.get('launch_nonce') == record.get('launch_nonce'), 'Interrupted dispatch proof is bound to a different process identity')
			observed_session = result.get('session_id')
			_require(isinstance(observed_session, str) and observed_session.strip(), 'Interrupted dispatch did not prove an observed session identity')
			_require(record.get('session_id') in {None, observed_session}, 'Interrupted dispatch session identity changed')
			record['session_id'] = observed_session
			record['status'] = 'COMPLETED'
			record['observed_at'] = _clock_text(clock)
			_write_record(path, record)
		else:
			_require(record.get('status') in {'LAUNCHED', 'COMPLETED'}, 'Dispatch has no accepted durable launch')
		_require(isinstance(record.get('session_id'), str) and record['session_id'].strip(), 'Dispatch durable record has no observed session identity')
		return _record_receipt(payload, _clock_text(clock), record['session_id'], reconcile=reconcile)

	def observe_dispatch_quiescence(payload):
		_require(isinstance(payload, dict) and isinstance(payload.get('dispatch_id'), str), 'Dispatch quiescence payload is missing')
		path = record_dir / (payload['dispatch_id'] + '.json')
		record = _read_record(path)
		_require(record.get('dispatch_id') == payload['dispatch_id'] and record.get('dispatch_nonce') == payload.get('dispatch_nonce') and (record.get('ticket') == payload.get('ticket')) and (record.get('session_id') is not None), 'Dispatch quiescence observation differs from the accepted run')
		observer = 'observe_quiescence' if callable(getattr(runner, 'observe_quiescence', None)) else 'observe'
		kwargs = {} if observer == 'observe_quiescence' else {'timeout': 0}
		result = _invoke(runner, observer, record, **kwargs)
		_require(isinstance(result, dict) and result.get('status') == 'COMPLETED' and (result.get('returncode') == 0) and (result.get('terminal') is True) and (result.get('pid') == record.get('pid')) and (result.get('launch_nonce') == record.get('launch_nonce')) and (result.get('session_id') == record.get('session_id')), 'Accepted dispatch is not proved terminal and quiescent')
		observed_at = _clock_text(clock)
		record['status'] = 'COMPLETED'
		record['observed_at'] = observed_at
		_write_record(path, record)
		return {'dispatch_id': payload['dispatch_id'], 'ticket': payload['ticket'], 'exact_tuple': payload['exact_tuple'], 'session_id': record['session_id'], 'status': 'QUIESCENT', 'terminal': True, 'observed_at': observed_at}

	def deliver_status(digest):
		_require(isinstance(digest, dict) and isinstance(digest.get('delivery_id'), str), 'Status digest is missing its delivery identity')
		outbox_dir.mkdir(parents=True, exist_ok=True)
		path = outbox_dir / 'controller-status.jsonl'
		delivery_id = digest['delivery_id']
		found = None
		try:
			with path.open('a+', encoding='utf-8', newline='\n') as stream:
				stream.seek(0)
				for line in stream:
					if line.strip():
						row = json.loads(line)
						if row.get('delivery_id') == delivery_id:
							found = row
							break
				if found is None:
					row = {'delivery_id': delivery_id, 'digest_sha256': fingerprint('controller-delivery', digest), 'digest': digest, 'observed_at': _clock_text(clock)}
					stream.seek(0, 2)
					position = stream.tell()
					stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
					stream.flush()
					stream.seek(position)
					encoded = stream.readline()
					found = json.loads(encoded)
					_require(found == row, 'Status outbox readback differs from the appended record')
		except (OSError, ValueError, json.JSONDecodeError) as exc:
			raise ValidationError('Status outbox write or readback failed') from exc
		_require(found.get('digest_sha256') == fingerprint('controller-delivery', digest), 'Status outbox readback differs from the requested digest')
		return {'delivery_id': delivery_id, 'status': 'DELIVERED', 'observed_at': found['observed_at']}

	def jira_token():
		token = os.environ.get(token_env)
		_require(isinstance(token, str) and token, 'Configured Jira credential environment variable is absent')
		return token

	def jira_json(method, path, body=None):
		_require(path.startswith('/'), 'Jira API path must be absolute')
		token = jira_token()
		headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + token}
		data = None if body is None else canonical(body)
		if data is not None:
			headers['Content-Type'] = 'application/json'
		try:
			result = _invoke(http, 'request', method, site + path, headers=headers, body=data, timeout=jira.get('timeout_seconds', 30), max_bytes=MAX_OUTPUT)
		finally:
			del token
		if isinstance(result, tuple) and len(result) == 2:
			status, output = result
		elif isinstance(result, dict):
			status, output = (result.get('status'), result.get('body', result.get('output', '')))
		else:
			raise ValidationError('Jira transport returned an unparsable result')
		_require(type(status) is int and 200 <= status < 300, 'Jira returned a non-success status')
		if output in (None, '', b''):
			return {}
		return _json_output(output)

	def provider_identity():
		myself = jira_json('GET', '/rest/api/3/myself')
		project = jira_json('GET', '/rest/api/3/project/' + quote(jira['project_key'], safe=''))
		tenant = jira_json('GET', '/_edge/tenant_info')
		observed_cloud = tenant.get('cloudId')
		observed_site = tenant.get('siteUrl')
		_require(myself.get('accountId') == jira['controller_actor_id'] and str(project.get('id')) == jira['provider_project_id'] and (project.get('key') == jira['project_key']) and (observed_cloud == jira['cloud_id']) and isinstance(observed_site, str) and (observed_site.rstrip('/') == site), 'Jira provider identity mismatch')
		return {'cloud_id': observed_cloud, 'site': observed_site.rstrip('/'), 'project_id': str(project['id']), 'project_key': project['key'], 'controller_actor_id': jira['controller_actor_id']}

	def read_current_status(binding):
		_require(isinstance(binding, dict) and isinstance(binding.get('issue_id'), str), 'Jira status read needs an issue identity')
		value = jira_json('GET', '/rest/api/3/issue/' + quote(binding['issue_id'], safe='') + '?fields=status')
		status_id = value.get('fields', {}).get('status', {}).get('id')
		observed_at = _clock_text(clock)
		provider = binding.get('jira_provider') or provider_identity()
		_require(isinstance(status_id, str) and status_id, 'Jira issue status is missing')
		return {'issue_id': binding['issue_id'], 'status_id': status_id, 'observed_at': observed_at, 'jira_provider': provider}

	def write_transition(record):
		_require(isinstance(record, dict) and isinstance(record.get('binding'), dict), 'Jira transition record is missing its binding')
		issue_id = record['binding']['issue_id']
		_require(isinstance(record.get('transition_id'), str) and record['transition_id'], 'Jira transition identity is missing')
		attempted_at = _clock_text(clock)
		jira_json('POST', '/rest/api/3/issue/' + quote(issue_id, safe='') + '/transitions', {'transition': {'id': record['transition_id']}})
		return {'operation_id': record['operation_id'], 'issue_id': issue_id, 'status': 'ATTEMPTED', 'observed_at': attempted_at, 'jira_provider': record['jira_provider']}

	def read_transition(record, operation):
		issue_id = record['binding']['issue_id']
		value = jira_json('GET', '/rest/api/3/issue/' + quote(issue_id, safe='') + '?fields=status&expand=changelog')
		status = value.get('fields', {}).get('status', {}).get('id')
		histories = value.get('changelog', {}).get('histories')
		_require(isinstance(status, str) and status and isinstance(histories, list), 'Jira transition history is unavailable')
		candidates = []
		for history in histories:
			if not isinstance(history, dict) or not isinstance(history.get('created'), str):
				continue
			_require(isinstance(operation, dict) and isinstance(operation.get('observed_at'), str), 'Jira transition readback has no operation attempt time')
			operation_time = operation['observed_at']
			if timestamp(history['created']) < timestamp(operation_time):
				continue
			author = history.get('author', {})
			actor = author.get('accountId') if isinstance(author, dict) else None
			for item in history.get('items', []):
				if not isinstance(item, dict) or item.get('field') != 'status':
					continue
				if item.get('to') == record.get('to_status_id') or item.get('toString') == status:
					if isinstance(actor, str) and actor:
						candidates.append((history['created'], actor))
		_require(candidates, 'Jira transition history does not prove the current transition')
		candidates.sort(key=lambda item: item[0])
		_created, actor = candidates[-1]
		observed_at = _clock_text(clock)
		return {'issue_id': issue_id, 'status': status, 'actor': actor, 'observed_at': observed_at, 'jira_provider': record['jira_provider']}

	def reconcile_merged_ticket(ticket):
		_text(ticket, 'Merged Jira ticket')
		provider = provider_identity()
		before = read_current_status({'issue_id': ticket, 'jira_provider': provider})
		target = jira['merged_status_id']
		operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'awf:jira-merge:' + jira['cloud_id'] + ':' + ticket))
		if before['status_id'] != target:
			record = {'operation_id': operation_id, 'binding': {'issue_id': ticket}, 'transition_id': jira['merged_transition_id'], 'to_status_id': target, 'jira_provider': provider}
			operation = write_transition({**record, 'producer_id': provider['controller_actor_id']})
			after = read_transition({**record, 'producer_id': provider['controller_actor_id']}, operation)
		else:
			after = {'issue_id': ticket, 'status': target, 'actor': provider['controller_actor_id'], 'observed_at': before['observed_at'], 'jira_provider': provider}
		_require(after.get('actor') == provider['controller_actor_id'], 'Jira merge transition actor was not independently observed')
		_require(after['status'] == target, 'Jira merge readback did not reach the configured terminal status')
		return {'ticket': ticket, 'issue_id': ticket, 'cloud_id': provider['cloud_id'], 'project_id': provider['project_id'], 'actor_id': provider['controller_actor_id'], 'status': 'RECONCILED', 'operation_id': operation_id, 'before_status_id': before['status_id'], 'after_status_id': after['status'], 'observed_at': after['observed_at']}
	scope_snapshots = {}

	def fetch_scope_page(scope, cursor):
		_text(scope, 'Jira scope', maximum=4096)
		observed_provider = provider_identity()
		provider = {'cloud_id': observed_provider['cloud_id'], 'project_id': observed_provider['project_id'], 'actor_id': observed_provider['controller_actor_id']}
		scope_sha = fingerprint('jira-progress-scope', {'scope': scope, 'binding': provider, 'include_epics': False})
		start = 0 if cursor is None else int(cursor)
		_require(start >= 0, 'Jira scope cursor is invalid')
		page_size = min(jira.get('page_size', MAX_JIRA_PAGE), MAX_JIRA_PAGE)
		snapshot = scope_snapshots.get(scope)
		if snapshot is None:
			_require(cursor is None, 'Jira scope continuation has no retained snapshot')
			observed_at = _clock_text(clock)
			snapshot = {'observed_at': observed_at}
			scope_snapshots[scope] = snapshot
		bounded_scope = '(' + scope + ') AND updated <= "' + snapshot['observed_at'] + '"'
		result = jira_json('GET', '/rest/api/3/search?jql=' + quote(bounded_scope, safe='') + '&startAt=' + str(start) + '&maxResults=' + str(page_size))
		issues = result.get('issues')
		total = result.get('total')
		_require(isinstance(issues, list) and type(total) is int and (total >= 0), 'Jira scope response is incomplete')
		if 'total' in snapshot:
			_require(total == snapshot['total'], 'Jira scope total changed during pagination')
		else:
			snapshot['total'] = total
		snapshot_id = fingerprint('jira-search-snapshot', {'scope': scope, 'binding': provider, 'observed_at': snapshot['observed_at'], 'total': total})
		items = []
		for issue in issues:
			fields = issue.get('fields', {}) if isinstance(issue, dict) else {}
			issue_type = str(fields.get('issuetype', {}).get('name', 'TASK')).upper()
			category = str(fields.get('status', {}).get('statusCategory', {}).get('key', '')).lower()
			_require(isinstance(issue.get('id'), str) and category in {'done', 'new', 'indeterminate'}, 'Jira scope item is incomplete')
			items.append({'id': issue['id'], 'issue_type': 'EPIC' if issue_type == 'EPIC' else issue_type, 'status_category': 'TERMINAL' if category == 'done' else 'NON_TERMINAL'})
		next_cursor = None if start + len(issues) >= total else str(start + len(issues))
		complete = next_cursor is None
		observed_at = snapshot['observed_at']
		return {'items': items, 'next_cursor': next_cursor, 'complete': complete, 'snapshot_id': snapshot_id, 'scope_sha256': scope_sha, 'observed_at': observed_at}
	return {'observe_inventory': observe_inventory, 'dispatch_ticket': dispatch_ticket, 'observe_dispatch': observe_dispatch, 'observe_dispatch_quiescence': observe_dispatch_quiescence, 'deliver_status': deliver_status, 'observe_publication': observe_publication, 'observe_overlap_allowance': observe_overlap_allowance, 'observe_overlap_conflict': observe_overlap_conflict, 'observe_overlap_sync_inventory': observe_overlap_sync_inventory, 'sync_writer': sync_writer, 'reconcile_overlap_sync': reconcile_overlap_sync, 'observe_provider_identity': provider_identity, 'read_current_status': read_current_status, 'write_transition': write_transition, 'read_transition': read_transition, 'reconcile_merged_ticket': reconcile_merged_ticket, 'fetch_scope_page': fetch_scope_page, 'observe_accepted_project_config': observe_accepted_project_config, 'observe_closeout_record': observe_closeout_record, 'observe_jira_reconciliation': observe_jira_reconciliation}

def build_adapters(config):
	return _build(config)
