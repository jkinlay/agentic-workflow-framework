"""Fixed-shape evidence digests rendered from validated records only.

A digest replaces free prose in Jira/PR comments. Its footer is generated and
is the only place authority disclaimers appear. Free prose is capped.
"""
from __future__ import annotations
import hashlib
import re

from . import ValidationError

PROSE_WORD_CAP = 80
AUDIENCES = ("jira", "pr", "owner")
AUTHORITIES = ("merge", "release", "activation", "qualification", "Jira Done")


INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def prose_errors(text):
    text = INVISIBLE.sub(" ", text)
    words = len(re.findall(r"[^\W_]+", text))
    if words > PROSE_WORD_CAP:
        return [f"free prose is {words} words; the cap is {PROSE_WORD_CAP}. Post a digest instead."]
    if re.search(r"\b(?:no|not)\s+(?:merge|release|activation|cutover|qualification)\b", text, re.I) and "Authority not claimed" not in text:
        return ["authority disclaimers belong in the generated digest footer, not hand-written prose"]
    return []


def check_prose(text):
    errors = prose_errors(text)
    if errors:
        raise ValidationError("Comment refused: " + "; ".join(errors))
    return text


def footer(applicable=AUTHORITIES):
    return ("Authority not claimed: " + ", ".join(applicable) + " (as applicable). "
            "Evidence comment; not a lifecycle transition.")


def render(state, *, audience="owner", gate=None, findings=(), validation=None, reviewer=None, ci=None):
    """Render one digest. `state` carries AWF state, PR, head, base, tree and tier."""
    if audience not in AUDIENCES:
        raise ValidationError("Digest audience must be jira, pr or owner")
    for key in ("awf_state", "ticket"):
        if not state.get(key):
            raise ValidationError(f"Digest needs {key}")
    header = (f"AWF {state['ticket']} | state {state['awf_state']} | PR {state.get('pr_number', '-')} | "
              f"head {state.get('head_sha', '-')} | base {state.get('base_sha', '-')} | tree {state.get('tree_sha', '-')} | "
              f"tier {state.get('risk_tier', '-')}")
    lines = [header, ""]
    if gate:
        lines += ["| Gate | Result |", "| --- | --- |"]
        lines += [f"| {name} | {value['result']} |" for name, value in gate["gates"].items()]
        lines.append(f"| conclusion | {gate['conclusion']} |")
    else:
        lines.append("Gate: no gate yet")
    lines.append("")
    open_findings = [f for f in findings if f["status"] != "RESOLVED"]
    if open_findings:
        lines.append("Open findings:")
        for item in open_findings:
            basis = item.get("basis") or {}
            key = basis.get("criterion_id") or basis.get("boundary_code") or "none"
            lines.append(f"- {item['id']} {item['severity']} basis={key} status={item['status']}")
    else:
        lines.append("Open findings: none")
    lines.append("")
    if validation:
        executed = sum(v.get("tests_executed", 0) for v in validation)
        codes = ", ".join(str(v.get("exit_code")) for v in validation)
        lines.append(f"Validation: tests_executed={executed} exit_codes=[{codes}]")
    else:
        lines.append("Validation: none recorded")
    if ci:
        lines.append("CI: " + "; ".join(f"{c['name']} {c['conclusion']} tests_executed={c['tests_executed']} depth={c.get('checkout_depth', 'unknown')}" for c in ci))
    if reviewer:
        lines.append(f"Reviewer: engine={reviewer.get('engine', '-')} run={reviewer.get('run_id', '-')}")
    authorities = tuple(state.get("authorities", AUTHORITIES))
    if not authorities or not set(authorities) <= set(AUTHORITIES):
        raise ValidationError("Digest authorities must be a non-empty subset of the generated footer's list")
    lines += ["", footer(authorities)]
    body = "\n".join(lines) + "\n"
    if audience == "jira":
        body = body.replace("| --- | --- |\n", "")
    return body


def digest_sha256(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def evidence_comment_event(body, base_event):
    """Bind a posted digest to its controller-event; comments are never transitions."""
    event = dict(base_event)
    event.update(event_type="evidence_comment", digest_sha256=digest_sha256(body))
    return event
