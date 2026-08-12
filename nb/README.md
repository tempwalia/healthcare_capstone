# Knowledge base (`nb/`)

Plain-language policy and process documents for the platform's knowledge-base retrieval tool
(`knowledge_base/retrieval.py`), exposed to the conversational assistant via the
`search_policy_knowledge_base` MCP tool, and browsable directly as MCP resources
(`kb://policies`, `kb://policies/{doc_id}`) by any MCP client. Same role in this repo that
`sample_documents/` plays for referral-document intake — real, realistic content, not lorem ipsum,
kept consistent with the actual mock data the app runs against.

Every document here is one retrievable unit (no sub-chunking) — each is already short and
single-topic, the same granularity `sample_documents/` uses.

## What's here

| File | Covers |
|---|---|
| `policies/referral_process_guide.txt` | How a referral moves from submission to a completed consult — every status a patient/provider will see on the dashboard, in plain language |
| `policies/appointment_approval_policy.txt` | How a recommended specialist gets approved and an appointment gets booked, cancellation expectations, what "wait time" means here |
| `policies/prior_authorization_policy.txt` | When a referral needs prior authorization before it can proceed, and what "prior auth required" means for the patient |
| `policies/hipaa_privacy_notice.txt` | Plain-language notice of how PHI is used, shared, and protected on this platform |
| `insurance/acme_ppo_gold.txt` | Acme PPO Gold plan (policy numbers `ACME-991123`) |
| `insurance/acme_hmo_silver.txt` | Acme HMO Silver plan (policy numbers `ACME-778890`) |
| `insurance/horizon_blue_ppo.txt` | Horizon Blue PPO plan (policy numbers `HORIZON-556677`) |
| `insurance/unitedcare_basic_hmo.txt` | UnitedCare Basic HMO plan (policy numbers `UNITEDCARE-334455`) |

The insurance documents describe exactly the same plans, copays, and network doctor IDs modeled in
`mock_systems/payer_mock/main.py`'s `PLANS` dict — and the prior-authorization document describes
that same mock's `PRIOR_AUTH_PREFIXES = ("M51", "M54")` toy rule — so the knowledge base never
contradicts what `check_eligibility` actually returns. If the mock payer's plan data changes, update
the matching document here in the same commit.

## How it's used

`knowledge_base/main.py` is a standalone MCP server (built with the `mcp` SDK's `FastMCP`, not
`fastapi_mcp` — this is the one server in the project that exposes real MCP tools, resources, *and*
prompt templates, not just tools) mounted at `/kb` in `app/main.py`. `knowledge_base/retrieval.py`
indexes every file under this folder with BM25 (lexical/keyword ranking — no embeddings, no API key
required, consistent with this project's zero-key-required design) at process startup, and re-scans
on a file-mtime change so editing a document here takes effect without a restart.
