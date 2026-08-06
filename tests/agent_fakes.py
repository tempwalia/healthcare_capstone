"""Test double for the MCP tool-calling seam (`app.agents.mcp_clients.get_tools`).

Pytest can't reach the mocked external systems over real MCP HTTP (no live
socket under httpx's ASGITransport), so this fakes just that one seam while
still round-tripping through the *same* mock FastAPI apps used in production
(via ASGITransport, in-process, no real socket) — exercising the real
request/response contracts, not a hand-rolled stand-in.
"""
import json
from dataclasses import dataclass
from typing import Any, Dict, List

from httpx import ASGITransport, AsyncClient

from mock_systems.notification_mock.main import app as notification_app
from mock_systems.payer_mock.main import app as payer_app
from mock_systems.provider_directory_mock.main import app as directory_app
from mock_systems.scheduling_mock.main import app as scheduling_app


@dataclass
class _ToolSpec:
    app: Any
    method: str
    path: str
    mode: str  # "json" body or query "params"


_TOOL_REGISTRY: Dict[str, _ToolSpec] = {
    "check_eligibility": _ToolSpec(payer_app, "POST", "/eligibility/check", "json"),
    "search_providers": _ToolSpec(directory_app, "GET", "/providers/search", "params"),
    "get_availability": _ToolSpec(scheduling_app, "GET", "/availability", "params"),
    "book_slot": _ToolSpec(scheduling_app, "POST", "/slots/book", "json"),
    "send_notification": _ToolSpec(notification_app, "POST", "/notifications/send", "json"),
}


class FakeMCPTool:
    """Stands in for a `langchain_mcp_adapters` tool: `.name` + async
    `.ainvoke(args)` returning the exact content-block shape a real MCP tool
    call produces (`[{"type": "text", "text": "<json>"}]`), so
    `app.agents.audit.call_tool_audited`'s parsing is exercised faithfully."""

    def __init__(self, name: str):
        self.name = name
        self._spec = _TOOL_REGISTRY[name]

    async def ainvoke(self, args: dict) -> List[Dict[str, str]]:
        transport = ASGITransport(app=self._spec.app)
        async with AsyncClient(transport=transport, base_url="http://mock") as client:
            if self._spec.mode == "json":
                response = await client.request(self._spec.method, self._spec.path, json=args)
            else:
                response = await client.request(self._spec.method, self._spec.path, params=args)
        response.raise_for_status()
        return [{"type": "text", "text": json.dumps(response.json())}]


async def fake_get_tools(servers: dict, names: List[str]) -> Dict[str, FakeMCPTool]:
    return {name: FakeMCPTool(name) for name in names}
