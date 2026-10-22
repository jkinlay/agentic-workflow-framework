"""Opt-in bounded evidence for the installed self-test harness (AWF-181).

The ordinary harness subprocess.run branch is intentionally independent.
No driver instrumentation, timeout extension, or retention policy lives here.
"""
from __future__ import annotations

import codecs
import io
import json
import locale
import math
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.agentic/lib'))
from agentic.child_process import child_env  # noqa: E402

PHASES = ('discovery', 'syntax', 'documentation', 'release_hygiene', 'test_suite')
CHUNK = 4096
TAIL = 65536
CARRY = 16384
JSON_LIMIT = 262144
EXCERPT_LIMIT = 16384
ASSERT_LIMIT = 8192
MAX_COUNT = (1 << 64) - 1
MAX_SECONDS = 10 ** 12
EVENTS = frozenset('self_test_' + name for name in (
    'start', 'phase_start', 'phase_complete', 'case_start', 'heartbeat', 'interrupted'))
# Other codecs may buffer an arbitrary unterminated shift sequence (UTF-7).
# Refuse them explicitly; all accepted incremental codecs have bounded carry.
ENCODINGS = frozenset(('utf-8', 'utf-8-sig', 'ascii', 'iso8859-1', 'cp437', 'cp850',
                       'utf-16', 'utf-16-le', 'utf-16-be',
                       'utf-32', 'utf-32-le', 'utf-32-be',
                       *(f'cp{page}' for page in range(1250, 1259))))


def bounded(value, limit, suffix=False):
    raw = value.encode('utf-8', 'replace')
    if len(raw) <= limit:
        return raw.decode('utf-8')
    mark = '[truncated]'
    if limit < len(mark):
        return mark[:max(0, limit)]
    room = max(0, limit - len(mark))
    text = (raw[-room:] if suffix and room else raw[:room]).decode('utf-8', 'ignore')
    return mark + text if suffix else text + mark


class Counts(dict):
    """Saturating counters; never claim exact accounting after saturation."""
    def __init__(self, **values):
        super().__init__(saturated=False, **values)

    def add(self, key, amount=1):
        value = self.get(key, 0) + amount
        if value > MAX_COUNT:
            self['saturated'] = True
        self[key] = min(MAX_COUNT, value)


class Diagnostics:
    def __init__(self):
        self.entries = []
        self.fields = []
        self.counts = Counts(omitted=0)

    def add(self, message):
        if len(self.entries) < 8:
            text = str(message)
            kept = bounded(text, 256)
            self.entries.append(kept)
            self.fields.append({'original_bytes': min(MAX_COUNT, len(text.encode('utf-8', 'replace'))),
                                'retained_bytes': len(kept.encode('utf-8')),
                                'truncated': kept != text})
        else:
            self.counts.add('omitted')


def number(value):
    return (type(value) in (int, float) and 0 <= value <= MAX_SECONDS
            and math.isfinite(value))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_keys')
        result[key] = value
    return result


def check_depth(raw):
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 8:
                raise ValueError('nesting')
        elif byte in (93, 125):
            depth -= 1


class Parser:
    def __init__(self):
        self.carry = bytearray()
        self.discarding = False
        self.discarded_record_bytes = 0
        self.counts = Counts(accepted=0, rejected=0, duplicates=0,
                             chronology_conflicts=0, ignored_unknown_fields=0,
                             incomplete_records=0, partial_bytes=0,
                             unfinished_record_bytes=0, oversized_record_bytes=0,
                             carry_high_water=0)
        self.reasons = Counts(**{k: 0 for k in (
            'oversized', 'nesting', 'duplicate_keys', 'invalid_json', 'wrong_type', 'unknown_event')})
        self.phases = [{
            'phase': name, 'status': 'unobserved', 'start_elapsed_seconds': None,
            'completion_elapsed_seconds': None, 'phase_elapsed_seconds': None,
            'parent_observed_running_seconds': None,
        } for name in PHASES]
        self.receipts = [None] * 5
        self.latest = None
        self.start_observed = False
        self.heartbeat_interval_seconds = None
        self.fields = {}

    def reject(self, reason):
        self.counts.add('rejected')
        self.reasons.add(reason)

    def field(self, key, value, limit):
        if value is None:
            return None
        kept = bounded(value, limit)
        self.fields[key] = {'original_bytes': len(value.encode('utf-8')),
                            'retained_bytes': len(kept.encode('utf-8')),
                            'truncated': kept != value}
        return kept

    def feed(self, chunk):
        # Byte-at-a-time framing keeps even the transient carry <= CARRY.
        for byte in chunk:
            if byte == 10:
                if self.discarding:
                    self.discarding = False
                    self.discarded_record_bytes = 0
                else:
                    self.record(bytes(self.carry))
                self.carry.clear()
            elif self.discarding:
                self.counts.add('partial_bytes')
                self.counts.add('oversized_record_bytes')
                self.discarded_record_bytes = min(MAX_COUNT, self.discarded_record_bytes + 1)
            elif len(self.carry) == CARRY:
                self.reject('oversized')
                self.counts.add('partial_bytes', len(self.carry) + 1)
                self.counts.add('oversized_record_bytes', len(self.carry) + 1)
                self.discarded_record_bytes = len(self.carry) + 1
                self.carry.clear()
                self.discarding = True
            else:
                self.carry.append(byte)
                self.counts['carry_high_water'] = max(self.counts['carry_high_water'], len(self.carry))

    def finish(self):
        if self.carry or self.discarding:
            self.counts.add('incomplete_records')
            self.counts.add('partial_bytes', len(self.carry))
            self.counts.add('unfinished_record_bytes', len(self.carry) + self.discarded_record_bytes)
        self.carry.clear()

    def record(self, raw):
        try:
            check_depth(raw)
            event = json.loads(raw, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError) as error:
            reason = str(error)
            self.reject(reason if reason in self.reasons else 'invalid_json')
            return
        if not isinstance(event, dict):
            self.reject('wrong_type')
            return
        kind = event.get('event')
        if not isinstance(kind, str) or kind not in EVENTS:
            self.reject('unknown_event')
            return
        if not number(event.get('elapsed_seconds')):
            self.reject('wrong_type')
            return
        phase = event.get('phase')
        transition = kind in ('self_test_phase_start', 'self_test_phase_complete', 'self_test_case_start')
        if ((transition and (not isinstance(phase, str) or phase not in PHASES)) or
                (not transition and phase is not None and
                 (not isinstance(phase, str) or phase not in PHASES))):
            self.reject('wrong_type')
            return
        last = event.get('last_completed_phase')
        if last is not None and (not isinstance(last, str) or last not in PHASES):
            self.reject('wrong_type')
            return
        current = event.get('current_test')
        if current is not None and not isinstance(current, str):
            self.reject('wrong_type')
            return
        if kind == 'self_test_case_start' and not isinstance(current, str):
            self.reject('wrong_type')
            return
        if kind == 'self_test_phase_complete' and not number(event.get('phase_elapsed_seconds')):
            self.reject('wrong_type')
            return
        if kind == 'self_test_start' and not number(event.get('heartbeat_interval_seconds')):
            self.reject('wrong_type')
            return
        if kind == 'self_test_interrupted' and not isinstance(event.get('interruption'), str):
            self.reject('wrong_type')
            return
        if 'detail' in event and not isinstance(event['detail'], str):
            self.reject('wrong_type')
            return
        try:
            for key in ('current_test', 'interruption', 'detail'):
                if isinstance(event.get(key), str):
                    event[key].encode('utf-8')
        except UnicodeEncodeError:
            self.reject('wrong_type')
            return
        allowed = {'event', 'elapsed_seconds', 'phase', 'last_completed_phase', 'current_test',
                   'phase_elapsed_seconds', 'heartbeat_interval_seconds', 'phases', 'interruption', 'detail'}
        self.counts.add('ignored_unknown_fields', len(event.keys() - allowed))
        self.counts.add('accepted')
        if kind == 'self_test_start':
            self.start_observed = True
            self.heartbeat_interval_seconds = event['heartbeat_interval_seconds']
        elif kind in ('self_test_phase_start', 'self_test_phase_complete'):
            index = PHASES.index(phase)
            slot = self.phases[index]
            key = 'start_elapsed_seconds' if kind.endswith('_start') else 'completion_elapsed_seconds'
            if slot[key] is not None:
                self.counts.add('duplicates')
                return
            slot[key] = event['elapsed_seconds']
            if kind.endswith('_start'):
                self.receipts[index] = time.monotonic()
                if slot['status'] == 'complete':
                    self.counts.add('chronology_conflicts')
                else:
                    slot['status'] = 'incomplete'
            else:
                if slot['start_elapsed_seconds'] is None or event['elapsed_seconds'] < slot['start_elapsed_seconds']:
                    self.counts.add('chronology_conflicts')
                slot['status'] = 'complete'
                slot['phase_elapsed_seconds'] = event['phase_elapsed_seconds']
            if any(p['start_elapsed_seconds'] is not None for p in self.phases[index + 1:]):
                self.counts.add('chronology_conflicts')
        else:
            self.fields.clear()
            self.latest = {'event': kind, 'elapsed_seconds': event['elapsed_seconds'],
                           'phase': phase, 'last_completed_phase': last,
                           'current_test': self.field('current_test', current, 1024)}
            for key in ('interruption', 'detail'):
                if key in event:
                    self.latest[key] = self.field(key, event[key], 256)

    def snapshot(self):
        now = time.monotonic()
        for slot, receipt in zip(self.phases, self.receipts):
            if slot['status'] == 'incomplete' and receipt is not None:
                slot['parent_observed_running_seconds'] = min(MAX_SECONDS, max(0, now - receipt))
        return {**self.counts, 'rejection_reasons': self.reasons,
                'start_observed': self.start_observed,
                'heartbeat_interval_seconds': self.heartbeat_interval_seconds,
                'phase_evidence_complete': all(p['status'] == 'complete' for p in self.phases)}


def available_read(pipe):
    """Return available bytes, None for no data yet, b'' for EOF; never block."""
    fd = pipe.fileno()
    if os.name == 'nt':
        import ctypes
        import msvcrt
        available = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(fd)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232):
                return b''
            raise OSError(error, 'PeekNamedPipe')
        if not available.value:
            return None
        return os.read(fd, min(CHUNK, available.value))
    ready, _, _ = select.select([fd], [], [], 0)
    if not ready:
        return None
    try:
        return os.read(fd, CHUNK)
    except BlockingIOError:
        return None


class Stream:
    def __init__(self, parser=None, decoder=None):
        self.tail = bytearray()
        self.counts = Counts(observed_bytes=0, retained_raw_bytes=0, discarded_raw_bytes=0)
        self.parser = parser
        self.eof = False
        self.error = None
        self.high_water = 0
        self.decoder = decoder
        self.text_tail = ''
        self.decode_error = None

    def decode(self, chunk, final=False):
        if self.decoder is None or self.decode_error is not None:
            return
        try:
            decoded = self.decoder.decode(chunk, final=final)
            # A distinct decoded tail avoids inventing decoding failures when
            # raw eviction splits a code point or a stateful encoding sequence.
            raw = (self.text_tail + decoded).encode('utf-8', 'surrogatepass')
            if len(raw) > TAIL:
                raw = raw[-TAIL:]
                # Keep a whole UTF-8 code point at the new left boundary.
                while raw and raw[0] & 0xc0 == 0x80:
                    raw = raw[1:]
            self.text_tail = raw.decode('utf-8', 'surrogatepass')
        except UnicodeError as error:
            self.decode_error = error

    def drain(self, pipe, stop):
        try:
            while not stop.is_set():
                chunk = available_read(pipe)
                if chunk is None:
                    stop.wait(.01)
                    continue
                if not chunk:
                    self.eof = True
                    self.decode(b'', final=True)
                    break
                self.counts.add('observed_bytes', len(chunk))
                overflow = max(0, len(self.tail) + len(chunk) - TAIL)
                if overflow:
                    del self.tail[:overflow]
                    self.counts.add('discarded_raw_bytes', overflow)
                self.tail.extend(chunk)
                self.decode(chunk)
                self.high_water = max(self.high_water, len(self.tail))
                self.counts['retained_raw_bytes'] = len(self.tail)
                if self.parser:
                    self.parser.feed(chunk)
        except OSError as error:
            self.error = bounded(str(error), 256)
        finally:
            if self.parser:
                self.parser.finish()

    def snapshot(self):
        raw = bytes(self.tail)
        try:
            raw.decode('utf-8')
            replaced = False
        except UnicodeError:
            replaced = True
        return {**self.counts, 'tail': raw.decode('utf-8', 'replace'),
                'decoding_replacement': replaced, 'raw_truncated': bool(self.counts['discarded_raw_bytes']),
                'eof': self.eof, 'total_child_output_known': self.eof,
                'high_water_bytes': self.high_water}


def format_tails(streams, budget, raw_tails=None):
    pieces = []
    # Equal shares, transferring unused space to the other stream.
    rendered = [name + ':\n' + streams[name]['tail'] + '\n' for name in ('stdout', 'stderr')]
    sizes = [len(x.encode('utf-8')) for x in rendered]
    allocations = [budget // 2, budget - budget // 2]
    for i in range(2):
        if sizes[i] < allocations[i]:
            allocations[1 - i] += allocations[i] - sizes[i]
            allocations[i] = sizes[i]
    accounting = {}
    for i, name in enumerate(('stdout', 'stderr')):
        header = name + ':\n'
        room = max(0, allocations[i] - len(header) - 1)
        raw = raw_tails[name] if raw_tails is not None else streams[name]['tail'].encode('utf-8')
        full = raw.decode('utf-8', 'replace')
        retained = len(raw)
        if len(full.encode('utf-8')) <= room:
            tail = full
        else:
            mark = '[truncated]'
            # Search over raw suffixes; decoding is explicit replacement. The
            # final loop handles a split code point at the search boundary.
            low, high = 0, len(raw)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = raw[-middle:].decode('utf-8', 'replace')
                if len((mark + candidate).encode('utf-8')) <= room:
                    low = middle
                else:
                    high = middle - 1
            retained = low
            tail = mark + (raw[-retained:].decode('utf-8', 'replace') if retained else '')
            while len(tail.encode('utf-8')) > room and retained:
                retained -= 1
                tail = mark + (raw[-retained:].decode('utf-8', 'replace') if retained else '')
        part = header + tail + '\n'
        pieces.append(part)
        accounting[name] = {'display_bytes': len(tail.encode('utf-8')),
                            'represented_raw_bytes': retained,
                            'omitted_raw_bytes': len(raw) - retained,
                            'truncated': retained != len(raw)}
    text = ''.join(pieces)
    assert len(text.encode('utf-8')) <= budget
    return text, accounting


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def serialize(value, raw_tails=None):
    serialization = value['serialization']
    serialization['initial_encoded_bytes'] = 0
    serialization['final_encoded_bytes'] = 0
    serialization['metadata_skeleton_bytes'] = 0
    serialization['reductions'] = []
    for name, stream in value['streams'].items():
        serialization[name] = {'represented_raw_bytes': stream['retained_raw_bytes'], 'omitted_raw_bytes': 0}
    raw_tails = ({name: bytes(raw) for name, raw in raw_tails.items()} if raw_tails is not None
                 else {name: stream['tail'].encode('utf-8') for name, stream in value['streams'].items()})
    serialization['initial_encoded_bytes'] = len(encode(value))
    # Finite candidate; alternate streams, evicting oldest serialized bytes.
    while len(encode(value)) > JSON_LIMIT and any(s['tail'] for s in value['streams'].values()):
        for name, stream in value['streams'].items():
            raw = raw_tails[name]
            raw_tails[name] = raw[min(4096, len(raw)):]
            stream['tail'] = raw_tails[name].decode('utf-8', 'replace')
            represented = len(raw_tails[name])
            serialization[name] = {'represented_raw_bytes': represented,
                                   'omitted_raw_bytes': stream['retained_raw_bytes'] - represented}
        if 'tails' not in serialization['reductions']:
            serialization['reductions'].append('tails')
    # The fixed schema bounds metadata; these stages also protect future changes.
    if len(encode(value)) > JSON_LIMIT:
        value['diagnostics']['entries'] = []
        serialization['reductions'].append('diagnostics')
    if len(encode(value)) > JSON_LIMIT:
        value['command_display'] = ''
        value['evidence_paths'] = {key: '' for key in value['evidence_paths']}
        serialization['reductions'].append('command_paths')
    if len(encode(value)) > JSON_LIMIT and value['latest_progress']:
        for key in ('current_test', 'interruption', 'detail'):
            if key in value['latest_progress']:
                value['latest_progress'][key] = ''
        serialization['reductions'].append('progress')
    skeleton = json.loads(encode(value))
    for stream in skeleton['streams'].values():
        stream['tail'] = ''
    skeleton['command_display'] = ''
    skeleton['evidence_paths'] = {key: '' for key in skeleton['evidence_paths']}
    skeleton['diagnostics']['entries'] = []
    if skeleton['latest_progress']:
        for key in ('current_test', 'interruption', 'detail'):
            if key in skeleton['latest_progress']:
                skeleton['latest_progress'][key] = None
    serialization['metadata_skeleton_bytes'] = len(encode(skeleton))
    assert serialization['metadata_skeleton_bytes'] <= 16384
    for _ in range(4):
        serialization['final_encoded_bytes'] = len(encode(value))
    result = encode(value)
    assert len(result) <= JSON_LIMIT
    return result


def validate_parent(parent):
    # Inspect literal components, including the scratch component, before mkdir.
    path = Path(os.path.abspath(parent))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('evidence parent is a link/reparse point')
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('evidence parent component is not a directory')
    return path


def worker_main(parent, fault=''):
    """All artifact filesystem access occurs here, never in the supervisor."""
    stage = 'allocation'
    try:
        if fault == 'stall':
            time.sleep(60)
        if fault == stage:
            raise OSError('injected allocation failure')
        parent = validate_parent(parent)
        parent.mkdir(parents=True, exist_ok=True)
        validate_parent(parent)
        run = parent / ('run-' + uuid.uuid4().hex)
        run.mkdir(exist_ok=False)
        print(json.dumps({'allocated': str(run)}), flush=True)
        payload = sys.stdin.buffer.read(2 * (JSON_LIMIT + EXCERPT_LIMIT) + 65536 + 1)
        data = json.loads(payload)
        for name in ('capture.json', 'excerpt.txt'):
            stage = 'open'
            if fault == stage:
                raise OSError('injected open failure')
            temporary = run / ('.' + name + '.partial')
            with temporary.open('xb') as output:
                stage = 'write'
                if fault == stage:
                    raise OSError('injected write failure')
                output.write(data[name].encode('utf-8'))
            stage = 'finalization'
            if fault == stage:
                raise OSError('injected replacement failure')
            os.replace(temporary, run / name)
            print(json.dumps({'saved': name}), flush=True)
    except Exception as error:
        print(json.dumps({'error': bounded(stage + ': ' + str(error), 256)}), flush=True)
        return 1
    return 0


def worker_command(parent):
    return [sys.executable, '-B', str(Path(__file__).absolute()), '--persist', str(parent)]


class Persistence:
    def __init__(self, parent, diagnostics):
        self.remaining = 2.0
        self.process = None
        self.run = None
        self.saved = []
        self.diagnostics = diagnostics
        self.buffer = bytearray()
        self.status = 'incomplete'
        started = time.monotonic()
        try:
            self.process = subprocess.Popen(worker_command(parent), stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            bufsize=0, env=child_env())
            os.set_blocking(self.process.stdin.fileno(), False)
            deadline = started + self.remaining - .15
            while time.monotonic() < deadline:
                if self.receive():
                    break
                time.sleep(.01)
            if self.run is None:
                self.diagnostics.add('allocation: worker deadline or allocation failure')
                self.stop(started + self.remaining)
        except Exception as error:
            self.diagnostics.add('allocation: ' + str(error))
            self.stop(started + self.remaining)
        self.remaining = max(0, self.remaining - (time.monotonic() - started))

    def receive(self):
        chunk = available_read(self.process.stdout)
        if chunk is None:
            return False
        if not chunk:
            return True
        self.buffer.extend(chunk)
        if len(self.buffer) > 16384:
            raise ValueError('oversized persistence acknowledgement')
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            ack = json.loads(line)
            if 'allocated' in ack:
                self.run = ack['allocated']
                print('self-test evidence allocated: ' + bounded(self.run, 2048), flush=True)
            if 'saved' in ack and ack['saved'] in ('capture.json', 'excerpt.txt'):
                self.saved.append(ack['saved'])
                print('self-test evidence saved: ' + bounded(str(Path(self.run) / ack['saved']), 2048), flush=True)
            if 'error' in ack:
                self.diagnostics.add(ack['error'])
                return True
        return self.run is not None

    def stop(self, deadline):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.kill()
                self.process.wait(timeout=max(.001, deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired) as error:
                self.diagnostics.add('persistence cleanup: ' + str(error))
        for pipe in (self.process.stdin, self.process.stdout):
            try:
                pipe.close()
            except OSError as error:
                self.diagnostics.add('persistence close: ' + str(error))

    def finalize(self, capture, excerpt):
        if self.run is None or self.process is None or self.remaining <= 0:
            return
        started = time.monotonic()
        deadline = started + self.remaining
        try:
            payload = encode({'capture.json': capture.decode('utf-8'), 'excerpt.txt': excerpt})
            # JSON wrapper escaping has a finite independent IPC ceiling.
            if len(payload) > 2 * (JSON_LIMIT + EXCERPT_LIMIT) + 65536:
                raise ValueError('persistence payload bound')
            offset = 0
            while offset < len(payload) and time.monotonic() < deadline - .15:
                try:
                    offset += os.write(self.process.stdin.fileno(), payload[offset:offset + CHUNK])
                except BlockingIOError:
                    time.sleep(.01)
            if offset == len(payload):
                self.process.stdin.close()
            while time.monotonic() < deadline - .15 and len(self.saved) < 2:
                self.receive()
                time.sleep(.005)
            if len(self.saved) == 2:
                self.status = 'acknowledged'
            else:
                self.diagnostics.add('finalization: worker deadline or incomplete acknowledgement')
        except Exception as error:
            self.diagnostics.add('finalization: ' + str(error))
        finally:
            self.stop(deadline)
            self.remaining = max(0, self.remaining - (time.monotonic() - started))


def run_bounded(command, *, timeout, evidence_parent, **kwargs):
    """Capture separate pipes with bounded tails; reject unsupported run options."""
    allowed = {'capture_output', 'stdout', 'stderr', 'stdin', 'input', 'check', 'text',
               'universal_newlines', 'encoding', 'errors', 'cwd', 'env', 'shell',
               'executable', 'creationflags', 'startupinfo', 'close_fds', 'pass_fds',
               'start_new_session', 'restore_signals'}
    unknown = kwargs.keys() - allowed
    if unknown:
        raise ValueError('unsupported bounded subprocess arguments: ' + ', '.join(sorted(unknown)))
    if not number(timeout):
        raise ValueError('bounded timeout must be finite and between zero and 10^12 seconds')
    capture = kwargs.pop('capture_output', False)
    stdout, stderr = kwargs.pop('stdout', None), kwargs.pop('stderr', None)
    if capture:
        if stdout is not None or stderr is not None:
            raise ValueError('capture_output conflicts with stdout/stderr')
    elif stdout != subprocess.PIPE or stderr != subprocess.PIPE:
        raise ValueError('bounded capture requires separate stdout/stderr pipes')
    check = kwargs.pop('check', False)
    text = kwargs.pop('text', None)
    universal = kwargs.pop('universal_newlines', None)
    if text is not None and universal is not None and bool(text) != bool(universal):
        raise ValueError('text and universal_newlines disagree')
    encoding = kwargs.pop('encoding', None)
    errors = kwargs.pop('errors', None)
    as_text = bool(text or universal or encoding or errors)
    encoding = encoding or locale.getencoding()
    errors = errors or 'strict'
    codec = codecs.lookup(encoding)
    if as_text and codec.name not in ENCODINGS:
        raise ValueError('unsupported bounded text encoding: ' + codec.name)
    codecs.lookup_error(errors)
    supplied_input = kwargs.pop('input', None)
    if supplied_input is not None and kwargs.get('stdin') is not None:
        raise ValueError('stdin and input conflict')
    if supplied_input is not None:
        if as_text:
            if not isinstance(supplied_input, str):
                raise TypeError('text input must be str')
            supplied_input = supplied_input.encode(encoding, errors)
        elif not isinstance(supplied_input, (bytes, bytearray)):
            raise TypeError('binary input must be bytes')
        kwargs['stdin'] = subprocess.PIPE
    diagnostics = Diagnostics()
    persistence = Persistence(evidence_parent, diagnostics)
    parser = Parser()
    def decoder():
        if not as_text:
            return None
        return io.IncrementalNewlineDecoder(codecs.getincrementaldecoder(encoding)(errors=errors), True)
    streams = {'stdout': Stream(decoder=decoder()), 'stderr': Stream(parser, decoder=decoder())}
    stop = threading.Event()
    threads = []
    started = time.monotonic()
    deadline = started + timeout
    process = None
    timed_out = False
    input_offset = 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   bufsize=0, env=child_env(kwargs.pop('env', None)), **kwargs)
        for name, pipe in (('stdout', process.stdout), ('stderr', process.stderr)):
            if os.name != 'nt':
                os.set_blocking(pipe.fileno(), False)
            thread = threading.Thread(target=streams[name].drain, args=(pipe, stop),
                                      name='awf-capture-' + name, daemon=True)
            threads.append(thread)
            thread.start()
        if supplied_input is not None:
            os.set_blocking(process.stdin.fileno(), False)
        elif process.stdin is not None:
            process.stdin.close()
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if supplied_input is not None and process.stdin and not process.stdin.closed:
                try:
                    if input_offset < len(supplied_input):
                        input_offset += os.write(process.stdin.fileno(), supplied_input[input_offset:input_offset + CHUNK])
                    else:
                        process.stdin.close()
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    process.stdin.close()
            time.sleep(.01)
    finally:
        if process is not None:
            kill_deadline = time.monotonic() + 2
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=max(.001, kill_deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired) as error:
                    diagnostics.add('child cleanup: ' + str(error))
            join_deadline = time.monotonic() + 1
            # One shared allowance, with cancellation time reserved inside it.
            for thread in threads:
                thread.join(max(0, join_deadline - .06 - time.monotonic()))
            stop.set()
            for thread in threads:
                thread.join(max(0, join_deadline - time.monotonic()))
            for pipe in (process.stdout, process.stderr, process.stdin):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError as error:
                        diagnostics.add('child pipe close: ' + str(error))
        else:
            persistence.stop(time.monotonic() + persistence.remaining)
    elapsed = min(MAX_SECONDS, time.monotonic() - started)
    for name, stream in streams.items():
        if not stream.eof:
            diagnostics.add(name + ': collection stopped before EOF; total child output unknown')
        if stream.error:
            diagnostics.add(name + ': ' + stream.error)
    snapshots = {name: stream.snapshot() for name, stream in streams.items()}
    parser_snapshot = parser.snapshot()
    raw_tails = {name: bytes(stream.tail) for name, stream in streams.items()}
    progress, assertion_accounting = format_tails(snapshots, ASSERT_LIMIT, raw_tails)
    metadata = bounded('outcome=' + ('timeout' if timed_out else 'exit') + '\n' +
                       json.dumps(parser.phases) + '\nlatest_progress=' + json.dumps(parser.latest), 4096)
    tails, excerpt_accounting = format_tails(snapshots, EXCERPT_LIMIT - len((metadata + '\n').encode('utf-8')), raw_tails)
    excerpt = metadata + '\n' + tails
    paths = {name: bounded(str(Path(persistence.run) / name), 2048)
             for name in ('capture.json', 'excerpt.txt')} if persistence.run else {}
    command_original = repr(command)
    command_display = bounded(command_original, 2048)
    field_retention = dict(parser.fields)
    display_fields = {'command_display': (command_original, command_display)}
    if persistence.run:
        display_fields.update({name: (str(Path(persistence.run) / name), display)
                               for name, display in paths.items()})
    for name, (original, display) in display_fields.items():
        field_retention[name] = {'original_bytes': min(MAX_COUNT, len(original.encode('utf-8', 'replace'))),
                                 'retained_bytes': len(display.encode('utf-8')),
                                 'truncated': original != display}
    unformatted_bytes = sum(len((name + ':\n' + stream['tail'] + '\n').encode('utf-8'))
                            for name, stream in snapshots.items())
    value = {'schema_version': 1, 'run_id': Path(persistence.run).name if persistence.run else None,
             'outcome': 'timeout' if timed_out else 'exit', 'returncode': process.returncode,
             'timeout_seconds': timeout, 'parent_elapsed_seconds': elapsed,
             'command_display': command_display, 'command_truncated': command_display != command_original,
             'evidence_paths': paths, 'path_displays_truncated': any('[truncated]' in p for p in paths.values()),
             'phases': parser.phases, 'latest_progress': parser.latest,
             'streams': snapshots, 'parser': parser_snapshot, 'field_retention': field_retention,
             'serialization': {
                 'assertion': {'streams': assertion_accounting, 'initial_encoded_bytes': unformatted_bytes,
                               'final_encoded_bytes': len(progress.encode('utf-8'))},
                 'excerpt': {'streams': excerpt_accounting,
                             'initial_encoded_bytes': unformatted_bytes + len((metadata + '\n').encode('utf-8')),
                             'final_encoded_bytes': len(excerpt.encode('utf-8'))}},
             'cleanup': {'child_reaped': process.poll() is not None,
                         'readers_stopped': all(not t.is_alive() for t in threads)},
             'persistence': {'allocation_acknowledged': persistence.run is not None,
                             'finalization': 'worker acknowledgement required; see supervisor log'},
             'diagnostics': {'entries': diagnostics.entries, 'field_retention': diagnostics.fields, **diagnostics.counts}}
    try:
        capture_bytes = serialize(value, raw_tails)
        persistence.finalize(capture_bytes, excerpt)
    except Exception as error:
        diagnostics.add('serialization: ' + str(error))
        persistence.stop(time.monotonic() + persistence.remaining)
    for entry in diagnostics.entries:
        print('self-test evidence diagnostic: ' + entry, file=sys.stderr, flush=True)
    print(progress, end='', flush=True)
    if timed_out:
        failure = subprocess.TimeoutExpired(command, timeout,
                                            output=bytes(streams['stdout'].tail), stderr=bytes(streams['stderr'].tail))
        raise AssertionError(f'subprocess timeout: elapsed={elapsed:.3f}s; limit={float(timeout):.3f}s; '
                             f'command={command_display}\n{progress}') from failure
    def returned(stream):
        raw = bytes(stream.tail)
        if not as_text:
            return raw
        if stream.decode_error is not None:
            raise stream.decode_error
        return stream.text_tail
    result = subprocess.CompletedProcess(command, process.returncode,
                                         returned(streams['stdout']), returned(streams['stderr']))
    if check:
        result.check_returncode()
    return result


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--persist':
        raise SystemExit(worker_main(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ''))
    raise SystemExit('internal persistence worker only')
