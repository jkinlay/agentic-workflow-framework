"""Reference AWF continuous-controller adapter.

This module is intentionally an adapter, not a controller.  The controller
owns admission, durable state, lifecycle decisions, and human authority.  The
adapter only performs bounded observations or one explicitly requested
external operation and returns the exact receipt shapes consumed by
``agentic.continuous_controller``.

The public factory accepts ordinary JSON configuration.  Tests and operator
qualification may inject ``_runner``, ``_http_transport`` and ``_clock``;
these are process-local hooks and are never part of a shipped JSON config.
Owner-publication operations are deliberately absent: their scan and owner
authorization evidence must be supplied by a separately qualified host.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import quote
from urllib.request import Request, urlopen

from agentic import ValidationError
from agentic.canonical import canonical, fingerprint, now_text, sha256, timestamp


MAX_OUTPUT = 1024 * 1024
MAX_PAGES = 20
MAX_SECONDS = 120
MAX_JIRA_PAGE = 100
_CREDENTIAL_NAMES = {"GH_TOKEN", "GITHUB_TOKEN"}


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _text(value, name, *, maximum=4096):
    _require(isinstance(value, str) and 0 < len(value) <= maximum
             and not any(ord(char) < 32 for char in value), name + " is invalid")
    return value


def _absolute(value, name):
    path = Path(_text(value, name))
    _require(path.is_absolute(), name + " must be absolute")
    return path


class _Clock:
    def now(self):
        return now_text()

    def monotonic(self):
        return time.monotonic()


class _SubprocessRunner:
    """Small default runner; tests replace it with a fake."""

    def __init__(self):
        self._processes = {}

    _WATCHER = r'''
import ctypes
import json
import os
import sys
import tempfile
import time

pid = int(sys.argv[1])
proof = sys.argv[2]
nonce = sys.argv[3]
code = None
if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x00100000 | 0x00000400, False, pid)
    if handle:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        value = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(value)):
            code = int(value.value)
        kernel32.CloseHandle(handle)
else:
    # The reference production host is Windows.  On POSIX a detached sibling
    # cannot prove a foreign child's exit code, so it deliberately emits no
    # terminal proof and the adapter remains fail-closed after a restart.
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
if code is not None:
    value = {"pid": pid, "launch_nonce": nonce, "status": "COMPLETED",
             "returncode": code, "terminal": True}
    directory = os.path.dirname(proof) or "."
    fd, temporary = tempfile.mkstemp(prefix=".awf-terminal-", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, proof)
'''

    def run(self, argv, *, cwd, env, timeout, max_bytes):
        try:
            result = subprocess.run(argv, cwd=str(cwd), env=env, shell=False,
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValidationError("External command failed or timed out") from exc
        if result.returncode != 0:
            raise ValidationError("External command returned a non-zero exit")
        if len(result.stdout) > max_bytes or len(result.stderr) > max_bytes:
            raise ValidationError("External command output exceeded its byte bound")
        try:
            return result.stdout.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError("External command output was not UTF-8") from exc

    def launch(self, argv, *, cwd, env, timeout, stdin, handoff_path, terminal_proof_path,
               launch_nonce):
        try:
            process = subprocess.Popen(argv, cwd=str(cwd), env=env, shell=False,
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       start_new_session=True)
        except OSError as exc:
            raise ValidationError("Codex detached launch failed") from exc
        try:
            process.stdin.write(stdin.encode("utf-8"))
            process.stdin.close()
        except (OSError, BrokenPipeError) as exc:
            process.kill()
            raise ValidationError("Codex prompt could not be delivered") from exc
        self._processes[process.pid] = process
        handoff = {"pid": process.pid, "launch_nonce": launch_nonce, "status": "LAUNCHED"}
        try:
            _write_record(Path(handoff_path), handoff)
            watcher_env = dict(env)
            subprocess.Popen([sys.executable, "-B", "-c", self._WATCHER,
                              str(process.pid), str(terminal_proof_path), launch_nonce],
                             cwd=str(cwd), env=watcher_env, shell=False,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise ValidationError("Codex durable launch handoff failed") from exc
        return {"pid": process.pid, "launch_nonce": launch_nonce,
                "handoff_path": str(handoff_path),
                "terminal_proof_path": str(terminal_proof_path), "status": "LAUNCHED"}

    def observe(self, record, *, timeout):
        pid = record.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise ValidationError("Detached run record has no process identity")
        proof_path = record.get("terminal_proof_path")
        _require(isinstance(proof_path, str), "Detached run has no terminal-proof path")
        proof = Path(proof_path)
        if proof.is_file():
            value = _read_record(proof)
            _require(value.get("pid") == pid and value.get("launch_nonce") == record.get("launch_nonce"),
                     "Detached terminal proof is bound to a different process")
            _require(value.get("status") == "COMPLETED" and value.get("terminal") is True,
                     "Detached Codex terminal outcome was not proven")
            return value
        process = self._processes.get(pid)
        if process is None:
            # A vanished PID is not proof of a successful terminal outcome.
            # After a host restart only the durable watcher proof is accepted.
            raise ValidationError("Detached Codex terminal outcome was not proven")
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("Detached Codex run timed out") from exc
        finally:
            self._processes.pop(pid, None)
        if code != 0:
            raise ValidationError("Detached Codex run returned a non-zero exit")
        value = {"pid": pid, "launch_nonce": record["launch_nonce"],
                 "status": "COMPLETED", "returncode": 0, "terminal": True}
        _write_record(proof, value)
        return value


class _HttpTransport:
    def request(self, method, url, *, headers, body, timeout, max_bytes):
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
                status = response.status
        except Exception as exc:
            raise ValidationError("Jira HTTP request failed or timed out") from exc
        if len(raw) > max_bytes:
            raise ValidationError("Jira response exceeded its byte bound")
        try:
            return status, raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError("Jira response was not UTF-8") from exc


def _invoke(obj, method, *args, **kwargs):
    function = getattr(obj, method, None)
    _require(callable(function), "Injected " + method + " operation is unavailable")
    try:
        return function(*args, **kwargs)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Injected external operation failed or timed out") from exc


def _normalize_command_result(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValidationError("External command output was not UTF-8") from exc
    if isinstance(value, dict):
        if value.get("returncode", 0) != 0:
            raise ValidationError("External command returned a non-zero exit")
        output = value.get("stdout", value.get("output"))
        _require(isinstance(output, (str, bytes)), "External command output is missing")
        return _normalize_command_result(output)
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        if value[0] != 0:
            raise ValidationError("External command returned a non-zero exit")
        return _normalize_command_result(value[1])
    raise ValidationError("External command returned an unparsable result")


def _json_output(value):
    try:
        if isinstance(value, (dict, list)):
            result = value
        else:
            result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("External provider output was not valid JSON") from exc
    _require(isinstance(result, (dict, list)), "External provider output must be a JSON object or array")
    return result


def _clock_text(clock, value=None):
    value = _invoke(clock, "now") if value is None else value
    timestamp(value)
    return value


def _safe_env(jira_token_env=None):
    result = dict(os.environ)
    result.pop("GH_TOKEN", None)
    result.pop("GITHUB_TOKEN", None)
    if jira_token_env:
        result.pop(jira_token_env, None)
    for key in tuple(result):
        if "JIRA" in key.upper() and ("TOKEN" in key.upper() or "PASSWORD" in key.upper()):
            result.pop(key, None)
    result["GH_PROMPT_DISABLED"] = "1"
    result["GIT_TERMINAL_PROMPT"] = "0"
    return result


def _sha256_file(path):
    try:
        digest = sha256(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValidationError("Pinned executable is unreadable") from exc
    return digest


def _executable(value, name):
    _require(isinstance(value, dict) and set(value) == {"path", "sha256"},
             name + " must contain only path and sha256")
    path = _absolute(value["path"], name + ".path")
    _require(re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None,
             name + ".sha256 must be a lowercase SHA-256")
    _require(path.suffix.lower() not in {".cmd", ".bat", ".ps1", ".sh"},
             name + " must pin a native executable")
    return {"path": path, "sha256": value["sha256"]}


def _verify_executable(item):
    _require(item["path"].is_file(), "Pinned executable is missing")
    _require(_sha256_file(item["path"]) == item["sha256"],
             "Pinned executable hash mismatch")


def _read_record(path):
    try:
        raw = path.read_bytes()
        _require(len(raw) <= MAX_OUTPUT, "Detached run record exceeded its byte bound")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("Detached run record is unavailable or invalid") from exc
    _require(isinstance(value, dict), "Detached run record is not an object")
    return value


def _write_record(path, value):
    data = canonical(value)
    _require(len(data) <= MAX_OUTPUT, "Detached run record exceeded its byte bound")
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(data + b"\n")
        os.replace(temporary, path)
    except OSError as exc:
        raise ValidationError("Detached run record could not be made durable") from exc


def _record_receipt(payload, observed_at, *, reconcile=False):
    fields = ["dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
              "prepared_at", "begun_at"]
    if reconcile:
        fields += ["reconcile_nonce", "reconcile_at"]
    result = {key: payload[key] for key in fields}
    result.update(status="ACCEPTED", observed_at=observed_at)
    return result


def _worktree(root, candidate):
    root = root.resolve()
    _require(root.is_absolute() and root.is_dir(), "Declared Codex worktree root is unavailable")
    path = (root if candidate is None else _absolute(candidate, "worktree")).resolve()
    _require(path == root or root in path.parents, "Codex worktree is outside the declared root")
    _require(path.is_dir(), "Codex worktree is unavailable")
    return path


def _validate_ticket_records(items):
    required = {"ticket", "priority", "disposition", "actor", "reason", "next_action",
                "resume_trigger", "paths", "dependencies_satisfied", "budget_available",
                "cap_available", "review_independent", "exact_tuple", "activity",
                "verification_gate", "reviewer_completion", "open_findings", "jira_status"}
    _require(isinstance(items, list) and len(items) <= 1000, "GitHub inventory is not a bounded list")
    result = []
    seen = set()
    for item in items:
        _require(isinstance(item, dict) and set(item) == required,
                 "GitHub inventory contains a malformed continuous-controller ticket")
        _require(item["ticket"] not in seen, "GitHub inventory contains a duplicate ticket")
        seen.add(item["ticket"])
        result.append(item)
    return result


def _page_items(response, *, key, page_size):
    _require(isinstance(response, dict) and set(response) <= {key, "complete", "next_page"},
             "Paged GitHub response has an invalid shape")
    items = response.get(key)
    _require(isinstance(items, list), "Paged GitHub response has no item list")
    complete = response.get("complete")
    next_page = response.get("next_page")
    if complete is True:
        _require(next_page is None, "Complete GitHub response has a next page")
    elif complete is False:
        _require(isinstance(next_page, int) and next_page > 0, "Incomplete GitHub response has no next page")
    else:
        raise ValidationError("GitHub response did not prove completeness")
    return items, complete is True, next_page


def _append_page(path, page_number, page_size):
    path = re.sub(r"([?&])per_page=[^&]*", r"\1", path)
    separator = "&" if "?" in path else "?"
    return path + separator + "page=" + str(page_number) + "&per_page=" + str(page_size)


def _build(config):
    _require(isinstance(config, dict), "Reference adapter configuration must be an object")
    for key in ("codex", "github", "jira", "outbox"):
        _require(isinstance(config.get(key), dict), "Reference adapter configuration is missing " + key)
    codex = config["codex"]
    _require(set(codex) >= {"executable", "roles", "worktree_root", "run_record_directory"},
             "Codex adapter configuration is incomplete")
    codex_executable = _executable(codex["executable"], "codex.executable")
    worktree_root = _absolute(codex["worktree_root"], "codex.worktree_root")
    record_dir = _absolute(codex["run_record_directory"], "codex.run_record_directory")
    _require(record_dir != worktree_root and worktree_root not in record_dir.parents,
             "codex.run_record_directory must be outside codex.worktree_root")
    roles = codex["roles"]
    _require(isinstance(roles, dict) and set(roles) == {"writer", "critic"},
             "Codex roles must define writer and critic")
    role_config = {}
    for role in ("writer", "critic"):
        value = roles[role]
        _require(isinstance(value, dict) and set(value) == {"model", "reasoning_effort", "sandbox"},
                 "Codex role configuration is incomplete")
        model = _text(value["model"], role + ".model")
        effort = _text(value["reasoning_effort"], role + ".reasoning_effort")
        sandbox = _text(value["sandbox"], role + ".sandbox")
        _require(sandbox in {"read-only", "workspace-write"}, role + ".sandbox is forbidden")
        if role == "critic":
            _require(sandbox == "read-only", "Critic sandbox must be read-only")
        role_config[role] = {"model": model, "reasoning_effort": effort, "sandbox": sandbox}
    timeout = codex.get("timeout_seconds", 900)
    _require(type(timeout) is int and 1 <= timeout <= 86400, "Codex timeout is invalid")

    github = config["github"]
    required_github = {"host", "repository", "repository_id", "project_id", "scope_sha256",
                       "base_branch", "branch_pattern", "expected_actor_id", "auth_profile",
                       "executable"}
    _require(required_github <= set(github), "GitHub adapter configuration is incomplete")
    _require(github["host"] == "https://github.com", "Only github.com is supported")
    _require(isinstance(github["repository_id"], int) and github["repository_id"] > 0,
             "GitHub repository_id must be positive")
    _require(isinstance(github["expected_actor_id"], int) and github["expected_actor_id"] > 0,
             "GitHub expected_actor_id must be positive")
    _text(github["auth_profile"], "github.auth_profile")
    _text(github["repository"], "github.repository")
    _text(github["project_id"], "github.project_id")
    _require(re.fullmatch(r"[0-9a-f]{64}", github["scope_sha256"]) is not None,
             "github.scope_sha256 must be a lowercase SHA-256")
    _text(github["base_branch"], "github.base_branch")
    _text(github["branch_pattern"], "github.branch_pattern")
    gh_executable = _executable(github["executable"], "github.executable")
    gh_page_size = github.get("page_size", 100)
    _require(type(gh_page_size) is int and 1 <= gh_page_size <= 100, "GitHub page_size is invalid")

    jira = config["jira"]
    required_jira = {"enabled", "cloud_id", "site", "provider_project_id", "project_key",
                     "controller_actor_id", "token_env", "merged_status_id", "merged_transition_id"}
    _require(required_jira <= set(jira), "Jira adapter configuration is incomplete")
    _require(type(jira["enabled"]) is bool, "jira.enabled must be boolean")
    for key in ("cloud_id", "site", "provider_project_id", "project_key", "controller_actor_id",
                "merged_status_id", "merged_transition_id"):
        _text(jira[key], "jira." + key)
    token_env = jira["token_env"]
    _require(isinstance(token_env, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", token_env)
             and token_env not in {"GH_TOKEN", "GITHUB_TOKEN"}, "jira.token_env is invalid")
    site = jira["site"].rstrip("/")
    _require(site.startswith("https://") and "@" not in site, "jira.site must be a credential-free HTTPS URL")
    outbox = config["outbox"]
    _require(set(outbox) == {"directory"}, "outbox configuration must contain only directory")
    outbox_dir = _absolute(outbox["directory"], "outbox.directory")
    _require(outbox_dir != worktree_root and worktree_root not in outbox_dir.parents,
             "outbox.directory must be outside codex.worktree_root")

    runner = config.get("_runner") or _SubprocessRunner()
    http = config.get("_http_transport") or _HttpTransport()
    clock = config.get("_clock") or _Clock()
    _require(callable(getattr(clock, "now", None)) and callable(getattr(clock, "monotonic", None)),
             "Clock injection is incomplete")

    def command(executable, args, *, cwd=None, stdin=None):
        _verify_executable(executable)
        argv = [str(executable["path"]), *[str(item) for item in args]]
        _require(all(isinstance(item, str) and item for item in argv), "External argv is invalid")
        env = _safe_env(token_env)
        if stdin is not None:
            # gh does not need stdin; this hook exists for fake runners and is
            # intentionally not passed to the shell because there is no shell.
            env["AWF_ADAPTER_STDIN_SHA256"] = hashlib.sha256(stdin.encode("utf-8")).hexdigest()
        result = _invoke(runner, "run", argv, cwd=cwd or worktree_root,
                         env=env, timeout=timeout, max_bytes=MAX_OUTPUT)
        if isinstance(result, (dict, list)):
            return result
        return _normalize_command_result(result)

    def gh_json(path, *, paginate=False):
        _text(path, "GitHub API path", maximum=2048)
        _require(".." not in path and not path.startswith("http"), "GitHub API path is unsafe")
        args = ["api", "--hostname", "github.com", "--method", "GET"]
        if paginate:
            args += ["--paginate", "--slurp"]
        output = command(gh_executable, [*args, path])
        return _json_output(output)

    def github_pages(endpoint, key):
        endpoint = _text(endpoint, "GitHub endpoint", maximum=2048)
        started = _invoke(clock, "monotonic")
        values, page = [], 1
        for _ in range(MAX_PAGES):
            _require(_invoke(clock, "monotonic") - started <= MAX_SECONDS,
                     "GitHub pagination exceeded its time bound")
            response = gh_json(_append_page(endpoint, page, gh_page_size), paginate=True)
            if isinstance(response, list):
                # gh --paginate --slurp is provider pagination evidence: the
                # CLI followed every Link header and returned one JSON value
                # per page.  A bare provider-default list is deliberately not
                # accepted because its page size is unknown.
                _require(all(isinstance(part, list) for part in response),
                         "GitHub response did not include pagination evidence")
                values = []
                for part in response:
                    values.extend(part)
                _require(len(values) <= 10000, "GitHub observation exceeded its item bound")
                return values
            items, complete, next_page = _page_items(response, key=key, page_size=gh_page_size)
            values.extend(items)
            _require(len(values) <= 10000, "GitHub observation exceeded its item bound")
            if complete:
                return values
            page = next_page or page + 1
        raise ValidationError("GitHub pagination did not prove completeness")

    def observe_inventory(controller_now=None):
        repository = gh_json("repos/" + github["repository"])
        _require(type(repository.get("id")) is int and repository["id"] == github["repository_id"]
                 and repository.get("full_name", "").casefold() == github["repository"].casefold(),
                 "GitHub inventory repository identity mismatch")
        records = github_pages(github.get("inventory_endpoint", "repos/" + github["repository"] + "/issues?state=open"), "items")
        # A GitHub issue body may carry the controller ticket record.  This
        # keeps provider inventory transport-specific while validating the
        # controller's exact record at the adapter boundary.
        tickets = []
        for record in records:
            if isinstance(record, dict) and set(record) == {"ticket", "priority", "disposition", "actor", "reason", "next_action", "resume_trigger", "paths", "dependencies_satisfied", "budget_available", "cap_available", "review_independent", "exact_tuple", "activity", "verification_gate", "reviewer_completion", "open_findings", "jira_status"}:
                tickets.append(record)
            else:
                _require(isinstance(record, dict) and isinstance(record.get("body"), str),
                         "GitHub inventory issue has no canonical ticket body")
                try:
                    tickets.append(json.loads(record["body"]))
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValidationError("GitHub inventory issue body is not canonical JSON") from exc
        tickets = _validate_ticket_records(tickets)
        binding = {"project_id": github["project_id"], "repository_id": str(github["repository_id"]),
                   "scope_sha256": github["scope_sha256"]}
        observed_at = _clock_text(clock, controller_now)
        return {"source": "host_observation", "observed_at": observed_at,
                "binding": binding, "complete": True,
                "inventory_sha256": fingerprint("controller-inventory", {"binding": binding, "tickets": tickets}),
                "tickets": tickets}

    def observe_publication(item):
        _require(isinstance(item, dict) and isinstance(item.get("ticket"), str),
                 "Publication observation needs a ticket")
        ticket = item["ticket"]
        slug = re.sub(r"[^a-z0-9]+", "-", ticket.lower()).strip("-")
        branch = github["branch_pattern"].format(ticket=ticket, slug=slug)
        repo = gh_json("repos/" + github["repository"])
        actor = gh_json("user")
        permission = gh_json("repos/" + github["repository"] + "/collaborators/" + quote(str(actor.get("login")), safe="") + "/permission")
        auth = _json_output(command(gh_executable, ["auth", "status", "--hostname", "github.com", "--json", "hosts"]))
        hosts = auth.get("hosts") if isinstance(auth, dict) else None
        _require(isinstance(hosts, dict) and isinstance(hosts.get("github.com"), list),
                 "GitHub authentication profile observation is incomplete")
        active = [entry for entry in hosts["github.com"]
                  if isinstance(entry, dict) and entry.get("active") is True]
        _require(len(active) == 1, "GitHub authentication profile is not uniquely observed")
        auth_profile = active[0].get("profile") or active[0].get("login")
        _require(isinstance(auth_profile, str) and auth_profile,
                 "GitHub authentication profile is missing")
        rules_response = gh_json(github.get(
            "rules_endpoint", "repos/" + github["repository"] + "/rulesets?includes_parents=true"),
            paginate=True)
        if isinstance(rules_response, list) and all(isinstance(part, list) for part in rules_response):
            rulesets = [rule for part in rules_response for rule in part]
        elif isinstance(rules_response, dict) and isinstance(rules_response.get("items"), list):
            rulesets = rules_response["items"]
        else:
            raise ValidationError("GitHub rules response did not prove completeness")
        _require(type(repo.get("id")) is int and repo["id"] == github["repository_id"]
                 and repo.get("full_name", "").casefold() == github["repository"].casefold(),
                 "GitHub publication repository identity mismatch")
        _require(type(actor.get("id")) is int and isinstance(actor.get("login"), str),
                 "GitHub actor observation is incomplete")
        permission_name = permission.get("permission")
        allowed = permission_name in {"push", "maintain", "admin"}
        ref = "refs/heads/" + branch
        applicable = []
        for ruleset in rulesets:
            if not isinstance(ruleset, dict) or ruleset.get("enforcement") != "active":
                continue
            conditions = ruleset.get("conditions", {}).get("ref_name", {})
            includes = conditions.get("include", []) if isinstance(conditions, dict) else []
            excludes = conditions.get("exclude", []) if isinstance(conditions, dict) else []
            _require(isinstance(includes, list) and isinstance(excludes, list),
                     "GitHub ruleset ref conditions are malformed")
            matches = any(pattern == "~ALL"
                          or (pattern == "~DEFAULT_BRANCH" and ref == "refs/heads/" + github["base_branch"])
                          or pattern in {branch, ref}
                          or __import__("fnmatch").fnmatchcase(ref, pattern)
                          or __import__("fnmatch").fnmatchcase(branch, pattern)
                          for pattern in includes if isinstance(pattern, str))
            excluded = any(__import__("fnmatch").fnmatchcase(ref, pattern)
                           or __import__("fnmatch").fnmatchcase(branch, pattern)
                           for pattern in excludes if isinstance(pattern, str))
            if matches and not excluded:
                applicable.append(ruleset)
        rules_state = "ALLOWED" if applicable else "UNOBSERVED"
        rules_evidence = ("Observed active GitHub ruleset(s) applicable to prospective " + ref
                          if applicable else "No complete applicable ruleset observation for prospective " + ref)
        observed_at = _clock_text(clock)
        identity = {"actor_id": actor["id"], "actor_login": actor["login"],
                    "auth_profile": auth_profile, "repository_id": repo["id"],
                    "repository": github["repository"], "readable": True,
                    "protocol": github.get("protocol", "https"), "observed_at": observed_at}
        return {"format": "awf-publication-readiness-1", "source": "host_observation",
                "host": github["host"], "observed_at": observed_at, "ticket": ticket,
                "slug": slug, "branch": branch, "identity": identity,
                "authenticated": True, "remote_reachable": True,
                "rules": {"state": rules_state, "evidence": rules_evidence},
                "push_permitted": allowed, "draft_pr_permitted": allowed}

    def dispatch_ticket(payload):
        _require(isinstance(payload, dict), "Dispatch payload is missing")
        required = {"dispatch_id", "stream", "ticket", "exact_tuple", "dispatch_nonce",
                    "prepared_at", "begun_at", "paths", "actor", "next_action"}
        _require(required <= set(payload), "Dispatch payload is incomplete")
        role = payload.get("role", "writer")
        _require(role in role_config, "Dispatch role is not configured")
        worktree = _worktree(worktree_root, payload.get("worktree"))
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / (payload["dispatch_id"] + ".json")
        if record_path.exists():
            raise ValidationError("Dispatch already has a durable run record; reconcile by observation")
        selected = role_config[role]
        sandbox = "read-only" if role == "critic" else selected["sandbox"]
        prompt = ("AWF continuous-controller role=" + role + "\nTicket: " + payload["ticket"]
                  + "\nAction: " + payload["next_action"] + "\nPaths: "
                  + ",".join(payload["paths"]))
        _require(len(prompt.encode("utf-8")) <= MAX_OUTPUT,
                 "Codex task prompt exceeded its byte bound")
        argv = ["exec", "--ephemeral", "--ignore-user-config", "--sandbox", sandbox,
                "-c", 'approval_policy="never"', "-c",
                "model_reasoning_effort=" + selected["reasoning_effort"], "--model", selected["model"],
                "--cd", str(worktree), "--json", "-"]
        launch_nonce = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                      "awf:codex-launch:" + payload["dispatch_nonce"]))
        handoff_path = record_path.with_suffix(".handoff.json")
        terminal_proof_path = record_path.with_suffix(".terminal.json")
        record = {"format": "awf-reference-dispatch-1", "dispatch_id": payload["dispatch_id"],
                  "dispatch_nonce": payload["dispatch_nonce"], "stream": payload["stream"],
                  "ticket": payload["ticket"], "prepared_at": payload["prepared_at"],
                  "begun_at": payload["begun_at"], "role": role, "argv": argv,
                  "worktree": str(worktree), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                  "argv_sha256": hashlib.sha256(canonical(argv)).hexdigest(),
                  "launch_nonce": launch_nonce, "handoff_path": str(handoff_path),
                  "terminal_proof_path": str(terminal_proof_path),
                  "status": "PREPARED", "created_at": _clock_text(clock)}
        _write_record(record_path, record)
        _verify_executable(codex_executable)
        launch_env = _safe_env(token_env)
        launch_env["AWF_DISPATCH_NONCE"] = launch_nonce
        launched = _invoke(runner, "launch", [str(codex_executable["path"]), *argv],
                           cwd=worktree, env=launch_env, timeout=timeout,
                           stdin=prompt, handoff_path=str(handoff_path),
                           terminal_proof_path=str(terminal_proof_path),
                           launch_nonce=launch_nonce)
        _require(isinstance(launched, dict), "Codex launch returned an invalid result")
        _require(launched.get("status") == "LAUNCHED" and
                 type(launched.get("pid")) is int and launched["pid"] > 0,
                 "Codex launch did not return a durable process identity")
        _require(launched.get("launch_nonce") == launch_nonce,
                 "Codex launch identity is not nonce-bound")
        _require(launched.get("handoff_path") == str(handoff_path) and
                 launched.get("terminal_proof_path") == str(terminal_proof_path),
                 "Codex launch did not return durable proof paths")
        record.update({"status": "LAUNCHED", "pid": launched["pid"],
                       "launched_at": _clock_text(clock)})
        _write_record(record_path, record)
        return _record_receipt(payload, record["launched_at"])

    def observe_dispatch(payload):
        _require(isinstance(payload, dict) and isinstance(payload.get("dispatch_id"), str),
                 "Dispatch observation payload is missing")
        path = record_dir / (payload["dispatch_id"] + ".json")
        record = _read_record(path)
        _require(record.get("dispatch_id") == payload["dispatch_id"]
                 and record.get("dispatch_nonce") == payload.get("dispatch_nonce"),
                 "Dispatch observation is bound to a different nonce")
        _require(record.get("ticket") == payload.get("ticket") and record.get("stream") == payload.get("stream"),
                 "Dispatch observation identity differs from durable record")
        reconcile = "reconcile_nonce" in payload
        if reconcile:
            _require(record.get("status") in {"PREPARED", "LAUNCHED", "COMPLETED"},
                     "Interrupted dispatch has no observable durable launch intent")
            if record.get("status") == "PREPARED":
                handoff = _read_record(Path(record["handoff_path"]))
                _require(handoff.get("launch_nonce") == record["launch_nonce"] and
                         type(handoff.get("pid")) is int and handoff["pid"] > 0,
                         "Interrupted dispatch has no durable launch handoff")
                record.update({"pid": handoff["pid"], "status": "LAUNCHED"})
                _write_record(path, record)
            result = _invoke(runner, "observe", record, timeout=timeout)
            _require(isinstance(result, dict) and result.get("status") == "COMPLETED"
                     and result.get("returncode") == 0 and result.get("terminal") is True,
                     "Interrupted dispatch terminal outcome was not proven by observation")
            record["status"] = "COMPLETED"
            record["observed_at"] = _clock_text(clock)
            _write_record(path, record)
        else:
            _require(record.get("status") in {"LAUNCHED", "COMPLETED"},
                     "Dispatch has no accepted durable launch")
        return _record_receipt(payload, _clock_text(clock), reconcile=reconcile)

    def deliver_status(digest):
        _require(isinstance(digest, dict) and isinstance(digest.get("delivery_id"), str),
                 "Status digest is missing its delivery identity")
        outbox_dir.mkdir(parents=True, exist_ok=True)
        path = outbox_dir / "controller-status.jsonl"
        delivery_id = digest["delivery_id"]
        found = None
        try:
            with path.open("a+", encoding="utf-8", newline="\n") as stream:
                stream.seek(0)
                for line in stream:
                    if line.strip():
                        row = json.loads(line)
                        if row.get("delivery_id") == delivery_id:
                            found = row
                            break
                if found is None:
                    row = {"delivery_id": delivery_id, "digest_sha256": fingerprint("controller-delivery", digest),
                           "digest": digest, "observed_at": _clock_text(clock)}
                    stream.seek(0, 2)
                    position = stream.tell()
                    stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                    stream.flush()
                    stream.seek(position)
                    encoded = stream.readline()
                    found = json.loads(encoded)
                    _require(found == row, "Status outbox readback differs from the appended record")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError("Status outbox write or readback failed") from exc
        _require(found.get("digest_sha256") == fingerprint("controller-delivery", digest),
                 "Status outbox readback differs from the requested digest")
        return {"delivery_id": delivery_id, "status": "DELIVERED", "observed_at": found["observed_at"]}

    def jira_token():
        token = os.environ.get(token_env)
        _require(isinstance(token, str) and token, "Configured Jira credential environment variable is absent")
        return token

    def jira_json(method, path, body=None):
        _require(path.startswith("/"), "Jira API path must be absolute")
        token = jira_token()
        headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
        data = None if body is None else canonical(body)
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            result = _invoke(http, "request", method, site + path, headers=headers,
                             body=data, timeout=jira.get("timeout_seconds", 30), max_bytes=MAX_OUTPUT)
        finally:
            del token
        if isinstance(result, tuple) and len(result) == 2:
            status, output = result
        elif isinstance(result, dict):
            status, output = result.get("status"), result.get("body", result.get("output", ""))
        else:
            raise ValidationError("Jira transport returned an unparsable result")
        _require(type(status) is int and 200 <= status < 300, "Jira returned a non-success status")
        if output in (None, "", b""):
            return {}
        return _json_output(output)

    def provider_identity():
        myself = jira_json("GET", "/rest/api/3/myself")
        project = jira_json("GET", "/rest/api/3/project/" + quote(jira["project_key"], safe=""))
        tenant = jira_json("GET", "/_edge/tenant_info")
        observed_cloud = tenant.get("cloudId")
        observed_site = tenant.get("siteUrl")
        _require(myself.get("accountId") == jira["controller_actor_id"]
                 and str(project.get("id")) == jira["provider_project_id"]
                 and project.get("key") == jira["project_key"]
                 and observed_cloud == jira["cloud_id"]
                 and isinstance(observed_site, str) and observed_site.rstrip("/") == site,
                 "Jira provider identity mismatch")
        return {"cloud_id": observed_cloud, "site": observed_site.rstrip("/"),
                "project_id": str(project["id"]), "project_key": project["key"],
                "controller_actor_id": jira["controller_actor_id"]}

    def read_current_status(binding):
        _require(isinstance(binding, dict) and isinstance(binding.get("issue_id"), str),
                 "Jira status read needs an issue identity")
        value = jira_json("GET", "/rest/api/3/issue/" + quote(binding["issue_id"], safe="") + "?fields=status")
        status_id = value.get("fields", {}).get("status", {}).get("id")
        observed_at = _clock_text(clock)
        provider = binding.get("jira_provider") or provider_identity()
        _require(isinstance(status_id, str) and status_id, "Jira issue status is missing")
        return {"issue_id": binding["issue_id"], "status_id": status_id,
                "observed_at": observed_at, "jira_provider": provider}

    def write_transition(record):
        _require(isinstance(record, dict) and isinstance(record.get("binding"), dict),
                 "Jira transition record is missing its binding")
        issue_id = record["binding"]["issue_id"]
        _require(isinstance(record.get("transition_id"), str) and record["transition_id"],
                 "Jira transition identity is missing")
        jira_json("POST", "/rest/api/3/issue/" + quote(issue_id, safe="") + "/transitions",
                  {"transition": {"id": record["transition_id"]}})
        return {"operation_id": record["operation_id"], "issue_id": issue_id,
                "status": "ATTEMPTED", "observed_at": _clock_text(clock),
                "jira_provider": record["jira_provider"]}

    def read_transition(record, operation):
        issue_id = record["binding"]["issue_id"]
        value = jira_json("GET", "/rest/api/3/issue/" + quote(issue_id, safe="")
                           + "?fields=status&expand=changelog")
        status = value.get("fields", {}).get("status", {}).get("id")
        histories = value.get("changelog", {}).get("histories")
        _require(isinstance(status, str) and status and isinstance(histories, list),
                 "Jira transition history is unavailable")
        candidates = []
        for history in histories:
            if not isinstance(history, dict) or not isinstance(history.get("created"), str):
                continue
            author = history.get("author", {})
            actor = author.get("accountId") if isinstance(author, dict) else None
            for item in history.get("items", []):
                if not isinstance(item, dict) or item.get("field") != "status":
                    continue
                if item.get("to") == record.get("to_status_id") or item.get("toString") == status:
                    if isinstance(actor, str) and actor:
                        candidates.append((history["created"], actor))
        _require(candidates, "Jira transition actor was not independently observed")
        candidates.sort(key=lambda item: item[0])
        _created, actor = candidates[-1]
        observed_at = _clock_text(clock)
        return {"issue_id": issue_id, "status": status, "actor": actor,
                "observed_at": observed_at, "jira_provider": record["jira_provider"]}

    def reconcile_merged_ticket(ticket):
        _text(ticket, "Merged Jira ticket")
        provider = provider_identity()
        before = read_current_status({"issue_id": ticket, "jira_provider": provider})
        target = jira["merged_status_id"]
        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "awf:jira-merge:" + jira["cloud_id"] + ":" + ticket))
        if before["status_id"] != target:
            record = {"operation_id": operation_id, "binding": {"issue_id": ticket},
                      "transition_id": jira["merged_transition_id"], "to_status_id": target,
                      "jira_provider": provider}
            write_transition({**record, "producer_id": provider["controller_actor_id"]})
            after = read_transition({**record, "producer_id": provider["controller_actor_id"]}, {})
        else:
            after = {"issue_id": ticket, "status": target, "actor": provider["controller_actor_id"],
                     "observed_at": before["observed_at"], "jira_provider": provider}
        _require(after.get("actor") == provider["controller_actor_id"],
                 "Jira merge transition actor was not independently observed")
        _require(after["status"] == target, "Jira merge readback did not reach the configured terminal status")
        return {"ticket": ticket, "issue_id": ticket, "cloud_id": provider["cloud_id"],
                "project_id": provider["project_id"], "actor_id": provider["controller_actor_id"],
                "status": "RECONCILED", "operation_id": operation_id,
                "before_status_id": before["status_id"], "after_status_id": after["status"],
                "observed_at": after["observed_at"]}

    scope_snapshots = {}

    def fetch_scope_page(scope, cursor):
        _text(scope, "Jira scope", maximum=4096)
        observed_provider = provider_identity()
        provider = {"cloud_id": observed_provider["cloud_id"],
                    "project_id": observed_provider["project_id"],
                    "actor_id": observed_provider["controller_actor_id"]}
        scope_sha = fingerprint("jira-progress-scope", {"scope": scope, "binding": provider, "include_epics": False})
        start = 0 if cursor is None else int(cursor)
        _require(start >= 0, "Jira scope cursor is invalid")
        page_size = min(jira.get("page_size", MAX_JIRA_PAGE), MAX_JIRA_PAGE)
        snapshot = scope_snapshots.get(scope)
        if snapshot is None:
            _require(cursor is None, "Jira scope continuation has no retained snapshot")
            observed_at = _clock_text(clock)
            snapshot = {"observed_at": observed_at}
            scope_snapshots[scope] = snapshot
        bounded_scope = "(" + scope + ') AND updated <= "' + snapshot["observed_at"] + '"'
        result = jira_json("GET", "/rest/api/3/search?jql=" + quote(bounded_scope, safe="")
                           + "&startAt=" + str(start) + "&maxResults=" + str(page_size))
        issues = result.get("issues")
        total = result.get("total")
        _require(isinstance(issues, list) and type(total) is int and total >= 0,
                 "Jira scope response is incomplete")
        if "total" in snapshot:
            _require(total == snapshot["total"], "Jira scope total changed during pagination")
        else:
            snapshot["total"] = total
        snapshot_id = fingerprint("jira-search-snapshot", {"scope": scope, "binding": provider,
                                                            "observed_at": snapshot["observed_at"],
                                                            "total": total})
        items = []
        for issue in issues:
            fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
            issue_type = str(fields.get("issuetype", {}).get("name", "TASK")).upper()
            category = str(fields.get("status", {}).get("statusCategory", {}).get("key", "")).lower()
            _require(isinstance(issue.get("id"), str) and category in {"done", "new", "indeterminate"},
                     "Jira scope item is incomplete")
            items.append({"id": issue["id"], "issue_type": "EPIC" if issue_type == "EPIC" else issue_type,
                          "status_category": "TERMINAL" if category == "done" else "NON_TERMINAL"})
        next_cursor = None if start + len(issues) >= total else str(start + len(issues))
        complete = next_cursor is None
        observed_at = snapshot["observed_at"]
        return {"items": items, "next_cursor": next_cursor, "complete": complete,
                "snapshot_id": snapshot_id, "scope_sha256": scope_sha,
                "observed_at": observed_at}

    return {"observe_inventory": observe_inventory, "dispatch_ticket": dispatch_ticket,
            "observe_dispatch": observe_dispatch, "deliver_status": deliver_status,
            "observe_publication": observe_publication,
            "observe_provider_identity": provider_identity,
            "read_current_status": read_current_status, "write_transition": write_transition,
            "read_transition": read_transition, "reconcile_merged_ticket": reconcile_merged_ticket,
            "fetch_scope_page": fetch_scope_page}


def build_adapters(config):
    """Return the reviewed cycle/Jira/merge operations for the controller."""
    return _build(config)
