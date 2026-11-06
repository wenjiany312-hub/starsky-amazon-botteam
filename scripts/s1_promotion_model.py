#!/usr/bin/env python3
"""S1 M2 step7: render the cost engine's CPC requirements, never recalculate them."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import yaml

HOME=Path(__file__).resolve().parents[1]
OUT=Path('S1_选品板块/amazon-market-research')
MD='06_推广难度.md'
MODEL='06_promotion_model.yaml'


def _module(name):
    path=(HOME/'scripts'/f'{name}.py').resolve()
    key='starsky_promotion_'+name+'_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if key not in sys.modules:
        spec=importlib.util.spec_from_file_location(key,path)
        module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module)
    return sys.modules[key]


# R09: a reader wants the answer first. Required click conversion to break even at the keyword CPC:
# <= GOOD 好推, <= HARD 有压力, above 难推 (new listings usually convert about 8-12 % of ad clicks).
GOOD, HARD = 0.08, 0.15


def _scenario_parts(label):
    """'L压缩包 $19.99' -> ('L压缩包', 19.99); other labels keep a single column."""
    import re
    match = re.match(r'^(.*?)\s*\$\s*([0-9.]+)\s*$', str(label or ''))
    return (match[1].strip(), float(match[2])) if match else (str(label or '主口径'), None)


def summary(model):
    """Plain-language head of the report: one sentence, one traffic-light table, what to confirm next."""
    analysis = model.get('analysis') or {}
    bases = [b for b in analysis.get('bases') or [] if b.get('realized_price')]
    keywords = [k for k in (model.get('promotion') or {}).get('keywords') or [] if k.get('cpc')]
    if not bases or not keywords:
        return ''
    core = max(keywords, key=lambda k: (bool(k.get('core')), k.get('search_volume') or 0))
    same = sorted({k['cpc'] for k in keywords if k.get('keyword') == core.get('keyword')}) or [core['cpc']]
    low, high = same[0], same[-1]
    def need(base, cpc):
        pre = base.get('pre_ad_profit')
        return None if pre is None or pre <= 0 else cpc / pre
    def light(value):
        if value is None: return '🔴 亏'
        if value >= 1: return '🔴 做不到'
        return ('🟢 ' if value <= GOOD else '🟡 ' if value <= HARD else '🔴 ') + f'{value*100:.0f}%'
    rows, prices = {}, []
    for base in bases:
        name, price = _scenario_parts(base['label'])
        rows.setdefault(name, {})[price] = base
        if price not in prices: prices.append(price)
    prices = sorted(prices, key=lambda p: (p is None, p or 0))
    names = list(rows)
    cpc_text = f'${low:.2f}' if low == high else f'${low:.2f}–{high:.2f}'
    good = [(n, p) for n in names for p in prices if rows[n].get(p) is not None and (need(rows[n][p], high) or 9) <= HARD]
    first = min(good, key=lambda x: (x[1] or 0, need(rows[x[0]][x[1]], high))) if good else None
    if first:
        verdict = (f"按核心词「{core.get('keyword')}」的竞价 {cpc_text}，最先推得动的是 **{first[0]}"
                   + (f" 卖 ${first[1]:.2f}" if first[1] else '') + f"**：广告点击里约 {need(rows[first[0]][first[1]], high)*100:.0f}% 下单就不亏。")
    else:
        verdict = f"按核心词「{core.get('keyword')}」的竞价 {cpc_text}，所有方案都要很高的转化率才不亏，**推广压力大**；先压成本或提价再看。"
    floor = prices[0] if prices and prices[0] else None
    if floor and first and first[1] != floor:
        best = min((need(rows[n][floor], high) for n in names if rows[n].get(floor) and need(rows[n][floor], high)), default=None)
        if best: verdict += f' 卖 ${floor:.2f} 时最好的方案也要约 {best*100:.0f}%，推不动。'
    loss = [n for n in names if all(need(rows[n].get(p) or {}, high) is None for p in prices if rows[n].get(p))]
    if loss: verdict += ' ' + '、'.join(loss) + ' 广告前就不赚钱，不建议推。'
    head = ['## 一句话结论', '', verdict, '',
            f"## 不亏需要的点击转化率（按 CPC ${high:.2f}）", '',
            '🟢 ≤8% 好推　🟡 8–15% 有压力　🔴 >15% 难推或亏损。新品广告一般能做到 8–12%。', '',
            '| 方案 | ' + ' | '.join(f'${p:.2f}' if p else '当前价' for p in prices) + ' |',
            '|---|' + '---|' * len(prices)]
    for name in names:
        head.append(f'| {name} | ' + ' | '.join(light(need(rows[name][p], high)) if rows[name].get(p) else '—' for p in prices) + ' |')
    if low != high:
        head += ['', f"CPC 若按较低的 ${low:.2f} 算，所需转化率约为上表的 {low/high*100:.0f}%。"]
    head += ['', '## 下一步要确认', '',
             '- 实际 CPC：先小预算跑一周核心词，拿到真实点击单价。',
             '- 实际点击转化率：按尺码分别看，低于上表所需就要提价或压成本。',
             '- 表里的利润来自第 6 步成本测算，待确认的成本项一变，上表跟着变。', '']
    return '\n'.join(head)


def headline(unified):
    """One sentence for the S1 front card (R09): same answer as the report head, no table."""
    import re
    try:
        analysis = _module('s1_promotion_analysis').analyse(unified)
    except (KeyError, TypeError, ValueError):
        return None
    text = summary({'analysis': analysis, 'promotion': (unified or {}).get('promotion') or {}})
    match = re.search(r'## 一句话结论\n\n(.+?)\n', text)
    return match[1].replace('**', '').strip() if match else None


def render(model):
    promotion=model['promotion'];basis=promotion['basis'];barriers=promotion['barriers']
    lead=summary(model)
    lines=[*( [f"统一成本引擎主口径结论：{promotion['conclusion']}（主口径为最保守情景，上方按各方案逐套计算）。",''] if lead
              else ['# 推广难度卡','',f"结论：**{promotion['conclusion']}**。",''] ),
           f"单件毛利采用 {basis['variant_id']}：{basis['pre_ad_profit']:.4f}（{basis['source']}）" if basis.get('pre_ad_profit') is not None else '缺正的单件毛利，不给伪造转化要求。',
           '', '| 核心词 | 售价 | 月搜索量 | CPC | 所需转化率 | 保本CPC（给定CVR） | 相关性 | 来源 / 日期 | 判断 |',
           '|---|---:|---:|---:|---:|---:|---|---|---|']
    def num(v):return '待补' if v is None else f'{v:.2f}'
    for row in promotion['keywords']:
        required='待补' if row['required_cvr'] is None else f"{row['required_cvr']*100:.1f}%"
        lines.append(f"| {row.get('keyword','')} | {num(row.get('price'))} | {row.get('search_volume') or '待补'} | {num(row.get('cpc'))} | {required} | {num(row.get('break_even_cpc'))} | {row.get('relevance','待补')} | {row.get('source','未标')} / {row.get('observed_at','日期待补')} | {row['reason']} |")
    if not promotion['keywords']:lines.append('| 待取得核心词CPC | — | — | — | — | — | — | 未取得 | 不作可推结论 |')
    lines.extend(['','## 竞争壁垒','',
          '- 自然前5评论数中位数：'+str(barriers.get('organic_top5_review_median','待补（未取得自然前5证据）')),
          '- Top20中1年内新品占比：'+str(barriers.get('top20_under_1yr_share','待补（未取得上架日期证据）')),
          '', '所需转化率采用统一引擎输出 CPC ÷ 含预留单件毛利；售价与保守变体口径逐行注明。',
          '本卡是推广难度判断，不构成广告投放或采购授权。',''])
    from s1_readability import reader_projection
    from s1_cost_present import render_assumptions
    from s1_promotion_analysis import render as render_analysis
    analysis=render_analysis(model.get('analysis') or {})  # R06.1 AS-01 第 2 批
    detail=reader_projection('\n'.join(lines))+'\n'+(reader_projection(analysis)+'\n' if analysis else '')+render_assumptions(model.get('unified_cost') or {})
    if not lead:
        return detail
    # Readable layout runs before folding: reader_projection leaves <details> content untouched.
    return ('# 推广难度卡\n\n'+reader_projection(lead).rstrip()+'\n\n<details><summary>明细：关键词、各转化率下的保本 CPC、'
            '每单广告成本与 CPC 上限</summary>\n\n'+detail.rstrip()+'\n\n</details>\n')


def run(root):
    native=_module('r01_native')
    compact=native.execute_native(root,run,step=7,report_paths=[(OUT/MD).as_posix()])
    if compact is not NotImplemented:return compact
    root=Path(root).resolve();path=root/OUT/'05_cost_model.yaml'
    if not path.is_file():raise ValueError('先完成综合成本与定价，缺05_cost_model.yaml')
    raw=path.read_bytes();cost=yaml.safe_load(raw) or {};unified=cost.get('unified_cost')
    if not isinstance(unified,dict):raise ValueError('旧成本账未带统一推广模型；不将旧案自动迁移')
    current=_module('s1_cost_engine')
    freshness=current.validate_provenance(unified,root)
    if not freshness['ok']:raise ValueError('；'.join(freshness['issues']))
    model={'schema_version':'r01.promotion.1','generator':'s1_promotion_model.py',
           'promotion':unified['promotion'],'cost_model_sha256':hashlib.sha256(raw).hexdigest(),
           'engine_provenance':unified['provenance'],'fee_provenance':unified['fee_provenance']}
    model['unified_cost'] = unified
    model['analysis'] = _module('s1_promotion_analysis').analyse(unified)
    md=render(model);model['md_sha256']=hashlib.sha256(md.encode()).hexdigest()
    (root/OUT/MD).write_text(md,encoding='utf-8',newline='\n')
    for path in (root/OUT).glob('*推广难度.md'):
        if path.name != MD:
            original=path.read_text(encoding='utf-8-sig')
            path.write_text(_module('s1_readability').keep_history_once(md,original,'此前推广研究原文（仅供历史核对）'),encoding='utf8',newline='\n')
    (root/OUT/MODEL).write_text(yaml.safe_dump(model,allow_unicode=True,sort_keys=False),encoding='utf-8')
    return {'ok':True,'md':str(root/OUT/MD),'model':str(root/OUT/MODEL),'conclusion':unified['promotion']['conclusion']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True)
    args=parser.parse_args(argv)
    try:result=run(args.run)
    except (ValueError,FileNotFoundError) as error:print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));return 1
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
