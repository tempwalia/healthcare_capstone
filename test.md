# Manual Test Guide — Sample Data & Steps

For hitting the running API by hand (Swagger UI at `http://localhost:8000/docs` is the easiest way —
paste the JSON bodies below into "Try it out"; curl/PowerShell works too).

## 0. Start the server

```
uv run python scripts/seed_roles.py
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.core.event_loop:selector_event_loop_factory
```
(The `--loop` flag is required on Windows — psycopg's async mode can't use the default ProactorEventLoop.)

There's no API endpoint for role assignment (by design — RBAC is DB-driven, not self-service). After
registering a user below, grant it a role with:
```
uv run python scripts/grant_role.py <username> <role_name>
```
Valid roles: `patient`, `pcp`, `specialist`, `care_coordinator`, `payer_admin`, `admin`.

---

## 1. Auth — register + login

Register a coordinator account (creates fixture data throughout this guide) and a plain patient account:

`POST /auth/register`
```json
{"email": "coord@example.com", "username": "coord1", "password": "testpass123"}
```
```json
{"email": "alice@example.com", "username": "alice", "password": "testpass123"}
```

`POST /auth/login` (form data, not JSON) — `username=coord1&password=testpass123` — copy the
`access_token` from the response into `Authorization: Bearer <token>` for every request below.

Then, in a terminal:
```
uv run python scripts/grant_role.py coord1 care_coordinator
uv run python scripts/grant_role.py alice patient
```
`care_coordinator` holds every `*:manage`/`*:view_all` permission, so use `coord1`'s token for all
setup/creation steps below unless a step says otherwise.

**Expect**: register → 201; login → 200 with `access_token` + `refresh_token`; a 6th rapid bad login
attempt on the same account → 429 (rate limit).

---

## 2. Patients & doctors (as `coord1`)

`POST /patients/`
```json
{
  "first_name": "Jamie", "last_name": "Rivera", "email": "jamie.rivera@example.com",
  "date_of_birth": "1985-04-12", "gender": "female",
  "insurance_provider": "Acme Health", "insurance_policy_number": "ACME-991123"
}
```
→ note the returned `id` as `PATIENT_ID`. (`insurance_policy_number: ACME-991123` is a real policy
seeded in the payer mock — verified, in-network, prior-auth required. Omit it, or use
`NOT-A-REAL-PLAN`, to exercise the eligibility-denial path instead.)

`POST /doctors/`
```json
{
  "first_name": "Sarah", "last_name": "Smith", "email": "dr.smith@hospital.com",
  "phone": "+15550001111", "specialization": "Cardiology", "license_number": "MD123456",
  "years_of_experience": 10, "bio": "Referring PCP"
}
```
→ note the returned `id` as `DOCTOR_ID`.

**Expect**: both 201. `GET /doctors/` works for *any* authenticated user (directory is intentionally
open); `GET /patients/` as `alice` (patient role, not linked to any record yet) returns an **empty**
list, not everyone's — see §6.

---

## 3. Referral workflow — eligible path (as `coord1`)

`POST /referral/requests/`
```json
{
  "patient_id": PATIENT_ID, "referring_doctor_id": DOCTOR_ID,
  "request_date": "2026-08-06", "reason": "Persistent lower back pain, suspected herniated disc"
}
```
→ 202, `status: "submitted"`, note `id` as `REFERRAL_ID`. The workflow runs in the background and
immediately pauses at `awaiting_documents` (no document can exist before the referral does).

`GET /referral/requests/{REFERRAL_ID}` → `status: "awaiting_documents"`.

Upload the two documents the intake node looks for (filename-keyword matched — use these exact names).
Space the two uploads a couple seconds apart if testing against a live server (background tasks can
race on the same workflow thread otherwise):

`POST /referral/requests/{REFERRAL_ID}/documents` (multipart, field name `file`)
- File 1: `referral_letter.txt`, content: `Patient presents with low back pain, diagnosis M54.5, referred for procedure 99213.`
- File 2: `mri_report.txt`, content: `MRI imaging report: mild disc bulge, no acute findings.`

`GET /referral/requests/{REFERRAL_ID}` → should now be `awaiting_specialist_approval` (eligible, since
the policy above is real).

`GET /referral-workflow/{REFERRAL_ID}/state` → check `specialist_candidates` (non-empty, each with
`doctor_id`/`score`/`reasons`) and `diagnosis_codes` (should include `M54.5`). Note a candidate's
`doctor_id` as `CANDIDATE_ID`.

`POST /referral-workflow/{REFERRAL_ID}/resume`
```json
{"doctor_id": CANDIDATE_ID}
```
→ 200, `status: "scheduled"`.

`GET /referral/requests/{REFERRAL_ID}/notes` → one specialist note referencing the reason + any prior
history.

---

## 4. Referral workflow — denied path

Repeat §3's `POST /referral/requests/` with a **new patient** (no `insurance_policy_number`, or
`"insurance_policy_number": "NOT-A-REAL-PLAN"`) and the same doctor. After both documents are uploaded,
`GET /referral/requests/{id}` → `status: "eligibility_denied"` (no pause, no specialist step).

---

## 5. Scheduling directly (as `coord1`)

`POST /schedule/availability/`
```json
{"doctor_id": DOCTOR_ID, "weekday": 0, "start_time": "09:00", "end_time": "12:00", "slot_minutes": 30}
```

`POST /schedule/slots/generate`
```json
{"doctor_id": DOCTOR_ID, "days_ahead": 14}
```
→ list of generated slots; note one `id` as `SLOT_ID`. Calling this twice in a row returns `[]` the
second time (idempotent).

`POST /schedule/slots/{SLOT_ID}/book`
```json
{"patient_id": PATIENT_ID, "reason": "Follow-up"}
```
→ 201, creates an `Appointment`. Booking the same `SLOT_ID` again → 409.

---

## 6. Ownership scoping (the IDOR fix) — this is the interesting one

As `coord1`, create a **second** patient (different email) — note its id as `OTHER_PATIENT_ID`.

Then link `alice`'s account to the *first* patient only:
```
uv run python -c "
import asyncio
from sqlalchemy import select
from app.database.session import async_session
from app.models.patient import Patient
from app.models.user import User
async def main():
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.username=='alice'))).scalar_one()
        patient = await db.get(Patient, PATIENT_ID)
        patient.user_id = user.id
        await db.commit()
asyncio.run(main())
"
```
(replace `PATIENT_ID` with the real int first). Log in as `alice` and try:

| Request | Expect |
|---|---|
| `GET /patients/{PATIENT_ID}` (her own) | 200 |
| `GET /patients/{OTHER_PATIENT_ID}` (not hers) | **404** — not a 403 leak, looks like it doesn't exist |
| `GET /patients/` | only her own record in `items` |
| `PUT /patients/{PATIENT_ID}` with `{"phone": "+10000000000"}` | **403** — `patient` role has `view_own`, not `manage` |
| `POST /doctors/` (any body) | **403** — `doctor:manage` required |
| `GET /referral/requests/{REFERRAL_ID}` (her own referral from §3) | 200 |

Before this fix, **every one of the 404/403 rows above returned 200** — any authenticated user could
read or write any patient/doctor/appointment/medical-record by ID.

---

## 7. Medical records (as `coord1`, needs `pcp`/`specialist`/`care_coordinator` — `manage` is not
granted to `care_coordinator`, use `pcp` for writes)

```
uv run python scripts/grant_role.py coord1 pcp
```
`POST /medical-records/`
```json
{
  "patient_id": PATIENT_ID, "doctor_id": DOCTOR_ID, "visit_date": "2026-05-01T09:00:00Z",
  "diagnosis": "Type 2 Diabetes Mellitus", "treatment": "Metformin"
}
```
→ 201. `GET /medical-records/{id}` as `alice` (linked to the same patient) → 200; as a *different*
unlinked patient account → 404.

---

## 8. Referral outcome + completion summary (as `coord1`, needs `care_coordinator`)

Using the `REFERRAL_ID` scheduled in §3:

`POST /referral/requests/{REFERRAL_ID}/outcome`
```json
{
  "symptoms": "Lower back pain, limited mobility", "diagnosis": "Lumbar disc herniation",
  "prescription": "Naproxen 500mg twice daily", "follow_up_notes": "Follow up in 4 weeks"
}
```
→ 202, referral moves to `completed`. `GET /referral/requests/{REFERRAL_ID}/outcome` a moment later →
`interaction_summary` populated (background task). Recording it a second time → 409. As `alice`
(patient) → 403 (outcome is staff-only, even though she can see the referral itself).

---

## 9. Analytics (as `coord1`)

`GET /analytics/referrals/summary` → real counts, e.g.:
```json
{
  "by_status": {"scheduled": 1, "eligibility_denied": 1},
  "avg_time_to_schedule_hours": 0.04,
  "delay_risk_referrals": 0,
  "top_specialties_requested": [{"specialty": "Orthopedics", "count": 2}],
  "eligibility_denial_rate": 0.5
}
```
As `alice` (no `analytics:view`) → 403.

---

## 10. Audit log (needs `admin`)

```
uv run python scripts/grant_role.py coord1 admin
```
`GET /audit/` → paginated log of every mutating action taken above (`patient.create`,
`referral.create`, `schedule.slot.book`, etc.), each with `actor_id`/`action`/`timestamp`.

---

## 11. Conversational assistant (as `alice`)

`POST /assistant/chat`
```json
{"message": "What's the status of my referral?", "session_id": "s1"}
```
→ 200. With `LLM_ENABLED=false` or no key, this is the deterministic FAQ fallback; with the real Groq
key configured (`.env`), it's a real tool-calling agent scoped to `alice`'s own data — try asking about
`REFERRAL_ID` from §4 (not hers) and confirm it's refused, not fabricated.

---

## Quick reference — who can do what

| Role | Can create/edit | Can view |
|---|---|---|
| `patient` | nothing | own patient/appointment/medical-record/referral only |
| `pcp` / `specialist` | patients, doctors, appointments, medical records | any patient (directory-style); own appointments/records only |
| `care_coordinator` | patients, doctors, appointments; referral approve/override/outcome | everything (referrals, appointments, medical records); not medical-record writes |
| `admin` | everything | everything |
