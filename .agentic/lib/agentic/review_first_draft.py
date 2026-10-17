"""Bounded first-draft publication for the native worker -> review-loop handoff.

The review loop deliberately starts at an existing PR.  This module owns only
the one-way bridge before that point: validate the worker receipt, publish the
exact tested tree, scan the complete unpublished history and PR body, and then
hand the observed PR to ``enroll``.  It contains no merge or Jira operation.
"""
from __future__ import annotations

import copy
from pathlib import Path
from hashlib import sha256
import re
import unicodedata

from . import ValidationError
from .gittree import candidate_tree, verify_publisher_tree
from .interaction import writer_commit_message
from .publication import scan_repository
from .provenance import (_validate_object_path, decode_git_paths, parse_writer_trailer,
                         validate_commit_message, validate_ref_locator,
                         read_commit_message_bytes, create_canonical_commit,
                         _FORMAT_CONTROLS)
from .review_loop import (complete_first_draft, confirm_first_draft_publication,
                          enroll, first_draft_failure,
                          record_first_draft_publication, require,
                          record_first_draft_worker_receipt,
                          reserve_first_draft)


_PATH_INVENTORY_LIMIT = 100000


def _bounded_text(value, label, maximum):
    require(isinstance(value, str), f"{label} must be text")
    try:
        raw = value.encode('utf-8', 'strict')
    except UnicodeError as exc:
        raise ValidationError(f"{label} must be strict UTF-8") from exc
    require(len(raw) <= maximum and '\ufeff' not in value
            and not any((ord(char) < 32 and char not in '\n\t')
                        or 0x7f <= ord(char) <= 0x9f
                        or ord(char) in _FORMAT_CONTROLS
                        or ord(char) in {0x2028, 0x2029}
                        for char in value),
            f"{label} is too large or contains forbidden text")
    return value


def _markdown_scalar(value, label):
    try:
        value = _bounded_text(value, label, 4096)
        require('\n' not in value and '\r' not in value and value.strip(),
                f"{label} must be one nonempty line")
    except (UnicodeError, ValidationError) as exc:
        raise ValidationError(f"BODY_SCALAR_INVALID: {label} is not canonical task data") from exc
    return (value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('`', '&#96;'))


def canonical_first_draft_title(value):
    """Return the one-line title admitted before any worker reservation."""
    try:
        value = validate_commit_message(value)
        require('\n' not in value and len(value.encode('utf-8')) <= 4096,
                'title must be one line within 4 KiB')
    except (UnicodeError, ValidationError) as exc:
        raise ValidationError(
            'TITLE_INVALID: first-draft title is not canonical: ' + str(exc)) from exc
    return value


def validate_first_draft_body_inputs(contract, *, risk_tier, worker_model,
                                     reasoning_effort, branch, tested_tree=None):
    """Validate static body fields before reservation and dynamic tree later."""
    try:
        require(isinstance(contract, str), "contract must be text")
        contract = contract.replace('\r\n', '\n')
        contract = _bounded_text(contract, "First-draft contract", 512 * 1024)
        require(contract.strip() and '\r' not in contract,
                "contract must be nonempty LF text")
    except (UnicodeError, ValidationError) as exc:
        raise ValidationError(
            'BODY_CONTRACT_INVALID: trusted first-draft contract is not canonical') from exc
    require(risk_tier in {1, 2, 3, "Tier 1", "Tier 2", "Tier 3"},
            "BODY_TIER_INVALID: First-draft PR requires a reviewed Tier 1, Tier 2 or Tier 3 risk tier")
    values = {
        "contract": contract,
        "risk_tier": risk_tier,
        "worker_model": _markdown_scalar(worker_model, "Worker model"),
        "reasoning_effort": _markdown_scalar(reasoning_effort, "Worker reasoning effort"),
        "branch": _markdown_scalar(branch, "First-draft branch"),
    }
    if tested_tree is not None:
        values["tested_tree"] = _markdown_scalar(
            tested_tree, "First-draft tested tree")
    return values


def render_first_draft_body(contract, *, risk_tier, worker_model, reasoning_effort,
                            branch, tested_tree):
    """Render the immutable contract facts recorded in the draft PR body."""
    values = validate_first_draft_body_inputs(
        contract, risk_tier=risk_tier, worker_model=worker_model,
        reasoning_effort=reasoning_effort, branch=branch,
        tested_tree=tested_tree)
    contract = values["contract"]
    risk_tier = values["risk_tier"]
    worker_model = values["worker_model"]
    reasoning_effort = values["reasoning_effort"]
    branch = values["branch"]
    tested_tree = values["tested_tree"]
    rendered = ("## AWF first draft\n\n"
            f"- Risk tier: {risk_tier}\n"
            f"- Worker model: `{worker_model}`\n"
            f"- Reasoning effort: `{reasoning_effort}`\n"
            f"- Head branch: `{branch}`\n"
            f"- Tested tree: `{tested_tree}`\n\n"
            "The first-draft worker run is charged to `max_agent_runs`; amendment "
            "cycles start at zero. The scheduled review loop enrolls this draft "
            "after its observed creation.\n\n" + contract.rstrip('\n') + "\n")
    _bounded_text(rendered, "First-draft body", 1024 * 1024)
    return rendered


def validate_worker_receipt(receipt, *, allowed_paths):
    """Validate the small, host-authenticated receipt returned by a worker."""
    require(isinstance(receipt, dict), "First-draft worker receipt must be an object")
    require(set(receipt) == {"outcome", "changes", "tested_tree", "ignored_untracked", "summary"},
            "First-draft worker receipt has unsupported or missing fields")
    require(receipt["outcome"] == "CHANGED", "First-draft worker did not produce a changed tree")
    require(isinstance(receipt["summary"], str) and receipt["summary"].strip()
            and len(receipt["summary"].encode('utf-8', 'strict')) <= 4096,
            "First-draft worker summary is empty")
    require(isinstance(receipt["tested_tree"], str)
            and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', receipt["tested_tree"]),
            "First-draft receipt has no valid tested_tree")
    changes = receipt["changes"]
    require(isinstance(changes, list) and changes, "First-draft receipt has no declared changes")
    paths = []
    for change in changes:
        require(isinstance(change, dict) and set(change) == {"path", "action"},
                "First-draft changes must contain path and action")
        require(change["action"] in {"added", "modified", "deleted"},
                "First-draft change has an unsupported action")
        paths.append(change["path"])
    ignored = receipt["ignored_untracked"]
    require(isinstance(ignored, list),
            "First-draft receipt must retain the excluded ignored_untracked inventory")
    _validate_receipt_path_inventories(paths, ignored)
    require(all(path in allowed_paths for path in paths),
            "First-draft worker changed a path outside its exact scope")
    return copy.deepcopy(receipt)


def _validate_receipt_path_inventories(*inventories):
    """Apply one canonical validator and aggregate cap to receipt paths."""
    try:
        require(sum(len(inventory) for inventory in inventories) <= _PATH_INVENTORY_LIMIT,
                "receipt path inventories exceed 100000 records")
        normalized = set()
        for inventory in inventories:
            for path in inventory:
                _validate_object_path(path)
                canonical = unicodedata.normalize('NFC', path)
                require(canonical not in normalized,
                        "receipt path inventories contain an NFC alias")
                normalized.add(canonical)
    except (UnicodeError, ValidationError) as exc:
        raise ValidationError(f"WORKER_RECEIPT_INVALID: {exc}") from exc


def publication_scan(root, base, head, body, *, mapping_path=None):
    """Run the history-aware scan bound to the exact prospective PR body."""
    result = scan_repository(root, base, head, pr_body_texts=[body], mapping_path=mapping_path)
    require(result["status"] == "PASS", "Publication scan blocked first push")
    return result


def _decode_git_path_inventories(*values):
    """Decode all Git inventories through one aggregate-bounded validator."""
    try:
        raw_values = []
        for value in values:
            require(isinstance(value, (bytes, str)),
                    "Git path inventory must be bytes or text")
            raw = value.encode('utf-8', 'strict') if isinstance(value, str) else value
            require(not raw or raw.endswith(b'\0'),
                    "Git path inventory lacks terminal NUL")
            raw_values.append(raw)
        combined = b''.join(raw_values)
        return set(decode_git_paths(combined, require_terminal_nul=bool(combined)))
    except (UnicodeError, ValidationError) as exc:
        raise ValidationError(f"PUBLISHER_PATH_INVALID: {exc}") from exc


def excluded_worktree_inventory(git, changes):
    """Return every untracked or Git-ignored path excluded from the tested tree."""
    declared_additions = {
        change["path"] for change in copy.deepcopy(changes)
        if change["action"] == "added"
    }
    paths = _decode_git_path_inventories(
        git.run("ls-files", "--others", "--exclude-standard", "-z"),
        git.run("ls-files", "--others", "--ignored", "--exclude-standard", "-z"))
    return sorted(paths - declared_additions)


def publish_codex_tested_tree(root, base, branch, receipt, *, body,
                              commit_message, codex_run_id, git,
                              allowed_paths, mapping_path=None, title=None,
                              prepare_publication=None, prepare_plan=None,
                              publication_plan=None, git_guard=None,
                              provenance_check=None):
    """Publish a Codex-written first draft with its required Writer trailer."""
    return publish_tested_tree(
        root, base, branch, receipt,
        body=body,
        commit_message=commit_message,
        codex_run_id=codex_run_id,
        git=git,
        allowed_paths=allowed_paths,
        mapping_path=mapping_path,
        title=title,
        prepare_publication=prepare_publication,
        prepare_plan=prepare_plan,
        publication_plan=publication_plan,
        git_guard=git_guard,
        provenance_check=provenance_check,
    )


def publish_tested_tree(root, base, branch, receipt, *, body, commit_message,
                        codex_run_id=None,
                        git, allowed_paths, mapping_path=None, title=None,
                        prepare_publication=None, prepare_plan=None,
                        publication_plan=None, git_guard=None,
                        provenance_check=None):
    """Commit/push exactly a worker-tested tree, then return a publication receipt.

    ``git`` is a narrow host adapter with ``run(*args)``.  It is intentionally
    injected so tests can prove scan denial and tree mismatch without a remote.
    """
    # This is the commit boundary, not merely a convenience wrapper.  Format
    # the message here so every caller must supply the authenticated Codex
    # execution identity before any Git mutation can occur.
    plain_commit_message = commit_message
    commit_message = writer_commit_message(commit_message,
                                           codex_run_id=codex_run_id)
    normalized_plain = validate_commit_message(plain_commit_message)
    commit_title = normalized_plain.split('\n')[0]
    require(len(commit_title.encode('utf-8')) <= 4096,
            "First-draft commit subject exceeds 4 KiB")
    if title is not None:
        title = canonical_first_draft_title(title)
    _bounded_text(body, "First-draft body", 1024 * 1024)
    require('\r' not in body and body.endswith('\n') and not body.endswith('\n\n'),
            "First-draft body must use LF and exactly one trailing LF")
    validate_ref_locator(base, "First-draft base")
    require(isinstance(branch, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch)
            and '..' not in branch and not branch.endswith(('.', '.lock')),
            "First-draft branch is unsafe")
    receipt = validate_worker_receipt(receipt, allowed_paths=set(allowed_paths))
    if git_guard:
        git_guard()
    require(git.run("symbolic-ref", "--short", "HEAD") == branch,
            "First-draft worker changed the publication branch")
    starting_head = git.run("rev-parse", "HEAD")
    expected = candidate_tree(root, base, receipt["changes"])
    require(expected.tested_tree == receipt["tested_tree"],
            "Worker tested_tree does not match the declared worktree changes")
    excluded = excluded_worktree_inventory(git, receipt["changes"])
    require(sorted(receipt["ignored_untracked"]) == excluded,
            "Worker ignored_untracked inventory does not match the candidate tree")
    plan = {"base": base, "head_tree": receipt["tested_tree"], "branch": branch,
            "body_sha256": sha256(body.encode()).hexdigest(),
            "title": title or commit_title, "commit_message": commit_message,
            "ignored_untracked": excluded}
    require(publication_plan is None or publication_plan == plan,
            "First-draft publication plan changed during recovery")
    if git_guard:
        git_guard()
    if starting_head == base:
        if prepare_plan:
            prepare_plan(plan)
        require(git.run("symbolic-ref", "--short", "HEAD") == branch
                and git.run("rev-parse", "HEAD") == base,
                "First-draft HEAD or branch changed before publisher staging")
        git.run("add", "--", *[x["path"] for x in receipt["changes"]])
        if git_guard:
            git_guard()
        require(git.run("symbolic-ref", "--short", "HEAD") == branch
                and git.run("rev-parse", "HEAD") == base,
                "First-draft HEAD or branch changed before publisher commit")
        create_canonical_commit(root, base, branch, commit_message)
        if git_guard:
            git_guard()
    else:
        # A failed durable-publication write can leave the exact unpublished
        # publisher commit locally.  Reconstruct only that one-child identity;
        # never replay the worker or create another commit around unknown HEAD.
        require(publication_plan == plan,
                "First-draft worker changed HEAD from the provider base")
        parents = git.run("rev-list", "--parents", "-n", "1", starting_head).split()
        require(parents == [starting_head, base],
                "First-draft worker changed HEAD from the provider base")
        require(git.run("show", "-s", "--format=%B", starting_head) == commit_message,
                "Retained first-draft publisher commit title changed during recovery")
    actual = verify_publisher_tree(root, receipt["tested_tree"])
    head = git.run("rev-parse", "HEAD")
    committed_message = read_commit_message_bytes(root, head)
    require(committed_message == commit_message.encode('utf-8'),
            "Created commit message bytes differ from the canonical emitter output")
    parse_writer_trailer(committed_message.decode('utf-8', 'strict'))
    parents = git.run("rev-list", "--parents", "-n", "1", head).split()
    require(len(parents) == 2 and parents == [head, base],
            "First-draft publisher commit is not a single child of the provider base")
    require(git.run("symbolic-ref", "--short", "HEAD") == branch,
            "First-draft publication branch changed during publisher commit")
    publication = {"base": base, "head": head, "head_tree": actual,
                   "branch": branch, "body": body,
                   "body_sha256": sha256(body.encode()).hexdigest(),
                   "title": title or commit_title,
                   "ignored_untracked": excluded}
    if provenance_check is not None:
        provenance_check(base, head)
    if prepare_publication:
        prepare_publication(publication)
    scan = publication_scan(root, base, head, body, mapping_path=mapping_path)
    publication["publication_scan"] = scan
    if git_guard:
        git_guard()
    git.run("push", "origin", f"{head}:refs/heads/{branch}")
    return publication


def republish_prepared_tree(root, publication, *, git, mapping_path=None, git_guard=None):
    """Retry only the exact frozen push after provider observation proved its absence."""
    require(isinstance(publication, dict), 'Prepared first-draft publication is missing')
    if git_guard:
        git_guard()
    require(git.run('rev-parse', 'HEAD') == publication.get('head'),
            'Local HEAD does not match the prepared first-draft publication')
    require(git.run('symbolic-ref', '--short', 'HEAD') == publication.get('branch'),
            'Local branch does not match the prepared first-draft publication')
    require(verify_publisher_tree(root, publication.get('head_tree')) == publication.get('head_tree'),
            'Local tree does not match the prepared first-draft publication')
    require(sha256(publication.get('body', '').encode()).hexdigest()
            == publication.get('body_sha256'),
            'Prepared first-draft body digest mismatch')
    publication_scan(root, publication['base'], publication['head'], publication['body'],
                     mapping_path=mapping_path)
    if git_guard:
        git_guard()
    git.run('push', 'origin', f"{publication['head']}:refs/heads/{publication['branch']}")
    return publication


def enroll_created_pr(store, config, snapshot, *, first_draft_run=True):
    """Enroll the observed draft PR and charge the first-draft run once."""
    if first_draft_run:
        repository_id = config.get('repository_id', str(config['key']).split(':', 1)[0])
        reserved = store.db.execute('SELECT 1 FROM prs WHERE key=?', (f'{repository_id}:0',)).fetchone()
        if reserved:
            return complete_first_draft(store, config, snapshot)
        # Compatibility for an operator-adopted PR: there is no pre-existing
        # reservation to rekey, so retain the historical enrollment behavior.
        return enroll(store, config, snapshot, first_draft_run=True)
    return enroll(store, config, snapshot, first_draft_run=False)


def run_first_draft(store, config, *, worker, publisher, observe_pr, republisher=None,
                    git_controls=None):
    """Run the native handoff and enroll only the PR identity observed afterward.

    The callbacks are host adapters: ``worker`` returns a receipt, ``publisher``
    must perform the exact-tree/scan/push sequence, and ``observe_pr`` reads the
    provider after creation. Keeping those operations separate prevents a
    guessed PR number or a publication result from becoming loop authority.
    """
    reservation = reserve_first_draft(store, config, git_controls=git_controls)
    try:
        publication = reservation.get('first_draft_publication')
        if publication is None:
            receipt = reservation.get('first_draft_worker_receipt')
            if receipt is None:
                receipt = worker(reservation['inflight']['id'])
                receipt = validate_worker_receipt(
                    receipt, allowed_paths=set(config['allowed_paths']))
                record_first_draft_worker_receipt(store, config, receipt)
            else:
                receipt = validate_worker_receipt(
                    receipt, allowed_paths=set(config['allowed_paths']))
            publication = publisher(receipt)
            require(isinstance(publication, dict) and publication.get('head'),
                    'First-draft publisher returned no observed head')
            record_first_draft_publication(store, config, publication)
            confirm_first_draft_publication(store, config, publication)
        elif reservation.get('first_draft_publication_status') == 'RETRY_PUSH':
            require(republisher is not None,
                    'Prepared first-draft push retry has no exact-tree publisher')
            publication = republisher(publication)
            confirm_first_draft_publication(store, config, publication)
        else:
            require(reservation.get('first_draft_publication_status') == 'PUSHED',
                    'Prepared first-draft publication must be reconciled before retry')
        snapshot = observe_pr(publication)
        require(isinstance(snapshot, dict) and snapshot.get('head') == publication['head'],
                'Created PR observation does not match the published head')
        require(snapshot.get('base') == publication['base'],
                'Created PR base does not match the publication base; enrollment remains paused')
        return enroll_created_pr(store, config, snapshot, first_draft_run=True)
    except Exception as exc:
        first_draft_failure(store, config, f'First-draft attempt requires reconciliation: {type(exc).__name__}: {exc}')
        raise
