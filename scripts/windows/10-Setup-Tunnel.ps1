#requires -Version 5.1
<#
.SYNOPSIS
    One-time Cloudflare Tunnel wiring for the KW-Pipeline backend.

.DESCRIPTION
    Drives the cloudflared CLI through the four steps the workstation
    deploy needs:

      1. Authenticate cloudflared with the Cloudflare account (browser
         flow, opens automatically — interactive, only once per machine).
      2. Create the named tunnel (skips when one with the same name
         already exists in the account).
      3. Copy the credentials JSON + render config.yml into
         docker\cloudflared\.
      4. Route the public hostname to the tunnel (creates the CNAME).

    After this script returns, the deploy is one ``docker compose up``
    away (see Start.ps1).

.PARAMETER Hostname
    Public hostname the tunnel will serve, e.g. kw-api.example.com.
    Must live in a Cloudflare zone you control. When omitted, the
    script reads the hostname from a previously rendered
    docker\cloudflared\config.yml; if that's missing too it falls
    back to the runbook default (kw-api.benhelli.org).

.PARAMETER TunnelName
    Cloudflare-side tunnel name. Defaults to ``kw-api``. Pick a
    different one only if you already have a ``kw-api`` tunnel for
    another deployment in the same account.

.EXAMPLE
    .\10-Setup-Tunnel.ps1
    .\10-Setup-Tunnel.ps1 -Hostname kw-api.example.com
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[a-z0-9.-]+\.[a-z]{2,}$')]
    [string]$Hostname,

    [string]$TunnelName = 'kw-api'
)

. "$PSScriptRoot\_lib.ps1"
Assert-Cloudflared

if (-not $Hostname) {
    $Hostname = Get-DefaultHostname
    Write-Warn2 "No -Hostname supplied; defaulting to '$Hostname'. Pass -Hostname <fqdn> to override."
}

$repoRoot = Get-RepoRoot
$cloudflaredDir = Join-Path $repoRoot 'docker\cloudflared'
$configExample = Join-Path $cloudflaredDir 'config.yml.example'
$configFile = Join-Path $cloudflaredDir 'config.yml'
$userCloudflared = Join-Path $env:USERPROFILE '.cloudflared'

# ── 1. Authenticate ──────────────────────────────────────────────────
$certPath = Join-Path $userCloudflared 'cert.pem'
if (Test-Path $certPath) {
    Write-Done "cloudflared already authenticated ($certPath exists)"
} else {
    Write-Step "Opening browser for Cloudflare authorization"
    Write-Warn2 "Pick the zone that owns '$Hostname' when prompted."
    cloudflared tunnel login
    if (-not (Test-Path $certPath)) {
        throw "cloudflared login did not produce $certPath. Re-run this step."
    }
    Write-Done "Authentication cert stored at $certPath"
}

# ── 2. Create the tunnel (or reuse) ──────────────────────────────────
Write-Step "Looking for an existing tunnel named '$TunnelName'"
$existing = (cloudflared tunnel list --output json | ConvertFrom-Json) `
    | Where-Object { $_.name -eq $TunnelName }
if ($existing) {
    $tunnelId = $existing.id
    Write-Done "Reusing existing tunnel $TunnelName ($tunnelId)"
} else {
    Write-Step "Creating tunnel '$TunnelName'"
    $output = cloudflared tunnel create $TunnelName 2>&1
    Write-Host $output
    $match = [regex]::Match([string]::Join("`n", $output), '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')
    if (-not $match.Success) {
        throw "Could not parse the tunnel UUID from cloudflared output."
    }
    $tunnelId = $match.Value
    Write-Done "Created tunnel $TunnelName ($tunnelId)"
}

# ── 3. Copy credentials + render config.yml ──────────────────────────
$credSrc = Join-Path $userCloudflared "$tunnelId.json"
if (-not (Test-Path $credSrc)) {
    throw "Credentials file not found at $credSrc. Did the tunnel create succeed?"
}

if (-not (Test-Path $cloudflaredDir)) {
    New-Item -ItemType Directory -Force -Path $cloudflaredDir | Out-Null
}
$credDest = Join-Path $cloudflaredDir "$tunnelId.json"
Copy-Item -Force $credSrc $credDest
Write-Done "Copied tunnel credentials to $credDest"

if (-not (Test-Path $configExample)) {
    throw "Missing $configExample — the repo's cloudflared example file disappeared."
}
$config = Get-Content $configExample -Raw
$config = $config -replace 'REPLACE-WITH-TUNNEL-UUID', $tunnelId
# Replace the example hostname with the operator-supplied one. The
# example file ships with kw-api.benhelli.org as a stand-in.
$config = $config -replace '(?m)^(\s*-\s*hostname:\s*).+$', "`${1}$Hostname"
Set-Content -Encoding ASCII -NoNewline:$false -Path $configFile -Value $config
Write-Done "Wrote $configFile (hostname=$Hostname, tunnel=$tunnelId)"

# ── 4. Route DNS ─────────────────────────────────────────────────────
Write-Step "Routing $Hostname to tunnel '$TunnelName'"
try {
    cloudflared tunnel route dns $TunnelName $Hostname
    Write-Done "DNS route created."
} catch {
    Write-Warn2 "Route command failed; this is fine if the CNAME already exists for the same tunnel."
    Write-Warn2 $_.Exception.Message
}

Write-Host ""
Write-Step "Tunnel ready"
Write-Host "  Tunnel id : $tunnelId"
Write-Host "  Hostname  : $Hostname"
Write-Host "  Config    : $configFile"
Write-Host "  Next      : .\20-Setup-Env.ps1 -Hostname $Hostname"
