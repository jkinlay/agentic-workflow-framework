"""Trusted controller adapters for governed heavy validation."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Callable

from . import ValidationError
from .canonical import canonical, fingerprint, sha256, timestamp
from .child_process import child_env
from .heavy_validation import (CHECKOUT_SNAPSHOT_TIMEOUT_SECONDS,
                               GIT_ATTESTATION_COMMAND_TIMEOUT_SECONDS,
                               resolve_without_alias)


_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_MAX_PROVIDER_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _positive(value, label):
    _require(type(value) is int and value > 0, f"{label} must be a positive integer")
    return value


def _nonnegative(value, label):
    _require(type(value) is int and value >= 0, f"{label} must be a nonnegative integer")
    return value


def _digest(value, label):
    _require(isinstance(value, str) and _SHA256.fullmatch(value),
             f"{label} must be a lowercase SHA-256")
    return value


def _resource_slots(value, label, *, positive=False):
    _require(isinstance(value, dict), f"{label} must be a mapping")
    result = {}
    for name, amount in value.items():
        _require(isinstance(name, str) and _LABEL.fullmatch(name),
                 f"{label} has an invalid resource name")
        (_positive if positive else _nonnegative)(amount, f"{label}.{name}")
        result[name] = amount
    return result


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
                 candidate: dict, authorization: dict) -> dict:
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
        body = provider_review.get("body")
        _require(isinstance(body, str), "GitHub review has no workload authorization")
        markers = re.findall(
            r"(?m)^AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: ([0-9a-f]{64})$", body)
        _require(markers == [authorization["sha256"]],
                 "GitHub review does not authorize the exact workload")
        _require(review["reviewer"]["provider"] == "github"
                 and str(user.get("id")) == review["reviewer"]["immutable_id"]
                 and user.get("login") == review["reviewer"]["login"],
                 "GitHub reviewer identity does not match the reviewed record")
        evidence = {"repository": repository, "pull": pull, "commit": commit,
                    "review": provider_review}
        return {"status": "AUTHENTICATED", **review["reviewer"],
                "evidence_sha256": fingerprint("github-heavy-validation-review", evidence),
                "review_sha256": review_digest, "plan_sha256": plan_digest,
                "candidate": deepcopy(candidate),
                "workload_authorization_sha256": authorization["sha256"]}


class GitCheckoutAttestor:
    """Prove a clean checkout at the exact reviewed HEAD and tree before dispatch."""

    def __init__(self, root: str | os.PathLike[str], run_git=None):
        self.root = resolve_without_alias(Path(root).absolute(), "execution checkout", directory=True)
        self.run_git = run_git or self._git

    def _git(self, args: list[str]) -> str:
        executable = shutil.which("git")
        if not executable:
            raise ValidationError("Git checkout attestation is unavailable")
        executable = str(Path(executable).resolve())
        if Path(executable).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
            raise ValidationError("Git checkout attestation needs a native executable")
        try:
            done = subprocess.run([executable, *args], cwd=str(self.root),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, shell=False,
                                  env=child_env(dict(os.environ)),
                                  timeout=GIT_ATTESTATION_COMMAND_TIMEOUT_SECONDS,
                                  check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError("Git checkout attestation is unavailable") from exc
        if done.returncode or len(done.stdout) > _MAX_PROVIDER_BYTES:
            raise ValidationError("Git checkout attestation failed")
        try:
            return done.stdout.decode("utf-8").strip()
        except UnicodeError as exc:
            raise ValidationError("Git checkout attestation returned malformed evidence") from exc

    def __call__(self, candidate: dict, working_directory: str) -> dict:
        observed_root = resolve_without_alias(Path(working_directory).absolute(),
                                              "execution checkout", directory=True)
        _require(os.path.normcase(str(observed_root)) == os.path.normcase(str(self.root)),
                 "Checkout attestor root mismatch")
        top = self.run_git(["rev-parse", "--show-toplevel"])
        head = self.run_git(["rev-parse", "--verify", "HEAD"])
        tree = self.run_git(["rev-parse", "--verify", "HEAD^{tree}"])
        dirty = self.run_git(["status", "--porcelain=v1", "--untracked-files=all",
                              "--ignored=matching"])
        _require(os.path.normcase(str(Path(top).resolve())) == os.path.normcase(str(self.root)),
                 "Execution root is not the checkout root")
        _require(head == candidate["head_sha"] and tree == candidate["tree_sha"],
                 "Checkout HEAD/tree does not match the candidate")
        _require(dirty == "", "Execution checkout is dirty")
        evidence = {"candidate": candidate, "working_directory": str(self.root),
                    "head": head, "tree": tree, "status": dirty}
        return {"status": "CLEAN", "candidate": deepcopy(candidate),
                "working_directory": str(self.root),
                "evidence_sha256": fingerprint("heavy-validation-checkout", evidence)}


class GitCheckoutSnapshotter:
    """Materialize execution only from the immutable reviewed Git object."""

    def __init__(self, root: str | os.PathLike[str], run_archive=None):
        self.root = resolve_without_alias(Path(root).absolute(), "execution checkout",
                                          directory=True)
        self.run_archive = run_archive or self._archive

    def _archive(self, head_sha: str) -> bytes:
        executable = shutil.which("git")
        if not executable:
            raise ValidationError("Git checkout snapshot is unavailable")
        executable = str(Path(executable).resolve())
        if Path(executable).suffix.casefold() in {".cmd", ".bat", ".ps1"}:
            raise ValidationError("Git checkout snapshot needs a native executable")
        try:
            done = subprocess.run(
                [executable, "archive", "--format=tar", head_sha], cwd=str(self.root),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, env=child_env(dict(os.environ)),
                timeout=CHECKOUT_SNAPSHOT_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValidationError("Git checkout snapshot is unavailable") from exc
        if done.returncode or len(done.stdout) > _MAX_SNAPSHOT_BYTES:
            raise ValidationError("Git checkout snapshot failed or exceeded its bound")
        return done.stdout

    @staticmethod
    def _extract(raw: bytes, destination: Path) -> int:
        count = 0
        try:
            archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
        except tarfile.TarError as exc:
            raise ValidationError("Git checkout snapshot archive is malformed") from exc
        with archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    raise ValidationError("Git checkout snapshot has an unsafe path")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise ValidationError("Git checkout snapshot contains an alias or special file")
                target = destination.joinpath(*name.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValidationError("Git checkout snapshot file is unreadable")
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     member.mode & 0o777 or 0o600)
                with source, os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                count += 1
        return count

    @staticmethod
    def _freeze(destination: Path):
        paths = sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        for path in paths:
            mode = path.stat().st_mode
            if path.is_dir():
                path.chmod(mode & ~0o222)
            else:
                path.chmod(mode & ~0o222)
        destination.chmod(destination.stat().st_mode & ~0o222)

    @staticmethod
    def _thaw(destination: Path):
        try:
            destination.chmod(destination.stat().st_mode | 0o700)
            paths = sorted(destination.rglob("*"), key=lambda item: len(item.parts))
            for path in paths:
                path.chmod(path.stat().st_mode | (0o700 if path.is_dir() else 0o600))
        except OSError:
            pass

    @staticmethod
    def _lock_snapshot(destination: Path):
        if os.name != "nt":
            raise ValidationError(
                "Immutable snapshot mutation exclusion is unavailable on this host")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handles = []
        paths = [destination, *sorted(destination.rglob("*"), key=lambda item: str(item))]
        invalid = wintypes.HANDLE(-1).value
        try:
            for path in paths:
                flags = 0x02000000 if path.is_dir() else 0x00000080
                handle = kernel.CreateFileW(str(path), 0x80000000, 0x00000001,
                                            None, 3, flags, None)
                if handle == invalid:
                    raise ValidationError(
                        "Snapshot mutation-exclusion handle acquisition failed")
                handles.append(handle)
        except Exception:
            for handle in reversed(handles):
                kernel.CloseHandle(handle)
            raise
        return kernel, handles

    @contextmanager
    def __call__(self, candidate: dict, working_directory: str):
        observed = resolve_without_alias(Path(working_directory).absolute(),
                                         "execution checkout", directory=True)
        _require(os.path.normcase(str(observed)) == os.path.normcase(str(self.root)),
                 "Checkout snapshotter root mismatch")
        raw = self.run_archive(candidate["head_sha"])
        archive_sha = sha256(raw)
        with tempfile.TemporaryDirectory(prefix="awf-heavy-snapshot-") as folder:
            snapshot_root = resolve_without_alias(Path(folder).absolute(),
                                                  "checkout snapshot root", directory=True)
            file_count = self._extract(raw, snapshot_root)
            self._freeze(snapshot_root)
            kernel, handles = self._lock_snapshot(snapshot_root)
            record = {"candidate": deepcopy(candidate),
                      "source_working_directory": str(self.root),
                      "snapshot_working_directory": str(snapshot_root),
                      "tree_sha": candidate["tree_sha"],
                      "archive_sha256": archive_sha, "file_count": file_count,
                      "mutation_guard": "windows-deny-write-delete-handles",
                      "guarded_paths": len(handles)}
            try:
                yield {"status": "IMMUTABLE", **record,
                       "evidence_sha256": fingerprint(
                           "heavy-validation-checkout-snapshot", record)}
            finally:
                for handle in reversed(handles):
                    kernel.CloseHandle(handle)
                self._thaw(snapshot_root)


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
        _require(set(self.limits) == {"max_workers", "max_heavy_jobs", "max_gpu_jobs",
                                      "resources", "engines"},
                 "Broker limits fields are invalid")
        for name in ("max_workers", "max_heavy_jobs", "max_gpu_jobs"):
            _nonnegative(self.limits[name], f"broker limits {name}")
        _resource_slots(self.limits["resources"], "broker limit resources")
        _require(isinstance(self.limits["engines"], dict), "Broker engine limits are invalid")
        for name, evidence in self.limits["engines"].items():
            _require(_LABEL.fullmatch(name) is not None and isinstance(evidence, dict)
                     and set(evidence) == {"identity_sha256", "slots"},
                     "Broker engine limit is invalid")
            _digest(evidence["identity_sha256"], "broker engine identity")
            _nonnegative(evidence["slots"], "broker engine slots")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _validate_state(self, state: dict):
        _require(isinstance(state, dict)
                 and set(state) == {"format", "broker_id", "next_fence", "leases"}
                 and state["format"] == "awf-heavy-validation-broker-state-2"
                 and state["broker_id"] == self.broker_id
                 and isinstance(state["leases"], list),
                 "Durable broker state is invalid")
        _positive(state["next_fence"], "broker next_fence")
        fences, lease_ids = [], []
        expected = {"lease_id", "fencing_token", "expires_at", "parallelism",
                    "resource_class", "resource_claims", "engine", "engine_identity_sha256",
                    "engine_slots", "request_sha256"}
        for item in state["leases"]:
            _require(isinstance(item, dict) and set(item) == expected,
                     "Durable broker lease fields are invalid")
            _digest(item["lease_id"], "broker lease_id")
            _positive(item["fencing_token"], "broker fencing_token")
            timestamp(item["expires_at"])
            _positive(item["parallelism"], "broker lease parallelism")
            _require(item["resource_class"] in {"standard", "heavy", "gpu"},
                     "Durable broker resource class is invalid")
            claims = _resource_slots(item["resource_claims"], "broker lease resources",
                                     positive=True)
            _require(set(claims).issubset(self.limits["resources"]),
                     "Durable broker lease contains unknown resources")
            _require(isinstance(item["engine"], str) and _LABEL.fullmatch(item["engine"]),
                     "Durable broker engine is invalid")
            _digest(item["engine_identity_sha256"], "broker lease engine identity")
            _positive(item["engine_slots"], "broker lease engine slots")
            _require(item["engine_slots"] == item["parallelism"],
                     "Durable broker engine slots do not match parallelism")
            _require(item["engine"] in self.limits["engines"]
                     and item["engine_identity_sha256"]
                     == self.limits["engines"][item["engine"]]["identity_sha256"],
                     "Durable broker engine identity is unknown")
            _digest(item["request_sha256"], "broker request_sha256")
            fences.append(item["fencing_token"])
            lease_ids.append(item["lease_id"])
        _require(len(fences) == len(set(fences)) and len(lease_ids) == len(set(lease_ids)),
                 "Durable broker fences and lease IDs must be unique")
        _require(fences == sorted(fences), "Durable broker fences are not monotonic")
        _require(not fences or state["next_fence"] > max(fences),
                 "Durable broker next_fence is not monotonic")
        _require(sum(item["parallelism"] for item in state["leases"])
                 <= self.limits["max_workers"], "Durable broker worker quantity exceeds limits")
        _require(sum(item["parallelism"] for item in state["leases"]
                     if item["resource_class"] in {"heavy", "gpu"})
                 <= self.limits["max_heavy_jobs"],
                 "Durable broker heavy quantity exceeds limits")
        _require(sum(item["parallelism"] for item in state["leases"]
                     if item["resource_class"] == "gpu") <= self.limits["max_gpu_jobs"],
                 "Durable broker GPU quantity exceeds limits")
        for name, available in self.limits["resources"].items():
            _require(sum(item["resource_claims"].get(name, 0) for item in state["leases"])
                     <= available, f"Durable broker resource {name} exceeds limits")
        for name, evidence in self.limits["engines"].items():
            _require(sum(item["engine_slots"] for item in state["leases"]
                         if item["engine"] == name) <= evidence["slots"],
                     f"Durable broker engine {name} exceeds limits")

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
                try:
                    state = (json.loads(raw.decode("utf-8")) if raw else
                             {"format": "awf-heavy-validation-broker-state-2",
                              "broker_id": self.broker_id, "next_fence": 1, "leases": []})
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValidationError("Durable broker state is invalid") from exc
                self._validate_state(state)
                result = operation(state)
                self._validate_state(state)
                encoded = canonical(state)
                stream.seek(0)
                stream.truncate()
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                _fsync_directory(self.path.parent)
                return result
            finally:
                self._unlock(stream)

    def acquire(self, request: dict) -> dict:
        expected = {"format", "candidate", "plan_sha256", "config_sha256",
                    "capacity_sha256", "parallelism", "resource_class", "engine",
                    "engine_identity_sha256", "engine_slots",
                    "required_resources", "resource_claims", "determinism", "isolation",
                    "required_duration_seconds"}
        _require(isinstance(request, dict) and set(request) == expected
                 and request["format"] == "awf-heavy-validation-lease-request-2",
                 "Lease request fields are invalid")
        _require(isinstance(request["candidate"], dict)
                 and set(request["candidate"]) == {"repository_id", "base_sha", "head_sha", "tree_sha"}
                 and type(request["candidate"]["repository_id"]) is int
                 and request["candidate"]["repository_id"] > 0
                 and all(isinstance(request["candidate"][name], str)
                         and re.fullmatch(r"[0-9a-f]{40}", request["candidate"][name])
                         for name in ("base_sha", "head_sha", "tree_sha")),
                 "Lease candidate is invalid")
        for name in ("plan_sha256", "config_sha256", "capacity_sha256"):
            _digest(request[name], f"lease {name}")
        _require(type(request["parallelism"]) is int and request["parallelism"] > 0,
                 "Lease parallelism is invalid")
        _require(isinstance(request["engine"], str) and _LABEL.fullmatch(request["engine"])
                 and request["engine"] in self.limits["engines"],
                 "Lease engine is invalid")
        _digest(request["engine_identity_sha256"], "lease engine identity")
        _positive(request["engine_slots"], "lease engine slots")
        _require(request["engine_slots"] == request["parallelism"]
                 and request["engine_identity_sha256"]
                 == self.limits["engines"][request["engine"]]["identity_sha256"],
                 "Lease engine identity/slots are invalid")
        _require(request["resource_class"] in {"standard", "heavy", "gpu"}
                 and isinstance(request["required_resources"], list)
                 and len(request["required_resources"]) == len(set(request["required_resources"]))
                 and all(isinstance(name, str) and _LABEL.fullmatch(name)
                         for name in request["required_resources"])
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
                         for value in request["determinism"].values())
                 and request["determinism"]["seed"] <= 2**63 - 1
                 and request["determinism"]["retry_limit"] <= 5,
                 "Lease determinism declaration is invalid")
        duration = _positive(request["required_duration_seconds"],
                             "lease required_duration_seconds")
        _require(duration <= 7 * 24 * 60 * 60,
                 "lease required_duration_seconds exceeds seven days")
        now = self.clock()
        expires = now + timedelta(seconds=duration)
        expires_text = expires.isoformat(timespec="seconds").replace("+00:00", "Z")
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
            engine_use = sum(item["engine_slots"] for item in active
                             if item["engine"] == request["engine"]
                             and item["engine_identity_sha256"]
                             == request["engine_identity_sha256"])
            reasons = []
            if worker_use + requested > self.limits["max_workers"]:
                reasons.append("worker capacity")
            if request["resource_class"] in {"heavy", "gpu"} and class_use + requested > self.limits["max_heavy_jobs"]:
                reasons.append("heavy capacity")
            if request["resource_class"] == "gpu" and gpu_use + requested > self.limits["max_gpu_jobs"]:
                reasons.append("gpu capacity")
            if (engine_use + request["engine_slots"]
                    > self.limits["engines"][request["engine"]]["slots"]):
                reasons.append("engine capacity")
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
                    "expires_at": expires_text, "parallelism": requested,
                    "resource_class": request["resource_class"],
                    "resource_claims": deepcopy(request["resource_claims"]),
                    "engine": request["engine"],
                    "engine_identity_sha256": request["engine_identity_sha256"],
                    "engine_slots": request["engine_slots"],
                    "request_sha256": request_sha}
            state["leases"].append(item)
            return {"status": "GRANTED", "lease_id": lease_id,
                    "fencing_token": fence, "broker_id": self.broker_id,
                    "acquired_at": acquired, "expires_at": expires_text,
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
        "engines": {name: {"identity_sha256": evidence["identity_sha256"],
                            "slots": evidence["parallel_slots"]}
                    for name, evidence in capacity["engines"].items()},
    }


def _receipt_key(value: bytes) -> bytes:
    _require(isinstance(value, bytes) and 32 <= len(value) <= 4096,
             "Result receipt key must contain 32 to 4096 bytes")
    return value


def _fsync_directory(path: Path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x40000000, 0x00000007, None, 3,
                                0x02000000, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), "Directory durability handle failed")
    try:
        if not kernel.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), "Directory durability flush failed")
    finally:
        kernel.CloseHandle(handle)


def _write_durable_exclusive(path: Path, raw: bytes, label: str) -> Path:
    target = Path(path).absolute()
    parent = resolve_without_alias(target.parent, f"{label} parent", directory=True)
    target = parent / target.name
    _require(not target.is_symlink(), f"{label} cannot be a symlink")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(parent)
    except Exception:
        try:
            target.unlink()
            _fsync_directory(parent)
        except OSError:
            pass
        raise
    return target


def write_result_log(path: str | os.PathLike[str], receipt_path: str | os.PathLike[str],
                     result: dict, receipt_key: bytes) -> dict:
    """Durably create a result plus keyed receipt; never overwrite either artifact."""
    key = _receipt_key(receipt_key)
    result_raw = canonical(result)
    envelope = {"format": "awf-heavy-validation-result-log-2",
                "result_sha256": sha256(result_raw), "result": result}
    log_raw = canonical(envelope)
    unsigned = {"format": "awf-heavy-validation-result-receipt-1",
                "log_sha256": sha256(log_raw),
                "result_sha256": envelope["result_sha256"]}
    mac = hmac.new(key, b"awf-heavy-validation-result-receipt-1\0" + canonical(unsigned),
                   hashlib.sha256).hexdigest()
    receipt = {**unsigned, "hmac_sha256": mac}
    receipt_raw = canonical(receipt)
    log_target = _write_durable_exclusive(Path(path), log_raw, "result log")
    try:
        receipt_target = _write_durable_exclusive(Path(receipt_path), receipt_raw,
                                                  "result receipt")
    except Exception:
        try:
            log_target.unlink()
            _fsync_directory(log_target.parent)
        except OSError:
            pass
        raise
    return {"log_path": str(log_target), "receipt_path": str(receipt_target), **receipt}


def read_result_log(path: str | os.PathLike[str], receipt_path: str | os.PathLike[str],
                    receipt_key: bytes) -> dict:
    """Verify and return completed evidence using its controller-held receipt key."""
    key = _receipt_key(receipt_key)
    log_target = resolve_without_alias(Path(path).absolute(), "result log", directory=False)
    receipt_target = resolve_without_alias(Path(receipt_path).absolute(),
                                           "result receipt", directory=False)
    try:
        log_raw = log_target.read_bytes()
        receipt_raw = receipt_target.read_bytes()
        envelope = json.loads(log_raw.decode("utf-8"))
        receipt = json.loads(receipt_raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Result evidence is unreadable") from exc
    _require(isinstance(envelope, dict)
             and set(envelope) == {"format", "result_sha256", "result"}
             and envelope["format"] == "awf-heavy-validation-result-log-2",
             "Result log envelope is invalid")
    _require(isinstance(receipt, dict)
             and set(receipt) == {"format", "log_sha256", "result_sha256", "hmac_sha256"}
             and receipt["format"] == "awf-heavy-validation-result-receipt-1",
             "Result receipt is invalid")
    for name in ("log_sha256", "result_sha256", "hmac_sha256"):
        _digest(receipt[name], f"result receipt {name}")
    result_raw = canonical(envelope["result"])
    _require(envelope["result_sha256"] == sha256(result_raw)
             and receipt["result_sha256"] == envelope["result_sha256"]
             and receipt["log_sha256"] == sha256(log_raw),
             "Result evidence digest mismatch")
    unsigned = {key_name: receipt[key_name] for key_name in
                ("format", "log_sha256", "result_sha256")}
    expected = hmac.new(key, b"awf-heavy-validation-result-receipt-1\0" + canonical(unsigned),
                        hashlib.sha256).hexdigest()
    _require(hmac.compare_digest(receipt["hmac_sha256"], expected),
             "Result receipt authentication failed")
    return {"result": deepcopy(envelope["result"]), "receipt": deepcopy(receipt),
            "log_path": str(log_target), "receipt_path": str(receipt_target)}
