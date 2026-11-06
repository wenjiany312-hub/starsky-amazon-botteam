"""Shared cost assumptions and native price scenarios for steps 6/7/9."""
from s1_readability import reader_projection
from s1_cost_engine import FIELD_LABELS, STATUS_LABELS


def promotion_summary(unified):
    """R06.1 PL-08：推广结论按已经算出的东西说话。

    能算出保本所需转化率就给条件结论，并列出仍待核实的项目；一个都算不出时才说“数据不足”，同时说清缺什么。
    以前只要有任何一项费用或竞品壁垒缺失，就整句写“数据不足，暂不判断”，卡片下面却列着算好的转化率。"""
    promotion = unified.get('promotion') or {}
    keywords = [k for k in promotion.get('keywords') or [] if k.get('cpc') is not None]
    pending = []
    missing = [FIELD_LABELS.get(k, k) for k in (unified.get('missing') or [])]
    if missing:
        pending.append('费用（' + '、'.join(missing[:3]) + ('等' if len(missing) > 3 else '') + '）')
    if (unified.get('main_basis') or {}).get('actual_profit') is None:
        pending.append('实际利润')
    barriers = promotion.get('barriers') or {}
    if any(barriers.get(k) is None for k in ('organic_top5_review_median', 'top20_under_1yr_share')):
        pending.append('竞争壁垒（自然前五评论中位数、Top20 新品占比）')
    main = unified.get('main_basis') or {}
    core = keywords[0] if keywords else None
    parts = []
    if core and main.get('kind') == 'user_condition':
        for row in main['scenarios']:
            profit = row['result'].get('pre_ad_profit')
            if profit and profit > 0:
                parts.append(f"{row['label']} {core['cpc'] / profit:.1%}")
            else:
                parts.append(f"{row['label']} 单件无广告前利润")
    elif core and core.get('required_cvr') is not None:
        parts.append(f"{core['required_cvr']:.1%}（{core.get('reason') or ''}）".replace('（）', ''))
    elif core and ((main.get('result') or {}).get('pre_ad_profit') or 0) > 0:
        # 费用有未知项时，按主口径（未核费用上界情景）的单件利润给条件值
        parts.append(f"{core['cpc'] / main['result']['pre_ad_profit']:.1%}（按{main.get('name') or '主口径'}）")
    if not parts:
        profit = (main.get('result') or {}).get('pre_ad_profit')
        reason = '缺核心词 CPC' if not core else '费用不全，算不出单件利润' if profit is None else '单件广告前利润不为正'
        return '数据不足，暂不判断：' + '、'.join([reason] + pending)
    text = f"条件结论：核心词「{core.get('keyword') or '核心词'}」CPC {core['cpc']:.2f}，保本需转化率 " + '、'.join(parts)
    return text + ('；仍待核实：' + '、'.join(pending) if pending else '')


def card_fields(unified, step):
    main=unified.get('main_basis') or {}
    if not main:return {}
    fields={'key_numbers':[{'label':'主口径','value':main['name']}]}
    if step==7:fields['summary']=promotion_summary(unified)
    return fields


def condition_table(unified, heading='### 主口径：你选定的条件情景'):
    """R06.1 PL-07：用户在本轮指定的条件情景（如 A 采购16元+4cm / B 采购19元+3cm）作主口径。没有时返回空串。"""
    main = unified.get('main_basis') or {}
    if main.get('kind') != 'user_condition':
        return ''
    def number(value, percent=False):
        if value is None:return '待核'
        return f'{value*100:.2f}%' if percent else f'{value:.2f}'
    lines = [heading, '', '| 情景 | 相对当前输入的改动 | 售价 | 单件落地成本 | 广告前利润 | 广告前毛利率 | 达到目标毛利的售价 |',
             '|---|---|---:|---:|---:|---:|---:|']
    for row in main['scenarios']:
        result = row['result']
        changes = '；'.join(f"{c['field']} {number(c['before'])}→{number(c['value'])}" for c in row['changes'])
        mark = '（代表值）' if row['id'] == main.get('representative') else ''
        lines.append('| '+row['label']+mark+' | '+changes+' | '+number(row['price'])+' | '+number(result.get('landed_cost'))+' | '+
                     number(result.get('pre_ad_profit'))+' | '+number(result.get('pre_ad_margin'),True)+' | '+number(result.get('target_price_pre_ad'))+' |')
    reference = main.get('reference_basis') or {}
    lines += ['', main['note'], '对照（未选情景时的口径）：'+str(reference.get('name') or '无')+'，广告前毛利率 '+
              number((reference.get('result') or {}).get('pre_ad_margin'),True)+'。', '']
    return '\n'.join(lines)


def render_assumptions(unified):
    main = unified.get('main_basis') or {}
    if not main:
        return ''
    def number(value, percent=False):
        if value is None:return '待核'
        if isinstance(value,bool):return '是' if value else '否'
        if isinstance(value,(int,float)):return f'{value*100:.2f}%' if percent else f'{value:.2f}'
        return str(value).replace('|','／').replace('\n',' ')
    result = main['result']
    lines=['## 主口径与未核费用情景','', '本次主口径：**'+main['name']+'**。'+main['note'],
           *([condition_table(unified)] if main.get('kind')=='user_condition' else []),
           '实际利润：待核；情景测算不代表厂家报价或采购授权。', '',
           '| 主口径 | 单件落地成本 | 广告前毛利率 | 目标售价 |', '|---|---:|---:|---:|',
           '| '+main['name']+' | '+number(result.get('landed_cost'))+' | '+number(result.get('pre_ad_margin'),True)+' | '+number(result.get('target_price_pre_ad'))+' |','',
           '### 每项输入与来源','','| 项目 | 原值 | 来源类型 | 来源 / 证据 |','|---|---:|---|---|']
    for key,node in unified['input_state'].items():
        lines.append('| '+FIELD_LABELS.get(key,key)+' | '+number(node.get('value'))+' | '+STATUS_LABELS.get(node.get('status'),'待核来源')+' | '+str(node.get('source') or '待核：缺来源').replace('|','／').replace('\n',' ')+' |')
    for case in unified.get('cost_assumption_scenarios') or []:
        lines += ['', '### '+case['basis_name'],'', '| 项目 | 数值 | 来源类型 | 来源 / 证据 |','|---|---:|---|---|']
        for row in case['assumption_table']:
            lines.append('| '+row['field']+' | '+number(row['value'])+' | '+STATUS_LABELS.get(row['kind'],row['kind'])+' | '+str(row.get('source') or '待核').replace('|','／').replace('\n',' ')+' |')
        estimate=case['result']
        lines += ['', '条件结果：单件落地成本 '+number(estimate['landed_cost'])+'；广告前毛利率 '+number(estimate['pre_ad_margin'],True)+'。未知费用本值仍为待核。']
    prices=unified.get('price_scenarios') or []
    if prices:
        lines += ['', '## 原生多售价情景','', '| 售价 | 口径 | 广告前毛利率 | 关键词 | CPC | 保本所需CVR |','|---:|---|---:|---|---:|---:|']
        for price in prices:
            for keyword in price['keywords'] or [{}]:
                lines.append('| '+number(price['price'])+' | '+price['basis_name']+' | '+number(price['result']['pre_ad_margin'],True)+' | '+str(keyword.get('keyword') or '待核核心词')+' | '+number(keyword.get('cpc'))+' | '+number(keyword.get('required_cvr'),True)+' |')
    return reader_projection('\n'.join(lines)+'\n')
