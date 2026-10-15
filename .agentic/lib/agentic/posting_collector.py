"""Trusted review-verdict posting observation for the production final gate.

Configuration may narrow ``COLLECTOR_ID`` but cannot create this registry.
Membership is produced only after this installed module re-reads the comment
and pull-request body through the enrolled review-host provider.

The pull-request body must carry this literal line outside code and comments:
<a id="review-verdict-<round>-<record_id>"></a>
The only other accepted form is ``<a name="review-verdict-<round>-<record_id>"></a>``.
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import re
import uuid

from . import ValidationError
from .canonical import canonical, sha256
from .gates import (overcap_approval_collector_receipt_sha256,
                    posting_collector_receipt_sha256)
from .policy import parse_overcap_approval
from .providers.github_review_host import HostDriver, load_config


COLLECTOR_ID = "github-production-posting-collector"
PROVIDER_KIND = "github"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TOP_LEVEL_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_LIST_FENCE = re.compile(
    r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]+"
    r"(?P<marker>`{3,}|~{3,})(?P<info>.*)$"
)
_BLOCKQUOTE_FENCE = re.compile(
    r"^ {0,3}>[ \t]?(?P<marker>`{3,}|~{3,})(?P<info>.*)$"
)
_POSSIBLE_FENCE = re.compile(
    r"^[ \t]*(?:(?:[-+*]|[0-9]{1,9}[.)])[ \t]+|>[ \t]?)?[`~]{3,}"
)
_BACKTICK_RUN = re.compile(r"`+")
_HTML_NAME = r"[A-Za-z][A-Za-z0-9.:-]*"
_HTML_PAIRED_TAG = re.compile(
    rf"^<(?P<tag>{_HTML_NAME})(?P<attrs>.*?)>"
    rf"[ \t]*</(?P=tag)[ \t]*>$",
    re.IGNORECASE,
)
_HTML_SELF_CLOSING_TAG = re.compile(
    rf"^<(?P<tag>{_HTML_NAME})(?P<attrs>.*?)/>$",
    re.IGNORECASE,
)
_HTML_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "base", "basefont", "blockquote", "body",
    "caption", "center", "col", "colgroup", "dd", "details", "dialog", "dir",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head",
    "header", "hr", "html", "iframe", "legend", "li", "link", "main", "menu",
    "menuitem", "nav", "noframes", "ol", "optgroup", "option", "p", "param",
    "search", "section", "summary", "table", "tbody", "td", "tfoot", "th",
    "thead", "title", "tr", "track", "ul",
})
_HTML_LITERAL_START = re.compile(
    r"^ {0,3}<(?P<tag>pre|script|style|textarea)(?=[ \t>]|$)",
    re.IGNORECASE,
)
_HTML_LITERAL_END = {
    tag: re.compile(rf"</{tag}>", re.IGNORECASE)
    for tag in ("pre", "script", "style", "textarea")
}
_HTML_BLOCK_TAG_START = re.compile(
    r"^ {0,3}</?(?P<tag>[A-Za-z][A-Za-z0-9-]*)(?P<suffix>.*)$"
)
_COMMONMARK_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
_COMMONMARK_ATTRIBUTE_NAME = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
_COMMONMARK_UNQUOTED_VALUE = r"[^ \t\n\f\r\"'=<>`]+"
_COMMONMARK_ATTRIBUTE_VALUE = (
    rf"(?:{_COMMONMARK_UNQUOTED_VALUE}|\"[^\"]*\"|'[^']*')"
)
_COMMONMARK_ATTRIBUTE = (
    rf"(?:[ \t]+{_COMMONMARK_ATTRIBUTE_NAME}"
    rf"(?:[ \t]*=[ \t]*{_COMMONMARK_ATTRIBUTE_VALUE})?)"
)
_HTML_COMPLETE_OPEN_TAG = re.compile(
    rf"^ {{0,3}}<(?P<tag>{_COMMONMARK_TAG_NAME})"
    rf"(?:{_COMMONMARK_ATTRIBUTE})*[ \t]*/?>[ \t]*$"
)
_HTML_COMPLETE_CLOSE_TAG = re.compile(
    rf"^ {{0,3}}</(?P<tag>{_COMMONMARK_TAG_NAME})[ \t]*>[ \t]*$"
)
_CONTAINER_CONTENT = (
    re.compile(r"^ {0,3}>[ \t]?(?P<content>.*)$"),
    re.compile(r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]+(?P<content>.*)$"),
)


class PostingCollectorMismatch(ValidationError):
    """A live provider observation differs from the retained posting claim."""


def _mismatch(code, detail):
    raise PostingCollectorMismatch(f"{code}: {detail}")


def _digest(value, label):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _mismatch("POSTING_REGISTRATION_MISMATCH", f"{label} is not a SHA-256 digest")
    return value


def _registration(release_sha256, repository_id):
    return {
        "collector_id": COLLECTOR_ID,
        "provider_kind": PROVIDER_KIND,
        "implementation_sha256": sha256(Path(__file__).read_bytes()),
        "release_sha256": _digest(release_sha256, "release_sha256"),
        "repository_ids": [repository_id],
        "receipts": {},
    }


def _gate_entry(registration):
    return {key: value for key, value in registration.items() if key != "collector_id"}


def _provider_response(comment_id, comment_url, comment_body, pr_body,
                       owner_approval=None):
    # Retain only the provider fields used by the gate.  This deliberately
    # excludes actor profiles, headers and other unrelated provider data.
    response = {
        "comment_id": comment_id,
        "comment_url": comment_url,
        "comment_body": comment_body,
        "pr_body": pr_body,
    }
    if owner_approval is not None:
        response["owner_approval"] = owner_approval
    return canonical(response)


def _formal_owner_approval(review_host, candidate, owner_review, pull):
    """Re-read one formal GitHub approval bound to the trusted owner claim."""
    if not isinstance(owner_review, dict) or owner_review.get("owner_review") is not True:
        _mismatch("OWNER_APPROVAL_MISMATCH", "Tier 3 owner review record is missing")
    expected_binding = {
        "repository_id": candidate.get("repository_id"),
        "pr_number": candidate.get("pr_number"),
        "base_sha": candidate.get("target_base_sha"),
        "head_sha": candidate.get("head_sha"),
    }
    if owner_review.get("candidate_binding") != expected_binding:
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "owner review does not bind the exact candidate")
    prefix = (
        f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}"
    )
    match = re.fullmatch(
        re.escape(prefix) + r"#pullrequestreview-([1-9][0-9]*)",
        owner_review.get("pr_comment_url", ""),
    )
    if match is None:
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "owner review URL is not an immutable GitHub review URL")
    head = pull.get("head") if isinstance(pull, dict) else None
    base = pull.get("base") if isinstance(pull, dict) else None
    if (not isinstance(head, dict) or not isinstance(base, dict)
            or head.get("sha") != candidate["head_sha"]
            or base.get("sha") != candidate["target_base_sha"]):
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "live pull request head/base differs from the owner candidate")
    review_id = int(match.group(1))
    review = review_host.api(
        f"pulls/{candidate['pr_number']}/reviews/{review_id}"
    )
    user = review.get("user") if isinstance(review, dict) else None
    if (not isinstance(review, dict) or review.get("id") != review_id
            or review.get("html_url") != owner_review["pr_comment_url"]
            or review.get("state") != "APPROVED"
            or review.get("commit_id") != candidate["head_sha"]
            or not isinstance(user, dict)
            or user.get("id") != owner_review.get("owner_id")):
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "GitHub review is not an approval by the claimed owner of the exact head")
    return {
        "id": review_id,
        "html_url": review["html_url"],
        "state": review["state"],
        "commit_id": review["commit_id"],
        "user": {"id": user["id"]},
        "candidate": expected_binding,
    }


def _retained_owner_approval(owner_review):
    observation = owner_review.get("posting_observation") if isinstance(owner_review, dict) else None
    envelope = observation.get("provider_response_bytes") if isinstance(observation, dict) else None
    try:
        raw = base64.b64decode(envelope["data"].encode("ascii"), validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (KeyError, TypeError, ValueError, UnicodeError):
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "owner record has no decodable provider approval evidence")
    approval = value.get("owner_approval") if isinstance(value, dict) else None
    if not isinstance(approval, dict):
        _mismatch("OWNER_APPROVAL_MISMATCH",
                  "owner record has no provider-observed formal approval")
    return approval


def _without_code_spans(markdown):
    """Remove definite CommonMark backtick spans; refuse an unmatched run."""
    visible = []
    cursor = 0
    while True:
        opener = _BACKTICK_RUN.search(markdown, cursor)
        if opener is None:
            visible.append(markdown[cursor:])
            break
        closer = _BACKTICK_RUN.search(markdown, opener.end())
        while closer is not None and len(closer.group()) != len(opener.group()):
            closer = _BACKTICK_RUN.search(markdown, closer.end())
        if closer is None:
            return None
        visible.append(markdown[cursor:opener.start()])
        visible.append("\n" * markdown[opener.start():closer.end()].count("\n"))
        cursor = closer.end()
    return "".join(visible)


def _fence_open(line):
    """Return a conservative fence description, or ``None`` when not a fence."""
    for kind, pattern in (
        ("top", _TOP_LEVEL_FENCE),
        ("list", _LIST_FENCE),
        ("blockquote", _BLOCKQUOTE_FENCE),
    ):
        match = pattern.fullmatch(line)
        if match is None:
            continue
        marker = match.group("marker")
        info = match.group("info")
        if marker[0] == "`" and "`" in info:
            return False
        return {
            "character": marker[0],
            "length": len(marker),
            "kind": kind,
            "content_indent": match.start("marker") if kind == "list" else 0,
        }
    if _POSSIBLE_FENCE.match(line):
        return False
    return None


def _fence_close(line, fence):
    marker = re.escape(fence["character"]) + "{" + str(fence["length"]) + ",}"
    if fence["kind"] == "top":
        prefix = r" {0,3}"
    elif fence["kind"] == "blockquote":
        prefix = r" {0,3}>[ \t]?"
    else:
        prefix = " " * fence["content_indent"]
    return re.fullmatch(prefix + marker + r"[ \t]*", line) is not None


def _strip_html_comments(line, in_comment):
    """Strip comments, retaining an unclosed comment as an end-of-body state."""
    visible = []
    cursor = 0
    while True:
        if in_comment:
            closer = line.find("-->", cursor)
            if closer < 0:
                return "".join(visible), True, True
            cursor = closer + 3
            in_comment = False
        opener = line.find("<!--", cursor)
        stray_closer = line.find("-->", cursor)
        if stray_closer >= 0 and (opener < 0 or stray_closer < opener):
            return "", in_comment, False
        if opener < 0:
            visible.append(line[cursor:])
            return "".join(visible), in_comment, True
        visible.append(line[cursor:opener])
        cursor = opener + 4
        in_comment = True


def _raw_html_start(line, type_7_boundary):
    """Return a CommonMark 0.31 HTML-block end condition for ``line``."""
    literal = _HTML_LITERAL_START.match(line)
    if literal is not None:
        return _HTML_LITERAL_END[literal.group("tag").casefold()]
    if re.match(r"^ {0,3}<!--", line):
        return "-->"
    if re.match(r"^ {0,3}<\?", line):
        return "?>"
    if re.match(r"^ {0,3}<![A-Za-z]", line):
        return ">"
    if re.match(r"^ {0,3}<!\[CDATA\[", line):
        return "]]>"

    block_tag = _HTML_BLOCK_TAG_START.match(line)
    if block_tag is not None and block_tag.group("tag").casefold() in _HTML_BLOCK_TAGS:
        suffix = block_tag.group("suffix")
        if not suffix or suffix[0] in " \t>" or suffix.startswith("/>"):
            return "blank"

    complete = _HTML_COMPLETE_OPEN_TAG.fullmatch(line)
    if complete is not None:
        if complete.group("tag").casefold() in {
            "pre", "script", "style", "textarea",
        }:
            return None
    else:
        complete = _HTML_COMPLETE_CLOSE_TAG.fullmatch(line)
    if complete is not None:
        if not type_7_boundary:
            return False
        return "blank"
    return None


def _raw_html_ended(line, end_condition):
    if hasattr(end_condition, "search"):
        return end_condition.search(line) is not None
    return end_condition in line


def _container_raw_html_start(line):
    for pattern in _CONTAINER_CONTENT:
        match = pattern.fullmatch(line)
        if match is not None:
            return _raw_html_start(match.group("content"), True) is not None
    return False


def _visible_markdown(body):
    """Return definitely visible lines, or ``None`` when syntax is ambiguous."""
    visible = []
    fence = None
    in_comment = False
    raw_html_end = None
    type_7_boundary = True
    for line in body.splitlines():
        if fence is not None:
            if _fence_close(line, fence):
                fence = None
                type_7_boundary = True
            continue
        if raw_html_end is not None:
            if raw_html_end == "blank":
                if not line.strip(" \t"):
                    raw_html_end = None
                    type_7_boundary = True
                continue
            if _raw_html_ended(line, raw_html_end):
                raw_html_end = None
                type_7_boundary = True
            continue
        if not in_comment:
            opened = _fence_open(line)
            if opened is False:
                return None
            if opened is not None:
                fence = opened
                continue
            if line.startswith("\t") or line.startswith("    "):
                visible.append("")
                type_7_boundary = True
                continue
            if _container_raw_html_start(line):
                return None
            raw_html_end = _raw_html_start(line, type_7_boundary)
            if raw_html_end is False:
                return None
            if raw_html_end is not None:
                if raw_html_end != "blank" and _raw_html_ended(line, raw_html_end):
                    raw_html_end = None
                    type_7_boundary = True
                continue
        line, in_comment, certain = _strip_html_comments(line, in_comment)
        if not certain:
            return None
        visible.append(line)
        type_7_boundary = not line.strip(" \t")
    if fence is not None:
        return None
    # An unclosed HTML comment intentionally hides the rest of the document.
    return _without_code_spans("\n".join(visible))


def _html_fragment(line, fragment):
    """Recognise only a complete ``a`` with one fragment attribute."""
    match = _HTML_PAIRED_TAG.fullmatch(line)
    if match is None:
        match = _HTML_SELF_CLOSING_TAG.fullmatch(line)
    if match is None:
        return None
    return line in {
        f'<a id="{fragment}"></a>',
        f'<a name="{fragment}"></a>',
    }


def _defines_fragment(body, fragment):
    visible = _visible_markdown(body)
    if visible is None:
        return False
    found = False
    for line in visible.splitlines():
        html = _html_fragment(line, fragment)
        if html is True:
            found = True
            continue
        if fragment in line:
            return False
    return found


def collect_posting_observation(review_host, *, candidate, verdict,
                                review_round_receipt, collector_run_id,
                                observed_at, release_sha256, owner_review=None):
    """Read one posted verdict and return its observation and registration.

    ``review_host`` is the existing GitHub review-host provider (or a
    test-local double).  The caller owns the collector run and timestamp; this
    function owns provider reads and the trusted collector implementation pin.
    """
    if not isinstance(candidate, dict) or not isinstance(verdict, dict):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH", "candidate and verdict are required")
    if not isinstance(review_round_receipt, dict):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH", "retained round receipt is required")
    artifact_binding = verdict.get("critic_artifact_binding")
    if (review_round_receipt.get("critic_artifact_binding") != artifact_binding
            or review_round_receipt.get("review_verdict_sha256")
            != sha256(review_round_receipt.get("review_verdict_json", "").encode("utf-8"))):
        _mismatch("POSTING_ROUND_BINDING_MISMATCH",
                  "verdict and retained round receipt do not share one critic artifact")
    verdict_json = review_round_receipt["review_verdict_json"]
    verdict_sha256 = review_round_receipt["review_verdict_sha256"]
    prefix = (
        f"{candidate['host']}/{candidate['repository']}/pull/{candidate['pr_number']}"
    )
    match = re.fullmatch(re.escape(prefix) + r"#issuecomment-([1-9][0-9]*)",
                         verdict.get("pr_comment_url", ""))
    if match is None:
        _mismatch("POSTING_COMMENT_MISMATCH",
                  "verdict comment URL is not the candidate's immutable comment URL")
    comment_id = int(match.group(1))
    expected_body_link = (
        f"{prefix}#review-verdict-{verdict['round']}-{verdict['record_id']}"
    )
    if verdict.get("pr_body_link") != expected_body_link:
        _mismatch("POSTING_ANCHOR_MISMATCH",
                  "verdict PR-body link does not identify its round and record")

    comment = review_host.api(f"issues/comments/{comment_id}")
    pull = review_host.api(f"pulls/{candidate['pr_number']}")
    if (not isinstance(comment, dict) or comment.get("id") != comment_id
            or comment.get("html_url") != verdict["pr_comment_url"]
            or not isinstance(comment.get("body"), str)
            or verdict_json not in comment["body"]):
        _mismatch("POSTING_COMMENT_MISMATCH",
                  "live immutable comment identity, URL or exact body differs")
    base = pull.get("base") if isinstance(pull, dict) else None
    base_repository = base.get("repo") if isinstance(base, dict) else None
    if (not isinstance(pull, dict) or pull.get("number") != candidate["pr_number"]
            or pull.get("html_url") != prefix
            or not isinstance(base_repository, dict)
            or base_repository.get("id") != candidate["repository_id"]):
        _mismatch("POSTING_BODY_MISMATCH",
                  "live pull request identity differs from the candidate")
    body = pull.get("body")
    if not isinstance(body, str):
        _mismatch("POSTING_BODY_MISMATCH", "live pull request body is not text")
    fragment = f"review-verdict-{verdict['round']}-{verdict['record_id']}"
    if not _defines_fragment(body, fragment):
        _mismatch("POSTING_ANCHOR_MISMATCH",
                  "live pull request body does not define the linked verdict fragment")
    anchor = f"review-verdict:{verdict['round']}:{verdict['record_id']}:{verdict_sha256}"
    if anchor not in body:
        _mismatch("POSTING_ANCHOR_MISMATCH",
                  "live pull request body omits the exact verdict anchor")

    owner_approval = (_formal_owner_approval(
        review_host, candidate, owner_review, pull
    ) if owner_review is not None else None)
    provider_response = _provider_response(
        comment_id, comment["html_url"], comment["body"], body, owner_approval
    )
    registration = _registration(release_sha256, candidate["repository_id"])
    observation = {
        "source": "host_observation",
        "observed_at": observed_at,
        "producer_id": COLLECTOR_ID,
        "run_id": collector_run_id,
        "provider_kind": PROVIDER_KIND,
        "critic_artifact_binding": artifact_binding,
        "review_verdict_record_id": verdict["record_id"],
        "review_verdict_sha256": verdict_sha256,
        "repository_id": candidate["repository_id"],
        "pr_number": candidate["pr_number"],
        "comment_id": comment_id,
        "comment_url": comment["html_url"],
        "comment_bytes": comment["body"],
        "comment_sha256": sha256(comment["body"].encode("utf-8")),
        "body_link": expected_body_link,
        "body_bytes": body,
        "body_sha256": sha256(body.encode("utf-8")),
        "provider_response_bytes": {
            "encoding": "base64",
            "data": base64.b64encode(provider_response).decode("ascii"),
        },
        "provider_response_sha256": sha256(provider_response),
        "collector_receipt_sha256": "0" * 64,
    }
    binding_sha256 = sha256(canonical(observation))
    receipt = posting_collector_receipt_sha256(
        observation, binding_sha256, _gate_entry(registration)
    )
    observation["collector_receipt_sha256"] = receipt
    registration["receipts"][collector_run_id] = receipt
    return {"observation": observation, "registration": registration}


def _retained_mismatch(expected, retained):
    if not isinstance(retained, dict):
        _mismatch("POSTING_OBSERVATION_MISMATCH", "retained observation is missing")
    groups = (
        ("POSTING_COLLECTOR_RUN_MISMATCH",
         ("source", "producer_id", "run_id", "provider_kind")),
        ("POSTING_ROUND_BINDING_MISMATCH",
         ("critic_artifact_binding", "review_verdict_record_id", "repository_id", "pr_number")),
        ("POSTING_VERDICT_DIGEST_MISMATCH", ("review_verdict_sha256",)),
        ("POSTING_COMMENT_MISMATCH",
         ("comment_id", "comment_url", "comment_bytes", "comment_sha256")),
        ("POSTING_ANCHOR_MISMATCH", ("body_link",)),
        ("POSTING_BODY_MISMATCH", ("body_bytes", "body_sha256")),
        ("POSTING_PROVIDER_RESPONSE_MISMATCH",
         ("provider_response_bytes", "provider_response_sha256")),
        ("POSTING_COLLECTOR_RUN_MISMATCH", ("collector_receipt_sha256",)),
    )
    for code, fields in groups:
        differing = [field for field in fields if retained.get(field) != expected.get(field)]
        if differing:
            _mismatch(code, "retained fields differ: " + ", ".join(differing))
    if set(retained) != set(expected) or retained.get("observed_at") != expected.get("observed_at"):
        _mismatch("POSTING_OBSERVATION_MISMATCH",
                  "retained observation shape or observation time differs")


def _overcap_comments(review_host, candidate):
    """Return every issue comment, refusing an incomplete provider listing."""
    comments = []
    for page in range(1, 101):
        value = review_host.api(
            f"issues/{candidate['pr_number']}/comments?per_page=100&page={page}"
        )
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            _mismatch("OVERCAP_APPROVAL_MISMATCH",
                      "GitHub PR comments response is malformed")
        comments.extend(value)
        if len(value) < 100:
            return comments
    _mismatch("OVERCAP_APPROVAL_MISMATCH",
              "GitHub PR comments exceed the bounded complete listing")


def _overcap_collector_run(bundle, comment_id, observed_at):
    """Create a deterministic runtime collector run for one approval receipt."""
    candidate = bundle["candidate"]
    identity = (
        f"{candidate['repository_id']}:{candidate['pr_number']}:"
        f"{candidate['head_sha']}:{comment_id}"
    )
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "awf-overcap-run:" + identity))
    existing = next(
        (item for item in bundle.get("runs", []) if item.get("run_id") == run_id),
        None,
    )
    if existing is not None:
        return existing, False
    template = next(
        (item for item in reversed(bundle.get("runs", []))
         if item.get("role") == "collector" and item.get("producer_id") == COLLECTOR_ID),
        None,
    )
    if template is None:
        _mismatch("OVERCAP_APPROVAL_MISMATCH",
                  "no runtime-owned production collector run is available")
    run = copy.deepcopy(template)
    run.update(
        record_id=str(uuid.uuid5(
            uuid.NAMESPACE_URL, "awf-overcap-record:" + identity)),
        run_id=run_id,
        context_id=str(uuid.uuid5(
            uuid.NAMESPACE_URL, "awf-overcap-context:" + identity)),
        producer_id=COLLECTOR_ID,
        binding=copy.deepcopy(bundle["review_verdicts"][-1]["binding"]),
        created_at=observed_at,
    )
    return run, True


def _collect_overcap_approvals(review_host, bundle, pull, registration):
    """Discover and record canonical approvals for retained over-cap rounds."""
    candidate = bundle["candidate"]
    overcap_verdicts = {
        item["round"]: item for item in bundle.get("review_verdicts", [])
        if item.get("tier") == 3 and item.get("round", 0) > 3
    }
    if not overcap_verdicts:
        return [], []
    head = pull.get("head") if isinstance(pull, dict) else None
    base = pull.get("base") if isinstance(pull, dict) else None
    base_repository = base.get("repo") if isinstance(base, dict) else None
    if (not isinstance(head, dict) or not isinstance(base, dict)
            or not isinstance(base_repository, dict)
            or head.get("sha") != candidate["head_sha"]
            or base.get("sha") != candidate["target_base_sha"]
            or base_repository.get("id") != candidate["repository_id"]):
        _mismatch("OVERCAP_APPROVAL_HEAD_MISMATCH",
                  "live pull request head/base differs from the approval candidate")

    runs = {item.get("run_id"): item for item in bundle.get("runs", [])
            if isinstance(item, dict)}
    terminal = max(bundle["review_verdicts"], key=lambda item: item["round"])
    observation = terminal.get("posting_observation", {})
    observed_at = observation.get("observed_at")
    if not isinstance(observed_at, str):
        _mismatch("OVERCAP_APPROVAL_MISMATCH",
                  "terminal posting observation has no collection time")
    prefix = (
        f"{candidate['host']}/{candidate['repository']}/pull/"
        f"{candidate['pr_number']}"
    )
    approvals = []
    added_runs = []
    for listed in _overcap_comments(review_host, candidate):
        body = listed.get("body")
        try:
            parsed = parse_overcap_approval(body)
        except ValidationError:
            continue
        verdict = overcap_verdicts.get(parsed["round"])
        if verdict is None:
            continue
        comment_id = listed.get("id")
        if type(comment_id) is not int or comment_id <= 0:
            _mismatch("OVERCAP_APPROVAL_MISMATCH",
                      "canonical approval has no immutable comment id")
        comment = review_host.api(f"issues/comments/{comment_id}")
        expected_url = f"{prefix}#issuecomment-{comment_id}"
        user = comment.get("user") if isinstance(comment, dict) else None
        if (not isinstance(comment, dict) or comment.get("id") != comment_id
                or comment.get("html_url") != expected_url
                or not isinstance(user, dict) or type(user.get("id")) is not int
                or comment.get("created_at") != listed.get("created_at")
                or comment.get("updated_at") != listed.get("updated_at")
                or comment.get("body") != body):
            _mismatch("OVERCAP_APPROVAL_MISMATCH",
                      "listed approval differs from its immutable GitHub comment")
        round_run = runs.get(verdict.get("run_id"))
        if round_run is None or not isinstance(round_run.get("created_at"), str):
            _mismatch("OVERCAP_APPROVAL_MISMATCH",
                      "approval round has no registered critic-run start")
        collector_run, is_new = _overcap_collector_run(
            bundle, comment_id, observed_at
        )
        provider = {
            "id": comment_id,
            "html_url": expected_url,
            "user": {"id": user["id"]},
            "created_at": comment.get("created_at"),
            "updated_at": comment.get("updated_at"),
            "body": body,
            "head_sha": candidate["head_sha"],
        }
        provider_bytes = canonical(provider)
        approval = {
            "channel": "github_pr_comment",
            "source": "host_observation",
            "comment_id": comment_id,
            "comment_url": expected_url,
            "repository_id": candidate["repository_id"],
            "pr_number": candidate["pr_number"],
            "actor_id": user["id"],
            "created_at": comment.get("created_at"),
            "updated_at": comment.get("updated_at"),
            "raw_body": body,
            "observed_at": observed_at,
            "producer_id": COLLECTOR_ID,
            "run_id": collector_run["run_id"],
            "provider_kind": PROVIDER_KIND,
            "round_started_at": round_run["created_at"],
            "provider_response_bytes": {
                "encoding": "base64",
                "data": base64.b64encode(provider_bytes).decode("ascii"),
            },
            "provider_response_sha256": sha256(provider_bytes),
            "collector_receipt_sha256": "0" * 64,
        }
        binding_sha256 = sha256(canonical(approval))
        receipt = overcap_approval_collector_receipt_sha256(
            approval, binding_sha256, _gate_entry(registration)
        )
        approval["collector_receipt_sha256"] = receipt
        registration["receipts"][collector_run["run_id"]] = receipt
        approvals.append(approval)
        if is_new:
            added_runs.append(collector_run)
    return approvals, added_runs


def registry_from_review_host(review_host, bundle, *, release_sha256):
    """Re-observe every retained round and return the trusted gate registry."""
    candidate = bundle.get("candidate", {})
    runs = {item.get("run_id"): item for item in bundle.get("runs", [])
            if isinstance(item, dict)}
    receipts = bundle.get("review_round_receipts", [])
    merged = _registration(release_sha256, candidate.get("repository_id"))
    owner_review = bundle.get("owner_review")
    if owner_review is not None:
        _retained_owner_approval(owner_review)
    terminal_round = max((item.get("round", 0)
                          for item in bundle.get("review_verdicts", [])), default=0)
    for verdict in sorted(bundle.get("review_verdicts", []),
                          key=lambda item: item.get("round", 0)):
        observation = verdict.get("posting_observation")
        if not isinstance(observation, dict):
            _mismatch("POSTING_OBSERVATION_MISMATCH", "retained observation is missing")
        run = runs.get(observation.get("run_id"))
        if (run is None or run.get("role") != "collector"
                or run.get("producer_id") != COLLECTOR_ID
                or observation.get("producer_id") != COLLECTOR_ID
                or run.get("created_at") != observation.get("observed_at")):
            _mismatch("POSTING_COLLECTOR_RUN_MISMATCH",
                      "observation has no runtime-owned collector run at its recorded time")
        matching = [item for item in receipts
                    if item.get("critic_artifact_binding")
                    == verdict.get("critic_artifact_binding")]
        if len(matching) != 1:
            _mismatch("POSTING_ROUND_BINDING_MISMATCH",
                      "round has no unique retained receipt")
        collected = collect_posting_observation(
            review_host,
            candidate=candidate,
            verdict=verdict,
            review_round_receipt=matching[0],
            collector_run_id=observation["run_id"],
            observed_at=observation.get("observed_at"),
            release_sha256=release_sha256,
            owner_review=(owner_review if verdict.get("round") == terminal_round else None),
        )
        _retained_mismatch(collected["observation"], observation)
        if (owner_review is not None and verdict.get("round") == terminal_round
                and owner_review.get("posting_observation") != observation):
            _mismatch("OWNER_APPROVAL_MISMATCH",
                      "owner record does not carry the registered provider observation")
        merged["receipts"].update(collected["registration"]["receipts"])
    approval_pull = review_host.api(f"pulls/{candidate['pr_number']}")
    approvals, added_runs = _collect_overcap_approvals(
        review_host, bundle, approval_pull, merged
    )
    bundle["overcap_approvals"] = approvals
    bundle.setdefault("runs", []).extend(added_runs)
    if not merged["receipts"]:
        _mismatch("POSTING_OBSERVATION_MISMATCH", "no review verdict postings were observed")
    return {COLLECTOR_ID: _gate_entry(merged)}


def registry_from_host_config(root, host_config_path, bundle, release_sha256):
    """Create a registry through the enrolled, pinned GitHub review host."""
    host_config = load_config(host_config_path, root)
    candidate = bundle.get("candidate", {})
    if (host_config.get("repository_id") != candidate.get("repository_id")
            or host_config.get("repository") != candidate.get("repository")
            or host_config.get("pr") != candidate.get("pr_number")):
        _mismatch("POSTING_CANDIDATE_MISMATCH",
                  "review-host enrollment differs from the final-gate candidate")
    return registry_from_review_host(
        HostDriver(host_config, root), bundle, release_sha256=release_sha256
    )
