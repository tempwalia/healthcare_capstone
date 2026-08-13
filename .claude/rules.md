# Rules & Conventions — Intelligent Care Coordination & Referral Management Platform

The working rules this project has actually been built and maintained under — process conventions,
security posture, and hard-won gotchas. Pair with [`architeture.md`](architeture.md) (system design)
and [`project_requirement.md`](project_requirement.md) (what's required). Where a rule exists because
of a real incident during the build, the incident is named so future changes can judge edge cases
instead of blindly following the letter of the rule.

## 1. Development Workflow

**Dual verification, every phase/feature pass — not optional, not "SQLite is good enough."**
Build the change, then (1) run `uv run pytest` (in-memory SQLite, `httpx.ASGITransport`) **and**
(2) manually smoke-test against a real local Postgres instance (register/login/curl the new
endpoints, or a small inline script for direct DB checks) before calling it done.

*Why*: this exact workflow caught multiple real Postgres-only bugs that SQLite-only testing missed
entirely — a naive/aware `datetime` comparison bug (SQLite round-trips `DateTime(timezone=True)` as
naive; Postgres doesn't), a `request_date` column mistyped as `DateTime` instead of `Date` (only
shifted under Postgres's timezone handling), and a background-task DB session that silently bypassed
test isolation. None of these showed up in a green `pytest` run alone. Treat a passing SQLite suite as
**necessary, not sufficient**.

When a change touches the LangGraph workflow, the assistant, or anything LLM-adjacent, also
smoke-test against the real configured LLM (Groq) at least once — several real bugs (a `fastapi_mcp`
schema bug, a role-prompt over-restriction on knowledge-base questions) only reproduced against a real
model's actual behavior, not the deterministic test stub.

For frontend (`static/`) changes: `node --check` syntax validation + a real browser pass is the bar,
not just an API-level smoke test — several sessions on this project shipped frontend changes verified
only via HTTP calls because no browser tool was available at the time, and that gap is explicitly
flagged in project memory as the first place to suspect if a UI bug is reported. If Playwright (or
equivalent) is available, use it — this project has real precedent for full end-to-end browser
verification (login → multi-step flow → confirm final state) once tooling allowed it.

**Git discipline**: commits are made **only** when the user explicitly asks. Substantial work has
accumulated uncommitted across many sessions on this project and that is expected, not an oversight to
fix proactively. Never `git add -A`/`git add .` — stage specific files. Never amend, force-push, or
rebase without explicit instruction.

**Permission-table changes require a re-seed.** Editing `app/core/seed.py`'s `ROLE_PERMISSIONS` does
**not** touch already-seeded DB rows — `uv run python scripts/seed_roles.py` must be re-run against
the real DB (`seed_roles_and_permissions` is idempotent, safe to run repeatedly) or the change is
invisible until then. This has been forgotten at least once mid-session; check `/auth/me`'s returned
permission set if a just-added permission "isn't working."

**Windows event loop**: LangGraph's Postgres checkpointer driver needs the selector event loop, not
Windows' default proactor loop — always run local uvicorn with
`--loop app.core.event_loop:selector_event_loop_factory`.

**Hardcoded mock/self-referential base URLs**: `mock_base_url`/`api_base_url`/`kb_base_url` all
default to `http://127.0.0.1:8000` **regardless of what port the server you're testing is actually
running on**. If you deliberately run a server on a non-default port (e.g. to avoid disturbing another
instance), override all three env vars to match, or every self-referential MCP call (eligibility
checks, specialist recommendation, KB search, the assistant's own tool calls) will silently 404 or
hang with no obvious link to the real cause. This has caused more than one real debugging detour.

**Background tasks can race a real socket.** Under pytest's `ASGITransport`, `BackgroundTasks` run to
completion before the response returns. Under a **real** uvicorn process over a real socket, they do
not reliably do so — firing two dependent requests (e.g. sequential document uploads) back-to-back
with no delay can race. Add a short delay between such calls when smoke-testing against a live server;
this is not an issue inside the pytest suite itself.

## 2. Security Posture (non-negotiable)

**Never implement a literal request that's a real security anti-pattern — build the secure version of
the actual underlying need instead, and say plainly what changed and why.** Established precedent: a
direct request to add a plaintext-visible password column "so admin can see and update passwords" was
**not** implemented as asked. Built instead: `POST /admin/users/{id}/reset-password` (sets a new
bcrypt-hashed password, revokes the target's existing refresh tokens) — because hashing is one-way, so
"viewing" an original password is cryptographically impossible regardless of admin privilege, not a
policy choice being declined. Applies to any future ask that would mean plaintext secrets, disabled
auth checks, logging credentials, or similar. Silence or a flat refusal without an alternative is
worse than explaining the trade-off and shipping the secure equivalent.

**RBAC is DB-modeled, never a hardcoded role string.** Every protected route uses
`require_permission("resource:action")` (`app/api/dependencies/auth.py`), checked against
`user_roles`/`role_permissions`, with `admin:*` as the universal bypass. Do not add
`if user.role == "..."` checks anywhere.

**Ownership scoping is a second, separate layer on top of permission checks — both are required.**
Holding `appointment:view_own` doesn't mean "can see appointment 7"; it means "can see appointments
they're actually a party to." This distinction was the source of a real, shipped vulnerability
(patients.py/doctors.py/appointments.py/medical_records.py originally had zero ownership scoping —
any authenticated user could fetch/edit any record by ID) and has recurred since: granting `specialist`
the `referral:record_outcome` permission almost shipped with a raw unscoped lookup that would have let
any specialist record an outcome for *any* referral platform-wide, not just their own — caught before
shipping and fixed to use `_get_scoped_referral`. **Any new mutating route on patient/referral/
appointment/medical-record data must go through the existing scoping helper for that resource
(`app/services/referral_scope.py`, `record_scope.py`, `document_access.py`) — never a bare
`select(Model).where(id==...)`.**

**A patient's own data path must never take an ID parameter the model/caller can substitute.** The
assistant's `get_my_patient_context` tool (and `GET /patients/me`, `/patients/me/context`) resolve
strictly from the caller's own linked record — no `patient_id` argument exists for a self-service tool
to be tricked into passing. Staff-role equivalents (`get_patient_context(patient_id)`) exist
separately and are withheld from roles that shouldn't have them at the assistant's tool-allowlist
layer, not just the route's permission layer.

**The assistant's tool surface must never exceed a role's real, already-scoped permissions.** New MCP
tools are only added to `BASE_REFERRAL_TOOLS`/`STAFF_PATIENT_TOOLS`/role-specific sets in
`app/agents/assistant_graph.py` once the underlying route is confirmed to have real ownership scoping
— not just because the route exists and technically returns data. `get_patient`/`get_doctor`/
`get_appointment` by raw ID were deliberately withheld from every role for a long stretch specifically
because those routes weren't yet scoped; they were only added once the scoping gap was closed.

**Every MCP-exposed `Optional[X] = None` FastAPI query param is a live schema bug waiting to
happen.** `fastapi_mcp` (as installed) injects a spurious top-level `type` onto any query-param JSON
schema lacking one — which every `Optional[X]` param produces (`anyOf: [X, null]`, no top-level
`type`) — creating a self-contradictory schema that rejects an LLM's normal `null` ("omit this
filter"). This bit the project twice (once for `Optional[List[str]]`, once for a plain `Optional[str]`)
before the general rule was understood. **Rule**: any FastAPI query param on a route later exposed as
an MCP tool must have a concrete, type-matching default (`""`, `0`, `False`, `[]` via
`Query([])`), never `None`. `test_no_assistant_tool_param_has_a_nullable_schema`
(`tests/test_assistant.py`) is a permanent regression guard — if it starts failing, this is the cause.
Tri-state params (where "omit" must mean something different from any concrete value — e.g.
`list_slots`'s `is_booked`, which needs "don't filter" distinct from both `true` and `false`) need a
string param manually parsed, not a naive boolean default that silently collapses one of the three
states.

**`POST /assistant/chat` must never surface a raw exception to the user.** Wrap `graph.ainvoke()` in
try/except, log server-side with `logger.exception(...)`, return a friendly in-conversation fallback.
One bad tool call or malformed LLM response must not dead-end the whole chat as an unhandled 500.

## 3. Permission Matrix

Source of truth: `app/core/seed.py::PERMISSIONS` / `ROLE_PERMISSIONS`. Do not hand-derive this
elsewhere — re-read that file if this table and the code ever disagree.

| Permission | Meaning |
|---|---|
| `referral:create` | Submit a new referral |
| `referral:view_own` / `view_all` | View own vs. every referral |
| `referral:approve` | Approve a HITL workflow decision |
| `referral:override` | Override an automated (eligibility) decision |
| `referral:record_outcome` | Record a consult outcome |
| `patient:view_own` / `view_all` / `manage` | Patient record access/mutation |
| `doctor:manage` | Doctor directory mutation (GET stays open to all authenticated users — not PHI) |
| `appointment:view_own` / `view_all` / `manage` | Appointment access/mutation |
| `medical_record:view_own` / `view_all` / `manage` | Medical record access/mutation |
| `audit:view` | Read the audit log |
| `analytics:view` | Referral analytics summary |
| `admin:*` | Bypasses every other check |

| Role | Grants |
|---|---|
| `patient` | `referral:create/view_own`, `patient:view_own`, `appointment:view_own`, `medical_record:view_own/manage` |
| `pcp` | `referral:create/view_own`, `patient:view_all/manage`, `doctor:manage`, `appointment:view_own/manage`, `medical_record:view_own/manage` |
| `specialist` | `referral:view_own/approve/record_outcome`, `patient:view_all/manage`, `doctor:manage`, `appointment:view_own/manage`, `medical_record:view_own/manage` |
| `care_coordinator` | `referral:create/view_all/approve/override/record_outcome`, `analytics:view`, `patient:view_all/manage`, `doctor:manage`, `appointment:view_all/manage`, `medical_record:view_all/manage` |
| `payer_admin` | `referral:view_all`, `analytics:view` only — read-only, no clinical access |
| `doctor` | `referral:view_all/approve/record_outcome`, `patient:view_all`, `appointment:view_all`, `medical_record:view_all/manage` (POC stand-in for a specialist not tied to one referral) |
| `admin` | `admin:*` |

`view_all` is granted broadly to every clinical/coordination role for `patient:view_all` (no
patient-panel model exists — any provider may legitimately need to look up any patient), but
`appointment`/`medical_record` visibility stays `view_own` even for `pcp`/`specialist` — clinical
notes are treated as more sensitive than basic demographics. This asymmetry is deliberate, not an
inconsistency to "fix."

## 4. Code Conventions

- **Deterministic constraint naming** (`app/database/base.py`'s `naming_convention`) — every FK/UQ/PK
  gets a predictable, greppable name (`fk_appointments_patient_id_patients`), not an Alembic-hashed
  one. Keep using it; don't hand-name constraints inconsistently.
- **Soft deletes only** (`SoftDeleteMixin` → `deleted_at`). Queries against soft-deletable models must
  explicitly filter `deleted_at.is_(None)` — there is no ORM-level global filter doing this for you.
- **Cross-field invariants are app-level, not DB CHECK constraints** (e.g. exactly one of
  `referral_outcomes.referral_request_id`/`appointment_id` set) — documented convention, because
  SQLite's test backend doesn't enforce CHECK constraints the same way Postgres does, so a DB
  constraint would pass tests while a real Postgres violation could still occur, or vice versa.
  Validate in the route/service layer instead, and document the invariant on the model docstring.
- **FK cycles closed with `use_alter=True` + an explicit constraint name** (matching the naming
  convention above) on the model column — needed whenever a new FK would create a 3+ table cycle that
  `Base.metadata.drop_all()`/`create_all()` can't topologically sort (hit once with
  `referral_requests → schedule_slots → appointments → referral_requests`). Doesn't change what
  Alembic emits for Postgres (already separate `add_column`/`create_foreign_key` steps); only affects
  SQLAlchemy's own DDL ordering for the SQLite test fixture.
- **`Enum` columns are filtered by member, not `.value` string.** `Appointment.status` is
  `Enum(AppointmentStatusEnum)` bound by the Python enum member — filtering with the raw string
  (`.value`) silently matches zero rows. This has caused one real, previously-shipped bug
  (`appointment_dedup.py`).
- **One seam for every LLM call**: `app/agents/llm.py::get_chat_model(task)`. Never construct an LLM
  client anywhere else — this is what makes every agent node unit-testable without network access and
  keeps the "real AI vs. deterministic fallback" boundary in exactly one place.
- **Shared logic goes in `app/services/`, nodes/routes stay thin wrappers.** Established pattern:
  `doctor_recommendation.py`, `eligibility.py`, `scheduling.py`, `referral_outcome.py`,
  `patient_context.py`, `document_access.py`, `record_scope.py`, `referral_scope.py`. When the same
  business logic is needed from more than one entry point (a LangGraph node and a REST route; a
  referral flow and a direct-appointment flow), extract to a service and have both call it — do not
  duplicate the logic inline a second time. This has been done retroactively more than once
  (`infer_specialty`, eligibility checking, outcome/summary generation) specifically to avoid drift
  between two copies of the same rule.
- **Naive/aware datetime handling**: `app/core/time_utils.py::ensure_aware()` exists because this bug
  class (SQLite silently drops timezone-awareness on round-trip; Postgres doesn't) recurred. Use it
  rather than hand-rolling a fix again.
- **Frontend has no build step, deliberately.** Plain HTML/CSS/vanilla JS ES modules under `static/`,
  served same-origin at `/app`. Don't introduce a bundler/framework without an explicit ask — it's a
  conscious match to the rest of this Python-only repo, not an oversight.
- **Reuse frontend rendering, don't refork it.** E.g. the consultation/outcome UI
  (`static/js/components/consultation.js`) is shared verbatim between the referral detail page and the
  appointment detail page; `schedule.js` mounts the `appointments.js` resource table rather than
  reimplementing a table.

## 5. Known Gaps (deliberately deferred, not oversights)

- **Full write-side ownership checks** are not implemented everywhere — e.g. a `pcp` can technically
  edit a patient/medical-record they never actually treated, once they hold the base `:manage`
  permission. Read-side ownership scoping and *create-side* validation (the caller must be a party to
  what they're creating) are done; full *update*-side "were you ever actually involved with this
  specific record" checks are not, across the board. If extending write access to a new role, check
  whether this gap is now load-bearing for that change (it was for medical records — see
  [`architeture.md`](architeture.md) §12 item 5 — and was closed there specifically).
- **`insurance_plans`/`doctor_insurance_networks` tables are modeled but not the real eligibility
  check.** Eligibility verification is a string match against the mocked payer system
  (`mock_systems/payer_mock`), not these tables. Populating them would currently be cosmetic.
- **Referral `PATCH`/`DELETE` have no `:manage`-style server-side gate**, only visibility scoping — the
  dashboard hides the controls client-side for callers who shouldn't see them, but the backend would
  technically allow it. Don't assume the UI's gating is the security boundary.
- **A pre-existing, currently-failing test**: `tests/test_knowledge_base.py::
  test_list_documents_covers_every_policy_and_insurance_file` fails because `nb/dctest.txt` exists in
  the knowledge-base folder but isn't in the test's expected document-ID set (150/151 passing as of
  the last full run). Either remove/relocate `nb/dctest.txt` if it was scratch content, or update the
  test's expected set if it's meant to stay — don't treat the suite as "fully green" until this is
  resolved one way or the other.
- **Doctor recommendation "distance" is a same-city text match**, not real geocoding —
  `Patient.city`/`Doctor.city` are plain free-text labels compared case-insensitively; no lat/long
  math despite `Doctor.latitude`/`longitude` columns existing.
- Elasticsearch, SMS delivery, a second workflow engine, forecasting analytics, and a full Care-Team
  RBAC redesign are explicitly out of scope — see [`project_requirement.md`](project_requirement.md)
  §4. Don't reintroduce these without a fresh, explicit ask.

## 6. Demo/Test Data

Current seeded roster (`scripts/reset_demo_data.py`, password `teste@123` for everyone): 1 `admin`
account (`admin`), 2 `care_coordinator` (`beth.coleman`, `marcus.diaz`), 10 `specialist`/doctor
accounts (usernames are bare first names: `priya, daniel, maria, james, lena, omar, sarah, ahmed,
grace, marcus`), 10 `patient` accounts (`alex.morgan, priya.sharma, wei.chen, fatima.ahmed,
diego.martinez, sarah.johnson, kwame.mensah, elena.rossi, ravi.patel, nina.kowalski`). Full table:
`LOGIN_CREDENTIALS.md`. No referrals/appointments/medical records/availability exist in a fresh reset
— `scripts/seed_dummy_doctors.py` (idempotent) re-adds availability/slots and the 6
`ProviderDirectoryLink` mappings (Priya/Daniel/Maria/James/Lena/Omar match the mock provider
directory's synthetic candidates by name+specialty) if that flow needs to demo again.

`scripts/reset_demo_data.py` is interactive and destructive (asks for a typed "yes") — **never run it
without an explicit, deliberate request**, and never as part of an automated/startup path. Repeated
live smoke-testing sessions have left throwaway referrals/appointments/records on the real demo
Postgres dataset as a known, accepted side effect; this script is the documented way back to a
pristine roster if that ever matters again.

## 7. Documentation Map

- `CAPSTONE_IMPLEMENTATION_GUIDE.md` — source-of-truth phased build plan, ADR detail, Phase
  "Definition of Done" checklists. Check the relevant Phase section before implementing something new
  in that area.
- `REBUILD_GUIDE.md` — earlier, generic FastAPI/JWT/SQLAlchemy fundamentals reference; mostly
  historical now that `app/` implements it.
- `WORKFLOW.md` — the three-identity model, full role×permission matrix, every dashboard page's exact
  data fields/scope, per-role walkthroughs, the referral lifecycle as a step table. Written from
  reading the actual code, not the plan — but can itself drift, so verify against code if something
  seems inconsistent.
- `RUNBOOK.md` — exact commands to run (Docker and local), first-time bootstrap, sanity checklist.
- `README.md` — project overview/features.
- `CURRICULUM_VS_SHIPPED.md` — gap analysis against the 15-day training curriculum; a point-in-time
  snapshot (predates the knowledge-base/LangSmith work), not a live document — verify claims against
  current code before trusting it.
- `LOGIN_CREDENTIALS.md` — current demo account roster (contains a real shared password; treat as
  sensitive, don't paste elsewhere casually).
- Both `CAPSTONE_IMPLEMENTATION_GUIDE.md` and `REBUILD_GUIDE.md` are meant to be kept in sync with
  reality when implementation deviates from the plan — update them, don't let them silently rot, when
  a deviation like the ones in [`architeture.md`](architeture.md) §12 happens again.

See [`design.md`](design.md) for how these rules manifest in the actual per-role user experience.
