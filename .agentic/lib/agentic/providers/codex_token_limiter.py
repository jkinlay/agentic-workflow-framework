"""Opt-in Codex native rollout limiter foundation; no qualification ships.

The host is trusted infrastructure, not deserialized input. Its observers verify
an existing reservation, executable version/platform, and an independently
accepted qualification receipt. Parsing configuration is never qualification.
Existing launch adapters require explicit trusted-host enrollment.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import time
from typing import Protocol

from .. import ValidationError
from ..budget_enrollment import encoded, require, text_id, validate_binding
from ..canonical import loads
from ..child_process import CODEX_LOG_RUNNER_POLICY
from ..safeio import run_bounded
from ..token_limiter import digest, sha256_text, token_count


class CodexLimiterHost(Protocol):
    """Implement only in protected infrastructure, never from worker assertions.

    observe_runtime independently validates the complete effective native
    configuration for the requested argv/settings/root without model work.
    It returns version, full OS/build/architecture, effective_config_sha256 and
    applied_launch_sha256 only after verifying the requested settings apply.
    observe_qualification authenticates owner acceptance, raw evidence and
    receipt bytes independently; a caller-authored receipt is insufficient.
    Protect executable/configuration bytes against replacement through launch.
    """
    def observe_runtime(self, launch): ...
    def observe_reservation(self, binding, reservation_id): ...
    def observe_qualification(self, context): ...


class CodexTokenLimiter:
    def __init__(self, journal, host, *, command, project_root, clock=time.time):
        require(isinstance(command, tuple) and bool(command)
                and all(isinstance(arg, str) and arg for arg in command), 'Trusted CLI command required')
        self.journal, self.host, self.command, self.clock = journal, host, command, clock
        self.project_root = Path(project_root).resolve(strict=True)
        require(self.project_root.is_dir(), 'Trusted project root must be a directory')
        self._cancellations = {}
        self._lock = threading.Lock()

    def _runtime(self, settings, model, effort, *, descriptor=None):
        launch = dict(settings=deepcopy(settings), model=model, effort=effort, cwd=str(self.project_root))
        if descriptor is not None:
            launch['adapter'] = deepcopy(descriptor)
        observed = self.host.observe_runtime(deepcopy(launch))
        require(isinstance(observed, dict) and set(observed) == {'cli_version', 'platform',
                    'effective_config_sha256', 'applied_launch_sha256'}
                and text_id(observed['cli_version']) and text_id(observed['platform'])
                and sha256_text(observed['effective_config_sha256'])
                and observed['applied_launch_sha256'] == digest(launch),
                'CLI runtime/effective settings observation unavailable')
        # Bind executable AND trusted wrapper files used by offline fake CLIs.
        files = []
        for index, arg in enumerate(self.command):
            if index == 0 or Path(arg).is_absolute():
                path = Path(arg)
                require(path.is_absolute() and path.is_file() and not path.is_symlink(),
                        'CLI executable/wrapper must be an absolute regular file')
                files.append(hashlib.sha256(path.read_bytes()).hexdigest())
        return dict(cli_version=observed['cli_version'], platform=observed['platform'],
            effective_config_sha256=observed['effective_config_sha256'],
            command_sha256=digest(dict(argv=self.command, files=files)))

    def _qualification(self, context):
        receipt = self.host.observe_qualification(deepcopy(context))
        if receipt is None:
            return None
        require(isinstance(receipt, dict) and set(receipt) == {'version', 'status', 'qualified_for',
            'receipt_id', 'evidence_sha256', 'expires_at', 'strict_total_tokens'}
            and type(receipt['version']) is int and receipt['version'] == 1
            and receipt['status'] == 'QUALIFIED' and encoded(receipt['qualified_for']) == encoded(context)
            and text_id(receipt['receipt_id']) and sha256_text(receipt['evidence_sha256'])
            and type(receipt['expires_at']) is int and self.clock() < receipt['expires_at']
            and receipt['strict_total_tokens'] is True, 'Exact CLI/platform/settings qualification required')
        return deepcopy(receipt)

    def prepare(self, binding, *, reservation_id, attempt_id, model, effort):
        binding = validate_binding(binding)
        require(text_id(reservation_id) and text_id(model) and isinstance(effort, str)
                and effort in {'low', 'medium', 'high', 'xhigh'},
                'Trusted reservation and model route required')
        reserved = self.host.observe_reservation(deepcopy(binding), reservation_id)
        require(isinstance(reserved, dict) and set(reserved) == {'binding', 'reservation_id',
            'reservation_tokens', 'policy_hash', 'operating_hash'}
            and reserved['binding'] == binding and reserved['reservation_id'] == reservation_id
            and token_count(reserved['reservation_tokens'], positive=True)
            and sha256_text(reserved['policy_hash']) and sha256_text(reserved['operating_hash']),
            'Observed positive reservation and policy binding required before prepare')
        settings = {'features.rollout_budget.enabled': True,
                    'features.rollout_budget.enforce_limit': True,
                    'features.rollout_budget.limit_tokens': reserved['reservation_tokens'],
                    # Retain exact TOML numeric text in integer-only AWF records.
                    'features.rollout_budget.sampling_token_weight': '1.0',
                    'features.rollout_budget.prefill_token_weight': '1.0'}
        context = dict(self._runtime(settings, model, effort), provider='codex', model=model, effort=effort,
                       settings=settings, counter_baseline_tokens=0,
                       mode='fresh-ephemeral-read-only', counting='input-including-cache-plus-output')
        receipt = self._qualification(context)
        plan = dict(reserved, attempt_id=attempt_id, qualification_context=context,
                    launch_root_sha256=digest(str(self.project_root)),
                    qualification_receipt=receipt, limits_enforced=receipt is not None)
        return self.journal.create(plan)

    def observe(self, attempt_id, observation):
        return self.journal.observe(attempt_id, observation)

    def stop(self, attempt_id, *, reason):
        receipt = self.journal.stop(attempt_id, reason=reason)
        with self._lock:
            cancellation = self._cancellations.get(attempt_id)
            if cancellation is not None:
                cancellation.set()
        return receipt

    def finish(self, attempt_id, *, success):
        return self.journal.finish(attempt_id, success=success)

    def run(self, attempt_id, *, prompt, cwd):
        """Launch once through owned-tree containment; no resume or extra flags.

        A stop receipt requests local cancellation only. Missing remote/final
        usage remains UNKNOWN; neither termination nor this result refunds it.
        """
        plan = self.journal.read(attempt_id)['plan']
        context = plan['qualification_context']
        require(context.get('mode') == 'fresh-ephemeral-read-only',
                'Standalone launch requires its own qualification mode')
        require(Path(cwd).resolve(strict=True) == self.project_root
                and digest(str(self.project_root)) == plan['launch_root_sha256'],
                'Launch project root changed after prepare')
        require(plan['limits_enforced'] is True, 'Live execution requires a qualification receipt')
        require(all(context[key] == value for key, value in
                    self._runtime(context['settings'], context['model'], context['effort']).items()),
                'CLI runtime changed after prepare')
        receipt = self._qualification(context)
        require(receipt is not None and encoded(receipt) == encoded(plan['qualification_receipt']),
                'CLI qualification changed after prepare')
        require(encoded(self.host.observe_reservation(deepcopy(plan['binding']), plan['reservation_id'])) ==
                encoded({key: plan[key] for key in ('binding', 'reservation_id', 'reservation_tokens',
                                           'policy_hash', 'operating_hash')}),
                'Reserved allowance changed after prepare')
        argv = [*self.command, 'exec', '--json', '--ephemeral', '--sandbox', 'read-only',
                '-m', context['model'], '-c', 'model_reasoning_effort="' + context['effort'] + '"']
        for key, value in context['settings'].items():
            argv += ['-c', key + '=' + str(value).lower()]
        argv.append('-')
        cancellation = threading.Event()
        with self._lock:
            self.journal.claim(attempt_id)
            self._cancellations[attempt_id] = cancellation
        decoder = _CodexEvents(self, attempt_id)
        success = False
        try:
            result = run_bounded(argv, stdin=prompt, cwd=cwd, policy=CODEX_LOG_RUNNER_POLICY,
                cancel_event=cancellation, stream_sink=decoder.feed, retain_stdout=False)
            success = result.returncode == 0
        except (OSError, ValidationError) as exc:
            # Cancellation/timeout drains owned stdout. Other transport/limit
            # failures cannot establish a complete stream, even while idle.
            if not isinstance(exc, ValidationError) or not str(exc).startswith(
                    ('SUBPROCESS_CANCELLED:', 'SUBPROCESS_TIMEOUT:')):
                decoder._record(b'unobserved-tail', unknown=True)
            self.stop(attempt_id, reason='PROCESS_FAILURE')
        finally:
            decoder.end()
            with self._lock:
                self._cancellations.pop(attempt_id, None)
        return self.finish(attempt_id, success=success)


class _CodexEvents:
    """Bounded incremental JSONL: native completed turns are per-turn deltas.

    Transport positions become durable sequences. Duplicate native completions
    cannot safely be disambiguated without provider IDs, so they poison totals.
    No raw model output, prompt, command or error message enters the journal.
    """
    MAX_LINE = 65_536

    def __init__(self, limiter, attempt_id):
        self.limiter, self.attempt_id = limiter, attempt_id
        self.pending = bytearray()
        self.sequence, self.total = 0, 0
        self.thread, self.open, self.complete = False, False, False
        self.unknown = False
        self.completions = set()

    def feed(self, channel, chunk):
        if channel != 'stdout':
            return
        self.pending.extend(chunk)
        while b'\n' in self.pending:
            raw, _, tail = self.pending.partition(b'\n')
            self.pending = bytearray(tail)
            if len(raw) > self.MAX_LINE:
                self._record(b'oversize', unknown=True)
            else:
                self._record(bytes(raw))
        if len(self.pending) > self.MAX_LINE:
            self.pending.clear()
            self._record(b'oversize', unknown=True)

    def _record(self, raw, *, unknown=False):
        self.sequence += 1
        self.unknown |= unknown
        try:
            event = loads(raw.decode('utf-8', 'strict'))
            require(isinstance(event, dict) and text_id(event.get('type')), 'Invalid CLI event')
            kind = event['type']
            if kind == 'thread.started':
                self.unknown |= self.thread or not text_id(event.get('thread_id'))
                self.thread = True
                self.complete = False
            elif kind == 'turn.started':
                self.unknown |= self.open or not self.thread
                self.open, self.complete = True, False
            elif kind == 'turn.completed':
                duplicate = digest(event) in self.completions
                self.unknown |= not self.open or duplicate
                usage = event.get('usage')
                require(isinstance(usage, dict) and all(token_count(usage.get(key))
                    for key in ('input_tokens', 'output_tokens')), 'Unknown CLI token usage')
                total = self.total if duplicate else self.total + usage['input_tokens'] + usage['output_tokens']
                require(token_count(total), 'CLI token counter overflow')
                self.total = total
                self.completions.add(digest(event))
                self.open, self.complete = False, True
            elif kind == 'turn.failed' or kind == 'error' and self.open:
                self.unknown = True
            elif kind == 'error':
                pass  # An idle process error does not reopen a completed turn.
            elif not self.open:
                self.unknown = True
        except (ValidationError, ValueError, UnicodeError, TypeError):
            self.unknown = True
        observation = dict(sequence=self.sequence, event_id=hashlib.sha256(raw).hexdigest(),
            counter_tokens=self.total, complete=self.complete and not self.open, unknown=self.unknown)
        self.limiter.observe(self.attempt_id, observation)
        plan = self.limiter.journal.read(self.attempt_id)['plan']
        if self.unknown or self.total > plan['reservation_tokens']:
            self.limiter.stop(self.attempt_id, reason='USAGE_UNKNOWN' if self.unknown else 'TOKEN_LIMIT')

    def end(self):
        if self.pending:
            self._record(b'truncated', unknown=True)
