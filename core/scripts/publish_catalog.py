"""Publish a local release locator only after the complete archive check passes."""
from pathlib import Path
import argparse
import json
import math
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION, ValidationError
from agentic.canonical import loads, sha256
from agentic.installer import install_lock, verify_release
from agentic.safeio import Tree


def read_validation_report(path):
    # Operational report metadata contains fractional elapsed seconds. It is
    # separate from the integer-only canonical evidence/signature protocol.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError('Duplicate validation report key: ' + key)
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValidationError('Non-finite validation report number: ' + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            invalid_constant(value)
        return result
    raw = path.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise ValidationError('Validation report exceeds the supported size')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=invalid_constant, parse_float=finite_float)


def publish(source, archive, validation_report, catalog):
    source, archive, validation_report = (Path(p).resolve(strict=True) for p in (source, archive, validation_report))
    catalog = Path(catalog).resolve(strict=False)
    if catalog.is_relative_to(source):
        raise ValidationError('Publish the mutable catalog outside the immutable release source')
    report = read_validation_report(validation_report)
    archive_digest = sha256(archive.read_bytes())
    manifest_digest = sha256((source / 'MANIFEST.json').read_bytes())
    if (report.get('status') != 'PASS' or report.get('validation_complete') is not True or report.get('template_version') != VERSION
            or report.get('archive_sha256') != archive_digest or report.get('manifest_sha256') != manifest_digest
            or not report.get('checks') or any(c.get('status') != 'PASS' for c in report['checks'])):
        raise ValidationError('Catalog publication requires passing final validation for these exact release bytes')
    for name in ['extracted_tests', 'installed_tests']:
        tests = report.get(name, {})
        result = tests.get('tests', {})
        if (tests.get('status') != 'PASS' or result.get('successful') is not True
                or type(result.get('run')) is not int or result['run'] <= 0
                or type(result.get('failures')) is not int or result['failures'] != 0
                or type(result.get('errors')) is not int or result['errors'] != 0):
            raise ValidationError('Fresh source and installed validation must both pass')
    with Tree(source) as tree:
        verify_release(tree, manifest_digest)
        expected = {name: tree.read(name) for name in tree.file_list(exclude_root_git=True)}
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        roots = {name.split('/', 1)[0] for name in names}
        if len(roots) != 1 or len(names) != len(set(names)) or any('/' not in name for name in names):
            raise ValidationError('Archive needs exactly one unambiguous release root')
        prefix = next(iter(roots)) + '/'
        if {name[len(prefix):] for name in names} != set(expected):
            raise ValidationError('Archive and source memberships differ')
        if any(zipped.read(prefix + name) != data for name, data in expected.items()):
            raise ValidationError('Archive and source bytes differ')
    value = {'format': 'awf-release-catalog-1', 'latest': {
        'version': VERSION, 'source_directory': str(source), 'archive': str(archive),
        'archive_sha256': archive_digest, 'manifest_sha256': manifest_digest}}
    data = (json.dumps(value, indent=2) + '\n').encode('utf-8')
    with Tree(catalog.parent) as tree, install_lock(tree, '.awf-catalog.lock'):
        if tree.inspect(catalog.name) is not None:
            old = tree.read(catalog.name)
            previous = loads(old.decode('utf-8'))
            if previous.get('format') != value['format']:
                raise ValidationError('Preserve the unrelated catalog; unexpected format')
            old_version = tuple(int(x) for x in previous['latest']['version'].split('.'))
            if old_version > tuple(int(x) for x in VERSION.split('.')):
                raise ValidationError('Do not move a published latest pointer backwards')
            if old != data:
                tree.write('.awf-catalog-history/' + sha256(old) + '.json', old)
        tree.write(catalog.name, data)
        if tree.read(catalog.name) != data:
            raise ValidationError('Published catalog readback differs')
    return {'status': 'PUBLISHED', 'catalog': str(catalog), 'version': VERSION,
            'manifest_sha256': manifest_digest, 'archive_sha256': archive_digest,
            'target_projects_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'archive', 'validation-report', 'catalog']:
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.source, args.archive, args.validation_report, args.catalog), indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc), 'next_step': 'Resolve the release identity or validation failure; keep the previous published catalog.'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
