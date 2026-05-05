#requires -Version 5.1
<#
.SYNOPSIS
    One-screen health snapshot of the workstation deploy.

.DESCRIPTION
    Surfaces five things the operator usually wants at a glance:

      1. ``docker compose ps`` for the three containers (with health).
      2. /health from inside the workstation (loopback).
      3. Resolved LLM provider posture from /admin/config.
      4. Cloudflare tunnel registration count (4 PoPs == healthy).
      5. The public hostname configured in cloudflared/config.yml.

.EXAMPLE
    .\Status.ps1
#>
[CmdletBinding()]
param()

. "$PSScriptRoot\_lib.ps1"
Assert-Docker

Write-Step "Containers"
docker compose @(Get-ComposeArgs) ps

Write-Host ""
Write-Step "Local API /health"
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Done $r.Content
} catch {
    Write-Warn2 "API not reachable on http://127.0.0.1:8000 — $($_.Exception.Message)"
}

Write-Host ""
Write-Step "LLM provider posture"
try {
    $cfg = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/admin/config' -TimeoutSec 5
    $llm = $cfg.llm
    Write-Host ("  provider_setting     : " + $llm.provider_setting)
    $activeProvider = if ($llm.active_provider) { $llm.active_provider } else { '<none>' }
    Write-Host ("  active_provider      : " + $activeProvider)
    Write-Host ("  active model         : " + ($(if ($llm.model) { $llm.model } else { '<none>' })))
    Write-Host ("  gemini_configured    : " + $llm.gemini_configured)
    Write-Host ("  anthropic_configured : " + $llm.anthropic_configured)
    if ($activeProvider -eq '<none>' -and -not $llm.gemini_configured -and -not $llm.anthropic_configured) {
        Write-Warn2 "No LLM key configured — Phase 2 entity extraction is disabled."
        Write-Host "  Remediation: set ANTHROPIC_API_KEY or GEMINI_API_KEY in .env, then run Start.ps1 to restart the API."
    }
} catch {
    Write-Warn2 "Could not read /admin/config: $($_.Exception.Message)"
}

Write-Host ""
Write-Step "Cloudflare tunnel registration"

# Pre-check the on-disk config so we can give a clearer remediation
# than ``cloudflared``'s native "requires the ID or name of the
# tunnel" error when the operator hasn't completed
# 10-Setup-Tunnel.ps1 yet.
$cloudflaredConfig = Join-Path (Get-RepoRoot) 'docker\cloudflared\config.yml'
$tunnelConfigOk = $true
if (-not (Test-Path $cloudflaredConfig)) {
    Write-Warn2 "docker\cloudflared\config.yml not found."
    Write-Host "  Remediation: run scripts\windows\10-Setup-Tunnel.ps1 once to authenticate and render the config."
    $tunnelConfigOk = $false
} else {
    $configText = Get-Content $cloudflaredConfig -Raw
    if ($configText -match 'REPLACE-WITH-TUNNEL-UUID') {
        Write-Warn2 "docker\cloudflared\config.yml still has the placeholder ``REPLACE-WITH-TUNNEL-UUID``."
        Write-Host "  Remediation: run scripts\windows\10-Setup-Tunnel.ps1 — it overwrites the placeholder with the real UUID."
        $tunnelConfigOk = $false
    }
}

# ``docker logs ... 2>&1`` yields stderr lines as PowerShell error
# records under StrictMode; suppress that so an unstarted /
# misconfigured container doesn't surface as a PowerShell parsing
# error. We capture both streams as a string array and grep them
# below.
$cloudflaredLogs = @()
try {
    $cloudflaredLogs = & docker logs kw-pipeline-cloudflared 2>&1 | ForEach-Object { "$_" }
} catch {
    Write-Warn2 "Could not read 'docker logs kw-pipeline-cloudflared': $($_.Exception.Message)"
}

$registered = (@($cloudflaredLogs) | Select-String "Registered tunnel connection").Matches.Count
if ($registered -ge 4) {
    Write-Done "$registered registered connections (>=4 means healthy across PoPs)"
} elseif ($registered -gt 0) {
    Write-Warn2 "$registered registered connections (expected >=4)"
} else {
    Write-Warn2 "No 'Registered tunnel connection' lines yet."
    if ($tunnelConfigOk) {
        Write-Host "  If you just ran Start.ps1, give it 10 s and re-run Status. Otherwise check the tail of 'docker logs kw-pipeline-cloudflared' below."
    }
    # Surface the last few container log lines so an operator sees the
    # real cloudflared error (e.g. missing tunnel UUID, bad credentials
    # path) rather than a confusing PowerShell stderr-mapping error.
    $tail = @($cloudflaredLogs) | Select-Object -Last 6
    if ($tail.Count -gt 0) {
        Write-Host "  Last log lines from kw-pipeline-cloudflared:"
        foreach ($line in $tail) {
            if ($line) { Write-Host "    $line" }
        }
    }
}

if ($tunnelConfigOk -and (Test-Path $cloudflaredConfig)) {
    $match = Get-Content $cloudflaredConfig | Select-String -Pattern '^\s*-\s*hostname:\s*(.+)\s*$' | Select-Object -First 1
    if ($match) {
        $hostname = $match.Matches[0].Groups[1].Value.Trim()
        if ($hostname) { Write-Host "  Public URL: https://$hostname" }
    }
}
