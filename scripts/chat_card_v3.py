"""Receipt-only, host-neutral V3 cards. No HTML or invented tool activity."""
from pathlib import Path
import re

CONTROLLERS = {'starsky-amazon-agent', 'starsky-fullchain-workflow'}

def clean_text(value):
    text = str(value or '')
    text = re.sub(r'<(oai-[\w-]+)\b[^>]*>.*?</\1\s*>', '', text, flags=re.S|re.I)
    text = re.sub(r'</?oai-[^>]*>', '', text, flags=re.I)
    return ' '.join(text.split())

def _limited(items, count=3, unit='个'):
    return ' · '.join(items[:count]) + (f' 等 {len(items)} {unit}' if len(items)>count else '')

def _links(artifacts, root):
    rows=[]
    entries=artifacts.values() if isinstance(artifacts,dict) else artifacts or []
    for item in entries:
        value=item.get('path') if isinstance(item,dict) else item
        if not value: continue
        path=Path(value)
        if path.name.startswith('_NEXT_'): continue  # Handoffs have their own acceptance-gated entry.
        if path.suffix.lower() not in {'.md','.html','.xlsx'}: continue
        target=path if path.is_absolute() else Path(root)/path
        label=str(item.get('label') or path.name) if isinstance(item,dict) else path.name
        label=label.replace('[','').replace(']','')
        rows.append(f'[{label}](<{target}>)')
    rows=list(dict.fromkeys(rows))
    # A card is not a file index: 3.0.23 showed 19 links on one card when earlier files were swept in.
    # Eight still lists every supplier report of a normal step-5 shortlist.
    if len(rows)>8:
        return ' · '.join(rows[:8])+f' 等 {len(rows)} 份'
    return ' · '.join(rows)

def render_card(receipt, *, stage, number, total, title, model, next_model,
                next_text, root, acceptance=None, delivery_step=None):
    final=number==(delivery_step or total)
    failed=receipt.get('outcome')=='blocked' or (final and not (acceptance or {}).get('accepted'))
    waiting=receipt.get('outcome')=='waiting_external'
    status='⏳' if waiting else '⚠️' if failed else '✅'
    title=f'{stage} · {number}/{total} {clean_text(title)}'
    if waiting: title += ' · 等待回填'
    if final and failed:
        count=(acceptance or {}).get('count')
        title += (' · '+clean_text(acceptance['label']) if stage=='S2' and (acceptance or {}).get('label') else ' · 需修改 '+str(count)+' 处' if stage=='S2' and count is not None
                  else ' · 验收未通过' + (f'（{count} 项）' if count is not None else '（待核验）'))
    lines=[f'{status} **{title}**　'+('■'*number+'□'*(total-number))]
    summary=clean_text(receipt.get('summary')) or '本步结果见报告。'
    lines.append('🎯 结论：'+summary[:40]+('…' if len(summary)>40 else ''))
    nums=receipt.get('key_numbers') or []
    if isinstance(nums,dict): nums=[{'label':k,'value':v} for k,v in nums.items()]
    if nums:
        lines.append('📊 关键数：'+_limited([clean_text(f"{x.get('label','')} {x.get('value','')}") if isinstance(x,dict) else clean_text(x) for x in nums]))
    skills=receipt.get('skills_called',receipt.get('skills_used',[])) or []
    skills=[clean_text(x.get('name') if isinstance(x,dict) else x) for x in skills]
    skills=list(dict.fromkeys(x for x in skills if x and x not in CONTROLLERS))
    if skills: lines.append('🧩 Skill：'+' · '.join(skills))
    calls=[]
    for c in receipt.get('mcp_calls') or []:
        if not c.get('server') or not c.get('tool'): continue
        server,tool=clean_text(c['server']),clean_text(c['tool'])
        if c.get('ok') is True:
            count=c.get('count',1)
            if not isinstance(count,int) or isinstance(count,bool) or count<1: continue
            calls.append(f'{server}（{tool} ×{count}）')
        elif c.get('ok') is False:
            calls.append(f"{server} ✗ {tool}（{clean_text(c.get('error'))[:40] or '调用失败'}）")
    if calls: lines.append('🔌 MCP：'+_limited(calls))
    cards=[]
    for c in receipt.get('kb_cards') or []:
        if isinstance(c,dict):
            cid=c.get('card_id') or c.get('id')
            if cid: cards.append(clean_text(f"{c.get('library','')} {cid}"))
        elif c: cards.append(clean_text(c))
    if cards: lines.append('📚 知识库：'+_limited(list(dict.fromkeys(cards)),unit='张'))
    tools=[clean_text(t.get('label') or t.get('name')) if isinstance(t,dict) else clean_text(t) for t in receipt.get('tools_run') or []]
    if tools: lines.append('🔧 内置工具：'+_limited(tools))
    links=_links(receipt.get('reports') or receipt.get('artifacts') or {},root)
    if links: lines.append('📦 报告：'+links)
    next_model=clean_text(next_model)
    model=clean_text(model)
    hint='下一步沿用' if not next_model or model==next_model else '下一步建议调到 '+next_model
    lines.append(f'🤖 模型：本步 {model} ｜ {hint}')
    action=clean_text(receipt.get('needs_user'))
    decision=receipt.get('decision') if isinstance(receipt.get('decision'),dict) else {}
    if (action and receipt.get('outcome')!='blocked' and decision.get('source')=='user' and decision.get('selection')
            and re.search(r'选\s*[1一]\s*个|选择|选一|回复\s*\d|请选|任选',action)):
        action=''  # R06.1 AS-03：用户已经选过，卡片不能再请他选；过期的提示会逼助手改卡
    if final and failed:
        issues=[clean_text(x) for x in (acceptance or {}).get('issues',[])[:3]]
        next_text='补齐数据'+('：'+'；'.join(issues) if issues else '，重新验收')
        action=''
        if stage == 'S2':
            count=(acceptance or {}).get('count')
            next_text=(acceptance or {}).get('next') or '回到第 6 步修改文案，再执行当前合规与审阅'
            action=(acceptance or {}).get('action') or (f'按修改清单改 {count} 处' if count is not None else '查看修改清单并重新核验')
    tail='👉 下一步：'+clean_text(next_text)
    if action: tail += ' ｜ 🙋 你要做：'+action
    if final and stage in {'S1','S2','S3','S4'} and acceptance and acceptance.get('accepted'):
        tail+=' ｜ 阶段验收：'+('先不验收（已记录）' if acceptance.get('review_pending') else '通过 / 带后补通过 / 先不验收')
    lines.append(tail)
    text='\n'.join(lines)+'\n'
    if stage in {'S1','S2','S3','S4'}:
        import importlib.util
        path=Path(__file__).with_name('ui_terms.py')
        if path.is_file():
            spec=importlib.util.spec_from_file_location('_card_ui_terms',path);terms=importlib.util.module_from_spec(spec);spec.loader.exec_module(terms);text=terms.rewrite(text)
    return text
