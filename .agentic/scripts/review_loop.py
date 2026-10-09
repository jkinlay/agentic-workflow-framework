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
from agentic.review_loop import (LoopStore, complete_first_draft, enroll, pause,
    resume, resume_first_draft, tick, require)
from agentic.review_first_draft import (publish_tested_tree, render_first_draft_body,
    run_first_draft)
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
            provider_base = driver.provider_base()
            local_base = driver.git(driver.worker, 'rev-parse', config['base_branch'])
            require(local_base == provider_base, 'Worker checkout base does not match the provider base snapshot')
            reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?',
                                        (f"{config['repository_id']}:0",)).fetchone()
            if reserved:
                prior = store.get(f"{config['repository_id']}:0").get('first_draft_publication')
                require(prior is None or prior.get('base') == provider_base,
                        'Provider base moved after the recorded first-draft publication')
            def worker(run_id=None):
                return driver.first_draft_worker(run_id=run_id)
            def publisher(receipt):
                body = render_first_draft_body(contract, risk_tier=tier,
                    worker_model=config['models']['worker'],
                    reasoning_effort=(config.get('reasoning_effort') or {}).get('worker', 'default'),
                    branch=config['head_branch'], tested_tree=receipt['tested_tree'])
                class GitAdapter:
                    def run(self, *git_args):
                        return driver.git(driver.worker, *git_args)
                current_provider_base = driver.provider_base()
                require(current_provider_base == provider_base, 'Provider base branch moved during first-draft execution')
                return publish_tested_tree(driver.worker, provider_base,
                    config['head_branch'], receipt, body=body, commit_message=args.title,
                    git=GitAdapter(), allowed_paths=config['allowed_paths'],
                    mapping_path=Path(config['state_dir']) / 'publication-deny.json') | {
                        'body': body, 'title': args.title}
            def observe(publication):
                require(publication['title'] == args.title,
                        'Retry title does not match the durably recorded first-draft publication')
                created = driver.create_draft_pr(title=publication['title'], body=publication['body'],
                    head=config['head_branch'], base=config['base_branch'])
                return driver.observe_created_pr(created, body=publication['body'], risk_tier=tier)
            value = run_first_draft(store, config, worker=worker, publisher=publisher, observe_pr=observe)
        elif args.command == 'pause':
            value = pause(store,config['key'],args.reason)
        else:
            first_key = f"{config['repository_id']}:0"
            reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?', (first_key,)).fetchone()
            if config.get('first_draft') is True and reserved:
                require(args.disposition is None,
                        'A first-draft reservation cannot consume an amendment-cap disposition')
                first_state = store.get(first_key)
                publication = first_state.get('first_draft_publication')
                expected_body = publication.get('body_sha256') if isinstance(publication, dict) else None
                candidate = driver.reconcile_created_pr(expected_body_sha256=expected_body)
                if candidate is None:
                    value = resume_first_draft(store, config, args.reconciled_run)
                else:
                    value = complete_first_draft(store, config, candidate,
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
