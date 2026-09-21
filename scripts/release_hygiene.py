"""Check real release documentation budgets, operational versions and prompts."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic import VERSION, ValidationError
from generate_prompts import render_prompts

HISTORY = {'CHANGELOG.md', 'MANIFEST.md', 'MANIFEST.json',
           '17-RED-TEAM-DISPOSITION.md', '19-RELEASE-VALIDATION.md'}
HISTORICAL_PILOT_DOCUMENTS = {
    '.agentic/benchmarks/native/EVALUATION-v1.8.4.md',
    '.agentic/benchmarks/native/RESULTS-v1.8.4.md',
    '.agentic/benchmarks/native/EVALUATION-v1.8.7.md',
    '.agentic/benchmarks/native/EVALUATION-v1.8.8.md',
    '.agentic/benchmarks/native/EVALUATION-v1.9.0.md',
    '.agentic/benchmarks/native/review-policy-cases.json',
    '.agentic/benchmarks/native/routine-cases.json',
}
SCAN_SUFFIXES = {'.md', '.py', '.yaml', '.yml', '.json', '.lock', '.ps1', '.txt'}
# Narrow signatures of common UTF-8 punctuation decoded as Windows-1252 and
# re-encoded. Valid UTF-8 alone cannot detect this corruption. Escaped source
# keeps these regression signatures from containing their own damaged text.
MOJIBAKE_SIGNATURES = {
    '\u00e2\u20ac\u201c', '\u00e2\u20ac\u201d',  # en/em dash
    '\u00e2\u20ac\u02dc', '\u00e2\u20ac\u2122',  # single quotes
    '\u00e2\u20ac\u0153', '\u00e2\u20ac\u009d',  # double quotes
    '\u00e2\u20ac\u00a6', '\u00c2\u00a0', '\ufffd',
}
LONG_FORM = {'SPECIFICATION.md', '.agentic/docs/20-NEW-PROJECT-SETUP.md',
             '.agentic/docs/21-EXISTING-PROJECT-ADOPTION.md',
             '.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md',
             '.agentic/docs/25-GIT-LINE-ENDINGS-AND-PROJECT-MIGRATION.md',
             '.agentic/docs/26-LOCAL-DISCOVERY-AND-ADOPTION.md',
             '.agentic/docs/27-MODEL-ROUTING.md'}
PROMPT_PATHS = {'.agentic/prompts', 'scripts/prompt_templates'}
VERSION_TOKEN = r'(?P<version>\d+\.\d+(?:\.\d+)?)(?!\d|\.\d)'
# Match release meaning, not bare numbers: requirements such as PyYAML==6.0.2,
# schema URNs and the compact AWF1.2 authorization wire format are independent.
RELEASE_PATTERNS = [
    re.compile(r'\b(?:AWF|AWT|Agentic Workflow(?: Framework)?)\s+(?:release\s+)?v?' + VERSION_TOKEN, re.I),
    re.compile(r'\b(?:agentic-workflow-template-v|AWF-(?:SKILL-v|v)|awf-(?:runtime-|venv-)?v?)' + VERSION_TOKEN, re.I),
    re.compile(r'\b(?:use\s+template|template\s+release|workflow\s+release)\s+v?' + VERSION_TOKEN, re.I),
    re.compile(r'\b(?:managed|same-version)\s+v?' + VERSION_TOKEN + r'\s+(?:workflow|installation)', re.I),
    re.compile(r'^\s*(?:#+\s*)?Version\s+v?' + VERSION_TOKEN, re.I | re.M),
    re.compile(r'[\x22\x27]?(?:template_version|expected_workflow_version|bundled_awf_version)[\x22\x27]?\s*[:=]\s*[\x22\x27]?' + VERSION_TOKEN, re.I),
    re.compile(r'--version(?:\s+|=)[\x22\x27]?v?' + VERSION_TOKEN, re.I),
]


def version_tuple(value):
    if not isinstance(value, str) or re.fullmatch(r'\d+\.\d+\.\d+', value) is None:
        raise ValidationError('Release hygiene needs a three-part runtime VERSION')
    return tuple(map(int, value.split('.')))


def word_budget(relative):
    """Per-file Markdown limits; generated manifest stays visible but uncapped."""
    name = relative.as_posix()
    if relative.name == 'MANIFEST.md':
        return None, 'generated_manifest'
    if relative.parent.as_posix() in PROMPT_PATHS or name in {
            '.agentic/review-loop/critic-prompt.md', '.agentic/review-loop/worker-prompt.md'}:
        return 350, 'native_prompt'
    if name in LONG_FORM or 'RUNBOOK' in relative.name.upper():
        return 1200, 'specification_or_runbook'
    return 800, 'documentation'


def stale_versions(body, current):
    """Older explicitly operational releases, across minor AND major boundaries."""
    current_tuple = version_tuple(current)
    # References to retained migration/disposition documents are historical links.
    body = re.sub(r'(?:MIGRATION-|RED-TEAM-DISPOSITION-v)[A-Za-z0-9.>_-]+\.md', '', body)
    if 'urn:awf:1.2:' in body:
        body = body.replace('Agentic Workflow 1.2 —', 'Authorization protocol —')
        body = body.replace(r'Agentic Workflow 1.2 \u2014', 'Authorization protocol')
    found = set()
    for pattern in RELEASE_PATTERNS:
        for match in pattern.finditer(body):
            value = match.group('version')
            parts = tuple(map(int, value.split('.')))
            comparison = current_tuple[:len(parts)]
            if parts < comparison:
                found.add(value)
    return sorted(found)


def check_release(root=ROOT, version=None):
    root = Path(root)
    current = version or VERSION
    version_tuple(current)
    problems, budgets = [], []
    total_words = manifest_words = 0
    files = sorted(p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts)
    for path in files:
        rel = path.relative_to(root)
        if path.suffix.lower() == '.md':
            words = len(path.read_text(encoding='utf-8').split())
            cap, category = word_budget(rel)
            total_words += words
            if cap is None:
                manifest_words += words
            elif words > cap:
                problems.append(f'{rel.as_posix()}: documentation budget exceeded: {words} > {cap} words ({category})')
            budgets.append({'path': rel.as_posix(), 'words': words, 'limit': cap, 'category': category})
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        body = path.read_text(encoding='utf-8')
        # History/tests are exempt from operational-version checks, not from
        # accidental text corruption; deliberate fixtures use Unicode escapes.
        if any(marker in body for marker in MOJIBAKE_SIGNATURES):
            problems.append(rel.as_posix() + ': suspected mojibake or replacement character; inspect original text')
        historical_validation = rel.parts[:3] == ('.agentic', 'validation', 'history') and path.suffix == '.json'
        if ('tests' in rel.parts or historical_validation or path.name in HISTORY
                or rel.as_posix() in HISTORICAL_PILOT_DOCUMENTS
                or path.name.startswith(('MIGRATION-', 'RED-TEAM-DISPOSITION-'))):
            continue
        stale = stale_versions(body, current)
        if stale:
            problems.append(rel.as_posix() + ': stale operational release version: ' + ', '.join(stale))
    if current == VERSION:
        prompts = render_prompts(root)
    else:
        # Explicit version override supports major-release regression fixtures.
        prompts = {p.stem + '.md': p.read_text(encoding='utf-8').replace('{{VERSION}}', current)
                   for p in sorted((root / 'scripts/prompt_templates').glob('*.md'))}
    if set(prompts) != {'controller.md', 'worker.md', 'critic.md', 'amendment.md', 'specialist-reviewer.md'}:
        problems.append('role prompt template catalog incomplete')
    for name, expected in prompts.items():
        actual = root / '.agentic/prompts' / name
        if not actual.is_file() or actual.read_text(encoding='utf-8') != expected:
            problems.append('.agentic/prompts/' + name + ': regenerate from runtime VERSION ' + current)
    for path in [root / 'AGENTS.md', root / 'README.md', *(root / '.agentic/docs').glob('*.md')]:
        if not path.is_file():
            problems.append(str(path.relative_to(root)) + ': required document missing')
            continue
        body = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8'), flags=re.S)
        current_header = re.search(r'^Version (\d+\.\d+\.\d+)', body, re.M)
        if current_header and current_header.group(1) != current:
            problems.append(str(path.relative_to(root)) + ': current version header differs from runtime VERSION')
        if len(re.findall(r'^# ', body, re.M)) > 1:
            problems.append(str(path.relative_to(root)) + ': multiple document H1 headings')
    if problems:
        raise ValidationError('Release hygiene failed:\n' + '\n'.join(problems))
    return {'status': 'PASS', 'version': current, 'prompts': len(prompts),
            'documentation_words': total_words - manifest_words,
            'markdown_total_words': total_words, 'generated_manifest_words': manifest_words,
            'documentation_file_budgets': budgets}


if __name__ == '__main__':
    import json
    print(json.dumps(check_release()))
