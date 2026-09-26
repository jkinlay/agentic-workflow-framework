[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][ValidatePattern('^AWF-[A-Za-z0-9_-]+$')][string]$TaskName,
    [ValidateRange(1,60)][int]$IntervalMinutes = 5,
    [switch]$Create
)
$ErrorActionPreference = 'Stop'
$runtimePath = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$configPath = (Resolve-Path -LiteralPath $Config).Path
$scriptPath = Join-Path $runtimePath '.agentic/scripts/review_loop.py'
$scheduledPath = Join-Path $runtimePath '.agentic/scripts/scheduled_tick.py'
$providerApiKeyEnvVars = @(
    'ANTHROPIC_API_KEY'
    'CODEX_API_KEY'
    'OPENAI_API_KEY'
)
foreach ($value in @($pythonPath,$configPath,$scriptPath,$scheduledPath)) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid command path' }
}
$preflightStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$preflightStartInfo.FileName = $pythonPath
$preflightStartInfo.Arguments = '-B "{0}" --config "{1}" status' -f $scriptPath,$configPath
$preflightStartInfo.UseShellExecute = $false
foreach ($name in @($preflightStartInfo.EnvironmentVariables.Keys)) {
    if ($providerApiKeyEnvVars -contains $name.ToUpperInvariant()) {
        [void]$preflightStartInfo.EnvironmentVariables.Remove($name)
    }
}
$preflightProcess = [System.Diagnostics.Process]::new()
$preflightProcess.StartInfo = $preflightStartInfo
try {
    if (-not $preflightProcess.Start()) { throw 'Unable to start the enrollment preflight' }
    $preflightProcess.WaitForExit()
    if ($preflightProcess.ExitCode -ne 0) { throw 'Enroll and reconcile the PR before scheduling' }
} finally {
    $preflightProcess.Dispose()
}
$arguments = '-B "{0}" --config "{1}"' -f $scheduledPath,$configPath
if (-not $Create) {
    [pscustomobject]@{TaskName=$TaskName;Executable=$pythonPath;Arguments=$arguments;IntervalMinutes=$IntervalMinutes;Created=$false}
    Write-Output 'Next: inspect this registration plan, then rerun with -Create when covered by existing host authorization. Owner: controller. Trigger: registration plan and host qualification verified.'
    return
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Task exists; inspect it instead of overwriting it' }
# Task Scheduler cannot remove inherited variables from an action. The scheduled
# entry point and review_loop.py scrub these provider keys at process startup.
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $runtimePath
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 65)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Write-Output 'Next: observe the first scheduled tick and relay its retained next-step report; continue independent stream work. Owner: controller. Trigger: first scheduled run or reported failure.'
