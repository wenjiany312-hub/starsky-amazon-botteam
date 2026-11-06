#!/usr/bin/env python3
"""Reconcile IP narrative with the ruler, preserving original evidence and citations by hash."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location('ip_sync_ruler', Path(__file__).with_name('compliance_ruler.py'))
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)
REPORT = '04_IP风险排查与专利分析.md'
MANIFEST = '04_ip_report_sync.json'
START, END = '<!-- IP_CURRENT_START -->', '<!-- IP_CURRENT_END -->'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'来源路径超出产品根：{relative}')
    return path


def sections(text):
    # Generated current findings must never become historical evidence on reruns.
    text = re.sub(re.escape(START) + r'.*?' + re.escape(END), '', text, flags=re.S)
    chunks = re.split(r'^##\s+(.+?)\s*$', text, flags=re.M)
    return [{'title': chunks[n], 'body': chunks[n + 1].strip()} for n in range(1, len(chunks), 2)
            if chunks[n].strip() != '保留的研究与检索记录']


def compact_unknown_tables(body):
    lines = body.splitlines()
    output, index = [], 0
    while index < len(lines):
        if not lines[index].startswith('|'):
            output.append(lines[index])
            index += 1
            continue
        end = index
        while end < len(lines) and lines[end].startswith('|'):
            end += 1
        table = lines[index:end]
        rows = [[cell.strip() for cell in line.strip('|').split('|')] for line in table[2:]]
        # Only collapse an entirely unscored matrix, never a table with real scores/results.
        empty = rows and all(re.search(r'null|未知|待核|无法.*评|不可.*评', row[-1], re.I) for row in rows)
        if empty:
            output += [f'以下 {len(rows)} 个维度尚不能评分：' + '、'.join(row[0] for row in rows) + '。',
                       '共同缺口：' + '；'.join(dict.fromkeys(cell for row in rows for cell in row[1:-1])) + '。']
        else:
            output += table
        index = end
    return re.sub(r'(?<![A-Za-z0-9_])null(?![A-Za-z0-9_])', '待核', '\n'.join(output), flags=re.I)


def response_cards(payload):
    if not isinstance(payload, dict):
        return []
    structured = payload.get('structuredContent')
    if isinstance(structured, dict):
        return structured.get('rules') or structured.get('cards') or []
    if isinstance(payload.get('rules'), list):
        return payload['rules']
    for block in payload.get('content') or []:
        if block.get('type') == 'text':
            try:
                cards = response_cards(json.loads(block['text']))
                if cards:
                    return cards
            except (ValueError, TypeError):
                continue
    return []


def verified_knowledge(root):
    evidence = root / 'S1_选品板块/知识库依据/S1/08/kb_evidence.yaml'
    used, rejected, sources = [], [], []
    if not evidence.is_file():
        return used, rejected, sources
    sources.append(evidence)
    for call in cr.load_yaml(evidence).get('calls') or []:
        ids = call.get('card_ids') or []
        if not ids:
            continue
        rel = call.get('response_path')
        try:
            response = safe_path(root, rel) if rel else None
        except ValueError:
            response = None
        from verify_kb_evidence import VerificationCache
        cache = VerificationCache()
        try:
            valid = (call.get('status') == 'ok' and response and response.is_file()
                     and cache.sha256(response, call.get('response_selector')) == call.get('response_sha256'))
        except (ValueError, KeyError, IndexError, TypeError):
            valid = False
        if not valid:
            rejected += [{'card_id': card_id, 'reason': '知识库响应缺失或 SHA-256 不符'} for card_id in ids]
            continue
        sources.append(response)
        try:
            cards = {card.get('id'): card for card in response_cards(cache.response(response, call.get('response_selector')))}
        except (ValueError, TypeError):
            cards = {}
        for card_id in ids:
            card = cards.get(card_id) or {}
            direct_ip = any(hook.get('skill') == 'amazon-ip-risk-assessment' for hook in card.get('starsky_hooks') or [])
            if not direct_ip or not card.get('statement'):
                rejected.append({'card_id': card_id, 'reason': '没有可验证的本 IP 模块用途；不沿用跨模块模板引用'})
                continue
            used.append({'card_id': card_id, 'title': card.get('title', card_id),
                         'statement': card['statement'], 'source': response.relative_to(root).as_posix(),
                         'authority': card.get('authority'), 'confidence': card.get('confidence'),
                         'usage': call.get('usage') or '约束本报告只做有证据的 IP 初筛，不构成采购或法律放行。'})
    return used, rejected, sources


def render(model, retained, knowledge):
    light = cr.LIGHT_WORD[model['overall_light']]
    counts = model['counts']
    lines = ['# IP 风险排查与专利分析', '', START, '',
             f'**最高灯：{light}**｜红灯 {counts["red"]} · 橙灯 {counts["orange"]} · 黄灯 {counts["yellow"]}',
             '', '本报告是研究阶段初筛；采购/上架未放行。检索未完成、材料待核和未选 SKU 均不能写成已清除权利。',
             '', '## 当前合规依据', '']
    for item in model['items']:
        lines.append(f'- {cr.LIGHT_ICON[item["light"]]} {item["title"]}：' + '；'.join(item['evidence']) + f'。处理：{item["action"]}')
    if not model['items']:
        lines.append('已核输入未命中风险规则；缺失证据仍需补齐。')
    if model['pending_confirmations']:
        lines += ['', '待厂家确认：' + '、'.join(a['label'] for a in model['pending_confirmations']) + '。']
    lines += [f'- {r["title"]}：{r["action"]}' for r in model['reminders'] if r.get('light') == 'gray']
    lines += ['', '## 检索结果与待你查清单', '',
              '| 项目 | 状态 | 本轮已有记录 |', '|---|---|---|']
    for search in model['searches']:
        complete = search['status'] == 'searched' and not search.get('problem')
        note = (search.get('result') if complete else search.get('reason')) or '尚无可验证检索结果'
        lines.append(f'| {search.get("title", search["id"])} | {"已查（按记录覆盖）" if complete else "待你查"} | {note.replace("|", "／")} |')
    for search in model['searches']:
        card = search.get('self_check_card')
        if not card:
            continue
        lines += ['', f'### {card["title"]}', '', '入口：' + '；'.join(f'[打开]({url})' for url in card['links']),
                  '输入词：' + '；'.join(card['queries'])]
        lines += [f'{number}. {step}' for number, step in enumerate(card['steps'], 1)]
    if any(s.get('self_check_card') for s in model['searches']):
        lines += ['', '### 统一截图回传格式', '',
                  '每项检索提交一份下表字段，附查询/筛选、结果列表（含零结果）和相关详情截图。复核前保留“待你查”。',
                  '', '| 字段 | 填写内容 |', '|---|---|',
                  '| 检索编号 | uspto_trademark / design_patent / utility_patent / image_reverse / schedule_a |',
                  '| 日期、查询词、筛选条件 | 按实际操作填写 |',
                  '| 结果数量、记录号、链接 | 记录实际结果；打不开不填零 |',
                  '| 截图文件、采购 SKU | 每个文件注明对应 SKU 与检索编号 |',
                  '| 判断、覆盖限制 | 说明相似之处、差异与未覆盖范围 |']
    lines += ['', '## 本报告采用的知识库依据', '']
    if knowledge:
        lines += [f'- {card["card_id"]}《{card["title"]}》：{card["usage"]} 依据：{card["statement"]}' for card in knowledge]
    else:
        lines.append('未取得可核验且直接适用于本报告的知识卡响应，不沿用历史模板引用。')
    lines += ['', '## 来源与修订', '',
              '当前灯色来自同目录 04A_compliance_ruler.yaml；检索原始记录、历史报告、关键词及知识库响应按 SHA-256 保留在 '
              '04_ip_report_sync.json 来源清单中。历史版本仅供追溯，当前结论以上述重新计算结果为准。',
              '', END, '', '## 保留的研究与检索记录', '',
              '以下保留原研究中的检索过程、关键词和设计观察；不作为新的权利清除或材料确认。']
    for section in retained:
        lines += ['', '## ' + section['title'], '', compact_unknown_tables(section['body'])]
    return '\n'.join(lines).rstrip() + '\n'


def run(root):
    delivery_scripts = Path(__file__).resolve().parents[3] / 'scripts'
    if str(delivery_scripts) not in sys.path:
        sys.path.insert(0, str(delivery_scripts))
    from r01_native import execute_native
    compact = execute_native(root, lambda view: run(view), step=8,
                             report_paths=[(cr.OUT / REPORT).as_posix()])
    if compact is not NotImplemented:
        return compact
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('产品根不存在')
    cr.run(root)
    folder = root / cr.OUT
    report, manifest = folder / REPORT, folder / MANIFEST
    previous = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
    old = report.read_text(encoding='utf-8-sig') if report.is_file() else ''
    unchanged = report.is_file() and digest(report) == previous.get('report_sha256')
    archives = previous.get('archives') or []
    if old and not unchanged:
        history = folder / '_history'
        history.mkdir(exist_ok=True)
        archived = history / f'04_IP风险排查与专利分析_{digest(report)[:16]}.md'
        if not archived.exists():
            archived.write_bytes(report.read_bytes())
        rel = archived.relative_to(root).as_posix()
        if rel not in archives:
            archives.append(rel)
    parsed = previous.get('retained_sections', []) if unchanged else sections(old)
    retained, excluded = [], []
    for section in parsed:
        if re.search(r'知识库|结论|总览|风险灯|合规尺子|最高灯|来源与修订', section['title']):
            excluded.append({'title': section['title'], 'reason': '重新计算结论或重新核验引用；原文保留于历史来源'})
        else:
            retained.append(section)
    model = cr.load_yaml(folder / cr.MODEL)
    knowledge, removed, sources = verified_knowledge(root)
    all_old = '\n'.join(safe_path(root, path).read_text(encoding='utf-8-sig') for path in archives)
    old_ids = set(re.findall(r'\b(?:KC|PK)-\d+\b', all_old))
    used_ids = {card['card_id'] for card in knowledge}
    for card_id in sorted(old_ids - used_ids - {item['card_id'] for item in removed}):
        removed.append({'card_id': card_id, 'reason': '旧引用未获得当前本模块可核验知识库响应支持'})
    rendered = render(model, retained, knowledge)
    evidence_path = root / 'S1_选品板块/知识库依据/S1/08/kb_evidence.yaml'
    if evidence_path.is_file():
        evidence = cr.load_yaml(evidence_path)
        # Legacy standalone records without library identity retain their
        # conservative verified_knowledge rendering; they cannot acquire a
        # fabricated identity or usage from this writer.
        if all(row.get('server') and (row.get('usage') or not row.get('card_ids')) for row in evidence.get('calls', [])):
            from kb_report_contract import render_authored_report
            rendered = render_authored_report(rendered, root, Path(__file__).resolve().parents[3], 'S1', 8, 'ip_md', evidence=evidence)
    report.write_text(rendered, encoding='utf-8', newline='\n')
    sources += [folder / cr.MODEL, folder / cr.INPUTS, folder / '04_compliance_blacklist.yaml']
    sources += [safe_path(root, path) for path in archives]
    sources += list(root.glob('**/网页核对.json'))
    source_records = [{'path': path.relative_to(root).as_posix(), 'sha256': digest(path)}
                      for path in dict.fromkeys(sources) if path.is_file()]
    record = {'schema_version': '1.0', 'generator': Path(__file__).name,
              'report_sha256': digest(report), 'archives': archives, 'sources': source_records,
              'retained_sections': retained, 'excluded_sections': previous.get('excluded_sections', []) + excluded,
              'knowledge_used': knowledge, 'removed_citations': removed,
              'research_delivery_ready': not model['missing_searches'] and model.get('search_plan_complete', False),
              'procurement_cleared': False, 'publication_cleared': False,
              'pending_attributes': model['unknown_attributes'], 'searches_complete': model['searches_complete']}
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'ok': True, 'changed': old != rendered, 'report': str(report), 'manifest': str(manifest),
            'archives': len(archives), 'knowledge_used': sorted(used_ids),
            'procurement_cleared': False, 'searches_complete': model['searches_complete']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.run), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
