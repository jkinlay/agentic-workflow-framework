"""Independent fresh-extraction/CLI/installation smoke validation of a built ZIP."""
from pathlib import Path
import hashlib
import argparse
import json
import os
import stat
import subprocess
import sys
import uuid
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION, ValidationError
from agentic.child_process import child_env
from release_review import add_review_arguments, review_arguments, review_source
RELEASE_LINE = VERSION.removesuffix('.0')
PREFIX = f'agentic-workflow-template-v{RELEASE_LINE}/'


class ValidationStageFailure(RuntimeError):
    """A stage failed after its partial evidence was retained."""

    def __init__(self, report):
        super().__init__(report['error'])
        self.report = report


def self_test_timeout(value):
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Self-test timeout must be an integer from 60 through 3600 seconds') from exc
    if not 60 <= seconds <= 3600:
        raise argparse.ArgumentTypeError('Self-test timeout must be an integer from 60 through 3600 seconds')
    return seconds


def write_report(output, report):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name('.'+output.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as stream:
        stream.write((json.dumps(report,indent=2)+'\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)


def checked_scratch_root():
    """Return the literal scratch path only when it cannot redirect writes."""
    scratch = ROOT / '.tmp-tests'
    if scratch.exists() or scratch.is_symlink():
        metadata = os.lstat(scratch)
        reparse = getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
        junction = getattr(scratch, 'is_junction', lambda: False)()
        if stat.S_ISLNK(metadata.st_mode) or reparse or junction or scratch.resolve() != scratch:
            raise ValueError('Refusing linked or reparse-point .tmp-tests scratch directory')
    return scratch


def preflight(archive_path, approved_digest, workdir, report_path):
    """Validate paths, pin and complete inventory before creating any outputs."""
    archive_path = Path(archive_path).resolve(strict=True)
    scratch = checked_scratch_root()
    report_path = Path(report_path).resolve()
    workdir = Path(workdir or report_path.parent).resolve()
    work_in_release = workdir.is_relative_to(ROOT) and not workdir.is_relative_to(scratch)
    report_in_release = report_path.is_relative_to(ROOT) and not report_path.is_relative_to(scratch)
    if work_in_release or report_in_release:
        raise ValueError('Validation work directory and report must be outside the source release tree')
    if report_path == archive_path:
        raise ValueError('Validation report must not overwrite the approved archive')
    if report_path.exists() and report_path.is_dir():
        raise ValueError('Validation report must be a file path')
    if report_path.exists():
        raise ValueError('Validation requires a new report path; preserve the prior attempt evidence')
    if workdir.exists() and not workdir.is_dir():
        raise ValueError('Validation work directory must be a directory')
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != approved_digest:
        raise ValueError('Archive differs from the independently approved ZIP digest')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if not names or len(set(name.casefold() for name in names)) != len(names):
            raise ValueError('Empty or duplicate ZIP inventory')
        for entry in entries:
            name = entry.filename
            parts = name.split('/')
            if (entry.orig_filename != name or not name.startswith(PREFIX) or entry.is_dir() or
                    any(part in {'', '.', '..'} for part in parts) or
                    any(char in name for char in (chr(92), ':'))):
                raise ValueError('Unsafe ZIP inventory')
        if PREFIX + 'MANIFEST.json' not in names:
            raise ValueError('ZIP inventory lacks source manifest')
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC failed')
    return archive_path, workdir, report_path


def assert_bootstrap_checks(report, destination, manifest, *, configured):
    """Inspect evidence from both real installed CLI invocations, not copy status."""
    assert report['status'] == ('CONFIGURED' if configured else 'INSTALLED_UNCONFIGURED')
    assert report['installed'] is True and report['active'] is False
    observed = report['post_install_checks']
    assert len(observed) == 2
    script = str(destination.resolve() / '.agentic/scripts/workflow.py')
    for item, action in zip(observed, ('verify-installation', 'validate-config')):
        assert item['command'] == [sys.executable, '-B', '-I', script, action]
        assert item.get('execution_status') != 'NOT_RUN'
        assert isinstance(item['output'], dict)
    integrity, configuration = observed
    assert integrity['exit_code'] == 0 and integrity['diagnostic'] is None
    assert integrity['output']['integrity_valid'] is True
    assert integrity['output']['source_manifest_sha256'] == manifest
    actual = configuration['output']
    assert configuration['exit_code'] == (0 if configured else 2)
    assert actual['status'] == ('ACCEPTED' if configured else 'REJECTED')
    assert report['configuration']['observation_source'] == 'installed validate-config subprocess'
    assert report['configuration']['unresolved'] == actual['unresolved']
    assert actual['operating']['status'] == 'ACCEPTED'
    assert len(actual['operating']['hash']) == 64
    assert actual['operating']['hash'] == report['operating']['hash']
    if configured:
        assert configuration['diagnostic'] is None and actual['unresolved'] == []
        assert len(actual['policy_sha256']) == 64
        assert actual['policy_sha256'] == report['pre_install_configuration']['policy_sha256']
    else:
        assert actual['policy_sha256'] is None and actual['unresolved']
        assert all(item['path'].startswith('$.') and item['reason'] and item['flag'] for item in actual['unresolved'])


def validate(argv=None):
    parser = argparse.ArgumentParser(description="Validate an approved source ZIP through extraction, tests, installation, upgrade and deterministic rebuilding. Runs trusted package code; retain the work directory as evidence.")
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--expected-zip-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True, help='New report file path; existing attempt reports are preserved')
    parser.add_argument('--workdir', type=Path)
    add_review_arguments(parser)
    parser.add_argument('--self-test-timeout-seconds', type=self_test_timeout, default=600,
                        help='Deadline for each full source/installed self-test only: 60..3600 seconds (default: 600). Other acceptance-stage deadlines remain unchanged; timed-out runs retain a FAILED partial report and fixture directory.')
    args = parser.parse_args(argv)
    args.reviews = args.reviews.resolve()
    if args.review_required_paths is not None:
        args.review_required_paths = args.review_required_paths.resolve()
    try:
        ARCHIVE, WORK, OUTPUT = preflight(args.archive, args.expected_zip_sha256, args.workdir, args.report)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        parser.error(str(exc))
    WORK.mkdir(parents=True, exist_ok=True)
    RUN = WORK / ('extraction-' + uuid.uuid4().hex)
    RUN.mkdir()
    with zipfile.ZipFile(ARCHIVE) as archive:
        for entry in archive.infolist():
            target = RUN.joinpath(*entry.filename.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
    SOURCE = RUN / PREFIX.rstrip('/')
    MANIFEST = hashlib.sha256((SOURCE / 'MANIFEST.json').read_bytes()).hexdigest()
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    checks = []
    try:
        current_review = review_source(SOURCE, MANIFEST, args.reviews, args.expected_reviews_sha256,
                                      args.review_required_paths, args.expected_review_required_paths_sha256)
    except (ValidationError, OSError, ValueError, TypeError, RecursionError) as error:
        parser.error(str(error))
    checks.append({'check':'Current independent review covers verified source bytes','status':'PASS'})
    review_cli = review_arguments(args)

    def command(args, cwd=SOURCE, code=0, name=None, timeout=180, error_output=False):
        label = name or ' '.join(map(str,args))
        try:
            done = subprocess.run([sys.executable, '-B', *map(str,args)], cwd=cwd,
                                  env=child_env(env), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            log = RUN / 'failure.log'
            log_error = None
            try:
                log.write_text(decoded(exc.stdout)+'\n'+decoded(exc.stderr),encoding='utf-8')
            except OSError as error:
                log_error = type(error).__name__
            error = f'{label}: timed out after {timeout} seconds; acceptance is incomplete'
            partial = {'template_version':VERSION, 'status':'FAILED', 'validation_complete':False,
                       'archive':ARCHIVE.name, 'archive_sha256':args_archive_pin,
                       'manifest_sha256':MANIFEST, 'checks':checks + [{'check':label, 'status':'FAILED', 'reason':'TIMEOUT'}],
                       'stage':label, 'error':error, 'failure_kind':'TIMEOUT', 'timeout_seconds':timeout,
                       'self_test_timeout_seconds':selected_self_test_timeout, 'run_directory':str(RUN),
                       'partial_log':str(log) if log_error is None else None, 'log_write_error':log_error,
                       'report':str(OUTPUT), 'report_written':True,
                       'next_action':'Inspect the retained stage log and fixtures, resolve the timeout, then rerun complete acceptance against the same approved archive.'}
            try:
                write_report(OUTPUT, partial)
            except OSError as error:
                partial.update(report_written=False, report_write_error=type(error).__name__)
            raise ValidationStageFailure(partial) from None
        # Decode synchronously: Windows text-mode reader threads can otherwise lose
        # Unicode output while the child still exits zero and appears to pass.
        stdout, stderr = done.stdout.decode('utf-8'), done.stderr.decode('utf-8')
        if done.returncode != code:
            (RUN / 'failure.log').write_text(stdout+'\n'+stderr,encoding='utf-8')
            raise RuntimeError(f'{label}: expected {code}, got {done.returncode}. See {RUN / "failure.log"}')
        checks.append({'check':label,'status':'PASS','exit_code':done.returncode})
        print(label + ': PASS',flush=True)
        return stderr if error_output and not stdout.strip() else stdout

    selected_self_test_timeout = args.self_test_timeout_seconds
    args_archive_pin = args.expected_zip_sha256
    command(['scripts/self_test.py','--report',RUN/'default-self-test.json'],
            name='Fresh ZIP default self-test runs without review pins',timeout=selected_self_test_timeout)
    default_checks = json.loads((RUN/'default-self-test.json').read_text(encoding='utf-8'))
    assert default_checks['status']=='PASS' and default_checks['release_qualified'] is False
    assert default_checks['review']=='NOT_PROVIDED' and default_checks['current_review']=={'status':'NOT_PROVIDED'}
    command(['scripts/self_test.py','--release'],code=1,
            name='Explicit source release check still refuses missing review pins')
    command(['scripts/validate_archive.py','--archive',ARCHIVE,'--expected-zip-sha256',args_archive_pin,
             '--workdir',RUN/'missing-pins','--report',RUN/'missing-pins-report.json'],code=2,
            name='Archive acceptance still refuses missing review pins')
    assert not (RUN/'missing-pins-report.json').exists()
    command(['scripts/self_test.py','--release','--report',RUN/'extracted-self-test.json',
             '--expected-manifest-sha256',MANIFEST,*review_cli], name='Fresh ZIP extraction self-test', timeout=selected_self_test_timeout)
    workflow = '.agentic/scripts/workflow.py'
    capabilities = json.loads(command([workflow,'capabilities'],name='Capabilities CLI'))
    assert all(capabilities[k] is False for k in ['live_dispatch','live_jira_mutations','live_merge'])
    assert capabilities['separate_host_review_loop']['automatic_by_default_after_enrollment'] is True
    assert capabilities['project_coordination']['routine_authorization_prompts'] is False
    assert capabilities['project_coordination']['proactive_native_stream_agents'] is False
    assert capabilities['project_coordination']['launches_native_agents'] is False
    assert capabilities['project_coordination']['attests_active_writers'] is False
    assert capabilities['project_coordination']['native_streams_enabled_by_default'] is True
    assert capabilities['project_coordination']['default_max_parallel_tickets'] == 6
    assert capabilities['project_coordination']['default_operating_streams'] == 3
    assert capabilities['project_coordination']['requires_separate_concurrency_activation'] is False
    assert capabilities['project_coordination']['requires_reference_controller_or_broker'] is False
    assert capabilities['separate_host_review_loop']['max_active_host_ticks'] == 1
    status_cli = '.agentic/scripts/project_status.py'
    progress = json.loads(command([status_cli,'--format','json','report','.agentic/examples/project-status.json'],name='Project status names next action owner and trigger'))
    assert all(progress['next_step'][key] for key in ['action','owner','trigger'])
    assert progress['next_step']['authorization']['required'] is False
    routine = json.loads(command([status_cli,'--format','json','action','.agentic/examples/routine-action.json'],name='Routine delegation continues without owner prompt'))
    assert routine['decision']=='CONTINUE' and routine['prompt_user'] is False and routine['execution_authority'] is False
    cycle = json.loads(command([status_cli,'--format','json','cycle','.agentic/examples/project-cycle.json','--now','2026-09-11T10:00:00Z'],name='Project cycle continues independent streams and PR review during authorization wait'))
    assert cycle['status']=='RUNNING' and cycle['finish_allowed'] is False
    assert {item['reference'] for item in cycle['continue_actions']} >= {'A','C'}
    assert len(cycle['prs_requiring_review'])==1 and len(cycle['input_drafts'])==1
    assert cycle['input_drafts'][0]['submit'] is False and cycle['execution_authority'] is False
    PLANNED = RUN / 'stream-project'
    PLANNED.mkdir()
    inventory = SOURCE / '.agentic/examples/stream-input.json'
    planner = ['.agentic/scripts/plan_streams.py','--input',inventory,'--expected-input-sha256',hashlib.sha256(inventory.read_bytes()).hexdigest(),'--project-root',PLANNED]
    command(planner,code=2,name='Synthetic inventory cannot enter live planning')
    planned = json.loads(command(planner+['--allow-synthetic','--now','2026-09-11T10:00:00Z'],name='Create complete A B C Markdown and dispatch plan'))
    assert planned['stream_labels']==['A','B','C'] and planned['project_status']=='READY'
    assert planned['prompt_user'] is False and planned['execution_authority'] is False
    assert len(planned['dispatch_packets'])==3 and all(p['live_dispatch_eligible'] is False for p in planned['dispatch_packets'])
    assert planned['capacity']['effective_writer_capacity'] is None
    assert hashlib.sha256((PLANNED/'STREAMS.md').read_bytes()).hexdigest()==planned['markdown_sha256']
    assert hashlib.sha256((PLANNED/'STREAMS.json').read_bytes()).hexdigest()==planned['plan_sha256']
    observed = json.loads(command(planner+['--allow-synthetic','--now','2026-09-11T10:00:00Z','--expected-plan-sha256',planned['plan_sha256'],'--host-writer-capacity','3'],name='Refresh stream plan with pinned history and three observed writer slots'))
    assert observed['capacity']['effective_writer_capacity']==3 and len(observed['dispatch_packets'])==3
    assert observed['capacity']['host_admission_confirmed'] is False
    command(['.agentic/scripts/review_loop.py','--help'],name='Host review loop CLI entry point')
    command(['.agentic/scripts/review_loop.py','--config','.agentic/review-loop/host-config.example.json','check'],code=2,name='Unqualified host cannot launch')
    command([workflow,'verify-installation'],name='Source integrity CLI')
    command([workflow,'validate-config'],code=2,name='Unconfigured project is rejected')
    command([workflow,'validate-config','--config','.agentic/examples/PROJECT_CONFIG.yaml'],name='Fixture configuration CLI')
    gate = json.loads(command([workflow,'evaluate','.agentic/examples/evidence-bundle.json','--config','.agentic/examples/PROJECT_CONFIG.yaml','--now','2026-09-09T12:00:00Z'],name='Offline evaluator CLI'))
    assert gate['conclusion']=='READY_FOR_OWNER_AUTHORIZATION' and gate['execution_authority'] is False
    command([workflow,'evaluate','.agentic/examples/evidence-bundle.json','--config','.agentic/examples/PROJECT_CONFIG.yaml','--now','2031-01-01T00:00:00Z'],code=2,name='Stale fixture is rejected')
    DEST = RUN / 'project'
    DEST.mkdir()
    (DEST/'README.md').write_bytes(b'Existing project readme\n')
    (DEST/'.github').mkdir()
    (DEST/'.github/EXISTING.md').write_bytes(b'Existing project metadata\n')
    (DEST/'AGENTS.md').write_bytes(b'Existing owner instructions\n')
    bootstrap = ['scripts/bootstrap_project.py','--dest',DEST,'--expected-manifest-sha256',MANIFEST]
    command(bootstrap+['--dry-run'],code=2,name='CLI conflict preflight')
    assert (DEST/'AGENTS.md').read_bytes()==b'Existing owner instructions\n'
    first = json.loads(command(bootstrap+['--on-conflict','backup'],code=1,
        name='Backed-up installation reports exact unconfigured residue after both installed CLI checks'))
    assert_bootstrap_checks(first, DEST, MANIFEST, configured=False)
    expected_residue = {'$.project.name','$.project.short_name','$.github.repository',
                        '$.github.repository_id','$.github.base_branch','$.validation.commands'}
    assert {item['path'] for item in first['configuration']['unresolved']} == expected_residue
    assert first['live_automation_enabled'] is False
    assert first['installer_launches_agents'] is False
    assert first['repository_rules']=='UNOBSERVED' and first['adoption_allowed'] is True
    assert '@maintainer' in (DEST/'.github/CODEOWNERS').read_text(encoding='utf-8')
    assert '.github/CODEOWNERS' not in json.loads((DEST/'.agentic/installed-manifest.json').read_bytes())['immutable_files']
    installed_configuration = json.loads((DEST/'.agentic/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
    assert installed_configuration['execution']['max_parallel_tickets'] == 6
    installed_operating = json.loads((DEST/'OPERATING_CONFIG.yaml').read_text(encoding='utf-8'))
    assert installed_operating['streams']['count'] == 3
    assert installed_configuration['execution']['max_parallel_tickets_per_stream'] == 1
    assert installed_configuration['execution']['native_streams'] == {'enabled': True, 'dispatch_policy': 'ready_independent'}
    assert all(value is False for value in installed_configuration['controller'].values())
    assert uuid.UUID(installed_configuration['project']['id']).version == 4
    assert installed_configuration['jira']['enabled'] is False
    assert installed_configuration['jira']['site'] is None and installed_configuration['jira']['project_key'] is None
    assert installed_configuration['validation']['required_ci_checks'] == []
    assert installed_configuration['merge_gate']['trusted_owner_ids'] == []
    assert (DEST/'README.md').read_bytes()==b'Existing project readme\n'
    assert (DEST/'.github/EXISTING.md').read_bytes()==b'Existing project metadata\n'
    assert any(p.read_bytes()==b'Existing owner instructions\n' for p in (DEST/'.agentic-backup').rglob('AGENTS.md'))
    command([workflow,'verify-installation'],cwd=DEST,name='Installed integrity CLI')
    unconfigured_status = json.loads(command([workflow,'status','--json'],cwd=DEST,
        name='Installed status names unconfigured paths and never claims active'))
    assert unconfigured_status['project_state']=='INSTALLED' and unconfigured_status['integrity_valid'] is True
    assert unconfigured_status['line']==f'AWF {VERSION}: INSTALLED — streams 3/6'
    assert unconfigured_status['ci_gate']=='NOT_CONFIGURED' and unconfigured_status['accepted_checkout']=='UNOBSERVED'
    assert {item['path'] for item in unconfigured_status['configuration']['unresolved']}==expected_residue
    assert all(path in unconfigured_status['next_action'] for path in expected_residue)
    rules_cli = '.agentic/scripts/repository_rules.py'
    rules_observation = RUN/'synthetic-no-rules.json'
    missing_rules = json.loads(command([rules_cli,'--repository','example/adoption-fixture','--synthetic-none',
        '--default-branch','trunk','--save-observation',rules_observation],cwd=DEST,
        name='Installed controller adoption preflight continues on synthetic missing default-branch rules'))
    assert missing_rules['repository_rules']=='MISSING' and missing_rules['adoption_allowed'] is True
    assert set(missing_rules['decision_codes'])=={'prepare_adoption_pr','report_missing_ruleset'}
    assert missing_rules['execution_authority'] is False
    assert missing_rules['rules_enablement_ready'] is False
    missing_rule_pin = hashlib.sha256(rules_observation.read_bytes()).hexdigest()
    command([rules_cli,'--repository','example/adoption-fixture','--observation',rules_observation,
        '--expected-observation-sha256',missing_rule_pin,'--require-enablement'],cwd=DEST,code=2,
        name='Installed enablement prerequisite refuses missing rules without blocking adoption')
    ADOPTION = RUN/'unprotected-adoption'; ADOPTION.mkdir()
    adopted = json.loads(command(['scripts/bootstrap_project.py','--dest',ADOPTION,
        '--expected-manifest-sha256',MANIFEST,'--github-repo','example/adoption-fixture',
        '--repository-id','54321','--project-name','Synthetic adoption fixture','--project-short-name','ADOPT',
        '--test-command','python -m unittest discover',
        '--codeowner','@other','--default-branch','trunk','--rules-observation',rules_observation,
        '--expected-rules-observation-sha256',missing_rule_pin],
        name='Synthetic unprotected project installs and reports missing rules with custom CODEOWNERS'))
    assert_bootstrap_checks(adopted, ADOPTION, MANIFEST, configured=True)
    assert adopted['configuration']['ci_gate']=='NOT_CONFIGURED'
    assert adopted['repository_rules']=='MISSING' and adopted['adoption_allowed'] is True
    assert adopted['live_automation_enabled'] is False
    owner_text = (ADOPTION/'.github/CODEOWNERS').read_text(encoding='utf-8')
    assert '@other' in owner_text and '@maintainer' not in owner_text and 'HUMAN_OWNER' not in owner_text
    command([workflow,'verify-installation'],cwd=ADOPTION,name='Custom CODEOWNERS installation verifies')
    configured_status = json.loads(command([workflow,'status','--json','--release-source',SOURCE,
        '--expected-manifest-sha256',MANIFEST,'--gh',RUN/'unavailable-gh-for-offline-acceptance'],cwd=ADOPTION,
        name='Configured status remains non-active without remote acceptance observations'))
    assert configured_status['project_state']=='CONFIGURED' and configured_status['integrity_valid'] is True
    assert configured_status['line']==f'AWF {VERSION}: CONFIGURED — streams 3/6'
    assert configured_status['configuration']['status']=='ACCEPTED' and configured_status['configuration']['unresolved']==[]
    assert configured_status['ci_gate']=='NOT_CONFIGURED' and configured_status['accepted_checkout']=='UNOBSERVED'
    assert configured_status['adoption']=='UNOBSERVED' and configured_status['execution_authority'] is False
    assert configured_status['next_action']
    ci_enablement_refusal = json.loads(command([workflow,'validate-config','--require-enablement'],cwd=ADOPTION,
        code=2,error_output=True,name='Configured adoption without pinned CI refuses live enablement configuration'))
    assert ci_enablement_refusal['status']=='REJECTED' and ci_enablement_refusal['execution_authority'] is False
    assert '$.validation.required_ci_checks' in ci_enablement_refusal['reason']
    assert 'CI_NOT_CONFIGURED' in ci_enablement_refusal['reason']
    # Component self-tests validate project-owned configuration. Run them on
    # the configured fixture; retain DEST's deliberate residue until mapping it
    # for the separate configuration/ownership/stream preservation upgrade.
    command(['.agentic/scripts/self_test.py','--report',RUN/'installed-self-test.json'],cwd=ADOPTION,name='Complete configured installed self-test',timeout=selected_self_test_timeout)
    command([status_cli,'report','.agentic/examples/project-status.json'],cwd=DEST,name='Installed project status Markdown CLI')
    cycle_markdown = command([status_cli,'cycle','.agentic/examples/project-cycle.json','--now','2026-09-11T10:00:00Z'],cwd=DEST,name='Installed whole-project continuation and authorization presentation')
    assert 'State: RUNNING' in cycle_markdown and 'Required decision:' in cycle_markdown and '\u2014' in cycle_markdown
    installed_plan = json.loads(command(['.agentic/scripts/plan_streams.py','--input','.agentic/examples/stream-input.json','--expected-input-sha256',hashlib.sha256(inventory.read_bytes()).hexdigest(),'--project-root',DEST,'--allow-synthetic','--now','2026-09-11T10:00:00Z'],cwd=DEST,name='Installed planner writes into project root'))
    assert installed_plan['stream_labels']==['A','B','C'] and (DEST/'STREAMS.md').is_file()
    installed_streams = (DEST/'STREAMS.md').read_bytes(), (DEST/'STREAMS.json').read_bytes()
    command([workflow,'operating','set','--instruction','Synthetic acceptance: preserve one operating stream under a lower governance ceiling',
        '--set','streams.count=1'],cwd=DEST,name='Audited operating reduction before lower-ceiling governance fixture')
    preserved_operating = (DEST/'OPERATING_CONFIG.yaml').read_bytes()
    assert json.loads(preserved_operating)['streams']['count'] == 1
    custom_config = json.loads((SOURCE/'.agentic/examples/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
    # Map the synthetic policy without replacing this installation's persisted
    # project identity with the example project's unrelated UUID.
    custom_config['project']['id'] = installed_configuration['project']['id']
    custom_config['execution']['max_parallel_tickets'] = 1
    custom_config_bytes = (json.dumps(custom_config,indent=2)+'\n').encode('utf-8')
    (DEST/'.agentic/PROJECT_CONFIG.yaml').write_bytes(custom_config_bytes)
    custom_owners = b'# Project-owned governance reviewers\n/.agentic/ @other\n'
    (DEST/'.github/CODEOWNERS').write_bytes(custom_owners)
    command([workflow,'validate-config'],cwd=DEST,name='Customized installed configuration')
    second = json.loads(command(bootstrap+['--mode','upgrade','--on-conflict','backup'],name=f'Complete v{VERSION} upgrade'))
    assert_bootstrap_checks(second, DEST, MANIFEST, configured=True)
    assert first['install_id']==second['install_id']
    upgraded_receipt = json.loads((DEST/'.agentic/installed-manifest.json').read_bytes())
    assert upgraded_receipt['project_id']==installed_configuration['project']['id']
    assert hashlib.sha256(upgraded_receipt['source_manifest_json'].encode('utf-8')).hexdigest()==MANIFEST
    assert (DEST/'.agentic/PROJECT_CONFIG.yaml').read_bytes()==custom_config_bytes
    assert (DEST/'.github/CODEOWNERS').read_bytes()==custom_owners
    assert (DEST/'OPERATING_CONFIG.yaml').read_bytes()==preserved_operating
    assert installed_streams==((DEST/'STREAMS.md').read_bytes(),(DEST/'STREAMS.json').read_bytes())
    command([workflow,'verify-installation'],cwd=DEST,name='Upgraded integrity CLI')
    command([workflow,'validate-config'],cwd=DEST,name='Configuration preserved after upgrade')
    limited = json.loads(command(['.agentic/scripts/plan_streams.py','--input',inventory,'--expected-input-sha256',hashlib.sha256(inventory.read_bytes()).hexdigest(),'--project-root',DEST,'--allow-synthetic','--now','2026-09-11T10:00:00Z','--host-writer-capacity','3','--expected-plan-sha256',installed_plan['plan_sha256']],name='External runtime honors the target project\'s explicit lower capacity'))
    assert limited['capacity']['effective_writer_capacity']==1 and len(limited['dispatch_packets'])==1
    assert len(limited['deferred_dispatch_packets'])==2 and limited['prompt_user'] is False
    command(['scripts/validate_git_checkout.py','--source',SOURCE,'--expected-manifest-sha256',MANIFEST,
        '--workdir',RUN/'g','--report',RUN/'git-checkout.json'],name='Windows-compatible Git checkout byte preservation and negative controls',timeout=360)
    git_checkout = json.loads((RUN/'git-checkout.json').read_text(encoding='utf-8'))
    assert git_checkout['status']=='PASS'
    before = (SOURCE/'MANIFEST.json').read_bytes()
    command(['scripts/generate_contracts.py'],name='Regenerate complete schemas')
    command(['scripts/generate_prompts.py'],name='Regenerate versioned role prompts')
    command(['scripts/generate_examples.py'],name='Regenerate complete examples/forms/workflow')
    command(['scripts/generate_review_loop.py'],name='Regenerate host loop schemas and default policy')
    command(['scripts/generate_interaction.py'],name='Regenerate complete synthetic interaction examples')
    command([workflow,'verify-installation'],name='Generated files reproduce approved bytes')
    assert before == (SOURCE/'MANIFEST.json').read_bytes()
    REBUILT = RUN/'rebuilt.zip'
    command(['scripts/build_release.py','--output',REBUILT],name='Rebuild deterministic ZIP')
    assert ARCHIVE.read_bytes()==REBUILT.read_bytes()
    checks.append({'check':'ZIP byte-for-byte reproducibility','status':'PASS'})
    report = {'template_version':VERSION,'status':'PASS','archive':ARCHIVE.name,
     'current_review':current_review,
     'self_test_timeout_seconds':selected_self_test_timeout,
     'archive_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),'manifest_sha256':MANIFEST,
     'checks':checks,'git_checkout':git_checkout,'extracted_tests':json.loads((RUN/'extracted-self-test.json').read_text()),
     'default_development_checks':default_checks,
     'adoption_configuration_acceptance':{'status':'PASS','unconfigured':first,
        'configured':adopted,'upgrade':second,'installed_status':unconfigured_status,
        'configured_status':configured_status,'ci_enablement_refusal':ci_enablement_refusal,
        'remote_acceptance_observed':False,
        'scope':'Actual installed verify-installation, validate-config and status CLIs; explicit synthetic configuration, no live GitHub metadata or project test execution.'},
     'adoption_rules_acceptance':{'status':'PASS','source':'synthetic_fixture','default_branch':'trunk',
        'repository_rules':'MISSING','adoption_allowed':True,'expected_decision_codes':missing_rules['decision_codes'],
        'hosted_adoption_pr_created':False,'rules_enablement_ready':False,'live_ruleset_tested':False,
        'scope':'Real local installer and installed prerequisite CLI with a synthetic rules-none observation; no hosted PR or legacy adapter enforcement claimed.'},
     'installed_tests':json.loads((RUN/'installed-self-test.json').read_text()),
     'external_operator_unit':{'source':source_external_unit,'installed':installed_external_unit},
     'limitations':['Offline evidence, synthetic Jira planning and host review-loop validation; no live Jira mutation, native agent dispatch, model, remote amendment or scheduler qualification tested.','Other operating systems not exercised in this run.']}
    # Keep portable release evidence, excluding transient local paths in test logs.
    for name in ['extracted_tests','installed_tests','default_development_checks']:
        report[name].pop('test_log',None)
        for skip in report[name]['tests']['skipped']:
            if 'WinError 1314' in skip['reason']:
                skip['reason']='Windows symlink creation privilege unavailable (WinError 1314).'
    for fragment in report['external_operator_unit'].values():
        for skip in fragment.get('skipped', []):
            if 'WinError 1314' in skip['reason']:
                skip['reason']='Windows symlink creation privilege unavailable (WinError 1314).'
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    catalog = RUN/'AWF_RELEASES.json'
    # An isolated catalog fixture tests discovery without publishing an incomplete
    # validation report. OUTPUT becomes eligible only after every check below.
    catalog.write_text(json.dumps({'format':'awf-release-catalog-1','latest':{
        'version':VERSION,'source_directory':str(SOURCE),'archive':str(ARCHIVE),
        'archive_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),'manifest_sha256':MANIFEST}},indent=2)+'\n',encoding='utf-8')
    locator = SOURCE/'global/awf/scripts/awf.py'
    located = json.loads(command([locator,'--catalog',catalog,'locate','--version',RELEASE_LINE],name=f'Local skill locates and verifies actual v{RELEASE_LINE} files'))
    assert located['source_verified'] is True and located['archive_verified'] is True
    assert located['adoption_pr_created'] is False and located['release_code_executed'] is False
    inspected = json.loads(command([locator,'--catalog',catalog,'inspect','--project',DEST,'--version',RELEASE_LINE],name='Local skill verifies target installation receipt without changing it'))
    assert inspected['installed']['receipt_verified_against_registered_release'] is True
    assert inspected['installed_by_helper'] is False and inspected['installer_apply_command'] is None
    invalid_report = RUN/'rejected-validation.json'
    bad = dict(report)
    invalid_report.write_text(json.dumps(bad),encoding='utf-8')
    catalog_before = catalog.read_bytes()
    command(['scripts/publish_catalog.py','--source',SOURCE,'--archive',ARCHIVE,'--validation-report',invalid_report,'--catalog',catalog,*review_cli],code=2,name='Provisional PASS report cannot publish a catalog')
    assert catalog.read_bytes()==catalog_before
    bad['validation_complete'] = True
    bad['archive_sha256'] = '0'*64
    invalid_report.write_text(json.dumps(bad),encoding='utf-8')
    command(['scripts/publish_catalog.py','--source',SOURCE,'--archive',ARCHIVE,'--validation-report',invalid_report,'--catalog',catalog,*review_cli],code=2,name='Wrong final archive identity preserves prior catalog')
    assert catalog.read_bytes()==catalog_before
    command([locator,'--catalog',catalog,'locate','--version','99.0'],code=2,name='Version request cannot silently substitute another release')
    if review_source(SOURCE, MANIFEST, args.reviews, args.expected_reviews_sha256,
                     args.review_required_paths, args.expected_review_required_paths_sha256) != current_review:
        raise ValidationError('Current review inputs changed during acceptance')
    if report['extracted_tests'].get('release_qualified') is not True or report['extracted_tests'].get('current_review') != current_review:
        raise ValidationError('Fresh source self-test did not qualify the same current review')
    checks.append({'check':'Final review inputs and source remain bound after acceptance','status':'PASS'})
    report['validation_complete'] = True
    write_report(OUTPUT, report)
    print(json.dumps({'status':'PASS','report':str(OUTPUT),'smoke_checks':len(checks),'run_directory':str(RUN)},indent=2))
    return 0


def main(argv=None):
    try:
        return validate(argv)
    except ValidationStageFailure as exc:
        print(json.dumps(exc.report,indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
