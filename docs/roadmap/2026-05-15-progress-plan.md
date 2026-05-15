# Progress Plan — 2026-05-15

Rolls forward from
[`2026-05-14-progress-plan.md`](2026-05-14-progress-plan.md). Written
against `main` at `fad7262` — the 2026-05-14 baseline did not move
during the audit cycle. The S+3 sprint the previous doc opened is
**still ahead of us**; this revision is needed because a code-level
audit on 2026-05-15 showed the 2026-05-14 plan was materially wrong
about what's "deferred" vs "already shipped".

Goal of this revision:

1. **Capture the audit correction.** Four out of the five "deferred"
   S+3 items the 2026-05-14 plan named as not-started are in fact
   already on `main` — the plan was reading the issue tracker, not
   the code.
2. **Re-scope S+3 against what's actually open.** The remaining
   genuine gaps + the post-audit follow-ups landed as five new
   PRs on 2026-05-15.
3. **Record the new in-flight PR chain** so reviewers can land them
   in the right order.

---

## A. Audit correction — 2026-05-15

The 2026-05-14 progress plan §A.2 listed five items as "not started":
EPIC-4 trust gap (4.1, 4.2, 3.2), #91 scope predicate sweep, and #40
async-queue retry/reconcile. The 2026-05-15 audit ran code-level
verification against `main` (`fad7262`) and found:

| 2026-05-14 plan said | Reality on `main` | Code evidence |
|---|---|---|
| **4.2** empty-retrieval short-circuit "not started" | ✅ shipped | `apps/api/app/services/knowledge/chat_service.py:149-176` + `EMPTY_RETRIEVAL_ANSWER:65` + tests `test_knowledge_chat.py:224, 243` |
| **4.1** server-side citation validation "not started" | ✅ shipped | `chat_service.py:214-231` + `_validate_citations:349` + `knowledge.chat.unresolved_citation` log |
| **3.2** embedding cache hit/miss counters "not started" | ✅ shipped | `apps/api/app/services/knowledge/projector.py:562` emits `cache_hits` |
| **#91 scope predicate sweep** "not started on 5 named routes" | ✅ shipped on all 5 | `/documents` (`lifecycle.py:1194 _list_documents_with_scope`), `/knowledge/search` (`knowledge.py:517-538`), `/knowledge/atlas` (`knowledge.py:658-671`), `/knowledge/chat` (`knowledge.py:745 accessible_document_id`), `neighborhood` (`knowledge.py:45-58`) |
| **#40** async-queue retry FSM + `/admin/reconcile` "not started" | 🟡 partial — ADR-006 PR-1 + PR-2 (worker harness, lifespan stuck-state recovery, in-memory queue) all shipped; `extraction.retry` event ⟷ existing `extraction.retried`; `extraction.dead_letter` ⟷ existing `extraction.recovery.summary`. The two genuinely-new pieces (`extraction.queue_depth` gauge + runtime `POST /admin/reconcile`) landed in PR #458 on 2026-05-15 | `extraction_recovery.py:96-108` (existing), `extraction_worker.py:138+` (existing), PR #458 (this cycle) |

**Net effect on the sprint.** The 2026-05-14 plan's "close three
deferred items in S+3" framing was over-stated. The only S+3
**implementation** work that actually remained on the morning of
2026-05-15 was:

- **#40** runtime reconcile + queue-depth gauge → shipped 2026-05-15 in PR #458.
- **`actor.id` audit-event backfill** (the residual #91 sub-item the 2026-05-14 plan called out separately) → shipped in PR #460 for the upload routes; status-change + extract paths queued as follow-ups.
- **`#327` multi-scope merge in list / catalog routes** — the 2026-05-14 plan asked to close this in S+3, but the issue body itself reads: *"Implementation work shouldn't start before D.3 [Swym membership client / #218]."* The plan was over-optimistic; #327 stays parked behind #218.

The other "deferred" items (4.1, 4.2, 3.2, the route-sweep half of
#91) were already done before the 2026-05-14 plan was written; the
plan simply didn't check.

## B. Drift sweep — issues that need re-classifying

The audit also found four drift items between the planning docs and
the issue tracker:

| Doc claim | Reality | Action |
|---|---|---|
| 2026-05-04 restructure §F: "#210 / #211 kept open as parent specs" | Both **closed**; EPIC-1 parent #336 + slices #338–#352 took over | Restructure §F needs a one-line note in a future revision. |
| 2026-05-04 restructure §C EPIC-9: "#59 duplicate uploads — KEEP, blocked on D9" | **Closed.** Not in open issues list. | Confirm whether D9 was taken; either way, update the restructure. |
| 2026-05-04 restructure §C EPIC-2 / 2026-05-14 plan S+5: "#84 retention / purge policy" listed as open | **Closed.** Likely superseded by ADR-027 (archive/purge admin tool, accepted). | Update S+5 — remove #84 reference. |
| 2026-05-14 plan §A.1: "Explorer truncation banners… (#321 closed)" | **#321 still open** (last updated 2026-05-07). | Either close #321 or amend the 2026-05-14 plan; this revision does not change either. |

These are docs hygiene, not work items.

## C. The 2026-05-15 PR chain (in landing order)

Five PRs went up on 2026-05-15. They should land in this order:

```
1. #457 — CI hygiene: clear 5 pre-existing ruff violations on main
2. #459 — CI hygiene: clear all 3 mypy errors on main (CI scope)
   ↳ both unblock green CI on every downstream PR

3. #452 — docs: 2026-05-14 progress plan (now scoped to the doc alone
           after the 2026-05-15 split)
4. #454 — feat(orb): graph view is a per-document tab (split out of #452)
5. #455 — feat(api+web): three semantic extraction methods + Method 3
           grey-out (split out of #452, depends on the dropdown wiring)
6. #456 — docs: park Extraction Trust Score & HITL spec (split out of #452)

7. #458 — feat(api): POST /admin/reconcile + extraction.queue_depth (#40)
8. #460 — feat(api): thread actor.id through document.uploaded (#91)
```

Sequencing notes:

- **Hygiene first.** PRs #457 + #459 are small, mechanical, and
  pull-the-ruff-and-mypy-CI-back-to-green. Land them before the
  feature PRs so each downstream PR's CI signal is meaningful.
- **The split chain (#452 / #454 / #455 / #456)** is the rewinding
  of the original over-stuffed PR #452 from 2026-05-14. Each is
  scoped to one concern and reviews independently.
- **#458 (#40)** depends on `main`; no downstream coupling.
- **#460 (actor.id)** depends on `main`; no downstream coupling. The
  threading is purely additive — every existing call site continues
  to work with `actor=None`.

## D. What's actually open after this cycle lands

```
EPIC 4 trust gap                            ✅ done on main (4.1, 4.2, 3.2)
#40 async-queue tail                        ✅ this cycle (PR #458)
#91 scope predicate sweep                   ✅ done on main + 🟡 actor.id (this cycle, upload paths only)
─ remaining: actor.id on document.status_changed (validate / reject routes)
─ remaining: actor.id on extraction.* events (async-path actor)
─ remaining: #327 multi-scope merge (still blocked on #218)

EPIC-1 taxonomy bootstrap                   not started — moves to S+4
#88 reviewer assignment                     not started — moves to S+5
#94 / #96 / #85 ops backbone                not started — moves to S+5
ADR-018 / ADR-021 / ADR-022                 still missing (taxonomy /
                                            audit retention / Postgres)
```

## E. S+3 close-out — what to land next

In priority order:

1. **Land the eight-PR chain above.** That moves CI to green and
   pulls four backlog items into `main`.
2. **`actor.id` on `document.status_changed`** — small follow-up to
   PR #460. Thread `actor` through `update_status` /
   `mark_validated` / `mark_rejected` from the validate / reject
   routes. Estimated ~80 LOC + 4 tests.
3. **`actor.id` on async-path `extraction.*` events** — bigger.
   Add an `actor` field on `ExtractionRequest`, persist it at
   enqueue, read it back at dequeue. Estimated ~150 LOC + 5 tests.
4. **Architecture decisions D3 / D11 / D14** — still pending from
   2026-05-14. The S+4 plan needs these decisions before EPIC-1
   taxonomy can bootstrap cleanly.

S+4 (taxonomy bootstrap) and S+5 (production-shape) keep their
2026-05-14 shape; nothing in the audit changes the work scoped
there.

## F. Open decisions (rolled forward from 2026-05-14)

D3 audit retention + tamper-evidence — **still target: S+3 review**.
D4 reviewer claim model — blocks #88 (push to S+5 if not taken).
D7 first 3DEXPERIENCE container size + auth/context model — still
   external to this team.
D9 duplicate uploads without `document_id` — new family or attach.
   Note: #59 itself is now closed. D9 may have been resolved
   implicitly; needs a one-line confirmation.
D10 customer-facing audience for `/knowledge/chat` — informs whether
   4.1 (now shipped) needs further tightening.
D11 SQLite → Postgres production trajectory — **still target: take
   in S+3 review**, final ADR-022 in S+5.
D14 `(:Section)` vs `(:Chunk)` deprecation in KG payload v0.3 —
   **still target: take in S+3 review**.

## G. What this document does *not* fix

- The pre-existing intermittent vitest flake **#440** (`KnowledgeGraphView`
  selection race). Same plan as 2026-05-14: queued as a side-quest.
- The product naming across `Knowledge Forge` / `Knowledge Explorer`
  / `Orbital` / `KW Pipeline` — marketing call, not architecture.
- The `apps/explorer` refactor (#229) — kept off-sprint by design.
- The AURA / companion layer (#373) — ADR-029 + ADR-030 framed it
  as external; no implementation work scheduled here.

---

*Generated 2026-05-15 by the post-audit re-scope pass. Next review:
after the 2026-05-15 PR chain lands and ADR-018 / draft ADR-021 /
draft ADR-022 are written.*
