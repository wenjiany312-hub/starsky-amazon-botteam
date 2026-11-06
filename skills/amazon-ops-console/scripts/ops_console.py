"""Local S5 console. All remote collection is read-only; reviews stay in SQLite."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
from urllib.parse import urlsplit

from ops_collect import _config, build_snapshot, run, write_json
from ops_review import ReviewLedger, ReviewError
from ops_manual import ManualFieldError, enrich_manual, save_manual


def allowed_origin(host, origin, port):
    return host in (f'127.0.0.1:{port}', f'localhost:{port}') and origin == 'http://' + host


def public_status(config, running, error):
    snapshot_path=Path(config['workspace'])/'reviewed_snapshot.json'
    snapshot=json.loads(snapshot_path.read_text(encoding='utf8')) if snapshot_path.is_file() else {}
    return {'store': config['store'], 'markets': [m['market'] for m in config['markets']],
            'running': running, 'last_error': error, 'backend_execution_enabled': False,
            'snapshot_status':snapshot.get('status'),'generated_at':snapshot.get('generatedAt'),
            'connections':snapshot.get('queries',{}).get('connections',{}).get('rows',[])}


def export_approved(ledger):
    records=ledger.list_actions(); approved=[]; expired=[]
    for record in records:
        if record['status']!='approved_not_executed':continue
        if datetime.fromisoformat(record['action']['expires_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc):
            expired.append(record['action_id'])
        else: approved.append(record)
    return {'schema_version': '1.0', 'created_at': datetime.now(timezone.utc).isoformat(),
            'backend_execution_enabled': False, 'state': 'not_submitted',
            'requires_live_context_and_current_value_revalidation':True,'expired_excluded':expired,'actions':approved}


def prepare(config_path):
    cfg = _config(config_path); root = Path(cfg['workspace'])
    project = Path(cfg.get('dashboard_project') or root/'app').resolve()
    node = Path(cfg.get('node_executable') or '')
    plugin = Path(cfg.get('data_plugin') or '')
    if not node.is_file() or not (plugin/'scripts/prepare-data-app.mjs').is_file():
        raise ValueError('set_node_executable_and_data_plugin_from_workspace_dependencies')
    if project.exists() and any(project.iterdir()):
        raise ValueError('dashboard_project_exists_use_build')
    root.mkdir(parents=True,exist_ok=True)
    snapshot = {'surface':'dashboard','title':'星空 · S5 店铺运营终端','status':'pending','buildStatus':'creating',
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'filters':[{'id':'market','label':'站点','field':'market','defaultValue':cfg['markets'][0]['market']},
                   {'id':'date','label':'数据日期','field':'date',
                    'queryIds':['sales_daily','ad_daily','sku_sales_daily','profit_daily']}],
        'queries':{k:{'rows':[],'source':{'label':k,'evidenceFlow':[]}} for k in
            ('ad_daily','ad_detail','campaigns','sales_daily','products','connections','costs','profit_daily','actions','comparisons','listing_checks','ad_observations')}}
    input_path=root/'reviewed_snapshot.json';write_json(input_path,snapshot)
    result=subprocess.run([str(node),str(plugin/'scripts/prepare-data-app.mjs'),'--surface','dashboard',
        '--from-reference','business-performance','--output',str(project),'--snapshot',str(input_path)],capture_output=True,timeout=120)
    if result.returncode: raise ValueError('dashboard_preparation_failed')
    content=Path(__file__).resolve().parent.parent/'assets/dashboard'
    for name in ('DashboardContent.jsx','dashboard.css'):
        shutil.copyfile(content/name,project/'src/content/dashboard'/name)
    initialized=json.loads((project/'src/data.json').read_text(encoding='utf8'))
    write_json(input_path,initialized)
    cfg['dashboard_project']=str(project)
    config_path=Path(config_path)
    if config_path.suffix.lower()=='.json':
        write_json(config_path,cfg)
    else:
        import yaml
        config_path.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False),encoding='utf8')
    refresh_view(cfg)
    return project


def refresh_view(config, *, collect=False, complete=True):
    cfg=_config(config); root=Path(cfg['workspace']);root.mkdir(parents=True,exist_ok=True)
    lock=root/'.console-update.lock'
    try:
        handle=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise ValueError('console_update_already_running') from None
    try:
        with os.fdopen(handle,'w') as stream:
            json.dump({'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat()},stream)
        return _refresh_view(cfg,collect=collect,complete=complete)
    finally:
        lock.unlink(missing_ok=True)


def _refresh_view(config, *, collect=False, complete=True):
    """Build source-backed content while leaving the Data App runtime untouched."""
    cfg = _config(config)
    if collect:
        subset=cfg.get('_collect_markets')
        run({**cfg,'markets':[m for m in cfg['markets'] if not subset or m['market'] in subset]})
        if cfg.get('sku_today_enabled',False):
            from ops_sales import collect_today_skus
            collect_today_skus({**cfg,'markets':[m for m in cfg['markets'] if not subset or m['market'] in subset]})
        if subset: build_snapshot(cfg)
    else:
        build_snapshot(cfg)
    root = Path(cfg['workspace'])
    snapshot = json.loads((root/'reviewed_snapshot.json').read_text(encoding='utf-8'))
    from ops_keywords import collect_keywords, enrich_keywords
    if collect:
        collect_keywords(cfg,snapshot)
    # Source rows retain the precise approved content and independent review state.
    records = ReviewLedger(root/'reviews.sqlite').list_actions()
    action_rows = []
    for r in records:
        a = r['action']; context = a['source_context']
        action_rows.append({**a, 'market': context['marketplace'],
                            'action_id': r['action_id'], 'action_hash': r['action_hash'], 'status': r['status']})
    snapshot['queries']['actions'] = {'rows': action_rows, 'source': {'label': '本地人工审核账本',
        'provider': 'SQLite review ledger', 'evidenceFlow': [{'title': '人工审核', 'detail': 'Content hash binds current/proposed values, object, context, evidence and expiration; approval does not execute.'}]}}
    for key, label in [('ad_detail','广告对象明细'),('costs','自维护成本'),('profit_daily','每日收益估算')]:
        snapshot['queries'].setdefault(key, {'rows': [], 'source': {'label': label, 'evidenceFlow': [], 'metricDefinitions': []}})
    # Optional enrichment is the same implementation used by the CLI and MCP.
    try:
        from ops_enrich import enrich_snapshot
    except ImportError:
        enrich_snapshot = None
    if enrich_snapshot:
        snapshot = enrich_snapshot(cfg, snapshot)
    from ops_listing import enrich_listings
    snapshot=enrich_listings(cfg,snapshot)
    snapshot=enrich_manual(cfg,snapshot)
    snapshot=enrich_keywords(cfg,snapshot)
    from ops_sales import enrich_sales
    from ops_restock import enrich_restock
    from ops_advice import enrich_advice
    snapshot=enrich_sales(cfg,snapshot)
    snapshot=enrich_restock(cfg,snapshot)
    snapshot=enrich_advice(cfg,snapshot)
    from ops_schedule import enrich_cadence
    snapshot=enrich_cadence(cfg,snapshot)
    snapshot['surface'] = 'dashboard'
    snapshot['title'] = '星空 · S5 店铺运营终端'
    snapshot['buildStatus'] = 'complete' if complete else 'updating'
    write_json(root/'reviewed_snapshot.json', snapshot)
    project = Path(cfg.get('dashboard_project') or root/'app').resolve()
    if not (project/'AGENTS.md').is_file():
        raise ValueError('dashboard_not_prepared')
    write_json(project/'src/data.json', snapshot)
    node = Path(cfg.get('node_executable', ''))
    script = Path(cfg.get('data_plugin', ''))/'scripts/data-app.mjs'
    if not node.is_file() or not script.is_file():
        raise ValueError('dashboard_build_runtime_missing')
    result = subprocess.run([str(node), str(script), 'build', '--project-dir', str(project), '--separate-data'],
                            capture_output=True, timeout=180, check=False)
    if result.returncode:
        (root/'build.log').write_bytes(result.stdout + result.stderr)
        raise ValueError('dashboard_build_failed')
    return snapshot


class Console:
    def __init__(self, config):
        self.config = _config(config)
        self.root = Path(self.config['workspace'])
        self.ledger = ReviewLedger(self.root/'reviews.sqlite')
        self.port = int(self.config.get('port', 9766))
        if not 1024 <= self.port <= 65535:
            raise ValueError('port_invalid')
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.running = False
        self.error = None

    def status(self):
        return {**public_status(self.config, self.running, self.error), 'csrf': self.csrf}

    def refresh(self):
        if not self.lock.acquire(blocking=False):
            return False
        self.running = True; self.error = None
        def job():
            try:
                refresh_view(self.config, collect=True)
            except Exception as exc:
                self.error = getattr(exc, 'code', None) or (str(exc) if isinstance(exc, (ValueError, ReviewError)) else 'local_update_failed')
            finally:
                self.running = False; self.lock.release()
        threading.Thread(target=job, daemon=True).start()
        return True


class Handler(SimpleHTTPRequestHandler):
    server_version = 'StarskyLocal/1.0'
    def log_message(self, *args):
        pass  # Query strings and headers never enter logs.

    @property
    def console(self):
        return self.server.console

    def _valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.console.port}', f'localhost:{self.console.port}')

    def _json(self, value, code=200, filename=None):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers(); self.wfile.write(body)

    def end_headers(self):
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404); return None

    def do_HEAD(self):
        if not self._valid_host():
            self.send_error(403);return
        return super().do_HEAD()

    def do_GET(self):
        if not self._valid_host():
            return self._json({'error':'host_not_allowed'},403)
        path = urlsplit(self.path).path
        if path.startswith('/api/'):
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (self.headers.get('Origin') and not allowed_origin(self.headers.get('Host'),self.headers.get('Origin'),self.console.port)):
                return self._json({'error':'origin_not_allowed'},403)
            if path == '/api/status':
                return self._json(self.console.status())
            if path == '/api/export-approved':
                return self._json(export_approved(self.console.ledger),filename='approved-actions-not-submitted.json')
            if path == '/api/cost-template':
                template=Path(__file__).resolve().parent.parent/'templates/成本维护模板.csv'
                content=template.read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="optional-cost-template.csv"')
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            return self._json({'error':'endpoint_not_found'},404)
        return super().do_GET()

    def do_POST(self):
        if not allowed_origin(self.headers.get('Host'),self.headers.get('Origin'),self.console.port):
            return self._json({'error':'origin_not_allowed'},403)
        if not secrets.compare_digest(self.headers.get('X-Ops-CSRF',''),self.console.csrf):
            return self._json({'error':'csrf_failed'},403)
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 16384 or self.headers.get_content_type() != 'application/json':
                raise ValueError('request_invalid')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('request_invalid')
            path = urlsplit(self.path).path
            if path == '/api/refresh':
                if body:
                    raise ValueError('unexpected_arguments')
                return self._json({'accepted':self.console.refresh()},202)
            if path == '/api/decide':
                if set(body) != {'action_id','expected_hash','decision'}:
                    raise ValueError('request_invalid')
                if not self.console.lock.acquire(blocking=False):
                    return self._json({'error':'update_in_progress'},409)
                try:
                    record = self.console.ledger.decide(**body)
                    try:
                        refresh_view(self.console.config)
                        return self._json({'status':record['status']})
                    except Exception:
                        return self._json({'status':record['status'],'view_update_error':'review_saved_rebuild_needed'})
                finally:
                    self.console.lock.release()
            if path == '/api/manual-highlights':
                if set(body) != {'market','asin','sku','value','expected_revision'}:
                    raise ValueError('request_invalid')
                if not self.console.lock.acquire(blocking=False):
                    return self._json({'error':'update_in_progress'},409)
                try:
                    record = save_manual(self.console.config, **body)
                    try:
                        refresh_view(self.console.config)
                        return self._json({'status':'saved_local','record':record})
                    except Exception:
                        return self._json({'status':'saved_local','record':record,
                                           'view_update_error':'manual_saved_rebuild_needed'})
                finally:
                    self.console.lock.release()
            return self._json({'error':'endpoint_not_found'},404)
        except ManualFieldError as exc:
            return self._json({'error':exc.code},409 if exc.code=='revision_conflict' else 400)
        except ReviewError as exc:
            return self._json({'error':str(exc)},409)
        except (ValueError,TypeError,KeyError):
            return self._json({'error':'invalid_local_request'},400)
        except Exception:
            return self._json({'error':'local_processing_failed'},500)


def main():
    parser = argparse.ArgumentParser(description='本地店铺巡检、看板与人工审核')
    parser.add_argument('--config',required=True)
    parser.add_argument('command',choices=['prepare','collect','scheduled-run','build','serve','status','export-approved','propose','receipt'])
    parser.add_argument('--input',help='本地建议或执行回执 JSON 路径')
    args = parser.parse_args()
    if args.command=='prepare':
        print(str(prepare(args.config))); return
    cfg = _config(args.config); root = Path(cfg['workspace'])
    ledger = ReviewLedger(root/'reviews.sqlite')
    if args.command=='scheduled-run':
        from ops_schedule import due_markets,record_collection
        due=due_markets(cfg)
        if due:
            snapshot=refresh_view({**cfg,'_collect_markets':due},collect=True)
            record_collection(cfg,due)
            print(json.dumps({'state':snapshot['status'],'updated_markets':due,'backend_writes':0},ensure_ascii=False))
        else:
            print(json.dumps({'state':'no_new_closed_day','updated_markets':[],'backend_writes':0}))
        return
    if args.command in ('collect','build'):
        snapshot = refresh_view(cfg, collect=args.command=='collect')
        print(json.dumps({'state':snapshot['status'],'snapshot':str(root/'reviewed_snapshot.json'),'backend_writes':0},ensure_ascii=False)); return
    if args.command == 'status':
        print(json.dumps(public_status(cfg,False,None),ensure_ascii=False)); return
    if args.command == 'export-approved':
        target = root/'exports'/('approved-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
        write_json(target,export_approved(ledger)); print(str(target)); return
    if args.command in ('propose','receipt'):
        if not args.input:
            parser.error('--input is required')
        value = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        record = ledger.propose(value) if args.command=='propose' else ledger.record_result(**value)
        refresh_view(cfg); print(json.dumps({'action_id':record['action_id'],'status':record['status']})); return
    console = Console(cfg)
    project = Path(cfg.get('dashboard_project') or root/'app')
    dist = project/'dist'
    if not (dist/'index.html').is_file():
        raise ValueError('dashboard_not_built')
    server = ThreadingHTTPServer(('127.0.0.1',console.port),partial(Handler,directory=str(dist)))
    server.console = console
    print(f'Local console: http://127.0.0.1:{console.port}/',flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
