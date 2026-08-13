# Implementation Explanation

Quick plan
- One short script to explain the six ADRs, where they are implemented, why they matter, and a 2–3 minute demo path.

## Opening (20–30s)
 I’ll walk through how our platform implements six key architecture decisions and where to point to show each one live. Goals: clear boundaries, reliable orchestration, swappable integrations, and auditable side effects.

## ADR-001 — Modular monolith; mocked externals as sub-apps (40–50s)
- What: One FastAPI process hosts bounded-context packages; external systems are mounted as independent FastAPI apps.
- Where: `app/main.py` (the `app.mount("/mock/{system}", ...)` calls), and each mock at `mock_systems/*/main.py`.
- Why: Keeps domain code decoupled and makes replacing mocks with real services straightforward.
- Demo tip: `curl localhost:8000/mock/payer/mcp` and `curl localhost:8000/mcp` (or show `docker compose ps` + both endpoints responding).

## ADR-002 — Orchestration for workflow; choreography for fan-out (40–50s)
- What: A LangGraph `StateGraph` orchestrates the referral workflow; side-effects are choreographed via a transactional outbox and independent consumers.
- Where: `app/agents/graph.py` (graph and pause points), `app/models/outbox.py`, `app/events/*` (write/publish/broadcaster).
- Why: Centralized sequencing with modular, observable side-effects.
- Demo tip: Start a referral, watch SSE (`GET /requests/{id}/events`) update live, then open Timeline (`GET /requests/{id}/timeline`) to show the same durable events.

## ADR-003 — REST for CRUD, Outbox for side effects, MCP for integrations (40–50s)
- What: REST for CRUD, transactional Outbox for cross-module side effects, MCP for agent↔system tool calls.
- Where: `app/api/routes/*` (REST), `app/models/outbox.py` + `app/events/*` (outbox), `app/main.py` (MCP mount), `app/agents/mcp_clients.py`, `knowledge_base/main.py`.
- Why: Each protocol suits its coupling/consistency needs and keeps integration surfaces explicit.
- Demo tip: Show `GET /docs` then trigger an MCP tool call (or use the assistant) to reach the same action.

## ADR-004 — Stateless API layer; workflow state in Postgres (30–40s)
- What: FastAPI is stateless; LangGraph checkpointer persists graph state to Postgres so flows survive restarts and can resume anywhere.
- Where: `app/agents/checkpointer.py` and `app/api/routes/ai/referral_workflow.py` (thread_id usage).
- Why: Resilience and horizontal scalability.
- Demo tip: Pause at `awaiting_specialist_approval`, restart the API container, then resume to show state persistence.

## ADR-005 — Swappable LLM factory with deterministic fallback (30–40s)
- What: A single seam constructs LLM clients (`app/agents/llm.py::get_chat_model`). Missing keys or `llm_enabled=False` yields a deterministic `StubChatModel`.
- Where: `app/agents/llm.py` and its call sites (assistant, extraction, ranking).
- Why: Enables demos/tests without secrets and predictable fallback behavior.
- Demo tip: Blank `LLMGW_API_KEY` and show assistant or ranking output still works (deterministic).

## ADR-006 — DB-based RBAC (30–40s)
- What: Permissions are modeled in DB tables and checked via `require_permission("resource:action")` rather than hardcoded role strings.
- Where: `app/core/seed.py` (seeded permissions/roles) and `app/api/dependencies/auth.py` (permission check dependency).
- Why: Fine-grained, auditable access control adjustable at runtime.
- Demo tip: Revoke a permission in Admin or the DB and show the UI/endpoint becomes unavailable or returns 403 immediately.

## Closing & Demo path (25–30s)
Minimal live demo (2–3 minutes):
1. Submit a referral → watch SSE status updates (ADR-002).
2. Open Timeline → show durable outbox events (ADR-002/003).
3. Show `GET /docs` then run an MCP tool call hitting the same action (ADR-003).
4. Toggle LLM key off and re-run assistant or ranking to show fallback (ADR-005).
5. Revoke a permission and refresh the UI to show RBAC (ADR-006).

## Files to reference during the presentation
- `app/main.py`
- `mock_systems/*/main.py`
- `app/agents/graph.py`
- `app/models/outbox.py`
- `app/events/*`
- `app/agents/mcp_clients.py`
- `knowledge_base/main.py`
- `app/agents/checkpointer.py`
- `app/agents/llm.py`
- `app/core/seed.py`
- `app/api/dependencies/auth.py`

## Single-line takeaway
We built a modular, resilient platform where orchestration, integration, and security are explicit seams you can demo and replace—use the referenced files as direct evidence.
