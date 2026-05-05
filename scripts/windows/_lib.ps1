#requires -Version 5.1
<#
.SYNOPSIS
    Shared helpers for the KW-Pipeline Windows deploy scripts.

.DESCRIPTION
    Dot-source this file from any other script in scripts\windows\:

        . "$PSScriptRoot\_lib.ps1"

    Provides:
      - Get-RepoRoot         — repo root resolved from this file's location
      - Get-ComposeArgs      — common -f / --profile args for docker compose
      - Assert-Docker        — fail fast if Docker Desktop isn't running
      - Assert-Cloudflared   — fail fast if cloudflared CLI is missing
      - Write-Step / -Done / -Warn — minimalist coloured logging

    These helpers are intentionally small. Each top-level script stays
    readable on its own; this library only collects the parts that would
    otherwise repeat verbatim.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    # scripts\windows\_lib.ps1 -> repo root is two levels up.
    Resolve-Path (Join-Path $PSScriptRoot '..\..')
}

function Get-ComposeFile {
    Join-Path (Get-RepoRoot) 'docker\docker-compose.yml'
}

function Get-ComposeArgs {
    @('-f', (Get-ComposeFile), '--profile', 'deploy')
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Done([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Warn2([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Yellow
}

function Assert-Docker {
    try {
        $null = docker version --format '{{.Server.Version}}' 2>$null
    } catch {
        throw "Docker Desktop is not running. Start it from the Start menu and re-run this script."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is not running. Start it from the Start menu and re-run this script."
    }
}

function Assert-Cloudflared {
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        throw "cloudflared CLI not found. Run scripts\windows\00-Install-Prereqs.ps1 first."
    }
}

function Get-DockerEnvFile {
    Join-Path (Get-RepoRoot) 'docker\.env'
}
