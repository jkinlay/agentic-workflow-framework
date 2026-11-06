"""Pre-E4: admission, destination isolation and real HTTP slow-drip regressions."""
from copy import deepcopy
from http.client import HTTPResponse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import test_reporting_e2_regressions as e2
from test_reporting_records import request
from agentic import ValidationError
from agentic.canonical import fingerprint
from agentic.reporting.records import prepare_snapshot, replay_snapshot
from agentic.reporting.safety import safe_report

SENTINEL = 'gh' + 'p_' + 'SYNTHETIC_CREDENTIAL_SENTINEL'
OPAQUE = 'synthetic-opaque-sender-value'


class AdmissionTests(unittest.TestCase):
    def test_snapshot_filters_before_hashing_and_replays_filtered_values(self):
        value = request()
        value['summary'] = SENTINEL
        record = prepare_snapshot(value, value['binding'], verify_sources=lambda v, b: v)
        self.assertNotIn(SENTINEL, json.dumps(record))
        self.assertEqual(replay_snapshot(record)['summary'], 'CREDENTIAL_WITHHELD')

    def test_snapshot_filters_untrusted_keys_and_exact_opaque_values(self):
        value = request()
        value['summary'] = OPAQUE
        value['sources'][0]['source'] = SENTINEL
        record = prepare_snapshot(value, value['binding'], credentials=(OPAQUE,))
        self.assertNotIn(OPAQUE, json.dumps(record))
        self.assertNotIn(SENTINEL, json.dumps(record))
        self.assertEqual(replay_snapshot(record)['summary'], 'CREDENTIAL_WITHHELD')
        self.assertNotIn(SENTINEL, json.dumps(safe_report({SENTINEL: 'value'}, trusted_keys=True)))

    def test_append_refuses_unsafe_record_before_any_journal_write(self):
        import tempfile
        from agentic.store import Store
        from agentic.status_report_store import ReportStore
        value = request()
        value['binding']['project'] = '00000000-0000-4000-8000-000000000001'
        record = prepare_snapshot(value, value['binding'])
        # A legacy/foreign constructor can bypass the new constructor guard.
        record['inputs']['summary'] = SENTINEL
        record['values']['summary'] = SENTINEL
        record['inputs_sha256'] = fingerprint('report-snapshot-2', record['inputs'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite'
            store = Store(path, value['binding']['project'])
            reports = ReportStore(path, value['binding']['project'])
            before = path.read_bytes()
            with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                reports.append(record)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.verify_chain()['sequence'], 0)

    def test_shared_filter_compares_resolved_value_without_retaining_it(self):
        value = {'summary': 'prefix ' + OPAQUE, OPAQUE: [OPAQUE]}
        with patch.dict(os.environ, AWF_REPORT_TEST=OPAQUE):
            filtered = safe_report(value, secret_refs=['env:AWF_REPORT_TEST'])
        self.assertNotIn(OPAQUE, json.dumps(filtered))
        self.assertIn('CREDENTIAL_WITHHELD', json.dumps(filtered))

    def test_append_compares_explicit_resolved_sender_before_writing(self):
        import tempfile
        from agentic.store import Store
        from agentic.status_report_store import ReportStore
        value = request()
        value['binding']['project'] = '00000000-0000-4000-8000-000000000001'
        value['summary'] = OPAQUE
        record = prepare_snapshot(value, value['binding'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite'
            Store(path, value['binding']['project'])
            reports = ReportStore(path, value['binding']['project'])
            before = path.read_bytes()
            with self.assertRaisesRegex(ValidationError, 'REPORT_CREDENTIAL_WITHHELD'):
                reports.append(record, credentials=(OPAQUE,))
            self.assertEqual(path.read_bytes(), before)


class ControllerAdmissionTests(unittest.TestCase):
    setUp = e2.ReportingE2Regressions.setUp
    cycle = e2.ReportingE2Regressions.cycle
    status = e2.ReportingE2Regressions.status

    def blocked(self, reason):
        self.runner.inventory[0]['body'] = json.dumps(dict(json.loads(self.runner.inventory[0]['body']),
            disposition='BLOCKED', actor='owner', reason=reason, next_action='Grant access'))

    def test_reference_source_filters_before_retained_evidence(self):
        self.blocked(SENTINEL)
        self.cycle('2026-11-10T10:00:00Z')
        evidence = list(Path(self.helper.temp.name).rglob('*reporting*'))
        retained = [p for p in evidence if p.is_file()]
        # Inspect every retained source file, including digest-bound snapshots.
        snapshots = []
        for path in Path(self.helper.temp.name).rglob('*.json'):
            raw = path.read_bytes()
            if b'"kind":"reporting"' in raw or b'"kind": "reporting"' in raw:
                snapshots.append(raw)
        self.assertTrue(snapshots, retained)
        self.assertTrue(all(SENTINEL.encode() not in raw for raw in snapshots))
        self.assertTrue(any(b'CREDENTIAL_WITHHELD' in raw for raw in snapshots))

    def test_opaque_sender_filtered_from_controller_store_and_status(self):
        self.blocked(OPAQUE)
        with patch.dict(os.environ, AWF_REPORT_TEST=OPAQUE):
            result = self.cycle('2026-11-10T10:00:00Z')
            status = self.status()
        self.assertNotIn(OPAQUE, json.dumps(result) + json.dumps(status))
        self.assertNotIn(OPAQUE.encode(), self.helper.path.read_bytes())

    def test_untrusted_provider_ids_are_ambiguous_never_persisted(self):
        network = self.network
        original = type(network).__call__
        for platform in ('slack', 'teams'):
            for identifier in (SENTINEL, OPAQUE, 'invalid-message', '1.0', '１２３'):
                with self.subTest(platform=platform, identifier=identifier):
                    self.settings['mode'] = platform
                    def http(owner, method, url, headers, payload):
                        value = original(owner, method, url, headers, payload)
                        if 'conversations.history' in url:
                            for message in value['messages']:
                                message['ts'] = identifier
                        if '/messages?' in url:
                            for message in value['value']:
                                message['id'] = identifier
                        return value
                    # Both transports and all controller/outbox logic stay real.
                    with patch.object(type(network), '__call__', http):
                        result = self.cycle('2026-11-10T%02d:00:00Z' % (10 + len(network.posts)))
                    states = [s for s in self.status()['deliveries'] if s['platform'] == platform]
                    self.assertTrue(states)
                    self.assertTrue(all(s['status'] == 'UNKNOWN' for s in states))
                    if identifier in (SENTINEL, OPAQUE):
                        self.assertNotIn(identifier, json.dumps(result))
                        self.assertNotIn(identifier.encode(), self.helper.path.read_bytes())

    def test_slack_hourly_progresses_while_teams_alert_waits_five_seconds(self):
        self.blocked('Owner access required')
        original = type(self.network).__call__
        slack_hourly = threading.Event()
        teams_wait = threading.Event()
        completed, errors = [], []
        def http(network, method, url, headers, payload):
            if method == 'POST' and 'graph.microsoft.com' in url:
                teams_wait.set()
                network.block.wait(5)
            result = original(network, method, url, headers, payload)
            if method == 'POST' and 'slack.com' in url and 'Hourly progress' in str(payload):
                slack_hourly.set()
            return result
        clean = Path(self.helper.temp.name) / 'clean-isolation-repository'
        subprocess.run(['git', 'clone', '--shared', '--quiet', str(self.repository['repository_root']), str(clean)],
                       check=True, capture_output=True)
        self.repository['repository_root'] = clean
        def cycle():
            try:
                completed.append(self.cycle('2026-11-10T10:00:00Z'))
            except BaseException as exc:
                errors.append(exc)
        with patch.object(type(self.network), '__call__', http):
            worker = threading.Thread(target=cycle)
            worker.start()
            try:
                self.assertTrue(teams_wait.wait(2))
                self.assertTrue(slack_hourly.wait(1.5))
                self.assertTrue(worker.is_alive())
                # The healthy destination needs no Teams release or timeout.
                posts = [body for name, body in self.network.posts if name == 'slack']
                self.assertIn('Owner action', str(posts[0]))
                self.assertIn('Hourly progress', str(posts[1]))
            finally:
                self.network.block.set()
                worker.join(45)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(completed[0]['errors'], [])


class CLIAdmissionTests(unittest.TestCase):
    def test_actual_cli_filters_source_credentials_and_provider_ids(self):
        import test_reporting_e3_regressions as e3
        for provider_id in (None, SENTINEL, OPAQUE):
            with self.subTest(provider_id=provider_id), patch.dict(os.environ, AWF_REPORT_TEST=OPAQUE):
                def prepare(case, net):
                    os.environ['AWF_REPORT_TEST'] = OPAQUE
                    # Closed PRs are reporting-only source data; controller
                    # stream/inventory retention belongs to another boundary.
                    case.prs = [dict(id=OPAQUE, number=99,
                        html_url='https://github.com/example/project/pull/99',
                        merged_at='2026-11-10T10:00:00Z', merge_commit_sha=SENTINEL,
                        head={'sha': 'c'*40})]
                    if provider_id is not None:
                        original = type(net).__call__
                        class HostileNetwork(type(net)):
                            def __call__(self, method, url, headers, payload):
                                value = original(self, method, url, headers, payload)
                                for message in value.get('messages', []):
                                    message['ts'] = provider_id
                                for message in value.get('value', []):
                                    if 'body' in message:
                                        message['id'] = provider_id
                                return value
                        net.__class__ = HostileNetwork
                def inspect(case, result, output):
                    self.assertNotIn(SENTINEL, output)
                    self.assertNotIn(OPAQUE, output)
                    self.assertNotIn(SENTINEL.encode(), case.helper.path.read_bytes())
                    self.assertNotIn(OPAQUE.encode(), case.helper.path.read_bytes())
                    states = case.status()['deliveries']
                    self.assertTrue(states)
                    self.assertEqual({s['status'] for s in states},
                                     {'DELIVERED'} if provider_id is None else {'UNKNOWN'})
                    for path in Path(case.helper.temp.name).rglob('*.json'):
                        raw = path.read_bytes()
                        if b'"kind":"reporting"' in raw:
                            self.assertNotIn(SENTINEL.encode(), raw)
                            self.assertNotIn(OPAQUE.encode(), raw)
                with redirect_stdout(io.StringIO()):
                    e3.cli_probe(0, prepare=prepare, inspect=inspect)

class DeadlineControllerTests(unittest.TestCase):
    setUp = e2.ReportingE2Regressions.setUp
    cycle = e2.ReportingE2Regressions.cycle
    status = e2.ReportingE2Regressions.status

    def test_slow_drip_full_read_has_overall_request_deadline(self):
        from agentic.reporting import http_transport as transport
        # Use the shipped http_json and Python HTTPResponse. Only the wire is fake.
        network = self.network
        clean = Path(self.helper.temp.name) / 'clean-deadline-repository'
        subprocess.run(['git', 'clone', '--shared', '--quiet', str(self.repository['repository_root']), str(clean)],
                       check=True, capture_output=True)
        self.repository['repository_root'] = clean
        original = type(network).__call__
        writers = []
        delayed = []
        stop = threading.Event()
        self.addCleanup(stop.set)
        class Opener:
            def open(inner, req, timeout):
                nonlocal started
                if started is None:
                    # Measure the network stage; repository admission and local
                    # storage before its first request are outside that bound.
                    started = time.monotonic()
                payload = json.loads(req.data) if req.data else None
                value = original(network, req.method, req.full_url, dict(req.headers), payload)
                if req.method == 'POST':
                    delayed.append(network.messages['slack'].pop())
                left, right = socket.socketpair()
                left.settimeout(timeout)
                response = HTTPResponse(left)
                data = json.dumps(value).encode()
                right.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n')
                if req.method == 'POST':
                    def drip():
                        try:
                            for byte in data:
                                if stop.wait(2):
                                    break
                                right.sendall(bytes([byte]))
                        except OSError:
                            pass
                        finally:
                            right.close()
                    worker = threading.Thread(target=drip)
                    writers.append(worker)
                    worker.start()
                else:
                    right.sendall(data)
                    right.close()
                response.begin()
                left.close()
                return response
        host = Path(self.helper.temp.name) / 'deadline-host'
        host.mkdir()
        config = e2._base_config(host)
        config.update(_runner=self.runner, _clock=self.clock,
            reporting_sources=dict(binding=deepcopy(self.binding), pull_requests=[99]),
            reporting={'slack': dict(context='workspace', sender='sender')})
        self.adapters = self.namespace['build_adapters'](config)
        self.settings['mode'] = 'slack'
        started = None
        try:
            with patch.object(transport, 'build_opener', return_value=Opener()):
                result = self.cycle('2026-11-10T10:00:00Z')
            self.assertLess(time.monotonic() - started, 17)
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(network.posts), 1)
            self.assertEqual(len(self.status()['deliveries'][0]['attempts']), 1)
            self.assertEqual(self.status()['deliveries'][0]['status'], 'UNKNOWN')
            network.messages['slack'].extend(delayed)
            with patch.object(transport, 'build_opener', return_value=Opener()):
                self.cycle('2026-11-10T10:00:01Z')
            self.assertEqual(self.status()['deliveries'][0]['status'], 'DELIVERED')
            self.assertEqual(len(network.posts), 1)
        finally:
            stop.set()
            for worker in writers:
                worker.join(3)


class HTTPSDeadlineTests(unittest.TestCase):
    def test_deadline_covers_dns_connect_tls_send_headers_and_body(self):
        from agentic.reporting.http_transport import http_json
        from agentic.reporting import deadline
        for stage in ('dns', 'connect', 'tls', 'send', 'headers', 'body'):
            with self.subTest(stage=stage):
                left, right = socket.socketpair()
                halted = threading.Event()
                lookup_done = threading.Event()
                class Wire:
                    def settimeout(self, seconds):
                        left.settimeout(seconds)
                    def connect(self, address):
                        if stage == 'connect':
                            halted.wait(2)
                    def do_handshake(self):
                        if stage == 'tls':
                            halted.wait(2)
                    def sendall(self, data):
                        if stage == 'send':
                            halted.wait(2)
                        else:
                            left.sendall(data)
                    def makefile(self, *args, **kwargs):
                        return left.makefile(*args, **kwargs)
                    def shutdown(self, how):
                        halted.set()
                        left.shutdown(how)
                    def close(self):
                        # HTTPResponse owns the fd after Connection: close.
                        left.close()
                wire = Wire()
                class TLS:
                    def wrap_socket(self, sock, **kwargs):
                        return sock
                def lookup(*args):
                    try:
                        if stage == 'dns':
                            halted.wait(2)
                        return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))]
                    finally:
                        lookup_done.set()
                def peer():
                    try:
                        right.recv(65536)
                        raw = b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}'
                        if stage == 'body':
                            header, raw = raw.split(b'\r\n\r\n', 1)
                            right.sendall(header + b'\r\n\r\n')
                        if stage in {'headers', 'body'}:
                            for byte in raw:
                                if halted.wait(.04):
                                    break
                                right.sendall(bytes([byte]))
                        else:
                            right.sendall(raw)
                    except OSError:
                        pass
                    finally:
                        right.close()
                worker = threading.Thread(target=peer)
                worker.start()
                started = time.monotonic()
                try:
                    with patch.object(deadline.socket, 'socket', return_value=wire), \
                         patch.object(deadline.socket, 'getaddrinfo', side_effect=lookup), \
                         patch('http.client._create_https_context', return_value=TLS()):
                        with self.assertRaisesRegex(ValidationError, '^REPORT_HTTP_UNAVAILABLE$'):
                            http_json('POST', 'https://slack.com/api/chat.postMessage', {}, {}, timeout=.15)
                    self.assertLess(time.monotonic() - started, .8)
                finally:
                    halted.set()
                    left.close()
                    right.close()
                    worker.join(2)
                    self.assertTrue(lookup_done.wait(2))
