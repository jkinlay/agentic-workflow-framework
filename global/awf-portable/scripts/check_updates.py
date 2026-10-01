#!/usr/bin/env python3
"""Read an owner-configured AWF release announcement channel; never install or fetch archives."""
from __future__ import annotations

import argparse
import importlib.util
import http.client
import json
from pathlib import Path
import queue
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("awf_update_locator", SCRIPT.with_name("awf.py"))
awf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(awf)

FORMAT = "awf-update-check-1"
MAX_CHANNEL_BYTES = 1024 * 1024
MAX_RELEASES = 1000
HTTPS_TIMEOUT_SECONDS = 10
READ_CHUNK_BYTES = 64 * 1024


def _version(value):
    if not isinstance(value, str) or not awf.VERSION.fullmatch(value):
        awf.fail("INVALID_VERSION", "Release versions must be numeric major.minor.patch without prerelease labels")
    return tuple(int(part) for part in value.split("."))


def _selector(value):
    if value is not None and (not isinstance(value, str) or not awf.SELECTOR.fullmatch(value)):
        awf.fail("INVALID_VERSION_SELECTOR", "Use a major.minor family or exact major.minor.patch version")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Channel redirects are not followed; configure the intended trusted endpoint explicitly", headers, fp)


def _https_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or \
            parsed.password is not None or parsed.query or parsed.fragment or \
            any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        awf.fail("UNSAFE_CHANNEL_URL", "Channel URL must be HTTPS without credentials, query, fragment or ambiguous characters")
    try:
        port = parsed.port
    except ValueError as exc:
        raise awf.DiscoveryError("UNSAFE_CHANNEL_URL", "Channel URL has an invalid port") from exc
    if port is not None and port == 0:
        awf.fail("UNSAFE_CHANNEL_URL", "Channel URL port must be positive")
    decoded = urllib.parse.unquote(parsed.path)
    if any(part in {".", ".."} for part in decoded.split("/")) or "\\" in decoded:
        awf.fail("UNSAFE_CHANNEL_URL", "Channel URL must not contain path traversal")
    return parsed


def _read_https(url):
    _https_url(url)
    # No ambient proxy credentials, cookies, auth handlers or redirected trust source.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AWF-update-check/1"})
    deadline = time.monotonic() + HTTPS_TIMEOUT_SECONDS
    cancelled = threading.Event()
    completed = queue.Queue(maxsize=1)

    def expired():
        if cancelled.is_set() or time.monotonic() >= deadline:
            awf.fail("CHANNEL_TIMEOUT", "Channel retrieval exceeded the caller deadline")

    def fetch():
        try:
            expired()
            with opener.open(request, timeout=HTTPS_TIMEOUT_SECONDS) as response:
                expired()
                if response.geturl() != url:
                    awf.fail("CHANNEL_SOURCE_CHANGED", "Response endpoint differs from the configured channel")
                chunks = []
                size = 0
                read = getattr(response, "read1", response.read)
                while True:
                    expired()
                    chunk = read(min(READ_CHUNK_BYTES, MAX_CHANNEL_BYTES + 1 - size))
                    expired()
                    if not isinstance(chunk, bytes):
                        awf.fail("INVALID_CHANNEL_RESPONSE", "Channel response must contain bytes")
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_CHANNEL_BYTES:
                        awf.fail("CHANNEL_TOO_LARGE", "Channel exceeds the 1 MiB limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
            completed.put_nowait((True, raw))
        except Exception as exc:
            completed.put_nowait((False, exc))

    # DNS/headers or a buffered transport can outlive a caller deadline. Never
    # synchronously join/close that worker on timeout; it cannot change our result.
    # Cancellation prevents subsequent reads when the pending transport returns.
    threading.Thread(target=fetch, name="awf-channel-read", daemon=True).start()
    try:
        ok, value = completed.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        cancelled.set()
        awf.fail("CHANNEL_TIMEOUT", "Channel retrieval exceeded the caller deadline; current installation is unchanged")
    if time.monotonic() >= deadline:
        cancelled.set()
        awf.fail("CHANNEL_TIMEOUT", "Channel retrieval completed after the caller deadline")
    if not ok:
        raise value
    return value


def _archive_reference(value):
    awf.relative_path(value)
    if any(char in value for char in "?#%"):
        awf.fail("UNSAFE_ARCHIVE_REFERENCE", "Archive must be a relative path without query, fragment or percent escapes")
    return value


def _read_channel(source):
    if not isinstance(source, (str, Path)) or not str(source):
        awf.fail("INVALID_CHANNEL", "Channel must be an absolute local JSON path or HTTPS URL")
    original = str(source)
    remote = original.lower().startswith("https://")
    if remote:
        parsed = _https_url(original)
        raw = _read_https(original)
        base_path = parsed.path.rsplit("/", 1)[0] + "/"
        base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))
        resolve = lambda name: urllib.parse.urljoin(base, urllib.parse.quote(name, safe="/"))
    else:
        if "://" in original:
            awf.fail("UNSAFE_CHANNEL_URL", "Only HTTPS remote channels are supported")
        path = awf.absolute_path(original, "Local channel")
        raw = awf.read_file(path, limit=MAX_CHANNEL_BYTES)
        resolve = lambda name: str(path.parent.joinpath(*name.split("/")))
    value = awf.parse_json(raw, "update channel")
    if not isinstance(value, dict) or set(value) != {"format", "releases"} or value["format"] != "awf-update-channel-1":
        awf.fail("INVALID_CHANNEL", "Expected exact awf-update-channel-1 fields: format and releases")
    releases = value["releases"]
    if not isinstance(releases, list) or not releases or len(releases) > MAX_RELEASES:
        awf.fail("INVALID_CHANNEL", "Channel must announce between 1 and 1000 releases")
    identities = set()
    candidates = []
    required = {"version", "archive", "archive_sha256", "manifest_sha256"}
    for entry in releases:
        if not isinstance(entry, dict) or set(entry) != required:
            awf.fail("INVALID_CHANNEL", "Release announcement fields differ from the supported contract")
        _version(entry["version"])
        if entry["version"] in identities:
            awf.fail("INVALID_CHANNEL", f"Duplicate announced version: {entry['version']}")
        identities.add(entry["version"])
        reference = _archive_reference(entry["archive"])
        for key in ("archive_sha256", "manifest_sha256"):
            if not isinstance(entry[key], str) or not awf.HEX.fullmatch(entry[key]):
                awf.fail("INVALID_CHANNEL", f"Announced {key} must be lowercase SHA-256")
        candidates.append({**entry, "archive_reference": reference, "archive": resolve(reference),
                           "announcement_only": True})
    candidates.sort(key=lambda entry: _version(entry["version"]))
    channel = {"source": original, "kind": "https" if remote else "local", "sha256": awf.digest(raw),
               "release_count": len(candidates), "available_versions": [entry["version"] for entry in candidates],
               "publisher_authenticated": False, "announcement_only": True,
               "trust_note": "The configured channel is an owner-supplied announcement source, not an archive verification or publisher signature."}
    return channel, candidates


def _optional_file(project, relative):
    path = project / relative
    present = awf.inspect_path(path, directory=False, missing_ok=True) is not None
    raw = awf.read_file(path) if present else None
    return raw, {"path": str(path), "exists": present, "sha256": awf.digest(raw) if present else None}


def _installed(project):
    awf.inspect_path(project, directory=True, missing_ok=True)
    raw = {}
    observed = {}
    for relative in (awf.RECEIPT, awf.VERSION_FILE, awf.CONFIG,
                     ".agentic/INSTALLING.json", ".agentic-install/journal.json"):
        raw[relative], observed[relative] = _optional_file(project, relative)
    result = {"status": "NOT_INSTALLED", "version": None, "manifest_sha256": None,
              "install_id": None, "observed_files": observed, "authenticated": False,
              "immutable_files_verified": False, "configuration_validated": False}
    if raw[".agentic/INSTALLING.json"] is not None or raw[".agentic-install/journal.json"] is not None:
        return {**result, "status": "INCONSISTENT", "issue": "An installation/recovery marker is present"}
    if raw[awf.RECEIPT] is None and raw[awf.VERSION_FILE] is None and raw[awf.CONFIG] is None:
        return result
    if raw[awf.RECEIPT] is None or raw[awf.VERSION_FILE] is None:
        return {**result, "status": "INCONSISTENT", "issue": "AWF files lack a complete receipt and matching version record"}
    try:
        receipt = awf.parse_json(raw[awf.RECEIPT], "installed receipt")
        provenance = awf.parse_json(raw[awf.VERSION_FILE], "installed version record")
        if not isinstance(receipt, dict) or not isinstance(provenance, dict) or \
                not isinstance(provenance.get("template"), dict) or not isinstance(provenance.get("installation"), dict):
            awf.fail("INVALID_INSTALLED_STATE", "Installed receipt/version record must contain the supported identity objects")
        version = receipt.get("template_version")
        _version(version)
        manifest = receipt.get("source_manifest_sha256")
        identity = receipt.get("install_id")
        if not isinstance(manifest, str) or not awf.HEX.fullmatch(manifest) or \
                not isinstance(identity, str) or not identity.strip() or len(identity) > 256 or \
                any(ord(char) < 32 for char in identity):
            awf.fail("INVALID_INSTALLED_STATE", "Installed identity needs a valid manifest SHA-256 and nonempty install ID")
        result.update(version=version, manifest_sha256=manifest, install_id=identity)
        if provenance["template"].get("version") != version or \
                provenance["installation"].get("source_manifest_sha256") != manifest or \
                provenance["installation"].get("install_id") != identity:
            awf.fail("INVALID_INSTALLED_STATE", "Installed receipt and version record disagree")
        result.update(status="OBSERVED_CONSISTENT",
                      note="Local identity records agree, but their installed files and source identity have not been independently authenticated.")
    except awf.DiscoveryError as exc:
        result.update(status="INCONSISTENT", issue=str(exc))
    return result


def _source_for_report(source):
    if source is None:
        return None
    value = str(source)
    if "://" not in value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        return "<invalid channel URL>"


def check_updates(project_path, channel_source=None, requested_version=None):
    context = {"format": FORMAT, "requested_version": requested_version, "channel": None,
               "configured_channel_source": _source_for_report(channel_source),
               "direct_https_used": False, "filesystem_transport": "not assessed; filesystem paths may use mounted or OS-managed network storage",
               "candidate": None, "archive_verified": False, "archive_fetched": False,
               "installed_authenticated": False, "active_adoption": False,
               "project_adoption_performed": False, "global_skill_installed": False,
               "adoption_pr_created": False, "release_code_executed": False}
    try:
        _selector(requested_version)
        project = awf.absolute_path(project_path, "Project")
        context.update(project_directory=str(project), installed=_installed(project))
        if channel_source is None:
            return {**context, "status": "UPDATE_CHECK_NOT_CONFIGURED", "network_used": None,
                    "next_action": {"owner": "native coordinator", "authorization_required": False,
                                    "action": "Configure an owner-approved local or HTTPS update channel and rerun this check. Report the separately observed bundled version without claiming it is latest."}}
        context["direct_https_used"] = str(channel_source).lower().startswith("https://")
        context["network_used"] = True if context["direct_https_used"] else None
        channel, candidates = _read_channel(channel_source)
        context.update(channel=channel)
        matching = [entry for entry in candidates if requested_version is None or entry["version"] == requested_version or
                    (requested_version.count(".") == 1 and entry["version"].startswith(requested_version + "."))]
        if not matching:
            return {**context, "status": "REQUESTED_VERSION_UNAVAILABLE",
                    "next_action": {"owner": "native coordinator", "authorization_required": False,
                                    "action": "Find an owner-approved channel announcing the requested version; preserve the current installation and do not substitute another release line."}}
        candidate = matching[-1]
        context["candidate"] = candidate
        installed = context["installed"]
        action = "Obtain the announced candidate archive from its resolved location, verify archive and manifest pins plus complete source bytes, then prepare an isolated adoption/upgrade PR preserving project configuration and history."
        if installed["status"] == "INCONSISTENT":
            status = "RECONCILIATION_REQUIRED"
            action = "Reconcile the inconsistent or unreceipted installed AWF state without rewriting evidence; independently verify the intended candidate before preparing a reviewed adoption/upgrade PR."
        elif installed["status"] == "NOT_INSTALLED":
            status = "ADOPTION_AVAILABLE"
        elif _version(installed["version"]) > _version(candidate["version"]):
            status = "NO_DOWNGRADE"
            action = "Preserve the observed newer installation, authenticate its source and installed bytes, and consult an approved channel for that version or a newer candidate; do not downgrade automatically."
        elif installed["version"] == candidate["version"]:
            if installed["manifest_sha256"] == candidate["manifest_sha256"]:
                status = "VERSION_MATCH_REQUIRES_VERIFICATION"
                action = "Authenticate the matching source archive and verify actual installed managed bytes before treating the observed version as current; no update or active adoption is proved by matching metadata."
            else:
                status = "RECONCILIATION_REQUIRED"
                action = "Resolve the differing manifests announced under the same version using independently trusted release evidence; preserve current files and do not silently reinstall or change pins."
        else:
            status = "UPDATE_AVAILABLE"
        return {**context, "status": status,
                "next_action": {"owner": "native coordinator", "authorization_required": False, "action": action}}
    except awf.DiscoveryError as exc:
        exc.proof = {**context, **exc.proof}
        raise
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
        raise awf.DiscoveryError("UPDATE_CHANNEL_UNAVAILABLE", str(exc), context) from exc


def rejected(exc):
    return {**exc.proof, "format": FORMAT, "status": "UPDATE_CHECK_REJECTED", "code": exc.code,
            "error": str(exc), "archive_verified": False, "archive_fetched": False,
            "installed_authenticated": False, "active_adoption": False,
            "next_action": {"owner": "native coordinator", "authorization_required": False,
                            "action": "Resolve the reported channel/path/version/integrity error using the configured trust source, then rerun the read-only update check. Preserve the current installation; this failure cannot establish that it is latest."}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--channel", help="Owner-configured absolute local JSON path or HTTPS URL")
    parser.add_argument("--version", help="Optional major.minor family or exact patch constraint")
    args = parser.parse_args(argv)
    try:
        result = check_updates(args.project, args.channel, args.version)
    except awf.DiscoveryError as exc:
        print(json.dumps(rejected(exc), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
