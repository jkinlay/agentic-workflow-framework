"""Exact expected diagnostic bytes from a captured rollback contract."""
from agentic import installer
from agentic.canonical import sha256


def rollback_evidence(journal, previous=None):
    paths = [row['path'] for row in reversed(journal.get('created_directories', []))
             if row['identity'] is None]
    if not paths:
        return previous
    record = dict(format='awf-retained-journal-1', journal=journal, retained_directories=paths)
    if previous is None:
        return record
    sources = previous['records'] if previous['format'] == 'awf-retained-directories-2' else [previous]
    return dict(format='awf-retained-directories-2', records=[*sources, record],
                retained_directories=list(dict.fromkeys(previous['retained_directories'] + paths)))


def assert_rollback_evidence(test, root, journal, previous=None):
    test.assertEqual('rollback_cleanup', journal['phase'])
    expected = rollback_evidence(journal, previous)
    path = root / installer.RETAINED_JOURNAL
    if expected is None:
        test.assertFalse(path.exists())
        return None
    test.assertEqual(installer.json_bytes(expected), path.read_bytes())
    for relative in expected['retained_directories']:
        test.assertTrue((root / relative).is_dir(), relative)
    return expected


def evidence_inventory(record):
    return {} if record is None else {installer.RETAINED_JOURNAL: sha256(installer.json_bytes(record))}
