#!/usr/bin/env python3
"""Run the automatic host review loop for one explicitly enrolled PR."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / '.agentic/lib'))
from agentic.child_process import scrub_process_env
scrub_process_env()

import argparse
import json
from agentic.installer import verify_installed
from agentic.review_loop import (LoopStore, complete_first_draft,
    confirm_first_draft_publication, enroll, pause, record_first_draft_publication,
    resume, resume_first_draft, tick, require)
from agentic.review_first_draft import (publish_tested_tree, render_first_draft_body,
    republish_prepared_tree, run_first_draft)
from agentic.providers.github_review_host import HostDriver, load_config
from agentic.interaction import loop_next_step, next_step, render_markdown, rejected_next_step


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--format',choices=['json','markdown'],default='json')
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ['check','enroll','tick','status']:
        sub.add_parser(name)
    p = sub.add_parser('pause')
    p.add_argument('--reason',required=True)
    p = sub.add_parser('resume')
    p.add_argument('--reconciled-run',required=True,help='Exact retained inflight UUID, or none; first inspect and stop any orphan processes and reconcile Git')
    p.add_argument('--disposition',type=Path,help='Owner cap disposition JSON (decision, disposition_id, owner, open_finding_ids, successor_ticket); required after REVIEW_CAP_REACHED')
    p = sub.add_parser('first-draft')
    p.add_argument('--title', required=True)
    args = parser.parse_args(argv)
    store = None
    try:
        digest = verify_installed(ROOT)
        config = load_config(args.config,ROOT)
        require(config['runtime_manifest_sha256'] == digest, 'Runtime is not the enrolled approved release')
        driver = HostDriver(config,ROOT)
        store = LoopStore(config['state_dir'])
        if args.command == 'check':
            candidate = driver.snapshot()
            driver.preflight(candidate)
            value = {'status':'PREFLIGHT_PASSED','candidate':candidate,'live_model_tested':False,'scheduler_tested':False,
                'next_step':next_step('Complete any outstanding host qualification, then enroll the owned PR and schedule bounded ticks within the existing authorized scope.')}
        elif args.command == 'enroll':
            candidate = driver.snapshot()
            driver.preflight(candidate)
            value = enroll(store,config,candidate)
        elif args.command == 'tick':
            value = tick(store,config,driver)
        elif args.command == 'status':
            first_key = f"{config['repository_id']}:0"
            reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?', (first_key,)).fetchone()
            value = store.get(first_key if reserved else config['key'])
        elif args.command == 'first-draft':
            require(config.get('first_draft') is True and config['pr'] == 0,
                    'first-draft requires an unbound first-draft host configuration')
            tier = config.get('risk_tier')
            require(tier in {1, 2, 3, 'Tier 1', 'Tier 2', 'Tier 3'}, 'A reviewed first-draft risk tier is required')
            contract = Path(config['contract_path']).read_text(encoding='utf-8')
            reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?',
                                        (f"{config['repository_id']}:0",)).fetchone()
            prior = None
            if reserved:
                reserved_state = store.get(f"{config['repository_id']}:0")
                git_controls = reserved_state.get('first_draft_git_controls')
                require(isinstance(git_controls, dict),
                        'Reserved first draft has no durable Git-control baseline')
                driver.require_first_draft_git_controls(git_controls)
                prior = reserved_state.get('first_draft_publication')
            else:
                git_controls = driver.first_draft_git_controls()
            provider_base = driver.provider_base()
            require(prior is None or prior.get('base') == provider_base,
                    'Provider base moved after the recorded first-draft publication')
            local_base = driver.git(driver.worker, 'rev-parse', config['base_branch'])
            require(local_base == provider_base, 'Worker checkout base does not match the provider base snapshot')
            if prior is None:
                require(driver.git(driver.worker, 'symbolic-ref', '--short', 'HEAD')
                        == config['head_branch'],
                        'First-draft worker checkout is not on the configured head branch')
                require(driver.git(driver.worker, 'rev-parse', 'HEAD') == provider_base,
                        'First-draft worker HEAD does not equal the provider base')
            driver.bind_reviewed_policy({'base': provider_base})
            def worker(run_id=None):
                return driver.first_draft_worker(run_id=run_id,
                                                 expected_git_controls=git_controls)
            def git_guard():
                driver.require_first_draft_git_controls(git_controls)
            class GitAdapter:
                def run(self, *git_args):
                    return driver.git(driver.worker, *git_args)
            def publisher(receipt):
                body = render_first_draft_body(contract, risk_tier=tier,
                    worker_model=config['models']['worker'],
                    reasoning_effort=(config.get('reasoning_effort') or {}).get('worker', 'default'),
                    branch=config['head_branch'], tested_tree=receipt['tested_tree'])
                git_guard()
                current_provider_base = driver.provider_base()
                require(current_provider_base == provider_base, 'Provider base branch moved during first-draft execution')
                return publish_tested_tree(driver.worker, provider_base,
                    config['head_branch'], receipt, body=body, commit_message=args.title,
                    git=GitAdapter(), allowed_paths=config['allowed_paths'],
                    mapping_path=Path(config['state_dir']) / 'publication-deny.json',
                    title=args.title, git_guard=git_guard,
                    prepare_publication=lambda publication:
                        record_first_draft_publication(store, config, publication))
            def republisher(publication):
                return republish_prepared_tree(driver.worker, publication,
                    git=GitAdapter(),
                    mapping_path=Path(config['state_dir']) / 'publication-deny.json',
                    git_guard=git_guard)
            def observe(publication):
                require(publication['title'] == args.title,
                        'Retry title does not match the durably recorded first-draft publication')
                git_guard()
                created = driver.create_draft_pr(title=publication['title'], body=publication['body'],
                    head=config['head_branch'], base=config['base_branch'])
                return driver.observe_created_pr(created, body=publication['body'], risk_tier=tier)
            value = run_first_draft(store, config, worker=worker, publisher=publisher,
                                    republisher=republisher, observe_pr=observe,
                                    git_controls=git_controls)
        elif args.command == 'pause':
            value = pause(store,config['key'],args.reason)
        else:
            first_key = f"{config['repository_id']}:0"
            reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?', (first_key,)).fetchone()
            if config.get('first_draft') is True and reserved:
                require(args.disposition is None,
                        'A first-draft reservation cannot consume an amendment-cap disposition')
                first_state = store.get(first_key)
                git_controls = first_state.get('first_draft_git_controls')
                require(isinstance(git_controls, dict),
                        'Reserved first draft has no durable Git-control baseline')
                driver.require_first_draft_git_controls(git_controls)
                publication = first_state.get('first_draft_publication')
                expected_body = publication.get('body_sha256') if isinstance(publication, dict) else None
                reconciled = driver.reconcile_created_pr(
                    expected_body_sha256=expected_body, publication=publication)
                if reconciled is None:
                    value = resume_first_draft(store, config, args.reconciled_run)
                elif isinstance(reconciled, str):
                    if reconciled == 'PUSHED':
                        confirm_first_draft_publication(store, config, publication,
                                                        reconciled=True)
                    value = resume_first_draft(store, config, args.reconciled_run,
                                               publication_status=reconciled)
                else:
                    value = complete_first_draft(store, config, reconciled,
                                                 reconciled_run=args.reconciled_run)
            else:
                candidate = driver.snapshot()
                driver.preflight(candidate)
                disposition = json.loads(args.disposition.read_text(encoding='utf-8')) if args.disposition else None
                value = resume(store,config,candidate,args.reconciled_run,disposition)
        if 'phase' in value:
            value['next_step']=loop_next_step(value)
        print(json.dumps(value,indent=2) if args.format=='json' else render_markdown(value))
        return 2 if value.get('phase') == 'PAUSED' else 0
    except Exception as exc:
        reason=f'{type(exc).__name__}: {exc}'
        value={'status':'REJECTED','reason':reason,'merge_authorized':False,'next_step':rejected_next_step(reason)}
        print(json.dumps(value,indent=2) if args.format=='json' else render_markdown(value),file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
