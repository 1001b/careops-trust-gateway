from __future__ import annotations

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError as exc:
        raise SystemExit(
            "Install the optional MCP dependency first: pip install -e '.[mcp]'"
        ) from exc

from careops.entities import resolve_provider
from careops.metrics import get_metric
from careops.policy import search_policy
from careops.semantic import metric_definition

mcp = _Server("careops-trust-gateway")


@mcp.tool(name="get_metric")
def get_metric_tool(metric: str, state: str = "TX", payer_network: str = "Aetna", period: str = "last_week") -> dict:
    """Return an authoritative governed metric result."""
    return get_metric(metric, state=state, payer_network=payer_network, period=period).as_dict()


@mcp.tool(name="explain_metric")
def explain_metric(metric: str) -> dict:
    """Return the governed semantic contract for a metric."""
    return metric_definition(metric)


@mcp.tool(name="search_policy")
def search_policy_tool(query: str, role: str = "analyst") -> list[dict]:
    """Retrieve only current policy content authorized for the caller role."""
    return search_policy(query, role=role)


@mcp.tool(name="resolve_entity")
def resolve_entity(entity_type: str, value: str) -> dict:
    """Resolve an enterprise entity with confidence and review signaling."""
    if entity_type != "provider":
        return {"matched": False, "reason": "demo_supports_provider_only"}
    return resolve_provider(value)


if __name__ == "__main__":
    mcp.run()
