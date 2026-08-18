<#
.SYNOPSIS
    Install or remove the external RAG health-check task.

.DESCRIPTION
    This script is intentionally explicit: it never registers a task merely
    because the application starts. Run it once from an elevated or normal
    PowerShell session according to the selected principal policy. The task
    runs as the current interactive user, which keeps the user's .env and uv
    installation available on a single-machine developer deployment.
#>

[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall')]
    [string]$Action = 'Install',
    [string]$TaskName = 'RAG-HealthCheck',
    [string]$ProjectRoot = '',
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$HealthScript = Join-Path $ProjectRoot 'scripts\health_check.py'

if ($Action -eq 'Uninstall') {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $TaskName"
    exit 0
}

if (-not (Test-Path -LiteralPath $HealthScript -PathType Leaf)) {
    throw "Health-check script not found: $HealthScript"
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw 'uv was not found on PATH. Open the same user environment used to run the application and retry.'
}

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute $uv.Source -Argument 'run python scripts\health_check.py' -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Runs the RAG health check outside the application process.' -Force | Out-Null

Write-Output "Installed scheduled task: $TaskName"
Write-Output "Project root: $ProjectRoot"
Write-Output "Interval: $IntervalMinutes minute(s)"
