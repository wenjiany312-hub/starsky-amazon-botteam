"""Call an enabled MCP (Codex config.toml or Claude-style JSON registry) from any shell-capable runtime.

Uses the installed MCP SDK for both stdio and streamable HTTP. No credential
copying, model gating, or automatic delegation. Tool calls retain the caller's
existing authorization requirements. Pass JSON as @file for portable quoting.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
import logging
import os
import re
from pathlib import Path
import sys
import tomllib


class BridgeError(RuntimeError):
    pass


def safe_diagnostics(exc, *, phase='unknown'):
    """Only allow structural error evidence, never exception text or request URLs."""
    leaves=[]
    def visit(error):
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:visit(child)
        else:leaves.append(error)
    visit(exc)
    codes=sorted({getattr(getattr(e,'response',None),'status_code',None) for e in leaves}
                 & {400,401,403,404,408,409,429,500,502,503,504})
    names=sorted({type(e).__name__ for e in leaves})
    if any(c in {401,403} for c in codes) or any(isinstance(e,PermissionError) for e in leaves):category='authorization_denied'
    elif 429 in codes:category='rate_limited'
    elif any(isinstance(e,(TimeoutError,asyncio.TimeoutError)) or 'Timeout' in type(e).__name__ for e in leaves):category='timeout'
    elif isinstance(exc,BridgeError):category=str(exc).split(':',1)[0]
    else:category='transport'
    return {'category':category,'phase':phase,'leaf_types':names,'http_status':codes}


class TransportFailure(BridgeError):
    def __init__(self, diagnostics):
        super().__init__('connection_or_tool_failed')
        self.diagnostics=diagnostics


def env_value(name):
    value = os.environ.get(name)
    if value:
        return value
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            pass
    return None


def _expand(value):
    """Expand ${VAR} from the environment; a missing variable is a missing credential."""
    def replace(match):
        found = env_value(match.group(1))
        if not found:
            raise BridgeError('missing_credential: ' + match.group(1))
        return found
    return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', replace, value)


def load_json_server(config_path, name):
    """Claude Code style registry: {"mcpServers": {name: {type,url,headers} | {command,args,env}}}."""
    try:
        servers = json.loads(Path(config_path).read_text(encoding='utf-8-sig')).get('mcpServers', {})
    except (OSError, ValueError) as exc:
        raise BridgeError('config_unreadable: 无法读取 MCP 配置') from exc
    aliases = ('sif', 'sif_mcp', 'sif-mcp') if name in {'sif_mcp', 'sif-mcp', 'sif'} else (name,)
    selected = next((n for n in aliases if n in servers), None)
    if selected is None:
        raise BridgeError('not_configured: ' + name)
    spec = dict(servers[selected], name=selected)
    if spec.get('enabled', True) is False:
        raise BridgeError('disabled: ' + selected)
    if spec.get('url'):
        spec['headers'] = {k: _expand(v) for k, v in spec.get('headers', {}).items()}
    elif spec.get('command'):
        spec['environment'] = {'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', **{k: _expand(v) for k, v in spec.get('env', {}).items()}}
    else:
        raise BridgeError('missing_transport: ' + selected)
    return spec


def load_server(config_path, name):
    if str(config_path).lower().endswith('.json'):
        return load_json_server(config_path, name)
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise BridgeError('config_unreadable: 无法读取 Codex MCP 配置') from exc
    aliases = ('sif_mcp', 'sif-mcp', 'sif') if name in {'sif_mcp', 'sif-mcp', 'sif'} else (name,)
    servers = data.get('mcp_servers', {})
    selected = next((n for n in aliases if n in servers), None)
    if selected is None:
        raise BridgeError('not_configured: ' + name)
    server = dict(servers[selected])
    if server.get('enabled', True) is False:
        raise BridgeError('disabled: ' + selected)
    server['name'] = selected
    if server.get('url'):
        headers = dict(server.get('http_headers', {}))
        for header, variable in server.get('env_http_headers', {}).items():
            value = env_value(variable)
            if not value:
                raise BridgeError('missing_credential: ' + variable)
            headers[header] = value
        variable = server.get('bearer_token_env_var')
        if variable:
            value = env_value(variable)
            if not value:
                raise BridgeError('missing_credential: ' + variable)
            headers['Authorization'] = 'Bearer ' + value
        server['headers'] = headers
    elif server.get('command'):
        environment = {'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        for variable in server.get('env_vars', []):
            if not isinstance(variable, str):
                raise BridgeError('unsupported_remote_environment: ' + selected)
            value = env_value(variable)
            if value is not None:
                environment[variable] = value
        environment.update(server.get('env', {}))
        server['environment'] = environment
    else:
        raise BridgeError('missing_transport: ' + selected)
    return server


def tool_allowed(server, name):
    allowed = server.get('enabled_tools')
    return (allowed is None or name in allowed) and name not in server.get('disabled_tools', [])


async def execute(config_path, server_name, action, tool=None, arguments=None, timeout=120):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client
    server = load_server(config_path, server_name)
    if action in {'schema', 'call'} and not tool_allowed(server, tool):
        raise BridgeError('tool_disabled: ' + str(tool))

    phase='start'
    async def operation():
        nonlocal phase
        async with AsyncExitStack() as stack:
            if server.get('url'):
                transport = await stack.enter_async_context(streamablehttp_client(
                    server['url'], headers=server['headers'], timeout=timeout, sse_read_timeout=timeout))
            else:
                # Child stderr may contain local credentials; never echo it into chat.
                errlog = stack.enter_context(open(os.devnull, 'w'))
                transport = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=server['command'], args=server.get('args', []), env=server['environment'],
                    cwd=server.get('cwd')), errlog=errlog))
            session = await stack.enter_async_context(ClientSession(
                transport[0], transport[1], read_timeout_seconds=timedelta(seconds=timeout)))
            phase='initialize'
            await session.initialize()
            if action == 'call':
                phase='call'
                result = await session.call_tool(tool, arguments or {})
                return result.model_dump(mode='json', exclude_none=True)
            phase='list'
            tools, cursor, seen = [], None, set()
            for _ in range(100):
                page = await session.list_tools(cursor=cursor)
                tools.extend(t for t in page.tools if tool_allowed(server, t.name))
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen:
                    raise BridgeError('invalid_tool_pagination')
                seen.add(cursor)
            else:
                raise BridgeError('tool_pagination_limit')
            if action == 'schema':
                match = next((t for t in tools if t.name == tool), None)
                if match is None:
                    raise BridgeError('tool_not_found: ' + str(tool))
                return match.model_dump(mode='json', exclude_none=True)
            return {'tools': [t.model_dump(mode='json', exclude_none=True) for t in tools]}

    try:
        return await asyncio.wait_for(operation(), timeout=timeout)
    except BridgeError:
        raise
    except Exception as exc:
        raise TransportFailure(safe_diagnostics(exc,phase=phase)) from None


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml')
    parser.add_argument('--server', default='sorftime')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('action', choices=['list', 'schema', 'call'])
    parser.add_argument('tool', nargs='?')
    parser.add_argument('arguments', nargs='?', default='{}')
    args = parser.parse_args()
    try:
        if args.action in {'schema', 'call'} and not args.tool:
            raise BridgeError('tool_name_required')
        if not 1 <= args.timeout <= 600:
            raise BridgeError('timeout_must_be_1_to_600')
        raw = Path(args.arguments[1:]).read_text(encoding='utf-8-sig') if args.arguments.startswith('@') else args.arguments
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            raise BridgeError('arguments_must_be_object')
        result = asyncio.run(execute(args.config, args.server, args.action, args.tool, arguments, args.timeout))
        print(json.dumps({'ok': not result.get('isError', False), 'server': args.server,
                          'source': 'direct_bridge', 'result': result,
                          **({'diagnostics':{'category':'tool_error','phase':'call'}} if result.get('isError') else {})}, ensure_ascii=False))
        return 2 if result.get('isError') else 0
    except TransportFailure as exc:
        print(json.dumps({'ok':False,'server':args.server,'source':'direct_bridge',
                          'error':'connection_or_tool_failed','diagnostics':exc.diagnostics},ensure_ascii=False))
        return 1
    except BridgeError as exc:
        error = str(exc)
    except (OSError, ValueError):
        error = 'invalid_arguments_or_file'
    except Exception as exc:
        # Do not print exception text/tracebacks: authenticated URLs may be present.
        error = 'connection_or_tool_failed: ' + type(exc).__name__
    print(json.dumps({'ok': False, 'server': args.server, 'source': 'direct_bridge', 'error': error,
                      'diagnostics':{'category':error.split(':',1)[0],'phase':'config_or_arguments'}}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
