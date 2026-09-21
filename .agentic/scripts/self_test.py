#!/usr/bin/env python3
"""Run complete source or installed-package validation without live effects."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import re
import sys
import time
import unittest
from urllib.parse import unquote
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION


def complete_form(value, schema, schemas):
    if '$ref' in schema:
        return complete_form(value, schemas[schema['$ref'].split(':')[-1]], schemas)
    if 'oneOf' in schema:
        if value is None:
            return
        return complete_form(value, next(s for s in schema['oneOf'] if s.get('type') != 'null'), schemas)
    if schema.get('type') == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise ValueError('Draft form is missing fields or contains stale fields')
        for key, child in value.items():
            complete_form(child, schema['properties'][key], schemas)
    if schema.get('type') == 'array':
        if not isinstance(value, list) or len(value) < schema.get('minItems', 0):
            raise ValueError('Draft form is missing required example items')
        for child in value:
            complete_form(child, schema['items'], schemas)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--checks-only', action='store_true', help='Explicit default: component checks without release qualification; review remains NOT_PROVIDED')
    mode.add_argument('--release', action='store_true', help='Require pinned current independent reviews and qualify source release acceptance')
    parser.add_argument('--expected-manifest-sha256')
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--expected-reviews-sha256')
    parser.add_argument('--review-required-paths', type=Path)
    parser.add_argument('--expected-review-required-paths-sha256')
    args = parser.parse_args(argv)
    if args.report and args.report.resolve().is_relative_to(ROOT):
        parser.error('Write validation reports outside the source/installed root')
    if args.report and args.report.exists():
        parser.error('Use a new report path; preserve existing reports and review inputs')
    started = time.monotonic()
    report = {'template_version': VERSION, 'profile': 'manual_reference', 'execution_authority': False,
        'tested_components': ['offline_evidence', 'review_tiers_and_cap_dispositions', 'jira_lifecycle_mirroring',
            'closeout_git_binding', 'evidence_digests', 'host_preflight', 'named_resource_leases',
            'synthetic_host_review_loop', 'local_git_amendment',
            'routine_autonomy_and_progress', 'synthetic_jira_stream_planning',
            'native_writer_capacity', 'project_continuation', 'unsent_draft_adapter',
            'balanced_model_routing', 'routing_budget_ledger', 'shadow_routing_evidence'],
        'created_at': datetime.now(timezone.utc).isoformat(), 'python': sys.version,
        'platform': platform.platform(), 'checks': {}, 'dependencies': {}, 'status': 'FAILED',
        'release_qualified': False, 'current_review': {'status': 'NOT_PROVIDED'}}
    try:
        from agentic import ValidationError
        from agentic.canonical import load, sha256
        from agentic.contracts import Contracts
        from agentic.installer import verify_installed
        from agentic.lifecycle import validate_workflow
        for package in ['PyYAML', 'jsonschema', 'attrs', 'jsonschema-specifications', 'referencing', 'rpds-py', 'typing-extensions']:
            report['dependencies'][package] = importlib.metadata.version(package)
        report['source_manifest_sha256'] = verify_installed(ROOT)
        report['checks']['integrity'] = 'PASS'
        review_values = (args.expected_manifest_sha256, args.reviews, args.expected_reviews_sha256,
                         args.review_required_paths, args.expected_review_required_paths_sha256)
        has_review_arguments = any(value is not None for value in review_values)
        if args.checks_only and has_review_arguments:
            raise ValueError('Do not combine --checks-only with current-review arguments')
        release_mode = args.release or has_review_arguments
        if (ROOT / 'MANIFEST.json').exists():
            sys.path.insert(0, str(ROOT / 'scripts'))
            if release_mode:
                from release_review import review_source
                report['current_review'] = review_source(ROOT, args.expected_manifest_sha256,
                    args.reviews, args.expected_reviews_sha256, args.review_required_paths,
                    args.expected_review_required_paths_sha256)
        else:
            if release_mode:
                raise ValueError('Source release qualification does not apply to installed component checks')
            report['current_review'] = {'status': 'NOT_APPLICABLE', 'reason': 'Installed component check; source release review is established separately by archive acceptance'}
        contracts = Contracts(ROOT / '.agentic/schemas')
        operating_schemas = {'operating-config', 'operating-change', 'operating-recommendation', 'operating-epics'}
        if len(contracts.schemas) != 38 or not operating_schemas <= set(contracts.schemas):
            raise ValueError('Expected complete catalog of 34 evidence/governance and four operating schemas')
        validate_workflow(load(ROOT / '.agentic/workflow.yaml'))
        contracts.validate('project-config', load(ROOT / '.agentic/PROJECT_CONFIG.yaml'))
        report['checks']['schema_catalog_and_workflow'] = 'PASS'
        from jsonschema import Draft202012Validator
        for name in ['critic','worker']:
            Draft202012Validator.check_schema(load(ROOT / f'.agentic/review-loop/{name}-result.schema.json'))
        host_default = load(ROOT / '.agentic/review-loop/host-config.example.json')
        if host_default['automation_default'] is not True or host_default['qualification']['sandbox_verified'] is not False:
            raise ValueError('Host loop must default to automation with explicit incomplete qualification')
        report['checks']['host_loop_output_schemas'] = 2
        report['checks']['default_automatic_policy_unqualified_until_enrolled'] = 'PASS'
        from agentic.providers.github import validate_ruleset_template
        validate_ruleset_template(load(ROOT / '.agentic/templates/awf-main-ruleset.json'))
        report['checks']['repository_ruleset_template'] = 'PASS'
        forms = list((ROOT / '.agentic/templates').glob('*.yaml')) + [
            path for path in (ROOT / '.agentic/templates').glob('*.json')
            if path.name != 'awf-main-ruleset.json']
        covered = set()
        for path in forms:
            value = load(path)
            if value.get('status') != 'UNFILLED' or value.get('template_for') not in contracts.schemas:
                raise ValueError(f'Unmarked or unknown draft form: {path.name}')
            name = value['template_for']
            if name in covered:
                raise ValueError('Duplicate record form')
            covered.add(name)
            complete_form(value['record'], contracts.schemas[name], contracts.schemas)
            try:
                contracts.validate(name, value)
            except ValidationError:
                pass
            else:
                raise ValueError('Unfilled wrapper accepted as runtime evidence')
        # Operating records are emitted by their commands; they are not
        # unfilled evidence-form wrappers.
        if covered != set(contracts.schemas) - {'candidate','project-config','evidence-bundle'} - operating_schemas:
            raise ValueError('Incomplete form catalog')
        report['checks']['complete_unfilled_forms'] = len(forms)
        roots = [ROOT / '.agentic']
        if (ROOT / 'MANIFEST.json').exists():
            roots.append(ROOT / 'scripts')
            roots.append(ROOT / 'global')
        code = sorted(p for root in roots for p in root.rglob('*.py'))
        for path in code:
            compile(path.read_bytes(), str(path), 'exec')
        report['checks']['python_syntax_files'] = len(code)
        report['code_sha256'] = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()) for p in code}
        markdown = list((ROOT / '.agentic').rglob('*.md')) + [ROOT / 'AGENTS.md', ROOT / '.github/PULL_REQUEST_TEMPLATE.md']
        if (ROOT / 'MANIFEST.json').exists():
            markdown += list(ROOT.glob('*.md')) + list((ROOT / 'global').rglob('*.md'))
        links = 0
        for path in set(markdown):
            content = path.read_text(encoding='utf-8')
            for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^\s)]+)\)', content):
                target = unquote(target.strip('<>').split('#', 1)[0])
                if not target or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', target):
                    continue
                if not (path.parent / target).exists():
                    raise ValueError(f'Broken document link: {path.relative_to(ROOT)} -> {target}')
                links += 1
        report['checks']['local_markdown_links'] = links
        if (ROOT / 'MANIFEST.json').exists():
            sys.path.insert(0, str(ROOT / 'scripts'))
            from release_hygiene import check_release
            report['checks']['source_release_hygiene'] = check_release(ROOT)
            report['tested_components'].append('real_source_release_hygiene')
        else:
            report['checks']['source_release_hygiene'] = 'NOT_APPLICABLE: installed runtime has no source generators'
        suite = unittest.defaultTestLoader.discover(str(ROOT / '.agentic/tests'))
        external_tests = ROOT / '.agentic/external-review/claude/tests'
        if external_tests.is_dir():
            suite.addTests(unittest.TestLoader().discover(str(external_tests)))
            report['tested_components'].append('offline_external_review_adapter')
        if (ROOT / 'MANIFEST.json').exists() and (ROOT / 'global/awf/tests').is_dir():
            suite.addTests(unittest.TestLoader().discover(str(ROOT / 'global/awf/tests')))
            report['tested_components'].append('local_release_discovery')
        if (ROOT / 'MANIFEST.json').exists() and (ROOT / 'scripts/tests').is_dir():
            suite.addTests(unittest.TestLoader().discover(str(ROOT / 'scripts/tests')))
            report['tested_components'].append('local_catalog_publication')
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        print(output.getvalue())
        report['tests'] = {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
            'successful': result.wasSuccessful()}
        report['test_log'] = output.getvalue()
        report['status'] = 'PASS' if result.wasSuccessful() else 'FAILED'
        if report['current_review'].get('release_qualified') is True:
            final_review = review_source(ROOT, args.expected_manifest_sha256,
                args.reviews, args.expected_reviews_sha256, args.review_required_paths,
                args.expected_review_required_paths_sha256)
            if final_review != report['current_review']:
                raise ValueError('Current review inputs changed during source validation')
        report['release_qualified'] = result.wasSuccessful() and report['current_review'].get('release_qualified') is True
    except Exception as exc:
        report['status'] = 'FAILED'
        report['release_qualified'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], file=sys.stderr)
    report['review'] = report['current_review']['status']
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(report, indent=2) + '\n')
        except OSError as error:
            report.update(status='FAILED', release_qualified=False, report_written=False,
                          report_write_error=type(error).__name__,
                          error='Could not create the new report; existing evidence was not overwritten')
    print(json.dumps({k: v for k,v in report.items() if k not in {'test_log','code_sha256'}}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
