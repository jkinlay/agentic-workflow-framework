"""Transactional local reference state, inbox/outbox, leases and reservations.

The database is a trusted coordinator component, not a worker-writable artifact.
No method sends an external request or claims OS/process fencing is installed.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import re
import sqlite3
import uuid

from . import ValidationError
from .canonical import canonical, fingerprint, loads, now_text, timestamp
from .lifecycle import transition

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
 event_type TEXT NOT NULL, timestamp TEXT NOT NULL, external_id TEXT UNIQUE, payload TEXT NOT NULL,
 previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only events'); END;
CREATE TABLE IF NOT EXISTS tickets(issue_id TEXT PRIMARY KEY,state TEXT NOT NULL,revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ownership(issue_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,generation INTEGER NOT NULL,expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations(operation_id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,
 status TEXT NOT NULL,generation INTEGER NOT NULL,result TEXT);
CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY,gate_hash TEXT NOT NULL,expires_at TEXT NOT NULL,
 generation INTEGER NOT NULL,status TEXT NOT NULL,source_event_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS leases(fencing_token INTEGER PRIMARY KEY AUTOINCREMENT,lease_id TEXT UNIQUE NOT NULL,
 task_id TEXT NOT NULL,owner_id TEXT NOT NULL,workers INTEGER NOT NULL,heavy INTEGER NOT NULL,gpu INTEGER NOT NULL,
 issued_at TEXT NOT NULL,expires_at TEXT NOT NULL,generation INTEGER NOT NULL,status TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS active_task_lease ON leases(task_id) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS lease_resources(lease_id TEXT NOT NULL,name TEXT NOT NULL,slots INTEGER NOT NULL,
 PRIMARY KEY(lease_id,name));
CREATE TABLE IF NOT EXISTS reservations(run_id TEXT PRIMARY KEY,ticket TEXT NOT NULL,day TEXT NOT NULL,
 tokens INTEGER NOT NULL,cost INTEGER NOT NULL,status TEXT NOT NULL,actual_tokens INTEGER,actual_cost INTEGER);
"""


class Store:
    def __init__(self, path: Path, project_id: str, worktree_roots=()):
        self.path = Path(path).absolute()
        uuid.UUID(project_id)
        for parent in [self.path, *self.path.parents]:
            if parent.exists() and (parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())):
                raise ValidationError("State path traverses a link/reparse point")
        if self.path.exists() and self.path.stat().st_nlink > 1:
            raise ValidationError("State file has multiple hardlinks")
        for worktree in worktree_roots:
            if self.path.is_relative_to(Path(worktree).resolve()):
                raise ValidationError("Trusted state must be outside worker worktrees")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        with self.connection() as db:
            db.executescript(DDL)
            db.execute("INSERT OR IGNORE INTO meta VALUES('project',?)", (project_id,))
            if db.execute("SELECT value FROM meta WHERE key='project'").fetchone()[0] != project_id:
                raise ValidationError("Database belongs to another project")
            for key, value in [("mode", "PAUSED"), ("generation", "1"), ("schema", "3")]:
                db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", (key, value))
            if self._meta(db, "schema") != "3":
                raise ValidationError("Unsupported database schema; explicit migration required")
            # Add attribution without relabeling legacy reservations or events.
            db.execute("BEGIN IMMEDIATE")
            try:
                if "operating_hash" not in {row["name"] for row in db.execute("PRAGMA table_info(reservations)")}:
                    db.execute("ALTER TABLE reservations ADD COLUMN operating_hash TEXT")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
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

    def _meta(self, db, name):
        return db.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()[0]

    def _running(self, db):
        if self._meta(db, "mode") != "RUNNING":
            raise ValidationError("Coordinator is paused or stopped")
        return int(self._meta(db, "generation"))

    def _event(self, db, kind, payload, external_id=None, now=None):
        data = canonical(payload).decode()
        if external_id:
            old = db.execute("SELECT * FROM events WHERE external_id=?", (external_id,)).fetchone()
            if old:
                if old["event_type"] != kind or old["payload"] != data:
                    raise ValidationError("External-event id reused with different content")
                return old["sequence"]
        last = db.execute("SELECT sequence,event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        sequence, previous = (last["sequence"] + 1, last["event_hash"]) if last else (1, "0" * 64)
        event_id, recorded = str(uuid.uuid4()), now or now_text()
        timestamp(recorded)
        envelope = {"sequence": sequence, "event_id": event_id, "project_id": self.project_id, "event_type": kind,
                    "timestamp": recorded, "external_id": external_id, "payload": payload, "previous_hash": previous}
        digest = fingerprint("event", envelope)
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (sequence, event_id, kind, recorded, external_id, data, previous, digest))
        return sequence

    def ingest(self, kind, payload, external_id):
        if not external_id or ":" not in external_id:
            raise ValidationError("External event identity must include source namespace")
        with self.transaction() as db:
            return self._event(db, kind, payload, external_id)

    def control(self, mode, reason, reconciled=False):
        if mode not in {"PAUSED", "RUNNING", "STOPPED"} or not reason.strip():
            raise ValidationError("Invalid control operation")
        if mode == "RUNNING" and not reconciled:
            raise ValidationError("Resume requires explicit completed reconciliation")
        with self.transaction() as db:
            generation = int(self._meta(db, "generation")) + 1
            db.execute("UPDATE meta SET value=? WHERE key='generation'", (str(generation),))
            db.execute("UPDATE meta SET value=? WHERE key='mode'", (mode,))
            db.execute("UPDATE requests SET status='REVOKED' WHERE status='OPEN'")
            db.execute("UPDATE leases SET status='REVOKED' WHERE status='ACTIVE'")
            db.execute("UPDATE operations SET status='UNKNOWN' WHERE status='IN_FLIGHT'")
            db.execute("UPDATE operations SET status='CANCELLED' WHERE status='PENDING'")
            self._event(db, "CONTROL_CHANGED", {"mode": mode, "generation": generation, "reason": reason})
            return generation

    def advance(self, issue_id, event, expected_revision, facts, resume_state=None):
        with self.transaction() as db:
            row = db.execute("SELECT state,revision FROM tickets WHERE issue_id=?", (issue_id,)).fetchone()
            state, revision = (row["state"], row["revision"]) if row else ("BACKLOG", 0)
            if expected_revision != revision:
                raise ValidationError("Concurrent ticket revision changed")
            new_state = transition(state, event, facts, resume_state)
            db.execute("INSERT INTO tickets VALUES(?,?,?) ON CONFLICT(issue_id) DO UPDATE SET state=excluded.state,revision=excluded.revision",
                       (issue_id, new_state, revision + 1))
            self._event(db, event, {"issue_id": issue_id, "previous_state": state, "state": new_state,
                                   "revision": revision + 1, "facts": facts, "resume_state": resume_state})
            return new_state, revision + 1

    def claim_issue(self, issue_id, owner_id, now, ttl_seconds):
        if ttl_seconds <= 0:
            raise ValidationError("Ownership TTL must be positive")
        expires = (timestamp(now) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        with self.transaction() as db:
            self._running(db)
            old = db.execute("SELECT * FROM ownership WHERE issue_id=?", (issue_id,)).fetchone()
            if old and timestamp(old["expires_at"]) > timestamp(now) and old["owner_id"] != owner_id:
                raise ValidationError("Ticket already owned by another controller")
            generation = old["generation"] + 1 if old else 1
            db.execute("INSERT INTO ownership VALUES(?,?,?,?) ON CONFLICT(issue_id) DO UPDATE SET owner_id=excluded.owner_id,generation=excluded.generation,expires_at=excluded.expires_at",
                       (issue_id, owner_id, generation, expires))
            self._event(db, "ISSUE_CLAIMED", {"issue_id": issue_id, "owner_id": owner_id, "generation": generation, "expires_at": expires}, now=now)
            return generation

    def enqueue(self, operation_id, kind, payload):
        if kind not in {"dispatch_proposal", "jira_transition_proposal", "merge_proposal"}:
            raise ValidationError("Only reference operation proposals are supported")
        uuid.UUID(operation_id)
        data = canonical(payload).decode()
        with self.transaction() as db:
            generation = self._running(db)
            old = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if old:
                if old["kind"] != kind or old["payload"] != data:
                    raise ValidationError("Idempotency key collision")
                return old["status"]
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,NULL)", (operation_id, kind, data, "PENDING", generation))
            self._event(db, "OPERATION_PROPOSED", {"operation_id": operation_id, "kind": kind, "payload": payload})
            return "PENDING"

    def begin_simulated_operation(self, operation_id):
        with self.transaction() as db:
            generation = self._running(db)
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row or row["status"] != "PENDING" or row["generation"] != generation:
                raise ValidationError("Operation is not current and pending")
            db.execute("UPDATE operations SET status='IN_FLIGHT' WHERE operation_id=?", (operation_id,))
            self._event(db, "SIMULATED_OPERATION_STARTED", {"operation_id": operation_id})

    def reconcile_operation(self, operation_id, status, evidence):
        if status not in {"SUCCEEDED", "FAILED", "UNKNOWN"} or not evidence:
            raise ValidationError("Reconciliation requires an outcome and evidence")
        with self.transaction() as db:
            row = db.execute("SELECT status,result FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row:
                raise ValidationError("Unknown operation")
            encoded = canonical(evidence).decode()
            if row["status"] in {"SUCCEEDED", "FAILED"}:
                if row["status"] != status or row["result"] != encoded:
                    raise ValidationError("Conflicting terminal operation result")
                return
            if row["status"] not in {"IN_FLIGHT", "UNKNOWN"}:
                raise ValidationError("Operation was never started or was cancelled")
            db.execute("UPDATE operations SET status=?,result=? WHERE operation_id=?", (status, encoded, operation_id))
            self._event(db, "OPERATION_RECONCILED", {"operation_id": operation_id, "status": status, "evidence": evidence})

    def recover_inflight(self):
        with self.transaction() as db:
            ids = [row[0] for row in db.execute("SELECT operation_id FROM operations WHERE status='IN_FLIGHT'")]
            db.execute("UPDATE operations SET status='UNKNOWN' WHERE status='IN_FLIGHT'")
            if ids:
                self._event(db, "UNCERTAIN_OPERATIONS_RECOVERED", {"operation_ids": ids})
            return ids

    def register_request(self, request_id, gate_hash, expires_at):
        uuid.UUID(request_id)
        timestamp(expires_at)
        with self.transaction() as db:
            generation = self._running(db)
            old = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old["gate_hash"] != gate_hash or old["expires_at"] != expires_at:
                    raise ValidationError("Request ID collision")
                return old["status"]
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,NULL)", (request_id, gate_hash, expires_at, generation, "OPEN"))
            self._event(db, "REQUEST_REGISTERED", {"request_id": request_id, "gate_hash": gate_hash, "expires_at": expires_at})
            return "OPEN"

    def consume_request(self, request_id, gate_hash, source_event_id, now):
        with self.transaction() as db:
            generation = self._running(db)
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if not row or row["status"] != "OPEN" or row["generation"] != generation or row["gate_hash"] != gate_hash:
                raise ValidationError("Request is stale, revoked, used, or does not match gate")
            if timestamp(row["expires_at"]) <= timestamp(now):
                raise ValidationError("Request expired")
            if db.execute("SELECT 1 FROM requests WHERE source_event_id=?", (source_event_id,)).fetchone():
                raise ValidationError("Authorization source event was already consumed")
            db.execute("UPDATE requests SET status='CONSUMED',source_event_id=? WHERE request_id=?", (source_event_id, request_id))
            self._event(db, "REFERENCE_REQUEST_CONSUMED", {"request_id": request_id, "gate_hash": gate_hash, "source_event_id": source_event_id}, now=now)
            return {"consumed": True, "execution_authority": False}

    def revoke_request(self, request_id, reason):
        if not reason:
            raise ValidationError("Revocation requires reason")
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM requests WHERE request_id=?", (request_id,)).fetchone():
                raise ValidationError("Unknown request")
            db.execute("UPDATE requests SET status='REVOKED' WHERE request_id=?", (request_id,))
            self._event(db, "REQUEST_REVOKED", {"request_id": request_id, "reason": reason})

    def acquire_lease(self, task_id, owner_id, workers, heavy, gpu, now, ttl_seconds, limits, resources=None, resource_limits=None):
        """Anonymous classes (workers/heavy/gpu) plus named exclusive resources (licensed_analysis_tool: 1).

        A named resource with no free slot refuses the dispatch and names the resource.
        """
        uuid.UUID(task_id)
        if not isinstance(limits, (list, tuple)) or len(limits) != 3 or any(type(cap) is not int or cap < 0 for cap in limits):
            raise ValidationError("Capacity limits must contain three nonnegative integers")
        if not all(type(v) is int and v >= 0 for v in [workers, heavy, gpu, ttl_seconds]) or workers < 1 or ttl_seconds < 1:
            raise ValidationError("Invalid resource reservation")
        resources = dict(resources or {})
        resource_limits = dict(resource_limits or {})
        for name, slots in resources.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name)) or type(slots) is not int or slots < 1:
                raise ValidationError("Named resources need lowercase names and positive slot counts")
            if name not in resource_limits:
                raise ValidationError(f"Named resource {name!r} is not declared in execution.host_broker.resources")
        expires = (timestamp(now) + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        with self.transaction() as db:
            generation = self._running(db)
            for row in db.execute("SELECT * FROM leases WHERE status='ACTIVE'").fetchall():
                if timestamp(row["expires_at"]) <= timestamp(now):
                    db.execute("UPDATE leases SET status='EXPIRED' WHERE lease_id=?", (row["lease_id"],))
                    self._event(db, "LEASE_EXPIRED", {"lease_id": row["lease_id"], "fencing_token": row["fencing_token"]}, now=now)
            old = db.execute("SELECT * FROM leases WHERE task_id=? AND status='ACTIVE'", (task_id,)).fetchone()
            if old:
                held = {r["name"]: r["slots"] for r in db.execute("SELECT name,slots FROM lease_resources WHERE lease_id=?", (old["lease_id"],))}
                if (old["owner_id"], old["workers"], old["heavy"], old["gpu"], held) != (owner_id, workers, heavy, gpu, resources):
                    raise ValidationError("Lease idempotency collision")
                return {**dict(old), "resources_held": held}
            usage = db.execute("SELECT COALESCE(SUM(workers),0),COALESCE(SUM(heavy),0),COALESCE(SUM(gpu),0) FROM leases WHERE status='ACTIVE'").fetchone()
            if any(used + requested > cap for used, requested, cap in zip(usage, [workers, heavy, gpu], limits)):
                raise ValidationError("Host capacity unavailable")
            for name, slots in resources.items():
                used = db.execute("SELECT COALESCE(SUM(r.slots),0) FROM lease_resources r JOIN leases l ON l.lease_id=r.lease_id WHERE l.status='ACTIVE' AND r.name=?", (name,)).fetchone()[0]
                if used + slots > resource_limits[name]:
                    raise ValidationError(f"Named resource {name!r} has no free slot ({used} of {resource_limits[name]} held by an ACTIVE lease); dispatch refused")
            lease_id = str(uuid.uuid4())
            db.execute("INSERT INTO leases(lease_id,task_id,owner_id,workers,heavy,gpu,issued_at,expires_at,generation,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (lease_id, task_id, owner_id, workers, heavy, gpu, now, expires, generation, "ACTIVE"))
            for name, slots in resources.items():
                db.execute("INSERT INTO lease_resources VALUES(?,?,?)", (lease_id, name, slots))
            row = dict(db.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone())
            row["resources_held"] = resources
            self._event(db, "LEASE_ACQUIRED", row, now=now)
            return row

    def check_lease(self, lease_id, fencing_token, owner_id, now):
        with self.connection() as db:
            generation = self._running(db)
            row = db.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if not row or row["status"] != "ACTIVE" or row["fencing_token"] != fencing_token or row["owner_id"] != owner_id or row["generation"] != generation or timestamp(row["expires_at"]) <= timestamp(now):
                raise ValidationError("Lease is expired, stale, or belongs to another owner")
            return True

    def release_lease(self, lease_id, owner_id):
        with self.transaction() as db:
            row = db.execute("SELECT owner_id,status FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
            if not row or row["owner_id"] != owner_id:
                raise ValidationError("Unknown lease or wrong owner")
            if row["status"] == "ACTIVE":
                db.execute("UPDATE leases SET status='RELEASED' WHERE lease_id=?", (lease_id,))
                self._event(db, "LEASE_RELEASED", {"lease_id": lease_id})

    def reserve_budget(self, run_id, ticket, now, tokens, cost, limits, *, operating_hash=None):
        uuid.UUID(run_id)
        if operating_hash is not None and (not isinstance(operating_hash, str) or not re.fullmatch('[0-9a-f]{64}', operating_hash)):
            raise ValidationError("operating_hash must be a validated snapshot SHA256 or null for legacy callers")
        if set(limits) != {"runs", "tokens", "cost", "daily_cost"} or any(type(value) is not int or value <= 0 for value in limits.values()):
            raise ValidationError("Budget limits must be positive integer runs, tokens, cost and daily_cost")
        if type(tokens) is not int or type(cost) is not int or tokens < 1 or cost < 1:
            raise ValidationError("Budgets use positive integer tokens/micro-USD")
        day = timestamp(now).date().isoformat()
        with self.transaction() as db:
            self._running(db)
            old = db.execute("SELECT * FROM reservations WHERE run_id=?", (run_id,)).fetchone()
            if old:
                if (old["ticket"], old["tokens"], old["cost"], old["operating_hash"]) != (ticket, tokens, cost, operating_hash):
                    raise ValidationError("Budget reservation ID collision")
                return old["status"]
            rows = db.execute("SELECT * FROM reservations").fetchall()
            def used(row, key):
                return row["actual_" + key] if row["status"] == "SETTLED" else row[key]
            ticket_rows = [r for r in rows if r["ticket"] == ticket]
            if len(ticket_rows) >= limits["runs"] or sum(used(r, "tokens") for r in ticket_rows) + tokens > limits["tokens"] or sum(used(r, "cost") for r in ticket_rows) + cost > limits["cost"] or sum(used(r, "cost") for r in rows if r["day"] == day) + cost > limits["daily_cost"]:
                raise ValidationError("Budget reservation exceeds hard limit")
            db.execute("INSERT INTO reservations (run_id,ticket,day,tokens,cost,status,actual_tokens,actual_cost,operating_hash) VALUES(?,?,?,?,?,'RESERVED',NULL,NULL,?)", (run_id, ticket, day, tokens, cost, operating_hash))
            self._event(db, "BUDGET_RESERVED", {"run_id": run_id, "ticket": ticket, "tokens": tokens, "cost": cost, "operating_hash": operating_hash}, now=now)
            return "RESERVED"

    def settle_budget(self, run_id, tokens, cost):
        if not all(type(v) is int and v >= 0 for v in [tokens, cost]):
            raise ValidationError("Invalid usage telemetry")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM reservations WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise ValidationError("No budget reservation")
            if row["status"] == "SETTLED":
                if row["actual_tokens"] != tokens or row["actual_cost"] != cost:
                    raise ValidationError("Conflicting usage settlement")
                return
            db.execute("UPDATE reservations SET status='SETTLED',actual_tokens=?,actual_cost=? WHERE run_id=?", (tokens, cost, run_id))
            overrun = tokens > row["tokens"] or cost > row["cost"]
            if overrun:
                db.execute("UPDATE meta SET value='PAUSED' WHERE key='mode'")
                db.execute("UPDATE meta SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'")
                db.execute("UPDATE requests SET status='REVOKED' WHERE status='OPEN'")
                db.execute("UPDATE leases SET status='REVOKED' WHERE status='ACTIVE'")
                db.execute("UPDATE operations SET status='UNKNOWN' WHERE status='IN_FLIGHT'")
                db.execute("UPDATE operations SET status='CANCELLED' WHERE status='PENDING'")
            self._event(db, "BUDGET_SETTLED", {"run_id": run_id, "tokens": tokens, "cost": cost, "overrun": overrun,
                                             "operating_hash": row["operating_hash"]})

    def verify_chain(self):
        with self.connection() as db:
            previous, sequence = "0" * 64, 0
            for row in db.execute("SELECT * FROM events ORDER BY sequence"):
                sequence += 1
                envelope = {"sequence": row["sequence"], "event_id": row["event_id"], "project_id": self.project_id,
                    "event_type": row["event_type"], "timestamp": row["timestamp"], "external_id": row["external_id"],
                    "payload": loads(row["payload"]), "previous_hash": row["previous_hash"]}
                if row["sequence"] != sequence or row["previous_hash"] != previous or fingerprint("event", envelope) != row["event_hash"]:
                    raise ValidationError("Event chain verification failed")
                previous = row["event_hash"]
            return {"sequence": sequence, "event_hash": previous}

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise ValidationError("Backup destination must be a new file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source:
            backup = sqlite3.connect(str(destination))
            try:
                source.backup(backup)
            finally:
                backup.close()
        return self.verify_chain()
