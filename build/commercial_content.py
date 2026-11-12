"""Keep the author's business examples out of distributable plugin copies."""
import html
import json
import re
from pathlib import Path

TEXT_SUFFIXES = {'.md', '.yaml', '.yml', '.json', '.html', '.txt', '.py', '.csv', '.toml'}
BRAND = r'AkoSlale|YUTUOFIELD|VIPTUVI|AKO(?:_[A-Za-z0-9]+)+'
PRODUCT = (
    r'棉签棉垫收纳罐|中号圆柱竹盖棉签盒|宠物棉签(?:线)?|棉签(?:收纳)?[罐盒]?|棉球[罐盒]|化妆棉收纳盒|收纳罐|'
    r'门底(?:挡风条|密封条)|门缝(?:挡风条|密封条|防风条)|挡风条|'
    r'宠物(?:垃圾|废物)桶|狗便垃圾桶|宠物便便桶|狗屎桶|垃圾桶|'
    r'(?:宠物)?(?:狗狗?|犬用)(?:安抚)?耳[罩帽]|宠物狗耳帽|耳罩|耳帽|PETEARCOVER\d+(?:[-_]?[A-Z0-9]+)*|Happy\s+Hoodie|'
    r'桌下(?:隐形)?抽屉|隐形抽屉|抽屉类目|'
    r'(?:排插|插排|插线板|电源线|电源插座)收纳盒|'
    r'犬用(?:雨衣|棉服)|雨衣|宠物清洁|电动牙刷架|狗(?:110|131)|Raincoat|'
    r'(?:pet\s+)?cotton\s+(?:swabs?|buds?|pads?|rounds?|balls?)(?:\s+(?:holders?|dispensers?|jars?|boxes?))?|'
    r'under[ -]?desk\s+(?:invisible\s+)?drawers?|door[ -]?(?:draft|draught)\s+(?:stoppers?|strips?)|'
    r'(?:pet|dog)\s+(?:waste|trash)\s+(?:bins?|cans?)|dog\s+ear\s+(?:hoods?|caps?|muffs?)|'
    r'power\s+strip\s+(?:storage|organizer)\s+boxes?'
)
PERSONAL = re.compile('(?:' + BRAND + '|' + PRODUCT + ')', re.I)

# Public trademark names and generic replacements are executable compliance rules,
# not owner product examples. Preserve the Mac-supplied dictionary byte for byte.
PUBLIC_RULE_FILES = {'skills/starsky-ads-terminal/engine/app/services/copy_guard.py'}

# Reviewed customer documentation and redacted screenshots are supplied by Mac.
# Preserve only these exact delivery paths, including original text bytes.
PUBLIC_GUIDE_FILES = {'skills/starsky-ads-terminal/references/客户SOP.md'}
PUBLIC_GUIDE_PREFIX = 'skills/starsky-ads-terminal/engine/app/ui/guide/'


def preserve_public_content(relative):
    return (relative in PUBLIC_RULE_FILES or relative in PUBLIC_GUIDE_FILES
            or relative.startswith(PUBLIC_GUIDE_PREFIX))


def neutralize(text):
    text = re.sub(BRAND, '示例品牌', text, flags=re.I)
    text = text.replace('产品推广知识库（宠物棉签线/收纳罐线）', '产品推广知识库（通用产品方法）')
    text = text.replace('dog q tip / dog q-tips / dog ear qtips…', '核心词的不同拼写和语序变体')
    text = text.replace('qtip and cotton round holder', '竞品商标关联词').replace('qtip holder', '竞品商标词')
    text = text.replace('F4 swab 变体落地', '通用词变体承接')
    text = text.replace('2026-07-28 宠物用品实测', '历史方法验证（通用化案例）')
    text = re.sub(r'狗110', 'SKU-A', text)
    text = re.sub(r'狗131', 'SKU-B', text)
    text = re.sub(PRODUCT, '示例产品', text, flags=re.I)
    return text


def generic_report(text):
    styles = ''.join(re.findall(r'<style[^>]*>(.*?)</style>', text, flags=re.S | re.I))
    headings = re.findall(r'<h2[^>]*>(.*?)</h2>', text, flags=re.S | re.I)
    headings = [html.escape(re.sub(r'<[^>]+>', '', neutralize(h)).strip()) for h in headings]
    sections = ''.join('<section><h2>' + h + '</h2><p>示例内容：按当前用户产品的真实资料、数据来源与验收结果填写。</p></section>' for h in headings)
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>S1 报告格式示意</title><style>' + styles + '</style><body><main><h1>S1 选品可行性报告 · 格式示意</h1><p>本页只展示报告格式，不包含作者产品、真实 ASIN、采购链接或客户经营数据，不可作为业务证据。</p>' + sections + '</main></body></html>\n'


def sanitize_commercial_tree(home):
    home = Path(home).resolve()
    changes = []
    for path in sorted(home.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if preserve_public_content(path.relative_to(home).as_posix()):
            continue
        text = path.read_text(encoding='utf-8-sig')
        changed = neutralize(text)
        relative = path.relative_to(home).as_posix()
        if relative == 'skills/amazon-visual-benchmark-learner/SKILL.md':
            changed = re.sub(r'^按标题关键词双重过滤：.*$', '按标题关键词双重过滤：**品类词** ∩ **形态/材质词**。两组词都来自当前用户确认的产品事实，不使用内置品类词。', changed, flags=re.M)
        if relative == 'skills/amazon-ip-risk-assessment/references/compliance_ruler.yaml':
            changed = changed.replace('generic: 示例产品', 'generic: 按当前商品填写非商标通用名称')
        if relative in ('skills/_brand_visual_contract/references/brand_story_playbook.md', 'skills/_brand_visual_contract/references/brand_store_playbook.md'):
            changed = re.sub(r'^来源：.*$', '来源：通用化项目经验与公开模块规格；案例经验不覆盖平台当前提示，品牌与商品以当前用户资料为准。', changed, flags=re.M)
        if path.relative_to(home).as_posix().endswith('/sources/manifest.yaml'):
            data = json.loads(changed)
            for record in data.get('sources', []):
                filename = record.get('filename')
                if filename and not filename.startswith(('https://', 'http://')):
                    record['filename'] = '来源-' + str(record.get('source_id', '资料')) + str(record.get('ext') or Path(filename).suffix)
            changed = json.dumps(data, ensure_ascii=False, indent=1) + '\n'
        # 插件是 mcp/promotion-kb/…，Bot Team 包是 mcp/local/promotion-kb/…，两种布局同一份规则
        if path.relative_to(home).as_posix().endswith('promotion-kb/kb/kb_manifest.yaml'):
            data = json.loads(changed)
            data['products'] = []
            data['products_note'] = '本发行库提供通用推广知识，不预置产品档案；产品资料与方向由当前用户提供。'
            data.pop('products_moved_home', None)
            changed = json.dumps(data, ensure_ascii=False, indent=1) + '\n'
        if path.relative_to(home).as_posix() == 'templates/gold_reports/S1_选品可行性_v2统一版.html':
            changed = generic_report(text)
        if changed == text:
            continue
        # Quotes containing private details become labelled summaries, not invented quotations.
        if path.suffix.lower() == '.md' and text.startswith('---'):
            pieces = changed.split('---', 2)
            before = text.split('---', 2)[1]
            old_quotes = re.findall(r'^\s+quote:.*$', before, flags=re.M)
            for quote in old_quotes:
                if PERSONAL.search(quote):
                    revised = neutralize(quote)
                    pieces[1] = pieces[1].replace(revised, revised.replace('quote: "', 'quote: "[通用化摘要，非原文] ', 1), 1)
            changed = '---'.join(pieces)
        if path.suffix.lower() == '.py':
            compile(changed, str(path), 'exec')
        if path.suffix.lower() == '.json' or (path.suffix.lower() == '.yaml' and changed.lstrip().startswith(('{', '['))):
            json.loads(changed)
        path.write_text(changed, encoding='utf8', newline='\n')
        changes.append(path.relative_to(home).as_posix())
    audit = audit_commercial_tree(home)
    if audit['personal_hits']:
        raise ValueError('Personal content remains in distribution: ' + ', '.join(h['file'] for h in audit['personal_hits']))
    return {'changed_files': changes, **audit}


def audit_commercial_tree(home):
    home = Path(home)
    hits = []
    checked = 0
    for path in home.rglob('*'):
        if not path.is_file():
            continue
        if preserve_public_content(path.relative_to(home).as_posix()):
            continue
        if PERSONAL.search(path.relative_to(home).as_posix()):
            hits.append({'file': path.relative_to(home).as_posix(), 'reason': 'personal_filename'})
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        checked += 1
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            if PERSONAL.search(line):
                hits.append({'file': path.relative_to(home).as_posix(), 'line': number})
    return {'checked_text_files': checked, 'personal_hits': hits}
