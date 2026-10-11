"""Fail-closed AWF skill discovery checks for host-launched Codex sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from . import VERSION, ValidationError


FORMAT = "awf-codex-skill-selection-1"
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def _absolute(path):
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _profile_homes(environment):
    """Return every explicit profile root in the platform's precedence order."""
    names = ("USERPROFILE", "HOME") if os.name == "nt" else ("HOME", "USERPROFILE")
    homes = []
    for name in names:
        value = environment.get(name)
        if value:
            path = _absolute(value)
            if path not in homes:
                homes.append(path)
    if not homes:
        homes.append(_absolute(Path.home()))
    return homes


def _json_version(path, key):
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8")).get(key)
        return value if isinstance(value, str) and SEMVER.fullmatch(value) else None
    except (OSError, UnicodeError, ValueError, AttributeError):
        return None


def skill_version(skill):
    """Read consistent structured/frontmatter version declarations for one skill."""
    skill = Path(skill)
    declarations = []
    for relative, key in (
        (".awf-install-receipt.json", "version"),
        ("SKILL-MANIFEST.json", "version"),
        (".awf-skill-receipt.json", "bundled_awf_version"),
        ("assets/release.json", "version"),
    ):
        value = _json_version(skill / relative, key)
        if value:
            declarations.append(value)
    try:
        text = (skill / "SKILL.md").read_text(encoding="utf-8-sig")
        match = re.search(r"(?i)\b(?:includes\s+)?AWF\s+(\d+\.\d+\.\d+)\b", text)
        if match:
            declarations.append(match.group(1))
    except (OSError, UnicodeError):
        pass
    return declarations[0] if declarations and len(set(declarations)) == 1 else None


def _candidate_skills(launch_directory, environment):
    homes = _profile_homes(environment)
    codex_home = _absolute(environment.get("CODEX_HOME") or (homes[0] / ".codex"))
    candidates = []

    def add(path, visible_to):
        candidates.append((_absolute(path), visible_to))

    add(codex_home / "skills" / "awf",
        f"Codex sessions launched with CODEX_HOME={codex_home}")
    for home in homes:
        add(home / ".codex" / "skills" / "awf",
            f"Codex sessions using the profile home {home}")
        add(home / ".agents" / "skills" / "awf",
            f"Agent sessions using the profile home {home}")
    launch = _absolute(launch_directory)
    for ancestor in (launch, *launch.parents):
        add(ancestor / ".codex" / "skills" / "awf",
            f"Codex sessions launched in {ancestor} or descendants")
        add(ancestor / ".agents" / "skills" / "awf",
            f"Agent sessions launched in {ancestor} or descendants")
    return codex_home / "skills" / "awf", candidates


def observe_codex_awf_skills(project_root, launch_directory, environment=None):
    """Describe every known AWF skill location visible to a pending launch."""
    environment = dict(os.environ if environment is None else environment)
    pinned, candidates = _candidate_skills(launch_directory, environment)
    combined = {}
    for path, visible_to in candidates:
        key = os.path.normcase(str(path))
        item = combined.setdefault(key, {"path": str(path), "visible_to": []})
        if visible_to not in item["visible_to"]:
            item["visible_to"].append(visible_to)
    visible = []
    for item in combined.values():
        path = Path(item["path"])
        if path.is_dir():
            visible.append({
                "path": item["path"],
                "version": skill_version(path),
                "visible_to": "; ".join(item["visible_to"]),
            })
    visible.sort(key=lambda item: os.path.normcase(item["path"]))
    problems = []
    pinned_text = str(_absolute(pinned))
    for item in visible:
        if item["version"] is None:
            problems.append(f"AWF skill version could not be established: {item['path']}")
        elif item["version"] != VERSION:
            problems.append(
                f"AWF skill {item['path']} has version {item['version']}; expected {VERSION}"
            )
    return {
        "format": FORMAT,
        "expected_version": VERSION,
        "project_root": str(_absolute(project_root)),
        "pinned_skill_path": pinned_text,
        "launch_directory": str(_absolute(launch_directory)),
        "visible_skills": visible,
        "status": "REFUSED" if problems else "PASS",
        "problems": problems,
    }


def enforce_pinned_awf_skill(project_root, launch_directory, environment=None,
                             *, record_path=None):
    """Record discovery and refuse a launch that could load another AWF version."""
    record = observe_codex_awf_skills(project_root, launch_directory, environment)
    if record_path is not None:
        target = Path(record_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    if record["status"] != "PASS":
        raise ValidationError("Codex AWF skill selection refused: " + "; ".join(record["problems"]))
    return record
