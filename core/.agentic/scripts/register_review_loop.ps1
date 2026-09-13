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
foreach ($value in @($pythonPath,$configPath,$scriptPath,$scheduledPath)) {
    if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Invalid command path' }
}
& $pythonPath -B $scriptPath --config $configPath status
if ($LASTEXITCODE -ne 0) { throw 'Enroll and reconcile the PR before scheduling' }
$arguments = '-B "{0}" --config "{1}"' -f $scheduledPath,$configPath
if (-not $Create) {
    [pscustomobject]@{TaskName=$TaskName;Executable=$pythonPath;Arguments=$arguments;IntervalMinutes=$IntervalMinutes;Created=$false}
    Write-Output 'Next: inspect this registration plan, then rerun with -Create when covered by existing host authorization. Owner: controller. Trigger: registration plan and host qualification verified.'
    return
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Task exists; inspect it instead of overwriting it' }
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $runtimePath
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 65)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Write-Output 'Next: observe the first scheduled tick and relay its retained next-step report; continue independent stream work. Owner: controller. Trigger: first scheduled run or reported failure.'
