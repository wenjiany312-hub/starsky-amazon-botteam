#!/usr/bin/env python3
"""第 8 步 IP 与合规 · HTML 阅读版（R09）：从 04A_compliance_ruler.yaml 与禁用词表生成，统一报告样式（左侧电梯导航）。

只做呈现，不改判断：灯色、结论、待确认项全部来自合规尺子模型；原 Markdown 报告保持不变。
3.0.25 狗84：用户要求“整份报告优化排版”，助手现写脚本做了一份阅读版；本模块把它变成第 8 步的固定产物。
"""
from __future__ import annotations

import base64
import html
import importlib.util
import re
import sys
from pathlib import Path

import yaml

OUT = Path('S1_选品板块') / 'amazon-ip-risk-assessment'
HTML_NAME = '04A_IP与合规阅读版.html'
ICON = {'red': '🔴', 'orange': '🟠', 'yellow': '🟡', 'green': '🟢', 'gray': '⚪'}
WORD = {'red': '硬门槛', 'orange': '要资质', 'yellow': '要规避', 'green': '未命中', 'gray': '待核'}
TONE = {'red': 'danger', 'orange': 'warn', 'yellow': 'warn', 'green': 'ok', 'gray': None}
GROUPS = [('overview', '先看结论'), ('fix', '整改与待确认'), ('terms', '文案与品牌词'), ('evidence', '检索与依据')]
STATUS = {'searched': '✅ 已查', 'user_todo': '📝 待你查', 'not_searched': '❌ 未查'}


def _renderer():
    path = Path(__file__).resolve().parents[2] / 'github-tabler-html-report/assets/salon_report.py'
    key = '_starsky_ip_salon_report'
    if key not in sys.modules:
        previous = list(sys.path)
        try:
            sys.path.insert(0, str(path.parent))
            # Source checkout: s1_product_front still lives in release-overrides until the build copies it.
            overrides = next((parent / 'release-overrides' for parent in path.parents if (parent / 'release-overrides/s1_product_front.py').is_file()), None)
            if overrides: sys.path.insert(1, str(overrides))
            spec = importlib.util.spec_from_file_location(key, path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally:
            sys.path[:] = previous
        sys.modules[key] = module
    module = sys.modules[key]
    module.STAGE_GROUPS.setdefault('IP', GROUPS)
    return module


def _esc(value):
    return html.escape(str(value if value is not None else '—'), quote=False)


def _links(urls):
    urls = [u for u in (urls if isinstance(urls, list) else [urls]) if isinstance(u, str) and u.startswith('http')]
    return ' '.join(f'<a href="{html.escape(u, quote=True)}" target="_blank" rel="noopener">来源{i}</a>' for i, u in enumerate(urls[:3], 1)) or '—'


def _short(text, limit=90):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    first = re.split(r'(?<=[。；])', text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit] + '…'


def _product_image(root):
    media = Path(root) / 'S1_选品板块/1688-product-detail-scraper'
    for picture in sorted(media.rglob('main_01.*')):
        mime = {'.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.webp': 'webp'}.get(picture.suffix.lower())
        if mime and picture.stat().st_size <= 3 * 1024 * 1024:
            return 'data:image/' + mime + ';base64,' + base64.b64encode(picture.read_bytes()).decode()
    return None


def _terms(blacklist):
    groups = []
    for key, label in (('forbidden_terms', '禁用词（不能出现在标题、五点、ST、图片文字）'), ('sensitive_terms', '敏感词（要有依据才能用）'),
                       ('brand_terms', '他牌词（不能当自己的卖点）'), ('brand_terms_ads_only', '只能用于广告定向的品牌词')):
        values = []
        for item in blacklist.get(key) or []:
            values.append(item.get('term') or item.get('word') or item.get('text') if isinstance(item, dict) else item)
        values = [str(v) for v in values if v]
        if values: groups.append((label, values))
    return groups


def build_html(model, blacklist=None, image=None):
    salon = _renderer()
    blacklist = blacklist or {}
    light = model.get('overall_light') or 'gray'
    product = model.get('product') or '目标产品'
    searches = model.get('searches') or []
    done = sum(1 for s in searches if s.get('status') == 'searched')
    cards = model.get('summary_card') or []
    pending = model.get('pending_confirmations') or []
    report = salon.Report(f'{product} · IP 与合规阅读版', f"站点 {model.get('marketplace') or 'US'} · 规则库 {model.get('rules_as_of') or '—'} · 初筛，不是法律意见；做不做由你拍板",
                          meta=[], stage='IP')

    s = report.section('decision', '当前结论', group='overview')
    s.kpis([('最高风险灯', ICON.get(light, '') + ' ' + WORD.get(light, light), '整体', TONE.get(light)),
            ('五项核查', f"{sum(1 for c in cards if c.get('light') in ('red', 'orange', 'yellow'))} 项需处理", '商标 / 外观 / 发明 / 图片 / 声明', None),
            ('必做检索', f'{done} / {len(searches)}', '已查 / 应查', None if done == len(searches) else 'warn'),
            ('待厂家确认', f'{len(pending)} 项', '属性依据', 'warn' if pending else 'ok')])
    if image:
        s.raw(f'<p><img src="{image}" alt="产品主图" style="max-width:260px;border-radius:10px;border:1px solid var(--line)"></p>')
    for card in cards:
        tone = TONE.get(card.get('light')) or 'info'
        s.raw(f'<div class="callout {tone}"><b>{ICON.get(card.get("light"), "")} {_esc(card.get("label"))}</b>　{_esc(_short(card.get("conclusion"), 120))}'
              f'<br><span style="font-size:13px">需要你做：{_esc(card.get("needs_user") or "—")}</span></div>')

    s = report.section('overview5', '五项风险速览', group='overview')
    s.table(['项', '灯', '结论', '需要你做', '来源'],
            [[_esc(c.get('label')), ICON.get(c.get('light'), '') + ' ' + WORD.get(c.get('light'), ''), _esc(c.get('conclusion')),
              _esc(c.get('needs_user') or '—'), _links(c.get('source'))] for c in cards])

    s = report.section('fix', '整改要求', group='fix')
    items = model.get('items') or []
    if items:
        s.table(['灯', '范围', '项目', '需要什么', '怎么处理'],
                [[ICON.get(i.get('light'), '') + ' ' + WORD.get(i.get('light'), ''), _esc(i.get('scope')), _esc(i.get('title')),
                  _esc(i.get('requires')), _esc(i.get('action'))] for i in items])
        evidence = ''.join(f'<li><b>{_esc(i.get("title"))}</b>：{_esc("；".join(i.get("evidence") or []))}</li>' for i in items)
        s.details('命中依据', f'<ul style="font-size:13px">{evidence}</ul>')
    else:
        s.p('规则库未命中出口禁限、资质或高侵权品类。')

    s = report.section('pending', '待确认清单', group='fix')
    rows = [[_esc(c.get('label')), _esc(c.get('needs_user'))] for c in cards if c.get('light') in ('red', 'orange', 'yellow', 'gray')]
    rows += [[_esc(p.get('label')), _esc(_short(p.get('evidence') or '请提供选定 SKU 的 BOM、结构照片或检测依据。'))] for p in pending]
    s.table(['事项', '怎么补'], rows) if rows else s.p('没有待确认事项。')

    s = report.section('terms', '禁用词与品牌词', group='terms')
    groups = _terms(blacklist)
    traps = model.get('trademark_traps') or []
    if traps:
        s.table(['看着像通用词的商标', '权利人', '改用'], [[_esc(t.get('term')), _esc(t.get('owner')), _esc(t.get('generic'))] for t in traps])
    for label, values in groups:
        s.h3(label)
        s.raw('<p>' + ' '.join(f'<span class="pill">{_esc(v)}</span>' for v in values[:60]) + ('<span class="pill">…</span>' if len(values) > 60 else '') + '</p>')
    if not groups and not traps:
        s.p('本轮没有生成禁用词或品牌词。')

    s = report.section('searches', '必做检索', group='evidence')
    s.table(['检索', '状态', '结果 / 原因', '入口'],
            [[_esc(x.get('title') or x.get('id')), STATUS.get(x.get('status'), _esc(x.get('status'))),
              _esc(_short(x.get('result') if x.get('status') == 'searched' else x.get('reason'), 160)), _links(x.get('links'))] for x in searches])

    s = report.section('attributes', '产品属性判定', group='evidence')
    word = {True: '是', False: '否', None: '⚪ 待核'}
    grouped = {}
    for a in (model.get('attributes') or {}).values():
        grouped.setdefault((a.get('value'), _short(a.get('evidence') or '—', 120)), []).append(a.get('label'))
    s.table(['属性', '判定', '依据'], [['、'.join(_esc(x) for x in labels), word.get(value, '⚪ 待核'), _esc(evidence)] for (value, evidence), labels in grouped.items()])
    s.p('<span style="color:var(--muted);font-size:13px">完整依据、原始检索词与截图回传格式见同目录 04A_合规尺子.md。</span>')
    return report.render()


def write_reading_html(root, model):
    root = Path(root)
    path = root / OUT / '04_compliance_blacklist.yaml'
    try:
        blacklist = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {} if path.is_file() else {}
    except (OSError, yaml.YAMLError):
        blacklist = {}
    target = root / OUT / HTML_NAME
    target.write_text(build_html(model, blacklist, _product_image(root)), encoding='utf-8', newline='\n')
    return target
