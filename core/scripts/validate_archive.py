"""Independent fresh-extraction/CLI/installation smoke validation of a built ZIP."""
from pathlib import Path
import hashlib
import argparse
import json
import os
import subprocess
import sys
import uuid
import zipfile
parser = argparse.ArgumentParser(description="Validate an approved source ZIP through extraction, tests, installation, upgrade and deterministic rebuilding. Runs trusted package code; retain the work directory as evidence.")
parser.add_argument('--archive', type=Path, required=True)
parser.add_argument('--expected-zip-sha256', required=True)
parser.add_argument('--report', type=Path, required=True)
parser.add_argument('--workdir', type=Path)
args = parser.parse_args()
ARCHIVE = args.archive.resolve(strict=True)
if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != args.expected_zip_sha256:
    parser.error('Archive differs from the independently approved ZIP digest')
WORK = (args.workdir or args.report.absolute().parent).absolute()
if WORK.is_relative_to(Path(__file__).resolve().parents[1]):
    parser.error('Validation work directory must be outside the source release tree')
WORK.mkdir(parents=True, exist_ok=True)
RUN = WORK / ('extraction-' + uuid.uuid4().hex)
RUN.mkdir()
PREFIX = 'agentic-workflow-template-v1.7/'
with zipfile.ZipFile(ARCHIVE) as archive:
    if archive.testzip() is not None:
        raise RuntimeError('ZIP CRC failed')
    for entry in archive.infolist():
        target = (RUN / entry.filename).resolve()
        if not entry.filename.startswith(PREFIX) or not target.is_relative_to(RUN.resolve()) or entry.is_dir():
            raise RuntimeError('Unsafe ZIP inventory')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(entry))
SOURCE = RUN / PREFIX.rstrip('/')
MANIFEST = hashlib.sha256((SOURCE / 'MANIFEST.json').read_bytes()).hexdigest()
env = dict(os.environ)
env.pop('PYTHONPATH', None)
env['PYTHONDONTWRITEBYTECODE'] = '1'
env['PYTHONIOENCODING'] = 'utf-8'
checks = []

def command(args, cwd=SOURCE, code=0, name=None, timeout=180):
    done = subprocess.run([sys.executable, '-B', *map(str,args)], cwd=cwd, env=env, capture_output=True, timeout=timeout)
    # Decode synchronously: Windows text-mode reader threads can otherwise lose
    # Unicode output while the child still exits zero and appears to pass.
    stdout, stderr = done.stdout.decode('utf-8'), done.stderr.decode('utf-8')
    label = name or ' '.join(map(str,args))
    if done.returncode != code:
        (RUN / 'failure.log').write_text(stdout+'\n'+stderr,encoding='utf-8')
        raise RuntimeError(f'{label}: expected {code}, got {done.returncode}. See {RUN / "failure.log"}')
    checks.append({'check':label,'status':'PASS','exit_code':done.returncode})
    print(label + ': PASS',flush=True)
    return stdout

command(['scripts/self_test.py','--report',RUN/'extracted-self-test.json'], name='Fresh ZIP extraction self-test', timeout=600)
workflow = '.agentic/scripts/workflow.py'
capabilities = json.loads(command([workflow,'capabilities'],name='Capabilities CLI'))
assert all(capabilities[k] is False for k in ['live_dispatch','live_jira_mutations','live_merge'])
assert capabilities['separate_host_review_loop']['automatic_by_default_after_enrollment'] is True
assert capabilities['project_coordination']['routine_authorization_prompts'] is False
assert capabilities['project_coordination']['proactive_native_stream_agents'] is False
assert capabilities['project_coordination']['launches_native_agents'] is False
assert capabilities['project_coordination']['attests_active_writers'] is False
assert capabilities['project_coordination']['native_streams_enabled_by_default'] is True
assert capabilities['project_coordination']['default_max_parallel_tickets'] == 3
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
first = json.loads(command(bootstrap+['--on-conflict','backup'],name='Complete backed-up project installation'))
assert first['live_automation_enabled'] is False
assert first['installer_launches_agents'] is False
installed_configuration = json.loads((DEST/'.agentic/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
assert installed_configuration['execution']['max_parallel_tickets'] == 3
assert installed_configuration['execution']['max_parallel_tickets_per_stream'] == 1
assert installed_configuration['execution']['native_streams'] == {'enabled': True, 'dispatch_policy': 'ready_independent'}
assert all(value is False for value in installed_configuration['controller'].values())
assert (DEST/'README.md').read_bytes()==b'Existing project readme\n'
assert (DEST/'.github/EXISTING.md').read_bytes()==b'Existing project metadata\n'
assert any(p.read_bytes()==b'Existing owner instructions\n' for p in (DEST/'.agentic-backup').rglob('AGENTS.md'))
command([workflow,'verify-installation'],cwd=DEST,name='Installed integrity CLI')
command(['.agentic/scripts/self_test.py','--report',RUN/'installed-self-test.json'],cwd=DEST,name='Complete installed self-test',timeout=600)
command([status_cli,'report','.agentic/examples/project-status.json'],cwd=DEST,name='Installed project status Markdown CLI')
cycle_markdown = command([status_cli,'cycle','.agentic/examples/project-cycle.json','--now','2026-09-11T10:00:00Z'],cwd=DEST,name='Installed whole-project continuation and authorization presentation')
assert 'State: RUNNING' in cycle_markdown and 'Required decision:' in cycle_markdown and '\u2014' in cycle_markdown
installed_plan = json.loads(command(['.agentic/scripts/plan_streams.py','--input','.agentic/examples/stream-input.json','--expected-input-sha256',hashlib.sha256(inventory.read_bytes()).hexdigest(),'--project-root',DEST,'--allow-synthetic','--now','2026-09-11T10:00:00Z'],cwd=DEST,name='Installed planner writes into project root'))
assert installed_plan['stream_labels']==['A','B','C'] and (DEST/'STREAMS.md').is_file()
installed_streams = (DEST/'STREAMS.md').read_bytes(), (DEST/'STREAMS.json').read_bytes()
custom_config = json.loads((SOURCE/'.agentic/examples/PROJECT_CONFIG.yaml').read_text(encoding='utf-8'))
custom_config['execution']['max_parallel_tickets'] = 1
custom_config_bytes = (json.dumps(custom_config,indent=2)+'\n').encode('utf-8')
(DEST/'.agentic/PROJECT_CONFIG.yaml').write_bytes(custom_config_bytes)
command([workflow,'validate-config'],cwd=DEST,name='Customized installed configuration')
second = json.loads(command(bootstrap+['--mode','upgrade','--on-conflict','backup'],name='Complete v1.7.0 upgrade'))
assert first['install_id']==second['install_id']
assert (DEST/'.agentic/PROJECT_CONFIG.yaml').read_bytes()==custom_config_bytes
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
report = {'template_version':'1.7.0','status':'PASS','archive':ARCHIVE.name,
 'archive_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),'manifest_sha256':MANIFEST,
 'checks':checks,'git_checkout':git_checkout,'extracted_tests':json.loads((RUN/'extracted-self-test.json').read_text()),
 'installed_tests':json.loads((RUN/'installed-self-test.json').read_text()),
 'limitations':['Offline evidence, synthetic Jira planning and host review-loop validation; no live Jira mutation, native agent dispatch, model, remote amendment or scheduler qualification tested.','Other operating systems not exercised in this run.']}
# Keep portable release evidence, excluding transient local paths in test logs.
for name in ['extracted_tests','installed_tests']:
    report[name].pop('test_log',None)
    for skip in report[name]['tests']['skipped']:
        if 'WinError 1314' in skip['reason']:
            skip['reason']='Windows symlink creation privilege unavailable (WinError 1314).'
OUTPUT = args.report.absolute()
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
catalog = RUN/'AWF_RELEASES.json'
# An isolated catalog fixture tests discovery without publishing an incomplete
# validation report. OUTPUT becomes eligible only after every check below.
catalog.write_text(json.dumps({'format':'awf-release-catalog-1','latest':{
    'version':'1.7.0','source_directory':str(SOURCE),'archive':str(ARCHIVE),
    'archive_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),'manifest_sha256':MANIFEST}},indent=2)+'\n',encoding='utf-8')
locator = SOURCE/'global/awf/scripts/awf.py'
located = json.loads(command([locator,'--catalog',catalog,'locate','--version','1.7'],name='Local skill locates and verifies actual v1.7 files'))
assert located['source_verified'] is True and located['archive_verified'] is True
assert located['adoption_pr_created'] is False and located['release_code_executed'] is False
inspected = json.loads(command([locator,'--catalog',catalog,'inspect','--project',DEST,'--version','1.7'],name='Local skill verifies target installation receipt without changing it'))
assert inspected['installed']['receipt_verified_against_registered_release'] is True
assert inspected['installed_by_helper'] is False and inspected['installer_apply_command'] is None
invalid_report = RUN/'rejected-validation.json'
bad = dict(report)
invalid_report.write_text(json.dumps(bad),encoding='utf-8')
catalog_before = catalog.read_bytes()
command(['scripts/publish_catalog.py','--source',SOURCE,'--archive',ARCHIVE,'--validation-report',invalid_report,'--catalog',catalog],code=2,name='Provisional PASS report cannot publish a catalog')
assert catalog.read_bytes()==catalog_before
bad['validation_complete'] = True
bad['archive_sha256'] = '0'*64
invalid_report.write_text(json.dumps(bad),encoding='utf-8')
command(['scripts/publish_catalog.py','--source',SOURCE,'--archive',ARCHIVE,'--validation-report',invalid_report,'--catalog',catalog],code=2,name='Wrong final archive identity preserves prior catalog')
assert catalog.read_bytes()==catalog_before
command([locator,'--catalog',catalog,'locate','--version','99.0'],code=2,name='Version request cannot silently substitute another release')
report['validation_complete'] = True
temporary_report = OUTPUT.with_name('.'+OUTPUT.name+'.'+uuid.uuid4().hex+'.tmp')
with temporary_report.open('xb') as stream:
    stream.write((json.dumps(report,indent=2)+'\n').encode('utf-8'))
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary_report, OUTPUT)
print(json.dumps({'status':'PASS','report':str(OUTPUT),'smoke_checks':len(checks),'run_directory':str(RUN)},indent=2))
