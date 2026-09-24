#!/usr/bin/env python3
"""Run one tick and retain a next-step report even when host preflight fails.

No chat transport is supplied. The native host coordinator relays this report.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.agentic/lib'))
from agentic.canonical import loads, sha256, now_text
from agentic.interaction import render_markdown, rejected_next_step
from agentic.safeio import Tree
from review_loop import main as run_tick


def run(config_path, invoke=run_tick):
    config_path=Path(config_path).resolve(strict=False)
    scratch = (ROOT / '.tmp-tests').resolve()
    if config_path.is_relative_to(ROOT) and not config_path.is_relative_to(scratch):
        raise ValueError('Scheduled host configuration and reports must be outside the immutable runtime')
    output,errors=io.StringIO(),io.StringIO()
    invocation_error=None
    with redirect_stdout(output), redirect_stderr(errors):
        try:
            code=invoke(['--config',str(config_path),'tick'])
        except Exception as exc:
            code=2
            invocation_error=f'{type(exc).__name__}: {exc}'
            print(invocation_error,file=sys.stderr)
    raw=output.getvalue().strip() or errors.getvalue().strip()
    try:
        if invocation_error:
            raise ValueError(invocation_error)
        value=loads(raw)
        if not isinstance(value,dict) or 'next_step' not in value:
            raise ValueError('Tick supplied no actionable status')
        if code != 0 and value.get('phase') != 'PAUSED' and value.get('status') not in {'REJECTED','FAILED','BLOCKED'}:
            raise ValueError('Failed tick supplied a contradictory success report')
        body=render_markdown(value)
    except Exception:
        detail=(invocation_error or errors.getvalue().strip() or
                (f'Tick exited with code {code}; inspect process outcome.' if code else raw))[:4000] or 'Tick produced no report; inspect process outcome'
        value={'status':'REJECTED','reason':detail,'next_step':rejected_next_step(detail)}
        body=render_markdown(value)
        code=2
    relative='.awf-status/'+sha256(str(config_path).encode('utf-8'))+'.md'
    report='Recorded at: '+now_text()+'\n\n'+body
    with Tree(config_path.parent) as tree:
        tree.write(relative,report.encode('utf-8'))
    print(report)
    print('Retained report: '+str(config_path.parent/relative))
    return code


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        return run(args.config)
    except Exception as exc:
        value={'status':'REJECTED','reason':str(exc),'next_step':rejected_next_step(str(exc))}
        print(render_markdown(value),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
