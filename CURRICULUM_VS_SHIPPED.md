# Curriculum vs. Shipped Platform — Gap Analysis

Every topic from the 15-day "Architect Academy" training track and the four foundation modules, checked against the code actually present in this repository today (not against `CAPSTONE_IMPLEMENTATION_GUIDE.md`'s own claims, though that doc is cited where relevant as a planning artifact).

## Status legend

| Tag | Meaning |
|---|---|
| ✅ Shipped | Implemented in running code, verified with a file:line citation |
| 🟡 Partial | Real but incomplete, stubbed, or narrower than the curriculum topic |
| 📄 Documented, not built | Covered in the guide/README/ADRs but no corresponding code |
| ❌ Not shipped | No evidence anywhere in the repo |
| 🅿️ Process-only | Describes *how you were supposed to work* (tooling, ceremonies), not a platform feature — not a real gap |

---

## Part A — 15-Day Training Track

### Section 1: Agentic AI Engineering Foundation

**Day 1 — Orientation, FDE role, use-case identification, LangGraph fundamentals**

| Topic | Status | Evidence |
|---|---|---|
| Orientation to FDE role | 🅿️ | Training experience, not a shippable feature |
| Use-case identification methodology | 📄 | `CAPSTONE_IMPLEMENTATION_GUIDE.md` §3.1 business capability map |
| LangChain vs LangGraph | ✅ | `app/agents/graph.py` (LangGraph `StateGraph`) built on top of LangChain chat models (`app/agents/llm.py`) |
| Nodes, edges, state, graph flow | ✅ | `app/agents/graph.py::build_graph` — 9 nodes, conditional edges; state in `app/agents/state.py::ReferralState` (`TypedDict`) |

**Day 2 — Agent loops, conditional routing, tool calling, state management**

| Topic | Status | Evidence |
|---|---|---|
| Agent loops | ✅ | `create_react_agent` loop in `app/agents/assistant_graph.py` (chat assistant) |
| Conditional routing | ✅ | `route_after_intake`, `route_after_eligibility` (`graph.py:15-20`) |
| Tool calling | ✅ | `call_tool_audited` invokes MCP tools (`check_eligibility`, `search_providers`, `get_availability`/`book_slot`, `send_notification`) from every node |
| State management | ✅ | `ReferralState` + `AsyncPostgresSaver` checkpointer (`app/agents/checkpointer.py`) — survives process restarts |

**Day 3 — Orchestration, prompt patterns, HITL, multi-agent**

| Topic | Status | Evidence |
|---|---|---|
| Orchestration | ✅ | Single `StateGraph` drives intake → eligibility → specialist → scheduling → summarize → notify |
| Prompt patterns | ✅ | `INTAKE_EXTRACTION_PROMPT`, `SUMMARY_PROMPT`, `COMPLETION_SUMMARY_PROMPT`, `_RANKING_PROMPT`, `ROLE_SYSTEM_PROMPTS`, all with Pydantic structured output |
| Human-in-the-loop (HITL) | ✅ | `await_specialist_approval` node calls `langgraph.types.interrupt()`; resumed via `POST /referral-workflow/{id}/resume` with `Command(resume=...)`, gated by `referral:approve` permission |
| Multi-agent | 🟡 | Two separate graphs exist (referral workflow + chat assistant `create_react_agent`), but there's no agent-to-agent handoff/supervisor pattern connecting them — "multi-agent" in the taught sense (collaborating agents) isn't demonstrated |

**Day 4 — Agentic design patterns**

| Topic | Status | Evidence |
|---|---|---|
| Skills | 🟡 | Role-scoped MCP tool allowlists (`BASE_REFERRAL_TOOLS` etc. in `assistant_graph.py:24-47`) act as a coarse skills boundary; no explicit skill abstraction |
| Memory | 🟡 | Session-scoped conversation memory via Postgres checkpointer (`thread_id = chat-{user_id}-{session_id}`); no long-term/vector memory |
| Planning | ❌ | No planner node or LLM-driven planning step anywhere; all routing is deterministic conditional edges |
| Agent-to-Agent (A2A) communication | ❌ | No evidence found; the two graphs don't talk to each other |
| Agent lifecycle | ✅ | Graph compiled once at FastAPI lifespan startup (`app/main.py:44-55`), checkpointer persists state across restarts |

**Day 5 — RAG via LangChain/LangGraph + LangSmith**

| Topic | Status | Evidence |
|---|---|---|
| RAG implementation | 🟡 | `tests/test_knowledge_base.py` covers BM25 retrieval over `nb/` policy docs — a real lexical retrieval utility with test coverage, but it is BM25 (not embedding-based), and the assistant's own tool list (`assistant_graph.py`) has no knowledge-base retrieval tool — not confirmed wired into the live chat flow |
| LangSmith tracing/eval | ❌ | No LangSmith dependency or usage found anywhere |
| Deliverable: "Debuggable RAG chatbot" | ❌ | The chat assistant exists (§Day 6+ below) but isn't a retrieval-augmented, traced chatbot as taught |

### Section 2: GitHub Copilot for AI-Assisted SDLC

**Day 6 — Copilot setup, repo understanding, modes, feature dev, documentation**

All 🅿️ **Process-only** — these describe how the developer used Copilot during the build, not a platform feature, and can't be verified from shipped code. The unusually thorough doc set (`README.md`, `CAPSTONE_IMPLEMENTATION_GUIDE.md`, `WORKFLOW.md`, `RUNBOOK.md`) is consistent with heavy AI-assisted documentation, but that's circumstantial, not evidence of tooling used.

**Day 7 — Refactoring, testing, debugging, RCA, code review**

| Topic | Status | Evidence |
|---|---|---|
| Unit / API testing | ✅ | 22 files, 151 `test_*` functions, `httpx.ASGITransport` + in-memory SQLite (`tests/conftest.py`) |
| BDD | ❌ | No BDD framework (`pytest-bdd`, `behave`) found — tests are plain pytest |
| Test data generation | ✅ | `scripts/reset_demo_data.py`; `conftest.py` fixtures/fakes |
| Debugging / RCA | 🟡 | Process itself is 🅿️, but there's a concrete artifact: migration `4dabbc27a591` fixes a Postgres-only date-column bug caught by running tests against real Postgres (documented in README) |
| Code review | 🅿️ | No CI review gate found; unverifiable from repo state |
| Deliverable: "Test suite & bug triage" | ✅ | Real 151-test suite; "triage" reflected in the guide's Known Gaps → Resolution Matrix |

**Day 8 — GitHub workflows, CI/CD, cloud deployment**

| Topic | Status | Evidence |
|---|---|---|
| GitHub PR workflow | 🅿️ | Unverifiable from repo state alone |
| CI/CD automation, GitHub Actions | ❌ | No `.github/workflows/` directory anywhere in the repo (confirmed by glob) |
| Cloud deployment | 🟡 | Dockerized deployment (`Dockerfile`, `docker-compose.yml`) works end-to-end (README documents a verified fresh-checkout rebuild); no actual cloud deploy — Kubernetes only exists as an appendix sketch in the guide, not real manifests |

### Section 3: MCP-Based Agent Engineering & Integration

**Day 9 — MCP fundamentals + dev setup**

| Topic | Status | Evidence |
|---|---|---|
| MCP fundamentals | ✅ | Platform's own API mounted as an MCP server (`FastApiMCP(app); mcp.mount_http()`, `app/main.py:112-113`) **and** 5 separate mock external systems each run their own MCP server (`mock_systems/{ehr,payer,provider_directory,scheduling,notification}_mock/main.py`) |
| Python SDK for MCP | ✅ | `langchain_mcp_adapters.MultiServerMCPClient` used throughout (`app/agents/mcp_clients.py`) |
| FastMCP | 🟡 | Project uses `fastapi_mcp.FastApiMCP` (wraps an existing FastAPI app as MCP) rather than the standalone `FastMCP` SDK taught — related but a different library, worth noting as a naming/library distinction |
| MCP Inspector | 🅿️ | A dev-only tool; wouldn't appear in shipped code either way |
| Deliverable: "running MCP server" | ✅ | 6 MCP servers total (1 platform + 5 mocks) |

**Day 10 — MCP tool development**

| Topic | Status | Evidence |
|---|---|---|
| Tool schema design | ✅ | Explicit `operation_id`s set on FastAPI routes for LLM-friendly tool names (`get_referral`, `list_referrals`, `check_eligibility`, `search_providers`, etc.) |
| Parameters definition | ✅ | e.g. `search_providers(specialty, location, insurance_plan_id)` |
| Dynamic tools | 🟡 | Role-based tool allowlisting dynamically restricts which tools are exposed per chat session (`assistant_graph.py`), but tools themselves are static, not generated |
| Deliverable: "MCP tool APIs" | ✅ | `check_eligibility`, `search_providers`, `get_availability`, `book_slot`, `send_notification`, plus the full platform API surface |

**Day 11 — MCP resources + prompt templates**

| Topic | Status | Evidence |
|---|---|---|
| MCP resources | ❌ | No evidence of MCP "resources" as a distinct concept — `FastApiMCP` exposes everything as tools |
| Prompt templates | ✅ | See Day 3 prompt list |
| Structured output | ✅ | `ExtractedCodes`, `_RankedCandidates` Pydantic models via `llm.with_structured_output(...)` |
| Deliverable: "Resource-enabled agent" | ❌ | Not built in the literal "MCP resources" sense; the prompt-template/structured-output half is shipped |

**Day 12 — MCP client + tool invocation**

| Topic | Status | Evidence |
|---|---|---|
| MCP client implementation | ✅ | `app/agents/mcp_clients.py` — least-privilege per-node scoping (`ELIGIBILITY_SERVERS`, `DIRECTORY_SERVERS`, `SCHEDULING_SERVERS`, `NOTIFICATION_SERVERS`) |
| Tool discovery | ✅ | `get_tools(servers, names)` fetches only named tools per client |
| Retry logic | ✅ | `call_tool_audited` uses `tenacity` (3 attempts, exponential backoff) |
| Failure handling | ✅ | Same wrapper, plus deterministic fallback (regex/rule-based/template) at every LLM call site |
| Deliverable: "End-to-end MCP agent" | ✅ | The entire referral workflow is exactly this |

### Section 4: Governance, Enterprise Integration & FDE Qualification

**Day 13 — MCP transports, security, testing**

| Topic | Status | Evidence |
|---|---|---|
| HTTP transport | ✅ | `streamable_http` (assistant client), `mount_http()` (servers) |
| SSE / WebSocket transports | ❌ | No evidence of either |
| OAuth | ❌ | MCP calls are authenticated by forwarding the caller's JWT bearer token (`Authorization` header), not an OAuth flow |
| JWT | ✅ | `app/auth/jwt_handler.py` (HS256), forwarded through to MCP tool calls |
| Logging | ✅ | `AuditLog` row written for every tool call (`call_tool_audited`) |
| Tracing | ❌ | No LangSmith or other tracing found |
| Validation | ✅ | Pydantic schemas validate tool I/O throughout |
| Deliverable: "Secure MCP agent" | 🟡 | Strong on JWT-forwarded auth, audit logging, sensitive-field redaction (`insurance_policy_number`, `ssn`), and tested role-based tool allowlists (`tests/test_assistant.py::test_patient_tool_allowlist_excludes_unscoped_routes`); missing OAuth and tracing |

**Day 14 — Enterprise integration**

| Topic | Status | Evidence |
|---|---|---|
| Database integration | ✅ | Postgres via async SQLAlchemy, 11 Alembic migrations |
| API integration | ✅ | 5 mocked external systems integrated via MCP (EHR, payer, provider directory, scheduling, notification) |
| GitHub tools | ❌ | No evidence |
| Azure OpenAI | ❌ | Uses Groq (OpenAI-compatible endpoint) via `ChatOpenAI`, not Azure OpenAI (`app/agents/llm.py:19-38`) |
| GCP Vertex AI | ❌ | No evidence |

**Day 15 — Capstone (Copilot + MCP + Multi-Agent + Cloud Deployment + Demo)**

| Component | Status |
|---|---|
| MCP | ✅ strong |
| Deployment | 🟡 Dockerized, not cloud |
| Multi-agent | 🟡 two independent graphs, not collaborative |
| Copilot | 🅿️ unverifiable |
| Demo | ✅ README documents a verified `docker compose up --build` + fresh-checkout rebuild test |

---

## Part B — Four Foundation Modules

### Module 1 — Requirements Analysis & Problem Framing

| Topic | Status | Evidence |
|---|---|---|
| Problem identification & decomposition | 📄 | `CAPSTONE_IMPLEMENTATION_GUIDE.md` deliverables traceability matrix |
| Stakeholder mapping & value streams | 📄 | `WORKFLOW.md` per-role walkthroughs (Patient, PCP, Specialist, Care Coordinator, Payer Admin, Admin) |
| Outcomes, KPIs & success criteria | ✅ | `app/api/routes/analytics.py` — `avg_time_to_schedule_hours`, `delay_risk_referrals`, `eligibility_denial_rate`, `top_specialties_requested` are real, queried metrics, not just documented ones |
| Functional decomposition (epics → features) | 📄 | Guide's 13 Implementation Phases |
| NFR categories & trade-offs | 🟡 | Documented in guide §3.2; some NFRs are actually enforced (rate limiting on login = availability/security NFR; RBAC = security NFR) rather than just written down |

### Module 2 — Architecture Principles & Design

| Topic | Status | Evidence |
|---|---|---|
| HLD, architecture views, ADRs | 📄 | Guide §3.3 (ADR-001–ADR-006+), §3.5 HLD/domain decomposition |
| API-first design | ✅ | Full REST API with `operation_id`s, sample req/resp documented in guide §3.6 |
| Microservices / bounded contexts | 🟡 | Core platform is a modular monolith (single FastAPI app, service-file boundaries), not deployed as microservices; the 5 mock external systems *are* separately deployable FastAPI services — a partial, deliberate split |
| Async / event-driven architecture | ✅ | `OutboxEvent` model + `outbox_events` table, `BackgroundTask`-triggered workflow runs |
| Monolith vs microservices vs modular monolith | 🟡 | Shipped reality: modular monolith + separate mock microservices, explicitly discussed as a trade-off in the guide |
| 12-factor app | 🟡 | Env-based config (`.env`) present; no systematic 12-factor audit found |
| Resilience & fault tolerance | ✅ | `tenacity` retries, deterministic fallback at every LLM call site, Docker healthchecks (`pg_isready`, `/health/ready`) |
| Stateless vs stateful design | ✅ | LangGraph workflow state is deliberately stateful (Postgres checkpointer) — an explicit, documented ADR trade-off |
| Observability by design | 🟡 | `AuditLog` + structured logging exist; no metrics/tracing stack (Prometheus/Grafana/OpenTelemetry) |
| Gateway / service mesh / integration hub | ❌ | Not shipped |
| Sync vs async integration | ✅ | Sync REST/MCP calls to mocks + async outbox events, both present |
| Security & identity (IAM, Zero Trust) | 🟡 | JWT + RBAC shipped; no Zero Trust or IAM federation |
| Reference architecture / deployment views | 📄 | Guide + README mermaid diagrams (system architecture, referral-lifecycle state diagram, auth sequence, event/outbox sequence) |

### Module 3 — AI Capabilities & Architecture Solutioning

| Topic | Status | Evidence |
|---|---|---|
| LLM/Copilot for requirement structuring | 🅿️ | Unverifiable from shipped code |
| RAG for contextual requirement mining | ❌ | Not shipped |
| AI-assisted gap analysis | 📄 | Guide's own "Known Gaps → Resolution Matrix" and "AI Opportunities Coverage Matrix" are exactly this practice, applied to itself |
| Multi-agent patterns (orchestrator/planner/executor) | 🟡 | The referral graph is an orchestrator over nodes (deterministic), but there's no distinct planner/executor role split |
| Ethical, responsible, governed AI design | ✅ | Audit logging of every tool call, PII redaction (ssn/policy numbers) in audit records, HITL approval gate before booking, tested role-scoped tool access preventing cross-patient data exposure |

### Module 4 — Decision Making, Governance & Compliance

| Topic | Status | Evidence |
|---|---|---|
| ADRs (structure, alternatives, traceability) | 📄 | Guide §3.3, ADR-001 through ADR-006+ |
| Governance & policy frameworks | 🟡 | RBAC-based governance shipped; no policy-as-code engine |
| IAM: authentication, authorization | ✅ | JWT auth (`app/core/security.py`, `app/auth/jwt_handler.py`), `require_permission` RBAC dependency, refresh-token rotation with reuse detection (`app/auth/refresh_tokens.py`) |
| Zero Trust | ❌ | Not shipped |
| RBAC | ✅ | Roles/permissions tables + `require_permission`, plus row-level visibility filters (`record_scope.py`, `referral_scope.py`) — regression-tested in `tests/test_record_scope.py` |
| ABAC | ❌ | Permission model is role-based only |
| PII/PHI awareness | 🟡 | Sensitive fields redacted in audit logs (`insurance_policy_number`, `ssn`); soft-delete preserves audit trail; no explicit encryption-at-rest or consent/retention policy found |
| Audit, logging & encryption | 🟡 | `AuditLog` ✅; passwords bcrypt-hashed ✅; no evidence of at-rest encryption for PHI fields specifically |
| DevOps & CI/CD alignment | ❌ | No CI pipeline (ties back to Day 8) |
| AI-assisted decisioning & governance | 🟡 | Audit/redaction/HITL patterns are real governance over AI decisions; no automated compliance-check tooling |

---

## Headline Findings

- **MCP is the standout.** The platform exposes its own REST API as an MCP server *and* wraps all 5 mocked external healthcare systems as separate MCP servers, with per-node least-privilege client scoping, retry/audit wrapping, and role-based tool allowlisting for the chat assistant. This covers Days 9–12 almost completely.
- **Core LangGraph fundamentals are solid.** State, conditional routing, human-in-the-loop interrupt/resume, and checkpointed persistence across restarts are all real, working code — Days 1–3 are well covered.
- **5 of 7 problem-statement "AI Opportunities" are fully implemented**: code extraction, specialist recommendation, referral-history summarization, missing-document detection, and the conversational assistant. The remaining two are honestly self-scoped as stretch work in the guide: delay prediction (#5) is a reactive per-referral signal plus an analytics metric, not a scheduled sweep with escalation; alternate-provider suggestion on wait-time overrun (#7) doesn't exist.
- **Architecture-track deliverables are real planning artifacts, not code-enforced ones.** Business capability map, ADRs, HLD, context diagram, sequence diagrams, and event catalogue all exist in `CAPSTONE_IMPLEMENTATION_GUIDE.md`, but nothing generates or validates them from the running system.
- **Clearest gaps against the curriculum**: no CI/CD (`.github/workflows` doesn't exist), no LangSmith tracing, no production-wired RAG pipeline (Day 5's "debuggable RAG chatbot" — only an isolated, test-covered BM25 utility), no OAuth for MCP (JWT-bearer forwarding instead), no Azure OpenAI/GCP Vertex AI (Groq instead), no Kubernetes/service mesh (Docker Compose only), no ABAC or policy-as-code (RBAC only).
- **Day 4's agentic design patterns are the weakest-covered topic overall**: no explicit planning component, no agent-to-agent communication, and "multi-agent" in the shipped system means two independent graphs (referral workflow + chat assistant) rather than a demonstrated collaborative pattern.
- **Governance/security punches above what the gap list above suggests.** Audit logging on every tool call, PII/PHI field redaction, a mandatory human approval gate before any booking, and a tool-allowlist that's actually regression-tested to prevent the assistant from leaking unscoped patient data (`tests/test_assistant.py`, `tests/test_record_scope.py`) — this is tested code, not just a documented intention.
