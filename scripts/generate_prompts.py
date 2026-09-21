"""Generate role prompts from templates and the single runtime VERSION."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION

def render_prompts(root=ROOT):
    return {p.stem + '.md': p.read_text(encoding='utf-8').replace('{{VERSION}}', VERSION)
            for p in sorted((root / 'scripts/prompt_templates').glob('*.md'))}

def main():
    for name, content in render_prompts().items():
        (ROOT / '.agentic/prompts' / name).write_text(content, encoding='utf-8', newline='\n')
    print('Generated role prompts from runtime VERSION')

if __name__ == '__main__':
    main()
