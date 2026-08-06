import json
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from app.services.audit import log_action

# Keys that never belong in an audit log line, whatever tool they're passed to.
_REDACTED_ARG_KEYS = {"insurance_policy_number", "policy_number", "ssn"}


def _parse_tool_content(content: Any) -> Any:
    """A LangChain `BaseTool.ainvoke(plain_args_dict)` call (no `tool_call_id`)
    returns just the `content` half of the underlying MCP result — a list of
    LangChain content blocks (`[{"type": "text", "text": "<json>"}]`), not a
    parsed dict; `fastapi_mcp`'s tool responses are always a single such text
    block containing the JSON-serialized FastAPI response body. Join and parse
    it back into the dict/list the caller actually wants.
    """
    if isinstance(content, list) and content and all(
        isinstance(block, dict) and block.get("type") == "text" for block in content
    ):
        text = "".join(block["text"] for block in content)
        return json.loads(text)
    return content


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
async def _invoke_tool(tool, args: dict):
    return await tool.ainvoke(args)


async def call_tool_audited(
    db: AsyncSession, *, referral_id: int, tool, args: dict, actor: str = "agent"
) -> Any:
    """Governance wrapper every node uses around an MCP tool call: drops
    unset-optional (`None`) args (fastapi_mcp forwards them as literal empty
    query params — e.g. `?insurance_plan_id=` — which FastAPI then rejects as
    invalid rather than treating as "not provided"), redacts sensitive args
    before they ever reach a log line, retries transient MCP failures, and
    writes an audit trail entry alongside the caller's own state change
    (rides the caller's transaction — see `log_action`)."""
    args = {k: v for k, v in args.items() if v is not None}
    redacted: Dict[str, Any] = {
        k: ("***" if k in _REDACTED_ARG_KEYS else v) for k, v in args.items()
    }
    content = await _invoke_tool(tool, args)
    result = _parse_tool_content(content)

    await log_action(
        db, actor_id=None, action=f"agent.tool_call:{tool.name}",
        resource_type="referral_request", resource_id=referral_id,
        details={"args": redacted, "actor": actor},
    )
    return result
