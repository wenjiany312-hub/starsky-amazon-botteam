"""One S1 stage document, rendered to Markdown and HTML from the same sections.

Raw observations and superseded drafts remain in history. No business approval
is inferred from formatting, a conditional margin, or report generation.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
import ast
from bs4 import BeautifulSoup
from s1_knowledge_evidence import BASE as KB_BASE, build_knowledge_sections, stage_references

STAGE = Path('S1_选品板块')
OVERVIEW = STAGE / '_阶段总览'
from stage_artifacts import role_path
from refresh_fx_snapshot import buffer_rate as fx_buffer_rate
from s1_criteria import criterion_text
MD = Path(role_path(Path(__file__).resolve().parents[1], 'S1', 'report_md'))
HTML = Path(role_path(Path(__file__).resolve().parents[1], 'S1', 'report_html'))
MARKET = STAGE / 'amazon-market-research'
TITLES = ['报告头与设计规格', '关键判定卡', '五问总决策表', '核心指标与覆盖', '增长跑赢与双重基准', '明确不做与硬门槛',
          '允许动作、禁止动作与授权预算', '结论翻转条件与下次复核', '市场容量与集中度', '成交价格带与价格分位', '竞品穿透、变体与新品分层',
          '趋势与时间窗口', '成本账与广告可推性', '匹配货源与定制路径', '合规与权利核查', '用户声音与验证动作',
          '实施条件与询价清单', '逐行核对台账', '验收与剩余证据']


def load(path):
    if not path.is_file(): return {}
    data = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    external = data.get('s1_stage_data') if isinstance(data, dict) else None
    if isinstance(external, dict) and external.get('external_path'):
        if set(external)-{'external_path','sha256','size'}:raise ValueError('阶段模型引用包含未封存字段')
        relative = Path(external['external_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('阶段模型路径越界')
        for parent in path.parents:
            target = parent / relative
            if target.is_file():
                if not target.resolve().is_relative_to(parent.resolve()):
                    raise ValueError('阶段模型路径越界')
                raw = target.read_bytes()
                if hashlib.sha256(raw).hexdigest() != external['sha256']:
                    raise ValueError('阶段模型内容已变化')
                data['s1_stage_data'] = json.loads(raw)
                break
        else:
            raise ValueError('阶段模型文件缺失')
    return data if isinstance(data, dict) else {}


def plugin_identity(home):
    """Identify the running installation, without assuming a release version."""
    manifests=[(host,Path(home)/folder/'plugin.json') for host,folder in
               [('codex','.codex-plugin'),('claude','.claude-plugin')]]
    found=[(host,path) for host,path in manifests if path.is_file()]
    if len(found)==1:
        host,path=found[0];raw=path.read_bytes()
        data=json.loads(raw.decode('utf-8-sig'))
        version=data.get('version')
        return {'host':host,'version':version if isinstance(version,str) and version.strip() else None,
                'manifest_sha256':hashlib.sha256(raw).hexdigest()}
    return {'host':'source_preview','version':None,'manifest_sha256':None}


def value(v):
    return v.get('value') if isinstance(v, dict) and 'value' in v else v


def cell(v):
    v = value(v)
    if v is None or (isinstance(v, float) and not __import__('math').isfinite(v)): return '未取得'
    if isinstance(v, bool): return '是' if v else '否'
    if isinstance(v, dict): return '；'.join(str(k) + '：' + cell(x) for k, x in v.items())
    if isinstance(v, (list, tuple)): return '；'.join(cell(x) for x in v)
    return str(v).replace('|', '／').replace('\n', ' ')


def reader_text(text):
    # URLs and literal code/keyword lists are evidence, not prose to translate.
    tokens = {}
    def protect(match):
        key = f'\x00{len(tokens)}\x00'; tokens[key] = match[0]; return key
    text = re.sub(r'https?://[^\s)>]+|`[^`]*`', protect, text)
    words = {'null': '待核实', 'None': '未取得', 'unknown': '未知', 'conditional': '条件测算',
             'full_delivery': '完整研究交付', 'acceptance_checks': '验收检查', 'cursor': '续查位置', 'CT': '结构透视'}
    for old, new in words.items(): text = re.sub(r'(?<![A-Za-z0-9_])' + old + r'(?![A-Za-z0-9_])', new, text)
    text = text.replace('側', '侧').replace('較', '较').replace('严格侧', '待核实口径')
    for key, original in tokens.items(): text = text.replace(key, original)
    return text


def table(headers, rows, collapse_missing=False):
    rows = list(rows)
    if collapse_missing and rows and all(all(v is None or v in ('', '—', '未知', 'null') for v in row[1:]) for row in rows):
        return f'以下 {len(rows)} 项待补证：' + '、'.join(cell(row[0]) for row in rows) + '。\n'
    if not rows: return '本轮未取得可用记录，待补充。\n'
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows]) + '\n'


def selected_design(root):
    """Only user-origin decisions define the product; supplier pages are references."""
    folder = root / '00_总控/starsky-fullchain-workflow'
    choices = []
    for path in sorted(folder.glob('*选款决策.json')):
        item = load(path)
        if item.get('source') == 'user': choices.append(item)
    data = load(root / 's1_decision_inputs.yaml')
    if len(choices) > 1:
        ids = {item.get('selection') for item in choices}
        if len(ids) > 1: raise ValueError('存在多个不同的用户选款决策，请先明确本轮选款，禁止按文件时间猜测')
    return choices[-1] if choices else {'product_name': data.get('product_name', '待确认产品'),
        'features': [c.get('name') for c in data.get('components', []) if c.get('name')],
        'supplier_sku': None, 'source': 'decision_inputs'}


def existing_sections(root):
    path = root / MD
    text = path.read_text(encoding='utf8') if path.exists() else ''
    original = BeautifulSoup((root/HTML).read_text(encoding='utf8'), 'html.parser') if (root/HTML).exists() else None
    title_ids = {}
    if original:
        for section in original.select('section.report-section[id]'):
            heading = section.find(['h2','h3'])
            if heading:
                title_ids[re.sub(r'^M\d{2}\s*', '', heading.get_text(' ',strip=True))] = section['id']
    output = {}
    boundaries=[]; depth=0; offset=0
    for line in text.splitlines(keepends=True):
        if depth == 0 and line.startswith('## '):boundaries.append((offset,line[3:].strip(),offset+len(line)))
        depth=max(0,depth+len(re.findall(r'<details\b',line))-len(re.findall(r'</details>',line)))
        offset+=len(line)
    for index,(start,heading,body_start) in enumerate(boundaries):
        match=re.match(r'(M\d{2}(?:-[\w-]+)?)\s+(.*)',heading)
        sid,title=(match[1],match[2]) if match else ('appendix-'+hashlib.sha256(heading.encode()).hexdigest()[:8],heading)
        candidate=title_ids.get(title,sid)
        if candidate in output:candidate=f'{sid}-detail-{sum(k.startswith(sid) for k in output)+1}'
        end=boundaries[index+1][0] if index+1<len(boundaries) else len(text)
        output[candidate]={'title':title,'body':text[body_start:end].strip()}
    if original:
        for section in original.select('section.report-section[id]'):
            if section['id'] in output:continue
            heading=section.find(['h2','h3'])
            if not heading:continue
            title=heading.get_text(' ',strip=True)
            heading.decompose()
            output[section['id']]={'title':re.sub(r'^M\d{2}\s*','',title),'body':section.decode_contents(),'format':'html'}
    return output


def compact_missing_tables(text):
    """Only collapse tables whose measurement cells are all absent; keep real zeroes."""
    def replace(match):
        lines=match[0].strip().splitlines()
        rows=[[c.strip() for c in line.strip().strip('|').split('|')] for line in lines[2:]]
        missing={'','—','未知','null','None','未取得','待核实'}
        if rows and all(len(row)>1 and all(c in missing for c in row[1:]) for row in rows):
            return f'以下 {len(rows)} 项待补证：'+ '、'.join(r[0] for r in rows)+'。\n'
        return match[0]
    return re.sub(r'^\|[^\n]+\n\|[\s|:\-]+\n(?:\|[^\n]*\n?)+',replace,text,flags=re.M)


def normalize_reader_cells(text):
    lines=[]
    for line in text.splitlines():
        if line.lstrip().startswith('|'):
            cells=line.split('|')
            for i,v in enumerate(cells):
                raw=v.strip()
                if raw in {'-1','-1.0','None','null'}:cells[i]=' 未取得 '
                elif raw.startswith(('[','{')):
                    try:cells[i]=' '+cell(ast.literal_eval(raw))+' '
                    except (ValueError,SyntaxError):pass
            line='|'.join(cells)
        lines.append(line)
    return '\n'.join(lines)


def portable_links(text, root):
    def fix(match):
        prefix,target=match[1],match[2].strip('<>')
        if not (target.startswith('/') or re.match(r'^[A-Za-z]:',target)):return match[0]
        normalized=target.replace('\\','/')
        for marker in ('S1_选品板块/','00_总控/','00_产品资料/'):
            if marker in normalized:
                path=root/normalized[normalized.index(marker):]
                if path.is_file():return prefix+'<'+os.path.relpath(path,root/MD.parent).replace('\\','/')+'>)'
        return re.sub(r'^!?\[|\]\($','',prefix)+'（原本机路径已失效，待补可移植文件）'
    return re.sub(r'(!?\[[^\]]*\]\()<?([^)>]+)>?\)',fix,text)


def current_decision(root, model=None):
    model=copy.deepcopy(model) if model is not None else load(root/OVERVIEW/'s1_decision_model.json')
    labels={'sample_ready':'具备拿样评估条件','research_pending':'补证后再评估','hold':'暂缓推进',
            'stop':'停止推进','pass':'通过当前研究门槛','watch':'观望，待补证','go':'可推进下一步评估'}
    state=str(model.get('decision') or '').lower()
    model['label']=labels.get(state,'尚未形成决策')
    model['verdict']={'sample_ready':'watch','pass':'watch','go':'invest','stop':'reject','hold':'watch','research_pending':'watch','watch':'watch'}.get(state,'no_data')
    if model['verdict']=='invest' and model.get('blockers'):model['verdict']='watch'
    receipts = sorted(Path(root).glob('00_总控/*/运行数据/流程控制/S1/receipts/09.json'))
    if receipts:
        delivered = load(receipts[0])
        final = (delivered.get('research_recommendation') or {}).get('verdict')
        if final:
            model['final_verdict'] = final
    return model


def reference_images(root, supplier):
    """Use only existing local supplier media; never substitute it for the chosen product."""
    offer=str(supplier.get('offer_id') or '')
    if not offer:return []
    folder=root/STAGE/'1688-product-detail-scraper/媒体'/offer
    return [p for p in sorted(folder.rglob('*')) if p.is_file() and p.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}][:2]


def source_manifest(root, facts):
    paths=[root/'s1_decision_inputs.yaml',root/'fx_rate_snapshot.yaml',root/'s1_purchase_checklist.yaml',
        root/STAGE/'1688-product-detail-scraper/03_product_ct.yaml',
        root/STAGE/'1688-product-detail-scraper/03_product_facts.yaml',
        root/OVERVIEW/'报告呈现约定.yaml',root/OVERVIEW/'s1_decision_model.json',
        root/MARKET/'01_product_profile.yaml',root/MARKET/'01_market_facts.json',
        root/MARKET/'05_cost_model.yaml',root/MARKET/'05_cost_inputs.yaml',root/MARKET/'05_成本账与广告可推性.md',
        root/STAGE/'amazon-ip-risk-assessment/04A_compliance_ruler.yaml',
        root/STAGE/'alibaba-supplier-evaluation/02_supplier_evidence.yaml',
        root/'运行数据/S1_后补采集/enrichment_facts.json']
    from s1_selection import OUTPUT as selection_output
    canonical=root/selection_output
    if canonical.is_file():
        paths.append(canonical)
        origin=(yaml.safe_load(canonical.read_bytes()) or {}).get('projection_source') or {}
        if origin.get('path'):paths.append(root/origin['path'])
    paths.extend((root/'00_总控/starsky-fullchain-workflow').glob('*选款决策.json'))
    # Both the recorded expectation and raw response participate in freshness.
    paths.extend((root/KB_BASE).rglob('*.json'))
    paths.extend((root/KB_BASE).rglob('kb_evidence.yaml'))
    paths.extend(root/ref['manifest_path'] for ref in stage_references())
    supplement=root/'运行数据/S1_后补采集'
    paths.extend(p for p in supplement.rglob('*') if p.is_file() and p.suffix.lower() in {'.json','.yaml','.yml'})
    for row in facts.get('source_manifest',[]):
        if isinstance(row,dict) and row.get('source'):paths.append(root/row['source'])
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            for p in paths if p.resolve().is_relative_to(root)}


COVERAGE_LABELS = {'category_comparison':'类目对照','competitors':'竞品详情','variations':'变体穿透',
    'reviews':'评论覆盖','monthly_trend':'月度趋势','keyword_signals':'关键词信号','peak_top100':'旺季前100核查',
    'organic_top5':'自然前5核查','ad_keyword_count':'广告词数量'}
STATUS_LABELS = {'met':'已达量','data_gap':'仍有缺口','needs_rank_audit':'待核对排名','pending':'待补充',
    'pending_verification':'待核实','complete':'已完成'}


def coverage_rows(coverage):
    for key, entry in coverage.items():
        if not isinstance(entry,dict):continue
        reason = cell(entry.get('reason') or ('数量已达要求，统计口径见来源' if entry.get('status')=='met' else '待补充可核实证据'))
        yield (COVERAGE_LABELS.get(key,'其他覆盖项目'),f"{cell(entry.get('observed'))} / {cell(entry.get('required'))}",
               STATUS_LABELS.get(entry.get('status'),'待核实'),reason[:117]+'…' if len(reason)>118 else reason)


def section_heading(sid, title):
    number=re.match(r'M\d{2}',sid)
    # Technical anchor suffixes are identifiers, never reader-facing headings.
    title=re.sub(r'^M\d{2}(?:-[\w-]+)?\s*','',title)
    return ((number[0]+' ') if number else '')+reader_text(title)


def merged_research_notes(root, *, archive_sources=True):
    """Carry only physical specification rows into current prose; preserve all history by link.

    Previous conclusions, metrics and keyword lists remain verbatim in the archive.
    Receipts are read on reruns so the same evidence survives after source removal.
    """
    records=archive_plan(root)+load(root/OVERVIEW/'S1_统一报告修订记录.yaml').get('archived',[])
    notes=[];seen=set()
    physical=re.compile(r'^(?:产品(?:名称|类型)?|内部结构|结构|设计要求|材质|材料|尺寸|规格|颜色|容量|适配(?:尺寸|范围|对象)?|包含|不包含|不含|销售内容|套装内容|隔层|扣具|提手|肩带)$')
    for record in records:
        source=record.get('from',''); archived=record.get('to','')
        path=root/source if (root/source).is_file() else root/archived
        if path.suffix!='.md' or not path.is_file():continue
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        rows=[]
        for line in path.read_text(encoding='utf8').splitlines():
            if not line.strip().startswith('|'):continue
            cells=[x.strip() for x in line.strip().strip('|').split('|')]
            if len(cells)==2 and physical.fullmatch(re.sub(r'[*`：:]','',cells[0])) and cells[1]:
                pair=(cells[0],cells[1])
                if pair not in rows:rows.append(pair)
        target=archived if archive_sources else path.relative_to(root).as_posix()
        notes.append({'source':source,'archive_path':target,'sha256':digest,'specification_rows':rows})
    return notes


def build_sections(root, facts=None, *, decision_model=None, knowledge_sections=None, archive_sources=True):
    from r01_case_store import CompactCase
    with CompactCase(root).materialize() as view:
        return _build_sections_native(view.root,facts,decision_model=decision_model,
            knowledge_sections=knowledge_sections,archive_sources=archive_sources)


def _build_sections_native(root, facts=None, *, decision_model=None, knowledge_sections=None, archive_sources=True):
    from r01_case_store import CompactCase
    root = Path(root).resolve()
    if facts is None:
        from s1_market_facts import build_market_facts, build_enrichment_facts, apply_enrichment_coverage
        facts = build_market_facts(root)
        supplementary = root / '运行数据/S1_后补采集'
        if (supplementary / 'enrichment_facts.json').is_file():
            facts = apply_enrichment_coverage(facts, build_enrichment_facts(supplementary), supplementary.relative_to(root).as_posix())
    sections = existing_sections(root)
    design = selected_design(root)
    inputs = load(root / 's1_decision_inputs.yaml')
    from s1_decision_report import load_cost
    cost = load_cost(root)
    ruler = load(root / STAGE / 'amazon-ip-risk-assessment/04A_compliance_ruler.yaml')
    profile = load(root / MARKET / '01_product_profile.yaml')
    fx = load(root / 'fx_rate_snapshot.yaml')
    decision=current_decision(root,decision_model)
    def set_section(n, body):
        sid=f'M{n:02}'
        sections[sid] = {'title': sections.get(sid,{}).get('title',TITLES[n]), 'body': body}
    features = design.get('features') or [design.get('product_name', '待确认设计')]
    set_section(0, '设计方案 ' + cell(design.get('selection', '待确认')) + '：本轮选中的产品方向，采购款式另行确认。\n\n' +
        table(['项目', '已知信息'], [('产品', design.get('product_name')), ('设计要求', features),
        ('不包含', design.get('excluded')), ('目标售价（研究目标）', design.get('target_price_usd')),
        ('采购 SKU', design.get('supplier_sku')), ('核心词', profile.get('core_roots')),
        ('参考 ASIN', [x.get('asin') for x in facts.get('representatives', [])[:3]]),
        ('数据日期', sorted({str(x.get('data_date')) for x in facts.get('demand_evidence', []) if x.get('data_date')})),
        ('运行模式', '真实研究；未完成项逐条列出'), ('汇率快照', fx_buffer_rate(fx))]))
    badges={'invest':'建议投入','watch':'观望，暂不投入','reject':'不建议直接投入','no_data':'数据不足，禁止决策'}
    set_section(1, '**'+badges[decision['verdict']]+'。**\n\n一句话结论：'+decision['label']+'；'+cell(decision.get('conclusion') or decision.get('next_action'))+'\n\n'+
        table(['判定依据','当前结果'], [('采购授权', decision.get('purchase_authorized')),
            ('待解决事项', str(len(decision.get('blockers') or []))+' 项；详见首屏待核事项与对应证据章节')])+'\n条件利润用于比较方案，不代表实际利润或采购许可。')
    set_section(2, table(['问题', '本轮判断'], [('要不要做', decision['label']),
        ('具体做什么', design.get('product_name')), ('怎么做', '先匹配设计和货源，再核实样品、费用与需求'),
        ('什么时候开始', '以证据回填和用户选择为准'), ('理由是什么', '下列统一市场快照、成本情景与合规结果')]))
    competitors = facts.get('competitors', [])
    representatives = facts.get('representatives', [])
    shortlist = facts.get('supplier_shortlist', [])
    set_section(3, table(['指标', '数量', '口径'], [('竞品详情记录', len(competitors), '同 ASIN 仅采用一份快照'),
        ('父体代表样本', len(representatives), '同父体不重复计入价格与销量'),
        ('匹配货源线索', len(shortlist), '须另核定制能力与厂家身份')]) + '\n' +
        table(['数据项目', '已取得 / 目标', '状态', '原因'], coverage_rows(facts.get('coverage') or {}))+
        '\n完整来源、续查要求与回包记录见[市场事实文件](../amazon-market-research/01_market_facts.json)。')
    set_section(5, table(['门槛', '当前判断'], [('规格匹配', '设计要求需厂家实物/尺寸证据逐项核对'),
        ('报价与包装', '采购 SKU、空包报价、最终包装与外箱须对应同一配置'),
        ('材料与扣具', '未知项列待厂家确认，不能按已含危险材料判定'),
        ('权利核查', '按所选 SKU 检索；参考货源其他款式只作避坑提醒')]) + '\n明确不做：未核实的规格、利润或权利，不能写成已获证明；未经用户选择不能自动下单。')
    settings = inputs.get('user_settings') or {}
    set_section(6, table(['项目', '本轮范围'], [('允许动作', decision.get('next_action')),
        ('采购 SKU', design.get('supplier_sku') or decision.get('selected_sku')),
        ('采购数量', value(decision.get('purchase_qty') or settings.get('purchase_qty'))),
        ('用户广告前目标', value(settings.get('pre_ad_margin_target'))),
        ('用户广告后目标', value(settings.get('post_ad_margin_target'))),
        ('默认分摊情景', '成本章节单列，不代表用户选择'), ('禁止动作', '未经授权采购或上架'),
        ('当前最高授权预算', value(decision.get('authorized_budget', decision.get('purchase_budget',settings.get('authorized_budget'))))),
        ('采购授权',decision.get('purchase_authorized'))])+'\n预算仅展示现有决策证据，不由本报告新增授权。')
    bands = facts.get('price_bands', {})
    set_section(9, table(['价格位置', 'USD'], list(bands.items())) +
        '\n价格口径：工具返回的可见售价，未核实结账价或 coupon 抵扣；同父体仅保留一个代表，价格分位与用户价带上下限分别展示。')
    set_section(10, table(['ASIN', '父体', '价格', '月销估计', '评论', '上架日期', '统计资格'],
        [(c.get('asin'), c.get('parent_asin'), c.get('price_usd'), c.get('monthly_sales'), c.get('reviews'),
          c.get('listing_date') or c.get('online_date'), c.get('counting_note') or ('计入父体代表' if c.get('asin') in {x.get('asin') for x in representatives} else '同父体，不重复计入')) for c in competitors]) +
        '\n各指标使用同一详情快照。原始日期与来源保留在市场事实文件；工具估计不等于本品表现。')
    details=[]
    for competitor in competitors:
        raw=competitor.get('raw') or {k:v for k,v in competitor.items() if k not in ('sha256','counting_note')}
        details.append('### '+cell(competitor.get('asin'))+'\n\n'+table(['详情字段','工具记录'],raw.items())+
                       '\n来源：'+cell(competitor.get('source'))+'；记录日期：'+cell(competitor.get('observed_at')))
    if details:
        sections['M10']['body']+='\n\n<details><summary>查看'+str(len(details))+'个竞品的完整详情与变体字段</summary>\n\n'+'\n\n'.join(details)+'\n\n</details>'
    criteria=facts.get('criteria') or {}
    if criteria:
        sections['M10']['body']+='\n\n'+table(['分层与进入难度判据','当前结果'],[(k,criterion_text(v)) for k,v in criteria.items()])
    rows = []; cpc_rows=[]; offer_rows=[]
    for scenario in cost.get('scenarios', []):
        estimates=([scenario] if scenario.get('status') == 'complete' else scenario.get('conditional_cases') or
                   ([scenario['conditional_estimate']] if scenario.get('conditional_estimate') else [{}]))
        for estimate in estimates:
            margin = estimate.get('gross_margin')
            rows.append([scenario.get('label') or scenario.get('id'), scenario.get('price'),
                         '已核实费用' if scenario.get('status') == 'complete' else estimate.get('case_label','费用待补'),
                         f'{margin:.1%}' if isinstance(margin, (int, float)) else None,
                         estimate.get('margin_band') or scenario.get('margin_band'),
                         estimate.get('allowed_landed_cost', scenario.get('allowed_landed_cost'))])
            for cpc in estimate.get('cpc_by_cvr',[]):
                cpc_rows.append([scenario.get('label') or scenario.get('id'),estimate.get('case_label','已核实费用'),
                    f"{cpc['cvr']:.0%}" if isinstance(cpc.get('cvr'),(int,float)) else None,
                    cpc.get('break_even_cpc'),cpc.get('allowable_cpc'),(estimate.get('ad_pressure') or {}).get('label')])
        offer=scenario.get('offer') or {}
        offer_rows.append([scenario.get('label') or scenario.get('id'),offer.get('name'),offer.get('contents'),
            offer.get('pain_points'),scenario.get('price'),scenario.get('price_position'),scenario.get('value_multiple')])
    from s1_cost_present import condition_table
    set_section(12, condition_table(cost.get('unified_cost') or {}) + table(['情景', '售价', '依据', '广告前毛利率', '等级', '允许到岸成本'], rows) +
        '\n毛利率档位：≥40% 很好、30–40% 优秀、20–30% 一般、<20% 偏低；实际报价缺失时只作条件比较。档位和目标毛利都是参考，不是能不能做的门槛，实际能接受多少由用户决定。\n\n'+
        table(['汇率快照绑定','本轮记录'],[('成本模型采用的采购汇率（人民币/美元）',cost.get('effective_purchase_rate')),
            ('快照有效采购汇率（人民币/美元）',fx_buffer_rate(fx)),('快照来源日期',fx.get('source_as_of_utc')),
            ('实际结算汇率',(fx.get('settlement_override') or {}).get('actual_settlement_rate'))])+
        '\n保本 CPC 与广告压力（美元；转化率是情景参数）：\n\n'+table(['售价情景','费用情景','转化率','保本 CPC','目标内 CPC','广告压力'],cpc_rows,collapse_missing=True)+
        '\n组合与定价情景（尚未确认的方案只作对照）：\n\n'+table(['情景','配置','包含内容','待验证痛点','售价','价格带位置','组合增值倍数'],offer_rows)+
        '\n条件测算中的页价、重量及费用区间均为明示假设；用户尚未选定的数量、包装和利润目标不能写成个人目标。\n\n' +
        '<details><summary>查看费用、假设与广告承受能力</summary>\n\n' + reader_text((root / MARKET / '05_成本账与广告可推性.md').read_text(encoding='utf8') if (root / MARKET / '05_成本账与广告可推性.md').exists() else '成本明细待生成。') + '\n\n</details>')
    set_section(13, table(['货源', 'SKU / 形态', '页面参考价', '用途与资格'],
        [(s.get('supplier_name') or s.get('company') or s.get('name') or s.get('offer_id'), s.get('sku_name') or s.get('shape') or '采购款式未选',
          s.get('page_price'), s.get('reason') or s.get('qualification') or '询价线索，未确认采购') for s in shortlist]) +
        '\n目标设计与现货不一致时，增加“定制、隔层、来图打样”厂家检索；不足三家匹配厂家时如实显示缺口。')
    for supplier in shortlist:
        for picture in reference_images(root,supplier):
            relative=os.path.relpath(picture,root/MD.parent).replace('\\','/')
            sections['M13']['body']+='\n\n![货源参考：'+cell(supplier.get('supplier_name') or supplier.get('company') or supplier.get('offer_id'))+'](<'+relative+'>)'
    lights = {'red': '🔴 已识别硬门槛', 'orange': '🟠 已识别资质要求', 'yellow': '🟡 需核查适用要求',
              'gray': '⚪ 属性待核', 'green': '🟢 当前规则未命中'}
    set_section(14, '**' + lights.get(ruler.get('overall_light'), '⚪ 尚未生成合规结果') + '**\n\n' +
        table(['项目', '依据', '处理'], [(r.get('title'), r.get('evidence'), r.get('action')) for r in ruler.get('items', [])]) +
        '\n待厂家确认：' + '、'.join((ruler.get('attributes') or {}).get(a, {}).get('label', a) for a in ruler.get('unknown_attributes', [])) +
        '\n\n' + table(['检索', '状态', '未完成原因'], [(s.get('title'), '已查' if s.get('status') == 'searched' else '待你查', s.get('result') if s.get('status') == 'searched' else s.get('reason')) for s in ruler.get('searches', [])]) +
        '\n逐步操作与截图回传格式见合规子报告；未完成检索不表示权利已清除。')
    set_section(16, table(['已获取事实','自动提取/推导','模拟参数','缺失及影响','继续与修订'],[
        (f'竞品详情 {len(competitors)} 条；父体代表 {len(representatives)} 个','父体去重后计算价格分位',
         '工具月销量估计仅供比较','目标规格需求、自然位次等见覆盖缺口；不能据此断言本品销量','补齐可比需求证据后复核进入条件'),
        ('成本来源见 E-COST-001','按选定费用情景计算毛利率与保本 CPC',
         '页价代采购价、重量及未报价费用上下界；转化率为情景参数','厂家空包报价与最终包装待确认；实际利润未核实','同一 SKU 报价和包装回填后重算'),
        ('用户选款与合规回包见证据索引','未知属性不计入已识别风险灯',
         '未推定材料或采购数量','权利检索和材料仍有待核；不等于权利已清除','厂家补材料证据，用户完成待查卡后更新')]))
    sections['M16']['title']='数据解释窗口（五栏）'
    ledger = facts.get('ledger', [])
    evidence_specs=[('E-MARKET-001',MARKET/'01_market_facts.json','市场快照、父体去重与价格分位'),
        ('E-COST-001',MARKET/'05_cost_model.yaml','允许到岸成本、条件毛利率与保本 CPC'),
        ('E-FX-001',Path('fx_rate_snapshot.yaml'),'汇率快照与实际结算缺口'),
        ('E-SUP-001',STAGE/'alibaba-supplier-evaluation/02_supplier_evidence.yaml','同形态 SKU 页价与供应商线索'),
        ('E-RISK-001',STAGE/'amazon-ip-risk-assessment/04A_compliance_ruler.yaml','三态属性、合规尺子与必做检索'),
        ('E-EXTRA-001',Path('运行数据/S1_后补采集/enrichment_facts.json'),'后补覆盖与回包索引；原始文件另作哈希绑定')]
    evidence_rows=[]
    for eid,path,purpose in evidence_specs:
        exists=(root/path).is_file()
        evidence_rows.append([eid,path.as_posix(),purpose,'已留存' if exists else '尚缺文件',
            hashlib.sha256((root/path).read_bytes()).hexdigest() if exists else None])
    set_section(17, table(['证据编号','来源文件','支撑内容','文件状态','SHA-256'],evidence_rows)+'\n\n逐行核对台账：\n\n'+table(['ASIN', '标题', '分类', '逐行原因', '统一月销快照'],
        [(x.get('asin'), x.get('title'), x.get('classification_label') or x.get('classification'), x.get('classification_reason') or x.get('reason'), x.get('monthly_sales')) for x in ledger]))
    sections['M17']['title']='证据与口径索引'
    # Preserved presentation modules must never retain a second version of derived facts.
    for sid in list(sections):
        if sid.startswith('M12-') and any(t in sections[sid]['title'] for t in ('售价','定价','情景')):
            sections[sid]['body']=table(['情景','售价','依据','广告前毛利率','等级','允许到岸成本'],rows)
        if sid.startswith('M17-') and any(t in sections[sid]['title'] for t in ('类目','台账')):
            sections[sid]['body']=sections['M17']['body']
        if sid.startswith('M13-') and '首批' in sections[sid]['title']:
            sections[sid]['body']=table(['项目','当前选择'],[
                ('采购款式',design.get('supplier_sku')),('采购数量',value(decision.get('purchase_qty'))),
                ('授权预算',value(decision.get('authorized_budget')))],collapse_missing=True)
    set_section(18, table(['检查', '当前结果'], [('原始资料', '保留原始回包和历史版本，正式报告统一取数'),
        ('数据覆盖', '见 M03；未达量的项不当成通过'), ('利润', '实际与条件测算分开'),
        ('采购授权', decision.get('purchase_authorized'))]) + '\n研究判定：'+decision['label']+'。验收日志单独保存，不引用旧日志冒充本轮通过。')
    sections['M18']['title']='验收自检与剩余证据'
    enrichment=load(root/'运行数据/S1_后补采集/enrichment_facts.json')
    if enrichment:
        labels={'review_count':'去重评论数','review_target':'评论目标数','variation_queried_count':'已查变体产品数',
                'variation_returned_count':'返回变体产品数','peak_product_count':'旺季候选产品数',
                'peak_top100_status':'旺季前100核查状态','organic_top5_status':'自然前5核查状态'}
        sections['supplement-coverage']={'title':'独立后补覆盖记录','body':table(['项目','本次后补覆盖'],
            [(label,STATUS_LABELS.get(enrichment.get(k),enrichment.get(k))) for k,label in labels.items()])+ 
            '\n后补记录单独保留来源与日期，不改变前述市场快照统计。'}
    notes=merged_research_notes(root,archive_sources=archive_sources)
    sections.pop('revision-evidence',None)
    if notes:
        content='历史关键词、备注和各次结论按原文归档；本轮市场与成本采用上述统一事实。以下仅保留历史记录中的规格描述，仍须以用户选款和厂家确认核对。\n\n'
        unique=[]
        for note in notes:
            for row in note['specification_rows']:
                if row not in unique:unique.append(row)
        if unique:content+=table(['规格项目','历史描述（待与本轮选款核对）'],unique)+'\n'
        for i,note in enumerate(notes,1):
            target=os.path.relpath(root/note['archive_path'],root/MD.parent).replace('\\','/')
            content+=f'- [历史研究记录 {i}](<{target}>)\n'
        sections['revision-evidence']={'title':'历史规格与研究归档','body':content}
    for n in range(19):
        sid = f'M{n:02}'
        sections.setdefault(sid, {'title': TITLES[n], 'body': '本轮证据不足，需补充对应来源后判断。'})
    # Replace only knowledge citations, preserving research and keyword history.
    for sid in list(sections):
        section=sections[sid]
        if '知识库依据' in section['title']:
            sections.pop(sid)
            continue
        pattern=r'^(?:[-*]\s*)?(?:选品库|产品推广库|广告库)\s+(?:KC|PK|AK)-\d+\s*[：:]'
        if section.get('format')=='html':
            soup=BeautifulSoup(section['body'],'html.parser')
            for node in list(soup.find_all(['p','li'])):
                if re.match(pattern,node.get_text(' ',strip=True)):node.decompose()
            section['body']=str(soup)
        else:
            section['body']='\n'.join(line for line in section['body'].splitlines() if not re.match(pattern,line.strip()))
    sections.update(build_knowledge_sections(root) if knowledge_sections is None else knowledge_sections)
    for section in sections.values():
        section['body'] = reader_text(portable_links(compact_missing_tables(normalize_reader_cells(section['body'])),root))
        section['title']=reader_text(section['title'])
    dates=[str(x.get('observed_at') or x.get('data_date'))[:10] for x in facts.get('demand_evidence',[]) if x.get('observed_at') or x.get('data_date')]
    dates += [str(x)[:10] for x in (cost.get('evaluation_date'),decision.get('generated_at')) if x]
    evidence_date=max((x for x in dates if re.fullmatch(r'\d{4}-\d{2}-\d{2}',x)),default=None)
    order=[]
    if (root/HTML).is_file():
        order=[s['id'] for s in BeautifulSoup((root/HTML).read_text(encoding='utf8'),'html.parser').select('section.report-section[id]')]
    ordered=dict(sorted(sections.items(),key=lambda pair:(order.index(pair[0]) if pair[0] in order else len(order),pair[0])))
    manifest=source_manifest(root,facts)
    if decision_model is not None:
        # Compact mode stores this model in the receipt, never in this legacy
        # sidecar. The receipt validates its own model against real inputs.
        manifest.pop((OVERVIEW/'s1_decision_model.json').as_posix(),None)
    model={'schema_version': 1, 'official_report_count': 1, 'product_name': design.get('product_name', '待确认产品'),
            'design': design, 'sections': ordered, 'facts': facts,
            'root':str(CompactCase(root).logical_root),'decision':decision,'evidence_date':evidence_date,
            'source_manifest':manifest,'source_manifest_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'merged_research_sources':[{k:v for k,v in note.items() if k!='body'} for note in notes],
            'presentation_contract': load(root / OVERVIEW / '报告呈现约定.yaml')}
    from stage_acceptance_summary import acceptance_summary
    model['plugin_identity']=plugin_identity(Path(__file__).resolve().parents[1])
    from s1_report_v2 import compose,native_display_models
    product=load(root/STAGE/'1688-product-detail-scraper/03_product_facts.yaml')
    ct,final_ruler=native_display_models(Path(__file__).resolve().parents[1],
        load(root/STAGE/'1688-product-detail-scraper/03_product_ct.yaml'),product,ruler)
    model['report_v2']=compose(model,inputs=inputs,
        ct=ct,product=product,ruler=final_ruler,cost=cost,checklist=purchase_checklist(root),
        acceptance=acceptance_summary(root,load(root/'workflow_state.yaml'),'S1'))
    if model['report_v2'].get('product_name'):model['product_name']=model['report_v2']['product_name']
    source=model['report_v2']['front'].get('image_source')
    if source and (root/source).is_file():
        model['source_manifest'][source]=hashlib.sha256((root/source).read_bytes()).hexdigest()
        model['source_manifest_sha256']=hashlib.sha256(json.dumps(model['source_manifest'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    from s1_report_modules import restore_modules
    restore_modules(model, root, cost=cost, ruler=final_ruler, product=product, inputs=inputs)
    model['source_manifest_sha256']=hashlib.sha256(json.dumps(model['source_manifest'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return model


def purchase_checklist(root):
    from r01_case_store import CompactCase
    template=load(Path(__file__).resolve().parents[1]/'templates/s1_purchase_checklist.yaml')
    store=CompactCase(root);logical=store.resolve('s1_purchase_checklist.yaml')
    with store.materialize() as view:
        saved=load(view.map_path(logical)).get('checks') or {}
    for group in template.get('groups') or []:
        for item in group.get('items') or []:item['checked']=saved.get(item['id']) is True
    return template


def save_purchase_checklist(root,updates):
    """Persist only checklist booleans; render in the same native view before commit."""
    from r01_case_store import CompactCase
    from sync_s1_decision_reports import write_transaction,prepare_sync_manifest
    store=CompactCase(root);root=store.logical_root
    allowed={item['id'] for group in purchase_checklist(root).get('groups') or [] for item in group.get('items') or []}
    if not isinstance(updates,dict) or not updates or any(key not in allowed or type(val) is not bool for key,val in updates.items()):
        raise ValueError('采购清单只接受已知项目ID和布尔勾选状态')
    saved=load(root/'s1_purchase_checklist.yaml');checks=saved.get('checks') or {};checks.update(updates)
    data={'schema_version':1,'checks':checks,'purchase_authorized':False}
    raw=yaml.safe_dump(data,allow_unicode=True,sort_keys=False).encode('utf8')
    outputs={root/'s1_purchase_checklist.yaml':raw}
    native_report=False
    with store.materialize(overrides={'s1_purchase_checklist.yaml':raw}) as view:
        receipts=sorted(root.glob('00_总控/*/运行数据/流程控制/S1/receipts/09.json'))
        receipt=next((p for p in receipts if load(p).get('s1_stage_data')),None)
        if receipt is not None:
            old=load(receipt).get('s1_stage_data') or {}
            staged=_prepare_compact_native(view.root,view.map_path(receipt),knowledge_verification=old.get('knowledge_verification'))
            outputs.update({root/p.relative_to(view.root):content for p,content in staged.items()})
        elif (root/MD).is_file():
            native_report=True
            model=build_sections(view.root)
            outputs[root/MD]=render_markdown(model).encode('utf8')
            outputs[root/HTML]=render_html(model).encode('utf8')
            outputs[root/OVERVIEW/'s1_stage_report.json']=json.dumps(model,ensure_ascii=False,indent=2,default=str).encode('utf8')
            revision=load(view.root/OVERVIEW/'S1_统一报告修订记录.yaml')
            revision.update(source_manifest=model['source_manifest'],source_manifest_sha256=model['source_manifest_sha256'])
            outputs[root/OVERVIEW/'S1_统一报告修订记录.yaml']=yaml.safe_dump(revision,allow_unicode=True,sort_keys=False).encode('utf8')
    sync_outputs=prepare_sync_manifest(root,outputs) if native_report else {}
    outputs.update(sync_outputs)
    originals={path:path.read_bytes() if path.is_file() else None for path in outputs}
    try:
        write_transaction(root,outputs,preserve_history=False)
        if sync_outputs:
            from s1_decision_report import validate_output_set
            if not validate_output_set(root):raise ValueError('采购报告同步后决策来源校验失败；已回滚')
    except BaseException:
        for path,previous in originals.items():
            if previous is None:path.unlink(missing_ok=True)
            else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(previous)
        raise
    return {'ok':True,'checklist':data,'report_updated':root/MD in outputs}


def render_markdown(model):
    if model.get('report_v2') and model.get('module_contract') != 'M00-M18':
        from s1_report_v2 import render_md
        from s1_readability import reader_projection
        return reader_projection(render_md(model['report_v2'],model['product_name']))
    front = ''
    if model.get('report_v2'):
        from s1_report_v2 import decision_headline
        front = '**唯一结论：'+decision_headline(model['report_v2']['decision_summary'])+'**\n\n'
    return '# S1 选品可行性研究报告｜' + cell(model['product_name']) + '\n\n' + front + '\n\n'.join(
        ('## ' + section_heading(sid, section['title']) + '\n\n') +
        re.sub(r'(?m)^#{1,2}(?=[ \t])','###',
               html_research_markdown(section['body']) if section.get('format')=='html' else section['body'])
        for sid, section in model['sections'].items()) + '\n'


def html_research_markdown(body):
    """Preserve HTML-only legacy evidence in the shared Markdown representation."""
    soup=BeautifulSoup(body,'html.parser')
    for t in list(soup.find_all('table')):
        rows=[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'])] for tr in t.find_all('tr')]
        if rows:t.replace_with('\n\n'+table(rows[0],rows[1:])+'\n\n')
    for image in list(soup.find_all('img')):
        image.replace_with('\n!['+str(image.get('alt','参考图'))+'](<'+str(image.get('src',''))+'>)\n')
    for tag in soup.find_all(['p','h3','h4','li','summary']):tag.append('\n\n')
    return soup.get_text(' ',strip=False).strip()


def render_html(model):
    if model.get('report_v2'):return render_html_v2(model)
    assets = Path(__file__).resolve().parents[1] / 'skills/github-tabler-html-report/assets'
    sys.path.insert(0, str(assets))
    # Source checkout has a delivery override; installed packages have the asset.
    override = Path(__file__).resolve().parents[3] / 'release-overrides'
    if override.is_dir(): sys.path.insert(0, str(override))
    from salon_report import Report
    r = Report('S1 选品可行性研究报告 · ' + cell(model['product_name']), '设计、市场、货源与条件利润，一份报告对照', stage='S1')
    design = model['design']
    decision=model.get('decision') or {}
    if decision.get('verdict'):
        r.product_decision(product_name=model['product_name'], product_id=str(design.get('selection') or '本轮设计'),
        image_path=None, image_source='用户设计与选款记录', image_role='specification',
        specifications=design.get('features') or [model['product_name']], verdict=decision['verdict'],
        conclusion=decision['label'], next_action=cell(decision.get('next_action')), evidence_date=model.get('evidence_date') or '未取得',
        blockers=decision.get('blockers') or [])
        front=BeautifulSoup(r.product_front,'html.parser')
        label=front.select_one('.decision-label')
        if label:label.string='是否可以做：'+decision.get('label','尚未形成决策')
        r.product_front=str(front)
    else:
        r.product_front='<section id="s1-product-decision" class="s1front"><h2>'+html.escape(model['product_name'])+'</h2><p>尚未形成决策</p></section>'
    for sid, section in model['sections'].items():
        match=re.match(r'M(\d{2})',sid)
        n = int(match[1]) if match else 99
        group = 'overview' if n <= 7 else 'comparison' if n in (8, 9, 10, 13, 15) else 'economics' if n in (11, 12, 16) else 'evidence'
        body=section['body']
        # Markdown paths are relative to _阶段总览; HTML lives one directory deeper.
        body=re.sub(r'(!?\[[^\]]*\]\()<?([^)>]+)>?(\))',lambda m:m[1]+'<'+('../'+m[2] if not re.match(r'\w+:|/|#',m[2]) else m[2])+'>'+m[3],body)
        rendered = r.section(sid, section_heading(sid, section['title']), group=group)
        rendered.raw(body) if section.get('format')=='html' else rendered.markdown(body)
        if sid == 'M03':
            f = model['facts']
            rendered.kpis([('竞品详情', str(len(f.get('competitors', [])))), ('父体代表', str(len(f.get('representatives', [])))),
                           ('匹配货源', str(len(f.get('supplier_shortlist', [])))), ('待核货源', str(len(f.get('supplier_excluded', []))))])
            coverage = f.get('coverage') or {}
            pairs = [(COVERAGE_LABELS.get(k,'其他覆盖项目'), min(v.get('observed') or 0, v.get('required') or 1) / max(1, v.get('required') or 1) * 100) for k, v in coverage.items() if isinstance(v, dict)]
            rendered.hbars_svg('本轮数据覆盖比例（%）', pairs)
        if sid == 'M09': rendered.hbars_svg('父体去重后的价格分位（USD）', [(k, v) for k, v in model['facts'].get('price_bands', {}).items() if isinstance(v, (int, float))])
        if sid == 'M10': rendered.hbars_svg('父体代表月销估计（工具样本）', [(x.get('asin'), x['monthly_sales']) for x in model['facts'].get('representatives', []) if isinstance(x.get('monthly_sales'), (int, float))][:10])
    generated=r.render()
    old_path=Path(model.get('root',''))/HTML
    contract=model.get('presentation_contract') or {}
    if not old_path.is_file() or not contract:
        return generated
    old=BeautifulSoup(old_path.read_text(encoding='utf8'),'html.parser')
    new=BeautifulSoup(generated,'html.parser')
    container=old.find(id='report-content')
    if container is None:
        raise ValueError('已确认呈现约定存在，但原HTML缺少report-content；不能安全保留布局')
    for prior in list(container.select('section.report-section[id]')):
        heading=prior.find(['h2','h3'])
        if prior['id'] not in model['sections'] and heading and '知识库依据' in heading.get_text():
            prior.decompose()
    for section in new.select('#report-content > section[id]'):
        previous=container.find(id=section['id'])
        if previous:
            # Keep user-confirmed classes/style/group and replace only current content.
            previous.clear()
            for child in list(section.contents):previous.append(child)
        else:container.append(section)
    # Keep navigation positions and theme; update its links to the actual section set.
    for selector in ('.elevator','.full-index'):
        prior=old.select_one(selector);fresh=new.select_one(selector)
        if prior and fresh:prior.replace_with(fresh)
    header=old.select_one('.hero')
    if header:
        fresh=new.select_one('.hero')
        if fresh:header.replace_with(fresh)
    # Retain the approved shell and custom CSS; replace executable framework code.
    # Latest framework styles follow legacy rules so mobile/navigation fixes take effect.
    for style in list(old.select('style[data-s1-framework]')):style.decompose()
    if old.head:
        for style in new.find_all('style'):
            style['data-s1-framework']='current'
            old.head.append(style)
    for script in list(old.find_all('script')):
        if script.get('type','').lower() not in {'application/json','application/ld+json'}:script.decompose()
    for script in new.find_all('script'):
        if old.body:old.body.append(script)
    return str(old)


def render_html_v2(model):
    from s1_report_v2 import render_front,CSS,render_purchase,PURCHASE_JS
    before=sys.path[:]
    try:
        assets=Path(__file__).resolve().parents[1]/'skills/github-tabler-html-report/assets'
        sys.path.insert(0,str(assets))
        override=Path(__file__).resolve().parents[3]/'release-overrides'
        if override.is_dir():sys.path.insert(0,str(override))
        from salon_report import Report,markdown_html
    finally:sys.path[:]=before
    report=model['report_v2']
    renderer=Report('S1 选品可行性研究报告 · '+cell(model['product_name']),'同一份结构化事实，M00–M18逐项核对',stage='S1')
    renderer.product_front=render_front(report,model['product_name'])
    chapters = ([{'id':sid,'title':section_heading(sid,s['title']),'body':s['body']} for sid,s in model['sections'].items()]
                if model.get('module_contract') == 'M00-M18' else report['chapters'])
    for index,chapter in enumerate(chapters,1):
        body=re.sub(r'(!?\[[^\]]*\]\()<?([^)>]+)>?(\))',lambda m:m[1]+'<'+('../'+m[2] if not re.match(r'\w+:|/|#',m[2]) else m[2])+'>'+m[3],chapter['body'])
        title=chapter['title'] if model.get('module_contract') == 'M00-M18' else str(index)+' '+chapter['title']
        section=renderer.section(chapter['id'],title,group='comparison' if index<4 else 'economics' if index<6 else 'evidence')
        if chapter['id']=='purchase':section.raw(render_purchase(report['purchase_checklist']))
        elif chapter['id']=='M06':
            section.markdown(body.split('本节为采购准备清单',1)[0])
            section.raw(render_purchase(report['purchase_checklist']))
        else:section.markdown(body)
        if chapter['id'] in ('cost','M12'):section.raw('<p id="margin-calculator"><a data-calculator-link href="#margin-calculator">打开毛利计算器：点导航底部“💰 毛利计算”</a></p>')
    appendix=re.sub(r'(!?\[[^\]]*\]\()<?([^)>]+)>?(\))',lambda m:m[1]+'<'+('../'+m[2] if not re.match(r'\w+:|/|#',m[2]) else m[2])+'>'+m[3],report['appendix'])
    if model.get('module_contract') != 'M00-M18':
        renderer.section('appendix','附录',group='evidence').raw('<details class="m2-appendix"><summary>数据来源、证据索引、知识库与验收</summary>'+markdown_html(appendix)+'</details>')
    page=renderer.render().replace('</style>',CSS+'</style>',1).replace('</body>',PURCHASE_JS+'</body>',1)
    from s1_margin_calculator import existing_url
    calculator=existing_url(model['root'])
    if calculator:
        page=page.replace('data-calculator-link href="#margin-calculator"','data-calculator-link href="'+html.escape(calculator,quote=True)+'"',1)
        page=page.replace('从毛利计算器的本地报告入口打开，即可保存到当前产品。','<a href="'+html.escape(calculator+'report',quote=True)+'">打开本地报告并保存采购进度</a>',1)
    old=Path(model['root'])/HTML
    if old.is_file() and model.get('presentation_contract'):
        prior=BeautifulSoup(old.read_text(encoding='utf8'),'html.parser')
        for style in prior.select('style[id]'):page=page.replace('</head>',str(style)+'</head>',1)
        theme=prior.body.get('data-theme') if prior.body else None
        if theme:page=page.replace('data-theme="mint"','data-theme="'+html.escape(theme,quote=True)+'"',1)
    layout=BeautifulSoup(page,'html.parser')
    for image in layout.select('#M13 img[src]'):
        if image.parent.name!='a':
            link=layout.new_tag('a',href=image['src'],target='_blank')
            image.wrap(link)
    layout.body['class']=list(layout.body.get('class',[]))+['s1-m2']
    front=layout.find(id='s1-product-decision')
    if model.get('module_contract') == 'M00-M18':
        mapping={'market':'M08','product':'M13','ip':'M14','promotion':'M12','cost':'M12','risk':'M07','purchase':'M06'}
        for anchor in front.select('a[href]'):
            key=anchor['href'].lstrip('#')
            if key in mapping:anchor['href']='#'+mapping[key]
    # Read controls remain usable below the answer; they must not displace it.
    controls=[layout.select_one(selector) for selector in ('.report-toolbar','.reader-nav','.full-index')]
    for control in reversed([x for x in controls if x is not None]):
        control.extract()
        if 'full-index' in control.get('class',[]):control.attrs.pop('open',None)
        front.insert_after(control)
    return str(layout).replace('<!DOCTYPE html>\n','<!doctype html>',1).replace('<meta charset="utf-8"/>','<meta charset="utf-8">',1)


def superseded_paths(root):
    root = Path(root).resolve(); stage = root / STAGE
    for path in list(stage.rglob('*')):
        if not path.is_file() or path.suffix not in ('.md', '.html'): continue
        if '_history' in path.parts or any(part.startswith('.') for part in path.relative_to(stage).parts): continue
        if not any(term in path.stem for term in ('纠偏版', '后补版', '人工复核版', '04_产品开发决策透视')): continue
        yield path


def archive_plan(root):
    root=Path(root).resolve(); stage=root/STAGE
    changes=[]
    for path in superseded_paths(root):
        raw = path.read_bytes()
        dest = stage / '_history' / ('superseded-' + hashlib.sha256(raw).hexdigest()[:12]) / path.relative_to(stage)
        if dest.exists() and dest.read_bytes() != raw: raise ValueError('历史归档内容冲突')
        changes.append({'from': path.relative_to(root).as_posix(), 'to': dest.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()})
    return changes


def affected_paths(root):
    root=Path(root).resolve()
    return [root/MD,root/HTML,root/OVERVIEW/'s1_stage_report.json',root/OVERVIEW/'S1_统一报告修订记录.yaml']+[
        root/c[k] for c in archive_plan(root) for k in ('from','to')]


def archive_superseded(root):
    """Compatibility entry point; archives are byte-verified before any source removal."""
    from sync_s1_decision_reports import write_transaction
    root=Path(root).resolve(); changes=archive_plan(root)
    originals={root/c['from']:(root/c['from']).read_bytes() for c in changes}
    try:
        write_transaction(root,{root/c['to']:originals[root/c['from']] for c in changes})
        for path in originals:path.unlink()
    except BaseException:
        for path,raw in originals.items():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        raise
    return changes


def prepare_compact(root, receipt_path, *, knowledge_verification=None):
    from r01_case_store import CompactCase
    store=CompactCase(root)
    receipt_path=store.resolve(receipt_path)
    with store.materialize() as view:
        outputs=_prepare_compact_native(view.root,view.map_path(receipt_path),knowledge_verification=knowledge_verification)
        return {view.logical_root/path.relative_to(view.root):raw for path,raw in outputs.items()}


def _prepare_compact_native(root, receipt_path, *, knowledge_verification=None):
    """Return a three-file transaction, keeping models/history in one receipt.

    No write occurs here. Old reader files are retained byte-for-byte inside
    the receipt before their first compact replacement; automatic later drafts
    replace the working final rather than multiplying history snapshots.
    """
    import s1_decision_report as decision_report
    root=Path(root).resolve()
    receipt_path=(root/receipt_path).resolve()
    if not receipt_path.is_relative_to(root) or receipt_path in (root/MD,root/HTML):
        raise ValueError('紧凑回执路径越界或覆盖报告')
    document=load(receipt_path)
    previous=document.get('s1_stage_data') or {}
    decision=decision_report.generate(root,compact=True)['model']
    old_decision=previous.get('decision_model') or {}
    if ({k:v for k,v in decision.items() if k!='generated_at'} ==
            {k:v for k,v in old_decision.items() if k!='generated_at'}):
        decision['generated_at']=old_decision['generated_at']
    knowledge_sections=None
    if knowledge_verification is not None:
        status=str(knowledge_verification.get('status','pending'))
        lines=['阶段引用核验：'+{'pass':'通过','fail':'未通过','pending':'待核'}.get(status,status)+'。', '',
               '<details><summary>逐步引用明细（点开查看）</summary>', '']
        for number,result in knowledge_verification.get('steps',{}).items():
            for ref in result.get('refs',[]):
                lines.append('- 第'+str(number)+'步 '+ref['library']+' '+ref['card_id']+'：本步已登记引用，阶段统一核验。')
        lines.extend('- '+str(error) for error in knowledge_verification.get('errors',[]))
        lines += ['', '</details>']
        knowledge_sections={'knowledge-evidence':{'title':'知识库依据与引用核验','body':'\n'.join(lines)}}
    model=build_sections(root,decision_model=decision,knowledge_sections=knowledge_sections,archive_sources=False)
    original_human=None
    old_human=document.get('human_report')
    if old_human and old_human != MD.as_posix():
        path=(root/old_human).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('既有完整业务报告缺失或越界：'+str(old_human))
        raw=path.read_bytes()
        original_human=(old_human,raw)
        model['source_manifest'][path.relative_to(root).as_posix()]=hashlib.sha256(raw).hexdigest()
    for note in model['merged_research_sources']:
        model['source_manifest'][note['archive_path']]=note['sha256']
    model['source_manifest_sha256']=hashlib.sha256(json.dumps(model['source_manifest'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    from s1_report_links import portable_links as output_links
    md=output_links(root,root/MD,render_markdown(model)).encode('utf8')
    page=output_links(root,root/HTML,render_html(model)).encode('utf8')
    preserved=copy.deepcopy(previous.get('preserved_reports') or {})
    if original_human:
        relative,raw=original_human
        preserved[relative]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes_base64':base64.b64encode(raw).decode('ascii'),'kind':'native_report_before_m2_composition'}
    if not previous:
        for relative in (MD,HTML):
            path=root/relative
            if path.is_file():
                raw=path.read_bytes()
                preserved[relative.as_posix()]={'sha256':hashlib.sha256(raw).hexdigest(),
                    'bytes_base64':base64.b64encode(raw).decode('ascii'),'kind':'pre_compact_original'}
    outputs={MD.as_posix():hashlib.sha256(md).hexdigest(),HTML.as_posix():hashlib.sha256(page).hexdigest()}
    data={'schema_version':1,'decision_model':decision,'stage_model':model,
          'source_manifest':model['source_manifest'],'outputs_sha256':outputs,
          'preserved_reports':preserved,'knowledge_verification':knowledge_verification,
          'revision':{'generated_at':decision['generated_at'],'source_manifest_sha256':model['source_manifest_sha256']},
          'business_approval':'unchanged; report generation never grants purchase or stage approval'}
    history_outputs = {}
    for relative, saved in preserved.items():
        if 'bytes_base64' in saved:
            raw = base64.b64decode(saved['bytes_base64'], validate=True)
            history = '_history/S1/09/final/' + relative
            history_outputs[root/history] = raw
            kind=saved.get('kind')
            saved.clear()
            saved.update(path=history, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
            if kind:saved['kind']=kind
    model_path = 'S1_选品板块/_阶段总览/s1_stage_report.json'
    model_bytes = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf8')
    document['s1_stage_data']={'external_path': model_path, 'sha256': hashlib.sha256(model_bytes).hexdigest(), 'size': len(model_bytes)}
    document['human_report']=MD.as_posix()
    old_proof=document.get('report_proof') or {}
    # A formal stage report has its own renderer and source manifest.  It is
    # not the generic per-step concatenation represented by projection v1.
    document['report_proof']={'kind':'s1_stage_report_v1','path':MD.as_posix(),
                              'sha256':outputs[MD.as_posix()],
                              'sources':old_proof.get('sources',[]),
                              'source_manifest_sha256':model['source_manifest_sha256']}
    # The physical outputs and their receipt seals form one transaction. Leaving
    # the prior metadata here made the next normal case read reject the report
    # that this very generator had just written.
    current_files={MD.as_posix():md,HTML.as_posix():page,model_path:model_bytes}
    metadata={relative:{'path':relative,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
              for relative,raw in current_files.items()}
    changed=any(document.get('business_files',{}).get(relative)!=item for relative,item in metadata.items())
    document.setdefault('business_files',{}).update(metadata)
    snapshot=document.setdefault('business_snapshot',{})
    snapshot.update(stage='S1',step=9)
    snapshot['schema_version']=2
    if changed:
        snapshot['revision']=int(snapshot.get('revision',0))+1
        from r01_case_store import CompactCase
        snapshot['case_sequence']=CompactCase(root)._max_sequence()+1
    for relative in current_files:
        document.get('restored_disk_files',{}).pop(relative,None)
    reports=[row for row in document.get('reports',[]) if row.get('path') not in {MD.as_posix(),HTML.as_posix(),old_human}]
    document['reports']=[{'label':HTML.name,'path':HTML.as_posix()},
                         {'label':MD.name,'path':MD.as_posix()},*reports]
    return {**history_outputs, root/model_path:model_bytes, root/MD:md,root/HTML:page,receipt_path:json.dumps(document,ensure_ascii=False,indent=2,default=str).encode('utf8')}


def validate_compact_output_set(root, receipt_path):
    from r01_case_store import CompactCase
    store=CompactCase(root)
    try:
        receipt_path=store.resolve(receipt_path)
        with store.materialize() as view:
            return _validate_compact_output_set_native(view.root,view.map_path(receipt_path))
    except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError):
        return False


def _validate_compact_output_set_native(root, receipt_path):
    """Check actual inputs, source manifest and both reader outputs from a receipt."""
    import s1_decision_report as decision_report
    root=Path(root).resolve()
    try:
        receipt_path=(root/receipt_path).resolve()
        if not receipt_path.is_relative_to(root):return False
        data=load(receipt_path)['s1_stage_data']
        manifest=data['source_manifest']
        if manifest!=data['stage_model']['source_manifest']:return False
        manifest_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if manifest_hash!=data['stage_model']['source_manifest_sha256']:return False
        decision=data['decision_model']
        current=decision_report.generate(root,compact=True)['model']
        if {k:v for k,v in current.items() if k!='generated_at'}!={k:v for k,v in decision.items() if k!='generated_at'}:
            return False
        for relative,expected in data['source_manifest'].items():
            source=(root/relative).resolve()
            if not source.is_relative_to(root):return False
            observed=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
            if observed!=expected:return False
        if set(data['outputs_sha256'])!={MD.as_posix(),HTML.as_posix()}:return False
        for relative,expected in data['outputs_sha256'].items():
            if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=expected:return False
        for saved in data.get('preserved_reports',{}).values():
            if 'bytes_base64' in saved:
                raw=base64.b64decode(saved['bytes_base64'],validate=True)
            else:
                source=(root/saved['path']).resolve()
                if not source.is_relative_to(root):return False
                raw=source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=saved['sha256']:return False
        seal=data.get('knowledge_verification') or {}
        if seal:
            if seal.get('status')!='pass':return False
            from r01_persistence import selected_sha256
            for row in seal.get('response_hashes',[]):
                source=(root/row['path']).resolve()
                if not source.is_relative_to(root):return False
                if selected_sha256(source,row.get('selector'))!=row['sha256']:return False
        return True
    except (OSError,ValueError,KeyError,TypeError,yaml.YAMLError):
        return False


def rebuild_compact(root, receipt_path, *, knowledge_verification=None):
    """Discard only invalidated generated seals, regenerate from verified sources.

    Unlike a normal read, this recovery entry can open a contradictory step9
    envelope. Its source seals are still mandatory; old bytes are archived,
    not blessed with replacement hashes. The renderer writes the new full set.
    """
    from r01_case_store import CompactCase, decode_business_files
    from sync_s1_decision_reports import rollback_transaction, write_transaction
    root = Path(root).resolve()
    store = CompactCase(root)
    receipt_path = store.resolve(receipt_path)
    document = json.loads(receipt_path.read_bytes()) if receipt_path.is_file() else {}
    generated = {MD.as_posix(), HTML.as_posix(), 'S1_选品板块/_阶段总览/s1_stage_report.json'}
    sources = {p: item for p, item in document.get('business_files', {}).items() if p not in generated}
    decode_business_files(sources)  # Validate shape; latest registered owner is checked below.
    with rollback_transaction(root):
        archive = root / '_history/S1/09/rebuild' / hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        outputs = {}
        archived_sources = {}
        for relative in generated | {receipt_path.relative_to(root).as_posix()}:
            path = store.resolve(relative)
            if path.is_file():
                raw = path.read_bytes()
                name = ('receipt.json.backup' if path == receipt_path else 'model.json.source' if path.suffix == '.json'
                        else 'report' + path.suffix)
                saved = archive / name
                if saved.is_file() and saved.read_bytes() != raw:
                    raise ValueError('重建历史内容地址冲突，原文件保留')
                outputs[saved] = raw
                archived_sources[relative] = {'path': saved.relative_to(root).as_posix(),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        document['business_files'] = sources
        for key in ('s1_stage_data', 'report_proof', 'human_report'):
            document.pop(key, None)
        for relative in generated:
            document.get('restored_disk_files', {}).pop(relative, None)
        document.setdefault('rebuild_history', []).append({'archive': archive.relative_to(root).as_posix(),
            'sources': archived_sources,
            'reason': 'official step9 regeneration from verified source seals; prior generated outputs invalidated'})
        outputs[receipt_path] = json.dumps(document, ensure_ascii=False, indent=2).encode('utf8')
        write_transaction(root, outputs, preserve_history=False)
        # A legitimate reopened earlier step may now own a newer input seal.
        # The normal index verifies that owner's actual bytes; never compute a
        # replacement source hash from unregistered physical edits.
        try:
            latest = store._index()
        except ValueError as error:
            raise ValueError('rebuild 来源核验失败：' + str(error)) from error
        for relative in sources:
            if relative not in latest:
                raise ValueError('rebuild 缺少已登记来源：' + relative)
            document['business_files'][relative] = copy.deepcopy(latest[relative][2])
            document.get('restored_disk_files', {}).pop(relative, None)
        write_transaction(root, {receipt_path: json.dumps(document, ensure_ascii=False, indent=2).encode('utf8')}, preserve_history=False)
        return run(root, compact=True, receipt_path=receipt_path, knowledge_verification=knowledge_verification)


def run(root, *, compact=False, receipt_path=None, knowledge_verification=None,sync_manifest=True):
    from sync_s1_decision_reports import write_transaction,prepare_sync_manifest
    from r01_case_store import CompactCase
    store=CompactCase(root)
    if receipt_path is not None:receipt_path=store.resolve(receipt_path)
    root = store.logical_root
    if compact:
        if receipt_path is None:raise ValueError('紧凑阶段生成需要指定现有流程回执路径')
        outputs=prepare_compact(root,receipt_path,knowledge_verification=knowledge_verification)
        # Pre-compact originals are already retained inside this transaction's
        # receipt, so creating per-file .s1-history duplicates is unnecessary.
        write_transaction(root,outputs,preserve_history=False)
        return {'ok':True,'compact':True,'md':str(root/MD),'html':str(root/HTML),
                'receipt':str((root/receipt_path).resolve()),'output_files':[str(p) for p in outputs]}
    model = build_sections(root)
    md, page = render_markdown(model), render_html(model)
    from s1_report_links import portable_links as output_links
    md=output_links(root,root/MD,md)
    page=output_links(root,root/HTML,page)
    changes=archive_plan(root)
    outputs = {root / MD: md.encode(), root / HTML: page.encode(),
               root / OVERVIEW / 's1_stage_report.json': json.dumps(model, ensure_ascii=False, indent=2, default=str).encode()}
    revision_path=root/OVERVIEW/'S1_统一报告修订记录.yaml'
    previous=load(revision_path)
    archived={c['from']+'|'+c['sha256']:c for c in previous.get('archived',[])+changes}
    revision = {'generated_at': datetime.now(timezone.utc).isoformat(), 'archived': list(archived.values()),
                'md_sha256': hashlib.sha256(md.encode()).hexdigest(), 'html_sha256': hashlib.sha256(page.encode()).hexdigest(),
                'presentation_contract': model['presentation_contract'],'source_manifest':model['source_manifest'],
                'source_manifest_sha256':model['source_manifest_sha256'],
                'merged_research_sources':model['merged_research_sources']}
    outputs[revision_path]=yaml.safe_dump(revision, allow_unicode=True, sort_keys=False).encode()
    for c in changes:outputs[root/c['to']]=(root/c['from']).read_bytes()
    sync_outputs=prepare_sync_manifest(root,outputs) if sync_manifest else {}
    outputs.update(sync_outputs)
    originals={path:path.read_bytes() if path.is_file() else None for path in set(outputs)|{root/c['from'] for c in changes}}
    try:
        write_transaction(root, outputs)
        for c in changes:(root/c['from']).unlink()
        if sync_outputs:
            from s1_decision_report import validate_output_set
            if not validate_output_set(root):raise ValueError('阶段报告同步后决策来源校验失败；已回滚')
    except BaseException:
        for path,raw in originals.items():
            if raw is None:path.unlink(missing_ok=True)
            else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        raise
    return {'ok': True, 'md': str(root / MD), 'html': str(root / HTML), 'archived': len(changes)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root', required=True)
    parser.add_argument('--compact-receipt',help='仅写阶段 MD / HTML 和此流程回执；模型与修订记录合入回执')
    args=parser.parse_args()
    # S1-R22: rewriting the step 9 receipt after it was recorded leaves the state on an older data version
    # and continue-s2 later fails as "superseded". The flow's own record/rebuild call run() directly.
    flow_state=load(Path(args.root)/'workflow_state.yaml') if (Path(args.root)/'workflow_state.yaml').is_file() else {}
    if args.compact_receipt and any(row.get('n')==9 for row in ((flow_state.get('s1_execution') or {}).get('records') or [])):
        raise SystemExit('第 9 步已经登记，不能直接重新生成它的回执（会让登记和回执的数据版本对不上）。'
                         '要改报告：先 workflow_flow.py reopen --stage S1 --steps 9 --reason "<用户原话>"，再 begin/record 第 9 步；'
                         '只想重出 HTML/MD 用 workflow_flow.py rebuild --stage S1 --step 9')
    print(json.dumps(run(args.root,compact=bool(args.compact_receipt),receipt_path=args.compact_receipt),ensure_ascii=False,indent=2))
