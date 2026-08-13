# Project Requirements — Intelligent Care Coordination & Referral Management Platform

Source problem statement: `problem_statemnet.txt` (repo root), the Architect Academy capstone brief.
Phased build plan: `CAPSTONE_IMPLEMENTATION_GUIDE.md`. This document restates what was actually
*required*, then cross-references what shipped — see [`architeture.md`](architeture.md) for how, and
[`rules.md`](rules.md) for the constraints that shaped the how.

## 1. Problem Statement

A healthcare organization needs to coordinate patient referrals across primary care providers (PCPs),
specialists, payers, and care coordinators. Today this is manual, slow, and opaque — referrals get
lost, eligibility isn't checked until late, specialist availability is unknown, and no one has an
end-to-end view of where a referral stands. The platform must:

1. Give each stakeholder role (patient, PCP, specialist, care coordinator, payer, admin) an
   appropriate, permissioned view of referral and patient data.
2. Automate the referral lifecycle — intake, eligibility verification, specialist matching,
   scheduling, notification — using an **agentic** (LLM + orchestration) approach, with a
   deterministic fallback so the system never depends on an LLM being available.
3. Integrate with external systems (EHR, payer/eligibility, provider directory, scheduling,
   notifications) that don't exist in real life for this capstone, so they must be **mocked** as
   separate services with realistic contracts.
4. Demonstrate real software-architecture practice: documented ADRs, a bounded-context domain model,
   a security/RBAC posture, auditability, and a deployment story (`docker compose up`).
5. Be gradable — runnable end-to-end with zero manually-provisioned secrets (an LLM API key is
   optional, not required).

## 2. Functional Requirements

### 2.1 Identity & Access
- Users register with a username/email/password; JWT bearer auth with refresh-token rotation.
- Role-Based Access Control via **DB-modeled** `Role`/`Permission` tables (not hardcoded role
  strings) — `require_permission("x:y")` gates every sensitive route.
- Seven roles: `patient`, `pcp`, `specialist`, `care_coordinator`, `payer_admin`, `doctor`, `admin`.
  See [`rules.md`](rules.md) §Permission Matrix for the exact grant table.
- A `User` account is a login; a `Patient`/`Doctor` row is a clinical identity. They're linked by an
  optional FK, not merged — an admin must explicitly **link** a user to a patient/doctor record after
  granting a role, or that user's "my own data" queries return empty. This three-identity model is
  documented in full in `WORKFLOW.md`.

### 2.2 Referral Lifecycle (the core domain workflow)
A referral must move through: **submitted → intake processing → (awaiting documents, if needed) →
eligibility checking → (eligibility denied → coordinator override, if needed) → specialist
recommendation → awaiting specialist approval (human-in-the-loop) → scheduling → scheduled →
completed**, with `cancelled` reachable from most states. Required capabilities at each step:

- **Intake**: extract structured fields (diagnosis/procedure codes) from uploaded referral documents;
  detect and gate on missing required documents.
- **Eligibility**: verify the patient's insurance against a mocked payer system; a denial must reach
  a human care coordinator for review/override, not dead-end silently.
- **Specialist recommendation**: rank candidate specialists by specialty match, network status,
  distance, and available capacity, with **explainable reasons** attached to the ranking, not just a
  bare score.
- **Human-in-the-loop approval**: the final specialist selection pauses for a real human decision by
  default — auto-approval, if ever added, must be an explicit configurable policy, not a silent
  shortcut.
- **Scheduling**: book a real appointment slot once a specialist is confirmed.
- **Notification**: notify the relevant parties (patient at minimum) once scheduled.
- **Consult outcome**: once the appointment happens, the treating doctor must be able to record
  symptoms/diagnosis/prescription/follow-up and have the system generate a care-journey summary for
  the next provider — this was a gap discovered organically during the build (see
  [`architeture.md`](architeture.md) §Deviations) and is now a first-class requirement.

### 2.3 Direct (non-referral) Care
Not every patient interaction starts with a referral. A patient must also be able to browse/search
real platform doctors and book an appointment directly, with the same specialist-recommendation
quality (specialty/network/distance ranking) available to that path — this requirement emerged after
initial delivery (see [`architeture.md`](architeture.md)) and was satisfied by **generalizing**, not
duplicating, the referral workflow's own recommendation and outcome logic.

### 2.4 Core CRUD Domains
Patients, Doctors, Appointments, Medical Records — full CRUD, each visible only to parties who have a
legitimate reason to see it (self, treating provider, or a coordinator/admin role), never open to
every authenticated user. Medical records must support versioned vitals/diagnosis fields and file
attachments, with authenticated download (not just a filename).

### 2.5 Conversational Assistant
A chat interface, scoped per-role, that can answer questions about a caller's own referrals,
appointments, medical records, and (for staff roles) any patient they have a legitimate reason to
look up — using the platform's own real, authorization-checked API as tools (not a shadow read path).
Must degrade gracefully (never a raw 500) if the LLM or a tool is unavailable.

### 2.6 Knowledge Base / Policy Q&A
General, non-PHI reference material (how referrals work, prior authorization rules, privacy notice,
insurance plan details) must be searchable by the assistant, consistent with the platform's actual
mocked payer data — not a source of contradictory answers.

### 2.7 Analytics
Care-coordination staff and payer admins need a referral analytics summary: status breakdown, average
time-to-schedule, delay-risk count, eligibility-denial rate, top specialties requested.

### 2.8 Audit
Every mutating API call and every agent tool call must be recorded in an audit log with actor, action,
resource, and timestamp.

## 3. Non-Functional Requirements

| NFR | Requirement |
|---|---|
| Availability | Stateless API layer — no in-process session/workflow state, so any replica can serve any request |
| Latency | Sync CRUD reads/writes should feel instant; AI-assisted steps (document extraction, ranking) must run **async** — never block an HTTP request on an LLM call |
| Security | JWT bearer + refresh rotation; least-privilege RBAC; MCP tool access scoped per role; no plaintext secrets anywhere (including passwords — see [`rules.md`](rules.md)) |
| Privacy | Minimum-necessary data exposure; a role's assistant tool surface must never exceed what that role's own permissions already allow through the UI |
| Auditability | Every mutation and every agent tool call logged |
| Explainability | Every AI recommendation stored/shown with the reasons that produced it, not just the output |
| Human oversight | Critical decisions (specialist selection, eligibility denial, booking) default to a human-in-the-loop pause |
| Resilience | LLM outage or missing API key must degrade to deterministic rule-based logic — never crash the request |
| Observability | Structured JSON logs; correlation IDs threaded end-to-end |
| Testability | Every LLM call goes through one seam so tests can stub it — no live network calls in the test suite |
| Extensibility | A new specialty, payer, or mock system should be a config/data change, not a core-code change |
| Deployability | Must run end-to-end via a single `docker compose up --build`, with zero required API keys |

## 4. Explicit Scope Boundaries

Excluded on record, at the user's explicit direction during a mid-build feature review (see
`WORKFLOW.md` / de-siloing pass memory), and never revisited:

- Elasticsearch-based search (server-side `q` filters on a couple of list endpoints were built
  instead, deemed sufficient for this scope).
- SMS delivery (the notification system stays a mock + in-app notification center).
- A second, separate workflow/orchestration engine.
- Predictive/forecasting analytics (only descriptive analytics — summary counts/averages — is in
  scope).
- A full Care-Team RBAC redesign (the existing role/permission model, with `view_own`/`view_all`
  splits, was judged sufficient).
- Full write-side ownership checks everywhere (e.g. a PCP editing a patient they never treated) —
  deliberately deferred hardening, tracked as a known gap, not silently dropped. See
  [`rules.md`](rules.md) §Known Gaps.

## 5. Acceptance Signal Actually Used

No formal sign-off gate existed beyond the working agreement established during the build (see
[`rules.md`](rules.md) §Development Workflow): each phase/feature pass was considered "done" only when
**both** the full `pytest` suite passed against in-memory SQLite **and** a live smoke test passed
against a real local Postgres instance (and, for several passes, a real Groq-hosted LLM and a real
Playwright browser session). Grading itself is `docker compose up --build` + the manual sanity
checklist in `RUNBOOK.md`.

## 6. Requirement → Delivery Cross-Reference

| Requirement area | Delivered in | Status as of 2026-08-12 |
|---|---|---|
| Identity/RBAC | `app/core/seed.py`, `app/api/dependencies/auth.py`, Alembic `user_roles`/`role_permissions` | Done |
| Referral lifecycle orchestration | `app/agents/graph.py` (LangGraph `StateGraph`, 10 nodes) | Done, including denied-eligibility override path |
| Direct appointment booking w/ recommendation | `app/services/doctor_recommendation.py`, `GET /doctors/recommend`, unified "New Request" flow | Done |
| Consult outcome + summary | `ReferralOutcome` model, referral- and appointment-scoped outcome routes | Done, generalized beyond the original referral-only scope |
| Core CRUD + ownership scoping | `app/api/routes/{patients,doctors,appointments,medical_records}.py` + `app/services/record_scope.py` | Done — an initial zero-scoping gap was found and fixed (see [`architeture.md`](architeture.md) §Deviations) |
| Conversational assistant | `app/agents/assistant_graph.py`, `/assistant/chat` | Done, role-scoped tool allowlists + prompts |
| Knowledge base / policy Q&A | `nb/`, `knowledge_base/` (first-party MCP server, BM25Plus retrieval) | Done |
| Analytics | `app/api/routes/analytics.py` | Done |
| Audit log | `app/models/audit.py`, `app/services/audit.py` | Done |
| Docker deployment | `Dockerfile`, `docker-compose.yml` | Done |
| Testing evidence | `tests/` — 158 test functions across 25 files | 150 passing / 1 pre-existing failing (see [`rules.md`](rules.md) §Known Gaps) as of last full run |

See [`architeture.md`](architeture.md) for the system design that satisfies these requirements, and
[`design.md`](design.md) for how each stakeholder role actually experiences the platform end to end.
