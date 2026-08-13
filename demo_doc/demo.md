# ADR Demo Guide — Where Every Architecture Decision Actually Lives

For each of the 6 ADRs recorded in `CAPSTONE_IMPLEMENTATION_GUIDE.md` §3.3: the one-line decision,
every concrete file path where it's implemented (not just designed), and a quick way to *show* it
live rather than just point at code. Verified directly against the running codebase, 2026-08-13.

---

## ADR-001 — Modular monolith; mocked externals as separately-packaged sub-apps

**Decision**: one FastAPI process, bounded contexts as router/model/service packages with no
cross-imports of internals; genuinely external organizations (payer, EHR, provider directory,
scheduling, notifications) are structurally isolated sub-applications, each with its own MCP mount —
the seam a real extraction would follow.

**Where implemented**
- `app/main.py:122-134` — five separate `app.mount("/mock/{system}", ...)` calls, each mounting a
  fully independent FastAPI app object.
- `mock_systems/ehr_mock/main.py`, `payer_mock/main.py`, `provider_directory_mock/main.py`,
  `scheduling_mock/main.py`, `notification_mock/main.py` — each its own `FastAPI()` instance, own
  in-memory dataset, own `FastApiMCP(...).mount_http()`.
- `app/services/` (13 files: `doctor_recommendation.py`, `eligibility.py`, `scheduling.py`,
  `patient_context.py`, `record_scope.py`, `referral_scope.py`, `document_access.py`,
  `referral_outcome.py`, `appointment_dedup.py`, `insurance.py`, `audit.py`, `storage.py`,
  `notifications.py`) — the interface layer bounded contexts talk through instead of reaching into
  each other's route/model modules directly.

**Demo it**: `docker compose up --build` starts the *entire* platform (core API + all 5 mocks + the
KB server) as one container from one image — show `docker compose ps` (one `api` service) next to
`curl localhost:8000/mock/payer/mcp` and `curl localhost:8000/mcp` both answering, proving they're
structurally separate MCP servers co-hosted in one process, not one flat API.

---

## ADR-002 — Orchestration for the clinical workflow; choreography for status fan-out

**Decision**: the referral workflow itself is orchestrated by one LangGraph `StateGraph` that owns
sequencing/retries/HITL pauses. Status visibility, notifications, and analytics are choreographed —
independent consumers react to outbox events without the workflow knowing they exist.

**Where implemented — orchestration**
- `app/agents/graph.py:45-88` (`build_graph`) — 10 nodes, one `StateGraph` owning the full sequence.
- `app/agents/graph.py:40-42` (`await_specialist_approval`) and
  `app/agents/nodes/eligibility.py` (`escalate_eligibility_node`) — the two real `interrupt()` pause
  points.

**Where implemented — choreography**
- `app/models/outbox.py` — the `outbox_events` table itself.
- `app/events/outbox.py::write_outbox_event` — called alongside a state change, same transaction,
  never a separate commit (the transactional-outbox guarantee).
- `app/events/publisher.py::publish_loop` — background poller (`app/main.py`'s lifespan), 0.5s
  interval, marks rows published and hands them to the broadcaster.
- `app/events/broadcaster.py` — in-process pub/sub fan-out (documented as the localized stand-in for
  Redis pub/sub the ADR names — swapping this one module is the only change needed to scale past one
  process).
- **Three independent consumers, none aware of each other or of the workflow**:
  1. Live status SSE: `app/api/routes/referral.py:601-617` (`GET /requests/{id}/events`) →
     `broadcaster.stream()`.
  2. Referral timeline: `app/api/routes/referral.py:481-` (`GET /requests/{id}/timeline`) — reads the
     same outbox rows back, never deleted.
  3. Analytics `avg_time_to_schedule_hours`: `app/api/routes/analytics.py:48-61` — pairs
     `referral.submitted`/`referral.appointment.scheduled` outbox timestamps, deliberately not
     `ReferralRequest.updated_at` (which only holds the latest transition).
  4. (Non-outbox choreography, same spirit) In-app notification bell: `app/agents/nodes/notify.py:38-41`
     (`create_notification`) — written independently of, and in addition to, the mocked external
     `send_notification` MCP call in the same node (line 31-34).

**Demo it**: open a referral's detail page and watch the live SSE status banner update with zero page
refresh while the workflow runs in the background; then open its Timeline tab and show the exact same
milestones the SSE stream pushed, now durably queryable after the fact.

---

## ADR-003 — REST for CRUD, outbox for cross-module side effects, MCP for agent↔system integration

**Decision**: three distinct communication styles, each the right size for its job, not one style
forced everywhere.

**Where implemented**
- **REST**: every file in `app/api/routes/` (14 routers) — standard verb-per-resource CRUD.
- **Outbox**: see ADR-002 above (`app/models/outbox.py`, `app/events/`).
- **MCP**:
  - `app/main.py:146-147` — `FastApiMCP(app).mount_http()`, the platform's own API exposed as tools at
    `/mcp`.
  - `mock_systems/*/main.py` — each mock similarly wrapped.
  - `knowledge_base/main.py` — the one server built on raw `mcp` SDK `FastMCP` (not `fastapi_mcp`),
    exposing a tool, resources (`kb://policies`, `kb://policies/{doc_id}`), and prompts
    (`explain_referral_process`, `compare_policies`).
  - `app/agents/mcp_clients.py` — LangGraph nodes reaching the mocked externals over MCP.
  - `app/agents/assistant_graph.py:239-248` — the conversational assistant reaching the platform's own
    API and the KB server over MCP, as its *only* way to read data (never a direct DB query).

**Demo it**: `GET /docs` (REST/Swagger) side-by-side with an MCP client (or the assistant chat itself)
calling `list_referrals` — same underlying route, two different protocols reaching it depending on who
the caller is (a human via a browser, an LLM via a tool call).

---

## ADR-004 — Stateless API layer; agent/workflow state externalized to Postgres

**Decision**: FastAPI holds no session/workflow state in process memory. LangGraph's checkpointer
persists every graph's state in Postgres, keyed by `thread_id`, so a human-in-the-loop pause survives
a restart and can be resumed by any replica.

**Where implemented**
- `app/agents/checkpointer.py::open_checkpointer` — `AsyncPostgresSaver`, opened once for the app's
  lifetime in `app/main.py`'s lifespan, `.setup()` idempotently creates LangGraph's own checkpoint
  tables (deliberately outside Alembic's migration history — LangGraph owns that schema).
- **Workflow graph thread IDs** — `app/api/routes/ai/referral_workflow.py:39,64,141` —
  `thread_id = f"referral-{referral_id}"`, identical across the initial kickoff and both resume
  endpoints (ordinary resume + eligibility-override resume), so all three touch the same persisted
  run.
- **Assistant graph thread IDs** — `app/api/routes/ai/assistant.py:61` —
  `thread_id = f"chat-{current_user.id}-{body.session_id}"` — per-user, per-session conversation
  memory, also Postgres-backed via the same checkpointer (`app/agents/graph.py::get_checkpointer`,
  reused rather than opening a second connection).
- JWT bearer auth itself (`app/auth/jwt_handler.py`) — no server-side session store at all, consistent
  with the same "no in-process state" posture at the HTTP layer.

**Demo it**: submit a referral to the point of `awaiting_specialist_approval`, restart the `api`
container (`docker compose restart api`), then resume it — the pause survived the process going away
entirely, proof the state was never in memory.

---

## ADR-005 — LLM provider behind a swappable factory, with a deterministic offline fallback

**Decision**: one seam constructs every LLM client; missing/blank API key (or `llm_enabled=False`)
returns a `StubChatModel` running deterministic rules instead of failing — the platform must run under
`docker compose up` with zero configured secrets.

**Where implemented**
- `app/agents/llm.py:19` — `get_chat_model(task)`, the single construction seam.
- **Every real call site** (6 total, confirmed by direct grep — no other construction path exists):
  - `app/agents/nodes/intake.py:171` — `code_extraction` (diagnosis/procedure code extraction)
  - `app/services/doctor_recommendation.py:131` — `specialist_ranking`
  - `app/services/referral_outcome.py:94` — `completion_summary`
  - `app/agents/nodes/summarizer.py:72` — `summarization`
  - `app/agents/assistant_graph.py:234` — `assistant` (chat)
  - (deterministic mirror, not an LLM call) `app/api/routes/ai/assistant.py:22-40` — `faq_fallback`,
    the "ADR-005 in miniature" canned responder the chat route falls back to when
    `build_assistant_graph` returns `None`.
- `app/core/config.py:30` — `llm_enabled: bool = False` (default off), `.env.example` documents this
  explicitly: "Leave LLMGW_API_KEY blank to run the whole platform on deterministic rule-based
  fallbacks — no key required for the app to run end-to-end."

**Demo it**: blank out `LLMGW_API_KEY` in `.env`, restart, and run the exact same referral flow —
specialist ranking still produces reasoned candidates (rule-based scoring text instead of an
LLM-authored explanation), document extraction still runs (regex instead of LLM), and the chat
assistant still answers the 4 FAQ categories — nothing 500s. Then re-enable the key and repeat, and
point out the LLM-authored ranking reasons read noticeably differently (natural language vs. the
rule-based `explain()` template) — proof the real model path, not just the fallback, is what ran.

---

## ADR-006 — RBAC via DB-modeled Role/Permission tables, not a hardcoded role string

**Decision**: `require_permission("resource:action")`, checked against real `user_roles`/
`role_permissions` join tables, gates every sensitive route — never `if user.role == "..."`.

**Where implemented**
- `app/core/seed.py` — `PERMISSIONS` (18 permissions) / `ROLE_PERMISSIONS` (7 roles) — the single
  source of truth, idempotently seeded by `seed_roles_and_permissions`.
- `app/api/dependencies/auth.py:45-68` — `require_permission(permission)`, the dependency factory:
  loads the caller's roles→permissions from the DB on every request, 403s unless the named permission
  or the universal `admin:*` bypass is present.
- **31 call sites across 10 route files** (confirmed by grep), e.g.:
  `app/api/routes/admin.py` (7), `app/api/routes/medical_records.py` (5),
  `app/api/routes/patients.py` (3), `app/api/routes/doctors.py` (3),
  `app/api/routes/appointments.py` (3), `app/api/routes/referral.py` (3),
  `app/api/routes/ai/referral_workflow.py` (3), `app/api/routes/schedule.py` (2),
  `app/api/routes/audit.py` (1), `app/api/routes/analytics.py` (1).
- **Ownership scoping layered on top** (not a substitute for the permission check):
  `app/services/referral_scope.py`, `record_scope.py`, `document_access.py` — a `view_own`-scoped
  caller only sees records they're actually a party to, checked *in addition to* holding the
  permission.
- `GET /auth/me` (`app/api/routes/auth.py:168`) exposes the same computed permission set to the
  frontend, so the dashboard's own gating (`static/js/state.js::hasPermission`) reads from the real
  DB-derived source, not a duplicated client-side role table.

**Demo it**: in the Admin panel, revoke a user's `analytics:view` permission-granting role live, have
them refresh `/app` — the Analytics nav item and the `GET /analytics/referrals/summary` route both
disappear/403 immediately, no redeploy, because the check is a live DB join, not a compiled-in role
list.

---

## Quick reference table

| ADR | Core file | Live-demo action |
|---|---|---|
| 001 | `app/main.py:122-134` | `docker compose ps` — one container, 7 MCP servers |
| 002 | `app/agents/graph.py` + `app/events/` | Watch SSE update live, then match it in the Timeline tab |
| 003 | `app/main.py:146-147`, `app/agents/mcp_clients.py` | `/docs` vs. an MCP tool call hitting the same route |
| 004 | `app/agents/checkpointer.py` | Restart the container mid-approval, resume successfully |
| 005 | `app/agents/llm.py:19` | Blank the API key, run the same flow, nothing breaks |
| 006 | `app/core/seed.py`, `app/api/dependencies/auth.py:45-68` | Revoke a permission live, watch access disappear |

See [`../.claude/architeture.md`](../.claude/architeture.md) §9 for the full ADR rationale/trade-offs,
and [`../.claude/rules.md`](../.claude/rules.md) for the security-incident history behind ADR-006's
enforcement (unscoped routes, the ownership-scoping gaps found and closed).
