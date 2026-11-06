#!/usr/bin/env python3
"""星空亚马逊 Bot Team · 构建脚本

用法：
  python3 build/build.py --platform grokbot --version 1.0.0-rc1 --edition internal
  python3 build/build.py --platform grokbot --version 1.0.0 --edition customer --license-id <授权ID>

edition:
  internal  = 老板自用：不检查授权；知识库原样带上（可能含自己店铺数据），包名带「内部自用」
  customer  = 客户版：必须给 --license-id；知识库不能有疑似 ASIN；默认 basic 档（去掉旗舰专属技能）
依赖：Python 3.9+、PyYAML
"""
import argparse, hashlib, json, os, re, shutil, sys, zipfile, datetime
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGSHIP_ONLY = {'amazon-ads-api-connector', 'amazon-sp-api-connector', 'amazon-ops-console'}
OWN_SKILLS = ['amazon-team-operating-model', 'amazon-mcp-onboarding', 'amazon-ai-person-disclosure', 'amazon-image-compliance-check', 'amazon-kb-query', 'amazon-data-hub']
COPY_DIRS = ['workflow', 'safety', 'skills', 'templates', 'scripts', 'knowledge', 'docs', 'config', 'license']
SECRET_PATTERNS = [r'sk-[A-Za-z0-9_\-]{20,}', r'AKIA[0-9A-Z]{16}', r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
                   r'(?i)(refresh_token|client_secret|app_secret|api_key)\s*[:=]\s*["\']?[A-Za-z0-9_\-\.]{16,}', r'Atzr\|[A-Za-z0-9_\-]{20,}']
ASIN = re.compile(r'\bB0[A-Z0-9]{8}\b')

def R(p): return open(os.path.join(ROOT, p), encoding='utf-8').read()
def Y(p): return yaml.safe_load(R(p))
def cp(src, dst):
    with open(src, 'rb') as i, open(dst, 'wb') as o: o.write(i.read())

def die(msg): print('❌ ' + msg); sys.exit(1)


def render_guide(out, a, team, kit, work):
    tpl_p = os.path.join(ROOT, 'docs', '使用说明.template.html')
    if not os.path.exists(tpl_p): return
    import html as H
    gp = os.path.join(ROOT, 'adapters', a.platform, 'guide.yaml')
    g = Y(f'adapters/{a.platform}/guide.yaml') if os.path.exists(gp) else {}
    cat = Y('mcp/third_party.yaml'); st = Y('config/team_settings.yaml')
    cn = {}
    for r in team['roles']: cn[r['id']] = Y(f"roles/{r['id']}/card.yaml")['name']
    mcp_by_role = {}
    for k, v in cat['mcps'].items():
        if v.get('required') == 'later': continue
        for rid in v.get('roles', []): mcp_by_role.setdefault(rid, []).append(v['name'])
    for k, v in (cat.get('local') or {}).items():
        for rid in v.get('roles', []): mcp_by_role.setdefault(rid, []).append(v['name'])
    prof = {k: (v or {}).get('profile', '') for k, v in ((st.get('models') or {}).get('roles') or {}).items()}
    rows = []
    for r in team['roles']:
        c = Y(f"roles/{r['id']}/card.yaml")
        rows.append(f"<tr><td><b>{H.escape(c['name'])}</b></td><td>{H.escape(c['description'])}</td><td>{'、'.join(mcp_by_role.get(r['id'], [])) or '—'}</td><td>{prof.get(r['id'], '')}</td></tr>")
    req = {'recommended': '推荐', 'optional': '可选'}
    mrows = []
    for k, v in cat['mcps'].items():
        if v.get('required') == 'later': continue
        link = v.get('get_key', '')
        link_html = f"<a href='{link}'>打开</a>" if str(link).startswith('http') else H.escape(str(link))
        if v.get('buy'): link_html += f" ｜ <a href='{v['buy']}'>渠道购买</a>"
        mrows.append(f"<tr><td><b>{v['name']}</b></td><td>{req.get(v.get('required'), '')}</td><td>{H.escape(v.get('use', ''))}</td><td>{'、'.join(cn.get(x, x) for x in v.get('roles', []))}</td><td>{link_html}</td></tr>")
    names = {'grokbot': 'Grok Bot', 'openbot': 'OpenBot', 'starsky': 'StarSky（DSH）'}
    rep = {'{VER}': a.version, '{PLATFORM_NAME}': names.get(a.platform, a.platform), '{EDITION}': '内部自用版' if a.edition == 'internal' else '客户版',
           '{ROLE_ROWS}': '\n'.join(rows), '{MCP_ROWS}': '\n'.join(mrows), '{DATE}': datetime.date.today().isoformat(),
           '{APPROVAL_WHERE}': g.get('approval_where', ''), '{NAME_WHERE}': g.get('name_where', ''), '{SECRET_WHERE}': g.get('secret_where', ''),
           '{MODEL_NOTE}': g.get('model_note', ''), '{MODEL_SECTION}': g.get('model_section', ''), '{MCP_PLATFORM_NOTE}': g.get('mcp_platform_note', ''),
           '{SAFETY_EXTRA}': g.get('safety_extra', '')}
    t = open(tpl_p, encoding='utf-8').read()
    for k, v in rep.items(): t = t.replace(k, v)
    open(os.path.join(out, '使用说明.html'), 'w', encoding='utf-8').write(t)
    tp = os.path.join(out, 'docs', '使用说明.template.html')
    if os.path.exists(tp): os.remove(tp)

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--platform', required=True, choices=['grokbot', 'openbot', 'starsky'])
    ap.add_argument('--version', required=True)
    ap.add_argument('--edition', default='internal', choices=['internal', 'customer'])
    ap.add_argument('--tier', default=None, choices=['basic', 'flagship'])
    ap.add_argument('--license-id', default='')
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist'))
    a = ap.parse_args(argv)
    tier = a.tier or ('flagship' if a.edition == 'internal' else 'basic')
    if a.edition == 'customer' and not a.license_id: die('客户版必须给 --license-id')
    team = Y('team.yaml'); adapter = Y(f'adapters/{a.platform}/adapter.yaml')
    kit = adapter.get('paths', {}).get('kit', '{KIT}'); work = adapter.get('paths', {}).get('work', '{WORK}')
    runtime = R(f'adapters/{a.platform}/runtime.md') if os.path.exists(os.path.join(ROOT, f'adapters/{a.platform}/runtime.md')) else ''
    pkg = f"starsky-botteam_{a.platform}_v{a.version}" + ('_internal' if a.edition == 'internal' else '')
    out = os.path.join(a.out, pkg)
    if os.path.exists(out): shutil.rmtree(out)
    os.makedirs(out)
    warns, errs = [], []
    # ---- copy shared dirs ----
    for d in COPY_DIRS:
        src = os.path.join(ROOT, d)
        if not os.path.exists(src): continue
        for dp, dn, fn in os.walk(src):
            dn[:] = [x for x in dn if x not in ('.versions', '.metadata', '__pycache__', '.git')]
            rel = os.path.relpath(dp, ROOT)
            if d == 'skills' and tier == 'basic':
                top = rel.split(os.sep)[1] if len(rel.split(os.sep)) > 1 else ''
                if top in FLAGSHIP_ONLY: continue
            os.makedirs(os.path.join(out, rel), exist_ok=True)
            for f in fn:
                if f in ('.DS_Store',) or f.endswith('.pyc') or f == '.env': continue
                cp(os.path.join(dp, f), os.path.join(out, rel, f))
    # mcp: local + lists
    for sub in ['mcp']:
        src = os.path.join(ROOT, sub)
        for dp, dn, fn in os.walk(src):
            dn[:] = [x for x in dn if x not in ('__pycache__', '.git', '.versions')]
            rel = os.path.relpath(dp, ROOT); os.makedirs(os.path.join(out, rel), exist_ok=True)
            for f in fn:
                if f == '.env' or f.endswith('.pyc') or f == '.DS_Store': continue
                cp(os.path.join(dp, f), os.path.join(out, rel, f))
    # ---- settings override ----
    st = Y('config/team_settings.yaml')
    for k, v in (adapter.get('settings_override') or {}).items():
        cur = st
        ks = k.split('.')
        for kk in ks[:-1]: cur = cur.setdefault(kk, {})
        cur[ks[-1]] = v
    st['platform'] = a.platform
    open(os.path.join(out, 'config/team_settings.yaml'), 'w', encoding='utf-8').write(yaml.safe_dump(st, allow_unicode=True, sort_keys=False))
    # ---- agents ----
    os.makedirs(os.path.join(out, 'agents'), exist_ok=True)
    have = {d for d in os.listdir(os.path.join(out, 'skills')) if os.path.isdir(os.path.join(out, 'skills', d))}
    skill_users = {}
    rows = []
    wm = f"<!-- starsky-botteam {a.version} · {a.platform} · {a.edition} · {a.license_id or '内部自用'} -->"
    for r in team['roles']:
        rid = r['id']; card = Y(f'roles/{rid}/card.yaml'); soul = R(f'roles/{rid}/soul.md')
        sk = [s for s in card['skills'] if not (tier == 'basic' and s in FLAGSHIP_ONLY)]
        for s in sk:
            skill_users.setdefault(s, []).append(card['name'])
            if s not in have: (errs if a.edition == 'customer' else warns).append(f'{card["name"]} 用到的技能 {s} 不在包里')
        rt = runtime.replace('{KIT}', kit).replace('{WORK}', work).replace('{ROLE}', rid)
        body = soul.replace('{{KIT}}', kit).replace('{{WORK}}', work)
        md = f"""---\nid: {rid}\nname: {card['name']}\njob: {card['job']}\nskills: [{', '.join(sk)}]\n---\n\n# {card['name']} · 档案\n\n## 档案卡（创建时照填）\n| 项 | 内容 |\n|---|---|\n| Name | {card['name']} |\n| Job | {card['job']} |\n| Description | {card['description']} |\n\n## 系统提示词（作为第一条消息发给这个角色）\n\n{rt}\n{body}\n\n{wm}\n"""
        open(os.path.join(out, 'agents', f'{rid}.md'), 'w', encoding='utf-8').write(md)
        rows.append(f"| {card['name']} | `agents/{rid}.md` | {card['job']} |")
    # KIT/WORK placeholders: own skills, docs, mcp notes, readme
    targets = [os.path.join(out, 'skills', s) for s in OWN_SKILLS] + [os.path.join(out, 'docs'), os.path.join(out, 'mcp')]
    for tgt in targets:
      for dp, dn, fn in os.walk(tgt):
        dn[:] = [x for x in dn if x != 'local']
        for f in fn:
            if f.endswith(('.md', '.txt')):
                p = os.path.join(dp, f); t = open(p, encoding='utf-8').read()
                if '{{KIT}}' in t or '{{WORK}}' in t:
                    open(p, 'w', encoding='utf-8').write(t.replace('{{KIT}}', kit).replace('{{WORK}}', work))
    # ---- SETUP ----
    stbl = []
    for s in sorted(skill_users):
        mode = '全文' if s in OWN_SKILLS else '指针'
        state = '' if s in have else '（⚠️ 包里没有，记为缺失）'
        stbl.append(f"| `{s}`{state} | {mode} | {'、'.join(dict.fromkeys(skill_users[s]))} |")
    missing = [s for s in skill_users if s not in have]
    miss_line = ('⚠️ 下面这些技能角色用到了、但**不在包里**，搭建时记为「缺失」列给老板：' + '、'.join(missing)) if missing else '角色用到的技能本包全部包含。'
    setup_tpl = os.path.join(ROOT, f'adapters/{a.platform}/SETUP.md')
    if os.path.exists(setup_tpl):
        s = open(setup_tpl, encoding='utf-8').read()
        rep = {'{VER}': a.version, '{EDITION}': '内部自用版' if a.edition == 'internal' else '客户版', '{PKG}': pkg, '{KIT}': kit, '{WORK}': work,
               '{ROLE_TABLE}': '\n'.join(rows), '{SKILL_TABLE}': '\n'.join(stbl), '{MISSING_LINE}': miss_line,
               '{OWN_SKILLS}': '、'.join(f'`{x}`' for x in OWN_SKILLS)}
        for k, v in rep.items(): s = s.replace(k, v)
        open(os.path.join(out, 'SETUP.md'), 'w', encoding='utf-8').write(s)
    else:
        warns.append(f'adapters/{a.platform}/SETUP.md 还没写，只带了通用 BOOTSTRAP.md')
    for f in ['BOOTSTRAP.md', 'team.yaml', '00_先读我.md']: cp(os.path.join(ROOT, f), os.path.join(out, f))
    for f in [f'adapters/{a.platform}/adapter.yaml', f'adapters/{a.platform}/runtime.md']:
        if os.path.exists(os.path.join(ROOT, f)):
            os.makedirs(os.path.join(out, 'adapter'), exist_ok=True); cp(os.path.join(ROOT, f), os.path.join(out, 'adapter', os.path.basename(f)))
    render_guide(out, a, team, kit, work)
    # ---- checks ----
    st_states = Y('workflow/sku_states.yaml')['states']
    bad = [(k, n) for k, v in st_states.items() for n in v.get('next', []) if n not in st_states and not str(n).startswith('恢复')]
    if bad: errs.append(f'状态机里有不存在的下一个状态：{bad}')
    for dp, dn, fn in os.walk(out):
        for f in fn:
            if f.endswith(('.md', '.yaml', '.yml', '.json', '.txt', '.py', '.toml', '.csv', '.html', '.env.example')):
                p = os.path.join(dp, f)
                try: t = open(p, encoding='utf-8', errors='ignore').read()
                except Exception: continue
                for pat in SECRET_PATTERNS:
                    m = re.search(pat, t)
                    if m and 'example' not in f and 'test' not in p.lower() and 'fixture' not in p.lower():
                        errs.append(f'疑似密钥：{os.path.relpath(p, out)} → {m.group(0)[:12]}…')
                        break
    # 客户版只在打包副本里脱敏疑似 ASIN，源码知识库保持原样
    asin_redacted = 0
    if a.edition == 'customer':
        for dp, dn, fn in os.walk(os.path.join(out, 'mcp', 'local')):
            for f in fn:
                if f.endswith(('.md', '.yaml', '.json', '.csv', '.txt')):
                    p = os.path.join(dp, f)
                    t = open(p, encoding='utf-8', errors='ignore').read()
                    t2, n = ASIN.subn('B0-REDACTED', t)
                    if n:
                        open(p, 'w', encoding='utf-8').write(t2)
                        asin_redacted += n
        if asin_redacted:
            warns.append(f'客户版已脱敏 {asin_redacted} 处疑似 ASIN（只改打包副本）')
    asin_hits = 0
    for dp, dn, fn in os.walk(os.path.join(out, 'mcp', 'local')):
        for f in fn:
            if f.endswith(('.md', '.yaml', '.json', '.csv', '.txt')):
                asin_hits += len(ASIN.findall(open(os.path.join(dp, f), encoding='utf-8', errors='ignore').read()))
    if asin_hits:
        (errs if a.edition == 'customer' else warns).append(f'知识库里有 {asin_hits} 处疑似 ASIN（客户版必须脱敏）')
    if errs:
        for e in errs: print('❌', e)
        die('构建未通过')
    # ---- watermark, readme, checksums, zip ----
    open(os.path.join(out, 'license', 'watermark.txt'), 'w', encoding='utf-8').write(
        json.dumps({'product': 'starsky-botteam', 'version': a.version, 'platform': a.platform, 'edition': a.edition, 'tier': tier,
                    'license_id': a.license_id or 'internal', 'built_at': datetime.datetime.now().isoformat(timespec='seconds')}, ensure_ascii=False, indent=2))
    lines = []
    for dp, dn, fn in os.walk(out):
        for f in sorted(fn):
            if f == 'CHECKSUMS.txt': continue
            p = os.path.join(dp, f)
            lines.append(f"{hashlib.sha256(open(p, 'rb').read()).hexdigest()}  {os.path.relpath(p, out)}")
    open(os.path.join(out, 'CHECKSUMS.txt'), 'w', encoding='utf-8').write('\n'.join(sorted(lines, key=lambda x: x.split('  ')[1])) + '\n')
    z = out + '.zip'
    if os.path.exists(z): os.remove(z)
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dp, dn, fn in os.walk(out):
            for f in fn:
                p = os.path.join(dp, f); zf.write(p, os.path.relpath(p, a.out))
    rep = {'package': os.path.basename(z), 'files': len(lines), 'zip_bytes': os.path.getsize(z), 'tier': tier, 'warnings': warns}
    open(out + '.build.json', 'w', encoding='utf-8').write(json.dumps(rep, ensure_ascii=False, indent=2))
    print(json.dumps(rep, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
