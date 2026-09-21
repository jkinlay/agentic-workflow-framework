"""Change risk tiers, finding bases, dispositions and the amendment cap.

Everything here is offline analysis of records the trusted coordinator already
holds. Nothing grants execution authority. Owner records reach these functions
only after `authorization.verify_owner_record` has authenticated their shape,
digest, grammar, trusted actor and verifier producer (gates.py, cli.py).
"""
from __future__ import annotations
import fnmatch
import re

from . import ValidationError
from .policy import BOUNDARY_CODES

CAP_DECISIONS = ("MERGE_WITH_NOTES", "PARK", "RESCOPE", "EXTEND_ONE_CYCLE")
CAP_EVENTS = {"MERGE_WITH_NOTES": "CAP_MERGE_WITH_NOTES", "PARK": "CAP_PARK",
              "RESCOPE": "CAP_RESCOPE", "EXTEND_ONE_CYCLE": "CAP_EXTEND_ONE_CYCLE"}
FINDING_DECISIONS = ("ACCEPT_RISK", "REQUIRE_FIX", "NOT_A_DEFECT")
NON_BLOCKING_DECISIONS = {"ACCEPT_RISK", "NOT_A_DEFECT"}
RISK_FLAGS = ("security", "schema_or_migration", "public_api", "data_loss", "concurrency", "production")
DEFAULT_RISK_TIERS = {
    "tier1_eligible_paths": ["tests/**", "fixtures/**", "tools/**", "evidence/**", "docs/**", "*.manifest.json"],
    "tier1_excluded_paths": [],
    "tier1_review": {"roles": ["critic"], "specialist_when_touching": ["security"], "findings": "advisory"},
    "tier2_review": {"roles": ["critic", "specialist"], "findings": "blocking"},
}
# Sentences that would make a mandatory boundary advisory. PROJECT_INSTRUCTIONS.md
# may add review policy; it can never weaken these. Fixed scan, like hygiene.
BOUNDARY_SENTENCE = ("Review tiers, cap dispositions and finding dispositions follow execution.risk_tiers in the reviewed "
                     "configuration and any project-owned PROJECT_INSTRUCTIONS.md; neither can weaken a mandatory boundary.")
# Contradictions that stand even when the sentence contains "not"/"never".
HARD_PHRASES = [
    re.compile(r"\b(?:do\s+not|don't|never)\s+block\b.{0,80}\b(?:protected|boundary|boundaries|credential|scope)\b", re.I | re.S),
    re.compile(r"\b(?:protected|boundary|boundaries|credential|scope)\b.{0,80}\b(?:do\s+not|don't|never)\s+block\b", re.I | re.S),
    re.compile(r"\b(?:boundary|boundaries|protected\s+path)\s+findings?\s+(?:as|are)\s+(?:suggestions?|optional|advice|informational)\b", re.I),
    re.compile(r"\b(?:protected\s+paths?|boundary|boundaries|credential|scope)\b.{0,60}\b(?:not\s+blocking|non\s?blocking|informational\s+only)\b", re.I | re.S),
    re.compile(r"\bunanimity\s+is\s+(?:a|the)\s+merge\s+condition\b", re.I),
    re.compile(r"\bextensions?\s+(?:are|is)\s+unlimited\b", re.I),
]
# Contradictions excused by a restating negation ("boundaries are never advisory").
SOFT_PHRASES = [
    re.compile(r"protected\s+paths?\b.{0,80}\badvisory", re.I | re.S),
    re.compile(r"\badvisory\b.{0,80}\bprotected\s+paths?", re.I | re.S),
    re.compile(r"\b(?:boundary|boundaries|scope\s+escapes?|credential\s+exposures?|unsafe\s+paths?)\b.{0,80}\b(?:advisory|non\s?blocking|waiv(?:e|ed|able)|ignored?)\b", re.I | re.S),
]
NEGATIVE_PHRASES = HARD_PHRASES + SOFT_PHRASES


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def risk_tiers(config):
    """Governance decides eligibility. Without execution.risk_tiers nothing is Tier 1."""
    return config["execution"].get("risk_tiers") or {**DEFAULT_RISK_TIERS, "tier1_eligible_paths": []}


def computed_tier(config, paths, contract=None):
    """Highest-privilege tier the changed paths and contract flags permit, with reasons."""
    tiers = risk_tiers(config)
    reasons = []
    if "risk_tiers" not in config["execution"]:
        reasons.append("execution.risk_tiers is not configured; Tier 1 needs a governance PR declaring tier1_eligible_paths")
    for path in sorted(paths):
        if matches(path, config["scope"]["protected_paths"]):
            reasons.append(f"{path}: matches scope.protected_paths")
        elif matches(path, tiers["tier1_excluded_paths"]):
            reasons.append(f"{path}: matches execution.risk_tiers.tier1_excluded_paths")
        elif not matches(path, tiers["tier1_eligible_paths"]):
            reasons.append(f"{path}: outside execution.risk_tiers.tier1_eligible_paths")
    if contract is not None:
        for flag in RISK_FLAGS:
            if contract["risk_flags"].get(flag) is True:
                reasons.append(f"risk_flags.{flag}: set")
    return (2 if reasons else 1), reasons


def check_tier_declaration(config, contract, paths):
    """Refuse a declared tier more permissive than the computed one; Tier 2 is always accepted."""
    declared = contract.get("risk_tier", 2)
    if declared not in (1, 2) or not str(contract.get("tier_justification", "")).strip():
        raise ValidationError("Contract must declare risk_tier 1 or 2 with a non-empty tier_justification")
    computed, reasons = computed_tier(config, paths, contract)
    if declared < computed:
        raise ValidationError("risk_tier 1 is not eligible: " + "; ".join(reasons))
    return declared


def review_roles(config, tier, domains_touched=()):
    """Roles consulted for a tier; boundaries are never narrowed by the tier."""
    tiers = risk_tiers(config)
    if tier == 2:
        return {"roles": list(tiers["tier2_review"]["roles"]), "findings": "blocking", "specialist_domains": "all_triggered"}
    consulted = [d for d in tiers["tier1_review"]["specialist_when_touching"] if d in set(domains_touched)]
    return {"roles": list(tiers["tier1_review"]["roles"]) + (["specialist"] if consulted else []),
            "findings": "advisory", "specialist_domains": consulted}


def tier1_specialist_domains(config, tier, required_domains):
    """Tier 1 consults only the configured touching domains; Tier 2 consults every trigger."""
    if tier == 2:
        return set(required_domains)
    allowed = set(risk_tiers(config)["tier1_review"]["specialist_when_touching"])
    return set(required_domains) & allowed


def finding_basis_errors(finding, criterion_ids):
    """A BLOCKER/MAJOR finding must name an acceptance criterion or a boundary code."""
    if finding["severity"] not in {"BLOCKER", "MAJOR"}:
        return []
    basis = finding.get("basis")
    if not isinstance(basis, dict) or len(basis) != 1:
        return [f"finding {finding['id']}: BLOCKER/MAJOR requires basis {{criterion_id}} or {{boundary_code}}"]
    if "criterion_id" in basis:
        if basis["criterion_id"] not in set(criterion_ids):
            return [f"finding {finding['id']}: basis criterion {basis['criterion_id']!r} is not in the contract"]
        return []
    if basis.get("boundary_code") not in BOUNDARY_CODES:
        return [f"finding {finding['id']}: unknown boundary code"]
    return []


def is_boundary(finding):
    basis = finding.get("basis")
    return isinstance(basis, dict) and "boundary_code" in basis


def locus(finding):
    basis = finding.get("basis") or {}
    key = basis.get("criterion_id") or basis.get("boundary_code")
    return (finding.get("path"), key) if key else None


def lineage_errors(findings, prior):
    """A new serious finding on an established locus must supersede the earlier finding."""
    errors = []
    prior_ids = {item["id"] for item in prior}
    prior_by_locus = {}
    for item in prior:
        if locus(item) is not None:
            prior_by_locus.setdefault(locus(item), []).append(item["id"])
    for item in findings:
        if item["id"] in prior_ids or item["severity"] not in {"BLOCKER", "MAJOR"}:
            continue
        key = locus(item)
        lineage = prior_by_locus.get(key, []) if key is not None else []
        supersedes = item.get("supersedes_finding_id")
        if lineage and supersedes not in lineage:
            errors.append(f"finding {item['id']}: locus {key!r} already has findings {lineage}; name supersedes_finding_id")
        if supersedes and supersedes not in prior_ids:
            errors.append(f"finding {item['id']}: supersedes unknown finding {supersedes!r}")
    return errors


def validate_findings(findings, prior, criterion_ids):
    errors = []
    for item in findings:
        errors.extend(finding_basis_errors(item, criterion_ids))
    errors.extend(lineage_errors(findings, prior))
    if errors:
        raise ValidationError("Review findings refused; re-emit: " + "; ".join(errors))
    return findings


def effective_dispositions(dispositions, head_sha):
    """Owner finding dispositions bind one exact head; HEAD_CHANGED voids them."""
    result = {}
    for record in dispositions or []:
        if record["decision"] not in FINDING_DECISIONS:
            raise ValidationError("Unknown finding disposition decision")
        if record["head_sha"] != head_sha:
            continue
        if record["finding_id"] in result:
            raise ValidationError(f"finding {record['finding_id']}: more than one disposition for the same head; record one decision")
        result[record["finding_id"]] = record
    return result


def blocking_findings(config, tier, findings, dispositions=None, head_sha=None, cap_accepted_ids=()):
    """Unresolved findings that keep the candidate unready, after tier, dispositions and a cap decision.

    A boundary-basis finding blocks whatever its severity and can never be accepted.
    `cap_accepted_ids` are the open findings an owner's MERGE_WITH_NOTES carries as notes.
    """
    blocking = set(config["critic"]["blocking_severities"])
    applied = effective_dispositions(dispositions, head_sha)
    cap_accepted = set(cap_accepted_ids)
    result, accepted = [], []
    for item in findings:
        if item["status"] == "RESOLVED":
            continue
        disposition = applied.get(item["id"])
        if is_boundary(item):
            if disposition is not None and disposition["decision"] in NON_BLOCKING_DECISIONS:
                raise ValidationError(f"finding {item['id']}: a mandatory boundary ({item['basis']['boundary_code']}) cannot be accepted or dismissed by disposition")
            if item["id"] in cap_accepted:
                raise ValidationError(f"finding {item['id']}: a mandatory boundary cannot be merged with notes")
            result.append(item)
            continue
        if item["severity"] not in blocking:
            continue
        if disposition is not None and disposition["decision"] in NON_BLOCKING_DECISIONS:
            accepted.append({"finding_id": item["id"], "decision": disposition["decision"], "rationale": disposition["rationale"],
                             "summary": item["summary"]})
            continue
        if item["id"] in cap_accepted:
            accepted.append({"finding_id": item["id"], "decision": "MERGE_WITH_NOTES", "rationale": "owner cap disposition",
                             "summary": item["summary"]})
            continue
        result.append(item)
    return result, accepted


def critic_gate(config, tier, critic, findings, dispositions=None, head_sha=None):
    """Tier 2 needs APPROVE; Tier 1 accepts REQUEST_CHANGES once every serious finding is dispositioned."""
    open_blocking, accepted = blocking_findings(config, tier, findings, dispositions, head_sha)
    if open_blocking:
        return False, accepted
    if tier == 2:
        return critic["verdict"] == "APPROVE", accepted
    return critic["verdict"] in {"APPROVE", "REQUEST_CHANGES"}, accepted


def evidence_only(contract, files_changed):
    """Amendments touching only declared evidence/documentation paths do not consume a cycle."""
    patterns = contract["scope"].get("evidence_paths", [])
    return bool(files_changed) and bool(patterns) and all(matches(path, patterns) for path in files_changed)


def cap_status(config, cycles, cap_extensions):
    limit = config["execution"]["max_amendment_cycles"]
    extensions = config["execution"].get("max_cap_extensions", 2)
    return {"cycles": cycles, "max_amendment_cycles": limit, "cap_reached": cycles >= limit,
            "cap_extensions": cap_extensions, "max_cap_extensions": extensions,
            "extension_available": cap_extensions < extensions}


def cap_disposition_plan(config, disposition, cycles, cap_extensions, open_findings):
    """Translate an owner cap disposition into the lifecycle event and verified facts."""
    status = cap_status(config, cycles, cap_extensions)
    decision = disposition["decision"]
    if decision not in CAP_DECISIONS:
        raise ValidationError("Unknown cap disposition")
    if not status["cap_reached"]:
        raise ValidationError("Cap disposition recorded before the amendment cap was reached")
    open_ids = {item["id"] for item in open_findings}
    if set(disposition["open_finding_ids"]) != open_ids:
        raise ValidationError("Cap disposition does not list the currently open findings exactly")
    facts = {"cap_disposition_verified": True}
    if decision == "EXTEND_ONE_CYCLE":
        if not status["extension_available"]:
            raise ValidationError(f"Extension {cap_extensions + 1} exceeds $.execution.max_cap_extensions = {status['max_cap_extensions']}; "
                                  "choose MERGE_WITH_NOTES, PARK or RESCOPE")
        facts["cap_extension_available"] = True
        return {"event": CAP_EVENTS[decision], "facts": facts, "cycles": cycles, "cap_extensions": cap_extensions + 1}
    if decision == "MERGE_WITH_NOTES":
        boundaries = [item["id"] for item in open_findings if is_boundary(item)]
        if boundaries:
            raise ValidationError("MERGE_WITH_NOTES cannot carry open boundary findings: " + ", ".join(boundaries))
        facts.update(no_open_boundary_findings=True, residual_risks_recorded=True)
        residual = [f"Merged with notes under cap disposition: {item['id']} ({item['severity']}) {item['summary']}" for item in open_findings]
        return {"event": CAP_EVENTS[decision], "facts": facts, "residual_risks": residual, "cycles": cycles, "cap_extensions": cap_extensions}
    if decision == "PARK":
        facts["blocker_recorded"] = True
        return {"event": CAP_EVENTS[decision], "facts": facts, "blocker": {"reason_code": "REVIEW_CAP_PARKED", "resume_state": "CHANGES_REQUESTED"},
                "cycles": cycles, "cap_extensions": cap_extensions}
    if not disposition.get("successor_ticket"):
        raise ValidationError("RESCOPE needs a recorded successor ticket")
    facts["successor_recorded"] = True
    return {"event": CAP_EVENTS[decision], "facts": facts, "successor_ticket": disposition["successor_ticket"],
            "cycles": cycles, "cap_extensions": cap_extensions}


def closure_met(contract, worker, critic):
    """Both parties must record MET against the frozen closure standard."""
    kind = contract["closure_standard"]["kind"]
    if kind == "DECLARED_LIMITATIONS" and not contract["closure_standard"]["accepted_limitations"]:
        raise ValidationError("DECLARED_LIMITATIONS closure needs at least one attributed limitation")
    return worker["closure"]["result"] == "MET" and critic["closure"]["result"] == "MET"


RESTATEMENTS = re.compile(r"\b(?:never|not|always|cannot|can't|no\s+tier|in\s+(?:any|every|either|both)\s+tiers?)\b", re.I)


def project_instructions_errors(text):
    """Refuse project instructions that contradict the mandatory boundary enumeration.

    A fixed negative-phrase scan, like hygiene. Sentences that restate the rule
    ("boundaries are never advisory") are accepted; code enforces the rule itself.
    """
    problems = []
    body = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]", "", text)
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"[*_`]", "", body).replace("-", " ")
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", body):
        hard = any(pattern.search(sentence) for pattern in HARD_PHRASES)
        soft = any(pattern.search(sentence) for pattern in SOFT_PHRASES) and not RESTATEMENTS.search(sentence)
        if hard or soft:
            problems.append(sentence.strip()[:200])
    return problems


def successor_contract(contract, finding, merge_commit_sha, now, *, ticket=None, objective=None):
    """Draft the successor ticket's contract for a post-merge finding or an out-of-standard defect.

    The merged ticket's state is unchanged; the successor carries `corrects` and
    starts as a DRAFT Tier 2 contract for the owner to ready.
    """
    import copy
    import uuid
    if not re.fullmatch(r"[0-9a-f]{40}", merge_commit_sha or ""):
        raise ValidationError("successor needs the observed merge commit")
    successor = copy.deepcopy(contract)
    successor.update(contract_id=str(uuid.uuid4()), contract_version=1, created_at=now, disposition="DRAFT",
                     ticket=ticket or contract["ticket"] + "-successor", risk_tier=2,
                     tier_justification="Successor of a merged ticket; uncertain until scoped, so Tier 2.",
                     objective=objective or f"Correct finding {finding['id']} found after merge of {contract['ticket']}: {finding['summary']}",
                     corrects={"ticket": contract["ticket"], "merge_commit_sha": merge_commit_sha},
                     acceptance_criteria=[{"id": "AC1", "text": f"Finding {finding['id']} is resolved: {finding['summary']}",
                                           "validation": "Independent critic verifies the fix at the successor's PR head."}])
    successor["closure_standard"] = {"kind": "FULL", "accepted_limitations": [], "evidence_required": []}
    successor["validation"]["platform_distinction"] = None
    return successor
