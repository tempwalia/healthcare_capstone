# Runbook — How to Run This Project

Step-by-step commands only. For what the system actually does, see `README.md`; for the
role/permission/data-flow reference, see `WORKFLOW.md`.

Two ways to run it — pick one:

- **[Option A: Docker](#option-a-docker-recommended)** — one command, matches how this project is graded (`docker compose up --build`).
- **[Option B: Local dev](#option-b-local-dev-uv)** — for active development with hot-reload.

Either way, finish with **[First-Time Bootstrap](#first-time-bootstrap-required-either-way)** — the
app has no self-service admin signup, so a few one-time commands are required before any account
can actually do anything.

---

## Prerequisites

| Option A (Docker) | Option B (Local dev) |
|---|---|
| Docker Desktop running | Python 3.11+ |
| — | [`uv`](https://docs.astral.sh/uv/) installed |
| — | A running Postgres 16 instance |

---

## Option A: Docker (recommended)

```bash
cd healthcare_capstone
cp .env.example .env      # fill in SECRET_KEY; LLMGW_API_KEY optional (see note below)
docker compose up --build
```

That's it — this single command builds the image, starts Postgres, runs migrations, seeds
roles/permissions, and starts the API. Wait for:

```
api-1  | INFO:     Application startup complete.
api-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Then open:
- **`http://localhost:8000/app`** — the dashboard
- **`http://localhost:8000/docs`** — Swagger UI
- **`http://localhost:8000/health/ready`** — should return `{"status":"healthy","database":true}`

**Note on `DATABASE_URL`**: whatever value is in your `.env` is overridden automatically by
`docker-compose.yml` to point at the compose-managed `postgres` service — you don't need to edit it
for Docker.

**Useful commands:**

```bash
docker compose logs -f api          # tail API logs
docker compose exec api sh          # shell inside the running container
docker compose down                 # stop (keeps data)
docker compose down -v              # stop AND wipe the Postgres/uploads volumes (clean slate)
docker compose up --build           # rebuild after a code change
```

---

## Option B: Local dev (uv)

```bash
cd healthcare_capstone
uv sync
cp .env.example .env
```

Edit `.env` — set `DATABASE_URL` to your own running Postgres instance, and a real `SECRET_KEY`.

```bash
uv run alembic upgrade head
uv run python scripts/seed_roles.py
uv run uvicorn app.main:app --reload
```

**On Windows**, add the loop flag (LangGraph's Postgres checkpointer driver can't run under
Windows' default event loop):

```bash
uv run uvicorn app.main:app --reload --loop app.core.event_loop:selector_event_loop_factory
```

Same URLs as above (`/app`, `/docs`, `/health/ready`) once you see `Application startup complete`.

---

## First-Time Bootstrap (required, either way)

A brand-new `POST /auth/register` only creates a bare login — no role, no linked patient/doctor
record. Nothing useful happens in the dashboard until you do this once.

**1. Register your first account** — via the dashboard's Register screen at `/app`, or:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "email": "you@example.com", "password": "yourpassword123"}'
```

**2. Grant yourself `admin`** — there's no API route for this by design (see `WORKFLOW.md` §10);
it's a one-time script:

```bash
# Docker:
docker compose exec api python scripts/grant_role.py your_username admin

# Local dev:
uv run python scripts/grant_role.py your_username admin
```

**3. Log in and open the Admin panel** (`/app` → sidebar → Admin) to bootstrap other accounts:

- Register more test users (patient, pcp, care_coordinator, ...) via the dashboard's Register
  screen, or `POST /auth/register` again with different usernames.
- For each, **grant a role** (Admin panel → role chips → grant), then, for `patient`/`pcp`/
  `specialist`/`doctor` accounts, **link them to a clinical record**:
  - Create a Patient/Doctor row first (Patients / Doctors pages, or `POST /patients/` /
    `POST /doctors/`), then use the Admin panel's "Link to Patient"/"Link to Doctor" action.
  - Skipping this step is the single most common "why is this empty" surprise — a role alone
    grants *permission*, the link decides *whose data "my own" resolves to* (full explanation in
    `WORKFLOW.md` §1 and §10).

**Optional — richer demo data:**

```bash
# Docker:
docker compose exec api python scripts/seed_sample_insurance.py

# Local dev:
uv run python scripts/seed_sample_insurance.py
```

Backfills existing patients with randomized insurance policies (mostly real/verified, some
intentionally invalid) so referral eligibility checks have real data to test both the
approve and deny paths.

**Optional — a full curated demo roster (10 doctors, 10 patients, 2 care coordinators):**

`scripts/reset_demo_data.py` is a **destructive** one-time script — it wipes every data table
(users, patients, doctors, referrals, appointments, everything except the schema and
roles/permissions reference tables) and replaces it with exactly 1 admin, 2 care coordinators,
10 doctors, and 10 patients, all sharing one password. Run it deliberately, not as part of normal
startup:

```bash
# Docker (needs an interactive terminal for the "yes" confirmation prompt):
docker compose exec api python scripts/reset_demo_data.py

# Local dev:
uv run python scripts/reset_demo_data.py
```

It writes `LOGIN_CREDENTIALS.md` with every account's username/password. Under Docker that file
is written *inside the container*, not on your host — either view it in place or copy it out:

```bash
docker compose exec api cat LOGIN_CREDENTIALS.md
# or, to save it to the host:
docker compose cp api:/app/LOGIN_CREDENTIALS.md ./LOGIN_CREDENTIALS.md
```

---

## Optional: Enable the real LLM

The platform runs fully functional with **zero** API keys (deterministic rule-based fallbacks
everywhere an LLM would otherwise be used). To turn on real LLM reasoning:

1. Get a free API key from [Groq](https://console.groq.com/) (or any OpenAI-compatible endpoint).
2. In `.env`:
   ```
   LLM_ENABLED=true
   LLMGW_API_KEY=your_key_here
   ```
3. Restart (`docker compose up --build` / re-run `uvicorn`). No code changes needed.

---

## Running Tests

```bash
uv run pytest
```

113 async tests, no Docker/Postgres required (uses an in-memory SQLite database via
`httpx.ASGITransport`).

---

## Quick Sanity Checklist

1. `GET /health/ready` → `{"status":"healthy","database":true}`
2. Register + log in via `/app` → lands on the Patients page
3. Admin account → Admin panel is visible in the sidebar
4. Grant a `patient` role to a test account + link it to a Patient record → that account now sees
   exactly one row on the Patients page
5. As a `pcp`/`care_coordinator` account, submit a referral (Referrals → "+ Request a Referral")
   → status starts at `submitted` and progresses automatically (watch the live status indicator on
   the referral detail page)
