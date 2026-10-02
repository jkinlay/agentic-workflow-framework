"""Trusted controller adapters for governed heavy validation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable

from . import ValidationError
from .canonical import canonical, fingerprint, loads, sha256, timestamp
from .child_process import child_env
from .heavy_validation import resolve_without_alias


_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_MAX_PROVIDER_BYTES = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


class GitHubReviewAuthenticator:
    """Authenticate a formal GitHub approval and the exact candidate tuple."""

    def __init__(self, repository: str, pull_number: int, review_id: int,
                 read_api: Callable[[str], dict] | None = None):
        _require(isinstance(repository, str) and _REPOSITORY.fullmatch(repository),
                 "Invalid GitHub repository identity")
        _require(type(pull_number) is int and pull_number > 0, "Invalid GitHub PR number")
        _require(type(review_id) is int and review_id > 0, "Invalid GitHub review ID")
        self.repository = repository
        self.pull_number = pull_number
        self.review_id = review_id
        self.read_api = read_api or self._gh_api

    @staticmethod
    def _gh_api(endpoint: str) -> dict:
        env = child_env(dict(os.environ))
        env.update(GH_PROMPT_DISABLED="1", GH_PAGER="cat")
        executable = shutil.which("gh")
        if not executable:
            raise ValidationError("GitHub review authentication is unavailable")
        executable = str(Path(executable).resolve())
        if Path(executable).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
            raise ValidationError("GitHub review authentication needs a native executable")
        try:
            done = subprocess.run(
                [executable, "api", "--hostname", "github.com", "--method", "GET", endpoint],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, env=env, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError("GitHub review authentication is unavailable") from exc
        if done.returncode or len(done.stdout) > _MAX_PROVIDER_BYTES:
            raise ValidationError("GitHub review authentication failed")
        try:
            value = json.loads(done.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationError("GitHub review authentication returned malformed evidence") from exc
        _require(isinstance(value, dict), "GitHub review authentication returned malformed evidence")
        return value

    def __call__(self, review: dict, review_digest: str, plan_digest: str,
                 candidate: dict) -> dict:
        repository = self.read_api(f"repos/{self.repository}")
        pull = self.read_api(f"repos/{self.repository}/pulls/{self.pull_number}")
        commit = self.read_api(f"repos/{self.repository}/git/commits/{candidate['head_sha']}")
        provider_review = self.read_api(
            f"repos/{self.repository}/pulls/{self.pull_number}/reviews/{self.review_id}")
        _require(repository.get("id") == candidate["repository_id"],
                 "GitHub repository ID does not match the candidate")
        _require(pull.get("number") == self.pull_number
                 and pull.get("head", {}).get("sha") == candidate["head_sha"]
                 and pull.get("base", {}).get("sha") == candidate["base_sha"],
                 "GitHub PR tuple does not match the candidate")
        _require(commit.get("sha") == candidate["head_sha"]
                 and commit.get("tree", {}).get("sha") == candidate["tree_sha"],
                 "GitHub commit tree does not match the candidate")
        user = provider_review.get("user") if isinstance(provider_review.get("user"), dict) else {}
        _require(provider_review.get("id") == self.review_id
                 and provider_review.get("state") == "APPROVED"
                 and provider_review.get("commit_id") == candidate["head_sha"],
                 "GitHub review is not an approval of the exact head")
        _require(review["reviewer"]["provider"] == "github"
                 and str(user.get("id")) == review["reviewer"]["immutable_id"]
                 and user.get("login") == review["reviewer"]["login"],
                 "GitHub reviewer identity does not match the reviewed record")
        evidence = {"repository": repository, "pull": pull, "commit": commit,
                    "review": provider_review}
        return {"status": "AUTHENTICATED", **review["reviewer"],
                "evidence_sha256": fingerprint("github-heavy-validation-review", evidence),
                "review_sha256": review_digest, "plan_sha256": plan_digest,
                "candidate": deepcopy(candidate)}


class FileLeaseBroker:
    """Single-host durable lease broker with OS-level serialization and fencing."""

    def __init__(self, state_path: str | os.PathLike[str], broker_id: str, limits: dict,
                 clock: Callable[[], datetime] | None = None):
        parent = resolve_without_alias(Path(state_path).absolute().parent,
                                       "broker state parent", directory=True)
        self.path = parent / Path(state_path).name
        _require(not self.path.is_symlink(),
                 "Broker state cannot be a symlink")
        _require(isinstance(broker_id, str) and broker_id, "Broker identity is required")
        self.broker_id = broker_id
        self.limits = deepcopy(limits)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _lock(stream):
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock(stream):
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _transaction(self, operation):
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\n")
                stream.flush()
            self._lock(stream)
            try:
                stream.seek(0)
                raw = stream.read().strip()
                state = (json.loads(raw.decode("utf-8")) if raw else
                         {"format": "awf-heavy-validation-broker-state-1",
                          "broker_id": self.broker_id, "next_fence": 1, "leases": []})
                _require(state.get("format") == "awf-heavy-validation-broker-state-1"
                         and state.get("broker_id") == self.broker_id
                         and type(state.get("next_fence")) is int
                         and isinstance(state.get("leases"), list),
                         "Durable broker state is invalid")
                result = operation(state)
                encoded = canonical(state)
                stream.seek(0)
                stream.truncate()
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                return result
            finally:
                self._unlock(stream)

    def acquire(self, request: dict) -> dict:
        expected = {"format", "candidate", "plan_sha256", "config_sha256",
                    "capacity_sha256", "parallelism", "resource_class", "engine",
                    "required_resources", "resource_claims", "determinism", "isolation",
                    "required_until"}
        _require(isinstance(request, dict) and set(request) == expected
                 and request["format"] == "awf-heavy-validation-lease-request-1",
                 "Lease request fields are invalid")
        _require(type(request["parallelism"]) is int and request["parallelism"] > 0,
                 "Lease parallelism is invalid")
        _require(request["resource_class"] in {"standard", "heavy", "gpu"}
                 and isinstance(request["required_resources"], list)
                 and isinstance(request["resource_claims"], dict)
                 and set(request["resource_claims"]).issubset(request["required_resources"])
                 and all(type(value) is int and value > 0
                         for value in request["resource_claims"].values()),
                 "Lease resource declaration is invalid")
        _require(request["isolation"] == {"process_tree": "REQUIRED",
                                          "network": "HOST_POLICY",
                                          "filesystem": "WORKTREE"},
                 "Lease isolation declaration is invalid")
        _require(isinstance(request["determinism"], dict)
                 and set(request["determinism"]) == {"seed", "retry_limit"}
                 and all(type(value) is int and value >= 0
                         for value in request["determinism"].values()),
                 "Lease determinism declaration is invalid")
        timestamp(request["required_until"])
        now = self.clock()
        request_sha = fingerprint("heavy-validation-lease-request", request)

        def operation(state):
            active = [item for item in state["leases"]
                      if timestamp(item["expires_at"]) > now]
            state["leases"] = active
            requested = request["parallelism"]
            worker_use = sum(item["parallelism"] for item in active)
            class_use = sum(item["parallelism"] for item in active
                            if item["resource_class"] in {"heavy", "gpu"})
            gpu_use = sum(item["parallelism"] for item in active
                          if item["resource_class"] == "gpu")
            reasons = []
            if worker_use + requested > self.limits["max_workers"]:
                reasons.append("worker capacity")
            if request["resource_class"] in {"heavy", "gpu"} and class_use + requested > self.limits["max_heavy_jobs"]:
                reasons.append("heavy capacity")
            if request["resource_class"] == "gpu" and gpu_use + requested > self.limits["max_gpu_jobs"]:
                reasons.append("gpu capacity")
            for name, amount in request["resource_claims"].items():
                used = sum(item["resource_claims"].get(name, 0) for item in active)
                if used + amount > self.limits["resources"].get(name, 0):
                    reasons.append(f"resource {name}")
            if reasons:
                return {"status": "DENIED", "reason": ", ".join(sorted(reasons))}
            fence = state["next_fence"]
            state["next_fence"] += 1
            lease_id = hashlib.sha256(f"{self.broker_id}:{fence}:{request_sha}".encode()).hexdigest()
            acquired = now.isoformat(timespec="seconds").replace("+00:00", "Z")
            item = {"lease_id": lease_id, "fencing_token": fence,
                    "expires_at": request["required_until"], "parallelism": requested,
                    "resource_class": request["resource_class"],
                    "resource_claims": deepcopy(request["resource_claims"]),
                    "request_sha256": request_sha}
            state["leases"].append(item)
            return {"status": "GRANTED", "lease_id": lease_id,
                    "fencing_token": fence, "broker_id": self.broker_id,
                    "acquired_at": acquired, "expires_at": request["required_until"],
                    "request_sha256": request_sha}
        return self._transaction(operation)

    def release(self, lease_id: str, fencing_token: int) -> dict:
        def operation(state):
            matches = [item for item in state["leases"]
                       if item.get("lease_id") == lease_id
                       and item.get("fencing_token") == fencing_token]
            if len(matches) != 1:
                return {"status": "STALE", "lease_id": lease_id,
                        "fencing_token": fencing_token}
            state["leases"].remove(matches[0])
            return {"status": "RELEASED", "lease_id": lease_id,
                    "fencing_token": fencing_token}
        return self._transaction(operation)


def broker_limits(config: dict, capacity: dict) -> tuple[str, dict]:
    broker = config["execution"]["host_broker"]
    resources = {name: min(amount, capacity["resources_available"].get(name, 0))
                 for name, amount in broker.get("resources", {}).items()}
    return broker["broker_id"], {
        "max_workers": min(broker["max_workers"], capacity["workers_available"]),
        "max_heavy_jobs": min(broker["max_heavy_jobs"], capacity["heavy_jobs_available"]),
        "max_gpu_jobs": min(broker["max_gpu_jobs"], capacity["gpu_jobs_available"]),
        "resources": resources,
    }


def write_result_log(path: str | os.PathLike[str], result: dict) -> dict:
    """Create one immutable, fsync'd result envelope; never overwrite evidence."""
    target = Path(path).absolute()
    parent = resolve_without_alias(target.parent, "result log parent", directory=True)
    target = parent / target.name
    result_raw = canonical(result)
    envelope = {"format": "awf-heavy-validation-result-log-1",
                "result_sha256": sha256(result_raw), "result": result}
    raw = canonical(envelope)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return {"path": str(target), "sha256": sha256(raw),
            "result_sha256": envelope["result_sha256"]}
