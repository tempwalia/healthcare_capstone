# Design — Intelligent Care Coordination & Referral Management Platform

Covers two things: the **API/schema design conventions** the backend follows, and the **dashboard's
UX/UI design system and per-role experience**. Pair with [`architeture.md`](architeture.md) (why the
system is shaped this way) and [`rules.md`](rules.md) (constraints on changing it). All details below
verified directly against `app/schemas/`, `app/api/routes/`, and `static/` as of 2026-08-13.

## 1. API Design Conventions

### 1.1 Resource routing
Standard REST verbs per resource (`app/api/routes/{patients,doctors,appointments,medical_records}.py`):
`POST /` create, `GET /` list, `GET /{id}` read, `PUT /{id}` update, `DELETE /{id}` delete (soft —
sets `deleted_at`, never a hard delete). Referral and scheduling sit under nested paths
(`/referral/requests/...`, `/schedule/availability/`, `/schedule/slots/...`) since they're each a
small resource family, not one flat CRUD resource.

### 1.2 Pagination
Every list endpoint returns the generic `Page[T]` envelope (`app/schemas/common.py`):
```json
{ "items": [...], "total": 143, "skip": 0, "limit": 20, "next": "?skip=20&limit=20" }
```
`skip`/`limit` query params, `next` pre-built as a ready-to-use query string (or `null` on the last
page). Applied uniformly — no endpoint returns a bare array.

### 1.3 Errors
FastAPI's default `{"detail": "..."}` shape (string) for simple errors, or FastAPI's standard
validation-error array shape (`[{"loc": [...], "msg": "...", ...}]`) for 422s — not a custom envelope.
The frontend's `api.js::toApiError` handles both, plus a fallback to `res.statusText` if the body
isn't JSON at all (e.g. a raw 500 from an upstream failure).

### 1.4 Auth
`POST /auth/login` (OAuth2 password-grant form body, not JSON — required by FastAPI's
`OAuth2PasswordBearer`/Swagger integration) returns `{access_token, refresh_token, token_type}`.
Every other route expects `Authorization: Bearer <access_token>`. `POST /auth/refresh` rotates both
tokens. The frontend auto-retries exactly once on a 401 by refreshing first
(`api.js::request`), except for `/auth/login`/`/auth/refresh` themselves (no infinite loop).
`GET /auth/me` returns `{id, username, email, roles, permissions}` — the same computed
union-of-role-permissions set `require_permission` checks internally, exposed so the frontend can
gate UI without re-deriving RBAC logic client-side.

### 1.5 Long-running / AI-assisted operations never block a request
`POST /referral/requests/` returns `202 Accepted` immediately — intake/eligibility/recommendation run
as background tasks against the LangGraph workflow, not inline in the request/response cycle. Clients
either poll `GET /referral-workflow/{id}/state` or subscribe to
`GET /referral/requests/{id}/events` (a hand-rolled `fetch` + `ReadableStream` SSE-style feed, not
`EventSource` — native `EventSource` can't carry a Bearer token, so this is a manual reconnecting
reader instead; see `static/js/api.js::streamReferralEvents`).

### 1.6 `operation_id` is the MCP tool name — set it deliberately
Every route exposed to the conversational assistant has an explicit `operation_id=` on its decorator
(`get_referral`, `list_referrals`, `list_slots`, `get_referral_analytics_summary`, ...) — this is
literally the tool name the LLM sees via `fastapi_mcp`. Don't rename one without updating
`ROLE_TOOL_ALLOWLIST` in `app/agents/assistant_graph.py` to match, or the tool silently disappears
from every role that had it.

### 1.7 File download pattern
Uploaded documents are served back via authenticated `FileResponse` endpoints
(`GET /referral/requests/{id}/documents/{doc_id}/download`,
`GET /medical-records/documents/{doc_id}/download`) — never a bare static file URL (would bypass
auth/scoping). The frontend can't use a plain `<a href>` for these (can't attach a JWT header), so
`api.js::downloadFile()` does an authenticated `fetch` → `Blob` → throwaway object-URL click instead.

## 2. Dashboard — Frontend Architecture

- **No build step.** Plain HTML/CSS/vanilla JS ES modules, served same-origin at `/app` via FastAPI's
  `StaticFiles(html=True)`. One `static/index.html` shell; everything else is JS-driven.
- **Hash-based client routing** (`static/js/router.js`) — `#/patients`, `#/referrals/12`. No
  server-side catch-all route is needed for deep links since the hash never reaches the server.
  `guarded(title, renderFn)` (`app.js`) is the single choke point every authenticated route passes
  through: redirects to `/login` if unauthenticated, sets the topbar title, and re-triggers a
  view-transition fade via a forced reflow (`view.offsetWidth`).
- **State**: `static/js/state.js` — a small pub/sub store (`getState`/`subscribe`/`setTokens`/`setMe`)
  holding `{accessToken, refreshToken, me}`. Every state-changing action (login, role change, token
  refresh) notifies subscribers, which is what keeps the sidebar/nav/notification bell in sync without
  a framework's reactivity system.
- **Component reuse over reimplementation** — established pattern, don't fork:
  - `static/js/resource.js::createResourceModule` is a generic factory behind the four CRUD-table
    modules (patients/doctors/appointments/medical-records) — table, create/edit modal, delete
    confirm, optional `serverSearchParam`, optional `onRowClick`, optional `extraToolbarButtons`.
  - `static/js/components/consultation.js::renderConsultationSection` is the one outcome-recording UI,
    used identically by the referral detail page's Outcome tab and the appointment detail page.
  - `static/js/modules/schedule.js` mounts `appointments.js`'s resource table at the bottom rather than
    reimplementing a table — "Scheduling & Appointments" is one nav item / one route, `/appointments`
    kept only as an alias so old links still resolve.
  - A shared `renderSlotList()` helper backs both direct-booking's slot picker and a referral's
    optional preferred-slot picker — same 30-minute-interval rendering, not two copies.
- **Notification bell** (`static/js/components/notifications.js`) is mounted once into a topbar
  sub-host (`topbarActionsHost`) that survives route changes — the topbar's title portion re-renders
  per route, the bell doesn't, avoiding a re-mount/re-poll cycle on every navigation. Polled every 30s
  on the same cadence as the health check, plus an immediate refresh piggybacked on the same
  `subscribe()` state-change notification login already fires (closes the "wait up to 30s after login"
  gap).
- **No client-side cache headers assumption**: `app/middlewares/no_cache_dashboard.py` sets
  `Cache-Control: no-store` on every `/app/*` response — `StaticFiles` sets no cache-control header by
  default, so a browser could otherwise keep serving yesterday's JS after a hand-edit during rapid
  iteration.

## 3. Design System

Tokens defined in `static/css/app.css` `:root` — change values there, not ad hoc per-component colors.

| Token group | Values | Use |
|---|---|---|
| Surface | `--surface #fcfcfb`, `--plane #f9f9f7` | Page/card backgrounds |
| Ink (text) | `--ink-1 #0b0b0b` (primary), `--ink-2 #52514e` (secondary), `--ink-3 #898781` (muted) | Text hierarchy |
| Line | `--gridline #e1e0d9`, `--baseline #c3c2b7` | Borders/dividers |
| Brand | `--primary #1a6fbf` / `--primary-dark #145a9c` / `--primary-tint #e8f1fb` | Primary actions, coordinator accent |
| Secondary | `--secondary #1baf7a` / `--secondary-tint #e5f8f1` | Provider accent |
| Sequential | `--sequential #2a78d6` | Patient accent, charts |
| Status | `--good #0ca30c`, `--warning #b9790a`, `--serious #c1502b`, `--critical #d03b3b` (+ each a `-tint` pastel) | Referral/eligibility status coloring |
| Radius | `--radius-sm 6px` / `-md 10px` / `-lg 14px` | |
| Shadow | `--shadow-sm/md/lg` | |
| Layout | `--sidebar-w 248px`, `--topbar-h 60px` | |

**Role accent system**: `document.body.dataset.roleAccent` (set by `app.js::applyRoleAccent`, derived
via `landing.js::resolveRoleAccent`) drives `--role-accent`, ambient UI color signaling "which space
am I in" — `coordinator` → primary blue, `provider` (pcp/specialist) → secondary green, `patient` →
sequential blue. `admin`/`payer_admin`/unroled sessions get no override (neutral gridline default).
Role precedence for both landing route and accent is `admin > care_coordinator > payer_admin >
specialist/pcp > patient` — deliberately mirrors `assistant_graph.py`'s `_ROLE_PRECEDENCE` so "which
role is this account acting as" answers consistently across the chat assistant and the dashboard
chrome. Font stack is system UI (`-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`),
base 14px, no custom webfont load.

## 4. Navigation & Role-Gated Landing

`NAV_ITEMS` (`app.js`) — each item optionally gated by `roles: [...]` (must hold at least one) or
`permission: "..."` (checked via `hasPermission`, backed by `GET /auth/me`'s permission set):

| Nav item | Route | Gate |
|---|---|---|
| Home | `/home` | `patient` role only |
| Ops Queue | `/ops-queue` | `care_coordinator` role only |
| My Day | `/my-day` | `pcp`/`specialist` roles |
| Patients | `/patients` | none (permission-scoped server-side) |
| Doctors | `/doctors` | none |
| Medical Records | `/medical-records` | none |
| Referrals | `/referrals` | none |
| Scheduling & Appointments | `/schedule` (alias `/appointments`) | none |
| Analytics | `/analytics` | `analytics:view` |
| Audit Log | `/audit` | `audit:view` |
| Assistant | `/assistant` | none |
| Admin | `/admin` | `admin:*` |

**Landing route on login / `/` / unmatched path** (`landing.js::resolveLandingRoute`) is role-specific,
not a one-size-fits-all redirect to Patients:

| Role (precedence order) | Lands on |
|---|---|
| `admin` | `/admin` |
| `care_coordinator` | `/ops-queue` |
| `payer_admin` | `/analytics` |
| `specialist` / `pcp` | `/my-day` |
| `patient` | `/home` |
| (no recognized role — fresh registration, unlinked staff) | `/patients` |

## 5. Page Inventory (by module)

| Module | Route(s) | Purpose | Primary audience |
|---|---|---|---|
| `auth.js` | `/login`, `/register` | Login/register, "Fill Sample Data" for quick demo registration | Everyone (pre-auth) |
| `home.js` | `/home` | Patient landing: upcoming appointment card (reuses `appointments.js`'s `selfServiceActions`), quick links | `patient` |
| `ops_queue.js` | `/ops-queue` | Care coordinator's action queue — referrals needing approval/override, at a glance | `care_coordinator` |
| `my_day.js` | `/my-day` | Doctor's daily list — today's appointments, click-through to appointment detail (previously read-only, now fully actionable) | `pcp`, `specialist` |
| `patients.js` | `/patients` | Generic resource table (via `resource.js`) — search, create ("Fill Sample Data" available), edit, delete | All roles (server-scoped) |
| `patient_detail.js` | `/patients/:id` | Read-only unified patient context (appointments/records/referrals/insurance/care-team in one call) — "View all →" links out, deliberately not inline-editable | Staff roles, self (patient) |
| `doctors.js` | `/doctors` | Directory CRUD | All roles (GET open to all authenticated; mutation gated) |
| `appointments.js` | mounted inside `/schedule` | Resource table for appointments | All roles (server-scoped) |
| `appointment_detail.js` | `/appointments/:id` | Appointment + doctor card + patient card + consultation/outcome section | Party to the appointment, staff |
| `schedule.js` | `/schedule` (alias `/appointments`) | Availability management, slot generation, symptom→doctor recommendation → book, appointments table | Staff + patient self-service booking |
| `medical_records.js` | `/medical-records` | Resource table + a "quick upload" toolbar button (standalone document upload, no referral needed) | All roles (server-scoped) |
| `referrals.js` | `/referrals`, `/referrals/:id` | List + detail: documents/notes/workflow-state/outcome/timeline tabs, live SSE status banner, eligibility-denial review/override UI, consult-outcome recording | All roles (server-scoped) |
| `new_request.js` | `/requests/new` | Unified referral-or-direct-appointment creation: mode dropdown, patient selector, doc pick/upload, symptom-driven doctor recommendation (city-ranked), optional slot picker, stale-recommendation nudge | Patient / PCP / care_coordinator |
| `analytics.js` | `/analytics` | Hand-rolled bar charts (no chart library) over the analytics summary endpoint | `analytics:view` holders |
| `audit.js` | `/audit` | Audit log table | `audit:view` holders |
| `assistant.js` | `/assistant` | Chat UI, role-aware suggestion chips (mirrors backend role-precedence client-side) | All roles |
| `admin.js` | `/admin` | User/role management, patient/doctor linking, password reset | `admin:*` holders |

## 6. Referral Detail Page — the richest single view

Tabs on `referrals.js`'s detail page, each backed by its own scoped endpoint:
1. **Overview** — status banner translating the raw state-machine value into plain language
   (`REFERRAL_PROGRESS_INFO` in `static/js/utils.js`: label + "waiting on" + next step — added because
   a raw status like `awaiting_specialist_approval` doesn't say whose court the ball is in).
2. **Documents** — upload, list, download; an "Attached Medical Record" card if one was linked at
   creation.
3. **Notes** — specialist notes (AI-generated history summary) + a real add-comment form.
4. **Workflow State** — live state snapshot; the eligibility-denial review UI (comment box,
   document-attach, "Override & Proceed" button, gated on `referral:override`) appears here exactly
   when `status === "eligibility_denied"`.
5. **Timeline** — full milestone-by-milestone history read from the durable outbox events.
6. **Outcome** — consult recording (shared `consultation.js` component), including a one-click
   "Complete with Defaults" POC shortcut for demos.

A live SSE-style status indicator on the header updates in real time as the LangGraph workflow
advances, without a page refresh.

## 7. Design Decisions Worth Preserving

- **Booking receipt = the appointment record itself**, no separate "receipt" model — the same
  `Appointment` row, already visible to patient/doctor/care_coordinator per existing scoping, is
  rendered as a printable confirmation (`window.print()`) right after booking. Don't add a parallel
  receipt entity.
- **Sample-data buttons use real, demo-verified values**, not invented ones — patient sample data
  always uses a real payer-mock-verified policy number so eligibility checks demo correctly; doctor
  sample data mirrors the mock provider directory's names/specialties for flavor consistency.
  `GET /auth/sample-patient-data` (unauthenticated) backs the registration screen's version.
- **Blocking alerts for a genuinely blocking state, toasts for everything else.** An empty
  Patient-selector dropdown for a `patient`-role account (root cause: role granted but not linked to a
  Patient record) shows a blocking `alert()` with the exact admin-panel fix path — a toast that
  auto-dismisses in ~4s was tried first and wasn't enough for something the user can't self-resolve
  without specific instructions.
- **The Outcome tab is omitted entirely** for a caller who'd 403 on it, rather than shown-then-blocked
  — don't render a control a role can't use and then explain why it doesn't work; just don't render it.
- **A capability caption directly in the UI** (e.g. above the referral-creation Patient field, and in
  the Workflow State tab about the mock-provider-directory synthetic-doctor-id behavior) is preferred
  over a support doc when a limitation is likely to visibly confuse a user in the moment.

See [`architeture.md`](architeture.md) §5 for the assistant's own design (tool allowlists, prompts) and
[`rules.md`](rules.md) §5 for known UI/UX gaps (e.g. referral PATCH/DELETE having no server-side
`:manage` gate, only client-side hiding).
