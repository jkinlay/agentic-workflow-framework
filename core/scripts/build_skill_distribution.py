#!/usr/bin/env python3
"""Build deterministic AWF source and portable skill archives; tests are separate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION
LINE = '.'.join(VERSION.split('.')[:2])


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def package(root, output):
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            info = zipfile.ZipInfo(root.name + "/" + p.relative_to(root).as_posix(), (2026, 9, 12, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, p.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(output) as z:
        if z.testzip() or len(z.namelist()) != len(paths):
            raise ValueError("Archive verification failed")
    value = digest(output.read_bytes())
    output.with_suffix(output.suffix + ".sha256").write_text(value + "  " + output.name + "\n", encoding="utf-8", newline="\n")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    skill = args.skill_source.resolve(strict=True)
    skill_words = sum(len(p.read_text(encoding='utf-8').split()) for p in skill.rglob('*.md'))
    if skill_words > 1000:
        parser.error(f'Portable skill documentation exceeds 1000 words: {skill_words}')
    output = args.output_dir.absolute()
    if output.is_relative_to(ROOT) or output.is_relative_to(skill) or output.exists():
        parser.error("Use a new output directory outside the release and skill sources")
    forbidden = {"catalog-location.json", "update-channel.json", "local-config.json", "SKILL-MANIFEST.json"}
    for p in skill.rglob("*"):
        if p.is_symlink() or getattr(p.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Links are not distributable: " + str(p))
        if p.is_file() and (p.stat().st_nlink != 1 or p.suffix == ".pyc" or "__pycache__" in p.parts):
            raise ValueError("Runtime residue/hardlink in skill source: " + str(p))
        if p.parent == skill and (p.name in forbidden or p.name.startswith(".awf")):
            raise ValueError("Host-specific or generated metadata in skill source: " + p.name)
    output.mkdir(parents=True)
    release_zip = output / f"agentic-workflow-template-v{LINE}.zip"
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_release.py"), "--output", str(release_zip)], check=True, capture_output=True, text=True, encoding="utf-8")
    proof = json.loads(result.stdout)
    distribution = output / f"AWF-v{LINE}-distribution"
    packaged_skill = distribution / "awf"
    shutil.copytree(skill, packaged_skill, ignore=shutil.ignore_patterns("agentic-workflow-template-v*.zip", "release.json"))
    assets = packaged_skill / "assets"
    assets.mkdir(exist_ok=True)
    for legal_name in ('LICENSE', 'NOTICE', 'PUBLISHER.json'):
        shutil.copyfile(ROOT / legal_name, distribution / legal_name)
    shutil.copyfile(release_zip, assets / release_zip.name)
    write_json(assets / "release.json", {"format": "awf-bundled-release-1", "version": VERSION,
        "archive": release_zip.name, "archive_sha256": proof["zip_sha256"], "manifest_sha256": proof["manifest_sha256"]})
    manifest = {"schema_version": 1, "name": "awf", "version": VERSION,
        "files": [{"path": p.relative_to(packaged_skill).as_posix(), "sha256": digest(p.read_bytes())}
                  for p in sorted(packaged_skill.rglob("*")) if p.is_file()]}
    write_json(packaged_skill / "SKILL-MANIFEST.json", manifest)
    pin = digest((packaged_skill / "SKILL-MANIFEST.json").read_bytes())
    installer_pin = digest((packaged_skill / "scripts/install_skill.py").read_bytes())
    launcher = f'''#!/usr/bin/env python3
"""Install AWF {VERSION}; forwards --dry-run, --dest and --backup-root."""
from pathlib import Path
import hashlib
import subprocess
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or later is required")
root = Path(__file__).resolve().parent / "awf"
for relative, expected in {{"SKILL-MANIFEST.json": "{pin}", "scripts/install_skill.py": "{installer_pin}"}}.items():
    if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
        raise SystemExit("Distribution integrity failure: " + relative)
raise SystemExit(subprocess.call([sys.executable, "-B", str(root / "scripts/install_skill.py"), "--expected-manifest-sha256", "{pin}", *sys.argv[1:]]))
'''
    (distribution / "install_awf.py").write_text(launcher, encoding="utf-8", newline="\n")
    guide = f'''# Install AWF {VERSION}

Extract this complete distribution. With Python 3.11+ installed, run from this folder:

```text
python -B install_awf.py --dry-run
python -B install_awf.py
```

On Windows, `py -3 install_awf.py` is also supported. Use an absolute interpreter path if Python is not on PATH.
The launcher contains the package pins and verifies the installer before executing it. Verify the outer ZIP SHA-256 against the trusted delivery record before running downloaded code.

The installer reuses your user-level `awf` installation, stages and verifies the new package, retains the previous folder in an external backup and preserves local catalog/update-channel settings. Do not uninstall the prior skill first. Use `--dest ABSOLUTE_SKILLS_DIRECTORY/awf` to choose another installation. It does not remove other copies or plugins. See [installation and rollback](awf/references/skill-installation.md).

The skill will be available on your next turn; start a new Codex task if needed to refresh discovery. Invoke `$awf` and ask it to adopt AWF {LINE} in the intended project. Existing projects retain their version until separately migrated. The bundled source contains the applicable versioned migration guides.

Balanced routing selects models and effort by role/task/risk, escalates within configured limits and records usage/outcomes. Adaptive mode initially makes evidence-based recommendations. The host must support the chosen models and enforce actual run limits; synthetic tests do not establish model quality or live integration.

Skill manifest SHA-256: `{pin}`

Release manifest SHA-256: `{proof['manifest_sha256']}`

Release ZIP SHA-256: `{proof['zip_sha256']}`

No personal paths, credentials or local settings are shipped. A copied update channel is a snapshot; future detection requires a maintained owner channel.
'''
    (distribution / "INSTALL.md").write_text(guide, encoding="utf-8", newline="\n")
    write_json(distribution / "AWF_RELEASE_CHANNEL.json", {"format": "awf-update-channel-1", "releases": [{"version": VERSION,
        "archive": "awf/assets/" + release_zip.name, "archive_sha256": proof["zip_sha256"], "manifest_sha256": proof["manifest_sha256"]}]})
    skill_zip = output / f"AWF-SKILL-v{LINE}.zip"
    skill_sha = package(packaged_skill, skill_zip)
    members = {p.relative_to(distribution).as_posix(): digest(p.read_bytes()) for p in sorted(distribution.rglob("*")) if p.is_file()}
    write_json(distribution / "DISTRIBUTION-MANIFEST.json", {"format": "awf-portable-distribution-1", "version": VERSION, "files": members})
    dist_zip = output / f"AWF-v{LINE}-distribution.zip"
    dist_sha = package(distribution, dist_zip)
    receipt = {"version": VERSION, "publisher": "Jonathan Kinlay", "license": "Apache-2.0",
        "skill_documentation_words": skill_words, "source_manifest_sha256": proof["manifest_sha256"], "source_archive_sha256": proof["zip_sha256"],
        "skill_manifest_sha256": pin, "skill_archive_sha256": skill_sha, "distribution_archive_sha256": dist_sha,
        "skill_path": str(packaged_skill), "distribution_zip": str(dist_zip), "tests_run_by_builder": False}
    write_json(output / "BUILD.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
