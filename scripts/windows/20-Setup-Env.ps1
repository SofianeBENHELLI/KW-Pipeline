#requires -Version 5.1
<#
.SYNOPSIS
    Generate docker\.env and patch the Neo4j password in
    docker-compose.yml.

.DESCRIPTION
    Two artefacts come out of this script:

      1. A strong Neo4j password is written into both NEO4J_AUTH and
         KW_NEO4J_PASSWORD in docker-compose.yml. If the file still
         carries the upstream ``test_password_change_me`` placeholder
         the patch is applied; otherwise the existing password is
         kept (re-runs are idempotent).

      2. A docker\.env file is written with the LLM provider posture
         + the API keys you want passed through to the api container.
         The compose file references each of these as ``${VAR:-}`` so
         missing keys cause the relevant feature to stay disabled —
         no harm done.

    All keys are optional. The most common posture is:

        -GeminiKey <key>            # primary LLM (Gemini 2.5 Flash)
        -AnthropicKey <key>         # fallback LLM
        -VoyageKey <key>            # Phase 3 vector search

    Provider routing follows ADR-013 §6:

        auto       — Gemini wins when its key is set, else Anthropic
        gemini     — pin Gemini (refuses to wire if key missing)
        anthropic  — pin Anthropic (refuses to wire if key missing)

.PARAMETER Provider
    KW_LLM_PROVIDER value. Defaults to ``auto``.

.PARAMETER Neo4jPassword
    Neo4j credential. Pass as a SecureString (the script prompts when
    omitted). If docker-compose.yml has already been patched away
    from the placeholder, pass ``-SkipNeo4jPatch`` to keep it.

.PARAMETER GeminiKey, AnthropicKey, VoyageKey
    LLM / embeddings keys. SecureString — script prompts when
    omitted; pass empty string to clear.

.EXAMPLE
    .\20-Setup-Env.ps1 -Provider auto

    Prompts for every secret interactively, writes docker\.env, and
    patches the Neo4j password in docker-compose.yml.

.EXAMPLE
    .\20-Setup-Env.ps1 -SkipNeo4jPatch -Provider gemini

    Re-run after rotating only the LLM keys; leaves docker-compose.yml
    untouched.
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', 'gemini', 'anthropic')]
    [string]$Provider = 'auto',

    [System.Security.SecureString]$Neo4jPassword,
    [System.Security.SecureString]$GeminiKey,
    [System.Security.SecureString]$AnthropicKey,
    [System.Security.SecureString]$VoyageKey,

    [string]$GeminiModel = '',
    [switch]$SkipNeo4jPatch
)

. "$PSScriptRoot\_lib.ps1"

function ConvertFrom-SecureStringToPlain([System.Security.SecureString]$secure) {
    if ($null -eq $secure) { return '' }
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Read-OptionalSecret([string]$Prompt) {
    $value = Read-Host -Prompt "$Prompt (leave blank to skip)" -AsSecureString
    return $value
}

$repoRoot = Get-RepoRoot
$composeFile = Get-ComposeFile
$envFile = Get-DockerEnvFile

if (-not $SkipNeo4jPatch) {
    Write-Step "Patching Neo4j password in docker-compose.yml"
    $minLength = 8

    # Detect the current password sitting in the compose. Three cases:
    #
    #   1. ``test_password_change_me`` placeholder still in place
    #      → first run; prompt + patch.
    #   2. Already-patched and >= ``$minLength`` characters
    #      → keep as-is unless the caller explicitly passes -Neo4jPassword.
    #   3. Already-patched but < ``$minLength`` characters
    #      → previous run accepted a too-short password (pre-validation
    #        ship of this script). Force a re-patch so Neo4j stops
    #        restart-looping with ``InvalidPasswordException``.
    $compose = Get-Content $composeFile -Raw
    $currentMatch = [regex]::Match($compose, 'NEO4J_AUTH:\s*neo4j/(\S+)')
    $currentPassword = if ($currentMatch.Success) { $currentMatch.Groups[1].Value } else { $null }

    $needsPatch = $false
    $reason = $null
    if (-not $currentPassword) {
        $needsPatch = $true
        $reason = "couldn't find NEO4J_AUTH in compose"
    } elseif ($currentPassword -eq 'test_password_change_me') {
        $needsPatch = $true
        $reason = "placeholder password still present"
    } elseif ($currentPassword.Length -lt $minLength) {
        $needsPatch = $true
        $reason = "current password is $($currentPassword.Length) characters; Neo4j 5.x requires >= $minLength"
        Write-Warn2 "Detected too-short password in compose: $reason. Forcing a re-patch."
    } elseif ($Neo4jPassword) {
        # Caller explicitly passed -Neo4jPassword on a previously-patched
        # file — they want to rotate. Re-patch.
        $needsPatch = $true
        $reason = "caller supplied -Neo4jPassword to rotate"
    }

    if (-not $needsPatch) {
        Write-Done "Neo4j password already set ($($currentPassword.Length) chars) — leaving compose alone. Pass -Neo4jPassword to rotate."
    } else {
        # Loop the prompt so a too-short password gives a second chance
        # instead of throwing.
        $plainNeo4j = $null
        while (-not $plainNeo4j) {
            if (-not $Neo4jPassword) {
                $Neo4jPassword = Read-Host -Prompt "Neo4j password (min $minLength chars; replaces the value currently in docker-compose.yml)" -AsSecureString
            }
            $plainNeo4j = ConvertFrom-SecureStringToPlain $Neo4jPassword
            if ([string]::IsNullOrEmpty($plainNeo4j)) {
                throw "Neo4j password cannot be empty. Re-run with a value or pass -SkipNeo4jPatch."
            }
            if ($plainNeo4j.Length -lt $minLength) {
                Write-Warn2 "Password is $($plainNeo4j.Length) characters; Neo4j 5.x community requires at least $minLength."
                Write-Host "  Pick a longer one, or pass -SkipNeo4jPatch and edit docker\docker-compose.yml manually."
                $Neo4jPassword = $null
                $plainNeo4j = $null
                continue
            }
        }

        # Always patch BOTH NEO4J_AUTH (neo4j service) and KW_NEO4J_PASSWORD
        # (api service) so they stay in sync — the api dies on bolt auth
        # mismatch otherwise. Use anchored regex replace so we don't
        # accidentally rewrite anything else that happens to match.
        $patched = $compose -replace 'NEO4J_AUTH:\s*neo4j/\S+', ('NEO4J_AUTH: neo4j/' + $plainNeo4j)
        $patched = $patched -replace 'KW_NEO4J_PASSWORD:\s*\S+', ('KW_NEO4J_PASSWORD: ' + $plainNeo4j)
        if ($patched -eq $compose) {
            throw "Could not find NEO4J_AUTH / KW_NEO4J_PASSWORD lines in $composeFile to patch. Inspect the file manually."
        }
        Set-Content -Encoding UTF8 -Path $composeFile -Value $patched
        Write-Done "Neo4j password patched in $composeFile ($reason)."
        Write-Warn2 "If you previously brought up the stack, you must wipe the Neo4j volume so the new password takes effect:"
        Write-Host "    docker compose --profile deploy down -v"
        Write-Host "    docker compose --profile deploy up -d"
    }
}

Write-Step "Writing $envFile"

# Prompt for any keys that weren't provided on the CLI.
if ($null -eq $GeminiKey)    { $GeminiKey    = Read-OptionalSecret 'Gemini API key (primary LLM)' }
if ($null -eq $AnthropicKey) { $AnthropicKey = Read-OptionalSecret 'Anthropic API key (fallback LLM)' }
if ($null -eq $VoyageKey)    { $VoyageKey    = Read-OptionalSecret 'Voyage API key (Phase 3 vector search)' }

$geminiPlain    = ConvertFrom-SecureStringToPlain $GeminiKey
$anthropicPlain = ConvertFrom-SecureStringToPlain $AnthropicKey
$voyagePlain    = ConvertFrom-SecureStringToPlain $VoyageKey

$lines = @(
    "# Generated by scripts\windows\20-Setup-Env.ps1 — do not commit (gitignored).",
    "KW_LLM_PROVIDER=$Provider",
    "GEMINI_API_KEY=$geminiPlain",
    "KW_GEMINI_MODEL=$GeminiModel",
    "ANTHROPIC_API_KEY=$anthropicPlain",
    "VOYAGE_API_KEY=$voyagePlain"
)
Set-Content -Encoding ASCII -Path $envFile -Value $lines

# Best-effort tighten ACL: only the current user can read.
try {
    $acl = Get-Acl $envFile
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME, 'FullControl', 'Allow'
    )
    $acl.SetAccessRule($rule)
    Set-Acl -Path $envFile -AclObject $acl
} catch {
    Write-Warn2 "Could not tighten ACL on $envFile (continuing): $_"
}

Write-Done "Wrote $envFile"
Write-Host ""
Write-Step "Summary"
Write-Host "  KW_LLM_PROVIDER     : $Provider"
Write-Host ("  GEMINI_API_KEY      : " + ($(if ($geminiPlain) { 'set' } else { 'empty' })))
Write-Host ("  KW_GEMINI_MODEL     : " + ($(if ($GeminiModel) { $GeminiModel } else { '(SDK default — gemini-2.5-flash)' })))
Write-Host ("  ANTHROPIC_API_KEY   : " + ($(if ($anthropicPlain) { 'set' } else { 'empty' })))
Write-Host ("  VOYAGE_API_KEY      : " + ($(if ($voyagePlain) { 'set' } else { 'empty' })))
Write-Host ""
Write-Host "  Next: .\Start.ps1"
