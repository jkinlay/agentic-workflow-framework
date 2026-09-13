"""Fail builds on stale operational releases or non-reproducible prompts."""
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION, ValidationError
from generate_prompts import render_prompts

# Historical review/disposition records and synthetic fixtures retain their dates/versions.
HISTORY = {'CHANGELOG.md', 'MANIFEST.md', 'MANIFEST.json',
           '17-RED-TEAM-DISPOSITION.md', '19-RELEASE-VALIDATION.md'}

def check_release(root=ROOT):
    problems = []
    documentation_words = sum(len(path.read_text(encoding='utf-8').split())
                              for path in root.rglob('*.md') if path.name != 'MANIFEST.md' and '.git' not in path.parts)
    if documentation_words > 6000:
        problems.append(f'documentation budget exceeded: {documentation_words} > 6000 words')
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix not in {'.md','.py','.yaml','.json','.lock'}:
            continue
        rel = path.relative_to(root)
        if '.git' in rel.parts or 'tests' in rel.parts or path.name in HISTORY or path.name.startswith('MIGRATION-'):
            continue
        body = path.read_text(encoding='utf-8')
        body = re.sub(r'MIGRATION-[A-Za-z0-9.>-]+\.md', '', body)
        # Older evidence protocol identifiers (AWF1.2/urn:awf:1.2) are NOT release versions.
        previous = '|'.join(re.escape('.'.join(VERSION.split('.')[:1]) + '.' + str(minor))
                            for minor in range(5, int(VERSION.split('.')[1])))
        if previous and re.search(r'(?<![\d.])(?:' + previous + r')(?:\.\d+)?(?![\d.])', body):
            problems.append(str(rel) + ': stale operational release version')
    prompts = render_prompts(root)
    if set(prompts) != {'controller.md','worker.md','critic.md','amendment.md','specialist-reviewer.md'}:
        problems.append('role prompt template catalog incomplete')
    for name, expected in prompts.items():
        actual = root / '.agentic/prompts' / name
        if not actual.is_file() or actual.read_text(encoding='utf-8') != expected:
            problems.append('.agentic/prompts/' + name + ': regenerate from runtime VERSION ' + VERSION)
    for path in [root / 'AGENTS.md', root / 'README.md', *(root / '.agentic/docs').glob('*.md')]:
        body = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8'), flags=re.S)
        current_header = re.search(r'^Version (\d+\.\d+\.\d+)', body, re.M)
        if current_header and current_header.group(1) != VERSION:
            problems.append(str(path.relative_to(root)) + ': current version header differs from runtime VERSION')
        if len(re.findall(r'^# ', body, re.M)) > 1:
            problems.append(str(path.relative_to(root)) + ': multiple document H1 headings')
    if problems:
        raise ValidationError('Release hygiene failed:\n' + '\n'.join(problems))
    return {'status':'PASS', 'version':VERSION, 'prompts':len(prompts), 'documentation_words':documentation_words}

if __name__ == '__main__':
    import json
    print(json.dumps(check_release()))
