# Architecture — Intelligent Care Coordination & Referral Management Platform

Companion to [`project_requirement.md`](project_requirement.md) (what's required) and
[`rules.md`](rules.md) (constraints/conventions). This document is how the system is actually built,
verified directly against the code in `app/`, `mock_systems/`, `knowledge_base/`, and `static/` as of
2026-08-13. The authoritative planning narrative (with full ADR rationale) lives in
`CAPSTONE_IMPLEMENTATION_GUIDE.md` §3; this doc summarizes and cross-checks it against what shipped.

## 1. Style: Modular Monolith + Separately-Packaged Mocks

**ADR-001.** One FastAPI process (`app/main.py`) hosts every bounded context as its own
router/model/service package, communicating only through a thin `app/services/` layer — no
cross-module reach into another module's internals. The four externally-facing bounded contexts a
real deployment would split into microservices (Referral/Care Coordination, Identity, Scheduling,
Analytics) are designed as that target state in the guide but implemented here as one deployable, for
a realistic capstone timeline (no distributed transactions, one migration history, one
`docker compose up`).

Every **external organization** the platform would integrate with in real life (EHR, payer,
provider directory, scheduling system, notification/comms) is mocked as its own **separate FastAPI
sub-application**, each with its own in-memory dataset and its own MCP mount — mounted into the same
process for convenience but structurally isolated, so extracting any one of them into a real service
later is a mount-point change, not a rewrite:

```
app.mount("/mock/ehr", ...)
app.mount("/mock/payer", ...)
app.mount("/mock/directory", ...)
app.mount("/mock/scheduling", ...)
app.mount("/mock/notification", ...)
app.mount("/kb", ...)          # first-party, not external — see §6
app.mount("/app", ...)          # the dashboard, static files
```

## 2. Orchestration vs. Choreography

**ADR-002.** The clinical referral workflow itself (intake → eligibility → recommend → approve →
schedule → notify) is **orchestrated** by a single LangGraph `StateGraph` that owns sequencing,
retries, and human-in-the-loop pauses — a regulated, auditable process needs one thing that knows the
whole sequence. Status fan-out (SSE live updates, in-app notifications, analytics) is **choreographed**:
each workflow step writes a domain event to a transactional outbox table (`outbox_events`), and
independent consumers react without the workflow knowing they exist.

## 3. Domain Model

### 3.1 Entity groups

| Group | Tables | Notes |
|---|---|---|
| Identity/RBAC | `users`, `roles`, `permissions`, `user_roles`, `role_permissions` | `User` is a login only |
| Clinical identity | `patients`, `doctors` | Each optionally FK'd to one `users.id`; the FK is what "my own record" resolves through |
| Referral workflow | `referral_requests`, `referral_documents`, `specialist_notes`, `referral_outcomes` | `referral_requests.status` is the LangGraph-driven state machine's visible projection |
| Scheduling | `doctor_availability`, `schedule_slots`, `appointments` | Slots are materialized from recurring weekly availability |
| Records | `medical_records`, `medical_record_documents` | `doctor_id` nullable — a patient's own quick-upload has no treating doctor yet |
| Insurance (partially wired) | `insurance_plans`, `doctor_insurance_networks` | Modeled but **not** the actual eligibility check today — see §9 Known Simplifications |
| Cross-cutting | `outbox_events`, `notifications`, `audit_logs`, `provider_directory_links` | Outbox is the event backbone; provider_directory_links bridges the mock directory's synthetic IDs to real `doctors` rows |

### 3.2 Three-identity model (the most important thing to understand about this schema)

A `User` (login credentials) is **not** the same row as a `Patient` or `Doctor` (clinical identity).
They're connected by an optional, nullable `user_id` FK on `patients`/`doctors`. This means:

- Granting a role (`patient`, `specialist`, ...) controls **what permissions** an account has.
- Linking a user to a patient/doctor row (admin-only action, `POST /admin/users/{id}/link-patient/{id}`)
  controls **whose data** "my own" resolves to.
- Both steps are required, independently, for a self-service account to see anything under "my own" —
  a role with no link sees an empty list, not an error. This is the #1 support-style confusion this
  platform produces, documented explicitly in `RUNBOOK.md`'s bootstrap steps and `WORKFLOW.md` §1/§10.

### 3.3 Referral status state machine

```
submitted → intake_processing ─┬→ awaiting_documents (dead end until a fresh document upload re-triggers intake)
                                └→ eligibility_checking ─┬→ eligibility_denied ──(coordinator override)──┐
                                                          └→ [specialist pre-chosen?]                     │
                                                              ├─ yes → book_real_appointment ←─────────────┘
                                                              └─ no  → awaiting_specialist_approval (human-in-the-loop)
                                                                          → scheduling → scheduled
scheduled → completed (via a recorded consult outcome)
any non-terminal state → cancelled
```
Full node-by-node code path: `app/agents/graph.py::build_graph`. `route_after_eligibility` and
`route_after_escalation` both branch on `state["specialist_preselected"]` — a referral created with an
already-chosen real platform doctor skips the external-mock-directory recommendation step entirely and
books straight against real `schedule_slots`; one created without a pre-chosen doctor goes through
`recommend_specialist` (ranks candidates from the **mocked** provider directory) →
`await_specialist_approval` (a real `interrupt()` pause) → `schedule_appointment`.

### 3.4 Two distinct "doctor" ID spaces — a deliberate seam, not a bug

`recommend_specialist` ranks candidates from `mock_systems/provider_directory_mock`'s own synthetic,
unnamespaced integer ID space — a stand-in for a real external directory this platform doesn't own.
Real, bookable platform doctors live in `doctors` with their own autoincrementing IDs. These spaces
are **not** assumed to coincide. `provider_directory_links` is the explicit, care-coordinator-vouched
mapping between them (`external_doctor_id` + `source_system` → `doctors.id`) — optional; a referral
resumed with no mapping behaves exactly as it did before this table existed (the appointment is booked
against whatever `Doctor` the recommendation, once approved, resolves to via the existing scheduling
node). The **only** way to skip the external directory entirely is the "specialist pre-selected" path
in §3.3, which points `specialist_id` straight at a real `doctors.id` from the start (used by direct
appointment booking, and by referrals where the requester picked a real doctor up front).

## 4. Agent / Workflow Layer (LangGraph)

**ADR-004.** FastAPI processes are stateless — the workflow graph's state lives in Postgres via
LangGraph's `AsyncPostgresSaver` checkpointer, keyed by `thread_id = referral_id` (workflow graph) or
`thread_id = chat-{user_id}-{session_id}` (assistant graph). A human-in-the-loop pause survives a
process restart and can be resumed by any replica — required for a realistic multi-day "pending
coordinator approval" queue, not a hypothetical.

- **Nodes** (`app/agents/nodes/`): `intake`, `eligibility`/`escalate_eligibility`, `specialist`,
  `scheduling` (`scheduling_node` for the external-directory path, `book_real_appointment_node` for
  the pre-selected-real-doctor path), `summarizer`, `notify`.
- **State**: `app/agents/state.py::ReferralState`, a `TypedDict` threaded through every node.
- **Two interrupt points**: `await_specialist_approval` (final specialist pick) and
  `escalate_eligibility_node` (a denied referral pauses for a coordinator's comment/override, resuming
  either into `recommend_specialist` or straight to `book_real_appointment_node` depending on whether a
  real doctor was already chosen — same branch logic as the non-denied path).
- **Reused services, not duplicated node logic**: `app/services/doctor_recommendation.py` (ranking:
  specialty inference, rule-based scoring, LLM re-ranking) and `app/services/eligibility.py` back
  *both* the LangGraph node path and the direct-booking REST path (`GET /doctors/recommend`) — the
  nodes are thin wrappers around these services, not separate implementations.
- **Compiled once** at FastAPI lifespan startup (`app/main.py`), not per-request — unlike the
  assistant graph (§5).

## 5. Conversational Assistant

Built **fresh per chat request** (`app/agents/assistant_graph.py::build_assistant_graph`), not cached
at startup — the deliberate reason is that its `MultiServerMCPClient` must carry the *calling user's
own JWT* so every tool call it makes runs through the platform's real, existing authorization as that
user, never a shared system identity. It talks to two MCP servers:

- `platform` (`{api_base_url}/mcp`, Bearer-authenticated) — the app's own REST routes, converted to
  MCP tools by `fastapi_mcp`.
- `knowledgebase` (`{kb_base_url}/kb/mcp`, unauthenticated — non-PHI reference content only).

`create_react_agent` (LangGraph prebuilt) runs the tool-calling loop, using the same Postgres
checkpointer the workflow graph uses. **Role-scoped tool allowlists and system prompts**
(`ROLE_TOOL_ALLOWLIST`, `ROLE_SYSTEM_PROMPTS`) mean a `patient` session and a `care_coordinator`
session literally cannot call the same tool set — enforced by filtering the tools handed to
`create_react_agent`, on top of (not instead of) every tool's own route-level permission check. See
[`rules.md`](rules.md) §Security Model for why unscoped routes were deliberately excluded from the
tool surface until they were fixed.

Falls back to `None` (chat route degrades to a canned FAQ responder) with **zero** network/DB touch if
no LLM is configured — see ADR-005 in §9.

## 6. Knowledge Base / RAG

`knowledge_base/` is a first-party MCP server — **not** under `mock_systems/` (that's reserved for
external-org stand-ins) — built directly on the raw `mcp` SDK's `FastMCP`, deliberately not
`fastapi_mcp` (confirmed by reading `fastapi_mcp/server.py`: it only ever converts REST routes into
tools, no resource/prompt primitive exists there). This is the one MCP server in the project exposing
all three MCP primitives:

- **Tool**: `search_policy_knowledge_base` (BM25Plus lexical search over `nb/`).
- **Resources**: `kb://policies`, `kb://policies/{doc_id}`.
- **Prompts**: `explain_referral_process`, `compare_policies` — fetched via
  `MultiServerMCPClient.get_prompt()` and folded into every role's assistant system prompt at graph-
  build time (§5), not just reachable by an external MCP client.

Retrieval is **BM25Plus, not BM25Okapi** — a real correctness fix, not a style choice: with only 8
short documents and heavy cross-referencing between them, classic Okapi's IDF term goes to exactly
zero (or negative) for terms appearing in exactly half the corpus, which real queries hit. No
embeddings, no API key — consistent with the platform's zero-required-credentials design (§9, ADR-005).
`knowledge_base/main.py`'s `session_manager.run()` is composed directly into `app/main.py`'s own
lifespan (FastAPI's `app.mount()` does **not** propagate a mounted sub-app's own `lifespan=`).

## 7. Event Backbone

**ADR-003 (event portion).** `outbox_events` is a transactional outbox — written in the *same* DB
transaction as the state change that caused it, so there's no dual-write gap between "the state
changed" and "an event says it changed." `app/events/publisher.py` polls unpublished rows and fans
them out via an in-process `app/events/broadcaster.py` (swappable for real pub/sub if this ever needs
to span more than one process). Consumers: the referral detail page's live SSE status indicator
(`GET /referral/requests/{id}/events`), the referral timeline tab (reads the *same* outbox rows back,
never deleted — `GET /referral/requests/{id}/timeline`), and `avg_time_to_schedule_hours` in
analytics (paired `referral.submitted` → `referral.appointment.scheduled` timestamps, more correct
than trusting `updated_at`, which only ever holds the latest transition).

## 8. API / RBAC / Auth Layer

**ADR-006.** RBAC is DB-modeled (`Role`/`Permission`/`user_roles`/`role_permissions`), not a hardcoded
`if user.role == "admin"` string check. `app/api/dependencies/auth.py::require_permission(name)` is a
dependency factory: loads the caller's roles→permissions, 403s unless `name` (or the universal
`admin:*` bypass) is present. JWT bearer auth (`app/auth/jwt_handler.py`) with refresh-token rotation
(`app/auth/refresh_tokens.py`); passwords hashed with bcrypt via `passlib` (`app/core/security.py`) —
never stored or exposed in plaintext, including to admins (see [`rules.md`](rules.md) §Security Model).

Full permission catalogue and role grants: `app/core/seed.py::PERMISSIONS`/`ROLE_PERMISSIONS` — the
single source of truth, reproduced in [`rules.md`](rules.md) §Permission Matrix.

Ownership scoping beyond bare permission checks (a `view_own`-scoped caller only sees records they're
actually a party to) lives in `app/services/referral_scope.py`, `record_scope.py`,
`document_access.py`, `appointment_dedup.py` — see [`rules.md`](rules.md) §Security Model for the
history of gaps found and closed here.

## 9. Architecture Decision Records (ADR-001 – ADR-006)

Full rationale/trade-offs for each are in `CAPSTONE_IMPLEMENTATION_GUIDE.md` §3.3; summarized inline
above (§1 = ADR-001, §2 = ADR-002, §8 = ADR-006, §4 = ADR-004). Two more:

- **ADR-003 (communication styles)**: REST for synchronous client/module CRUD; the outbox (§7) for
  "X happened, who cares reacts"; MCP specifically for **agent-to-system** integration (mocked
  external systems, and the platform's own API as tools for the assistant) — three mechanisms because
  each is the right size for its job, not one style forced everywhere.
- **ADR-005 (LLM swappability + offline fallback)**: `app/agents/llm.py::get_chat_model(task)` is the
  *only* place that constructs an LLM client. No API key configured (or `llm_enabled=False`) →
  `StubChatModel`, which runs small deterministic rules (regex code extraction, weighted rule-based
  specialist ranking, template summaries) instead of failing. This is why the platform is fully
  functional under `docker compose up` with zero secrets, why every agent node is unit-testable
  without a network call, and why it's explicit *which* parts of the pipeline are "real AI" vs.
  "rule-based safety net."

## 10. Tech Stack

| Layer | Choice | Why (where non-obvious) |
|---|---|---|
| API framework | FastAPI (async) | |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | Deterministic constraint naming convention (`app/database/base.py`) so autogenerated migrations are stable/greppable |
| DB | Postgres 16 (prod/dev), SQLite in-memory (tests only, via `aiosqlite` + `httpx.ASGITransport`) | Dual-tested every phase — SQLite alone missed real bugs (naive/aware datetime handling, a `Date`-vs-`DateTime` column mistype) that only Postgres surfaced |
| Agent orchestration | LangGraph (`StateGraph`, `create_react_agent`) | |
| LLM | Groq (OpenAI-compatible endpoint), `langchain-openai` client | Free tier, no embeddings endpoint — hence BM25 for retrieval, not embeddings |
| Agent↔system integration | MCP (`fastapi_mcp` for REST-as-tools; raw `mcp` SDK `FastMCP` for the KB server) | |
| Tracing | LangSmith (optional, env-var-only, zero code coupling) | |
| Auth | `python-jose` (JWT), `passlib[bcrypt]` | |
| Rate limiting | `slowapi` | Login endpoint specifically, 5/minute per IP |
| Document parsing | `pypdf` | Referral/medical-record document extraction |
| Retrieval | `rank-bm25` (BM25Plus) | |
| Frontend | Plain HTML/CSS/vanilla JS ES modules, no build step | Matches the rest of this Python-only repo; served same-origin at `/app` via `StaticFiles`, hash-based client routing |
| Containerization | Docker + `docker-compose.yml` (api + postgres services) | |

## 11. Deployment Topology

```
docker compose up --build
 ├─ postgres (postgres:16, healthchecked)
 └─ api (this repo's Dockerfile)
     ├─ FastAPI app (uvicorn) — port 8000
     │   ├─ /            core app info
     │   ├─ /auth, /patients, /doctors, /appointments, /medical-records,
     │   │   /referral, /schedule, /analytics, /admin, /notifications,
     │   │   /audit, /health         (REST, JWT-protected per-route)
     │   ├─ /referral-workflow, /assistant   (AI routes)
     │   ├─ /mcp                     (fastapi_mcp — every REST route as a tool)
     │   ├─ /mock/{ehr,payer,directory,scheduling,notification}  (mocked externals, own MCP each)
     │   ├─ /kb                      (first-party knowledge-base MCP server)
     │   ├─ /app                     (static dashboard)
     │   └─ /docs                    (Swagger UI)
     └─ Alembic migrations + role/permission seed run at container start (`scripts/start.sh`)
```
One process, one container image (the mocks and the KB server are mounted sub-apps, not separate
containers — consistent with ADR-001's "design microservices, ship a monolith" call). See
`RUNBOOK.md` for exact bootstrap commands and `Dockerfile`/`docker-compose.yml` for the container
definition.

## 12. Real Deviations From the Original Plan (worth knowing before touching related code)

These are places the shipped system differs from `CAPSTONE_IMPLEMENTATION_GUIDE.md`'s original phase
plan, found and fixed during the build rather than planned upfront:

1. **Unscoped-routes security gap** (found during Phase 9): `patients.py`/`doctors.py`/
   `appointments.py`/`medical_records.py` originally had *zero* ownership scoping — any authenticated
   user could fetch/edit any record by ID. Fixed with `app/services/record_scope.py` + new
   `*:view_own`/`*:view_all`/`:manage` permissions. Full history in
   [`rules.md`](rules.md) §Security Model.
2. **Phase 9 assistant went beyond the guide's sketch**: role-specific tool allowlists/prompts (the
   guide didn't spec per-role prompts), plus an entirely new consult-outcome + completion-summary
   feature not in the original plan at all (added because there was genuinely no way to close out a
   referral otherwise).
3. **The referral workflow's specialist recommendation is architecturally bound to the external mock
   directory's synthetic ID space** (§3.4) — direct appointment booking needed its own real-doctor
   recommendation path, which was built as a **shared service** (`doctor_recommendation.py`) rather
   than a parallel implementation, then a genuine new LangGraph routing branch
   (`specialist_preselected`) was added so a referral with a pre-chosen real doctor could skip the
   external-directory step too, once that became a requirement.
4. **Direct-booked appointments (no referral at all) originally had no outcome/summary path** — the
   referral-only `ReferralOutcome` model was generalized (nullable `referral_request_id`, new nullable
   `appointment_id`, exactly one set) rather than building a second parallel outcome model.
5. **Medical-record create/update/delete had zero ownership scoping** even after the read-side fix in
   #1 — found while widening `medical_record:manage` to patient/care_coordinator roles; fixed before
   the widening shipped, not after.
6. **Two real library bugs were found and fixed at the source, not worked around downstream**: (a)
   `fastapi_mcp` injects a spurious top-level `"type"` onto any `Optional[X]` query-param schema
   lacking one, producing a self-contradictory schema an LLM's `null` gets rejected against — every
   MCP-exposed route was audited and fixed, with a permanent regression test
   (`test_no_assistant_tool_param_has_a_nullable_schema`). (b) `FastMCP.streamable_http_app()`'s
   lifespan doesn't propagate through `app.mount()` — worked around by composing
   `session_manager.run()` directly into the root app's own lifespan.

See [`rules.md`](rules.md) for the working conventions these deviations were handled under, and
[`design.md`](design.md) for how they surface in the actual UI/role experience.
