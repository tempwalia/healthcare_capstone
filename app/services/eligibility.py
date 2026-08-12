"""Insurance eligibility checking — extracted from
`app.agents.nodes.eligibility` so the audited MCP tool call itself is a
plain, reusable function rather than something only reachable through the
referral LangGraph. `eligibility_node` wraps this with its referral-specific
side effects (status mutation, notifications); any other call site (a
coordinator-triggered recheck, a future direct-appointment eligibility
check) can call this directly with or without a referral in scope.
"""
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import mcp_clients
from app.agents.audit import call_tool_audited


async def check_eligibility(
    db: AsyncSession, *, referral_id: Optional[int], insurance_policy_number: str, procedure_code: str,
) -> Dict[str, Any]:
    tools = await mcp_clients.get_tools(mcp_clients.ELIGIBILITY_SERVERS, ["check_eligibility"])
    return await call_tool_audited(
        db, referral_id=referral_id, tool=tools["check_eligibility"],
        args={
            "insurance_policy_number": insurance_policy_number or "",
            "procedure_code": procedure_code or "UNKNOWN",
        },
    )
