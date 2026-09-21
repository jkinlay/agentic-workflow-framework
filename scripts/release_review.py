"""Validate current review claims against exact verified release bytes.

This checks evidence consistency, not reviewer identity or review quality.
Historical reviews belong in separately labelled records, never as overrides of
a stale current claim. Call only with the archive/source bytes already verified
by release integrity checks.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import math
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError, VERSION
from agentic.installer import verify_release
from agentic.safeio import Tree


def validate_current_reviews(records, source_files, version, required_paths=()):
    if not isinstance(records, list) or not 1 <= len(records) <= 32:
        raise ValidationError('Current release needs a bounded nonempty review list')
    if not isinstance(source_files, dict) or not source_files or any(not isinstance(v, bytes) for v in source_files.values()):
        raise ValidationError('Review validation requires verified release byte contents')
    ids, coverage = set(), {}
    for record in records:
        if not isinstance(record, dict) or record.get('status') != 'PASS' or record.get('scope_kind') != 'current_release' or record.get('release') != version:
            raise ValidationError('Review must explicitly pass for this current release')
        identity = record.get('review_id')
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 200 or identity in ids:
            raise ValidationError('Review identity must be nonempty and unique')
        ids.add(identity)
        hashes = record.get('reviewed_file_sha256')
        if not isinstance(hashes, dict) or not hashes:
            raise ValidationError('Current review must identify reviewed file bytes')
        for name, expected in hashes.items():
            if not isinstance(name, str) or not name or '\\' in name or ':' in name or any(p in ('', '.', '..') for p in name.split('/')) or PurePosixPath(name).is_absolute():
                raise ValidationError('Review contains an invalid release-relative path')
            if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
                raise ValidationError('Review digest must be a lowercase SHA-256')
            if name not in source_files or hashlib.sha256(source_files[name]).hexdigest() != expected:
                raise ValidationError(f'Current review bytes differ: {identity}: {name}')
            coverage.setdefault(name, []).append(identity)
    missing = set(required_paths) - set(coverage)
    if missing:
        raise ValidationError('Current review coverage missing: ' + ', '.join(sorted(missing)))
    return {'status': 'PASS', 'release': version, 'scope_kind': 'current_release',
            'review_ids': sorted(ids), 'reviewed_files': len(coverage),
            'reviewed_file_sha256': {name: hashlib.sha256(source_files[name]).hexdigest() for name in sorted(coverage)},
            'coverage': {name: sorted(ids) for name, ids in sorted(coverage.items())},
            'authenticates_reviewers': False}


def pinned_json(path, expected, source):
    if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
        raise ValidationError('External review inputs require lowercase SHA-256 pins')
    path = Path(path).resolve(strict=True)
    if path.is_relative_to(Path(source).resolve()):
        raise ValidationError('Review evidence and coverage scope must be outside the source release')
    with path.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValidationError('External review input size or digest mismatch')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError('Duplicate review document key: ' + key)
            result[key] = value
        return result
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValidationError('Non-finite review metadata')
        return value
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_float=number, parse_constant=number)


def review_source(source, manifest_sha256, reviews, reviews_sha256,
                  required_paths=None, required_paths_sha256=None):
    """Verify every current claim, with all-content or pinned operator coverage."""
    source = Path(source).resolve(strict=True)
    if not isinstance(manifest_sha256, str) or re.fullmatch('[0-9a-f]{64}', manifest_sha256) is None:
        raise ValidationError('Current review requires an independently approved manifest SHA-256')
    with Tree(source) as tree:
        manifest, files = verify_release(tree, manifest_sha256)
    if reviews is None or reviews_sha256 is None:
        raise ValidationError('Current release review evidence and its SHA-256 are required')
    document = pinned_json(reviews, reviews_sha256, source)
    records = document if isinstance(document, list) else [document]
    if (required_paths is None) != (required_paths_sha256 is None):
        raise ValidationError('Coverage scope requires both its path and SHA-256')
    required = sorted(files)
    scope = 'all_manifest_content'
    if required_paths is not None:
        plan = pinned_json(required_paths, required_paths_sha256, source)
        if (not isinstance(plan, dict) or set(plan) != {'format', 'release', 'paths'}
                or plan['format'] != 'awf-review-coverage-1' or plan['release'] != VERSION
                or not isinstance(plan['paths'], list) or not plan['paths']
                or any(not isinstance(p, str) or p not in files for p in plan['paths'])
                or len(plan['paths']) != len(set(plan['paths']))):
            raise ValidationError('Coverage must name unique existing content paths for this release')
        required = sorted(plan['paths'])
        scope = 'pinned_operator_scope'
    result = validate_current_reviews(records, files, VERSION, required)
    return {**result, 'source_manifest_sha256': manifest,
            'reviews_sha256': reviews_sha256, 'required_paths_sha256': required_paths_sha256,
            'required_paths': required, 'coverage_basis': scope,
            'scope_completeness': 'All manifest content' if scope == 'all_manifest_content' else 'Operator assertion; completeness against project history is not independently established',
            'release_qualified': True, 'execution_authority': False}


def add_review_arguments(parser, required=True):
    parser.add_argument('--reviews', type=Path, required=required, help='External current review record or list; never inside the release')
    parser.add_argument('--expected-reviews-sha256', required=required)
    parser.add_argument('--review-required-paths', type=Path, help='Optional externally approved coverage document; default covers every manifest content file')
    parser.add_argument('--expected-review-required-paths-sha256')


def review_arguments(args):
    values = ['--reviews', str(args.reviews), '--expected-reviews-sha256', args.expected_reviews_sha256]
    if args.review_required_paths is not None:
        values += ['--review-required-paths', str(args.review_required_paths),
                   '--expected-review-required-paths-sha256', args.expected_review_required_paths_sha256]
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    add_review_arguments(parser)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(review_source(args.source, args.expected_manifest_sha256, args.reviews,
                         args.expected_reviews_sha256, args.review_required_paths,
                         args.expected_review_required_paths_sha256), indent=2))
        return 0
    except (ValidationError, OSError, ValueError, TypeError, RecursionError) as error:
        print(json.dumps({'status': 'REJECTED', 'release_qualified': False, 'reason': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
