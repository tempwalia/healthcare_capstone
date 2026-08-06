# Platform Workflow — Roles, Pages, Data Points & How They Connect

This document walks through the whole system end-to-end: how a person becomes
a role, what each role sees from login onward, every data point each page
reads or writes, and how those data points flow between roles — with the
referral lifecycle and the Admin panel's linking mechanism as the two threads
that tie everything together.

It describes the system as implemented in `app/` and `static/`, not the
aspirational plan — see `CAPSTONE_IMPLEMENTATION_GUIDE.md` for the phased
build history and design rationale behind these decisions.

---

## 1. The three-identity model (read this first)

Almost every "why doesn't this show up" question in this system traces back
to one fact: **a login and a clinical record are two different rows in two
different tables, linked by an optional foreign key.**

| Table | What it is | Key column |
|---|---|---|
| `users` | A login: username/email/password hash. Has zero clinical meaning by itself. | `users.id` |
| `patients` | A clinical/demographic record: name, DOB, insurance, allergies, etc. | `patients.id`, nullable `patients.user_id` |
| `doctors` | A directory record: name, specialization, license. | `doctors.id`, nullable `doctors.user_id` |
| `roles` / `permissions` | What a user is *allowed to do* (RBAC), independent of which clinical record they are. | `user_roles`, `role_permissions` |

A brand-new `POST /auth/register` produces **only** a `users` row with **no
role and no linked patient/doctor**. Three separate admin actions turn that
into a working account:

1. **Grant a role** (`POST /admin/users/{id}/roles`) — decides *what the
   account is allowed to do* (see the permission matrix in §3).
2. **Link to a Patient or Doctor record** (`POST /admin/users/{id}/link-patient/{patient_id}`
   or `.../link-doctor/{doctor_id}`) — decides *which clinical record the
   account's own-data views (`patient:view_own`, `appointment:view_own`,
   `referral:view_own`, etc.) resolve to*.
3. (Optional) Set a password via reset, if handing the account to a demo user.

Steps 1 and 2 are independent and both required. A user with the `patient`
role but no linked `Patient` row has `patient:view_own` permission but **owns
nothing** — every scoped query in `app/services/record_scope.py` /
`referral_scope.py` looks up `Patient.user_id == current_user.id`, finds
nothing, and returns an empty list (not an error, not everything — see §7).
This is precisely the "empty Patient dropdown" failure mode the dashboard's
Admin panel and referral form now guard against with an explicit alert
telling the user to go get linked.

```
 users (login)                patients / doctors (clinical record)
 ┌───────────┐   user_id FK   ┌───────────────────────┐
 │ id        │◄───────────────│ id                    │
 │ username  │   (nullable,   │ first_name/last_name  │
 │ password  │    1:1)        │ DOB, insurance, ...    │
 └─────┬─────┘                └───────────────────────┘
       │  user_roles (M:N)
       ▼
     roles ──role_permissions (M:N)──► permissions
```

---

## 2. Login → dashboard, mechanically

This sequence is the same for every role; only what renders afterward
differs.

1. **`POST /auth/login`** (`app/api/routes/auth.py`) — username + password →
   an access JWT (short-lived, `sub=username`) + a refresh token (opaque,
   hashed at rest in `refresh_tokens`, rotated on every `/auth/refresh` call,
   revoked on `/auth/logout` or an admin password reset). Rate-limited to
   5/minute. Both tokens are stored in `localStorage` by `static/js/state.js`.
2. **`GET /auth/me`** — the dashboard's very first authenticated call
   (`static/js/modules/auth.js::fetchMe`). Returns `{id, username, email,
   roles, permissions}` — the permissions list is the **union of every
   granted role's permissions**, computed the same way `require_permission`
   checks it server-side. The frontend never re-derives permissions from role
   names; it trusts this list verbatim (`static/js/state.js::hasPermission`).
3. **Nav rendering** (`static/js/app.js::NAV_ITEMS`) — each nav item optionally
   declares a required permission; items without a matching permission are
   simply not rendered. This is a UX convenience only — every route is
   re-checked server-side regardless of what the sidebar shows.
4. **Landing page** — every role lands on `/patients` (formatted per that
   role's own visibility scope — see §7.1) unless redirected.

No server-side page routes exist for the dashboard itself — `/app` is a
static-file mount with hash-based client routing (`#/referrals/12`), so a
deep link never needs a matching FastAPI route.

---

## 3. Roles and what each one is allowed to do

Defined in `app/core/seed.py`, re-synced on every startup (idempotent).

| Permission | patient | pcp | specialist | care_coordinator | payer_admin | admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `referral:create` | ✅ | ✅ | | | | ✅* |
| `referral:view_own` | ✅ | ✅ | ✅ | | | ✅* |
| `referral:view_all` | | | | ✅ | ✅ | ✅* |
| `referral:approve` | | | | ✅ | | ✅* |
| `referral:override` | | | | ✅ | | ✅* |
| `referral:record_outcome` | | | | ✅ | | ✅* |
| `patient:view_own` | ✅ | | | | | ✅* |
| `patient:view_all` | | ✅ | ✅ | ✅ | | ✅* |
| `patient:manage` | | ✅ | ✅ | ✅ | | ✅* |
| `doctor:manage` | | ✅ | ✅ | ✅ | | ✅* |
| `appointment:view_own` | ✅ | ✅ | ✅ | | | ✅* |
| `appointment:view_all` | | | | ✅ | | ✅* |
| `appointment:manage` | | ✅ | ✅ | ✅ | | ✅* |
| `medical_record:view_own` | ✅ | ✅ | ✅ | | | ✅* |
| `medical_record:view_all` | | | | ✅ | | ✅* |
| `medical_record:manage` | | ✅ | ✅ | | | ✅* |
| `analytics:view` | | | | ✅ | ✅ | ✅* |
| `audit:view` | | | | | | ✅* |
| `admin:*` | | | | | | ✅ |

\* `admin:*` is a single bypass permission checked first everywhere
(`require_permission`) — the admin role doesn't actually hold the individual
permissions above, it short-circuits every check.

Two design points worth calling out because they surprise people reading the
table:

- **`patient:view_all` is broad** — every clinical/coordination role (pcp,
  specialist, care_coordinator) can look up *any* patient, because this app
  has no patient-panel/assignment model (a walk-in, a new intake, or
  cross-coverage all need to pull up a chart that isn't "theirs" yet).
- **`appointment`/`medical_record` visibility stays need-to-know even for
  clinical roles** — `view_own` for pcp/specialist means "encounters I'm
  actually the assigned doctor on," not "any patient's." Clinical notes are
  treated as more sensitive than basic demographics. `care_coordinator` holds
  `medical_record:view_all` for oversight but never `:manage` — coordinators
  don't author clinical notes.
- **`audit:view` is admin-only.** No clinical or coordination role can read
  the audit log in this build.

---

## 4. Every page, gated by what, showing what data

All list pages are paginated (`Page[T]` = `{items, total, skip, limit}`,
`app/schemas/common.py`) and all four CRUD-table pages
(Patients/Doctors/Appointments/Medical Records) share one generic factory,
`static/js/resource.js` — same table/modal/pager shape, different field
config per resource.

| Page (`#/...`) | Nav gate | Data shown (columns) | Create/Edit data points | Backend scope applied |
|---|---|---|---|---|
| **Patients** | none (all roles see the tab) | id, name, DOB, gender, phone, insurance provider | first/last name, email, phone, DOB, gender, address, emergency contact name/phone, insurance provider + **policy number**, allergies, blood type, preferred language, lifestyle, family history | `patient_visibility_filter` — `patient` role sees only their own linked row; everyone with `patient:view_all` sees all; writes need `patient:manage` |
| **Doctors** | none | id, name, specialization, department, phone, years of experience | + email, license number, bio, certifications, languages spoken, ratings (1-5), profile picture URL | Reads open to any authenticated user (non-PHI directory); writes need `doctor:manage` |
| **Appointments** | none | id, patient, doctor, date/time, status, type | patient, doctor, datetime, duration, reason, notes, type (in_person/telehealth/phone), location, reminder_sent, follow_up_required, (edit-only) status | `appointment_visibility_filter` — `view_own` = party to it (patient or assigned doctor); writes need `appointment:manage` |
| **Medical Records** | none | id, patient, doctor, visit date, diagnosis, treatment | patient, doctor, visit datetime, diagnosis, symptoms, treatment, prescription, notes, BP systolic/diastolic, heart rate, temperature, weight, height, record type | `medical_record_visibility_filter` — same need-to-know shape as appointments; writes need `medical_record:manage` |
| **Referrals** (list) | none | id, patient, referring doctor, status badge, requested date, target wait days | new referral: patient, referring doctor, specialist (optional), request date, reason, preferred location, target wait days | `referral_visibility_filter` — see §7.1; create needs `referral:create` |
| **Referral detail** | via list | header (patient/referring doctor/specialist/dates/reason) + 4 tabs: **Documents**, **Notes**, **Workflow State**, **Outcome** | document upload (file), specialist-approval selection, outcome form (symptoms/diagnosis/prescription/follow-up notes) | Same referral scope for the record; Outcome tab is staff-only (`_get_staff_scoped_referral` excludes the patient-visibility branch even though the referral itself is patient-visible) |
| **Scheduling** | none | availability windows, generated slots (doctor/start/end/booked), booking flow | doctor availability (weekday, start/end time, slot length); "Book an Appointment" (patient, symptoms text → ranked doctors → open slot → booking receipt) | Open to any authenticated user today (see §8 for the gap) |
| **Analytics** | `analytics:view` | 3 stat tiles (avg. time-to-schedule, delay-risk count, eligibility denial rate) + 2 bar charts (referrals by status, top specialties requested) | read-only | `require_permission("analytics:view")` |
| **Audit Log** | `audit:view` | id, timestamp, actor user id, action, JSON details | read-only | `require_permission("audit:view")` — admin only, per §3 |
| **Assistant** | none | role-scoped chat transcript | free-text chat message | see §9 |
| **Admin** | `admin:*` | user list with role chips + role/link/reset controls | grant/revoke role, link user↔patient, link user↔doctor, reset password | `require_permission("admin:*")` on every admin route |

---

## 5. Per-role walkthrough

### 5.1 Patient

**Permissions:** `referral:create`, `referral:view_own`, `patient:view_own`,
`appointment:view_own`, `medical_record:view_own`.

- Logs in → lands on **Patients**, sees exactly one row (their own linked
  record) or zero if not yet linked by an admin.
- **Appointments** / **Medical Records**: sees only encounters where they are
  the patient party.
- **Referrals**: the "+ Request a Referral" form auto-fills and **locks** the
  Patient field to their own linked record (fetched via `GET
  /patients/?limit=1`, scoped to "only me"). If unlinked, a blocking alert
  tells them to get an admin to link their account — see §10.
- Submitting a referral kicks off the whole cross-role LangGraph workflow
  (§6) — from this point on, the patient mostly *watches* (the referral
  detail page's live SSE indicator) while pcp/coordinator/specialist act.
- Can view (not create) specialist notes and workflow state for their own
  referral, but the **Outcome tab is invisible to them** — consult outcomes
  are staff-only even on a referral they can otherwise see in full.
- **Assistant**: role-scoped to "your own referrals only," with a system
  prompt that explicitly forbids speculating about other patients.
- Cannot see Analytics, Audit, or Admin at all (nav items don't even render).

### 5.2 PCP (Primary Care Provider)

**Permissions:** `referral:create`, `referral:view_own`, `patient:view_all`,
`patient:manage`, `doctor:manage`, `appointment:view_own`,
`appointment:manage`, `medical_record:view_own`, `medical_record:manage`.

- Can look up **any** patient (directory lookup — new intake, walk-in), and
  create/edit patient and doctor directory records.
- **Referrals**: submits on behalf of any patient (no auto-lock, unlike the
  patient role) — this is the "PCP refers patient to specialist" entry point
  the whole platform exists for. Fields: patient, referring doctor (usually
  themselves if linked), optional specialist, request date, reason,
  preferred location, target wait days.
- `referral:view_own` here means "referrals where I am the referring doctor
  or the specialist" (`referral_scope.py` checks both `referring_doctor_id`
  and `specialist_id` against the caller's linked `Doctor` row).
- **Appointments/Medical Records**: `view_own` = encounters where they're the
  assigned doctor; `manage` lets them create/edit records for any patient
  they've treated (no further ownership check on writes — see the Known Gaps
  note in §11).
- Cannot approve/override referral workflow steps, cannot see Analytics or
  Audit.

### 5.3 Specialist

**Permissions:** `referral:view_own`, `patient:view_all`, `patient:manage`,
`doctor:manage`, `appointment:view_own`, `appointment:manage`,
`medical_record:view_own`, `medical_record:manage`.

- Same CRUD reach as pcp over patients/doctors/appointments/medical records,
  **minus `referral:create`** — a specialist receives referrals, doesn't
  submit them.
- Sees referrals where they're the assigned `specialist_id` — but in
  practice, most AI-recommended specialists come from the **mock provider
  directory's synthetic doctor-id space** (Dr. Priya Rao #88, Dr. Daniel Kim
  #91, etc. — see `mock_systems/provider_directory_mock/main.py`), which has
  no corresponding real `doctors` row or platform login. A specialist role in
  this build is realistically populated by manually creating a `Doctor` row
  and referral with a matching real `specialist_id`, not by the AI workflow
  handing off automatically. This is also *why* `referral:record_outcome`
  belongs to `care_coordinator` and not `specialist` (§5.4, §6 step 8) — the
  realistic actor relaying a consult report is coordination staff, not a
  synthetic specialist with no login.
- **Assistant**: role-scoped to reviewing referral status/documents/notes for
  referrals they're party to.

### 5.4 Care Coordinator

**Permissions:** `referral:view_all`, `referral:approve`,
`referral:override`, `referral:record_outcome`, `analytics:view`,
`patient:view_all`, `patient:manage`, `doctor:manage`,
`appointment:view_all`, `appointment:manage`, `medical_record:view_all`
(no `medical_record:manage`).

This is the **operational hub role** — it's the only one that sees the whole
platform rather than a filtered slice:

- **Referrals**: sees every referral, platform-wide. Is the human-in-the-loop
  approver at the `awaiting_specialist_approval` step (§6 step 5) — the
  Workflow State tab surfaces ranked specialist candidates with scores/reasons,
  and a "Select this specialist" button calls `POST
  /referral-workflow/{id}/resume`, which unpauses the LangGraph interrupt and
  triggers scheduling.
- **Records the consult outcome** once the appointment has actually happened
  (symptoms/diagnosis/prescription/follow-up notes) via the Outcome tab —
  this is staff-only data, invisible to the patient.
- **Analytics** is coordinator-visible: referral funnel by status, delay-risk
  count, eligibility denial rate, top specialties requested, average
  time-to-schedule.
- **Assistant**: the broadest tool allowlist of any role — also gets
  `get_workflow_state`, `list_slots`, `list_availability` on top of the base
  referral tools, to support "coordinate care platform-wide" questions.
- Cannot see the Audit Log (admin-only) or reach the Admin panel.

### 5.5 Payer Admin

**Permissions:** `referral:view_all`, `analytics:view`. Nothing else.

- The narrowest role with platform-wide reach — read-only visibility into
  every referral and the analytics rollup, nothing else. No patient/doctor/
  appointment/medical-record access at all, no referral actions. Models a
  payer-side auditor checking utilization and denial-rate trends without
  touching clinical operations.

### 5.6 Admin

**Permissions:** `admin:*` (bypasses every other check).

- Full reach over every page, plus the **Admin nav item**, which no other
  role can even see. Covered in detail in §10 — this is the role that makes
  every other role's account actually usable.

---

## 6. The referral lifecycle — the thread connecting every role

This is the core cross-role saga. It's a LangGraph state machine
(`app/agents/graph.py`), persisted to Postgres via a checkpointer keyed by
`thread_id = "referral-{referral_id}"`, so it survives process restarts and
can pause for a human decision mid-flight.

| Step | Node | Actor who triggers it | Data consumed | Data produced | Referral status after |
|---|---|---|---|---|---|
| 1 | *(route entry)* | **Patient or PCP** — `POST /referral/requests/` | patient_id, referring_doctor_id, specialist_id?, request_date, reason, preferred_location, target_wait_days | `ReferralRequest` row + `workflow_thread_id` | `submitted` |
| 2 | `intake_node` | system (background task) | uploaded `ReferralDocument`s' extracted text | `diagnosis_codes`, `procedure_codes` (ICD-10/CPT, LLM or regex fallback), `missing_documents` | `awaiting_documents` (if a referral letter or imaging/labs doc is missing) or `eligibility_checking` |
| 2a | *(re-entry)* | **Patient/PCP** — `POST /referral/requests/{id}/documents` | uploaded file | re-triggers `intake_node` from scratch if still `awaiting_documents` | same as step 2 |
| 3 | `eligibility_node` | system, via mock payer (`check_eligibility` MCP tool) | patient's `insurance_policy_number`, first diagnosis code | `eligibility{verified, network_status, copay_estimate_usd, prior_auth_required}` | `awaiting_specialist_approval` (verified) or `eligibility_denied` |
| 3a | `escalate_eligibility_node` | system | denied eligibility result | audit log entry + `referral.eligibility.escalated` event | `eligibility_denied` (graph run ends — a human override endpoint exists but coordinator UI doesn't expose a resume-from-denial action yet) |
| 4 | `specialist_node` | system, via mock provider directory (`search_providers` MCP tool) | inferred specialty (diagnosis-code prefix or keyword match on `reason`), `preferred_location` | `specialist_candidates` (ranked list: doctor_id, score, reasons — in-network/distance/rating/next-slot) | `awaiting_specialist_approval` |
| 5 | `await_specialist_approval` | **Care Coordinator** — `POST /referral-workflow/{id}/resume {doctor_id}` | one chosen candidate from step 4 | `selected_doctor_id` | `scheduling` |
| 6 | `scheduling_node` | system, via mock scheduling (`get_availability`/`book_slot` MCP tools) | `selected_doctor_id` | `appointment{appointment_id, scheduled_for}` (mock-system booking, not a `schedule_slots` row — see §11) | `scheduled` (or `scheduling_delayed` if no slot within target) |
| 7 | `summarizer_node` | system | patient's prior `MedicalRecord`s (last 5 visits), allergies, this referral's reason | a `SpecialistNote` (LLM or template-generated care-journey summary for the specialist to read pre-consult) | unchanged |
| 8 | `notify_node` | system, via mock notification (`send_notification` MCP tool) | patient's linked `user_id`, scheduled time | a delivery receipt (patient-facing "your appointment is confirmed" message) | unchanged (graph reaches `END`) |
| 9 | *(outside the graph)* | **Care Coordinator** — `POST /referral/requests/{id}/outcome` | symptoms, diagnosis, prescription, follow-up notes (what actually happened at the consult) | `ReferralOutcome` row + background-generated `interaction_summary` (whole-care-journey summary reusing step 7's history-gathering logic) | `completed` |

Every status transition also writes an outbox event (`app/events/outbox.py`)
that the referral detail page's live SSE stream (`GET
/referral/requests/{id}/events`) surfaces in real time to anyone watching —
this is how a patient sees their referral move from `submitted` to
`scheduled` without refreshing.

**Why this table is "how data points connect roles":** the *reason* text a
patient or PCP types at step 1 becomes the specialty inference input at step
4; the *insurance policy number* a PCP entered on the Patient page at some
earlier point becomes the eligibility check input at step 3; the *specialist
candidate* a coordinator picks at step 5 becomes the *doctor* on the booked
appointment everyone (patient/doctor/coordinator) sees on the Appointments
page after step 6; the *prior medical records* a pcp/specialist wrote weeks
ago become the specialist's pre-consult briefing at step 7. No role acts in
isolation — each step's output is the next role's input.

---

## 7. Two visibility mechanisms worth understanding directly

### 7.1 `referral_visibility_filter` (`app/services/referral_scope.py`)

- `referral:view_all` or `admin:*` → no filter, sees everything.
- Otherwise requires `referral:view_own`, then ANDs in:
  `patient_id == caller's linked Patient.id` OR
  `referring_doctor_id == caller's linked Doctor.id` OR
  `specialist_id == caller's linked Doctor.id`.
- If the caller holds `referral:view_own` but has **no** linked
  patient/doctor record at all, the filter evaluates to `false()` —
  authenticated, permitted, but linked to nothing, so they see **nothing**,
  not everything. This is the deliberate fail-closed behavior that makes
  Admin-panel linking mandatory, not optional.

### 7.2 `patient` / `appointment` / `medical_record` visibility (`app/services/record_scope.py`)

Same shape, three separate filters with different breadth (see §3's
"need-to-know" note): `patient_visibility_filter` grants `view_all` broadly
to clinical roles; `appointment_visibility_filter` and
`medical_record_visibility_filter` stay strictly party-to-the-encounter even
for pcp/specialist.

---

## 8. Scheduling — a shared resource, not a role-owned one

`DoctorAvailability` (recurring weekly windows) → `ScheduleSlot` (materialized
concrete bookable slots, idempotent generation) → booking an `Appointment`
and marking the slot `is_booked`. Every write route
(`create_availability`, `generate_slots`, `book_slot`) currently only checks
`get_current_active_user` — **any authenticated user** can generate slots or
book on behalf of any patient today (this is a known, documented gap, not an
oversight — see §11). The dashboard's "Book an Appointment" card layers a
client-side specialty-keyword recommendation
(`static/js/modules/schedule.js::SPECIALTY_KEYWORDS`, mirroring
`specialist_node`'s server-side heuristic) on top of this for a
non-AI-workflow manual booking path — useful for a coordinator or pcp booking
a direct visit, separate from the AI referral pipeline in §6.

---

## 9. The Assistant — same chat UI, different tools per role

`POST /assistant/chat` resolves the caller's most-privileged role
(`care_coordinator > specialist > pcp > patient`, `assistant_graph.py`), then
builds an LLM agent fresh for that request with:

- A **role-specific system prompt** (§5's per-role permissions echoed as
  instructions — e.g. the patient prompt explicitly says "never speculate
  about another patient").
- A **role-specific tool allowlist**: every role gets `get_referral`,
  `list_referrals`, `list_referral_documents`, `list_specialist_notes`;
  `care_coordinator` additionally gets `get_workflow_state`, `list_slots`,
  `list_availability`.
- Tool calls run through the platform's **real per-user JWT**, not a shared
  system identity — an LLM tool call is exactly as scoped as a browser
  request from that same user would be. Deliberately **not** exposing
  `get_patient`/`get_doctor`/`get_appointment`/`get_medical_record` as tools
  at all, because those REST routes have no ownership scoping (§11) — handing
  an LLM a tool that can fetch any patient by ID would defeat the whole
  point.
- With no LLM configured, every role falls back to the same deterministic
  keyword-matched FAQ responder (no tool calls, no patient data) — this path
  is role-agnostic on purpose since it never touches real data.

---

## 10. The Admin panel — why linking is the glue

The Admin page (`static/js/modules/admin.js`, gated by `admin:*`) is the only
place any of the following can happen, and every one of them is required
before another role's account produces anything meaningful:

| Admin action | Backend route | What breaks without it |
|---|---|---|
| **Grant a role** | `POST /admin/users/{id}/roles` | The account has zero permissions — every gated route returns 403, and the nav sidebar renders almost nothing. |
| **Revoke a role** | `DELETE /admin/users/{id}/roles/{role_name}` | (used to walk back a grant, e.g. demoting a test coordinator back to plain patient) |
| **Link to Patient** | `POST /admin/users/{id}/link-patient/{patient_id}` | `patient:view_own` has no linked row to resolve to → `GET /patients/` returns `[]`; the referral self-service form has nothing to auto-fill and shows a blocking alert instead of a form; `appointment:view_own`/`medical_record:view_own`'s patient-side condition also comes up empty. |
| **Link to Doctor** | `POST /admin/users/{id}/link-doctor/{doctor_id}` | A pcp/specialist account's `view_own` doctor-side condition (appointments, medical records, referrals-as-referring-doctor-or-specialist) has nothing to match against. |
| **Reset password** | `POST /admin/users/{id}/reset-password` | The only way to hand a demo account a known password without ever reading the real hash back (bcrypt is one-way by design) — also revokes the user's existing refresh tokens so an old session can't outlive the reset. |

**Concretely, why this matters:** granting the `patient` role and linking a
user to a `Patient` row are *two separate actions on two separate tables*
(`user_roles` vs. `patients.user_id`). A user can legitimately have one
without the other — e.g. right after `POST /auth/register`, before an admin
has done anything. The role alone is enough to make `patient:view_own`-gated
routes return `200` instead of `403`, but the query behind that `200` still
has nothing to filter down to without the link — it returns an empty list,
which from the outside looks identical to "the feature is broken." This
exact failure mode was hit and confirmed live during dashboard testing (an
account had the role but not the link), and it's why both the referral
form's self-service path and the Admin panel's own description text now spell
out explicitly that **both steps are required**, not just one.

In short: roles answer "what can this login do," linking answers "whose data
does 'my own' mean" — and every `*:view_own` permission in the system depends
on both being set before it does anything useful.

---

## 11. Known, documented gaps (so nobody re-discovers them as surprises)

- **Doctor directory mutations, appointment/medical-record/patient writes
  have no ownership check beyond the coarse `*:manage` permission** — e.g. a
  pcp can edit a patient they've never actually treated, or a medical
  record's `doctor_id` in the request body isn't verified to match the
  caller. Read-side visibility scoping (§7) is real; write-side ownership
  enforcement is not, and is called out as deferred hardening in the
  implementation guide.
- **Scheduling routes (`/schedule/*`) have no permission gate at all** beyond
  being authenticated — any logged-in user can generate slots or book an
  appointment for any patient/doctor pair.
- **Referral PATCH/DELETE only check visibility, not a `:manage`-style
  permission** — a `patient` role could technically edit/delete their own
  referral via the raw API. The dashboard gates the Edit/Delete buttons
  client-side on `referral:approve`/`referral:override`/`admin:*` as a UX
  guard, but this is not a server-side enforcement.
- **The AI-recommended specialist is usually not a real platform login** —
  candidates come from the mock provider directory's synthetic doctor-id
  space (§5.3), which is why outcome-recording belongs to `care_coordinator`
  rather than the specialist role.
