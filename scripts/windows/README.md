# KW-Pipeline — Windows workstation deploy scripts

PowerShell toolkit for running the KW-Pipeline backend (FastAPI +
Neo4j + Cloudflare Tunnel sidecar) on a Windows workstation with
Docker Desktop.

This is the Windows companion to `docs/runbook/workstation-deploy.md`.
The Linux runbook still applies — these scripts just automate the
manual steps for Windows operators.

## Prerequisites

- **Docker Desktop**, running, with **"Start Docker Desktop when you
  log in"** checked under Settings → General. The scripts assume
  Docker is available; they don't install it.
- **PowerShell 5.1+** (built into Windows 10/11).
- **A Cloudflare zone** you control (free plan is fine). The first
  setup step opens a browser to authorise a tunnel against it.
- **Your API keys** at hand (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
  `VOYAGE_API_KEY`). All optional — leave any empty to skip the
  matching feature.

## Quickstart — one-liner first-time setup

From a fresh PowerShell window in the repo root:

```powershell
cd scripts\windows
.\Bootstrap.ps1 -Hostname kw-api.<your-zone>
```

That walks through every step interactively:

1. Verifies Docker, installs `cloudflared` + `git` via winget if
   missing.
2. Browser-authenticates `cloudflared` with your Cloudflare account.
3. Creates the named tunnel, writes `docker\cloudflared\config.yml`,
   routes the DNS CNAME.
4. Prompts for the Neo4j password and the LLM/embedding API keys,
   writes them to `docker\.env`.
5. Registers a logon-triggered scheduled task so the stack survives
   reboots.
6. Brings the stack up and prints status.

After it returns, `https://kw-api.<your-zone>/health` should answer
`{"status":"ok"}`.

## Scripts in this folder

| Script | Purpose |
|---|---|
| `Bootstrap.ps1` | One-shot first-time setup. Runs the three numbered scripts in order. |
| `00-Install-Prereqs.ps1` | Verify Docker; winget-install `cloudflared` + `git` if missing. |
| `10-Setup-Tunnel.ps1` | `cloudflared` login + tunnel create + DNS route + render `config.yml`. |
| `20-Setup-Env.ps1` | Write `docker\.env`, patch the Neo4j password in `docker-compose.yml`. |
| `Start.ps1` | `docker compose up -d` (deploy profile) + wait for `/health`. |
| `Stop.ps1` | `docker compose stop` (volumes preserved). |
| `Status.ps1` | One-screen summary: containers, `/health`, active LLM provider, tunnel registration. |
| `Logs.ps1` | Tail one container's logs. `-Service api|cloudflared|neo4j` (default `api`). |
| `Update.ps1` | `git pull` + rebuild api image + recreate api container. |
| `Setup-AutoStart.ps1` | Register / remove the logon scheduled task. |
| `_lib.ps1` | Shared helpers. Dot-sourced by every other script. |

## Day-to-day flow

```powershell
# Start the deploy (or after reboot, if auto-start is off)
.\Start.ps1

# See what's happening
.\Status.ps1

# Tail logs from one container
.\Logs.ps1 -Service api
.\Logs.ps1 -Service cloudflared

# Push a new backend version
.\Update.ps1

# Pause everything; data survives
.\Stop.ps1

# Switch LLM provider for an A/B test
.\20-Setup-Env.ps1 -Provider anthropic -SkipNeo4jPatch
.\Update.ps1
```

## Re-running on the same machine

Every script is idempotent:

- `00-Install-Prereqs.ps1` — skips installs when the tools are present.
- `10-Setup-Tunnel.ps1` — reuses an existing tunnel with the same name
  in your account; only re-renders the config file.
- `20-Setup-Env.ps1` — overwrites `docker\.env` and patches the
  Neo4j password only when the file still carries the upstream
  placeholder.
- `Setup-AutoStart.ps1` — `-Force`-replaces any existing task with the
  same name.

## Troubleshooting

**"Docker Desktop is not running"** — start it from the Start menu.
Wait for the whale icon to go solid in the taskbar before re-running.

**Tunnel containers exit with auth errors** — delete
`docker\cloudflared\<UUID>.json` and re-run `10-Setup-Tunnel.ps1`. The
script will re-issue credentials.

**Public URL returns 502 after waking from sleep** — Docker Desktop
sometimes restarts the cloudflared sidecar slowly after a sleep/wake
cycle. `.\Status.ps1` should show the registered-connection count
returning to 4 within ~30 s; `.\Stop.ps1` then `.\Start.ps1` forces a
clean restart.

**ScheduledTask is registered but doesn't run at logon** — open
`Task Scheduler` → `Task Scheduler Library`, find `KWPipelineDeploy`,
right-click → Properties. Check the **Last Run Result** column for
the underlying error. The 60 s default delay is usually enough; bump
it with `.\Setup-AutoStart.ps1 -DelaySeconds 120` if Docker Desktop
takes longer to come up on your machine.

**LLM stays disabled** — `.\Status.ps1` shows `active_provider: <none>`
when no key is configured for the resolved provider, or when
`KW_KNOWLEDGE_LAYER_ENABLED` is unset (the compose file sets it to
`true` by default — don't change that line). Re-run
`.\20-Setup-Env.ps1 -SkipNeo4jPatch` to fix the keys without touching
Neo4j; then `.\Update.ps1` to pick them up.

## Running scripts past the execution policy

If PowerShell refuses to execute the scripts ("running scripts is
disabled on this system"), either run them directly:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Start.ps1
```

…or relax the policy for your user once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
