"""Bounded first-draft publication for the native worker -> review-loop handoff.

The review loop deliberately starts at an existing PR.  This module owns only
the one-way bridge before that point: validate the worker receipt, publish the
exact tested tree, scan the complete unpublished history and PR body, and then
hand the observed PR to ``enroll``.  It contains no merge or Jira operation.
"""
from __future__ import annotations

from pathlib import Path
from hashlib import sha256

from . import ValidationError
from .gittree import candidate_tree, verify_publisher_tree
from .publication import scan_repository
from .review_loop import (complete_first_draft, enroll, first_draft_failure,
                          record_first_draft_publication, require,
                          reserve_first_draft)


def render_first_draft_body(contract, *, risk_tier, worker_model, reasoning_effort,
                            branch, tested_tree):
    """Render the immutable contract facts recorded in the draft PR body."""
    require(isinstance(contract, str) and contract.strip(), "First-draft contract is empty")
    require(risk_tier in {1, 2, 3, "Tier 1", "Tier 2", "Tier 3"},
            "First-draft PR requires a reviewed Tier 1, Tier 2 or Tier 3 risk tier")
    require(isinstance(worker_model, str) and worker_model.strip(), "Worker model is missing")
    require(isinstance(reasoning_effort, str) and reasoning_effort.strip(), "Worker reasoning effort is missing")
    require(isinstance(branch, str) and branch.strip() and isinstance(tested_tree, str),
            "First-draft publication identity is incomplete")
    return ("## AWF first draft\n\n"
            f"- Risk tier: {risk_tier}\n"
            f"- Worker model: `{worker_model}`\n"
            f"- Reasoning effort: `{reasoning_effort}`\n"
            f"- Head branch: `{branch}`\n"
            f"- Tested tree: `{tested_tree}`\n\n"
            "The first-draft worker run is charged to `max_agent_runs`; amendment "
            "cycles start at zero. The scheduled review loop enrolls this draft "
            "after its observed creation.\n\n" + contract.strip() + "\n")


def validate_worker_receipt(receipt, *, allowed_paths):
    """Validate the small, host-authenticated receipt returned by a worker."""
    require(isinstance(receipt, dict), "First-draft worker receipt must be an object")
    require(set(receipt) == {"outcome", "changes", "tested_tree", "ignored_untracked", "summary"},
            "First-draft worker receipt has unsupported or missing fields")
    require(receipt["outcome"] == "CHANGED", "First-draft worker did not produce a changed tree")
    require(isinstance(receipt["summary"], str) and receipt["summary"].strip(),
            "First-draft worker summary is empty")
    require(isinstance(receipt["tested_tree"], str) and len(receipt["tested_tree"]) in {40, 64},
            "First-draft receipt has no valid tested_tree")
    changes = receipt["changes"]
    require(isinstance(changes, list) and changes, "First-draft receipt has no declared changes")
    paths = []
    for change in changes:
        require(isinstance(change, dict) and set(change) == {"path", "action"},
                "First-draft changes must contain path and action")
        require(isinstance(change["path"], str) and change["path"] in allowed_paths,
                "First-draft worker changed a path outside its exact scope")
        require(change["action"] in {"added", "modified", "deleted"},
                "First-draft change has an unsupported action")
        paths.append(change["path"])
    require(len(paths) == len(set(paths)), "First-draft changes contain duplicate paths")
    ignored = receipt["ignored_untracked"]
    require(isinstance(ignored, list) and len(ignored) == len(set(ignored)),
            "First-draft receipt must retain the excluded ignored_untracked inventory")
    require(all(isinstance(path, str) and path and path not in paths for path in ignored),
            "ignored_untracked must contain distinct paths excluded from the published tree")
    return receipt


def publication_scan(root, base, head, body, *, mapping_path=None):
    """Run the history-aware scan bound to the exact prospective PR body."""
    result = scan_repository(root, base, head, pr_body_texts=[body], mapping_path=mapping_path)
    require(result["status"] == "PASS", "Publication scan blocked first push")
    return result


def publish_tested_tree(root, base, branch, receipt, *, body, commit_message,
                        git, allowed_paths, mapping_path=None):
    """Commit/push exactly a worker-tested tree, then return a publication receipt.

    ``git`` is a narrow host adapter with ``run(*args)``.  It is intentionally
    injected so tests can prove scan denial and tree mismatch without a remote.
    """
    receipt = validate_worker_receipt(receipt, allowed_paths=set(allowed_paths))
    expected = candidate_tree(root, base, receipt["changes"])
    require(sorted(receipt["ignored_untracked"]) == list(expected.ignored_untracked),
            "Worker ignored_untracked inventory does not match the candidate tree")
    require(expected.tested_tree == receipt["tested_tree"],
            "Worker tested_tree does not match the declared worktree changes")
    git.run("add", "--", *[x["path"] for x in receipt["changes"]])
    git.run("commit", "-m", commit_message)
    actual = verify_publisher_tree(root, receipt["tested_tree"])
    head = git.run("rev-parse", "HEAD")
    scan = publication_scan(root, base, head, body, mapping_path=mapping_path)
    git.run("push", "origin", f"{head}:refs/heads/{branch}")
    return {"base": base, "head": head, "head_tree": actual,
            "branch": branch, "body_sha256": sha256(body.encode()).hexdigest(),
            "publication_scan": scan}


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


def run_first_draft(store, config, *, worker, publisher, observe_pr):
    """Run the native handoff and enroll only the PR identity observed afterward.

    The callbacks are host adapters: ``worker`` returns a receipt, ``publisher``
    must perform the exact-tree/scan/push sequence, and ``observe_pr`` reads the
    provider after creation. Keeping those operations separate prevents a
    guessed PR number or a publication result from becoming loop authority.
    """
    reservation = reserve_first_draft(store, config)
    try:
        publication = reservation.get('first_draft_publication')
        if publication is None:
            receipt = worker(reservation['inflight']['id'])
            validate_worker_receipt(receipt, allowed_paths=set(config['allowed_paths']))
            publication = publisher(receipt)
            require(isinstance(publication, dict) and publication.get('head'),
                    'First-draft publisher returned no observed head')
            record_first_draft_publication(store, config, publication)
        snapshot = observe_pr(publication)
        require(isinstance(snapshot, dict) and snapshot.get('head') == publication['head'],
                'Created PR observation does not match the published head')
        require(snapshot.get('base') == publication['base'],
                'Created PR base does not match the publication base; enrollment remains paused')
        return enroll_created_pr(store, config, snapshot, first_draft_run=True)
    except Exception as exc:
        first_draft_failure(store, config, f'First-draft attempt requires reconciliation: {type(exc).__name__}: {exc}')
        raise
