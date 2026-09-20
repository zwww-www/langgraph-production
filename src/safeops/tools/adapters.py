from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from safeops.config import Settings
from safeops.tools.registry import Handler, ToolRegistry


def http_handler(client: httpx.AsyncClient, url: str) -> Handler:
    async def call(args: dict[str, Any], key: str, customer: str) -> dict[str, Any]:
        response = await client.post(
            url,
            headers={"Idempotency-Key": key},
            json={"args": args, "customer_id": customer, "idempotency_key": key},
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("external receipt must be an object")
        return result

    return call


def mcp_handler(url: str, name: str) -> Handler:
    async def call(args: dict[str, Any], key: str, customer: str) -> dict[str, Any]:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    name, arguments={**args, "customer_id": customer, "idempotency_key": key}
                )
                if result.isError or not isinstance(result.structuredContent, dict):
                    raise ValueError("MCP returned no confirmed structured receipt")
                return result.structuredContent

    return call


def bind_external(settings: Settings, registry: ToolRegistry, client: httpx.AsyncClient) -> None:
    # Only statically registered, schema-checked tools can be rebound. No discovery auto-allow.
    for name, binding in settings.tool_bindings.items():
        registry.require(name)
        if binding["transport"] == "http":
            handler = http_handler(client, binding["url"])
        elif binding["transport"] == "mcp":
            handler = mcp_handler(settings.mcp_servers[binding["server"]], binding["tool"])
        else:
            raise ValueError("unsupported tool transport")
        registry.bind(name, handler)
