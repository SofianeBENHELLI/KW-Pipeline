#requires -Version 5.1
<#
.SYNOPSIS
    Tail logs from one of the deploy containers.

.PARAMETER Service
    Which container to tail. Defaults to ``api``.

.PARAMETER Lines
    How many existing lines to print before tailing. Defaults to 200.

.EXAMPLE
    .\Logs.ps1
    .\Logs.ps1 -Service cloudflared
    .\Logs.ps1 -Service neo4j -Lines 50
#>
[CmdletBinding()]
param(
    [ValidateSet('api', 'cloudflared', 'neo4j')]
    [string]$Service = 'api',

    [int]$Lines = 200
)

. "$PSScriptRoot\_lib.ps1"
Assert-Docker

$container = "kw-pipeline-$Service"
Write-Step "Tailing $container (Ctrl-C to stop)"
docker logs --tail $Lines -f $container
