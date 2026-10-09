"""Durable owner-push handoff and draft-PR resumption through reviewed adapters.

The host authenticates the owner-publication policy and enforces ordinary
publication/deny scans and provider authority. This store retains completed
work; it cannot launch a worker, push a branch, or authorize a provider action.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import uuid

from . import ValidationError
from .canonical import canonical, fingerprint, fresh, loads, sha256, timestamp
from .controller_state import configure_database, protected_state_path, restrict_state_permissions
from .provider_identity import github_identity_preflight
from .publication_readiness import _eligible_branch
from .providers.github import branch_name, repository_name


APPLICATION_ID = 0x41574650
MAX_COMMAND = 8192
MAX_REQUEST = 1024 * 1024
DDL = """
CREATE TABLE IF NOT EXISTS owner_publication_batch(
 batch_id TEXT PRIMARY KEY,request_json TEXT NOT NULL,request_sha256 TEXT NOT NULL,
 config_sha256 TEXT NOT NULL,command TEXT NOT NULL,prepared_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner_publication_pr(
 batch_id TEXT NOT NULL,stream TEXT NOT NULL,operation_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL,started_at TEXT,remote_json TEXT,receipt_json TEXT,
 PRIMARY KEY(batch_id,stream));
"""


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _literal(value):
    _require(isinstance(value, str) and value and not any(ord(char) < 32 for char in value),
             "Owner command arguments must be nonempty single-line literals")
    return "'" + value.replace("'", "''") + "'"


def _request(config, request):
    _require(isinstance(request, dict) and set(request) == {"format", "owner_policy_evidence", "worktree", "streams"}
             and request["format"] == "awf-owner-publication-1", "Malformed owner-publication handoff")
    evidence = request["owner_policy_evidence"]
    _require(isinstance(evidence, str) and evidence.startswith("urn:") and len(evidence) <= 2048,
             "Explicit host-authenticated owner-publication policy evidence is required")
    worktree = Path(request["worktree"])
    _require(worktree.is_absolute() and worktree.is_dir(), "Owner publication requires an absolute existing worktree")
    github = config.get("github", {})
    _require(github.get("host") == "https://github.com" and type(github.get("repository_id")) is int
             and github["repository_id"] > 0, "Owner publication needs a pinned GitHub repository")
    repository_name(github.get("repository"))
    branch_name(github.get("base_branch"))
    streams = request["streams"]
    _require(isinstance(streams, list) and 1 <= len(streams) <= 6, "Handoff needs one to six completed streams")
    seen_streams, seen_branches, seen_tickets = set(), set(), set()
    for item in streams:
        _require(isinstance(item, dict) and set(item) == {
            "stream", "ticket", "slug", "branch", "head", "title", "body", "completed_result_sha256", "stream_state"},
            "Malformed completed-stream handoff")
        _require(item["stream"] in tuple("ABCDEF") and item["stream"] not in seen_streams,
                 "Stream identities must be unique")
        _eligible_branch(github, item["ticket"], item["slug"], item["branch"])
        _require(item["branch"] not in seen_branches and item["ticket"] not in seen_tickets,
                 "Publication branches and tickets must be unique")
        _require(isinstance(item["head"], str) and re.fullmatch(r"[0-9a-f]{40}", item["head"]),
                 "Completed work needs an exact commit SHA")
        _require(isinstance(item["completed_result_sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", item["completed_result_sha256"]),
                 "Completed work needs a retained worker-result digest")
        _require(isinstance(item["stream_state"], dict) and item["stream_state"],
                 "Retain the complete controller stream/lifecycle snapshot")
        _require(isinstance(item["title"], str) and 0 < len(item["title"]) <= 256
                 and not any(ord(char) < 32 for char in item["title"]), "Draft title must be bounded single-line text")
        body = item["body"]
        _require(isinstance(body, str) and 0 < len(body.encode("utf-8")) <= 128 * 1024
                 and not body.startswith("\ufeff") and "\r" not in body
                 and body.endswith("\n") and not body.endswith("\n\n"),
                 "Draft body must be bounded canonical UTF-8 with exactly one final LF")
        seen_streams.add(item["stream"])
        seen_branches.add(item["branch"])
        seen_tickets.add(item["ticket"])
    _require(len(canonical(request)) <= MAX_REQUEST, "Owner-publication handoff exceeds its byte bound")
    return loads(canonical(request).decode())


def _scan_binding(value, *, kind, repository_id, base_sha, head_sha, tree_sha,
                  body_sha256, comments_sha256):
    required = {"format", "kind", "receipt_sha256", "result", "scan_complete",
                "repository_id", "base_sha", "head_sha", "tree_sha", "body_sha256",
                "comments_sha256", "blocking_findings", "unscanned", "mapping_loaded"}
    _require(isinstance(value, dict) and set(value) == required,
             "Malformed owner-publication scan binding")
    _require(value["format"] == "awf-owner-publication-scan-binding-1" and value["kind"] == kind,
             "Owner-publication scan binding has the wrong kind")
    _require(isinstance(value["receipt_sha256"], str)
             and re.fullmatch(r"[0-9a-f]{64}", value["receipt_sha256"]),
             "Owner-publication scan binding needs a receipt digest")
    _require(value["result"] == "PASS" and value["scan_complete"] is True
             and type(value["blocking_findings"]) is int and value["blocking_findings"] == 0
             and type(value["unscanned"]) is int and value["unscanned"] == 0,
             "Owner-publication scans must be complete terminal PASS results")
    expected = {"repository_id": repository_id, "base_sha": base_sha, "head_sha": head_sha,
                "tree_sha": tree_sha, "body_sha256": body_sha256,
                "comments_sha256": comments_sha256}
    _require(all(value[name] == expected[name] and type(value[name]) is type(expected[name])
                 for name in expected),
             "Owner-publication scan binding differs from the authorized candidate")
    _require(type(value["mapping_loaded"]) is bool
             and (kind != "private_deny" or value["mapping_loaded"] is True),
             "Private-deny scan binding requires its mapping")


def _authorization(config, request, authorization, *, now):
    required = {"format", "source", "observed_at", "repository", "repository_id", "base_branch",
                "policy_evidence", "policy_authorized", "comments", "streams"}
    _require(isinstance(authorization, dict) and set(authorization) == required
             and authorization["format"] == "awf-owner-publication-authorization-1"
             and authorization["source"] == "reviewed_host_adapter",
             "Malformed reviewed-host owner-publication authorization")
    github = config["github"]
    _require(authorization["repository"] == github["repository"]
             and type(authorization["repository_id"]) is int
             and authorization["repository_id"] == github["repository_id"]
             and authorization["base_branch"] == github["base_branch"],
             "Owner-publication authorization has a foreign repository identity")
    fresh(authorization["observed_at"], now, 300, skew_seconds=0)
    _require(authorization["policy_authorized"] is True
             and authorization["policy_evidence"] == request["owner_policy_evidence"],
             "Reviewed host did not authorize the exact owner-publication policy evidence")
    comments = authorization["comments"]
    _require(isinstance(comments, dict)
             and set(comments) == {"source", "scope", "complete", "count", "sha256"}
             and comments["source"] == "host_observation"
             and comments["scope"] == "pull_request_comments"
             and comments["complete"] is True
             and type(comments["count"]) is int and comments["count"] >= 0
             and isinstance(comments["sha256"], str)
             and re.fullmatch(r"[0-9a-f]{64}", comments["sha256"]),
             "Owner-publication needs a complete host comment observation")
    streams = authorization["streams"]
    _require(isinstance(streams, list) and len(streams) == len(request["streams"]),
             "Owner-publication authorization must cover every stream")
    by_stream = {item["stream"]: item for item in request["streams"]}
    seen = set()
    stream_fields = {"stream", "branch", "base_sha", "head_sha", "tree_sha", "body_sha256",
                     "completed_result_sha256", "publication_scan", "private_deny_scan"}
    for item in streams:
        _require(isinstance(item, dict) and set(item) == stream_fields
                 and item["stream"] in by_stream and item["stream"] not in seen,
                 "Malformed or duplicate owner-publication stream authorization")
        expected = by_stream[item["stream"]]
        _require(item["branch"] == expected["branch"] and item["head_sha"] == expected["head"]
                 and item["completed_result_sha256"] == expected["completed_result_sha256"]
                 and item["body_sha256"] == sha256(expected["body"].encode("utf-8")),
                 "Owner-publication authorization differs from retained stream work")
        _require(all(isinstance(item[name], str) and re.fullmatch(r"[0-9a-f]{40}", item[name])
                     for name in ("base_sha", "head_sha", "tree_sha")),
                 "Owner-publication authorization needs an exact base/head/tree tuple")
        for kind, field in (("publication", "publication_scan"), ("private_deny", "private_deny_scan")):
            _scan_binding(item[field], kind=kind, repository_id=authorization["repository_id"],
                          base_sha=item["base_sha"], head_sha=item["head_sha"], tree_sha=item["tree_sha"],
                          body_sha256=item["body_sha256"], comments_sha256=comments["sha256"])
        seen.add(item["stream"])
    _require(seen == set(by_stream), "Owner-publication authorization omitted a stream")
    return loads(canonical(authorization).decode())


def prepare_owner_publication(store, config, request, *, now, authorize_owner_publication):
    """Authorize through one digest-pinned reviewed adapter before rendering a push command."""
    _require(callable(authorize_owner_publication),
             "Owner publication requires a reviewed host authorization adapter")
    normalized = _request(config, request)
    authorization = authorize_owner_publication(loads(canonical(normalized).decode()))
    return store.prepare(config, normalized, authorization, now=now)


class OwnerPublicationStore:
    def __init__(self, path, *, worktree_roots):
        _require(bool(worktree_roots), "Owner-publication state must name protected worktree exclusions")
        self.worktree_roots = tuple(Path(root).resolve() for root in worktree_roots)
        self.path = protected_state_path(path, self.worktree_roots)
        with self.connection() as db:
            db.executescript(DDL)
        restrict_state_permissions(self.path)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            configure_database(db, self.path, APPLICATION_ID)
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def prepare(self, config, request, authorization, *, now):
        timestamp(now)
        request = _request(config, request)
        authorization = _authorization(config, request, authorization, now=now)
        request = {**request, "host_authorization": authorization}
        worktree = Path(request["worktree"]).resolve()
        _require(worktree in self.worktree_roots, "The command worktree must be a protected state exclusion")
        config_sha = fingerprint("owner-publication-config", config)
        request_sha = fingerprint("owner-publication-request", request)
        batch_id = fingerprint("owner-publication-batch", {"config": config_sha, "request": request_sha})
        remote = config["github"]["host"] + "/" + config["github"]["repository"] + ".git"
        command = "& git -C " + _literal(str(worktree)) + " push --atomic " + _literal(remote)
        for item in request["streams"]:
            command += " " + _literal(item["head"] + ":refs/heads/" + item["branch"])
        _require(len(command) <= MAX_COMMAND, "Owner publication command exceeds its bound")
        with self.transaction() as db:
            row = db.execute("SELECT request_sha256 FROM owner_publication_batch WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                db.execute("INSERT INTO owner_publication_batch VALUES(?,?,?,?,?,?)",
                           (batch_id, canonical(request).decode(), request_sha, config_sha, command, now))
                for item in request["streams"]:
                    db.execute("INSERT INTO owner_publication_pr VALUES(?,?,?,'WAITING_OWNER_PUSH',NULL,NULL,NULL)",
                               (batch_id, item["stream"], str(uuid.uuid4())))
        return self.snapshot(batch_id, config)

    def snapshot(self, batch_id, config):
        with self.connection() as db:
            row = db.execute("SELECT * FROM owner_publication_batch WHERE batch_id=?", (batch_id,)).fetchone()
            _require(row is not None, "Unknown owner-publication handoff")
            _require(row["config_sha256"] == fingerprint("owner-publication-config", config),
                     "Accepted publication configuration changed; reconcile the retained handoff")
            request = loads(row["request_json"])
            _require(row["request_sha256"] == fingerprint("owner-publication-request", request),
                     "Retained owner-publication request digest changed")
            operations = [dict(item) for item in db.execute(
                "SELECT * FROM owner_publication_pr WHERE batch_id=? ORDER BY stream", (batch_id,))]
            return {"batch_id": batch_id, "request": request, "command": row["command"],
                    "prepared_at": row["prepared_at"], "operations": operations,
                    "complete": all(item["state"] == "PR_CREATED" for item in operations),
                    "execution_authority": False}

    def record_heads(self, batch_id, config, observation, *, now):
        snapshot = self.snapshot(batch_id, config)
        github = config["github"]
        expected = {item["branch"]: item["head"] for item in snapshot["request"]["streams"]}
        _require(isinstance(observation, dict) and set(observation) == {
            "source", "host", "repository", "repository_id", "observed_at", "complete", "heads"},
            "Malformed remote-head observation")
        _require(observation["source"] == "host_observation" and observation["complete"] is True
                 and observation["host"] == github["host"] and observation["repository"] == github["repository"]
                 and type(observation["repository_id"]) is int and observation["repository_id"] == github["repository_id"],
                 "Remote-head observation has a foreign or incomplete repository identity")
        fresh(observation["observed_at"], now, 300, skew_seconds=0)
        _require(timestamp(observation["observed_at"]) >= timestamp(snapshot["prepared_at"]),
                 "Remote-head observation predates the owner handoff")
        _require(observation["heads"] == expected, "Owner push is missing or remote heads differ from completed work")
        with self.transaction() as db:
            db.execute("UPDATE owner_publication_pr SET state='PR_PENDING',remote_json=? WHERE batch_id=? AND state='WAITING_OWNER_PUSH'",
                       (canonical(observation).decode(), batch_id))

    def begin_pr(self, batch_id, stream, *, now):
        timestamp(now)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM owner_publication_pr WHERE batch_id=? AND stream=?", (batch_id, stream)).fetchone()
            _require(row is not None, "Unknown publication stream")
            if row["state"] != "PR_PENDING":
                return False
            db.execute("UPDATE owner_publication_pr SET state='PR_IN_FLIGHT',started_at=? WHERE batch_id=? AND stream=?",
                       (now, batch_id, stream))
            return True

    def uncertain_pr(self, batch_id, stream):
        with self.transaction() as db:
            db.execute("UPDATE owner_publication_pr SET state='PR_UNKNOWN' WHERE batch_id=? AND stream=? AND state='PR_IN_FLIGHT'",
                       (batch_id, stream))

    def finish_pr(self, batch_id, config, stream, receipt, *, now):
        snapshot = self.snapshot(batch_id, config)
        operation = next(item for item in snapshot["operations"] if item["stream"] == stream)
        item = next(item for item in snapshot["request"]["streams"] if item["stream"] == stream)
        github = config["github"]
        expected = {"operation_id": operation["operation_id"], "repository_id": github["repository_id"],
                    "branch": item["branch"], "head": item["head"], "base_branch": github["base_branch"],
                    "body_sha256": sha256(item["body"].encode()), "draft": True}
        _require(operation["state"] in {"PR_IN_FLIGHT", "PR_UNKNOWN"}, "Draft PR operation has not begun")
        _require(isinstance(receipt, dict) and set(receipt) == set(expected) | {"url", "number", "observed_at"}
                 and all(receipt[key] == value and type(receipt[key]) is type(value) for key, value in expected.items()),
                 "Draft PR readback differs from the exact retained operation")
        _require(type(receipt["number"]) is int and receipt["number"] > 0
                 and receipt["url"] == github["host"] + "/" + github["repository"] + "/pull/" + str(receipt["number"]),
                 "Draft PR URL or immutable repository binding differs")
        fresh(receipt["observed_at"], now, 300, skew_seconds=0)
        _require(timestamp(receipt["observed_at"]) >= timestamp(operation["started_at"]),
                 "Draft PR readback predates its durable intent")
        with self.transaction() as db:
            prior = db.execute("SELECT receipt_json FROM owner_publication_pr WHERE batch_id=? AND stream<>? AND receipt_json IS NOT NULL",
                               (batch_id, stream)).fetchall()
            _require(all(loads(row["receipt_json"])["number"] != receipt["number"] for row in prior),
                     "A draft PR cannot satisfy two different retained streams")
            db.execute("UPDATE owner_publication_pr SET state='PR_CREATED',receipt_json=? WHERE batch_id=? AND stream=? AND state IN ('PR_IN_FLIGHT','PR_UNKNOWN')",
                       (canonical(receipt).decode(), batch_id, stream))


def resume_owner_publication(store, batch_id, config, *, now, observe_identity,
                             observe_remote_heads, create_draft_pr, observe_draft_pr):
    """Resume only publication; uncertain PR mutations are observed, never retried.

    The reviewed create adapter must enforce its operation UUID, publication
    scans, accepted permission and head/body/base preconditions. A separate
    readback establishes success; the create callback's return is not proof.
    """
    _require(all(callable(adapter) for adapter in
                 (observe_identity, observe_remote_heads, create_draft_pr, observe_draft_pr)),
             "Owner publication requires reviewed observation and PR adapters")
    snapshot = store.snapshot(batch_id, config)
    if snapshot["complete"]:
        return snapshot
    identity = observe_identity()
    github_identity_preflight(config, identity)
    fresh(identity["observed_at"], now, 300, skew_seconds=0)
    store.record_heads(batch_id, config, observe_remote_heads(snapshot), now=now)
    errors = []
    for operation in store.snapshot(batch_id, config)["operations"]:
        if operation["state"] == "PR_CREATED":
            continue
        item = next(item for item in snapshot["request"]["streams"] if item["stream"] == operation["stream"])
        payload = {"batch_id": batch_id, "operation_id": operation["operation_id"],
                   "repository": config["github"]["repository"], "repository_id": config["github"]["repository_id"],
                   "base_branch": config["github"]["base_branch"], "draft": True, **item}
        try:
            if store.begin_pr(batch_id, item["stream"], now=now):
                create_draft_pr(payload)
            receipt = observe_draft_pr(payload)
            store.finish_pr(batch_id, config, item["stream"], receipt, now=now)
        except Exception as exc:
            store.uncertain_pr(batch_id, item["stream"])
            errors.append({"stream": item["stream"], "state": "PR_UNKNOWN",
                           "reason": str(exc) if isinstance(exc, ValidationError) else type(exc).__name__})
    return {**store.snapshot(batch_id, config), "errors": errors}
