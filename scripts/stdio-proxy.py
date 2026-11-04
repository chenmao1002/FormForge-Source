"""Official MCP stdio transport forwarding to the already running FormForge service."""
import asyncio
import base64
import logging
import os
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server


async def main():
    root = Path(__file__).resolve().parents[1]
    token = (root / ".local" / "api-token.txt").read_text(encoding="utf-8").strip()
    port = int(os.getenv("FORMFORGE_PORT", "17840"))
    language = os.getenv("FORMFORGE_MCP_LANGUAGE", "zh-CN").strip() or "zh-CN"
    if language.lower() not in {"zh", "zh-cn", "en", "en-us"}:
        raise ValueError("FORMFORGE_MCP_LANGUAGE must be zh-CN or en")
    # This endpoint is always loopback, including when the caller uses a system proxy.
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token, "Accept-Language": language}, timeout=30, trust_env=False) as client:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                upstream = await session.initialize()
                server = Server("FormForge stdio bridge", instructions=upstream.instructions)

                @server.list_tools()
                async def list_tools():
                    return (await session.list_tools()).tools

                @server.call_tool()
                async def call_tool(name, arguments):
                    return await session.call_tool(name, arguments)

                @server.list_resources()
                async def list_resources():
                    return (await session.list_resources()).resources

                @server.read_resource()
                async def read_resource(uri):
                    remote = await session.read_resource(uri)
                    return [ReadResourceContents(
                        content=item.text if hasattr(item, "text") else base64.b64decode(item.blob),
                        mime_type=item.mimeType, meta=item.meta,
                    ) for item in remote.contents]

                async with stdio_server() as (stdin, stdout):
                    await server.run(stdin, stdout, server.create_initialization_options())


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main())
