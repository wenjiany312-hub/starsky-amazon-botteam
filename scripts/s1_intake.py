"""S1 one-line intake and host-specific product container resolution."""
import argparse
import json
import re

MARGIN_NOTE = '广告前目标毛利只是参考值，不是能不能做的门槛：定高一点，前期利润更厚、压力更小、成功把握更大；实际能接受多少由你根据经验和资金决定，有经验的卖家用更低毛利也可以做。'
from pathlib import Path
from datetime import date

import yaml


def parse(reply):
    reply = str(reply).strip()
    if not reply: raise ValueError('需要真实用户回复；不能脚本代选')
    market = re.search(r'(?<![A-Za-z])(US|UK|DE|JP|AU|CA|FR|IT|ES)(?![A-Za-z])', reply, re.I)
    price = re.search(r'\$?(\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?)', reply)
    margin = re.search(r'(?:毛利|margin)\s*(\d+(?:\.\d+)?)\s*%', reply, re.I)
    budget = re.search(r'(?:首批预算|预算|budget)\s*([\d,]+(?:\.\d+)?)\s*(元|CNY|USD|美元|GBP|英镑)?', reply, re.I)
    # Only interpret the card's leading option sequence. Numbers in a product
    # description, budget or payback target must never become menu choices.
    options = re.match(r'^([1-4])\s+([1-4])(?:\s+([A-F](?:[\s,、]+[A-F])*))?(?:\s+([1-3]))?(?=\s|$)', reply, re.I)
    option_margin = {None:.40,'1':.30,'2':.40,'3':.50}[options.group(4) if options else None]
    rate = float(margin.group(1)) / 100 if margin else option_margin
    if not 0 < rate < 1: raise ValueError('广告前目标毛利必须在0%与100%之间')
    # Template letters are menu answers, not letters inside maintenance prose.
    explicit = re.fullmatch(r'(?:默认\s+)?([A-F](?:[\s,、]+[A-F])*)(?:[，,]\s*计划.*)?', reply, re.I)
    structured = re.match(r'^(?:US|UK|DE|JP|AU|CA|FR|IT|ES)\s+\$?\d+(?:\.\d+)?[-–]\d+(?:\.\d+)?\s+([A-F](?:[\s,、]+[A-F])*)(?=\s|$)', reply, re.I)
    selected = options.group(3) if options else explicit.group(1) if explicit else structured.group(1) if structured else ''
    templates = list(dict.fromkeys(re.findall(r'[A-F]', (selected or '').upper())))
    initial = None
    if budget:
        currency = {'元': 'CNY', '美元': 'USD', '英镑': 'GBP'}.get(budget.group(2), (budget.group(2) or 'CNY').upper())
        initial = {'value': float(budget.group(1).replace(',', '')), 'currency': currency, 'status': 'user_attested'}
    return {'schema_version': 1, 'user_reply': reply, 'marketplace': market.group(1).upper() if market else
            {'1':'US','2':'UK','3':'DE','4':'JP'}[options.group(1)] if options else 'US',
        'price_band': re.sub(r'\s', '', price.group(1)).replace('–', '-') if price else
            {'1':'8-15','2':'15-25','3':'25-40','4':'40+'}[options.group(2)] if options else '15-25',
        'templates': templates, 'target_margin_pre_ad': rate, 'initial_budget': initial,
        'field_sources': {'marketplace': 'user_attested' if market or options else 'default_assumption',
            'price_band': 'user_attested' if price or options else 'default_assumption',
            'target_margin_pre_ad': 'user_attested' if margin or (options and options.group(4)) else 'default_assumption'}}


def product_container(host, configured=None, workspace=None):
    if host not in ('codex', 'claude'): raise ValueError('unknown host')
    base = Path(configured).expanduser() if configured is not None else (
        Path(workspace) if workspace is not None else Path.home() / ('Starsky-Codex' if host == 'codex' else 'Starsky-Claude'))
    forbidden = 'starsky-claude' if host == 'codex' else 'starsky-codex'
    if any(part.lower() == forbidden for part in base.parts):
        raise ValueError('产品根配置属于另一个宿主；请用本宿主目录或用户明确指定的新目录')
    while base.name.lower() == 'product_runs' and base.parent.name.lower() == 'product_runs': base = base.parent
    return base if base.name.lower() == 'product_runs' else base / 'product_runs'


def run(root, reply):
    from r01_native import execute_native
    result = execute_native(root, lambda view: run(view, reply), step=1,
        report_paths=['00_总控/starsky-fullchain-workflow/00A_选品需求确认单.md'])
    if result is not NotImplemented: return result
    root = Path(root).resolve(); data = parse(reply)
    target = root / '00A_quick_select.yaml'
    if target.is_file():
        existing = yaml.safe_load(target.read_bytes()) or {}
        data = {**existing, **data}  # Preserve historical notes/keywords and unrelated fields.
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf8')
    md = root / '00_总控/starsky-fullchain-workflow/00A_选品需求确认单.md'; md.parent.mkdir(parents=True, exist_ok=True)
    body = ('# 选品条件\n\n站点：' + data['marketplace'] + '；价带：' + data['price_band'] +
        '；广告前目标毛利：' + str(round(data['target_margin_pre_ad'] * 100)) + '%（' +
        ('你指定' if (data.get('field_sources') or {}).get('target_margin_pre_ad') == 'user_attested' else '系统默认，可改') +
        '）\n\n> ' + MARGIN_NOTE + '\n\n首批预算：' +
        (str(data['initial_budget']['value']) + ' ' + data['initial_budget']['currency'] if data['initial_budget'] is not None else '未提供（可选）') +
        '\n\n📁 产品文件夹：' + str(root) + '\n\n用户原话：' + reply + '\n')
    from kb_report_contract import render_native_narrative
    body = render_native_narrative(body, root, Path(__file__).resolve().parents[1], 1, 'intake_md')
    md.write_text(body, encoding='utf8')
    return {'ok': True, 'intake_yaml': target.name, 'intake_md': md.relative_to(root).as_posix(), 'intake': data}


def remember_for_navigator(root):
    """S1-R9: a new product goes to the top of the navigator history, so the float can follow it at once."""
    path = Path.home() / '.starsky-codex' / 'nav_float_history.json'
    try:
        old = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        old = []
    old = [str(x) for x in old if isinstance(x, str) and x != str(root)] if isinstance(old, list) else []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([str(root)] + old[:19], ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass  # the navigator is optional; product creation never fails on it


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path); parser.add_argument('--reply')
    parser.add_argument('--create-product', action='store_true'); parser.add_argument('--host', choices=['codex','claude'])
    parser.add_argument('--workspace', type=Path); parser.add_argument('--base', type=Path)
    args = parser.parse_args(argv)
    if args.create_product:
        if not args.host: parser.error('--create-product requires explicit --host')
        base = product_container(args.host, configured=args.base, workspace=args.workspace).resolve()
        base.mkdir(parents=True,exist_ok=True)
        name = date.today().isoformat() + '_待定产品'; target = base / name; count = 1
        while target.exists():
            count += 1; target = base / (name + str(count))
        target.mkdir()
        remember_for_navigator(target)
        print(json.dumps({'root':str(target),'host':args.host},ensure_ascii=False)); return 0
    if args.root is None or not args.reply: parser.error('--root and --reply are required for intake')
    print(json.dumps(run(args.root, args.reply), ensure_ascii=False)); return 0


if __name__ == '__main__': raise SystemExit(main())
