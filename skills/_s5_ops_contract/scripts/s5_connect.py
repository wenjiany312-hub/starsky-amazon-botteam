#!/usr/bin/env python3
"""S5 第二部分接入：领星、飞书、每日推送，一个脚本管。密钥只从用户拖进来的 txt 读，写进用户目录，不在输出里显示。

  python s5_connect.py status                         # 接了什么、缺什么、下一步说什么口令
  python s5_connect.py ip                             # 本机直连出口 IP（绕开代理；领星白名单要加这个）
  python s5_connect.py lingxing --file 领星密钥.txt    # 写凭据 → 连接测试 → 列店铺
  python s5_connect.py lingxing --stores 9964,39058   # 选要每天巡检的店铺（不给就全选）
  python s5_connect.py saihu --file 赛狐密钥.txt      # 赛狐（开发测试中）：先把 client_id / secret 存好，接口上线后自动启用
  python s5_connect.py feishu --file 飞书配置.txt      # 写 webhook / 签名 → 发一条测试消息
  python s5_connect.py schedule on|off|status        # 每天北京时间 9 点推日报（Mac launchd / Windows 任务计划）
  python s5_connect.py log                            # 最近一次日报的运行记录（「日报为什么没推」）
  python s5_connect.py route --root <产品根> --set manual|lingxing|saihu   # 进 S5 时用户选的路线（悬浮窗只显示这条）
  python s5_connect.py check --root <产品根>          # 按路线检测配置：完成就记下来，之后从 S4 进 S5 不再重复配置
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()
SKILLS = HERE.parents[2]
LX_DIR = SKILLS / 'lingxing-erp-connector'
TEMPLATE = LX_DIR / 'references' / 'lingxing_config模板.yaml'
IP_SERVICES = ('http://ip.3322.net', 'http://myip.ipip.net', 'http://ip.cip.cc')
LABEL = 'com.starsky.dailyreport'


def home():
    host = '.starsky-claude' if (SKILLS.parent / '.claude-plugin').is_dir() else '.starsky-codex'
    return Path(os.environ.get('STARSKY_HOME') or (Path.home() / host)).expanduser()


def config_dir():
    return Path(os.environ.get('STARSKY_CONFIG_DIR') or (home() / 'config'))


def lx_config_path():
    return config_dir() / 'lingxing_config.yaml'


def ops_root():
    return Path(os.environ.get('STARSKY_OPS_ROOT') or (home() / 'ops_workspace'))


def load_client():
    spec = importlib.util.spec_from_file_location('s5_connect_lingxing', LX_DIR / 'scripts' / 'lingxing_client.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONFIG = lx_config_path()
    mod.load_config = lambda: {**(read_config().get('lingxing') or {}),
                               'base_url': (read_config().get('lingxing') or {}).get('base_url') or 'https://openapi.lingxing.com'}
    mod.load_stores = lambda: read_config().get('stores') or []
    return mod


def read_config():
    path = lx_config_path()
    if path.is_file():
        doc = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    else:
        doc = yaml.safe_load(TEMPLATE.read_text(encoding='utf-8')) or {}
    for key, script in globals().get('EXTRA_CHECKS', {}).items():
        spec = importlib.util.spec_from_file_location('s5_config_' + key, script)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        if hasattr(mod, 'shared_config'):
            for name, value in mod.shared_config().items():
                current = doc.get(name) or {}
                fields = {'lingxing': ('app_id', 'app_secret'), 'feishu': ('webhook', 'secret')}.get(name)
                if fields:
                    doc[name] = {**value, **{k: v for k, v in current.items() if v}}
                elif not current:
                    doc[name] = value
    return doc


def write_config(doc):
    path = lx_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    head = '# 星空 S5 ERP 路线配置（领星凭据 / 巡检店铺 / 飞书）。含密钥，只存本机，不要分享。\n'
    path.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def read_txt(path):
    try:
        return Path(path).expanduser().read_text(encoding='utf-8-sig')
    except PermissionError:
        raise SystemExit(json.dumps({'status': 'blocked', 'error': '系统不让读这个位置（Mac 的「下载」文件夹常见）：把 txt 挪到别的文件夹再拖进来'}, ensure_ascii=False))


def field(text, *names):
    for name in names:
        m = re.search(rf'{name}\s*[:：=]\s*(\S+)', text, re.I)
        if m and not m.group(1).startswith('<'):
            return m.group(1).strip()
    return ''


def egress_ip():
    import requests
    session = requests.Session()
    session.trust_env = False  # 领星白名单认直连出口；代理 / VPN 的出口不算
    for url in IP_SERVICES:
        try:
            text = session.get(url, timeout=8).text
            m = re.search(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b', text)
            if m:
                return {'ip': m.group(1), 'source': url}
        except Exception:  # noqa: BLE001 — 换下一个服务
            continue
    return {'ip': None, 'error': '三个国内查 IP 服务都没连上：检查网络，或关掉代理 / VPN 的「全局模式」后再试'}


def cmd_ip(args):
    out = egress_ip()
    if out.get('ip'):
        out['next'] = f"领星 → 设置 → 业务配置 → 开放接口 → IP 白名单 → 编辑，加入 {out['ip']}"
    return out, (0 if out.get('ip') else 2)


def cmd_lingxing(args):
    doc = read_config()
    lx = doc.setdefault('lingxing', {})
    if args.file:
        text = read_txt(args.file)
        app_id, secret = field(text, 'app ?id', 'appid'), field(text, 'app ?secret', 'appsecret')
        if not app_id or not secret:
            return {'status': 'fail', 'error': 'txt 里没找到 APP ID / AppSecret：按「APP ID：xxx」「AppSecret：xxx」各一行填'}, 2
        lx.update(app_id=app_id, app_secret=secret, base_url=lx.get('base_url') or 'https://openapi.lingxing.com')
        write_config(doc)
    if not lx.get('app_id'):
        return {'status': 'blocked', 'error': '还没有领星凭据：填好「领星密钥.txt」拖进对话说「接入领星」'}, 2
    client = load_client()
    try:
        cfg = client.load_config()
        with contextlib.redirect_stdout(io.StringIO()):
            token = client.get_token(cfg)
            resp = client.call(cfg, token, '/erp/sc/data/seller/lists', {}, method='GET')
    except SystemExit as exc:
        msg = str(exc)
        out = {'status': 'fail', 'error': '领星连接未完成，请核对本机凭据与开放接口权限'}
        if '403' in msg or '白名单' in msg:
            out.update(egress_ip(), next='把上面的 IP 加进领星白名单后，再说「接入领星」')
        return out, 2
    sellers = resp.get('data') or []
    known = {s.get('sid'): s for s in doc.get('stores') or []}
    wanted = {int(x) for x in (args.stores or '').replace('，', ',').split(',') if x.strip()} or None
    stores = []
    for s in sellers:
        sid = int(s.get('sid'))
        mp = str(s.get('country') or s.get('marketplace') or '').upper()
        mp = {'美国': 'US', '英国': 'UK', '德国': 'DE', '日本': 'JP', '加拿大': 'CA', '法国': 'FR', '意大利': 'IT', '西班牙': 'ES'}.get(s.get('country'), mp)
        prev = known.get(sid) or {}
        stores.append({'sid': sid, 'nickname': prev.get('nickname') or s.get('name') or str(sid), 'marketplace': prev.get('marketplace') or mp,
                       'patrol': (sid in wanted) if wanted is not None else prev.get('patrol', True)})
    doc['stores'] = stores
    write_config(doc)
    return {'status': 'ok', 'stores': stores,
            'next': '要改巡检哪几个店：说「只巡检 <sid>」；接下来可以「接入飞书」「生成成本表」'}, 0


def cmd_saihu(args):
    """赛狐开放接口要客户经理开「自用型应用」；数据接入还在开发测试，这里只把凭据安全存好。"""
    doc = read_config()
    text = read_txt(args.file)
    cid, secret = field(text, 'client_id', 'client id', 'appid'), field(text, 'client_secret', 'client secret', 'appsecret')
    if not cid or not secret:
        return {'status': 'fail', 'error': 'txt 里没找到 client_id / client_secret：按「client_id：xxx」「client_secret：xxx」各一行填'}, 2
    doc.setdefault('saihu', {}).update(client_id=cid, client_secret=secret)
    write_config(doc)
    return {'status': 'saved', 'erp': 'saihu',
            'note': '凭据已存，这条通道还没接通，本轮先用手动表格',
            'next': '切换手动表格路线，再把批量操作下载的表格发给助手'}, 0


def cmd_youmaiyun(args):
    """Keep the official credential file; do not invent an unverified provider schema."""
    text = read_txt(args.file)
    if not text.strip():
        return {'status': 'fail', 'error': '凭据文件为空，请先按官方接入指引取得文件'}, 2
    target = config_dir()/'youmaiyun_credentials.txt'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf8')
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    doc = read_config()
    doc['youmaiyun'] = {'credentials_file': str(target), 'connected': False}
    write_config(doc)
    return {'status': 'saved', 'erp': 'youmaiyun',
            'note': '凭据已存，这条通道还没接通，本轮先用手动表格'}, 0


def cmd_feishu(args):
    doc = read_config()
    fs = doc.setdefault('feishu', {})
    text = read_txt(args.file)
    hook = (re.search(r'https://open\.(?:feishu|larksuite)\.(?:cn|com)/open-apis/bot/v2/hook/[0-9A-Za-z-]+', text) or [None])[0]
    if not hook:
        return {'status': 'fail', 'error': 'txt 里没找到 Webhook 地址（https://open.feishu.cn/open-apis/bot/v2/hook/… 那一串）'}, 2
    fs.update(webhook=hook, secret=field(text, '签名校验', '签名', 'secret'))
    for k, v in (('push_priority', ['P1', 'P2']), ('max_items', 10), ('push_when_clear', True)):
        fs.setdefault(k, v)
    write_config(doc)
    env = {**os.environ, 'STARSKY_CONFIG_DIR': str(config_dir())}
    run = subprocess.run([sys.executable, str(LX_DIR / 'scripts' / 'feishu_push.py'), '--test'], capture_output=True, text=True, env=env)
    ok = run.returncode == 0
    return {'status': 'ok' if ok else 'fail', 'signed': bool(fs.get('secret')),
            'detail': (run.stdout or run.stderr).strip()[-300:],
            'next': '去飞书群看有没有「连通性测试」消息；有就说「开启日报」' if ok else '没发出去：核对 Webhook 和签名密钥（签名要和机器人设置里的一致）'}, (0 if ok else 2)


# ---------- 每日推送 ----------

LAUNCHER = """# 星空每日日报启动器（固定位置）：每次从安装记录找当前插件版本再运行，插件升级后不用重设定时任务。
import json, pathlib, subprocess, sys
rec = pathlib.Path(r'{rec}')
d = json.loads(rec.read_text(encoding='utf-8-sig'))
script = pathlib.Path(d['home']) / 'skills' / '_s5_ops_contract' / 'scripts' / 's5_connect.py'
sys.exit(subprocess.call([d.get('python') or sys.executable, str(script), 'run-daily'] + sys.argv[1:]))
"""


def install_record():
    spec = importlib.util.spec_from_file_location('s5_freshness', HERE.parents[3] / 'scripts' / 'plugin_freshness.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.install_record(HERE.parents[3])


def write_launcher():
    launcher = home() / 'ops' / 'starsky_daily.py'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.format(rec=install_record()), encoding='utf-8')
    return launcher


def cmd_schedule(args):
    system = platform.system()
    logs = ops_root() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    if system == 'Darwin':
        plist = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
        uid = str(os.getuid())
        if args.action == 'on':
            launcher = write_launcher()
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{launcher}</string><string>--window</string></array>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>5</integer></dict>
  <key>StandardOutPath</key><string>{logs}/launchd.out</string>
  <key>StandardErrorPath</key><string>{logs}/launchd.err</string>
</dict></plist>
''', encoding='utf-8')
            subprocess.run(['launchctl', 'bootout', f'gui/{uid}', str(plist)], capture_output=True)
            subprocess.run(['launchctl', 'bootstrap', f'gui/{uid}', str(plist)], capture_output=True)
        elif args.action == 'off':
            subprocess.run(['launchctl', 'bootout', f'gui/{uid}', str(plist)], capture_output=True)
        on = subprocess.run(['launchctl', 'print', f'gui/{uid}/{LABEL}'], capture_output=True).returncode == 0
    elif system == 'Windows':
        task = 'StarskyDailyReport'
        if args.action == 'on':
            cmd = f'"{sys.executable}" "{write_launcher()}" --window'
            subprocess.run(['schtasks', '/Create', '/F', '/TN', task, '/SC', 'HOURLY', '/MO', '1', '/ST', '00:05', '/TR', cmd],
                           capture_output=True, text=True)
        elif args.action == 'off':
            subprocess.run(['schtasks', '/Delete', '/F', '/TN', task], capture_output=True, text=True)
        on = subprocess.run(['schtasks', '/Query', '/TN', task], capture_output=True).returncode == 0
    else:
        return {'status': 'blocked', 'error': f'{system} 暂不支持定时任务'}, 2
    return {'status': 'ok', 'schedule': 'on' if on else 'off', 'system': system,
            'note': '每小时检查一次，北京时间 9–12 点且当天没推过才推；电脑关机或一直睡眠当天就不推'}, 0


def beijing_now():
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(hours=8)


def cmd_run_daily(args):
    """定时任务调用：拉数 → 利润健康 → 今日方案 → 推飞书（每个巡检店铺）。"""
    today = beijing_now().date().isoformat()
    logs = ops_root() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    mark = logs / f'pushed_{today}'
    if args.window and (mark.exists() or not 9 <= beijing_now().hour < 12):
        return {'status': 'skip'}, 0
    env = {**os.environ, 'STARSKY_OPS_ROOT': str(ops_root()), 'STARSKY_CONFIG_DIR': str(config_dir())}
    stores = [s for s in read_config().get('stores') or [] if s.get('patrol')]
    ok = True
    with (logs / f'daily_{today}.log').open('a', encoding='utf-8') as log:
        log.write(f'==== {beijing_now():%F %T} 北京时间 · {HERE}\n')
        for s in stores:
            mp = s.get('marketplace') or ''
            steps = [(LX_DIR / 'scripts' / 'daily_patrol_data.py', True), (HERE.parent / 's5_daily_health.py', False),
                     (HERE.parent / 's5_daily_plan.py', False), (LX_DIR / 'scripts' / 'feishu_push.py', True)]
            for script, required in steps:
                run = subprocess.run([sys.executable, str(script), '--marketplace', mp] + (['--dry-run'] if args.dry and 'feishu' in script.name else []),
                                     capture_output=True, text=True, env=env, cwd=str(script.parent))
                log.write(run.stdout + run.stderr)
                if run.returncode != 0 and required:
                    ok = False
    if ok and not args.dry:
        mark.touch()
    return {'status': 'ok' if ok else 'fail', 'log': str(logs / f'daily_{today}.log')}, (0 if ok else 2)


def cmd_log(args):
    logs = ops_root() / 'logs'
    files = sorted(logs.glob('daily_*.log'))
    if not files:
        return {'status': 'empty', 'note': '还没有日报运行记录：说「开启日报」，或检查电脑在北京时间 9–12 点是否开着'}, 0
    text = files[-1].read_text(encoding='utf-8', errors='replace')
    keep = [l for l in text.splitlines() if re.search(r'====|已推送|推送失败|Traceback|Error|错误|提示|403|白名单|FINDINGS', l)]
    return {'status': 'ok', 'file': str(files[-1]), 'pushed_today': (logs / f'pushed_{beijing_now().date().isoformat()}').exists(),
            'lines': keep[-20:]}, 0


ROUTE_FILE = Path('S5_运营板块') / 's5_route.json'
ROUTES = {'manual': ('manual', None, '手动表格'), 'erp': ('erp', None, 'ERP'), 'lingxing': ('erp', 'lingxing', '领星'), 'saihu': ('erp', 'saihu', '赛狐'), 'youmaiyun': ('erp', 'youmaiyun', '优麦云')}
GUIDE_ANCHOR = {'manual': 'part1', 'erp': 'lingxing', 'lingxing': 'lingxing', 'saihu': 'saihu', 'youmaiyun': 'youmaiyun'}
ROUTES_D = HERE.parents[0].parent / 'routes.d'  # 别的版本档往这里放 *.yaml 就多出一条路线（基础版没有这个目录，代码不用改）
EXTRA_CHECKS = {}
EXTRA_ROUTES = {}


def load_extra_routes():
    """routes.d/<key>.yaml：{key, label, guide_anchor, check_script}。check_script 是同目录下的 Python，导出 check(root, doc) → items。"""
    if not ROUTES_D.is_dir():
        return
    for path in sorted(ROUTES_D.glob('*.yaml')):
        try:
            spec = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            key, label = str(spec['key']), str(spec['label'])
        except (OSError, ValueError, KeyError, yaml.YAMLError):
            continue
        if key in ROUTES:
            continue
        ROUTES[key] = (key, None, label)
        EXTRA_ROUTES[key] = spec
        GUIDE_ANCHOR[key] = str(spec.get('guide_anchor') or key)
        if spec.get('check_script'):
            EXTRA_CHECKS[key] = ROUTES_D / str(spec['check_script'])


load_extra_routes()


def route_file(root):
    return Path(root or ops_root()) / ROUTE_FILE


def read_setup():
    path = config_dir()/'s5_setup.json'
    if not path.is_file():
        return {'version': 1, 'routes': {}, 'configured': {key: False for key in ROUTES},
                'stores': [], 'feishu': {'configured': False, 'skipped': False}, 'cost_tables': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('需要对象')
        data.setdefault('version', 1)
        data.setdefault('routes', {})
        for key in ROUTES:
            data.setdefault('configured', {}).setdefault(key, False)
        data.setdefault('stores', [])
        data.setdefault('feishu', {'configured': False, 'skipped': False})
        data.setdefault('cost_tables', {})
        return data
    except (OSError, ValueError) as exc:
        raise ValueError('店铺接入记录损坏，请修复 s5_setup.json；不会清空已有配置') from exc


def read_route(root):
    setup = read_setup()
    key = setup.get('default_route')
    if key in (setup.get('routes') or {}):
        data = dict(setup['routes'][key])
        data['configured'] = setup.get('configured') or {}
        data['config_done'] = bool(data['configured'].get(key, data.get('config_done')))
        return data
    try:
        data = json.loads(route_file(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict) and route_key(data):
        write_route(root, data)
        return data
    return {}


def write_route(root, data):
    key = route_key(data)
    if not key:
        raise ValueError('未知接入路线')
    setup = read_setup()
    setup.update(version=1, default_route=key)
    setup.setdefault('configured', {}).update(data.get('configured') or {})
    if 'config_done' in data:
        setup['configured'][key] = bool(data['config_done'])
    setup.setdefault('routes', {})[key] = {k: v for k, v in data.items() if k != 'configured'}
    doc = read_config()
    setup['stores'] = [{k: store[k] for k in ('sid', 'marketplace', 'nickname', 'patrol') if k in store}
                       for store in doc.get('stores') or []]
    # Only readiness and source references belong here; credentials stay in their existing files.
    setup['feishu'] = {'configured': bool((doc.get('feishu') or {}).get('webhook')),
                       'skipped': bool(data.get('feishu_skipped'))}
    setup['cost_tables'] = cost_tables(doc)
    save_setup(setup)
    if root is not None:
        scope = route_file(root)
        scope.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if scope.is_file():
            try:
                existing = json.loads(scope.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                pass
        scope_data = {k: v for k, v in existing.items() if k in ('scope', 'asins', 'skus', 'parent')}
        scope_data.setdefault('scope', 'product')
        scope_data['product_root'] = str(Path(root).resolve())
        scope.write_text(json.dumps(scope_data, ensure_ascii=False)+'\n', encoding='utf8')


def save_setup(setup):
    path = config_dir()/'s5_setup.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(setup, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    temporary.replace(path)


def cost_tables(doc):
    path = config_dir() / 'cost_tables.yaml'
    local = (yaml.safe_load(path.read_text(encoding='utf-8')) or {}) if path.is_file() else {}
    return {**(doc.get('cost_tables') or {}), **local}


def route_key(data):
    for key, (route, erp, _label) in ROUTES.items():
        if data.get('route') == route and (data.get('erp') or None) == erp:
            return key
    return None


def erp_detected():
    """本机已经接入了哪些 ERP（有凭据就算）。进 S5 时用来问「要不要改用自动取数」。"""
    doc = read_config()
    found = []
    lx, sh = doc.get('lingxing') or {}, doc.get('saihu') or {}
    if lx.get('app_id') and lx.get('app_secret'):
        found.append('lingxing')
    if sh.get('client_id') and sh.get('client_secret'):
        found.append('saihu')
    ym = doc.get('youmaiyun') or {}
    if ym.get('credentials_file') and Path(ym['credentials_file']).is_file():
        found.append('youmaiyun')
    return found


def entry_prompt(key, data, detected):
    """进 S5 时给用户的一句话：默认沿用上次路线，不重新配置；有别的可用路线就问一句要不要换。选什么都保存成新的默认。"""
    _route, _erp, label = ROUTES[key]
    if data.get('config_done'):
        suffix = '' if key == 'manual' or data.get('feishu_skipped') else ' + 飞书日报'
        return f'沿用：{label}{suffix}（已配好）。本轮看全店还是这个产品？'
    status = '配置还没完成'
    line = f'沿用上次路线：{label}（{status}）。'
    alts = []
    if key == 'manual':
        if 'lingxing' in detected:
            alts.append('检测到你已经接入了领星，本轮要改用领星自动取数吗？')
        elif 'saihu' in detected:
            alts.append('检测到你存了赛狐凭据；赛狐数据接入还在开发测试中，现在先用手动报表。')
        else:
            alts.append('以后接了领星 / 赛狐，随时说「换成接领星」。')
    elif key == 'lingxing':
        alts.append('要这一轮改用手动报表也行，说「换成手动报表」。')
    elif key in ('saihu', 'youmaiyun'):
        alts.append(label+'这条通道还没接通，本轮先用手动表格。')
    return line + ''.join(alts) + ' 回「沿用」就直接开始，要换就说换成哪条；换了也会记住。'


def entry_card():
    text = '''S5 运营 = 广告优化 + 文案优化 + 每日日报。
选一种接法，以后可换：

① 手动表格（不用接任何系统）
   准备：广告后台「批量操作」下载 7–14 天表格
   能做：诊断 → 建议 → 你批准 → 出上传表你去传
   市场数据：用已接的 SIF / Sorftime MCP 补

② 接 ERP 自动取数 + 飞书日报
   ERP：领星、赛狐、优麦云，你用哪个就接哪个，按官方接入指引操作
   准备：ERP 开放接口密钥、IP 白名单、飞书机器人
   能做：每天 9 点自动拉数 → 飞书推日报 → 你批准后一键写回竞价 / 否词 / 预算

两种都建议补一张成本表（算保本 ACOS 和利润）。'''
    for spec in EXTRA_ROUTES.values():
        text += '\n\n' + str(spec.get('entry_text') or spec['label'])
    return text + ('\n回 1、2 或 3。' if EXTRA_ROUTES else '\n回 1 或 2。')


def cmd_route(args):
    """进 S5 的路线：默认沿用上次选的，随时可换；换路线只重做那条路线的配置检测，已配好的路线记着不用重配。"""
    data = read_route(args.root)
    detected = erp_detected()
    if args.set:
        route, erp, label = ROUTES[args.set]
        done_before = data.get('configured') or {}
        saved = (read_setup().get('routes') or {}).get(args.set) or {}
        data = {**saved, 'route': route, 'erp': erp, 'label': label, 'anchor': GUIDE_ANCHOR[args.set], 'chosen_at': datetime.now().astimezone().isoformat(),
                'previous': data.get('label') if data.get('label') != label else data.get('previous'),
                'configured': done_before, 'config_done': bool(done_before.get(args.set))}
        write_route(args.root, data)
    key = route_key(data)
    guide = str(HERE.parents[1] / 'guide' / 'S5运营接入指南.html')
    if not key:
        return {'status': 'unset', 'detected_erp': detected,
                'guide': guide, 'ask': entry_card()}, 0
    result = {'status': 'ok', 'route': key, 'label': data['label'], 'config_done': bool(data.get('config_done')), 'detected_erp': detected,
            'guide_section': GUIDE_ANCHOR[key], 'guide': guide + '#' + GUIDE_ANCHOR[key], 'ask': entry_prompt(key, data, detected),
            'next': '跑 s5_connect.py check 看配置是否完成' if not data.get('config_done') else '配置已完成：用户回「沿用」就直接说下一步；要换路线就 route --set'}
    setup = read_setup()
    seen = set(setup.get('detected_channels') or [])
    new = [k for k in detected if k != key and k not in seen]
    if new:
        labels = '、'.join(ROUTES[k][2] for k in new)
        result['ask'] += f' 检测到新接入的{labels}，本轮要换路线吗？'
        setup['detected_channels'] = sorted(seen | set(detected))
        save_setup(setup)
    if args.set and key in EXTRA_CHECKS:
        spec = importlib.util.spec_from_file_location('s5_select_' + key, EXTRA_CHECKS[key])
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        if hasattr(mod, 'on_select'):
            result['onboarding'] = mod.on_select(args.root, read_config())
    return result, 0


def item(name, ok, fix='', level='required'):
    return {'name': name, 'ok': bool(ok), 'level': level, 'fix': '' if ok else fix}


def cmd_check(args):
    """配置检测：只查这条路线需要的东西。必须项都通过就记 config_done，之后不再重复配置；推荐项没做只提醒。"""
    data = read_route(args.root)
    key = route_key(data)
    if not key:
        return {'status': 'unset', 'note': '先选路线：手动表格，或 ERP（领星、赛狐、优麦云）'}, 2
    doc = read_config()
    lx, fs, sh = doc.get('lingxing') or {}, doc.get('feishu') or {}, doc.get('saihu') or {}
    costs = cost_tables(doc)
    items = []
    if key == 'manual':
        feed = Path(args.root or ops_root()) / 'S5_运营板块' / '_投喂_广告报表'
        feed.mkdir(parents=True, exist_ok=True)  # 建好就算通过：下载的表格放这里
        items.append(item('放报表的文件夹', feed.is_dir(), '建不出来：检查产品文件夹权限'))
        has_file = any(p.suffix.lower() in ('.xlsx', '.xls', '.csv') for p in feed.iterdir())
        items.append(item('已放入广告报表', has_file, '去广告后台「批量操作」下载最近 7–14 天的表格（勾选商品推广、搜索词）放进 ' + str(feed), 'recommended'))
    elif key == 'lingxing':
        items.append(item('领星 APP ID / AppSecret', lx.get('app_id') and lx.get('app_secret'), '打开指南「接入领星」，填领星密钥.txt 拖进对话说「接入领星」'))
        stores = [s for s in doc.get('stores') or [] if s.get('patrol')]
        items.append(item('选好要优化的店铺', stores, '说「接入领星」后按提示选店铺'))
        if lx.get('app_id') and lx.get('app_secret'):
            try:
                client = load_client()
                # Shared configuration may come directly from the terminal; don't require a second copy.
                with contextlib.redirect_stdout(io.StringIO()):
                    client.get_token({**lx, 'base_url': lx.get('base_url') or 'https://openapi.lingxing.com'})
                items.append(item('领星连接（含 IP 白名单）', True))
            except (SystemExit, Exception) as exc:  # noqa: BLE001 — 连接失败要给人话，不带密钥
                ip = egress_ip()
                items.append(item('领星连接（含 IP 白名单）', False,
                                  '连接没通，请核对凭据和白名单' + ('；把这个出口 IP 加进领星白名单：' + ip['ip'] if ip.get('ip') else '')))
    elif key in EXTRA_CHECKS:  # 其他版本档带来的路线：由它自己的检测脚本给出必须项和推荐项
        spec = importlib.util.spec_from_file_location('s5_extra_check_' + key, EXTRA_CHECKS[key])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        items.extend(mod.check(args.root, doc))
    elif key == 'erp':
        items.append(item('选择 ERP', False, '领星、赛狐、优麦云，用哪个接哪个，按官方接入指引操作'))
    else:
        provider = doc.get(key) or {}
        label = ROUTES[key][2]
        saved = provider.get('client_id') and provider.get('client_secret') or provider.get('credentials_file') and Path(provider['credentials_file']).is_file()
        items.append(item(label+'凭据已保存', saved, '按接入指南导入'+label+'密钥文件'))
        items.append(item(label+'数据接入', False, '凭据已存，这条通道还没接通，本轮先用手动表格' if saved else '尚未导入凭据；这条通道还没接通，本轮先用手动表格'))
    items.append(item('成本表（算保本 ACOS 和利润）', costs, '说「生成成本表」（领星预填）/「登记成本表 <路径>」/「毛利大概 30%」，三选一', 'recommended'))
    if key != 'manual':
        if getattr(args, 'skip_feishu', False):
            data['feishu_skipped'] = True
        items.append(item('飞书日报', fs.get('webhook') or data.get('feishu_skipped'), '接入飞书，或明确说「先不要」跳过'))
    required_ok = all(i['ok'] for i in items if i['level'] == 'required')
    data['config_done'] = required_ok
    data.setdefault('configured', {})[key] = required_ok  # 每条路线各记各的：换过去再换回来不用重配
    data['config_checked_at'] = datetime.now().astimezone().isoformat()
    write_route(args.root, data)
    todo = [i for i in items if not i['ok'] and i['level'] == 'required']
    tips = [i for i in items if not i['ok'] and i['level'] == 'recommended']
    feed_path = str(Path(args.root or ops_root()) / 'S5_运营板块' / '_投喂_广告报表')
    if required_ok:
        nxt = ('配置完成。去广告后台「批量操作」下载最近 7–14 天表格放进 ' + feed_path + '，然后说「优化广告」') if key == 'manual' else \
              '配置完成。直接说「优化广告」，自动拉数据出方案（之后每次进 S5 不用再配置）'
    else:
        nxt = '还差：' + '；'.join(i['name'] for i in todo) + '。照指南对应一节做，卡住截图发我'
    return {'status': 'ready' if required_ok else 'todo', 'route': key, 'label': data['label'], 'items': items,
            'reminders': [i['name'] + '：' + i['fix'] for i in tips], 'next': nxt}, (0 if required_ok else 2)


def cmd_status(args):
    data = read_route(None)
    key = route_key(data)
    if key == 'manual' or key in EXTRA_CHECKS:
        return cmd_check(argparse.Namespace(root=None))
    doc = read_config()
    lx, fs, sh = doc.get('lingxing') or {}, doc.get('feishu') or {}, doc.get('saihu') or {}
    costs = cost_tables(doc)
    stores = [s for s in doc.get('stores') or [] if s.get('patrol')]
    todo = []
    if not lx.get('app_id') and not sh.get('client_id'):
        todo.append('接入领星：填「领星密钥.txt」拖进对话说「接入领星」（指南第 2 节）；用赛狐的看指南 2b 节')
    if not fs.get('webhook') and not data.get('feishu_skipped'):
        todo.append('（可选）接入飞书：填「飞书配置.txt」拖进对话说「接入飞书」（指南第 3 节）')
    missing_cost = [s.get('marketplace') for s in stores if s.get('marketplace') not in costs]
    if missing_cost:
        todo.append('成本表：' + '、'.join(missing_cost) + ' 还没有 —— 说「生成成本表」「登记成本表 <路径>」或「毛利大概 30%」（指南第 4 节）')
    sched, _ = cmd_schedule(argparse.Namespace(action='status')) if fs.get('webhook') else ({'schedule': 'off'}, 0)
    if fs.get('webhook') and sched.get('schedule') != 'on':
        todo.append('（可选）说「开启日报」：每天北京时间 9 点推飞书')
    return {'status': 'ok', 'lingxing': bool(lx.get('app_id')), 'saihu': '凭据已存，数据接入开发测试中' if sh.get('client_id') else False, 'patrol_stores': [f"{s.get('marketplace')}·{s.get('sid')}" for s in stores],
            'feishu': bool(fs.get('webhook')), 'cost_tables': {k: (v if isinstance(v, str) else '估算') for k, v in costs.items()},
            'patrol_config': (config_dir() / '巡检配置.yaml').is_file(), 'schedule': sched.get('schedule'),
            'guide': str(HERE.parents[1] / 'guide' / 'S5运营接入指南.html'),
            'templates': [str(HERE.parents[1] / 'templates' / n) for n in ('领星密钥.txt', '赛狐密钥.txt', '飞书配置.txt')], 'todo': todo}, 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('ip')
    p = sub.add_parser('lingxing')
    p.add_argument('--file')
    p.add_argument('--stores')
    p = sub.add_parser('saihu')
    p.add_argument('--file', required=True)
    p = sub.add_parser('youmaiyun')
    p.add_argument('--file', required=True)
    p = sub.add_parser('feishu')
    p.add_argument('--file', required=True)
    p = sub.add_parser('schedule')
    p.add_argument('action', choices=['on', 'off', 'status'])
    p = sub.add_parser('run-daily')
    p.add_argument('--window', action='store_true', help='只在北京时间 9–12 点且当天没推过时运行（Windows 任务计划用）')
    p.add_argument('--dry', action='store_true', help='只预演，不发飞书')
    sub.add_parser('log')
    p = sub.add_parser('route')
    p.add_argument('--root')
    p.add_argument('--set', choices=sorted(ROUTES))
    p = sub.add_parser('check')
    p.add_argument('--root')
    p.add_argument('--skip-feishu', action='store_true')
    args = ap.parse_args(argv)
    fn = {'status': cmd_status, 'ip': cmd_ip, 'lingxing': cmd_lingxing, 'saihu': cmd_saihu, 'youmaiyun': cmd_youmaiyun, 'feishu': cmd_feishu, 'schedule': cmd_schedule,
          'run-daily': cmd_run_daily, 'log': cmd_log, 'route': cmd_route, 'check': cmd_check}[args.cmd]
    out, code = fn(args)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())
