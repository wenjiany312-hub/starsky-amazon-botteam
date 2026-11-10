"""Knowledge presentation and natural-language routes; never fabricate MCP results."""
import argparse
import json
import re
from pathlib import Path

import yaml
import kb_plan

INTRO = '''【内置工具卡】
| 工具 | 自动使用位置 | 随时可问 |
|---|---|---|
| 选品库 | S1 定模式、需求、难度、货源、成本与合规 | 选品方法：难度怎么判 / 按方法论评一下这个品 |
| 广告库 | S4 广告、S5 诊断（按当前步骤计划） | 广告知识库：ACOS 高怎么办 |
| 产品推广库 | S1 市场穿透、S4/S5（按当前步骤计划） | 推广手册：断货重推 / 老品反超 / 排名恢复 |
| 1688 货源辅助 | S1 第 5、6 步 | 1688 找货：关键词 |
知识库状态：检查三库；异常时运行「检测星空配置」。方法阈值是判断依据，不能代替实时市场数据与供应商核实。'''


def intro_once(marker, explicit=False):
    """Acknowledge actual delivered introduction, not a speculative preflight."""
    marker = Path(marker)
    if marker.is_file() and not explicit: return ''
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'shown': True, 'tools':['选品库','广告库','产品推广库','1688']}, ensure_ascii=False), encoding='utf8')
    return INTRO


def unavailable_kind(evidence, amendment=None):
    """知识库降级的真实原因分类：not_called 没调用 / unarchived 调用了但回包没能入档 / stale_version 调用连着旧版库 / tool_error 调用返回报错。
    有补记时以补记里核对过的分类为准（原登记保持原样，不改写）。"""
    if amendment:
        states = {c['corrected']['status'] for c in amendment.get('corrections', [])}
        if 'called_stale_version' in states: return 'stale_version'
        if states & {'called_reply_unverifiable', 'called_reply_archived_late'}: return 'unarchived'
        if 'called_error' in states: return 'tool_error'
        if 'pending' in states: return 'pending'
        if 'record_unsupported' in states: return 'record_unknown'
        return 'not_called'
    errors = [str(r.get('error') or '') for r in evidence.get('calls', []) if r.get('status') == 'error']
    if not errors or not any(errors): return 'record_unknown'
    if errors and all(e.startswith('本步没有调用') for e in errors): return 'not_called'
    if errors and all(e.startswith('本步没有调用') or '无法核验' in e for e in errors): return 'unarchived'
    return 'tool_error'


UNAVAILABLE_LEAD = {
    'not_called': '本步知识库没有调用成功',
    'unarchived': '本步知识库已调用，但回包没能自动归档，不能作为依据',
    'stale_version': '本步知识库已调用，但当时连着旧版知识库，不能作为新版依据',
    'tool_error': '本步知识库调用返回了报错',
    'pending': '本步知识库调用仍在等待完整回包',
    'record_unknown': '本步知识库记录无法完整核验，请检查记录格式与补记 Hash',
}


def card_lines(root, home, stage, number, checked=None):
    plan = kb_plan.plan_step(root, home, stage, number)
    if not plan['calls'] and not plan.get('reuse_step'): line = '📚 本步无知识库'
    elif checked and checked.get('status')=='pass' and checked.get('knowledge_status')=='used':
        refs = '、'.join(kb_plan.LIBRARIES[row['server']][1]+' '+row['card_id'] for row in checked['refs'])
        phases = list(dict.fromkeys(c['args'].get('selection_phase') or c['args'].get('scene') or c['args'].get('lifecycle_stage') for c in plan['calls']))
        line = '📚 本步知识库：'+ ' / '.join(x for x in phases if x)+' · 依据 '+refs+f" · ✓ 已引用 {checked['card_count']} 张"
    elif checked and checked.get('status')=='pass' and checked.get('knowledge_status')=='unavailable':
        kind = _kind_for_step(root, home, stage, number)
        line = ('📚 本步知识库：⚠ 未接通，已提示并继续；建议运行「检测星空配置」' if kind in ('not_called', 'tool_error') else
                '📚 本步知识库：⚠ ' + UNAVAILABLE_LEAD[kind] + '；已提示并继续（原登记保留，说明见补记）')
    elif plan.get('reuse_step'): line = f"📚 本步知识库：引用本轮第{plan['reuse_step']}步；⚠ 依据尚未核验"
    else: line = '📚 本步知识库：⚠ 本步该调用但未调用或证据未通过'
    if checked and checked.get('status')=='pass' and checked.get('fallback_notices'):
        line += '\n'+'\n'.join(checked['fallback_notices'])
    if '1688_scraper' in plan['tools']: line += '\n🔧 本步内置工具：1688 货源辅助'
    if plan.get('reuse_step'): line += f"\n依据来源：本轮第{plan['reuse_step']}步，未重复调用。"
    if plan.get('observations'):
        from kb_observations import plan as observation_plan
        observation=(checked or {}).get('observations') or {}
        if observation.get('observation_status')=='unavailable':
            line += '\n⚠ 回写未完成：'+'；'.join(observation['failure_reasons'])
        else: line += '\n'+observation_plan(root,home,stage,number)['notice']
    return line


def _kind_for_step(root, home, stage, number):
    try:
        plan = kb_plan.plan_step(root, home, stage, number)
        evidence = kb_plan.load(kb_plan.inside(root, plan['evidence_path']))
        import kb_amend
        return unavailable_kind(evidence, kb_amend.latest(root, home, stage, number))
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return 'record_unknown'


def enrich_report(md, root, home, stage, number, *, evidence=None, defer_verification=False):
    """Called before the native report hash is computed. Actual card use is author supplied."""
    plan=kb_plan.plan_step(root,home,stage,number)
    source_step=plan.get('reuse_step',number)
    path = kb_plan.inside(root,kb_plan.evidence_path(stage,source_step,root))
    inline = evidence is not None
    if evidence is None:
        if not path.is_file(): return md  # Standalone reports remain usable; controller enforces step evidence.
        evidence = kb_plan.load(path)
    deferred = defer_verification or evidence.get('verification_status') == 'pending_stage'
    gaps = kb_plan.fallback_gaps(root, evidence, verify_integrity=not deferred)
    if gaps:
        md = '\n'.join(dict.fromkeys(g['notice'] for g in gaps))+'\n\n'+md
        # Per-step technical ledger; do not call kb_record_observation or mutate original KB.
        if inline or deferred:
            # The caller persists this object in its step receipt; no sidecar.
            evidence['technical_gaps'] = gaps
        else:
            ledger = path.with_name('technical_gaps.yaml')
            content = yaml.safe_dump({'schema_version':1,'gaps':gaps},allow_unicode=True,sort_keys=False)
            if not ledger.is_file() or ledger.read_bytes() != content.encode('utf8'):
                ledger.write_text(content,encoding='utf8')
    if evidence.get('status')=='unavailable':
        reason = str(evidence.get('reason') or '').strip()
        if not reason: raise ValueError('知识库降级缺原因')
        amendment = None
        try:
            import kb_amend
            amendment = kb_amend.latest(root, home, stage, source_step)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            amendment = {'corrections': [{'corrected': {'status': 'record_unsupported'}}]}
        kind = unavailable_kind(evidence, amendment)
        # The opening words are a checked contract; the real class is added after the reason so it is never lost.
        actual = f'【实际情况：{UNAVAILABLE_LEAD[kind]}】' if kind != 'not_called' else ''
        extra = f"（补记 {amendment['revision']}：{amendment['summary']}）" if amendment and "revision" in amendment else ''
        md = f'本步未能调用知识库：{reason}{actual}{extra}；建议运行『检测星空配置』\n\n'+md
    lines=[]
    for row in evidence.get('calls',[]):
        ids=row.get('card_ids') or []
        if not ids: continue
        usage=str(row.get('usage') or '').strip()
        if not usage: raise ValueError('知识卡必须填写实际用途，不能自动编造用途')
        name=kb_plan.LIBRARIES[row['server']][1]
        rendered_usage=usage if usage.startswith('用于') else '用于'+usage
        lines.extend(f'- {name} {cid}：{rendered_usage}' for cid in ids)
    if lines:
        # A re-rendered report already ending in a basis section gets one merged section, not a second one
        # (R06.1: the shared market report carried two). Cards cited before keep their line unless re-cited.
        match=re.search(r'(?ms)^##[ \t]+知识库依据[ \t]*\n(.*)\Z',md)
        if match and not re.search(r'(?m)^## ',match.group(1)):
            card=lambda line:tuple(line.split('：',1)[0].split()[1:3])
            cited={card(line) for line in lines}
            kept=[line for line in match.group(1).splitlines() if line.startswith('- ') and card(line) not in cited]
            md=md[:match.start()];lines=kept+lines
        lines=list(dict.fromkeys(lines))
        # R09: readers asked for the card list folded; the heading and lines stay for the verifiers.
        md=(md.rstrip()+'\n\n## 知识库依据\n\n<details><summary>本报告引用了 '+str(len(lines))+' 张知识卡（点开查看用途）</summary>\n\n'
            +'\n'.join(lines)+'\n\n</details>\n')
    return md


def manual_calls(command, root, home):
    command=command.strip(); calls=[]
    def add(server,tool,args):
        kb_plan.validate_args(kb_plan.schemas(home,server)[tool],args)
        calls.append({'server':server,'library':kb_plan.LIBRARIES[server][1],'tool':tool,'args':args})
    if command=='知识库状态':
        for server in kb_plan.LIBRARIES: add(server,'kb_status',{})
    elif command.startswith('选品方法'):
        add('selection_knowledge_base','kb_query',{'text':command.partition('：')[2] or command})
    elif command=='按方法论评一下这个品':
        calls=[c for c in kb_plan.plan_step(root,home,'S1',3)['calls'] if c['tool']=='kb_scenario_rules']
    elif command.startswith('广告知识库'):
        add('ads_knowledge_base','kb_diagnose',{'symptom':command.partition('：')[2] or command})
    elif command.startswith('推广手册'):
        scene=command.partition('：')[2].strip()
        add('promotion_knowledge_base','kb_playbook',{'scene':scene})
    elif command.startswith('1688 找货'):
        keyword=command.partition('：')[2].strip()
        if not keyword: raise ValueError('请提供1688找货关键词')
        calls=[{'server':'1688_scraper','library':'1688 货源辅助','tool':'search_offers','args':{'keyword':keyword}}]
    else: raise ValueError('不是内置工具口令')
    return calls


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root');p.add_argument('--home',default=str(Path(__file__).resolve().parents[1]));p.add_argument('--command')
    p.add_argument('--intro',action='store_true');p.add_argument('--ack-intro',action='store_true');p.add_argument('--explicit',action='store_true')
    p.add_argument('--marker',default=str(Path.home()/'.starsky-codex/ui/builtin-tools.json'))
    a=p.parse_args()
    if a.intro:
        if a.ack_intro: text=intro_once(a.marker,a.explicit)
        else: text=INTRO if a.explicit or not Path(a.marker).is_file() else ''
        print(text)
    else:
        if not a.root or not a.command: p.error('--root 和 --command 必填')
        print(json.dumps({'calls':manual_calls(a.command,a.root,a.home),'notice':'逐条实际调用，引用库名+卡号；知识库状态异常时运行检测星空配置。'},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
