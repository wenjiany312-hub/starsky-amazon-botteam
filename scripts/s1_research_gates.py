"""S1 M2 demand, direction scoring and positioning gates with source-bound facts."""
from copy import deepcopy
import json
import math
from pathlib import Path

import yaml

AXES = ('competition', 'differentiation', 'margin_barrier', 'market_capacity', 'trend')
TOOLS = {'product_reviews', 'product_trend', 'category_report', 'keyword_search_results', 'keyword_trend'}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def select(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'): raise ValueError('source requires JSON Pointer')
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        document = document[int(token)] if isinstance(document, list) else document[token]
    return document


def sourced(cell, root, cache=None):
    if not isinstance(cell, dict) or 'value' not in cell or not cell.get('time_window'):
        raise ValueError('fact requires value, source and time_window；事实字段为空或结构不全，请从已归档原包补 value/source/time_window，缺数保留 null，不编造值')
    source = cell.get('source') or {}
    if not isinstance(source, dict) or not isinstance(source.get('path'), str) or not source['path'].strip():
        raise ValueError('fact requires a raw response source；source.path 为空或非文本，请填归档回包相对产品根的路径')
    path = (Path(root) / source['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise ValueError('source escapes product root')
    cache = {} if cache is None else cache
    if path not in cache: cache[path] = yaml.safe_load(path.read_bytes())
    raw = cache[path]
    observed = select(raw, source.get('selector'))
    if observed != cell['value'] or type(observed) is not type(cell['value']):
        # JSON integers and real numbers represent the same number.
        if not (number(observed) and number(cell['value']) and observed == cell['value']):
            raise ValueError('source mismatch: ' + str(source))
    return observed


def review_identity(cell, row, root, cache):
    """Identify a raw review location, rather than accepting producer-made IDs."""
    source = cell['source']; path = (Path(root) / source['path']).resolve()
    current = cache[path]; location = []; parent = None
    for encoded in source['selector'][1:].split('/'):
        token = encoded.replace('~1', '/').replace('~0', '~')
        parent = current
        if isinstance(current, list):
            index = int(token)
            # Negative indices are Python conveniences, not JSON Pointer indices.
            if index < 0: raise ValueError('评论source不接受负索引')
            token = str(index); current = current[index]
        else: current = current[token]
        location.append(token)
    if isinstance(parent, dict):
        for keys, expected in [(('asin', 'ASIN'), row['asin']), (('id', 'review_id', 'reviewId'), row['id'])]:
            actual = next((parent[key] for key in keys if key in parent), None)
            if actual is not None and str(actual) != str(expected):
                raise ValueError('评论ASIN/ID与原始回包身份不一致')
    return str(path).casefold(), tuple(location)


def demand_shape_errors(doc):
    """Reject malformed containers before accessing fields; null still means missing."""
    if not isinstance(doc, dict):
        return ['需求文件必须是 YAML 对象，不能是空文件、列表或文本；按 --demand-template 生成新草稿后填写真实字段']
    errors = []
    for key in ('validated_positioning', 'core_keyword', 'collection', 'category_comparison_plan'):
        if doc.get(key) is not None and not isinstance(doc[key], dict):
            errors.append(key + ' 必须是对象，不能用空字符串或列表代替')
    collection = doc.get('collection')
    if isinstance(collection, dict):
        for key in ('requests', 'attempts'):
            rows = collection.get(key)
            if rows is not None and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
                errors.append(f'collection.{key} 必须是工具记录对象列表，空行请删除；attempts 只能登记真实调用')
    position = doc.get('validated_positioning')
    if isinstance(position, dict) and position.get('core_keywords') is not None and not isinstance(position['core_keywords'], list):
        errors.append('validated_positioning.core_keywords 必须是关键词列表')
    for key in ('same_form_top10', 'top3_trends', 'reviews'):
        rows = doc.get(key)
        if rows is None: continue
        if not isinstance(rows, list):
            errors.append(key + ' 必须是列表'); continue
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f'{key}[{i}] 必须是对象，空行请删除或补齐'); continue
            if key == 'top3_trends' and row.get('months') is not None:
                if not isinstance(row['months'], list) or any(not isinstance(month, dict) for month in row['months']):
                    errors.append(f'{key}[{i}].months 必须是月份对象列表')
    return errors


def evaluate_demand(doc, root):
    shape_errors = demand_shape_errors(doc)
    if shape_errors:
        return {'status': 'fail', 'errors': shape_errors, 'blocking_errors': shape_errors, 'shortages': [],
                'metrics': {'monthly_searches': None, 'same_form_top10': 0, 'top3_complete_trends': 0, 'reviews': 0}}
    blocking = []; shortages = []; positioning = doc.get('validated_positioning') or {}; cache = {}
    def fail(message): blocking.append(message)
    def fact(cell, label, minimum=0):
        if cell is None: return None
        try:
            value = sourced(cell, root, cache)
            if not number(value) or value < minimum: raise ValueError('invalid numeric value')
            return value
        except (ValueError, KeyError, OSError, IndexError, TypeError, yaml.YAMLError) as error:
            fail(label + ': ' + str(error)); return None
    if not all(positioning.get(key) for key in ('form', 'audience', 'core_keywords', 'target_size')):
        fail('缺验证定位 form/audience/core_keywords/target_size')
    keyword = doc.get('core_keyword') or {}
    if keyword.get('form') != positioning.get('form') or keyword.get('audience') != positioning.get('audience'):
        fail('核心词不是已验证形态与人群')
    if keyword.get('term') not in (positioning.get('core_keywords') or []): fail('核心词不属于验证定位')
    searches = fact(keyword.get('monthly_searches'), '核心形态词月搜索量')
    if searches is None or searches < 1: shortages.append('核心形态词月搜索量不足')
    top = doc.get('same_form_top10') or []; seen = set(); valid_top = []
    for row in top:
        asin = row.get('asin')
        if not asin or asin in seen: fail('Top10 ASIN缺失或重复'); continue
        seen.add(asin)
        if row.get('form') != positioning.get('form') or row.get('audience') != positioning.get('audience'):
            fail('Top10存在不同形态/人群：' + str(asin)); continue
        values = [fact(row.get(key), f'{asin} {key}', 0.01 if key == 'price' else 0)
                  for key in ('price', 'monthly_sales', 'review_count')]
        if all(value is not None for value in values): valid_top.append(asin)
    if len(valid_top) < 10: shortages.append(f'同形态Top10价/销/评不足：{len(valid_top)}/10')
    trends = {row.get('asin'): row.get('months') or [] for row in doc.get('top3_trends') or []}
    complete_trends = 0
    for asin in valid_top[:3]:
        months = trends.get(asin, [])
        unique = {row.get('month') for row in months if row.get('month')}
        values = [fact(row.get('sales'), f'{asin} {row.get("month")} trend') for row in months]
        if len(unique) >= 12 and len(months) == len(unique) and all(value is not None for value in values): complete_trends += 1
    if complete_trends < 3: shortages.append(f'Top3各12个月趋势不足：{complete_trends}/3')
    reviews = {}; declared_ids = {}
    for row in doc.get('reviews') or []:
        try:
            text = sourced(row.get('text'), root, cache)
            if row.get('asin') not in valid_top or not row.get('id') or not isinstance(text, str) or not text.strip():
                raise ValueError('评论缺同形态ASIN/ID/实际文本')
            identity = review_identity(row['text'], row, root, cache)
            declared = (row['asin'], str(row['id']))
            if identity in reviews and reviews[identity] != declared:
                raise ValueError('同一原始评论source不能改ASIN/ID重复计数')
            if declared in declared_ids and declared_ids[declared] != identity:
                raise ValueError('同一评论ASIN/ID不能换source重复计数')
            reviews[identity] = declared; declared_ids[declared] = identity
        except (ValueError, KeyError, OSError, IndexError, TypeError, yaml.YAMLError) as error: fail('评论：' + str(error))
    if len(reviews) < 100: shortages.append(f'真实可拉取评论不足：{len(reviews)}/100')
    errors = blocking + shortages
    return {'status': 'fail' if errors else 'pass', 'errors': errors,
        'blocking_errors': blocking, 'shortages': shortages,
        'metrics': {'monthly_searches': searches, 'same_form_top10': len(valid_top),
                    'top3_complete_trends': complete_trends, 'reviews': len(reviews)}}


def _continue_demand(document, root, call_tool, normalize=None):
    """Actually invoke the tool adapter before offering the low-demand choice.

    normalize is the host's source-preserving response mapper. A missing mapper
    leaves raw results for the host to read; it never turns a successful ping
    into fabricated evidence or an automatic pass.
    """
    doc = deepcopy(document)
    gate = evaluate_demand(doc, root)
    if gate['status'] == 'pass': return {'document': doc, 'gate': gate, 'status': 'pass', 'choices': []}
    collection = doc.setdefault('collection', {}); attempts = collection.setdefault('attempts', [])
    requests = collection.get('requests') or []
    if not requests: raise ValueError('缺真实续拉请求；先按MCP当前schema为核心词、Top3趋势及评论生成工具参数')
    for request in requests:
        tool = request.get('tool'); args = request.get('arguments')
        if tool not in TOOLS or not isinstance(args, dict): raise ValueError('unsupported demand tool or missing arguments')
        attempt = {'server': 'Sorftime', 'tool': tool, 'arguments': deepcopy(args)}
        try:
            raw = call_tool(tool, args); attempt['response'] = raw
            if isinstance(raw, dict) and raw.get('isError'):
                raise RuntimeError(json.dumps(raw, ensure_ascii=False))
            attempt['ok'] = True
            if normalize is not None: doc = normalize(doc, request, raw)
        except Exception as error:
            attempt.update(ok=False, error=str(error))
        attempts.append(attempt)
    doc['collection'] = collection
    gate = evaluate_demand(doc, root)
    pending_normalization = normalize is None and any(row.get('ok') for row in attempts)
    return {'document': doc, 'gate': gate, 'status': 'needs_normalization' if pending_normalization else gate['status'],
            'choices': [] if gate['status'] == 'pass' or pending_normalization else
                       ['换方向'] if gate['blocking_errors'] else ['换方向', '接受低需求继续']}


def continue_demand(document, root, call_tool, normalize=None):
    """New-user category collection also runs when original demand already passes."""
    result = _continue_demand(document, root, call_tool, normalize)
    from s1_category_comparison import required, collect
    if required(root):
        categories = collect(result['document'], root, call_tool)
        result['document']['category_comparison'] = categories
        result['category_comparison'] = categories
        errors = categories['validation_errors']
        if errors:
            result['gate']['errors'].extend(errors)
            result['gate']['blocking_errors'].extend(errors)
            result['gate']['status'] = 'fail'
            result.update(status='fail', choices=['换方向'])
    return result


def evaluate_scores(candidates):
    errors = []
    for row in candidates:
        scores = row.get('scores') or {}
        for axis in AXES:
            cell = scores.get(axis) or {}
            if cell.get('score') is None:
                if not str(cell.get('missing_reason') or '').strip(): errors.append(f'{row.get("name")} {axis}: null缺原因')
                continue
            score = cell['score']; evidence = cell.get('evidence') or {}
            if not number(score) or not 1 <= score <= 5 or not number(evidence.get('value')) or not evidence.get('source') or not evidence.get('time_window'):
                errors.append(f'{row.get("name")} {axis}: 分数须1–5且有数字、来源和时间窗')
    if not candidates: errors.append('缺方向评分表')
    return {'status': 'fail' if errors else 'pass', 'errors': errors}


def compare_positioning(old, new):
    changes = []
    before, after = old.get('target_size'), new.get('target_size')
    if isinstance(before, list) and isinstance(after, list):
        if any(number(a) and number(b) and a > 0 and abs(b - a) / a > .30 + 1e-12 for a,b in zip(before,after)):
            changes.append('尺寸：' + '×'.join(map(str,before)) + 'cm → ' + '×'.join(map(str,after)) + 'cm（变化>30%）')
        if len(before) != len(after):
            changes.append('尺寸维数变化：' + str(before) + ' → ' + str(after) + '，需核对同一测量口径')
    for key, label in [('audience','人群'), ('form','形态'), ('category','品类')]:
        if old.get(key) and new.get(key) and old[key] != new[key]: changes.append(label + '：' + str(old[key]) + ' → ' + str(new[key]))
    if old.get('core_keywords') and new.get('core_keywords') and set(old['core_keywords']).isdisjoint(new['core_keywords']):
        changes.append('核心词：' + ', '.join(old['core_keywords']) + ' → ' + ', '.join(new['core_keywords']))
    return {'requires_confirmation': bool(changes), 'changes': changes,
            'choices': ['沿用原定位（换货源或定制）', '按新定位重新过需求门'] if changes else []}


def artifact_document(root, receipt, role):
    value = receipt.get('artifacts', {}).get(role)
    if value is None: raise ValueError('缺本步结构化产物：' + role)
    item = value if isinstance(value, dict) else {'path': value}
    if not isinstance(item.get('path'), str) or not item['path'].strip():
        raise ValueError(f'artifacts.{role}.path 为空或非文本；填写相对产品根的业务文件路径，再用同一条 record 重交')
    path = (Path(root) / item['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise ValueError('artifact escapes product root；核对 --root 与产物相对路径，勿修改流程状态')
    doc = yaml.safe_load(path.read_bytes())
    return select(doc, item['selector']) if item.get('selector') else doc


def user_choice(receipt, choices=None):
    decision = receipt.get('decision') or {}
    message = str(decision.get('message') or '').strip()
    if (decision.get('source') != 'user' or message.lower() in ('', '继续', '下一步', '好', 'ok', '确认') or
            receipt.get('user_reply') != message):
        raise ValueError('需要绑定真实用户原话user_reply的具体选择，不得脚本代填source:user')
    if choices is not None and decision.get('action') not in choices:
        raise ValueError('用户选择必须为：' + ' / '.join(choices))
    return decision


def check_m2_step(root, step_number, receipt, execution):
    """M2 only. The caller keeps legacy gates on their original nine steps."""
    details = {}; errors = []
    try:
        if step_number == 2:
            doc = artifact_document(root, receipt, 'selection_yaml')
            candidates = doc.get('candidates') or doc.get('directions') or []
            details['scores'] = evaluate_scores(candidates); errors.extend(details['scores']['errors'])
            if not 3 <= len(candidates) <= 5: errors.append('方向筛选必须3–5个候选')
            from s1_selection import validate_selection
            validate_selection(root,receipt)
            decision = user_choice(receipt)
            if not decision.get('selection'): errors.append('需要用户选1个方向')
        if step_number == 3:
            doc = artifact_document(root, receipt, 'demand_yaml')
            details['demand'] = evaluate_demand(doc, root)
            if demand_shape_errors(doc):
                return {'status': 'fail', 'errors': details['demand']['errors'], 'checks': details}
            details['validated_positioning'] = doc.get('validated_positioning')
            from s1_category_comparison import required, category_coverage
            if required(root, execution) or required(root):
                details['category_comparison'] = category_coverage(root)
                errors.extend(details['category_comparison']['validation_errors'])
            if details['demand']['status'] != 'pass':
                errors.extend(details['demand']['blocking_errors'])
                attempts = (doc.get('collection') or {}).get('attempts') or []
                metrics = details['demand']['metrics']; needed = set()
                if metrics['reviews'] < 100: needed.add('product_reviews')
                if metrics['top3_complete_trends'] < 3: needed.add('product_trend')
                if metrics['same_form_top10'] < 10 or metrics['monthly_searches'] is None or metrics['monthly_searches'] < 1: needed.add('keyword_search_results')
                actual = {row.get('tool') for row in attempts if isinstance(row.get('arguments'), dict) and
                          row['arguments'] and
                          (row.get('response') is not None or (row.get('ok') is False and row.get('error')))}
                if not needed <= actual: errors.append('需求缺数必须先实际自动续拉：' + ', '.join(sorted(needed - actual)) +
                                                     '。运行 <python> scripts/s1_research_gates.py --root "<产品根>" --continue-demand '
                                                     '"S1_选品板块/amazon-market-research/02_demand_validation.yaml" 获取宿主 MCP 调用清单；'
                                                     '由宿主调用 → capture_mcp.py 原样归档 → 规范化新需求草稿 → --check-demand，再用同一条 record 重交。'
                                                     '续拉指引不代表已调用；不得直接覆盖封存证据或修改 workflow_state.yaml')
                if not errors:
                    user_choice(receipt, {'accept_low_demand'})
                    if receipt.get('outcome') != 'partial': errors.append('接受低需求只能partial，不能假称需求通过')
                    else: details['demand_exception'] = receipt['decision']
        if step_number == 4:
            decision = user_choice(receipt)
            if not decision.get('selection'): errors.append('需要用户确认具体SKU/款型')
            quick = artifact_document(root, receipt, 'ip_quick_yaml')
            if quick.get('status') == 'blocked' or quick.get('light') == 'red': errors.append('IP快筛明显撞车，先换款或放弃')
            if quick.get('status') != 'pass' or not quick.get('searches'): errors.append('缺品牌商标/外观专利真实快筛')
            from s1_acceptance_contract import early_coverage_errors
            state = yaml.safe_load((Path(root) / 'workflow_state.yaml').read_bytes()) if (Path(root) / 'workflow_state.yaml').is_file() else {}
            mode = (state or {}).get('run_mode') if (state or {}).get('run_mode') in ('full_delivery', 'salon_live') else 'full_delivery'
            coverage = early_coverage_errors(root, mode)
            details['early_coverage'] = 'skipped_no_facts' if coverage is None else ('pass' if not coverage else 'fail')
            errors.extend(coverage or [])
        if step_number == 5:
            decision = user_choice(receipt)
            if not decision.get('selection'): errors.append('需要用户选货源SKU')
            facts = artifact_document(root, receipt, 'facts')
            old = execution.get('validated_positioning') or {}
            new = facts.get('positioning') or {}
            if not old or not new: errors.append('缺需求验证定位或所选货源实际定位，不能静默换品类')
            else:
                compared = compare_positioning(old, new); details['positioning'] = compared
                if compared['requires_confirmation']:
                    errors.extend(compared['changes'])
                    errors.append(' / '.join(compared['choices']))
            page = facts.get('page_data') or {}; factory = facts.get('factory') or {}; packaging = facts.get('packaging') or {}
            def val(value): return value.get('value') if isinstance(value, dict) else value
            price = val(page.get('price', factory.get('unit_price_cny')))
            weight = val(page.get('weight_kg', packaging.get('unit_package_weight_kg', factory.get('unit_package_weight_kg'))))
            if not number(price) or price <= 0: errors.append('页面和现有资料完全没有价格，先补关键字段')
            if not number(weight) or weight <= 0: errors.append('页面和图片完全没有重量，先补关键字段')
            details['supplier_status'] = 'page_data_pending_confirmation'
        if step_number == 8:
            ruler = artifact_document(root, receipt, 'ruler_yaml')
            if ruler.get('final_status') == 'red' or ruler.get('overall_light') == 'red':
                user_choice(receipt, {'accept_risk'})
                if receipt.get('outcome') != 'partial': errors.append('接受IP风险只允许partial，红灯不可改成绿灯')
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as error:
        errors.append(str(error))
    return {'status': 'fail' if errors else 'pass', 'errors': errors, 'checks': details}


def bridge_adapter(script=None):
    """Use the same installed transport and host registry as every other MCP call."""
    import subprocess
    import sys
    bridge = Path(script) if script is not None else Path(__file__).with_name('starsky_mcp.py')
    def call(tool, arguments):
        completed = subprocess.run([sys.executable,'-B',str(bridge),'--server','sorftime','--timeout','120',
            'call',tool,json.dumps(arguments,ensure_ascii=False)],capture_output=True,text=True,encoding='utf8',timeout=130)
        try: document=json.loads(completed.stdout)
        except ValueError: raise RuntimeError('MCP bridge returned invalid JSON: ' + completed.stdout + completed.stderr)
        if completed.returncode != 0 or document.get('ok') is not True:
            raise RuntimeError(json.dumps(document,ensure_ascii=False))
        return document['result']
    return call


def demand_report(gate, research_notes=''):
    """Render the verified source-bound gate plus attributed research notes."""
    from s1_readability import reader_projection
    metrics=gate['metrics']
    rows=['# 需求证据一页纸','','结论：'+('最低证据已齐全。' if gate['status']=='pass' else '完整需求门仍未通过，保留原研究结论。'),
          '本报告仅使用留存输入复算；未补采、未改变用户的选款或采购授权。','',
          '| 项目 | 原始输入复核结果 |','|---|---:|',
          '| 核心词月搜索量 | '+str(metrics['monthly_searches'])+' |',
          '| 同形态价/销/评齐全样本 | '+str(metrics['same_form_top10'])+'/10 |',
          '| 前三各有完整历史的样本 | '+str(metrics['top3_complete_trends'])+'/3 |',
          '| 可核实评论 | '+str(metrics['reviews'])+' |','','## 待补证据','']
    rows+=['- '+error for error in gate['errors']]
    if research_notes:rows+=['','## 沿用的原研究分析','',research_notes]
    return reader_projection('\n'.join(rows))


def demand_path(root, value):
    root = Path(root).expanduser().resolve()
    if not root.is_dir(): raise ValueError(f'产品根不存在：{root}；--root 请填写已有产品目录的绝对路径')
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('需求文件路径为空；填写相对 --root 的业务文件路径')
    path = (root / Path(value).expanduser()).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('demand input escapes product root；需求文件必须在 --root 指定的产品目录内')
    return root, path.relative_to(root).as_posix()


def read_demand(root, relative, callback):
    """Read/check a complete verified view without committing or refreshing reports."""
    from r01_native import enabled, store_module, _display_result
    if enabled(root):
        import tempfile
        store = store_module()
        # materialize() calls restore_legacy(), which can rewrite old receipts.
        # Use its verified index and filtered files in a disposable read-only copy.
        index = store.CompactCase(root)._index()
        files = store._files(root)
        files.update({path: entry[1] for path, entry in index.items()})
        with tempfile.TemporaryDirectory(prefix='starsky-demand-check-') as temporary:
            view = Path(temporary)
            for name, raw in files.items():
                target = view / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            return _display_result(callback(yaml.safe_load((view / relative).read_bytes()), view), view, root, None)
    return callback(yaml.safe_load((root / relative).read_bytes()), root)


def continuation_recovery(error, root):
    text = str(error).lower()
    steps = []
    if any(word in text for word in ('hash', 'sha256', '封存', 'proof')):
        steps.append('哈希校验失败：保留错误中的文件和旧回执，用可信备份核对原始字节；不要重算旧哈希、改旧回执或跳过校验。'
                     '确认原件后仅通过 r01_native.py execute 的 inputs 提交新版业务内容。')
    if any(word in text for word in ('root', '产品根', 'path', 'no such file', '目录', 'cwd', 'session', '会话', '对话')):
        steps.append(f'核对 --root={Path(root).expanduser().resolve()} 与本次产品一致；相对路径以产品根解析，不能以 shell cwd 猜测。'
                     '自动归档若找不到本对话，请在该产品目录开启/切回正确宿主会话后调用 MCP；不要读取其他产品会话。')
    steps.append('只修新的业务草稿，保留封存证据和 workflow_state.yaml；先 --check-demand，修好后用同一条正式 record 重交。')
    return steps


def host_continuation(doc, evidence_root, root, relative):
    """Plan host MCP work. No calls, attempts, evidence writes or state changes."""
    import os
    import shlex
    import subprocess
    import sys
    gate = evaluate_demand(doc, evidence_root)
    doc = doc if isinstance(doc, dict) else {}
    commands = {}
    def command(name, script, *args):
        argv = [sys.executable, '-B', str(Path(__file__).with_name(script)), *map(str, args)]
        commands[name] = {'argv': argv, 'command': subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)}
    command('capture', 'capture_mcp.py', '--root', root, '--stage', 'S1', '--step', '3')
    command('check', 's1_research_gates.py', '--root', root, '--check-demand', relative)
    command('continue', 's1_research_gates.py', '--root', root, '--continue-demand', relative)
    command('native_execute', 'r01_native.py', 'execute', '--root', root, '--step', '3')
    collection = doc.get('collection') or {}
    requests = collection.get('requests') if isinstance(collection, dict) else None
    request_errors = []; calls = []
    if requests is not None and not isinstance(requests, list):
        request_errors.append('collection.requests 必须是工具请求对象列表'); requests = []
    if gate['status'] != 'pass' and not requests:
        requests = []
        keyword = doc.get('core_keyword') or {}
        term = keyword.get('term') if isinstance(keyword, dict) else None
        if isinstance(term, str) and term.strip():
            if not gate['metrics']['monthly_searches']:
                requests.append({'tool': 'keyword_trend', 'arguments': {'keyword': term, 'keyword_support_site': 'US', 'type': 2}})
            if gate['metrics']['same_form_top10'] < 10:
                requests.append({'tool': 'keyword_search_results', 'arguments': {'keyword': term, 'keyword_support_site': 'US', 'page': 1}})
        else:
            request_errors.append('先填写 core_keyword.term 和已确认定位，再按宿主当前 schema 生成需求请求')
        top = doc.get('same_form_top10') or []
        asins = [r.get('asin') for r in top if isinstance(r, dict) and r.get('asin')] if isinstance(top, list) else []
        for asin in list(dict.fromkeys(asins))[:3]:
            if gate['metrics']['top3_complete_trends'] < 3:
                requests.append({'tool': 'product_trend', 'arguments': {'asin': asin, 'amz_site': 'US', 'type': 'SalesVolume'}})
            if gate['metrics']['reviews'] < 100:
                requests.append({'tool': 'product_reviews', 'arguments': {'asin': asin, 'amz_site': 'US', 'review_type': 'Both'}})
        if not asins: request_errors.append('先从搜索结果选出同形态同人群 Top10，再为前三名补趋势与真实评论；当前不能编造 ASIN')
    for i, request in enumerate(requests or []):
        if (not isinstance(request, dict) or not isinstance(request.get('tool'), str) or request['tool'] not in TOOLS or
                not isinstance(request.get('arguments'), dict) or not request['arguments']):
            request_errors.append(f'collection.requests[{i}] 缺允许的 tool 或非空 arguments；按宿主当前 schema 补齐'); continue
        calls.append({'tool': 'mcp__sorftime__' + request['tool'], 'arguments': deepcopy(request['arguments'])})
    from s1_category_comparison import required, category_coverage
    if required(evidence_root):
        coverage = category_coverage(evidence_root)
        if coverage['validation_errors']:
            gate['errors'].extend(coverage['validation_errors'])
            gate['blocking_errors'].extend(coverage['validation_errors']); gate['status'] = 'fail'
            request_errors.append('相邻类目证据仍须补齐：按 category_comparison_plan 的站点与 parent_node_id 在宿主调用 category_tree，'
                                  '再对真实相邻节点调用 category_report；保持相同日期窗，通过既有类目规范化流程登记 comparison_scope.yaml。')
    return {'status': 'pass' if gate['status'] == 'pass' else 'needs_host_collection', 'gate': gate, 'choices': [],
            'transport': 'host_mcp', 'root': str(root), 'cwd': str(Path.cwd()), 'demand': relative,
            'requests': calls if gate['status'] != 'pass' else [], 'request_errors': request_errors,
            'recovery': continuation_recovery(' '.join(gate['errors'] + request_errors), root) if gate['status'] != 'pass' else [],
            'normalization_command': {'script': 'scripts/s1_research_gates.py',
                                      'args': ['--root', '{root}', '--check-demand', relative]},
            'commands': commands, 'next_steps': [
                '在当前宿主直接调用 requests 中的 MCP 工具，参数按宿主实时 schema 核对；自动建议默认美国站，其他站点须改成产品已确认站点。搜索结果不足可分页，不能把首屏当完整 Top10。',
                '宿主返回后运行 commands.capture 原样归档；读取实际 data_saved 路径和 calls 的调用键。回包缺失/截断须重调，不拼写模拟回包。若产品根与会话 cwd 不匹配，切回该产品会话再调用与归档。',
                '将回包规范化到新的需求草稿：每个事实包含 value、source.path、source.selector（如 /calls/<实际调用键>/response/...）、time_window；数值和时间口径必须与原包一致，缺数保留 null 与原因。',
                'collection.attempts 仅登记实际宿主调用的 tool、arguments、response 或真实错误；成功回包尚未完成规范化时，不得据此接受低需求。',
                '紧凑案或已封存文件：构造 JSON 请求，inputs 的键为 demand 路径、值为 {yaml: 新草稿}，commands 为 [normalization_command]；'
                '把该 JSON 从标准输入交给 commands.native_execute（例如命令后加 < 新提交请求.json）。原始回包同样作为新版 inputs 提交；不要直接覆盖封存文件。',
                '执行 commands.check 复核；通过后仍须用原来的正式 record 重交。若仍不足，保留门禁失败原因，只在实际续拉完成且仅数量不足时由用户明确选择 partial；本命令不登记步骤完成。']}


def run_continuation(root, relative, call_tool=None, normalize=None):
    """Default CLI is a read-only host handoff; explicit adapters keep native retries."""
    root, relative = demand_path(root, relative)
    if call_tool is None:
        return read_demand(root, relative, lambda doc, view: host_continuation(doc, view, root, relative))
    from r01_native import execute_native
    path = root / relative
    result = execute_native(root, lambda view: run_continuation(view, relative, call_tool, normalize), step=3,
                            report_paths=['S1_选品板块/amazon-market-research/01_市场分析报告.md'])
    if result is not NotImplemented: return result
    doc = yaml.safe_load(path.read_bytes())
    result = continue_demand(doc, root, call_tool, normalize)
    path.write_text(yaml.safe_dump(result['document'], allow_unicode=True, sort_keys=False), encoding='utf8')
    # R09: no user-facing log page. Every attempt (success or raw error) stays in a machine
    # record under 运行数据; the gate result is shown in plain words in the market report (6.2).
    market = root / 'S1_选品板块/amazon-market-research'
    record = market / '运行数据' / '需求续拉记录.json'; record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({'status': result['status'], 'gate': result['gate'], 'choices': result['choices'],
                                  'attempts': result['document'].get('collection', {}).get('attempts', []),
                                  'category_comparison': result.get('category_comparison')},
                                 ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf8')
    report = market / '01_市场分析报告.md'
    try:
        from s1_market_facts import write_market_facts
        from s1_step_refresh import write_market_reports
        report = write_market_reports(root, write_market_facts(root), 3)['md']
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Partial cases (no profile yet) still keep the record; the report is regenerated at step 3/4.
        result['report_error'] = str(error)
    return {'status': result['status'], 'gate': result['gate'], 'choices': result['choices'],
            'md': Path(report).relative_to(root).as_posix() if Path(report).is_file() else None,
            'record': record.relative_to(root).as_posix(), **({'report_error': result['report_error']} if result.get('report_error') else {})}


DEMAND_TEMPLATE = """# 第 3 步需求验证：S1_选品板块/amazon-market-research/02_demand_validation.yaml（R06.1 原生模板）
# 每个数值/文本事实都写成 {value, source: {path, selector}, time_window}：
#   path     = 已存回包的相对路径（capture_mcp.py 存在 S1_选品板块/amazon-market-research/运行数据/mcp_calls__<工具>.json）
#   selector = 回包里该值的 JSON 指针，例如 /calls/<调用键>/response/data/0/price；value 必须与回包里的值完全相同
#   time_window = 数据时间窗，例如 近30天 / 2026-09
# 写完先运行 s1_research_gates.py --root "产品根" --check-demand <本文件> 看缺什么，再 record。
validated_positioning: {form: null, audience: null, core_keywords: [], target_size: null, category: null}
core_keyword:
  term: null            # 必须在 core_keywords 里
  form: null            # 与 validated_positioning.form 相同
  audience: null        # 与 validated_positioning.audience 相同
  monthly_searches: {value: null, source: {path: null, selector: null}, time_window: null}
same_form_top10:        # 10 个同形态、同人群 ASIN
- asin: null
  form: null
  audience: null
  price: {value: null, source: {path: null, selector: null}, time_window: null}
  monthly_sales: {value: null, source: {path: null, selector: null}, time_window: null}
  review_count: {value: null, source: {path: null, selector: null}, time_window: null}
top3_trends:            # Top10 前 3 个 ASIN，各 12 个不同月份
- asin: null
  months:
  - month: null         # YYYY-MM
    sales: {value: null, source: {path: null, selector: null}, time_window: null}
reviews:                # 至少 100 条真实评论，asin 必须是 Top10 之一
- asin: null
  id: null
  text: {value: null, source: {path: null, selector: null}, time_window: null}
"""


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--continue-demand', help='输出宿主 MCP 续拉、原样归档、规范化与正式重交指引（只读，不自行直连）')
    mode.add_argument('--demand-template', type=Path, help='第 3 步：写出 02_demand_validation.yaml 的填写模板（含字段说明）')
    mode.add_argument('--check-demand', type=Path, help='第 3 步：record 前先核对需求文件，列出缺项')
    args = parser.parse_args(argv)
    try:
        root, relative = demand_path(args.root, args.demand_template or args.check_demand or args.continue_demand)
        if args.demand_template:
            from r01_native import store_module
            if store_module().CompactCase(root).exists(relative):
                raise ValueError('模板目标已存在或已封存；请换新草稿文件名，不能覆盖原证据')
            with (root / relative).open('x', encoding='utf-8') as output:
                output.write(DEMAND_TEMPLATE)
            print(json.dumps({'ok': True, 'template': str(root / relative)}, ensure_ascii=False)); return 0
        if args.check_demand:
            result = read_demand(root, relative, evaluate_demand)
            if result['status'] != 'pass': result['recovery'] = continuation_recovery(' '.join(result['errors']), root)
        else:
            result = run_continuation(root, relative)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['status'] == 'pass' else 2
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as error:
        print(json.dumps({'ok': False, 'status': 'blocked', 'error': str(error),
                          'root': str(args.root.expanduser().resolve()), 'cwd': str(Path.cwd()),
                          'recovery': continuation_recovery(error, args.root)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__': raise SystemExit(main())
