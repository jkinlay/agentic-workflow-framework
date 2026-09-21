"""Combine local operating diagnostics with governance acceptance, without rehashing policy."""
from __future__ import annotations

from copy import deepcopy

from . import ValidationError
from .canonical import load_yaml
from .operating import inspect_operating, operating_ceiling, validate_operating
from .safeio import Tree


def with_operating(root, governance, report):
    """A full current project needs both layers; keep governance's policy hash intact."""
    result = deepcopy(report)
    # Source verification has no mutable audit state: reading a pinned template
    # must not create a lock file and invalidate its complete manifest. Normal
    # installed projects always use the required file and transaction-aware lock.
    with Tree(root) as tree:
        source_template = tree.inspect('MANIFEST.json') is not None and tree.inspect('.agentic/installed-manifest.json') is None
        if source_template:
            try:
                value = validate_operating(load_yaml(tree.read('OPERATING_CONFIG.yaml')), governance)
                operating = {'status': 'ACCEPTED', 'hash': value.operating_hash, 'source': value.source,
                             'streams': value.count, 'last_change_id': None, 'refusals': [],
                             'provenance': 'source_template', **operating_ceiling(governance)}
            except (ValidationError, OSError, ValueError) as exc:
                operating = {'status': 'REJECTED', 'hash': None, 'source': None, 'streams': None,
                             'last_change_id': None, 'diagnostic': str(exc),
                             'refusals': getattr(exc, 'refusals', [{'path': '$', 'reason': 'Restore the verified source operating template'}])}
        else:
            operating = inspect_operating(root, governance)
    result['operating'] = operating
    if operating['status'] != 'ACCEPTED':
        result['status'] = 'REJECTED'
        for item in operating['refusals']:
            result['unresolved'].append({
                'path': 'OPERATING_CONFIG.yaml:' + item['path'],
                'reason': item['reason'],
                'flag': item.get('remedy', 'Correct OPERATING_CONFIG.yaml through workflow.py operating set; governance changes require a PR'),
                'operating_refusal': deepcopy(item),
            })
    return result
