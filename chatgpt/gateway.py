"""Dedicated stdio entry point; never changes the Codex/Claude MCP server."""

import asyncio
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from jsonschema import Draft202012Validator
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import InitializedNotification, ToolAnnotations

from policy import ALLOWED_TOOLS, DEFAULT_WRITE_FOLDERS, WRITE_TOOLS, VaultPolicy
from diagnostics import Diagnostics


def create_gateway(vault, diagnostics=None, *, write_folders=DEFAULT_WRITE_FOLDERS):
    from mcp_obsidian.server import tool_handlers
    from mcp_obsidian.request_diagnostics import failure_details, observe_request_failures

    diagnostics = diagnostics or Diagnostics()
    policy = VaultPolicy(Path(vault), write_folders=write_folders)
    write_scope = (
        ' Restricted to files under: '
        + ', '.join(f'{folder}/' for folder in sorted(policy.write_folders))
        + '; hidden paths and links are rejected.'
        if policy.write_folders else ' Writes are disabled by configuration.'
    )
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
        if name == 'obsidian_delete_file':
            tool.description = 'Delete one existing regular file. Directories cannot be deleted. Requires boolean confirm=true.'
        if name in WRITE_TOOLS:
            tool.description += write_scope
        tool.annotations = ToolAnnotations(
            readOnlyHint=name not in WRITE_TOOLS,
            destructiveHint=name in WRITE_TOOLS and name != 'obsidian_append_content',
            idempotentHint=name not in WRITE_TOOLS or name in {'obsidian_put_content', 'obsidian_delete_file'},
            openWorldHint=False,
        )
        catalog[name] = tool
    validators = {name: Draft202012Validator(tool.inputSchema) for name, tool in catalog.items()}

    async def initialized(notification):
        diagnostics.emit('mcp_initialized')

    app.notification_handlers[InitializedNotification] = initialized

    @app.list_tools()
    async def list_tools():
        return list(catalog.values())

    # Validate inside our handler so SDK dispatch and direct calls both log
    # rejected arguments without the SDK echoing their contents into errors.
    @app.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        fields = {'call_id': uuid4().hex, 'tool': name if name in catalog else 'unknown'}
        started = monotonic()
        api_error_count = 0

        def api_failed(details):
            nonlocal api_error_count
            api_error_count += 1
            diagnostics.emit('api_error', **fields, **details)

        diagnostics.emit('tool_started', **fields)
        stage = 'tool_not_allowed'
        try:
            with observe_request_failures(api_failed):
                if name not in catalog:
                    raise PermissionError('Tool is not enabled for ChatGPT')
                stage = 'invalid_arguments'
                if not validators[name].is_valid(arguments):
                    raise ValueError('Invalid tool arguments')
                stage = 'policy_rejected'
                prepared = policy.prepare(name, arguments)
                stage = 'backend'
                result = tool_handlers[name].run_tool(prepared)
        except Exception as error:
            details = failure_details(error) if stage == 'backend' else {'category': stage}
            diagnostics.emit('tool_failed', **fields, **details,
                             duration_ms=round((monotonic() - started) * 1000, 3),
                             api_error_count=api_error_count)
            raise
        diagnostics.emit('tool_completed', **fields,
                         outcome='completed_with_api_errors' if api_error_count else 'success',
                         duration_ms=round((monotonic() - started) * 1000, 3),
                         api_error_count=api_error_count)
        return result

    return app, list_tools, call_tool


async def main():
    diagnostics = Diagnostics()
    with diagnostics.capture_library_logs():
        diagnostics.emit('gateway_starting')
        stage = 'load_config'
        try:
            config = json.loads(Path(__file__).with_name('connection.json').read_text())
            env = config['env']
            stage = 'https_config'
            if env.get('OBSIDIAN_PROTOCOL') != 'https':
                raise ValueError('HTTPS is required')
            stage = 'ca_config'
            if not Path(env.get('REQUESTS_CA_BUNDLE', '')).is_file():
                raise ValueError('Trusted certificate file is required')
            stage = 'vault_config'
            vault = Path(config['vault'])
            settings = json.loads((vault / '.obsidian/plugins/obsidian-local-rest-api/data.json').read_text())
            stage = 'api_key_check'
            if settings.get('apiKey') != env.get('OBSIDIAN_API_KEY'):
                raise ValueError('Configured API key does not match the guarded Vault')
            os.environ.update(env)
            stage = 'tls_check'
            from mcp_obsidian.obsidian import Obsidian
            if Obsidian(env['OBSIDIAN_API_KEY']).verify_ssl is not True:
                raise ValueError('TLS verification must be enabled')
            stage = 'create_gateway'
            app, _, _ = create_gateway(
                vault, diagnostics=diagnostics,
                write_folders=config.get('write_folders', DEFAULT_WRITE_FOLDERS),
            )
            stage = 'stdio'
            async with stdio_server() as (read, write):
                # Ready to serve; this does not claim a completed MCP handshake.
                diagnostics.emit('gateway_ready')
                await app.run(read, write, app.create_initialization_options())
        except asyncio.CancelledError:
            diagnostics.emit('gateway_stopped', reason='cancelled')
            raise
        except Exception:
            diagnostics.emit('gateway_failed', stage=stage)
            # Startup exceptions may embed paths or configuration values.
            raise SystemExit(1) from None
        else:
            diagnostics.emit('gateway_stopped', reason='stream_closed')


if __name__ == '__main__':
    asyncio.run(main())
