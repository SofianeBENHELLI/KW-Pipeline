#requires -Version 5.1
<#
.SYNOPSIS
    One-time install of the prerequisites the KW-Pipeline Windows
    deploy needs that aren't already on the box.

.DESCRIPTION
    Verifies Docker Desktop is running, then installs cloudflared and
    git via winget if either is missing. Idempotent — safe to re-run.

    Does NOT install Docker Desktop itself; the user is expected to
    have it set up and configured to start at logon (Settings →
    General → "Start Docker Desktop when you log in").

.EXAMPLE
    .\00-Install-Prereqs.ps1
#>
[CmdletBinding()]
param()

. "$PSScriptRoot\_lib.ps1"

Write-Step "Checking Docker Desktop"
Assert-Docker
$serverVersion = docker version --format '{{.Server.Version}}'
$composeVersion = (docker compose version --short)
Write-Done "Docker Engine $serverVersion, Compose $composeVersion"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is not installed on this machine. Install 'App Installer' from the Microsoft Store, then re-run this script."
}

Write-Step "Checking git"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Done "git already installed: $((git --version))"
} else {
    Write-Warn2 "git not found — installing via winget"
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    Write-Done "git installed. Reopen this PowerShell window to refresh PATH."
}

Write-Step "Checking cloudflared"
if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    Write-Done "cloudflared already installed: $((cloudflared --version) -split "`n" | Select-Object -First 1)"
} else {
    Write-Warn2 "cloudflared not found — installing via winget"
    winget install --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements
    Write-Done "cloudflared installed. Reopen this PowerShell window to refresh PATH."
}

Write-Host ""
Write-Step "Prereqs OK"
Write-Host "  Next: .\10-Setup-Tunnel.ps1 -Hostname <your.domain>"
