"""第 9 步：用案例里已有的事实预填 s1_decision_inputs.yaml（R06.1 AS-01 第 6 批）。

3.0.22 的案例里助手为第 9 步现写了 20KB 脚本：手抄目标毛利、货源链接、页面价、件重尺、合规灯色进官方输入，
再另编一份“两方案综合报告”（AS-06）。官方合成器现在能直接呈现用户选的 A/B 条件方案（PL-07），
剩下的就是把能确定的事实预填好：

    python scripts/s1_decision_prefill.py --root "<产品根>"
    然后补判断类字段（需求证据与反证、厂家核查、风险是否排除、下一步）：写进临时 yaml 后
    python scripts/s1_decision_prefill.py --root "<产品根>" --fill <该文件>（不要直接改封存的输入文件），再运行
    python scripts/sync_s1_decision_reports.py --root "<产品根>" --compact-receipt <第9步回执>

只填空着的字段，已有内容一律不动；只写有来源的事实（页面提取、用户条件、合规尺子研究灯色），
“风险已排除”“可行”“实际利润”“组件用量”这类判断永远留给用户或助手，不由脚本代填。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

INPUTS = 's1_decision_inputs.yaml'
TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / INPUTS
FACTS = 'S1_选品板块/1688-product-detail-scraper/03_product_facts.yaml'
PROFILE = 'S1_选品板块/amazon-market-research/01_product_profile.yaml'
QUICK = ('00_总控/starsky-fullchain-workflow/00A_quick_select.yaml', '00A_quick_select.yaml')
COST_INPUTS = 'S1_选品板块/amazon-market-research/05_cost_inputs.yaml'
RULER = 'S1_选品板块/amazon-ip-risk-assessment/04A_compliance_ruler.yaml'
LIGHT = {'gray': 0, 'green': 1, 'yellow': 2, 'red': 3}
RISK_CARD = {'intellectual_property': ('商标', '外观', '发明', '图片'), 'compliance': ('合规',)}


def _load(path):
    path = Path(path)
    return (yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}) if path.is_file() else {}


def _empty(node):
    if node is None or node == '' or node == [] or node == {}:
        return True
    return isinstance(node, dict) and set(node) >= {'value'} and node.get('value') is None and node.get('status') in (None, 'missing')


def _fill(container, key, value, filled, label):
    if value is not None and _empty(container.get(key)):
        container[key] = value
        filled.append(label)


def _fill_document(view, doc):
    view = Path(view)
    filled, needs = [], []
    facts, profile = _load(view / FACTS), _load(view / PROFILE)
    style = (profile.get('selected_style') or {}).get('name')
    supplier = facts.get('selected_supplier') or {}
    if style and doc.get('product_name') in (None, '', '待确认产品'):
        doc['product_name'] = style
        filled.append('产品名')
    settings = doc.setdefault('user_settings', {})
    for relative in QUICK:
        quick = _load(view / relative)
        if quick.get('target_margin_pre_ad') is not None:
            status = (quick.get('field_sources') or {}).get('target_margin_pre_ad', 'default_assumption')
            _fill(settings, 'pre_ad_margin_target', {'value': quick['target_margin_pre_ad'], 'unit': 'ratio', 'status': status,
                                                     'source': relative + ' / target_margin_pre_ad'}, filled, '广告前目标毛利')
            break
    post = (_load(view / COST_INPUTS).get('target_post_ad_margin'))
    if isinstance(post, dict) and post.get('value') is not None:
        _fill(settings, 'post_ad_margin_target', {**post, 'unit': 'ratio'}, filled, '广告后目标毛利')
    if not doc.get('components') and supplier.get('offer_id'):
        factory, page, packaging = facts.get('factory') or {}, facts.get('page_data') or {}, facts.get('packaging') or {}
        url = supplier.get('offer_url') or f"https://detail.1688.com/offer/{supplier['offer_id']}.html"
        doc['components'] = [{
            'name': style or facts.get('product') or '主件',
            'quantity': {'value': None, 'status': 'missing', 'source': '每套用量待用户确认'},
            'offer_url': {'value': url, 'status': 'page_extracted', 'source': FACTS + ' / selected_supplier'},
            'supplier': supplier.get('name'), **({'sku': supplier['sku']} if supplier.get('sku') else {}),
            **{key: node for key, node in (('page_price', factory.get('unit_price_cny')), ('moq', page.get('moq')),
                                            ('page_weight', packaging.get('unit_package_weight_kg'))) if isinstance(node, dict)}}]
        filled.append('组件（货源链接、页面价、起订量、件重；用量待确认）')
    size = (facts.get('packaging') or {}).get('unit_package_cm')
    weight = (facts.get('packaging') or {}).get('unit_package_weight_kg')
    if isinstance(size, dict) and size.get('value') and not (doc.get('packaging') or {}).get('page_reference'):
        text = '×'.join(f'{v:g}' for v in size['value']) + 'cm' + (f"，{weight['value'] * 1000:g}g" if isinstance(weight, dict) and weight.get('value') else '')
        doc.setdefault('packaging', {})['page_reference'] = {'value': text + '（页面件重尺，不是已核最终包装）', 'status': 'page_extracted',
                                                             'source': size.get('source') or FACTS}
        filled.append('页面包装参考')
    ruler = _load(view / RULER)
    cards = ruler.get('summary_card') or []
    for risk in doc.setdefault('risks', []):
        words = RISK_CARD.get(risk.get('id'))
        rows = [c for c in cards if words and any(w in str(c.get('label') or c.get('item') or c.get('name') or '') for w in words)]
        if rows and not risk.get('research_light'):
            worst = max(rows, key=lambda c: LIGHT.get(c.get('light'), 0))
            risk['research_light'] = worst.get('light')
            risk['research_source'] = RULER + ' / summary_card'
            filled.append(f"{risk.get('name') or risk.get('id')}研究灯色（{worst.get('light')}）")
    for component in doc.get('components') or []:
        if _empty(component.get('quantity')):
            needs.append(f"组件「{component.get('name')}」每套用量")
    demand = doc.get('demand') or {}
    if not demand.get('evidence'):
        needs.append('需求证据')
    if not demand.get('counterevidence'):
        needs.append('需求反证')
    needs += [f"厂家核查：{c.get('name')}" for c in doc.get('supplier_checks') or [] if _empty(c.get('result'))]
    needs += [f"风险是否排除：{r.get('name')}" for r in doc.get('risks') or [] if _empty(r.get('cleared'))]
    if not doc.get('next_action') or doc.get('next_action') == _load(TEMPLATE).get('next_action'):
        needs.append('下一步')
    return doc, filled, needs


FILL_HINT = ('补字段：把 still_needed 里的判断写进一个临时 yaml（只写要补的键，结构同 s1_decision_inputs.yaml），'
             '运行 s1_decision_prefill.py --root "<产品根>" --fill <该文件>。不要直接改 s1_decision_inputs.yaml：'
             '它已封存，直接改会报 physical source hash mismatch。')


def _merge(base, extra):
    """Judgement fields from the assistant; dicts merge key by key, other values replace."""
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def prefill(root, fill=None):
    """fill: judgement fields to merge, submitted through the same sealed transaction (3.0.25 step 9:
    editing the sealed file directly broke the case store)."""
    root = Path(root).resolve()
    state = {}

    def write(view):
        view = Path(view)
        path = view / INPUTS
        doc = _load(path) or _load(TEMPLATE)
        if fill:
            doc = _merge(doc, fill)
        doc, filled, needs = _fill_document(view, doc)
        path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')
        state.update(filled=filled, still_needed=needs, inputs=INPUTS, merged=sorted(fill or {}))
        if needs:
            state['how_to_fill'] = FILL_HINT
        return dict(state)
    from case_mutation import mutate
    return mutate(root, 'S1', 9, write, operation='decision_prefill')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--fill', type=Path, help='要补的判断字段（yaml），合并进 s1_decision_inputs.yaml 并封存')
    options = parser.parse_args(argv)
    try:
        fill = yaml.safe_load(options.fill.read_text(encoding='utf-8-sig')) if options.fill else None
        if fill is not None and not isinstance(fill, dict):
            raise ValueError('--fill 文件必须是 yaml 映射（键值对）')
        result = prefill(options.root, fill)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
