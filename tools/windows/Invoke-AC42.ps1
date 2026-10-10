<#
.SYNOPSIS
  AC42 release gate (AWF-11): Windows end-to-end upgrade of a recorded AWF
  installation fixture to this 1.9.4 candidate, with an evidence file.

.DESCRIPTION
  Run from the root of a fresh clone of the candidate commit, on a Windows
  host with Git for Windows and CPython 3.11+ (python.org or py launcher).
  Everything is written under -ScratchRoot; the clone itself is not modified. The
  only network access is pip downloading the hash-locked wheels from
  .agentic/requirements.lock. No provider, credential or Jira access is used:
  the adoption-PR merge and GitHub answers are a recorded synthetic fixture.

  Steps: create a venv; install the locked dependencies (--require-hashes);
  download the same locked wheels into an offline wheelhouse; run
  scripts/ac42_e2e.py, which upgrades the fixture through
  scripts/bootstrap_project.py with that wheelhouse, then runs
  verify-installation, validate-config and the self-test; merges the upgrade
  PR fixture; requires ACTIVE from the installed project's
  .agentic\scripts\workflow.py status command; emits a handoff snapshot;
  checks owner values and external data are unchanged and that a fresh
  core.autocrlf=true clone still verifies; and checks that a value added
  and then removed in branch history blocks publication (AC43).

  Every required row must PASS before the result or gate_eligible can pass.
  Until AWF-8 implements the AC48-AC51 read-only external-resource admission
  exercise, that row is NOT_COVERED and this release gate fails closed.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\Invoke-AC42.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\Invoke-AC42.ps1 -FromVersion 1.9.2
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\Invoke-AC42.ps1 -Cleanup

.NOTES
  All writes (venv, wheelhouse, pip cache, fixture repositories, evidence)
  stay under -ScratchRoot (default C:\awf-ac42-scratch); -Work must be inside
  it. -Cleanup deletes every work-* directory under -ScratchRoot and keeps
  the evidence directory.
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateSet("1.8.3", "1.8.9", "1.9.1", "1.9.2", "1.9.3")]
    [string]$FromVersion = "1.9.1",
    [string]$ScratchRoot = "C:\awf-ac42-scratch",
    [string]$Work = "",
    [string]$Evidence = "",
    [switch]$Cleanup
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force -Path $ScratchRoot | Out-Null
$scratch = (Resolve-Path -LiteralPath $ScratchRoot).Path.TrimEnd("\")
if ($Cleanup) {
    Get-ChildItem -LiteralPath $scratch -Directory -Filter "work-*" | ForEach-Object {
        Write-Host "[AC42] removing $($_.FullName)"
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
    Write-Host "[AC42] cleanup done; evidence kept in $(Join-Path $scratch 'evidence')"
    exit 0
}
if (-not $Work) { $Work = Join-Path $scratch ("work-" + $stamp) }
if (-not $Evidence) { $Evidence = Join-Path $scratch ("evidence\ac42-evidence-" + $stamp + ".json") }
$Work = [System.IO.Path]::GetFullPath($Work)
if (-not $Work.StartsWith($scratch + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "-Work must be inside $scratch"
}
$source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $source "MANIFEST.json"))) { throw "Run from an AWF source checkout." }
if (Test-Path $Evidence) { throw "Evidence file already exists: $Evidence" }
New-Item -ItemType Directory -Force -Path $Work, (Split-Path -Parent $Evidence) | Out-Null
$env:PIP_CACHE_DIR = Join-Path $Work "pip-cache"
$env:PIP_CONFIG_FILE = "NUL"
$env:PYTHONNOUSERSITE = "1"
$lock = Join-Path $source ".agentic\requirements.lock"
$venv = Join-Path $Work "venv"
$wheelhouse = Join-Path $Work "wheelhouse"
$started = Get-Date

Write-Host "[AC42] candidate: $(git -C $source rev-parse HEAD)"
Write-Host "[AC42] git core.autocrlf (global): $(git config --global --get core.autocrlf)"
& $Python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r $lock
if ($LASTEXITCODE -ne 0) { throw "locked dependency install failed" }
& $py -m pip download --disable-pip-version-check --require-hashes --only-binary=:all: -r $lock -d $wheelhouse
if ($LASTEXITCODE -ne 0) { throw "wheelhouse download failed" }

& $py -B (Join-Path $source "scripts\ac42_e2e.py") --from-version $FromVersion `
    --work (Join-Path $Work "run") --evidence $Evidence --runtime-wheelhouse $wheelhouse
$code = $LASTEXITCODE
if (Test-Path $Evidence) {
    $hash = (Get-FileHash -Algorithm SHA256 $Evidence).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii -Path ($Evidence + ".sha256") -Value ("$hash  " + (Split-Path -Leaf $Evidence))
    Write-Host "[AC42] evidence: $Evidence"
    Write-Host "[AC42] evidence sha256: $hash"
}
Write-Host ("[AC42] elapsed: {0:N0} s; exit code {1}" -f ((Get-Date) - $started).TotalSeconds, $code)
exit $code
