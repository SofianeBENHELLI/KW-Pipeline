#requires -Version 5.1
<#
.SYNOPSIS
    One-shot first-time setup of the KW-Pipeline workstation deploy
    on Windows.

.DESCRIPTION
    Drives the three numbered scripts in order:

      00-Install-Prereqs   — winget cloudflared + git, verify Docker
      10-Setup-Tunnel      — cloudflared login + create + route DNS
      20-Setup-Env         — write docker\.env + Neo4j password

    Then optionally registers the auto-start scheduled task and
    brings the stack up.

    Idempotent end-to-end: re-running Bootstrap on a working machine
    reuses the existing tunnel and config without breaking anything.

.PARAMETER Hostname
    Public hostname (must be in a Cloudflare zone you control), e.g.
    ``kw-api.example.com``. Required.

.PARAMETER Provider
    KW_LLM_PROVIDER. Defaults to ``auto`` (Gemini primary, Anthropic
    fallback per ADR-013 §6).

.PARAMETER NoStart
    Skip the final ``docker compose up`` so you can review docker\.env
    before launching.

.PARAMETER NoAutoStart
    Skip the auto-start scheduled task registration.

.EXAMPLE
    .\Bootstrap.ps1 -Hostname kw-api.example.com

    Walks through every prompt interactively, ends with a running
    deploy and a scheduled task that brings it back up at logon.

.EXAMPLE
    .\Bootstrap.ps1 -Hostname kw-api.example.com -NoAutoStart -NoStart

    Run only the configuration steps; leave the actual ``up`` and
    auto-start hook for later.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9.-]+\.[a-z]{2,}$')]
    [string]$Hostname,

    [ValidateSet('auto', 'gemini', 'anthropic')]
    [string]$Provider = 'auto',

    [switch]$NoStart,
    [switch]$NoAutoStart
)

. "$PSScriptRoot\_lib.ps1"

Write-Step "Bootstrap — $Hostname (provider=$Provider)"
& "$PSScriptRoot\00-Install-Prereqs.ps1"
& "$PSScriptRoot\10-Setup-Tunnel.ps1" -Hostname $Hostname
& "$PSScriptRoot\20-Setup-Env.ps1" -Provider $Provider

if (-not $NoAutoStart) {
    & "$PSScriptRoot\Setup-AutoStart.ps1"
}

if (-not $NoStart) {
    & "$PSScriptRoot\Start.ps1"
}

Write-Host ""
Write-Step "Done."
Write-Host "  Public URL : https://$Hostname"
Write-Host "  Logs       : .\Logs.ps1 -Service api"
Write-Host "  Status     : .\Status.ps1"
Write-Host "  Update     : .\Update.ps1"
Write-Host "  Stop       : .\Stop.ps1"
