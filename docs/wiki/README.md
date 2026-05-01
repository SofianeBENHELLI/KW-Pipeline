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

## Source of truth

If a wiki page conflicts with anything in `README.md`, `AGENTS.md`,
`docs/architecture/`, or `docs/adr/`, the in-repo doc wins. The wiki
exists for navigation and orientation, not for storing decisions.
