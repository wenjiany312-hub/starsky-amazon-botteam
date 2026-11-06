#!/usr/bin/env python3
"""Shared, evidence-bound S1 decision view. Never authorizes purchase or S2.

Input schema v1: product_name, scope, components, demand.evidence/counterevidence,
supplier_checks[{name,result}], risks[{name,cleared,severity}], user_settings,
scenarios. Facts may be plain display values or {value, unit, status, source,
observed_at, valid_until}. Only verified/confirmed dated facts can pass gates.
Economics comes exclusively from the hash-matched native 05_cost_model.yaml.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import os
import shutil
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s1_readability import readable_html, readable_markdown
from s1_decision_binding import configuration_hash

OUTPUT_DIR = Path('S1_选品板块/_阶段总览')
INPUT = 's1_decision_inputs.yaml'
MD = '_附录/04_产品开发决策依据.md'
HTML = '_附录/04_产品开发决策依据.html'
MODEL = 's1_decision_model.json'
COST_DIR = Path('S1_选品板块/amazon-market-research')
USER_PROFILE = Path.home() / '.starsky/选品配置.yaml'
PRODUCT_PROFILE = Path('00_产品资料/选品配置.yaml')
LABELS = {'sample_ready': '具备拿样评估条件', 'research_pending': '补证后再评估', 'hold': '暂缓推进'}
SUPPLIER_REQUIRED = ('assembly', 'packaging', 'labeling', 'carton', 'quote', 'lead_time', 'quality')
RISK_REQUIRED = ('intellectual_property', 'compliance', 'safety', 'logistics')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def value(item):
    return item.get('value') if isinstance(item, dict) and 'value' in item else item


def text(item):
    unit = item.get('unit') if isinstance(item, dict) else None
    item = value(item)
    if item is None:
        return '待补'
    if isinstance(item, bool):
        return '是' if item else '否'
    if isinstance(item, (dict, list)):
        return '；'.join(str(k) + '：' + text(v) for k, v in item.items()) if isinstance(item, dict) else '；'.join(text(v) for v in item)
    return str(item) + (' ' + str(unit) if unit else '')


def esc(item):
    return html.escape(text(item), quote=True)


def parse_time(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def verified(item, now):
    if not isinstance(item, dict) or item.get('status') not in ('verified', 'confirmed'):
        return False
    observed, until = parse_time(item.get('observed_at')), parse_time(item.get('valid_until'))
    return (item.get('value') is not None and bool(str(item.get('source') or '').strip())
            and observed is not None and until is not None and observed <= now <= until)


def list_of(data):
    return data if isinstance(data, list) else []


def build_model(data, input_sha256, now=None, cost_model=None):
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('s1_decision_inputs.yaml 必须使用 schema_version: 1')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    blockers, hold = [], False
    components = list_of(data.get('components'))
    if not components:
        blockers.append('组件：尚未填写产品组成和用量。')
    for component in components:
        quantity = value(component.get('quantity'))
        if not isinstance(quantity, (float, int)) or isinstance(quantity, bool) or quantity <= 0:
            blockers.append('组件：' + text(component.get('name')) + ' 用量无效或待确认。')
    demand = data.get('demand') or {}
    evidence = list_of(demand.get('evidence'))
    if not evidence or not all(verified(e, now) and bool(value(e)) for e in evidence):
        observed = list_of(demand.get('observed_evidence'))
        if observed:
            sources = '；'.join(str(e.get('provider', '')) + ' ' + str(e.get('data_date', '')) for e in observed)
            blockers.append('需求：已有取数（' + sources + '）；跨源口径与目标规格需求仍待核对。')
        else:
            blockers.append('需求：缺已核验且未过期的需求证据；纯描述不能替代取数。')
    supplier = list_of(data.get('supplier_checks'))
    supplier_ids = {c.get('id') for c in supplier}
    for required in SUPPLIER_REQUIRED:
        if required not in supplier_ids:
            blockers.append('厂家：缺少必需核查项 ' + required + '。')
    if not supplier:
        blockers.append('厂家：尚未填写核查清单。')
    for check in supplier:
        result = check.get('result')
        na = check.get('id') == 'assembly' and len(components) == 1 and value(result) == 'not_applicable'
        if not verified(result, now) or (value(result) is not True and not na):
            blockers.append('厂家：' + text(check.get('name')) + ' 尚未以当前证据确认。')
            hold |= verified(result, now) and value(result) is False
    risks = list_of(data.get('risks'))
    risk_ids = {r.get('id') for r in risks if r.get('severity', 'blocking') != 'info'}
    for required in RISK_REQUIRED:
        if required not in risk_ids:
            blockers.append('风险：缺少必需排查项 ' + required + '。')
    if not risks:
        blockers.append('风险：尚无风险检查证据。')
    for risk in risks:
        if risk.get('severity', 'blocking') == 'info':
            continue
        cleared = risk.get('cleared')
        if not verified(cleared, now) or value(cleared) is not True:
            blockers.append('风险：' + text(risk.get('name')) + ' 未排除。')
            hold |= verified(cleared, now) and value(cleared) is False
    cost = cost_model or {}
    complete = [s for s in list_of(cost.get('scenarios')) if s.get('status') == 'complete']
    configuration_digest = configuration_hash(data)
    economics_current = (cost.get('_source_current') is True and bool(complete)
                         and cost.get('product_configuration_sha256') == configuration_digest
                         and not cost.get('legacy_undated_evidence'))
    eligible = [s for s in complete if s.get('meets_pre_ad_target') is True]
    prices = list_of(demand.get('market_price_evidence'))
    market_eligible = [s for s in eligible if any(p.get('scenario_id') == s.get('id')
                       and verified(p.get('price'), now) and value(p.get('price')) == s.get('price')
                       and verified(p.get('form_match'), now) and value(p.get('form_match')) is True for p in prices)]
    if not market_eligible:
        blockers.append('市场售价：缺少与完整成本情景同形态、同售价且未过期的市场证据；假设高价不能支持拿样。')
    target = value((data.get('user_settings') or {}).get('pre_ad_margin_target'))
    same_target = target is not None and cost.get('target_pre_ad_margin') == target
    if not economics_current or not complete:
        blockers.append('成本：完整真实费用和当前输入哈希尚未核验；报价、比例代理与情景利润不能算实际达标。')
    elif not same_target or not eligible:
        blockers.append('成本：没有满足本产品个人广告前目标的完整情景，或报告与成本账目标不一致。')
        hold |= bool(same_target and complete and not eligible)
    model = dict(schema_version=1, model_version='s1-decision-v1', input_sha256=input_sha256,
                 generated_at=now.isoformat(), product_name=text(data.get('product_name')),
                 scope=value(data.get('scope', 's1_only')), decision='hold' if hold else ('research_pending' if blockers else 'sample_ready'),
                 blockers=blockers, purchase_authorized=False, actual_profit=None,
                 components=list_of(data.get('components')), demand=demand,
                 supplier_checks=supplier, risks=risks, user_settings=data.get('user_settings') or {},
                 packaging=data.get('packaging') or {}, cost_breakdown=list_of(data.get('cost_breakdown')),
                 native_cost_fields=cost.get('fields') or [], landed=cost.get('landed') or {},
                 native_cost_evidence=cost.get('evidence') or {}, currency=cost.get('currency') or 'USD',
                 scenarios=cost.get('scenarios') or list_of(data.get('scenarios')),
                 economics_status='verified_costs' if economics_current else 'conditional',
                 cost_source_sha256=cost.get('_file_sha256'), cost_inputs_sha256=cost.get('inputs_sha256'),
                 cost_inputs_observed_sha256=cost.get('_inputs_file_sha256'),
                 product_configuration_sha256=configuration_digest,
                 next_action='仅评估是否拿样；采购另行授权。' if not blockers else '补齐下方阻塞项后，重新生成本报告。')
    # Contribution is the native model's pre-ad contribution, never a proxy net profit.
    model['verified_pre_ad_contribution'] = eligible[0].get('contribution') if economics_current and eligible else None
    return model


def safe_url(item, image=False):
    raw = text(item).strip()
    if image and raw.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,', 'data:image/gif;base64,')):
        return raw
    parts = urlsplit(raw)
    if parts.scheme in ('http', 'https') and parts.netloc:
        return raw
    if parts.scheme == '' and raw not in ('', '待补') and not raw.startswith(('//', '\\')):
        return raw.replace('\\', '/')
    return ''


def evidence_html(item):
    if not isinstance(item, dict) or 'value' not in item:
        return '<span>' + esc(item) + '</span><small>描述性输入 · 待核验</small>'
    return ('<span>' + esc(item) + '</span><small>' + esc(item.get('status', '待核验'))
            + ' · 来源：' + esc(item.get('source')) + ' · 采集：' + esc(item.get('observed_at'))
            + ' · 有效至：' + esc(item.get('valid_until')) + '</small>')


def evidence_paragraph(item):
    # Keep the text outside an inline span so the lossless paragraph splitter
    # can split at sentence boundaries without breaking a surrounding tag.
    metadata = evidence_html(item).split('</span>', 1)[-1]
    return readable_html('<p>' + esc(item) + '</p>') + metadata


def table(headers, rows):
    return ('<div class="table-scroll" tabindex="0"><table><thead><tr>'
            + ''.join('<th>' + esc(h) + '</th>' for h in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + c + '</td>' for c in row) + '</tr>' for row in rows)
            + '</tbody></table></div>')


def pct(item):
    n = value(item)
    return f'{n:.1%}' if isinstance(n, (int, float)) and not isinstance(n, bool) else text(item)


STYLE = '''.s1-decision{--ink:#142d3b;--muted:#546b77;--line:#dce7e7;color:var(--ink);font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;max-width:1160px;margin:auto;overflow-wrap:anywhere}.s1-decision *{box-sizing:border-box}.s1-decision h1{font-size:clamp(26px,5vw,42px);line-height:1.3;margin:12px 0}.s1-decision h2{font-size:24px;margin:30px 0 14px}.s1-decision h3{margin:0 0 12px}.s1-decision p{margin:10px 0}.s1-decision .hero{background:#123d46;color:#fff;padding:28px;border-radius:20px}.s1-decision .eyebrow{letter-spacing:2px;font-size:13px;color:#b7e5de}.s1-decision .badge{display:inline-block;border-radius:99px;padding:5px 14px;background:#ffe2a0;color:#543b00;font-weight:700}.s1-decision .blockers{background:#fff4e2;border:1px solid #e8c780;padding:20px;border-radius:14px;margin:18px 0}.s1-decision .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:18px}.s1-decision .card{border:1px solid var(--line);border-radius:16px;background:white;padding:20px;min-width:0}.s1-decision img{display:block;width:100%;height:230px;object-fit:contain;background:#f3f6f4;border-radius:10px}.s1-decision .no-image{display:grid;place-items:center;height:180px;background:#edf2f2;color:var(--muted)}.s1-decision small{display:block;color:var(--muted);font-size:12px;margin-top:6px}.s1-decision a{color:#096974;text-underline-offset:3px}.s1-decision .table-scroll{overflow-x:auto;max-width:100%;border:1px solid var(--line);border-radius:12px;margin:12px 0}.s1-decision table{border-collapse:collapse;width:100%;font-size:14px}.s1-decision th,.s1-decision td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);min-width:100px}.s1-decision th{background:#eaf3f1}.s1-decision details{background:#f3f7f6;padding:18px;border-radius:12px;margin-top:14px}.s1-decision summary{cursor:pointer;font-weight:700}.s1-decision dl{display:grid;grid-template-columns:90px 1fr;gap:8px;font-size:14px}.s1-decision dt{color:var(--muted)}.s1-decision dd{margin:0;min-width:0}.s1-decision code{font-size:12px;white-space:normal;word-break:break-all}.s1-decision .two{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:600px){.s1-decision .hero{padding:20px}.s1-decision .two{grid-template-columns:1fr}.s1-decision th,.s1-decision td{padding:9px}.s1-decision h2{font-size:21px}}'''


COMPONENT_FIELDS = [('sku', 'SKU'), ('specification', '规格'), ('material', '材质'), ('quantity', '套装用量'),
                    ('page_price', '页面报价'), ('moq', '起订量'), ('stock', '页面库存'), ('page_weight', '页面重量'),
                    ('package', '包装信息'), ('package_info', '包装说明'), ('notes', '核查备注')]
PACK_LABELS = {'final_dimensions': '最终包装尺寸', 'final_weight': '最终包装重量', 'carton_dimensions': '外箱尺寸',
               'carton_qty': '每箱数量', 'carton_weight': '外箱毛重', 'status': '包装确认状态',
               'page_reference': '页面件重尺（参考）'}
FEE_LABELS = {'referral': '平台佣金', 'fba': 'FBA 配送', 'inbound_placement': '入库配置', 'storage': '仓储',
              'return_loss': '退货损耗', 'total': '平台费用合计'}


def cost_detail_rows(model):
    """Shared semantic rows for HTML/MD: label, fact, note, source grade."""
    rows = []
    for field in model['native_cost_fields']:
        key = (str(field.get('group')) + '.' if field.get('group') else '') + str(field.get('key'))
        evidence = dict(model['native_cost_evidence'].get(key) or field)
        evidence.setdefault('unit', field.get('unit'))
        rows.append((field.get('label') or key, evidence, '原生成本输入', evidence.get('status', '待核验')))
    for part in model['landed'].get('parts_cny', []):
        rows.append((part.get('item'), {'value': part.get('cny'), 'unit': 'CNY/套'}, '到岸成本分项', model['economics_status']))
    for scenario in model['scenarios']:
        for key, label in FEE_LABELS.items():
            if key in (scenario.get('fees') or {}):
                rows.append((text(scenario.get('id') or scenario.get('name')) + ' · ' + label,
                             {'value': scenario['fees'][key], 'unit': model['currency'] + '/套'},
                             '平台费用；按该情景包装和售价核算', scenario.get('status', 'conditional')))
    for row in model['cost_breakdown']:
        rows.append((row.get('name') or row.get('item'), row.get('amount', row.get('value')), row.get('notes'), '条件展示 · 不能替代真实成本'))
    return rows


def render_fragment(model):
    """Reusable HTML block; callers must use the same persisted, current model."""
    parts = ['<style>' + STYLE + '</style><section class="s1-decision" data-input-sha256="' + esc(model['input_sha256']) + '">',
             '<header class="hero"><div class="eyebrow">S1 · PRODUCT DECISION</div><h1>' + esc(model['product_name']) + '</h1>',
             '<p>产品开发决策透视 · 证据、成本与执行边界放在同一页</p><span class="badge">' + esc(LABELS[model['decision']]) + '</span>',
             '<p>' + esc(model['next_action']) + '</p><p>拿样条件不等于采购授权，也不代表进入 S2。</p></header>',
             '<div class="blockers"><h3>当前阻塞项</h3><ul>' + ''.join('<li>' + esc(b) + '</li>' for b in model['blockers']) + '</ul>'
             + ('<p>已核验条件满足；仍需用户决定是否拿样。</p>' if not model['blockers'] else '') + '</div>',
             '<h2>01 / 组件 CT 与采购对象</h2><div class="cards">']
    for component in model['components']:
        image_url, offer = safe_url(component.get('image'), True), safe_url(component.get('offer_url'))
        visual = '<img loading="lazy" src="' + html.escape(image_url, quote=True) + '" alt="' + esc(component.get('name')) + '">' if image_url else '<div class="no-image">图片待补</div>'
        if offer:
            visual = '<a href="' + html.escape(offer, quote=True) + '" target="_blank" rel="noopener noreferrer">' + visual + '</a>'
        parts += ['<article class="card"><h3>' + esc(component.get('name')) + '</h3>' + visual,
                  ('<p><a href="' + html.escape(offer, quote=True) + '" target="_blank" rel="noopener noreferrer">查看该组件货源</a></p>') if offer else '<p>货源链接待补</p>',
                  '<dl>' + ''.join('<dt>' + label + '</dt><dd>' + evidence_html(component.get(key)) + '</dd>' for key, label in COMPONENT_FIELDS if key in component or key in ('quantity', 'page_price', 'moq')) + '</dl></article>']
    if not model['components']:
        parts.append('<p>组件清单待补。</p>')
    parts += ['</div><h3>最终包装与外箱</h3>', table(['项目', '值与单位', '证据状态'],
               [[esc(PACK_LABELS.get(k, k)), evidence_html(v), esc(v.get('status', '待核验') if isinstance(v, dict) else '待核验')]
                for k, v in model['packaging'].items()]), '<h2>02 / 需求证据与反证</h2><div class="two">']
    for key, label in [('evidence', '支持需求的证据'), ('counterevidence', '需要主动验证的反证')]:
        rows = list_of(model['demand'].get(key))
        parts.append('<div class="card"><h3>' + label + '</h3>' + ''.join(evidence_paragraph(r) for r in rows) + ('<p>尚未填写。</p>' if not rows else '') + '</div>')
    parts += ['</div><h2>03 / 成本情景与个人目标</h2><p>个人广告前毛利目标：<b>' + esc(pct(model['user_settings'].get('pre_ad_margin_target'))) + '</b>。广告后目标须单独核算。</p>',
              '<p>仅完整真实成本可支持达标判断；其余均为条件情景，不表示实际利润。</p>']
    rows = []
    for s in model['scenarios']:
        bounds = s.get('pricing_boundaries') or {}
        actual = model['economics_status'] == 'verified_costs' and s.get('status') == 'complete'
        conditional = s.get('conditional_estimate') or {}
        margin = s.get('gross_margin') if actual else conditional.get('gross_margin', s.get('pre_ad_margin'))
        rows.append([esc(s.get('name') or s.get('label') or s.get('id')), esc(s.get('price')), '完整真实费用' if actual else '条件情景 · 待核验',
                     esc(pct(margin)), esc(bounds.get('required_price', s.get('required_price'))),
                     esc(bounds.get('unknown_cost_room', s.get('unknown_cost_room'))), esc(s.get('notes') or s.get('evidence_gaps'))])
    parts += [table(['情景', '售价', '证据等级', '广告前毛利率', '目标所需售价', '未知费用容纳额', '缺口与说明'], rows),
              '<h3>费用分项与包装口径</h3><p>采购、配件、包装、贴标、头程和平台费用分别列示；缺失值保持待补，代理费用保留条件标识。</p>',
              table(['费用/参数', '值与单位', '说明', '等级'], [[esc(label), evidence_html(e), esc(note), esc(grade)] for label, e, note, grade in cost_detail_rows(model)]),
              '<h2>04 / 厂家与风险检查</h2>', table(['检查项', '检查结果', '来源与时效'], [[esc(c.get('name')), esc(value(c.get('result'))), evidence_html(c.get('result'))] for c in model['supplier_checks']]),
              table(['风险项', '是否排除', '证据'], [[esc(r.get('name')), esc(value(r.get('cleared'))), evidence_html(r.get('cleared'))] for r in model['risks']]),
              '<details><summary>展开证据口径与版本核对</summary><p>状态：' + esc(model['decision']) + '；模型：' + esc(model['model_version']) + '。</p>',
              '<p>输入哈希：<code>' + esc(model['input_sha256']) + '</code></p><p>成本源哈希：<code>' + esc(model.get('cost_source_sha256')) + '</code></p>',
              '<p>生成时间：' + esc(model['generated_at']) + '。证据变化或失效后，须重新生成；本页不执行采购、改价或阶段跳转。</p></details></section>']
    return readable_html(''.join(parts).replace('广告前毛利', '广告前利润率').replace('利润率率', '利润率'))


def md_cell(item):
    return text(item).replace('|', '\\|').replace('\n', '<br>')


def render_md(model):
    lines = ['# 产品开发决策透视', '', '## ' + model['product_name'], '', '**结论：' + LABELS[model['decision']] + '**', '',
             model['next_action'], '', '拿样条件不等于采购授权，也不代表进入 S2。', '', '## 当前阻塞项', '']
    lines += ['- ' + b for b in model['blockers']] or ['- 已核验条件满足；仍需用户决定是否拿样。']
    lines += ['', '## 组件 CT', '']
    for c in model['components']:
        lines += ['### ' + md_cell(c.get('name')), '']
        img, offer = safe_url(c.get('image_md_url') or c.get('image'), True), safe_url(c.get('offer_url'))
        if img:
            picture = '![' + md_cell(c.get('name')).replace(']', '') + '](<' + img.replace('>', '%3E') + '>)'
            lines.append('[' + picture + '](<' + offer.replace('>', '%3E') + '>)' if offer else picture)
        lines += ['[查看该组件货源](<' + offer.replace('>', '%3E') + '>)' if offer else '货源链接待补', '', '| 字段 | 内容 |', '| --- | --- |']
        lines += ['| ' + label + ' | ' + md_cell(c.get(key)) + ' |' for key, label in COMPONENT_FIELDS if key in c]
        lines.append('')
    lines += ['## 最终包装与外箱', '', '| 项目 | 值与单位 |', '| --- | --- |']
    lines += ['| ' + md_cell(PACK_LABELS.get(k, k)) + ' | ' + md_cell(v) + ' |' for k, v in model['packaging'].items()]
    lines.append('')
    for key, label in [('evidence', '需求证据'), ('counterevidence', '反证')]:
        lines += ['## ' + label, '']
        for e in list_of(model['demand'].get(key)):
            lines += ['- ' + md_cell(e)]
            if isinstance(e, dict):
                lines += ['  - ' + '；'.join(k + '：' + md_cell(e.get(k)) for k in ('status', 'source', 'observed_at', 'valid_until'))]
    lines += ['', '## 成本情景', '', '个人广告前毛利目标：' + pct(model['user_settings'].get('pre_ad_margin_target')) + '。广告后目标单独核算。', '',
              '仅完整真实成本可支持达标判断；其余均为条件情景，不表示实际利润。', '', '| 情景 | 售价 | 等级 | 广告前毛利率 | 目标所需售价 | 未知费用容纳额 |', '| --- | --- | --- | --- | --- | --- |']
    for s in model['scenarios']:
        actual = model['economics_status'] == 'verified_costs' and s.get('status') == 'complete'
        bounds = s.get('pricing_boundaries') or {}
        margin = s.get('gross_margin') if actual else (s.get('conditional_estimate') or {}).get('gross_margin', s.get('pre_ad_margin'))
        lines += ['| ' + ' | '.join(md_cell(v) for v in [s.get('name') or s.get('label') or s.get('id'), s.get('price'), '完整真实费用' if actual else '条件情景 · 待核验', pct(margin), bounds.get('required_price', s.get('required_price')), bounds.get('unknown_cost_room', s.get('unknown_cost_room'))]) + ' |']
    lines += ['', '## 费用分项与包装口径', '', '缺失值保持待补；历史费用与代理费用不能替代完整真实成本。', '', '| 费用/参数 | 值与单位 | 说明 | 等级 |', '| --- | --- | --- | --- |']
    lines += ['| ' + ' | '.join(md_cell(x) for x in (label, e, note, grade)) + ' |' for label, e, note, grade in cost_detail_rows(model)]
    for key, label, field in [('supplier_checks', '厂家核查', 'result'), ('risks', '风险排查', 'cleared')]:
        lines += ['', '## ' + label, '', '| 项目 | 结果 | 证据 |', '| --- | --- | --- |']
        for row in model[key]:
            e = row.get(field)
            lines += ['| ' + md_cell(row.get('name')) + ' | ' + md_cell(e) + ' | ' + md_cell(e if isinstance(e, str) else {k: v for k, v in (e or {}).items() if k != 'value'}) + ' |']
    lines += ['', '## 版本与边界', '', '状态：' + model['decision'], '', 'input_sha256: ' + model['input_sha256'], '',
              'cost_source_sha256: ' + str(model.get('cost_source_sha256')), '', 'model_version: ' + model['model_version'], '', 'generated_at: ' + model['generated_at'], '']
    return readable_markdown('\n'.join(lines).replace('广告前毛利', '广告前利润率').replace('利润率率', '利润率'))


def with_local_images(data, root):
    """Embed only explicitly supplied local raster files; never request a URL."""
    data = copy.deepcopy(data)
    for component in list_of(data.get('components')):
        ref = value(component.get('image'))
        if not isinstance(ref, str) or ref.startswith(('https:', 'http:', 'data:')):
            continue
        path = Path(ref)
        if not path.is_absolute():
            path = root / path
        from r01_case_store import CompactCase
        store=CompactCase(root)
        logical=store.resolve(path)
        with store.materialize() as view:
            path=view.map_path(logical)
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif'}.get(path.suffix.lower())
            embedded = None
            if mime and path.is_file() and path.stat().st_size <= 8 * 1024 * 1024:
                embedded = 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
        if isinstance(component.get('image'), dict):
            component['image']['value'] = embedded
        else:
            component['image'] = embedded
    return data


def markdown_assets(model):
    assets = {}
    for component in model['components']:
        ref = value(component.get('image'))
        if not isinstance(ref, str) or not ref.startswith('data:image/'):
            continue
        header, encoded = ref.split(',', 1)
        extension = {'data:image/png;base64': '.png', 'data:image/jpeg;base64': '.jpg',
                     'data:image/webp;base64': '.webp', 'data:image/gif;base64': '.gif'}.get(header)
        if not extension:
            continue
        raw = base64.b64decode(encoded, validate=True)
        relative = 'assets/s1-' + digest(raw) + extension
        assets[relative] = raw
        component['image_md_url'] = relative
    return assets


def load_cost(root):
    from r01_case_store import CompactCase
    with CompactCase(root).materialize() as view:
        return _load_cost_native(view.root)


def _load_cost_native(root):
    location = root / COST_DIR / '05_cost_model.yaml'
    inputs = root / COST_DIR / '05_cost_inputs.yaml'
    if not location.is_file():
        return {}
    raw = location.read_bytes()
    cost = yaml.safe_load(raw.decode('utf-8-sig')) or {}
    cost['_file_sha256'] = digest(raw)
    cost['_inputs_file_sha256'] = digest(inputs.read_bytes()) if inputs.is_file() else None
    cost['_source_current'] = inputs.is_file() and digest(inputs.read_bytes()) == cost.get('inputs_sha256')
    if cost.get('unified_cost'):
        from s1_cost_engine import validate_provenance
        checked=validate_provenance(cost['unified_cost'],root=root)
        cost['_source_current'] &= checked['ok']
        cost['_engine_provenance_issues']=checked['issues']
    # An unchanged file can contain evidence that has expired since cost generation.
    def fresh(node):
        if isinstance(node, dict):
            if node.get('valid_until') and (parse_time(node['valid_until']) is None or parse_time(node['valid_until']) < datetime.now(timezone.utc)):
                return False
            return all(fresh(v) for v in node.values())
        return all(fresh(v) for v in node) if isinstance(node, list) else True
    if inputs.is_file() and not fresh(yaml.safe_load(inputs.read_bytes().decode('utf-8-sig'))):
        cost['_source_current'] = False
    return cost


def load_decision_inputs(root):
    """Read-only settings layers: explicit input > product > user; no defaults."""
    from r01_case_store import CompactCase
    with CompactCase(root).materialize() as view:
        return _load_decision_inputs_native(view.root,view.logical_root)


def _load_decision_inputs_native(root,logical_root):
    root = Path(root)
    logical_root=Path(logical_root)
    raw = (root / INPUT).read_bytes()
    data = yaml.safe_load(raw.decode('utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('决策输入须为 YAML 对象')
    settings, origins, hashes = {}, {}, {}
    for label, path in [('user', USER_PROFILE), ('product', root / PRODUCT_PROFILE)]:
        profile_raw = path.read_bytes() if path.is_file() else None
        hashes[label] = digest(profile_raw) if profile_raw is not None else None
        if profile_raw is not None:
            profile = yaml.safe_load(profile_raw.decode('utf-8-sig')) or {}
            current = profile.get('user_settings', profile)
            if not isinstance(current, dict):
                raise ValueError('选品配置 user_settings 须为对象：' + str(path))
            for key, item in current.items():
                settings[key] = item
                origins[key] = {'layer': label, 'path': str(logical_root/PRODUCT_PROFILE) if label=='product' else str(path)}
    for key, item in (data.get('user_settings') or {}).items():
        # Empty template cells do not erase an already confirmed profile value.
        if key in settings and value(item) is None and (not isinstance(item, dict) or item.get('status') in (None, 'missing')):
            continue
        settings[key] = item
        origins[key] = {'layer': 'input', 'path': str(logical_root / INPUT)}
    data['user_settings'] = settings
    try:
        from s1_market_facts import build_market_facts
        facts = build_market_facts(root)
        data.setdefault('demand', {})['observed_evidence'] = facts.get('demand_evidence', [])
    except ImportError:
        pass
    return data, {'profile_sha256': hashes, 'settings_provenance': origins, 'input_sha256': digest(raw)}


def generate(root, *, compact=False):
    from r01_case_store import CompactCase
    with CompactCase(root).materialize() as view:
        return _generate_native(view.root,compact=compact,output_root=view.logical_root)


def _generate_native(root, *, compact=False,output_root=None):
    """Stage a complete directory, then swap it. Never publish a mixed output set.

    Existing overview files are copied unchanged; previous outputs remain in a
    sibling _history directory. A failed final rename restores the old directory.
    A lock excludes concurrent writers; validation rejects interrupted swaps.
    """
    root = Path(root).resolve()
    data, provenance = load_decision_inputs(root)
    model = build_model(data, provenance['input_sha256'], cost_model=load_cost(root))
    model['components'] = with_local_images(data, root).get('components') or []
    model.update(provenance)
    if compact:
        # The stage renderer owns the two reader outputs and shared receipt.
        # Do not clone the overview, create appendix copies or archive drafts.
        return {'ok': True, 'compact': True, 'model': model}
    root=Path(output_root) if output_root is not None else root
    assets = markdown_assets(model)
    md = render_md(model)
    md = md.replace('# 产品开发决策透视', '# 附录：产品开发决策依据', 1).replace('](<assets/', '](<../assets/')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>产品开发决策透视</title><body style="margin:0;padding:clamp(12px,3vw,32px);background:#f5f8f7">' + render_fragment(model) + '</body></html>'
    model['outputs_sha256'] = {MD: digest(md.encode()), HTML: digest(page.encode())}
    model['outputs_sha256'].update({relative: digest(raw) for relative, raw in assets.items()})
    out = root / OUTPUT_DIR
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = out.parent / '.s1-decision.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    staged = Path(tempfile.mkdtemp(prefix='.s1-decision-stage-', dir=out.parent))
    backup = None
    try:
        if out.exists():
            shutil.copytree(out, staged, dirs_exist_ok=True)
        for name, content in [(MD, md), (HTML, page), (MODEL, json.dumps(model, ensure_ascii=False, indent=2, default=str))]:
            (staged / name).parent.mkdir(parents=True, exist_ok=True)
            (staged / name).write_text(content, encoding='utf-8', newline='\n')
        for relative, raw in assets.items():
            (staged / relative).parent.mkdir(parents=True, exist_ok=True)
            (staged / relative).write_bytes(raw)
        if out.exists():
            history = out.parent / '_history'
            history.mkdir(exist_ok=True)
            backup = history / ('decision-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
            out.rename(backup)
        try:
            staged.rename(out)
        except BaseException:
            if backup is not None:
                backup.rename(out)
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        lock.unlink(missing_ok=True)
    return {'ok': True, 'decision': model['decision'], 'model': str(out / MODEL), 'html': str(out / HTML), 'md': str(out / MD), 'input_sha256': model['input_sha256']}


def validate_output_set(root):
    from r01_case_store import CompactCase
    with CompactCase(root).materialize() as view:
        return _validate_output_set_native(view.root)


def _validate_output_set_native(root):
    root = Path(root)
    out = root / OUTPUT_DIR
    if (out.parent / '.s1-decision.lock').exists():
        return False
    try:
        model = json.loads((out / MODEL).read_text(encoding='utf-8'))
        if digest((root / INPUT).read_bytes()) != model['input_sha256']:
            return False
        cost = load_cost(root)
        if cost.get('_file_sha256') != model.get('cost_source_sha256'):
            return False
        if cost.get('_inputs_file_sha256') != model.get('cost_inputs_observed_sha256'):
            return False
        data, provenance = load_decision_inputs(root)
        if provenance['profile_sha256'] != model.get('profile_sha256'):
            return False
        current = build_model(data, model['input_sha256'], cost_model=cost)
        if current['decision'] != model['decision'] or current['blockers'] != model['blockers']:
            return False
        manifest_path = out / 's1_report_sync.json'
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('input_sha256') != model['input_sha256']:
                return False
            if manifest.get('model_sha256') != digest((out / MODEL).read_bytes()):
                return False
            if 'presentation_contract_sha256' in manifest:
                contract = out / '报告呈现约定.yaml'
                observed = digest(contract.read_bytes()) if contract.exists() else None
                if observed != manifest['presentation_contract_sha256']:
                    return False
            for relative, expected in manifest.get('reports', {}).items():
                location = (root / relative).resolve()
                if not location.is_relative_to(root.resolve()) or not location.is_file() or digest(location.read_bytes()) != expected:
                    return False
        output_hashes = model['outputs_sha256']
        if MD not in output_hashes or HTML not in output_hashes:
            return False
        stage_model_path = out / 's1_stage_report.json'
        if stage_model_path.is_file():
            stage_model = json.loads(stage_model_path.read_text(encoding='utf8'))
            for relative, expected in stage_model.get('source_manifest', {}).items():
                location = (root / relative).resolve()
                if not location.is_relative_to(root.resolve()):
                    return False
                observed = digest(location.read_bytes()) if location.is_file() else None
                if observed != expected:
                    return False
        return all((out / name).resolve().is_relative_to(out.resolve())
                   and digest((out / name).read_bytes()) == expected for name, expected in output_hashes.items())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(generate(args.root), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
