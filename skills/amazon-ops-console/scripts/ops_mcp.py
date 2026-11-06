"""Optional local stdio MCP, sharing the exact read-only console implementation."""
import argparse
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from ops_collect import _config
from ops_console import public_status, refresh_view

def make_server(config_path):
    config=_config(config_path)
    mcp=FastMCP('Starsky Ops Console')

    @mcp.tool()
    def ops_status() -> dict:
        """Read local collection availability; no credentials or account write."""
        path=Path(config['workspace'])/'collection_status.json'
        return {'connection':public_status(config,False,None),'collection':json.loads(path.read_text(encoding='utf8')) if path.exists() else None}

    @mcp.tool()
    def ops_patrol() -> dict:
        """Collect authorized store data, refresh local patrol and dashboard. Does not change Amazon."""
        snapshot=refresh_view(config,collect=True)
        return {'state':snapshot['status'],'generatedAt':snapshot['generatedAt'],
                'rows':{k:len(v['rows']) for k,v in snapshot['queries'].items()},'backend_writes':0}

    @mcp.tool()
    def ops_report() -> str:
        """Read this store's latest local patrol text. Review decisions are made in the local dashboard."""
        path=Path(config['workspace'])/'daily_report.md'
        return path.read_text(encoding='utf8') if path.is_file() else 'No patrol report has been generated.'

    return mcp

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    args=parser.parse_args();make_server(args.config).run(transport='stdio')
