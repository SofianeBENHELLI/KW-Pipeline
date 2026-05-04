# Audit follow-ups session — 2026-05-04

This handover captures the **audit follow-ups session** that landed on
top of the 2026-05-04 Phase 2 closure. Six slices shipped on one
branch (`claude/audit-codebase-vgXdv` → **PR #205, draft**) executing
the punch-list from the audit done at the head of this PR.

If you're picking up where this left off, jump to
[What's next](#whats-next) and [Open decisions](#open-decisions).

For Phase 2 final state, see
[2026-05-04-phase-2-closure.md](2026-05-04-phase-2-closure.md). For
the broader `main` snapshot before the audit, see
[2026-05-03-session.md](2026-05-03-session.md).

---

## TL;DR

- Six audit-driven slices merged into PR #205 as one branch, 7
  commits. CI green on the latest push.
- Phase 3 chat surface (`POST /knowledge/chat`) is reachable
  end-to-end — backend service, web `<ChatPanel/>`, widget search
  panel ported. Chat in the widget is the only A-track follow-up.
- Three more backlog items closed: section batching (#195), opt-in
  spaCy NER (#190), batch-upload UI (#82 UI half).
- Backend: 757 tests, **95.03% coverage** (gate is 95). Mypy and ruff
  clean.
- Web: 105 vitest pass across 7 suites. Bundle budgets green
  (initial 69.1 KB / graph 514.2 KB / KG 3.0 KB).

---

## What shipped on PR #205

| Slice | Commit | Result |
|---|---|---|
| A.1 — Widget search panel | `9213fb4` | Ports `<SearchPanel/>` into the 3DX widget. Same disabled / error / empty / populated state shape as the web panel. |
| A.2 — Chat service skeleton (backend) | `c85eda6` | `KnowledgeChatService` + `POST /knowledge/chat`, mode dispatch (`rag` / `graph` / `hybrid`). `LLMClient.complete_text` added. New error code `KW_CHAT_DISABLED`. ADR-013 still holds — no LangChain. |
| A.2 — ruff format follow-up | `fde84f4` | CI's pinned ruff caught a quote-style nit the locally-installed ruff didn't. No semantic change. |
| A.3 — `<ChatPanel/>` + `<ChatModeToggle/>` | `89a17cc` | Wires the new endpoint into `apps/web`. Citations clickable; parent `selectDocument`s the cited document. |
| B.4 — #195 Section batching | `5312717` | `EntityExtractor.max_sections_per_call` now wired (was reserved). Default stays at **1** (no behavioural change); >1 enables batched calls with per-section demultiplexing. |
| B.5 — #190 Opt-in spaCy NER | `44911b4` | New `SpacyNerEnricher` for person/organization assets. Lives behind a `ner` extra and `KW_NER_ENABLED`. Default install untouched. |
| C.7 — #82 (UI half) batch-upload | `9e52f5a` | Multi-select on the existing PipelineWidget upload. ≥2 files dispatches to `POST /documents/upload/batch`; per-file outcomes render inline alongside the aggregate `summary`. |

---

## Current `main`-relative state

| Item | Value |
|---|---|
| Branch | `claude/audit-codebase-vgXdv` |
| HEAD | `9e52f5a` — *Closes #82 (UI half) — batch-upload UI in apps/web* |
| PR | [#205](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/205) — **draft**, 7 commits |
| Backend tests | 757 passed (was 660), coverage **95.03%** |
| Web tests | 105 passed across 7 suites (was 81 across 5) |
| Lint / type-check | ruff + ruff format clean (api), mypy clean (46 src files), tsc clean (web) |
| OpenAPI snapshot | regenerated; `apps/web/src/api/generated/schema.ts` in sync |

### CI flake to know about

Frontend (vitest, node 22) job failed once on commit `5312717`
(B.4, a backend-only commit) and passed on every other push. Locally
vitest is green across 3 consecutive runs. Same pattern as the
historical [#198](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/198)
flake. If it fires again on a frontend PR, dig into the actual
failing test before assuming it's a flake.

---

## What's next

The product-mission gap analysis from the audit conversation is the
canonical next-step list. Ranked by how much each undercuts the
"governed, trusted, reviewable, traceable" claim:

### Immediate (next 1–2 sprints)

1. **Widget chat panel** — one-file follow-up to A.3. Same shape as
   `apps/web/src/features/chat/ChatPanel.tsx`. Closes the
   "Phase 3 reachable everywhere" story end-to-end.
2. **ADR-016 — chat surface mode taxonomy** — the chat skeleton
   shipped without a dedicated ADR. Pin the RAG / GraphRAG / Hybrid
   contract before more callers depend on it.
3. **Citation validation on the chat answer** — today the LLM can
   emit a `[chunk_id]` it didn't see. Validate server-side against
   the citation list before returning. Small, deterministic,
   high-trust win.
4. **Embedding cache hit-rate observability** — emit
   `knowledge.embeddings.cache.{hit,miss}` counters from
   `KnowledgeProjector.project_chunks` so we can verify the
   `(model_id, sha256(text))` cache works under real load.

### Governance core (multi-PR — pre-multi-tenant gating)

5. **#83 — Auth + 3DEXPERIENCE user context.** Without this, every
   "governed" claim is aspirational. Drives **everything** below.
6. **#91 — Workspace / project scoping.** Pairs with #83.
7. **#26 residual — persist audit events as data**, not just log
   lines. New `audit_events` append-only table.
8. **#88 — Reviewer assignment / locking / comments.** Without this
   "expert-validated" doesn't survive >1 reviewer.

### Operational maturity

9. **#40 — Async parser/extraction queue.** Validate path now hops
   through LLM + embedding writes; this is the next bottleneck under
   real document size or concurrency.
10. **#96 — Runtime metrics + readiness probes + ingestion SLAs.**
    Depends on the structured logs from #42.
11. **#84 — Retention / purge policy.** Right-to-be-forgotten +
    storage cost.
12. **#94 — Backup / restore / DR runbook.** SQLite + filesystem are
    today's source of truth with no documented restore.
13. **#85 — Malware scanning** on uploads.

### RAG / chat hardening

14. **Hybrid retrieval (BM25 + vector)** — better recall for
    keyword-heavy queries.
15. **Reranking step** before the LLM call.
16. **Eval harness** — golden Q&A pairs + a CI gate on retrieval
    quality.

### Knowledge fabric

17. **#22 — Canonical knowledge-asset taxonomy.** Today's entity
    types are free-form strings; canonicalization across documents
    is impossible without a shared schema.
18. **Entity resolution / canonicalization** across documents
    (depends on #22).
19. **#89 — Source metadata + 3DEXPERIENCE object links.** Triples
    can't trace back to PLM/CAD without this.
20. **#124 residual — reconciliation HTTP/CLI surface.** Service
    layer exists; operator-facing route is the small follow-up.

### Document intelligence

21. **#47 — OCR for scanned PDFs** (tesseract behind an opt-in extra).
22. **Table / structured data extraction.**
23. **More parsers** — XLSX, HTML, EML, CSV.
24. **#90 — Export validated assets / handoff package.**

---

## Open decisions

These need a product / architecture call **before** the
corresponding work starts. Ordered by blast radius:

1. **Auth model for #83.** Options: 3DEXPERIENCE SSO (binds us to
   3DX as the only host), opaque API tokens (works everywhere, no
   user identity), OAuth/OIDC against an external IdP (most flexible,
   most ops). **Drives #88, #91, #26 residual.**

2. **Workspace boundary for #91.** Per project? Per 3DX
   collaborative space? Per tenant? Affects every downstream query
   (catalog list, knowledge graph page, search, chat).

3. **Audit-event retention + tamper-evidence.** How long do we keep
   them? Append-only is easy; cryptographic chaining is real work.
   Drives #26's residual schema.

4. **Reviewer claim model for #88.** Optimistic (anyone can override
   anyone else's review with an audit trail) or pessimistic
   (lock-and-release). Drives the FSM for the validate route.

5. **Async queue technology for #40.** Reuse SQLite (simplest, single
   process), Redis (existing-stack tax), or a real broker like NATS
   / Postgres-as-queue. Drives the deployment footprint significantly.

6. **Chat answer surface — direct LLM or audited wrapper?**
   Customer-facing chat likely needs a server-side gate that strips
   uncited claims. Demo chat can skip it. Pick before #14–#16
   (hardening) starts.

7. **Voyage SDK pin.** Today's `voyageai>=0.2,<0.3` cap exists
   because 0.3 pulls LangChain transitively (forbidden by ADR-013).
   Track upstream; bump as soon as the LangChain edge is removed.

8. **Customer-facing endpoints.** Is the audience for
   `POST /knowledge/chat` reviewers (internal, demo) or end-users
   (production, strict)? The hardening backlog (#14–#16) only
   matters for the second case.

---

## Run the demo locally (unchanged from prior handover)

```bash
# from the repo root:
./scripts/demo-backend.sh
./scripts/demo-frontend.sh
open demo.html
```

Phase 3 in the demo requires both `ANTHROPIC_API_KEY` and
`VOYAGE_API_KEY` in your local `.env` (gitignored). Without them,
the search and chat panels render their disabled-state remediation
copy verbatim. Without the knowledge layer kill switch
(`KW_KNOWLEDGE_LAYER_ENABLED=true`), neither route is wired.

---

## Quick verification recipe

```bash
# Backend
cd apps/api
../../.venv312/bin/python -m pytest --cov=app --cov-fail-under=95 -q
# Expected: 757 passed, coverage ≥ 95%
../../.venv312/bin/python -m ruff check && \
  ../../.venv312/bin/python -m ruff format --check && \
  ../../.venv312/bin/python -m mypy app
# Expected: all clean

# Web
cd ../web
npx vitest run
npm run typecheck
npm run build
npm run bundle:check
# Expected: 105 passed, build clean, all budgets satisfied
```

---

## API keys

Unchanged from the prior handover:

- `ANTHROPIC_API_KEY` — Phase 2 entity extraction + Phase 3 chat.
- `VOYAGE_API_KEY` — Phase 3 vector embeddings.

New optional flag for #190:

- `KW_NER_ENABLED=true` — enables the spaCy NER enricher. Requires
  `pip install -e .[ner]` and `python -m spacy download
  en_core_web_sm`.

New optional knob for #195:

- `EntityExtractor(max_sections_per_call=8)` — opt-in section
  batching. Default is 1 (per-section calls, no behavioural change).

---

*Generated 2026-05-04 by the audit-followups session. Next handover
should land in `docs/handover/<date>-session.md` once #83 + #91 land
and the chat surface is hardened against the citation validation
gap.*
