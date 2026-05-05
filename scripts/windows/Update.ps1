#requires -Version 5.1
<#
.SYNOPSIS
    Pull the latest backend code, rebuild the api image, and restart
    only the api container.

.DESCRIPTION
    Neo4j and the Cloudflare tunnel keep running while the api
    container is recreated, so the public URL only sees ~2 s of
    "Backend health" red while uvicorn restarts. Use this for
    routine deploys.

.EXAMPLE
    .\Update.ps1
#>
[CmdletBinding()]
param()

. "$PSScriptRoot\_lib.ps1"
Assert-Docker

Push-Location (Get-RepoRoot)
try {
    Write-Step "git pull"
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed (non-fast-forward?)." }

    Write-Step "Rebuilding api image"
    docker compose @(Get-ComposeArgs) build api
    if ($LASTEXITCODE -ne 0) { throw "docker compose build failed." }

    Write-Step "Recreating api container"
    docker compose @(Get-ComposeArgs) up -d api
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed." }
} finally {
    Pop-Location
}

& "$PSScriptRoot\Status.ps1"
