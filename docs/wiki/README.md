# Wiki source

These Markdown pages are the source of the project Wiki. They live in the
main repo so they're version-locked with the code; once GitHub's Wiki has
been initialised (the first page must be created through the web UI), the
contents here can be mirrored to the wiki git repo.

## Mirror to GitHub Wiki

```bash
# Step 1 (one-time, manual): visit
#   https://github.com/SofianeBENHELLI/KW-Pipeline/wiki
# and click "Create the first page". Save any placeholder.

# Step 2: clone the wiki repo and replace its contents.
git clone https://github.com/SofianeBENHELLI/KW-Pipeline.wiki.git /tmp/kw-wiki
cp -f docs/wiki/*.md /tmp/kw-wiki/
cd /tmp/kw-wiki
git add . && git commit -m "Sync wiki from docs/wiki/" && git push
```

## Pages

- `Home.md` — entry point.
- `Overview.md` — three-minute summary.
- `Architecture.md` — module layout + boundary protocols.
- `Knowledge-Layer.md` — graph + LLM entity extraction.
- `Operating-Modes.md` — env vars, modes, CI gates.
- `Decisions.md` — index of every ADR.
- `Roadmap.md` — what shipped, what's next.

## Mirror to 3DSwym

The same pages can be published to a 3DSwym community wiki using the
[Publish2Swym](https://btcc.s3.eu-west-1.amazonaws.com/widget-lab/npm/publish-to-swym/dist/publish-to-swym-latest.tgz)
CLI. Each page in this folder already carries the required
`<!-- $PublishToSwym{ ... }$ -->` tag (`Home.md` is the wiki root; the
others use `"parent": "./Home.md"`). `README.md` is excluded from the
glob in `p2sconfig.json`.

```bash
# One-time install
npm install -g https://btcc.s3.eu-west-1.amazonaws.com/widget-lab/npm/publish-to-swym/dist/publish-to-swym-latest.tgz

# Fill in p2sconfig.json (repo root):
#   "baseurl"           — the 3DSwym tenant URL (the full app, not 3DDashboard)
#   "wiki.communities"  — community ID(s), found in <baseurl>/#community:<ID>/...
# Credentials should live in ~/.p2sconfig.json, not in the repo.

publish2swym wiki              # creates or updates pages
publish2swym wiki -m <code>    # if 2FA is enabled (CLI only, never config)
publish2swym wiki -f           # force re-publish, e.g. after editing inside 3DSwym
```

After the first run, Publish2Swym writes `*.md.sidecar` companion files
(persistence mode is set to `sidecar` in `p2sconfig.json` so the MD
sources stay clean). **Commit those sidecar files** — they hold the
3DSwym page IDs and are how subsequent runs update existing pages
instead of creating duplicates.

## Source of truth

If a wiki page conflicts with anything in `README.md`, `AGENTS.md`,
`docs/architecture/`, or `docs/adr/`, the in-repo doc wins. The wiki
exists for navigation and orientation, not for storing decisions.
