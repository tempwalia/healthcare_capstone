from typing import Any, Dict, List

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.core.config import settings

ELIGIBILITY_SERVERS = {"payer": f"{settings.mock_base_url}/mock/payer/mcp"}
DIRECTORY_SERVERS = {"directory": f"{settings.mock_base_url}/mock/directory/mcp"}
SCHEDULING_SERVERS = {"scheduling": f"{settings.mock_base_url}/mock/scheduling/mcp"}
NOTIFICATION_SERVERS = {"notification": f"{settings.mock_base_url}/mock/notification/mcp"}


def build_mcp_client(servers: Dict[str, str]) -> MultiServerMCPClient:
    """servers: {name: url}. Each node builds a client scoped to only the
    servers it's allowed to call — least-privilege tool access, per ADR-006."""
    return MultiServerMCPClient(
        {name: {"url": url, "transport": "streamable_http"} for name, url in servers.items()}
    )


async def get_tools(servers: Dict[str, str], names: List[str]) -> Dict[str, Any]:
    """Build a client scoped to `servers`, fetch its tools once, and return
    the requested ones keyed by name.

    This is the one seam nodes call through to reach MCP tools — tests
    monkeypatch `app.agents.mcp_clients.get_tools` itself (module-attribute
    lookup at call time, same convention as
    `app.database.session.async_session`) to avoid a real MCP/HTTP round
    trip, so a node must call it as `mcp_clients.get_tools(...)`, never via
    `from app.agents.mcp_clients import get_tools`.
    """
    client = build_mcp_client(servers)
    tools = await client.get_tools()
    by_name = {tool.name: tool for tool in tools}
    return {name: by_name[name] for name in names}
