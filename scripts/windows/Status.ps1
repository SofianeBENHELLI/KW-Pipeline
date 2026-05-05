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
    Write-Host ("  active_provider      : " + ($(if ($llm.active_provider) { $llm.active_provider } else { '<none>' })))
    Write-Host ("  active model         : " + ($(if ($llm.model) { $llm.model } else { '<none>' })))
    Write-Host ("  gemini_configured    : " + $llm.gemini_configured)
    Write-Host ("  anthropic_configured : " + $llm.anthropic_configured)
} catch {
    Write-Warn2 "Could not read /admin/config: $($_.Exception.Message)"
}

Write-Host ""
Write-Step "Cloudflare tunnel registration"
$registered = (docker logs kw-pipeline-cloudflared 2>&1 | Select-String "Registered tunnel connection").Matches.Count
if ($registered -ge 4) {
    Write-Done "$registered registered connections (>=4 means healthy across PoPs)"
} elseif ($registered -gt 0) {
    Write-Warn2 "$registered registered connections (expected >=4)"
} else {
    Write-Warn2 "No 'Registered tunnel connection' lines yet — give it 10 s after Start, then re-run."
}

$cfg = Join-Path (Get-RepoRoot) 'docker\cloudflared\config.yml'
if (Test-Path $cfg) {
    $match = Get-Content $cfg | Select-String -Pattern '^\s*-\s*hostname:\s*(.+)\s*$' | Select-Object -First 1
    if ($match) {
        $hostname = $match.Matches[0].Groups[1].Value.Trim()
        if ($hostname) { Write-Host "  Public URL: https://$hostname" }
    }
}
