#requires -Version 5.1
<#
.SYNOPSIS
    Bring the KW-Pipeline workstation deploy up.

.DESCRIPTION
    Runs ``docker compose --profile deploy up -d`` against the repo's
    canonical compose file. Three containers come up:

        kw-pipeline-neo4j         (5.23 community)
        kw-pipeline-api           (FastAPI, persistent SQLite + filesystem)
        kw-pipeline-cloudflared   (tunnel sidecar)

    Then waits up to 60 s for /health to return 200 and prints a
    summary including which LLM provider resolved.

.EXAMPLE
    .\Start.ps1
#>
[CmdletBinding()]
param([int]$HealthTimeoutSeconds = 60)

. "$PSScriptRoot\_lib.ps1"
Assert-Docker

Write-Step "docker compose up -d (profile=deploy)"
docker compose @(Get-ComposeArgs) up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }

Write-Step "Waiting for /health on http://127.0.0.1:8000 (max ${HealthTimeoutSeconds}s)"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            $ok = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if ($ok) {
    Write-Done "API healthy."
    & "$PSScriptRoot\Status.ps1"
} else {
    Write-Warn2 "API did not respond healthy within ${HealthTimeoutSeconds}s."
    Write-Warn2 "Tail the logs with: .\Logs.ps1 -Service api"
    exit 1
}
