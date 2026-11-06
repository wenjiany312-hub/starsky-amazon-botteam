"""Sorftime 类目宽筛：把筛选条件和返回值逐行对照（R10）。

2026-11-03 实测（US，价格 25–40、Top3 份额≤25、月销≥1000 打底）：加 ratings_count_max=500、改成 50、不加，
返回的 20 行完全相同、顺序也相同；而价格、Top3 份额、亚马逊自营占比等条件都被遵守。也就是说评论数上限在这个接口上
没有可观察的效果（接口没用它，或它对应的不是返回里的 average_review_count）。这不是插件传错参数，
但意味着：**服务端筛选参数不能当作“已通过筛选”的证据**，必须拿返回值自己复核。

本模块只做复核：每个筛选参数对应一个返回字段，逐行比较，给出 honored / violated / unmapped。
参数与返回字段的对应关系是我们的口径假设（Sorftime 文档只写 “ratings count”，返回里最接近的是
average_review_count——top100 平均评论数），报告里一律带着“口径假设”说出来，不冒充官方定义。
"""
from __future__ import annotations

import json
import math

# 筛选参数前缀 -> (返回字段, 说明)
FIELDS = {
    'ratings_count': ('average_review_count', 'top100 平均评论数'),
    'ratings': ('average_star_rating', 'top100 平均星级'),
    'price': ('average_price', 'top100 平均售价'),
    'month_sales_volume': ('top100_monthly_sales_volume', 'top100 月销量合计'),
    'top3_product_sales_share': ('top3_product_sales_share', 'Top3 产品销量占比（%）'),
    'amazon_owned_sales_share': ('amazon_owned_sales_share', '亚马逊自营销量占比（%）'),
    'newproduct_sales_share': ('new_product_3m_sales_share', '3 个月内新品销量占比（%）'),
    'top100_top400_sales_share': (None, 'Top100/Top400 销量比：返回里没有对应字段，无法本地复核'),
}
TOOLS = {'search_categories_broadly', 'category_search_from_top_node', 'category_search_from_product_name'}
ASSUMED = {'ratings_count'}  # 对应关系是口径假设，不是文档明确写的


def _number(value):
    try:
        number = float(str(value).strip().rstrip('%'))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _rows(response):
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return []
    data = response.get('data') if isinstance(response, dict) else None
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def audit(args, response):
    """-> [{param, field, bound, rows, violated, status, note}]；没带任何可核对筛选参数时返回空列表。"""
    rows = _rows(response)
    out = []
    for key, bound in sorted((args or {}).items()):
        if key.endswith('_min') or key.endswith('_max'):
            base, kind = key[:-4], key[-3:]
        else:
            continue
        if base not in FIELDS or bound in (None, -9999):
            continue
        field, label = FIELDS[base]
        entry = {'param': key, 'bound': bound, 'field': field, 'label': label, 'rows': len(rows)}
        if field is None:
            out.append({**entry, 'status': 'unmapped', 'violated': None, 'note': label})
            continue
        if not rows:
            out.append({**entry, 'status': 'no_rows', 'violated': 0, 'note': '没有返回行，无法复核'})
            continue
        values = [_number(r.get(field)) for r in rows]
        known = [v for v in values if v is not None and not (field == 'average_price' and v == -1)]  # -1 = 该期无数据
        limit = _number(bound)
        if limit is None:
            out.append({**entry, 'status': 'unknown', 'violated': None, 'note': '筛选边界不是有限数字，无法复核'})
            continue
        bad = [v for v in known if (v < limit if kind == 'min' else v > limit)]
        missing = len(rows) - len(known)
        note = f'返回 {len(rows)} 行，{len(bad)} 行不满足 {key}={bound}（对照字段 {field}），{missing} 行缺少可核对的数值'
        if base in ASSUMED:
            note += '；参数与该字段的对应是口径假设，文档只写 ratings count'
        out.append({**entry, 'status': 'violated' if bad else ('unknown' if missing else 'honored'),
                    'checked': len(known), 'missing': missing, 'violated': len(bad), 'note': note})
    return out


def notices(tool, args, response):
    """给 capture 用：只返回有问题的条目（人话）。"""
    if tool not in TOOLS:
        return []
    return [row['note'] + '。这些行不能当作已通过该筛选；请核对字段口径并按可用返回值复核，不能据此断言服务端忽略参数。'
            for row in audit(args, response) if row['status'] in ('violated', 'unknown', 'unmapped')]
