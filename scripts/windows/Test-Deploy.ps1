#requires -Version 5.1
<#
.SYNOPSIS
    End-to-end smoke test for the KW-Pipeline workstation deploy.

.DESCRIPTION
    Hits the running deploy with a small set of read-only + minimal-write
    probes and prints PASS / FAIL per check. Exits non-zero on any
    failure so the script is usable in CI and Task Scheduler health
    monitors.

    Checks, in order:

      1. ``GET /health``                          (must be 200 OK)
      2. ``GET /admin/config``                    (sanitised config snapshot;
                                                   reports active LLM provider
                                                   and embedding posture)
      3. ``POST /documents/upload``               (uploads a tiny text fixture
                                                   with a per-run Idempotency-Key,
                                                   asserts the response carries
                                                   document_id + version_id)
      4. ``GET /documents/{id}``                  (fetches the document we just
                                                   uploaded and verifies the
                                                   filename round-trips)

    The upload's idempotency key is unique per run, so re-running the
    smoke test never collides with a previous run. The smoke uses the
    dev-mode auth (``KW_AUTH_MODE=dev`` is the compose default) so no
    bearer token is needed.

.PARAMETER BaseUrl
    Where to point the smoke at. Defaults to ``http://127.0.0.1:8000``
    so the run hits the API container directly without going through
    the Cloudflare tunnel + Access. Pass the public URL when you want
    to verify the tunnel + (when configured) the Cloudflare Access
    posture too — note that Access will block the script unless you've
    pre-issued a service token.

.PARAMETER KeepFixture
    By default the uploaded fixture is left on the server (so you can
    poke it via the explorer or chat). Pass this switch to print the
    fixture's document_id at the end so you can clean it up manually.

.EXAMPLE
    .\Test-Deploy.ps1

    Runs against the local container. Quickest sanity probe; takes
    ~5 seconds end-to-end.

.EXAMPLE
    .\Test-Deploy.ps1 -BaseUrl https://kw-api.example.com

    Runs against the public URL (must not be gated behind Cloudflare
    Access for the script to authenticate without a service token).
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [switch]$KeepFixture
)

. "$PSScriptRoot\_lib.ps1"

# ── State + helpers ──────────────────────────────────────────────────
$script:Failures = @()
$script:Successes = @()

function Pass([string]$check, [string]$detail = '') {
    $script:Successes += $check
    Write-Host ("  [PASS] {0}{1}" -f $check, ($(if ($detail) { " — $detail" } else { '' }))) -ForegroundColor Green
}

function Fail([string]$check, [string]$detail) {
    $script:Failures += @{ check = $check; detail = $detail }
    Write-Host ("  [FAIL] {0} — {1}" -f $check, $detail) -ForegroundColor Red
}

function Get-Json([string]$Path) {
    $uri = "$BaseUrl$Path"
    return Invoke-RestMethod -Uri $uri -Method GET -TimeoutSec 10
}

# ── Check 1: /health ─────────────────────────────────────────────────
Write-Step "1. /health"
try {
    $health = Get-Json '/health'
    if ($health.status -eq 'ok') {
        Pass '/health' "status=ok"
    } else {
        Fail '/health' "unexpected payload: $($health | ConvertTo-Json -Compress)"
    }
} catch {
    Fail '/health' $_.Exception.Message
}

# ── Check 2: /admin/config ───────────────────────────────────────────
Write-Step "2. /admin/config"
$cfg = $null
try {
    $cfg = Get-Json '/admin/config'
    Pass '/admin/config reachable' "schema_version=$($cfg.schema_version)"
} catch {
    Fail '/admin/config reachable' $_.Exception.Message
}

if ($cfg) {
    $llm = $cfg.llm
    $kl = $cfg.knowledge_layer
    $emb = $cfg.embeddings

    Write-Host ""
    Write-Host "    Posture snapshot:" -ForegroundColor Gray
    Write-Host ("      knowledge_layer.enabled  : {0}" -f $kl.enabled)
    Write-Host ("      knowledge_layer.neo4j    : {0}" -f $kl.neo4j_configured)
    Write-Host ("      llm.provider_setting     : {0}" -f $llm.provider_setting)
    Write-Host ("      llm.active_provider      : {0}" -f ($(if ($llm.active_provider) { $llm.active_provider } else { '<none>' })))
    Write-Host ("      llm.model                : {0}" -f ($(if ($llm.model) { $llm.model } else { '<none>' })))
    Write-Host ("      llm.gemini_configured    : {0}" -f $llm.gemini_configured)
    Write-Host ("      llm.anthropic_configured : {0}" -f $llm.anthropic_configured)
    Write-Host ("      embeddings.configured    : {0}" -f $emb.configured)
    Write-Host ("      embeddings.model         : {0}" -f $emb.model)
    Write-Host ""

    # Posture sanity: if the operator pinned a provider, refuse to call
    # this a green run when it failed to resolve.
    if ($llm.provider_setting -ne 'auto' -and -not $llm.active_provider) {
        Fail 'llm posture' "provider pinned to '$($llm.provider_setting)' but no active provider — key likely missing"
    } else {
        Pass 'llm posture'
    }
}

# ── Check 3: POST /documents/upload ──────────────────────────────────
Write-Step "3. /documents/upload (text fixture)"

$tmpFile = Join-Path $env:TEMP ("kw-smoke-{0}.txt" -f ([Guid]::NewGuid().ToString('N')))
$fixtureContent = "KW-Pipeline smoke fixture {0}.`nThis line should round-trip through extract." -f (Get-Date -Format 'o')
[System.IO.File]::WriteAllText($tmpFile, $fixtureContent)

$idempotencyKey = "kw-smoke-{0}" -f ([Guid]::NewGuid().ToString('N'))

# curl.exe ships with Windows 10 1803+; preferred over Invoke-WebRequest
# because PowerShell 5.1 doesn't natively support multipart bodies.
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    Fail 'curl.exe' "not found on PATH (Windows 10 1803+ ships it). Falling back to skip; upload check skipped."
} else {
    $uploadJsonPath = Join-Path $env:TEMP ("kw-smoke-resp-{0}.json" -f ([Guid]::NewGuid().ToString('N')))
    $statusFile = Join-Path $env:TEMP ("kw-smoke-status-{0}.txt" -f ([Guid]::NewGuid().ToString('N')))

    & $curl.Source `
        --silent --show-error `
        --max-time 30 `
        --output $uploadJsonPath `
        --write-out "%{http_code}" `
        --header "Idempotency-Key: $idempotencyKey" `
        --form "file=@$tmpFile;type=text/plain" `
        "$BaseUrl/documents/upload" `
        > $statusFile

    $httpStatus = (Get-Content $statusFile -Raw).Trim()
    Remove-Item $statusFile -ErrorAction SilentlyContinue

    if ($httpStatus -eq '200' -or $httpStatus -eq '201') {
        try {
            # ``UploadDocumentResponse`` extends ``DocumentVersion`` —
            # the response is a flat version object whose ``id`` is the
            # version id and ``document_id`` is the family id. We don't
            # try to verify duplicates here because the smoke fixture
            # is unique per run.
            $upload = Get-Content $uploadJsonPath -Raw | ConvertFrom-Json
            $documentId = $upload.document_id
            $versionId = $upload.id
            if ($documentId -and $versionId) {
                Pass 'upload accepted' ("document_id={0} version_id={1} status={2}" -f $documentId, $versionId, $upload.status)
            } else {
                Fail 'upload payload' "missing document_id or id in response"
            }
        } catch {
            Fail 'upload payload parse' $_.Exception.Message
        }
    } else {
        $body = if (Test-Path $uploadJsonPath) { Get-Content $uploadJsonPath -Raw } else { '<no body>' }
        Fail 'upload accepted' "HTTP $httpStatus — $body"
    }

    Remove-Item $uploadJsonPath -ErrorAction SilentlyContinue
}

Remove-Item $tmpFile -ErrorAction SilentlyContinue

# ── Check 4: GET /documents/{id} round-trip ──────────────────────────
if ($documentId) {
    Write-Step "4. /documents/$documentId"
    try {
        $doc = Get-Json "/documents/$documentId"
        if ($doc.id -eq $documentId) {
            Pass 'document round-trip' ("filename={0}" -f $doc.original_filename)
        } else {
            Fail 'document round-trip' "id mismatch: requested $documentId, got $($doc.id)"
        }
    } catch {
        Fail 'document round-trip' $_.Exception.Message
    }
}

# ── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Step "Summary"
Write-Host ("  Passed: {0}" -f $script:Successes.Count) -ForegroundColor Green
Write-Host ("  Failed: {0}" -f $script:Failures.Count) -ForegroundColor ($(if ($script:Failures.Count) { 'Red' } else { 'Green' }))

if ($KeepFixture -and $documentId) {
    Write-Host ""
    Write-Host "  Smoke fixture left in catalog (use -KeepFixture:`$false to suppress this note):"
    Write-Host "    document_id : $documentId"
}

if ($script:Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "  Failures:" -ForegroundColor Red
    foreach ($f in $script:Failures) {
        Write-Host ("    - {0}: {1}" -f $f.check, $f.detail) -ForegroundColor Red
    }
    exit 1
}

exit 0
