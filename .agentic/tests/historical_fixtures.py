"""Byte-exact historical test sources; never consult checkout Git history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent / 'fixtures/historical'


def fixture_index():
    value = json.loads((FIXTURES / 'index.json').read_bytes())
    if value['format'] != 'awf-historical-test-fixtures-1':
        raise AssertionError('Unsupported historical fixture index')
    return value


def historical_bytes(commit, path):
    matches = [entry for entry in fixture_index()['sources']
               if entry['commit'] == commit and entry['path'] == path]
    if len(matches) != 1:
        raise AssertionError('Historical fixture identity missing or ambiguous: ' + commit + ':' + path)
    entry = matches[0]
    raw = (FIXTURES / entry['fixture']).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    digest = hashlib.sha256(raw).hexdigest()
    if (len(raw) != entry['length'] or blob != entry['blob']
            or digest != entry['sha256']):
        raise AssertionError('Historical fixture integrity mismatch: ' + commit + ':' + path)
    return raw
