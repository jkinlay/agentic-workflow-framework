"""Trusted review-verdict posting observation for the production final gate.

Configuration may narrow ``COLLECTOR_ID`` but cannot create this registry.
Membership is produced only after this installed module re-reads the comment
and pull-request body through the enrolled review-host provider.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re

from . import ValidationError
from .canonical import canonical, sha256
from .gates import posting_collector_receipt_sha256
from .providers.github_review_host import HostDriver, load_config


COLLECTOR_ID = "github-production-posting-collector"
PROVIDER_KIND = "github"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
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
_HTML_OPEN_TAG = re.compile(
    rf"^<(?P<tag>{_HTML_NAME})(?:[ \t]+[^<>]*)?>[ \t]*$",
    re.IGNORECASE,
)
_HTML_CLOSE_TAG = re.compile(
    rf"^</(?P<tag>{_HTML_NAME})[ \t]*>$",
    re.IGNORECASE,
)
_HTML_ATTRIBUTES = re.compile(
    rf"(?:[ \t]+{_HTML_NAME}(?:[ \t]*=[ \t]*"
    r"(?:\"[^\"<>]*\"|'[^'<>]*'|[^\s\"'=<>`]+))?)*[ \t]*$"
)
_HTML_ATTRIBUTE = re.compile(
    rf"[ \t]+(?P<name>{_HTML_NAME})(?:[ \t]*=[ \t]*"
    r"(?:\"(?P<double>[^\"<>]*)\"|'(?P<single>[^'<>]*)'|"
    r"(?P<bare>[^\s\"'=<>`]+)))?"
)
_NON_RENDERING_HTML = {
    "code", "pre", "script", "style", "template", "textarea", "title", "xmp",
}
_RENDERING_HTML = {
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo",
    "blockquote", "button", "caption", "cite", "data", "dd", "del", "details",
    "dfn", "dialog", "div", "dl", "dt", "em", "fieldset", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "i", "ins", "kbd", "label", "legend", "li", "main",
    "mark", "menu", "nav", "ol", "p", "q", "s", "samp", "section", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "time", "tr", "u", "ul", "var",
}


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


def _provider_response(comment_id, comment_url, comment_body, pr_body):
    # Retain only the provider fields used by the gate.  This deliberately
    # excludes actor profiles, headers and other unrelated provider data.
    return canonical({
        "comment_id": comment_id,
        "comment_url": comment_url,
        "comment_body": comment_body,
        "pr_body": pr_body,
    })


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


def _visible_markdown(body):
    """Return definitely visible lines, or ``None`` when syntax is ambiguous."""
    visible = []
    fence = None
    in_comment = False
    for line in body.splitlines():
        if fence is not None:
            if _fence_close(line, fence):
                fence = None
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
                continue
        line, in_comment, certain = _strip_html_comments(line, in_comment)
        if not certain:
            return None
        visible.append(line)
    if fence is not None:
        return None
    # An unclosed HTML comment intentionally hides the rest of the document.
    return _without_code_spans("\n".join(visible))


def _heading_fragment(heading):
    # The required fragment is ASCII.  GitHub heading fragments lowercase the
    # text, discard punctuation, retain hyphens/underscores and map whitespace
    # to hyphens.
    if any(character in heading for character in "<>&\\"):
        return None
    plain = heading.casefold()
    plain = re.sub(r"[^a-z0-9_\- \t]", "", plain)
    return re.sub(r"[ \t]", "-", plain.strip())


def _html_fragment(line, fragment):
    """Recognise only a complete, ordinary rendering element on one line."""
    match = _HTML_PAIRED_TAG.fullmatch(line)
    if match is None:
        match = _HTML_SELF_CLOSING_TAG.fullmatch(line)
    if match is None:
        return None
    tag = match.group("tag").casefold()
    attributes = match.group("attrs")
    if tag in _NON_RENDERING_HTML or tag not in _RENDERING_HTML:
        return False
    if _HTML_ATTRIBUTES.fullmatch(attributes) is None:
        return None
    values = {}
    for attribute in _HTML_ATTRIBUTE.finditer(attributes):
        name = attribute.group("name").casefold()
        if name in values:
            return None
        value = next(
            (item for item in (
                attribute.group("double"), attribute.group("single"),
                attribute.group("bare"),
            ) if item is not None),
            None,
        )
        values[name] = value
    return (values.get("id") == fragment
            or (tag == "a" and values.get("name") == fragment))


def _defines_fragment(body, fragment):
    visible = _visible_markdown(body)
    if visible is None:
        return False
    found = False
    raw_html_tag = None
    for line in visible.splitlines():
        if raw_html_tag is not None:
            closing = _HTML_CLOSE_TAG.fullmatch(line)
            if fragment in line:
                return False
            if closing is not None and closing.group("tag").casefold() == raw_html_tag:
                raw_html_tag = None
            continue
        heading = _MARKDOWN_HEADING.match(line)
        if heading is not None:
            rendered = _heading_fragment(heading.group(1))
            if rendered is None:
                return False
            if rendered == fragment:
                found = True
            continue
        html = _html_fragment(line, fragment)
        if html is True:
            found = True
            continue
        if fragment in line:
            return False
        opening = _HTML_OPEN_TAG.fullmatch(line)
        if opening is not None:
            raw_html_tag = opening.group("tag").casefold()
    if raw_html_tag is not None:
        return False
    return found


def collect_posting_observation(review_host, *, candidate, verdict,
                                review_round_receipt, collector_run_id,
                                observed_at, release_sha256):
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

    provider_response = _provider_response(
        comment_id, comment["html_url"], comment["body"], body
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


def registry_from_review_host(review_host, bundle, *, release_sha256):
    """Re-observe every retained round and return the trusted gate registry."""
    candidate = bundle.get("candidate", {})
    runs = {item.get("run_id"): item for item in bundle.get("runs", [])
            if isinstance(item, dict)}
    receipts = bundle.get("review_round_receipts", [])
    merged = _registration(release_sha256, candidate.get("repository_id"))
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
        )
        _retained_mismatch(collected["observation"], observation)
        merged["receipts"].update(collected["registration"]["receipts"])
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
