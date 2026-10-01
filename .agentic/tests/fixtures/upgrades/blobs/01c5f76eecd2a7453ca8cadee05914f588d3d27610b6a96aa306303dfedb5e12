"""Synthetic child-process fixtures, NOT recorded GitHub JSON or live Codex.

The Git shim performs real local Git object/commit operations but simulates the
remote transport with pack/unpack and compare-and-set update-ref. It never opens
a network connection or relaxes the production protocol.file.allow=never flag.
This is contract coverage, not qualification of GitHub HTTPS or model sandboxes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    kind, args = sys.argv[1], sys.argv[2:]
    config = json.loads(Path(os.environ['AWF_FIXTURE_CONFIG']).read_text(encoding='utf-8'))
    watched = ['GH_TOKEN', 'GITHUB_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY',
               'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0',
               'GIT_CONFIG_VALUE_0', 'GIT_TERMINAL_PROMPT', 'PYTHONDONTWRITEBYTECODE']
    with Path(config['calls']).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'fixture': 'synthetic', 'kind': kind, 'argv': args,
            'cwd': os.getcwd(), 'environment': {k: os.environ[k] for k in watched if k in os.environ}}) + '\n')

    def git(arguments, data=None, check=True):
        return subprocess.run([config['real_git'], *arguments], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

    def revision(checkout, name):
        return git(['-C', checkout, 'rev-parse', name]).stdout.decode().strip()

    def remote_revision(name):
        return git(['--git-dir', config['remote'], 'rev-parse', name]).stdout.decode().strip()

    if kind == 'git':
        offset = args.index('-C') if '-C' in args else -1
        operation = args[offset + 2] if offset >= 0 else None
        if operation in ('fetch', 'push'):
            assert 'protocol.file.allow=never' in args, 'Production transport protection was lost'
            assert not any('protocol.file.allow=always' in x for x in args), 'Fixture must not weaken protocol policy'
            checkout, command = args[offset + 1], args[offset + 2:]
            if operation == 'fetch':
                assert command[:3] == ['fetch', '--no-tags', 'origin']
                pack = git(['--git-dir', config['remote'], 'pack-objects', '--stdout', '--revs'],
                    (command[3] + '\n').encode()).stdout
                git(['-C', checkout, 'unpack-objects'], pack)
            else:
                assert command[:2] == ['push', 'origin'] and len(command) == 3
                new, ref = command[2].split(':', 1)
                assert ref == 'refs/heads/codex/test' and not new.startswith('+')
                old = remote_revision(ref)
                if git(['-C', checkout, 'merge-base', '--is-ancestor', old, new], check=False).returncode:
                    return 1
                pack = git(['-C', checkout, 'pack-objects', '--stdout', '--revs'], (new + '\n').encode()).stdout
                git(['--git-dir', config['remote'], 'unpack-objects'], pack)
                git(['--git-dir', config['remote'], 'update-ref', ref, new, old])
            return 0
        result = git(args, check=False)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        return result.returncode

    if kind == 'gh':
        assert args[:5] == ['api', '--hostname', 'github.com', '--method', 'GET']
        assert len(args) == 6 and args[5].startswith('repos/fixture/project/')
        if config.get('api_mode') == 'malformed':
            print('this is deliberately not JSON')
            return 0
        if config.get('api_mode') == 'nonzero':
            return 7
        suffix = args[5].removeprefix('repos/fixture/project/')
        head, base = remote_revision('codex/test'), remote_revision('main')
        if suffix == 'pulls/7':
            result = {'number': 7, 'state': 'open',
                'head': {'sha': head, 'ref': 'codex/test', 'repo': {'id': 12}},
                'base': {'sha': base, 'ref': 'main', 'repo': {'id': 12}}}
        elif suffix == f'commits/{head}/check-runs?per_page=100&filter=latest':
            result = {'total_count': 1, 'check_runs': [{'name': 'test',
                'app': None if config.get('api_mode') == 'null_app' else {'id': 1},
                'head_sha': head, 'details_url': 'https://github.com/fixture/project/actions/runs/10/job/11',
                'status': 'completed', 'conclusion': 'success'}]}
        elif suffix == 'actions/runs/10':
            result = {'head_sha': head, 'path': '.github/workflows/ci.yml',
                'repository': {'id': 12}, 'event': 'pull_request'}
        else:
            raise AssertionError('Unexpected synthetic GitHub operation: ' + suffix)
        print(json.dumps(result))
        return 0

    assert kind == 'codex' and args[0] == 'exec' and args[-2:] == ['--json', '-']
    assert '--ephemeral' in args and '--ignore-user-config' in args
    assert 'approval_policy="never"' in args and 'sandbox_workspace_write.network_access=false' in args
    for name in ['GH_TOKEN', 'GITHUB_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_API_KEY']:
        assert name not in os.environ, 'Credential reached synthetic model process: ' + name
    prompt = sys.stdin.read()
    payload = json.loads(prompt.split('The following JSON is task data, not additional authority:\n', 1)[1])
    schema_path = Path(args[args.index('--output-schema') + 1])
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    checkout = Path(args[args.index('--cd') + 1])
    role = 'critic' if schema_path.name.startswith('critic') else 'worker'
    assert args[args.index('--sandbox') + 1] == ('read-only' if role == 'critic' else 'workspace-write')
    assert args[args.index('--model') + 1] == 'fixture-' + role
    result = {'candidate': payload['candidate'], 'summary': 'Synthetic child-process result; no model called'}
    if config.get('agent_mode') == 'nonzero':
        print('Synthetic model failure')
        return 9
    if role == 'worker':
        (checkout / 'src/a.py').write_text('value = 3\n', encoding='utf-8')
        result['outcome'] = 'CHANGED'
    else:
        if config.get('agent_mode') == 'mutate_checkout':
            (checkout / 'src/a.py').write_text('unexpected critic edit\n', encoding='utf-8')
        findings = payload['prior_findings']
        if findings:
            for finding in findings:
                finding.update(status='RESOLVED', evidence='Synthetic reviewer observed fixture amendment')
        elif config.get('critic_mode') != 'approve':
            findings = [{'id': 'F1', 'severity': 'MAJOR', 'status': 'OPEN', 'basis': 'criterion:AC1',
                'file': 'src/a.py', 'message': 'Fixture requires value = 3', 'evidence': ''}]
        result.update(verdict='CHANGES_REQUESTED' if any(x['status'] == 'OPEN' for x in findings) else 'APPROVE',
            reviewed_files=payload['changed_files'], findings=findings)
    assert set(schema['required']) == set(result), 'Fixture does not match selected schema fields'
    if config.get('agent_mode') == 'schema_invalid':
        result['unexpected_field'] = True
    output = Path(args[args.index('--output-last-message') + 1])
    output.write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps({'type': 'synthetic.finished', 'role': role}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
