#requires -Version 5.1
<#
.SYNOPSIS
    Wire the workstation deploy to come up on user logon, with a
    short delay so Docker Desktop has time to finish starting.

.DESCRIPTION
    Registers a Scheduled Task in the *current user* profile that
    runs Start.ps1 at logon, after a 60-second delay. The delay is
    important: Docker Desktop's daemon takes a moment to come up
    after the icon appears in the taskbar, and ``docker compose up``
    will fail if the daemon isn't ready yet.

    Re-running this script replaces any existing task with the same
    name, so it's safe to run repeatedly.

.PARAMETER TaskName
    Defaults to ``KWPipelineDeploy``.

.PARAMETER DelaySeconds
    Logon-to-start delay, default 60 s. Bump this if your machine
    takes longer to bring Docker Desktop up.

.EXAMPLE
    .\Setup-AutoStart.ps1

.EXAMPLE
    .\Setup-AutoStart.ps1 -DelaySeconds 120

.EXAMPLE
    # Remove the auto-start hook:
    .\Setup-AutoStart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'KWPipelineDeploy',
    [int]$DelaySeconds = 60,
    [switch]$Remove
)

. "$PSScriptRoot\_lib.ps1"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Done "Scheduled task '$TaskName' removed."
    } else {
        Write-Warn2 "No task named '$TaskName' to remove."
    }
    return
}

$startScript = Join-Path $PSScriptRoot 'Start.ps1'
if (-not (Test-Path $startScript)) { throw "Cannot find $startScript" }

# Use powershell.exe (built-in) so the task works without pwsh installed.
$delayIso = "PT${DelaySeconds}S"
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = $delayIso
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'KW-Pipeline workstation deploy auto-start (Cloudflare tunnel + API + Neo4j).' `
    -Force | Out-Null

Write-Done "Scheduled task '$TaskName' registered."
Write-Host "  Trigger : at logon for $env:USERNAME, delayed ${DelaySeconds}s"
Write-Host "  Action  : powershell.exe Start.ps1 (hidden window)"
Write-Host "  Remove  : .\Setup-AutoStart.ps1 -Remove"
