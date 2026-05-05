#requires -Version 5.1
<#
.SYNOPSIS
    Stop the workstation deploy without destroying volumes.

.DESCRIPTION
    Runs ``docker compose stop`` against the deploy profile. Data
    survives — the next ``Start.ps1`` resumes from where this stops.

    Use ``docker compose down`` (or .\Reset.ps1) instead when you
    actually want to wipe state.

.EXAMPLE
    .\Stop.ps1
#>
[CmdletBinding()]
param()

. "$PSScriptRoot\_lib.ps1"
Assert-Docker

Write-Step "docker compose stop (profile=deploy)"
docker compose @(Get-ComposeArgs) stop
if ($LASTEXITCODE -ne 0) { throw "docker compose stop failed." }
Write-Done "Stopped. Volumes preserved — Start.ps1 to resume."
