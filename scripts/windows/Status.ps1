#requires -Version 5.1
<#
.SYNOPSIS
    One-screen health snapshot of the workstation deploy.

.DESCRIPTION
    Surfaces five things the operator usually wants at a glance:

      1. ``docker compose ps`` for the three containers (with health).
      2. /health from inside the workstation (loopback).
      3. Resolved LLM provider posture from /admin/config.
      4. Cloudflare tunnel readiness — primary signal is a GET on
         ``https://<public-hostname>/health``; the log-line heuristic
         (registered-connections count) is a secondary informational
         signal because it lags the real handshake.
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

# When neo4j is unhealthy or restarting, the API depends_on it and
# never starts. Surface the tail of neo4j logs proactively so the
# operator sees the real cause (stale volume password, memory
# pressure, port conflict, license prompt) without having to know to
# run ``docker logs`` themselves.
$neo4jStatus = (& docker inspect --format '{{.State.Health.Status}}' kw-pipeline-neo4j 2>$null)
if ($LASTEXITCODE -eq 0 -and $neo4jStatus -and $neo4jStatus -ne 'healthy') {
    Write-Host ""
    Write-Warn2 "kw-pipeline-neo4j is '$neo4jStatus' — API depends on it and won't start until it's healthy."
    Write-Host "  Common causes:"
    Write-Host "    1. Stale ``neo4j_data`` volume from a previous run with a different password."
    Write-Host "       Fix (wipes Neo4j data): ``cd docker && docker compose --profile deploy down -v && docker compose --profile deploy up -d``"
    Write-Host "    2. Docker Desktop RAM < 4 GB. Settings -> Resources -> bump and restart Docker."
    Write-Host "    3. Ports 7687 or 7474 already bound. Run: ``netstat -ano | findstr ""7687 7474""``"
    $neo4jLogs = @()
    try {
        # Force-wrap in @() — when docker logs returns 0/1 lines or
        # the catch fires, the assignment can land as $null or a
        # scalar string, both of which break ``.Count`` under
        # StrictMode (PropertyNotFoundStrict).
        $neo4jLogs = @(& docker logs --tail 8 kw-pipeline-neo4j 2>&1 | ForEach-Object { "$_" })
    } catch {}
    if (@($neo4jLogs).Count -gt 0) {
        Write-Host "  Last log lines from kw-pipeline-neo4j:"
        foreach ($line in $neo4jLogs) {
            if ($line) { Write-Host "    $line" }
        }
    }
}

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

# Resolve the public hostname up-front so we can probe it as the
# ground-truth signal. The log-line heuristic ("Registered tunnel
# connection") races with the connector handshake and can lag the
# real readiness by 10-30 s — a hostname that already serves
# /health is the authoritative answer regardless.
$publicHostname = $null
if ($tunnelConfigOk -and (Test-Path $cloudflaredConfig)) {
    $match = Get-Content $cloudflaredConfig | Select-String -Pattern '^\s*-\s*hostname:\s*(.+)\s*$' | Select-Object -First 1
    if ($match) {
        $publicHostname = $match.Matches[0].Groups[1].Value.Trim()
    }
}

# ``docker logs ... 2>&1`` yields stderr lines as PowerShell error
# records under StrictMode; suppress that so an unstarted /
# misconfigured container doesn't surface as a PowerShell parsing
# error. We capture both streams as a string array and grep them
# below.
$cloudflaredLogs = @()
try {
    # Force-wrap in @() so a single-line / empty stream lands as an
    # array, not a scalar / $null. PowerShell StrictMode raises
    # PropertyNotFoundStrict on .Count of either.
    $cloudflaredLogs = @(& docker logs kw-pipeline-cloudflared 2>&1 | ForEach-Object { "$_" })
} catch {
    Write-Warn2 "Could not read 'docker logs kw-pipeline-cloudflared': $($_.Exception.Message)"
}

# ``Select-String`` returns nothing when there are zero matches, so
# ``...Matches.Count`` raises under StrictMode (PropertyNotFoundStrict).
# Wrap the result in ``@(...)`` so an empty pipeline still gives us
# a 0-length array we can ``.Count`` safely.
$registered = @(@($cloudflaredLogs) | Select-String "Registered tunnel connection").Count

# Public-URL probe: the authoritative readiness check. If /health
# returns 200 over HTTPS, the entire chain (Cloudflare ->
# cloudflared -> api) is up regardless of what the log heuristic
# says.
$publicHealthOk = $false
if ($publicHostname) {
    try {
        $r = Invoke-WebRequest -Uri "https://$publicHostname/health" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            $publicHealthOk = $true
        }
    } catch {
        # Surface the failure reason under -Verbose without changing
        # the steady-state output: when the probe fails we still fall
        # through to the log-grep + remediation hints below.
        Write-Verbose "Public-URL probe to https://$publicHostname/health failed: $($_.Exception.Message)"
    }
}

if ($publicHealthOk) {
    if ($registered -ge 4) {
        Write-Done "Public URL up — https://$publicHostname/health = 200 ($registered registered connections)"
    } elseif ($registered -gt 0) {
        Write-Done "Public URL up — https://$publicHostname/health = 200 ($registered registered connections; expected 4 once steady)"
    } else {
        Write-Done "Public URL up — https://$publicHostname/health = 200 (log-grep hasn't caught a 'Registered tunnel connection' line yet — ignore the lag)"
    }
} elseif ($registered -ge 4) {
    Write-Done "$registered registered connections (>=4 means healthy across PoPs)"
} elseif ($registered -gt 0) {
    Write-Warn2 "$registered registered connections (expected >=4)"
} else {
    Write-Warn2 "No 'Registered tunnel connection' lines yet."
    if ($publicHostname) {
        Write-Host "  Probed https://$publicHostname/health — not 200 either, so the tunnel really is down."
    } elseif ($tunnelConfigOk) {
        Write-Host "  If you just ran Start.ps1, give it 10 s and re-run Status. Otherwise check the tail of 'docker logs kw-pipeline-cloudflared' below."
    }
    # Surface the last few container log lines so an operator sees the
    # real cloudflared error (e.g. missing tunnel UUID, bad credentials
    # path) rather than a confusing PowerShell stderr-mapping error.
    # ``Select-Object -Last 6`` on an empty pipeline returns nothing
    # ($null), not an empty array — StrictMode then crashes on .Count.
    # Same @() wrap pattern as the registered-count above.
    $tail = @(@($cloudflaredLogs) | Select-Object -Last 6)
    if ($tail.Count -gt 0) {
        Write-Host "  Last log lines from kw-pipeline-cloudflared:"
        foreach ($line in $tail) {
            if ($line) { Write-Host "    $line" }
        }
    }
}

if ($publicHostname) { Write-Host "  Public URL: https://$publicHostname" }
