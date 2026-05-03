# Workstation deploy — backend reachable from 3DEXPERIENCE

Run the KW-Pipeline backend (FastAPI + Neo4j) on a workstation, expose it at a stable HTTPS hostname, and let the deployed 3DEXPERIENCE widget call it without any port-forwarding, public IP, or DNS-record juggling.

## What this deploys

```
3DEXPERIENCE widget (https://*.3dexperience.3ds.com)
        │ HTTPS
        ▼
Cloudflare edge (free, handles TLS / WAF / DDoS)
        │ outbound TCP from your workstation
        ▼
cloudflared connector (docker container on workstation)
        │ docker network
        ▼
kw-pipeline-api (FastAPI, port 8000) ──► kw-pipeline-neo4j (5.23)
```

Hostname used in this runbook: **`kw-api.benhelli.org`**. Substitute your own subdomain at the steps that mention it.

## One-time setup

### 0. Prerequisites on the workstation
```bash
# Docker Engine 24+ and Docker Compose v2 (ships with Desktop on macOS).
docker --version && docker compose version

# Cloudflared CLI (only needed locally for the one-time tunnel + DNS
# wiring; the ongoing connector runs as a docker sidecar).
brew install cloudflared        # macOS
# or:  https://github.com/cloudflare/cloudflared/releases (Linux .deb / .rpm)
```

You also need an **`benhelli.org`** zone in Cloudflare (free plan is fine). If the domain isn't there yet, change the nameservers at your registrar to the pair Cloudflare gives you and wait an hour for propagation.

### 1. Authenticate cloudflared with the Cloudflare account

```bash
cloudflared tunnel login
```

This opens a browser, you pick `benhelli.org`, and Cloudflare drops `~/.cloudflared/cert.pem`. That cert grants tunnel-create rights for the zone you picked.

### 2. Create the tunnel

```bash
cloudflared tunnel create kw-api
```

Cloudflare prints a UUID and writes the credentials to `~/.cloudflared/<UUID>.json`. Copy both into the repo's compose-mounted directory:

```bash
cp ~/.cloudflared/<UUID>.json docker/cloudflared/<UUID>.json
cp docker/cloudflared/config.yml.example docker/cloudflared/config.yml

# Replace the two REPLACE-WITH-TUNNEL-UUID placeholders in config.yml
# with the actual UUID. macOS:
sed -i '' "s/REPLACE-WITH-TUNNEL-UUID/<UUID>/g" docker/cloudflared/config.yml
# Linux:
sed -i "s/REPLACE-WITH-TUNNEL-UUID/<UUID>/g" docker/cloudflared/config.yml
```

The `docker/cloudflared/.gitignore` keeps both files out of source control.

### 3. Route the public hostname to the tunnel

```bash
cloudflared tunnel route dns kw-api kw-api.benhelli.org
```

Cloudflare creates a CNAME record `kw-api.benhelli.org → <UUID>.cfargotunnel.com` in your zone. Lookup propagates instantly through Cloudflare's resolver.

### 4. (Recommended) Lock the hostname behind Cloudflare Access

The backend has **no application-level auth** today. If you don't gate the tunnel, anyone with the URL can upload + extract documents on your workstation.

**Free Cloudflare Access policy** (one-screen setup):

1. Cloudflare dashboard → **Zero Trust** → **Access** → **Applications** → **Add an application** → **Self-hosted**.
2. Application domain: `kw-api.benhelli.org`.
3. **Add a policy** → name "Allow operator" → Action **Allow** → Include rule **Emails: your-email@example.com** (and any colleagues who need it).
4. Save. The first time anyone hits the URL they get a one-time email-link login; the cookie lasts 24 h by default.

Skip this only if you're OK with the URL being open to the internet (e.g. it's a throwaway demo and you'll tear it down within hours).

### 5. Bring everything up

From the repo root on the workstation:

```bash
docker compose -f docker/docker-compose.yml --profile deploy up -d
```

This starts three containers: `neo4j`, `api`, and `cloudflared`. The compose file's `restart: unless-stopped` policy auto-resumes them on workstation reboot.

Verify:

```bash
# 1. API is up locally on the workstation.
curl -s http://127.0.0.1:8000/health
# → {"status":"ok"}

# 2. Tunnel is healthy.
docker logs kw-pipeline-cloudflared 2>&1 | grep "Registered tunnel connection"
# → 4× "Registered tunnel connection" lines (one per Cloudflare PoP)

# 3. Public URL works (from anywhere on the internet).
curl -s https://kw-api.benhelli.org/health
# → {"status":"ok"}
# If you set up Cloudflare Access, this returns the Access login page
# until you authenticate; an authenticated browser hits the API directly.
```

### 6. Point the widget at the new URL

In 3DEXPERIENCE → KW FORGE tab → widget settings cog (top-right of the widget) → paste `https://kw-api.benhelli.org` as the API base URL → save. Hard-reload the dashboard tab (`Cmd+Shift+R`). The "Backend health" pill should turn green.

## Day-to-day operations

### Ship a new build of the API
```bash
cd /path/to/KW-Pipeline
git pull
docker compose -f docker/docker-compose.yml --profile deploy build api
docker compose -f docker/docker-compose.yml --profile deploy up -d api
```
Compose recreates only the `api` container; Neo4j and the tunnel keep running, so the widget never sees more than ~2 s of "Backend health" red while uvicorn restarts.

### Inspect logs
```bash
docker logs -f kw-pipeline-api          # FastAPI stdout (JSON, set by Dockerfile)
docker logs -f kw-pipeline-cloudflared  # tunnel + edge connection events
docker logs -f kw-pipeline-neo4j        # only when debugging Cypher errors
```

### Pause the deployment
```bash
docker compose -f docker/docker-compose.yml --profile deploy stop
# Resume with the original `up -d` command.
```

### Wipe the demo data (dangerous — deletes uploaded documents)
```bash
docker compose -f docker/docker-compose.yml --profile deploy down
docker volume rm kw-pipeline_api_data kw-pipeline_neo4j_data
```

### API keys (Anthropic / Voyage)
The `api` container reads `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` from the host's environment (or a `docker/.env` file Docker Compose loads automatically). Drop them in there once and `docker compose up -d api` to pick them up — no image rebuild needed.

```bash
# docker/.env  (gitignored — covered by docker/cloudflared/.gitignore's
# parent-folder pattern + the repo's existing top-level .gitignore)
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

## What this runbook deliberately doesn't cover

- **Multiple workstations / failover.** Cloudflare Tunnel can multi-home — run a second cloudflared on a backup machine, both register the same tunnel, edge load-balances. Out of scope for the single-workstation demo path.
- **A real production database.** Neo4j Community is fine for a demo workload; switching to Neo4j AuraDB or self-hosted Enterprise is a `KW_NEO4J_URI` change.
- **Backups.** The `api_data` and `neo4j_data` Docker volumes live under `/var/lib/docker/volumes/`. Snapshot them with whatever your workstation already uses (Time Machine, restic, …); see [`docs/runbook/reconciliation.md`](reconciliation.md) for the in-app reconciliation path that lets you rebuild the graph from the SQLite catalog if Neo4j is wiped.
