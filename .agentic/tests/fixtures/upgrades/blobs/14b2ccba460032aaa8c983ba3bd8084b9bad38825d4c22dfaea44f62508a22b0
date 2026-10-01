#!/usr/bin/env python3
"""Run the automatic host review loop for one explicitly enrolled PR."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / '.agentic/lib'))
from agentic import ValidationError
from agentic.installer import verify_installed
from agentic.review_loop import LoopStore, enroll, pause, resume, tick, require
from agentic.review_host import HostDriver, load_config
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
            value = store.get(config['key'])
        elif args.command == 'pause':
            value = pause(store,config['key'],args.reason)
        else:
            candidate = driver.snapshot()
            driver.preflight(candidate)
            value = resume(store,config,candidate,args.reconciled_run)
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
