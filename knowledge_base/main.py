"""First-party MCP server for the local policy knowledge base (`nb/`).

Deliberately built on the `mcp` SDK's raw `FastMCP` (already a direct
dependency: `mcp<2.0.0`), not `fastapi_mcp` like every other MCP server in
this project — `fastapi_mcp.FastApiMCP.mount_http()` only ever converts REST
routes into tools (confirmed by reading `fastapi_mcp/server.py`), with no
resource or prompt registration path at all. This is the one MCP server in
the project that exposes all three MCP primitives — a tool, resources, and
prompt templates — closing that specific curriculum gap for real, not just
adding another tool-only server.

Not placed under `mock_systems/`: that package is explicitly for stand-ins
for external organizations (ADR-001) — this is first-party platform content,
so it's a sibling top-level package instead.

`stateless_http=True`: every call here is a one-shot tool/resource/prompt
fetch (matches how every other MCP tool in this app is already used — see
`app/agents/mcp_clients.py`), never a long-lived multi-turn MCP session, so
there's no reason to pay for session-id tracking.

Mounting note (see app/main.py): `streamable_http_app()`'s returned Starlette
app declares its own `lifespan` that starts `mcp.session_manager.run()` — the
task group `handle_request()` requires before it will serve *any* request,
stateless or not (confirmed in `mcp/server/streamable_http_manager.py`).
Starlette does not propagate a `Mount`ed sub-app's lifespan automatically
(the ASGI spec sends exactly one lifespan event, to the root app), so
`app/main.py`'s own lifespan explicitly enters `mcp.session_manager.run()`
alongside the existing Postgres checkpointer — mounting alone is not enough.
"""
from typing import List

from mcp.server.fastmcp import FastMCP

from knowledge_base import retrieval

mcp = FastMCP("policy-knowledge-base", stateless_http=True)


@mcp.tool()
def search_policy_knowledge_base(query: str, top_k: int = 5) -> List[dict]:
    """Search the local healthcare/insurance policy knowledge base (referral
    process, appointment approval, prior authorization, privacy notice, and
    each insurance plan's coverage/copay/network details) and return the
    best-matching documents ranked by relevance. Use this for any question
    about how referrals or appointments get approved, what a specific plan
    covers, or to compare insurance plans — when comparing, call this once
    per plan by name, then synthesize across the results."""
    return retrieval.search(query, top_k=top_k)


@mcp.resource("kb://policies")
def list_policy_documents() -> List[dict]:
    """Catalog of every document in the knowledge base (id, title, category),
    without full text — read a specific `kb://policies/{doc_id}` resource
    (using an id from this catalog) for the full document."""
    return retrieval.list_documents()


@mcp.resource("kb://policies/{doc_id}")
def read_policy_document(doc_id: str) -> str:
    """Full text of one knowledge-base document, addressed by the id shown in
    the `kb://policies` catalog (e.g. `policies/referral_process_guide.txt`)."""
    doc = retrieval.get_document(doc_id)
    if doc is None:
        return f"No knowledge-base document found with id '{doc_id}'."
    return doc["text"]


@mcp.prompt()
def explain_referral_process() -> str:
    """Ready-made prompt: explain what happens next for a referral using the
    platform's own stage-by-stage status language, not a generic description
    of how referrals work in general."""
    return (
        "Using search_policy_knowledge_base, look up the referral process guide and "
        "explain, in the platform's own stage-by-stage language (submitted, reviewing "
        "documents, verifying insurance coverage, selecting a specialist, booking the "
        "appointment, scheduled, completed), what happens next for this referral and "
        "who — if anyone — needs to act next."
    )


@mcp.prompt()
def compare_policies(policy_a: str, policy_b: str) -> str:
    """Ready-made prompt: compare two named insurance plans using only the
    knowledge base's actual documents, not general insurance knowledge."""
    return (
        f"Using search_policy_knowledge_base, look up '{policy_a}' and '{policy_b}' "
        f"separately, then compare them on copay, in-network doctor coverage, and "
        f"prior-authorization rules. State plainly which plan is cheaper and which "
        f"has broader network access — don't just restate both documents side by side."
    )
