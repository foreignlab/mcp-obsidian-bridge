"""Dedicated stdio entry point; never changes the Codex/Claude MCP server."""

import asyncio
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ToolAnnotations

from policy import ALLOWED_TOOLS, WRITE_TOOLS, VaultPolicy


def create_gateway(vault):
    from mcp_obsidian.server import tool_handlers

    policy = VaultPolicy(Path(vault))
    app = Server('obsidian-chatgpt-restricted')
    catalog = {}
    for name in sorted(ALLOWED_TOOLS):
        tool = tool_handlers[name].get_tool_description().model_copy(deep=True)
        tool.inputSchema['additionalProperties'] = False
        properties = tool.inputSchema.get('properties', {})
        for key, schema in properties.items():
            if schema.get('type') == 'string':
                schema['maxLength'] = 1_000_000 if key == 'content' else 4096
        if 'context_length' in properties:
            properties['context_length'].update(minimum=0, maximum=1000)
        if 'filepaths' in properties:
            properties['filepaths'].update(minItems=1, maxItems=50)
        if 'days' in properties:
            properties['days']['maximum'] = 3650
        if name in WRITE_TOOLS:
            tool.description += ' Restricted to files under 000_Inbox/ and 020_Projects/; hidden paths and links are rejected.'
        if name == 'obsidian_delete_file':
            tool.description = 'Delete one existing regular file under 000_Inbox/ or 020_Projects/. Directories cannot be deleted. Requires boolean confirm=true.'
        tool.annotations = ToolAnnotations(
            readOnlyHint=name not in WRITE_TOOLS,
            destructiveHint=name in WRITE_TOOLS and name != 'obsidian_append_content',
            idempotentHint=name not in WRITE_TOOLS or name in {'obsidian_put_content', 'obsidian_delete_file'},
            openWorldHint=False,
        )
        catalog[name] = tool
    validators = {name: Draft202012Validator(tool.inputSchema) for name, tool in catalog.items()}

    @app.list_tools()
    async def list_tools():
        return list(catalog.values())

    @app.call_tool()
    async def call_tool(name, arguments):
        if name not in catalog:
            raise PermissionError('Tool is not enabled for ChatGPT')
        # Validate even when called directly rather than through the SDK dispatcher.
        if not validators[name].is_valid(arguments):
            raise ValueError('Invalid tool arguments')
        prepared = policy.prepare(name, arguments)
        return tool_handlers[name].run_tool(prepared)

    return app, list_tools, call_tool


async def main():
    config = json.loads(Path(__file__).with_name('connection.json').read_text())
    env = config['env']
    if env.get('OBSIDIAN_PROTOCOL') != 'https':
        raise ValueError('HTTPS is required')
    if not Path(env.get('REQUESTS_CA_BUNDLE', '')).is_file():
        raise ValueError('Trusted certificate file is required')
    vault = Path(config['vault'])
    settings = json.loads((vault / '.obsidian/plugins/obsidian-local-rest-api/data.json').read_text())
    if settings.get('apiKey') != env.get('OBSIDIAN_API_KEY'):
        raise ValueError('Configured API key does not match the guarded Vault')
    os.environ.update(env)
    from mcp_obsidian.obsidian import Obsidian
    if Obsidian(env['OBSIDIAN_API_KEY']).verify_ssl is not True:
        raise ValueError('TLS verification must be enabled')
    app, _, _ = create_gateway(vault)
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
