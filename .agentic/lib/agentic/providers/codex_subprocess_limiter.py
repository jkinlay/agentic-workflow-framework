"""Trusted opt-in bridge for existing Codex subprocess launch surfaces.

No factory, runtime observer or qualification receipt ships. Enrollment is an
in-process host operation, never a setting, ticket field or serialized flag.
The existing adapter retains its process containment, output and settlement.
"""
from copy import deepcopy
from pathlib import Path
import uuid

from ..budget_enrollment import encoded, require
from ..reservation_estimator import estimate_reservation
from ..token_limiter import digest, token_count
from .codex_token_limiter import CodexTokenLimiter


class CodexSubprocessLimiter(CodexTokenLimiter):
    """Qualify the exact adapter launch, then bind its observed reservation.

The trusted host supplies observe_estimate(binding): minutes and difficulty.
observe_runtime validates argv, root, effective configuration and native flags
without model work. Receipt authentication remains the independent host port.
"""
    def _context(self, selected, tokens, argv, cwd):
        require(Path(cwd).resolve(strict=True) == self.project_root,
                'Budget launch project root changed')
        require(isinstance(argv, list) and argv and argv[0] == 'exec'
                and '--ephemeral' in argv and '--json' in argv
                and not any(arg in ('resume', '--resume', 'fork') for arg in argv)
                and not any('rollout_budget' in arg for arg in argv),
                'Budget launch requires fresh arguments without budget overrides')
        models = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg in ('--model', '-m')]
        efforts = [arg.split('=', 1)[1].strip('"') for arg in argv
                   if arg.startswith('model_reasoning_effort=')]
        require(models == [selected['model']] and efforts == [selected['reasoning_effort']],
                'Budget launch route differs from selected route')
        settings = {'features.rollout_budget.enabled': True,
                    'features.rollout_budget.enforce_limit': True,
                    'features.rollout_budget.limit_tokens': tokens,
                    # Retain exact TOML numeric text in integer-only AWF records.
                    'features.rollout_budget.sampling_token_weight': '1.0',
                    'features.rollout_budget.prefill_token_weight': '1.0'}
        flags = [arg for key, value in settings.items()
                 for arg in ('-c', key + '=' + str(value).lower())]
        # Before the final stdin marker; no adapter sandbox/output flag changes.
        require(argv[-1] == '-', 'Budget launch requires stdin input')
        applied = argv[:-1] + flags + ['-']
        descriptor = dict(argv=applied)
        runtime = self._runtime(settings, selected['model'], selected['reasoning_effort'],
                                descriptor=descriptor)
        return dict(runtime, provider='codex', model=selected['model'],
            effort=selected['reasoning_effort'], settings=settings, argv=applied,
            launch_root_sha256=digest(str(self.project_root)),
            counter_baseline_tokens=0, mode='fresh-ephemeral-adapter',
            counting='input-including-cache-plus-output')

    def admission(self, binding, selected, request, *, argv, cwd):
        observed = dict(request=deepcopy(request), capabilities={'models': {
            selected['model']: [selected['reasoning_effort']]}}, limits_enforced=False)
        self._prepared = None
        estimator = getattr(self.host, 'observe_estimate', None)
        estimate = estimator(deepcopy(binding)) if callable(estimator) else None
        if estimate is None:
            return observed
        require(isinstance(estimate, dict) and set(estimate) == {'minutes', 'difficulty'},
                'BUDGET_NOT_READY: reservation_estimate unavailable')
        estimate = estimate_reservation(**estimate)
        context = self._context(selected, estimate['reservation_tokens'], argv, cwd)
        receipt = self._qualification(context)
        observed['limits_enforced'] = receipt is not None
        if receipt is not None:
            observed['request']['reservation_tokens'] = estimate['reservation_tokens']
            self._prepared = dict(binding=deepcopy(binding), estimate=estimate,
                context=context, receipt=receipt, selected=deepcopy(selected), argv=deepcopy(argv))
        else:
            self._prepared = None
        return observed

    def reserve(self, binding, reservation):
        pending = getattr(self, '_prepared', None)
        require(pending is not None and binding == pending['binding'],
                'BUDGET_NOT_READY: qualification_receipt unavailable')
        observed = self.host.observe_reservation(deepcopy(binding), reservation['run_id'])
        require(isinstance(observed, dict) and set(observed) == {'binding', 'reservation_id',
            'reservation_tokens', 'policy_hash', 'operating_hash'}
            and observed['binding'] == binding and observed['reservation_id'] == reservation['run_id']
            and token_count(observed['reservation_tokens'], positive=True)
            and observed['reservation_tokens'] == reservation['reservation_tokens']
            == pending['estimate']['reservation_tokens'], 'Observed reservation differs from qualified estimate')
        from ..token_limiter import sha256_text
        require(all(sha256_text(observed[key]) for key in ('policy_hash', 'operating_hash')),
                'Observed reservation policy binding unavailable')
        plan = dict(observed, attempt_id=str(uuid.uuid4()), qualification_context=pending['context'],
            qualification_receipt=pending['receipt'], estimate=pending['estimate'],
            selected=pending['selected'], argv=pending['argv'],
            launch_root_sha256=digest(str(self.project_root)), limits_enforced=True)
        self._prepared = None
        return self.journal.create(plan)

    def launch(self, plan, *, argv, cwd):
        retained = self.journal.read(plan['attempt_id'])['plan']
        require(encoded(plan) == encoded(retained), 'Budget launch plan changed')
        require(digest(str(self.project_root)) == plan['launch_root_sha256'],
                'Budget launch project root changed')
        require(argv == plan['argv'], 'Budget launch arguments changed')
        context = self._context(plan['selected'], plan['reservation_tokens'], argv, cwd)
        require(encoded(context) == encoded(plan['qualification_context']), 'Budget launch runtime changed')
        receipt = self._qualification(context)
        require(receipt is not None and encoded(receipt) == encoded(plan['qualification_receipt']),
                'BUDGET_NOT_READY: qualification_receipt changed')
        observed = self.host.observe_reservation(deepcopy(plan['binding']), plan['reservation_id'])
        require(encoded(observed) == encoded({key: plan[key] for key in ('binding', 'reservation_id',
            'reservation_tokens', 'policy_hash', 'operating_hash')}), 'Budget launch reservation changed')
        self.journal.claim(plan['attempt_id'])
        return deepcopy(context['argv'])

    def complete(self, plan, *, actual_tokens, success, lower_bound_tokens=None):
        saved = self.journal.read(plan['attempt_id'])
        require(encoded(plan) == encoded(saved['plan']), 'Budget completion plan changed')
        if saved['snapshot']['state'] == 'FINISHED':
            return saved['snapshot']['outcome']
        known = token_count(actual_tokens)
        lower_bound = actual_tokens if known else lower_bound_tokens
        require(lower_bound is None or token_count(lower_bound), 'Invalid diagnostic token lower bound')
        self.observe(plan['attempt_id'], dict(sequence=1,
            event_id=digest(dict(tokens=actual_tokens, lower_bound=lower_bound, success=success)),
            counter_tokens=lower_bound if lower_bound is not None else 0, complete=known, unknown=not known))
        if known and actual_tokens > plan['reservation_tokens']:
            self.stop(plan['attempt_id'], reason='TOKEN_LIMIT')
        return self.finish(plan['attempt_id'], success=success)


def enrolled_limiter(host, binding, cwd):
    """A trusted callable supplies the instance; configuration cannot enroll."""
    bridge = getattr(host, '_pre_run_bridges', {}).get(binding['run_id'])
    if bridge is not None:
        from ..pre_run_budgeting import PreRunSubprocessLimiter
        return PreRunSubprocessLimiter(*bridge, binding)
    context = getattr(host, '_pre_run_contexts', {}).get(binding['project_id'])
    if context is not None:
        from ..pre_run_budgeting import restore_launch
        restored = restore_launch(context, binding)
        if restored is not None:
            return restored
    factory = getattr(host, '_token_limiter_factory', None)
    if factory is None:
        return None
    limiter = factory(deepcopy(binding), Path(cwd))
    require(isinstance(limiter, CodexSubprocessLimiter), 'Trusted Codex subprocess limiter required')
    return limiter
