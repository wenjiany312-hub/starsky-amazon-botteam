"""Stage-scoped delivery and business checks; reuse the existing specialist validators."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml


def result(errors, **extra):
    return {'status': 'fail' if errors else 'pass', 'errors': errors, **extra}


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8-sig')) or {}


def load_record_receipt(root, record):
    """Resolve immutable small metadata and verify its bound raw version."""
    ref = record.get('receipt_ref')
    if not ref:
        value = record.get('receipt') or record
        return load(safe_file(root, value)) if isinstance(value, str) else value
    path = safe_file(root, ref['path'])
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    def selected(pointer):
        if not isinstance(pointer, str) or not pointer.startswith('/'):
            raise ValueError('回执引用缺有效JSONPointer')
        value = document
        for key in pointer[1:].split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    def hashed(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')).hexdigest()
    value = selected(ref['selector'])
    if not isinstance(value, dict) or hashed(value) != ref['sha256']:
        raise ValueError('已封存的回执元数据发生变化')
    if isinstance(record.get('receipt'), dict) and record['receipt'] != value:
        raise ValueError('状态与已封存回执元数据不一致')
    raw = record.get('raw_ref') or {}
    if raw:
        if raw.get('path') != ref['path']:
            raise ValueError('原始数据引用不属于同一回执')
        if raw.get('status') == 'superseded':
            raise ValueError('原始数据已被后续final替换：superseded，不可恢复旧raw')
        if raw.get('kind') == 'step_final':
            snapshot = document.get('business_snapshot') or {}
            if any(snapshot.get(key) != raw.get(key) for key in ('revision', 'case_sequence')):
                raise ValueError('原始数据已被后续final替换：superseded，不可恢复旧raw')
        if hashed(selected(raw['selector'])) != raw.get('sha256'):
            from r01_case_store import verify_migrated_reference
            if not verify_migrated_reference(document, raw['selector'], raw.get('sha256'), root):
                raise ValueError('回执原始数据版本发生变化')
    return value


def safe_file(root, path):
    item=path if isinstance(path,dict) else {}
    path=item.get('path') if item else path
    if not isinstance(path,(str,Path)):
        raise ValueError('产物缺少有效路径')
    root = Path(root).resolve(); path = (root / path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
        raise ValueError('缺失/空文件或越界产物：' + str(path))
    if item.get('selector') is not None:
        from r01_persistence import selected_bytes
        selected_bytes(path,item['selector'])
    return path


def module(path):
    path = Path(path)
    sys.path.insert(0, str(path.parent))
    name = 'starsky_checked_' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)
    return mod


def run(home, root, relative, args):
    cmd = [sys.executable, '-B', str(Path(home) / relative), *[str(x) for x in args]]
    process = subprocess.run(cmd, cwd=root, capture_output=True, encoding='utf8', errors='replace',
                             timeout=60, env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    return result([] if process.returncode == 0 else [relative + ': ' + (process.stdout + process.stderr)[-4000:]],
                  exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)


def all_artifacts(root, stage, receipt):
    state = load(Path(root) / 'workflow_state.yaml')
    flow = state.get(stage.lower() + '_execution', {})
    artifacts = {role: item for record in flow.get('records', []) for role, item in record['artifacts'].items()}
    artifacts.update(receipt.get('artifacts', {}))
    return {role: safe_file(root, path) for role, path in artifacts.items()}


def check_html(home, root, stage, artifacts):
    """A missing stage report fails; other stages are neither required nor counted."""
    errors = []; measured = {}
    contract = load(Path(home) / 'templates/html_report_contract.yaml')['reports'][stage]
    measure = module(Path(home) / 'scripts/检查HTML达标.py').measure
    items = contract['items']
    if stage == 'S2':
        keyword_files = [v for k, v in artifacts.items() if k.startswith('keyword_html')]
        report_files = [(p, next(i for i in items if i['id'] == 's2_keywords')) for p in keyword_files]
        if not keyword_files:
            errors.append('缺少本阶段词库 HTML；未生成不能按跳过处理')
        report_files.append((artifacts.get('report_html') or artifacts.get('listing_html'), next(i for i in items if i['id'] == 's2_listing')))
    else:
        report_files = [(artifacts.get('report_html'), items[0])]
    for value, spec in report_files:
        if not value:
            errors.append('缺少本阶段必交 HTML：' + spec['id']); continue
        try:
            path = safe_file(root, value)
            data = measure(str(path)); measured[str(path)] = data
            if not data.get('charset_ok'): errors.append(path.name + '：charset 前 1024 字节内缺少 UTF-8 声明')
            if not data.get('style_ok'): errors.append(path.name + '：未通过既有 Tabler 样式检查')
            for key, floor in ({} if stage in {'S3','S4'} and data.get('field_gate') else spec.get('minimum', {})).items():
                if isinstance(floor, (int, float)) and data.get(key, 0) < floor:
                    errors.append(f'{path.name}：{key} {data.get(key, 0)} < {floor}')
            for key, ceiling in spec.get('maximum', {}).items():
                if isinstance(ceiling, (int, float)) and data.get(key, 0) > ceiling:
                    errors.append(f'{path.name}：{key} {data.get(key, 0)} > {ceiling}')
        except (ValueError, OSError) as error:
            errors.append(str(error))
    return result(errors, measured=measured)


def manifest_rows(data):
    if isinstance(data, list): return data
    if not isinstance(data, dict): return []
    rows = []
    for key in ('items', 'assets', 'images', 'listing_images', 'aplus_modules', 'shots', 'videos'):
        value = data.get(key)
        if isinstance(value, list): rows += value
        elif isinstance(value, dict): rows += [dict(v, asset_id=k) for k, v in value.items() if isinstance(v, dict)]
    return rows


def check_gpt_web_feeds(root):
    errors = []
    listing = Path(root) / 'S3_视觉板块' / 'GPT网页端投喂_主副图'
    aplus = Path(root) / 'S3_视觉板块' / 'GPT网页端投喂_A+'
    for folder, label in ((listing, 'GPT网页端投喂_主副图'), (aplus, 'GPT网页端投喂_A+')):
        if not folder.is_dir():
            errors.append('缺投喂文件夹 ' + label + '：出图文档和垫图必须打在同一个扁平目录里，禁止让用户自己去翻')
    if listing.is_dir():
        mds = [p for p in listing.glob('*.md') if p.name != '00_把本文件夹全部上传.md']
        imgs = [p for p in listing.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}]
        if not any(p.name.startswith('08_') for p in mds):
            errors.append('主副图投喂包缺少 08_主副图出图文档.md')
        if any('08A_' in p.name or '剧本' in p.name for p in mds):
            errors.append('主副图投喂包不要夹 08A 或套图剧本，只留一份 08_')
        if len(imgs) < 7:
            errors.append('主副图投喂包垫图不足 7 张，用户还得自己去 1688 媒体里找')
        listing_prompt = next((p for p in mds if p.name.startswith('08_')), None)
        if listing_prompt and 'HARD COUNT' not in listing_prompt.read_text(encoding='utf-8', errors='ignore'):
            errors.append('08_ 出图文档缺少 HARD COUNT（网页端会停在 6 张）')
    if aplus.is_dir():
        mds = [p for p in aplus.glob('*.md') if p.name != '00_把本文件夹全部上传.md']
        imgs = [p for p in aplus.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}]
        has_premium = any(p.name.startswith('09_') and '标准' not in p.name for p in mds)
        has_standard = any(p.name.startswith('09S_') or '标准A+' in p.name for p in mds)
        if not mds:
            errors.append('A+ 投喂包缺少出图提示词 MD')
        if has_premium and has_standard:
            errors.append('A+ 投喂包同时有 09_ 和 09S_，网页端会投错标准 A+')
        if not imgs:
            errors.append('A+ 投喂包缺少垫图')
        aplus_prompt = next((p for p in mds if p.name.startswith('09')), None)
        if aplus_prompt and 'HARD COUNT' not in aplus_prompt.read_text(encoding='utf-8', errors='ignore'):
            errors.append('A+ 出图文档缺少 HARD COUNT')
    return result(errors)


def check_aplus(root, manifest, require_generated, expected_modules=None, aplus_type=None):
    data = load(manifest)
    standard = (aplus_type or data.get('aplus_type')) == 'standard'
    modules=expected_modules if expected_modules is not None else 5
    checked = check_media(root, manifest, require_generated, minimum=modules if standard else modules*2)
    if not standard:
        slots = {(str(r.get('module_id','')), str(r.get('platform','')).lower()) for r in manifest_rows(data)}
        if len(slots) != modules*2 or sum(p == 'desktop' for _,p in slots) != modules or sum(p == 'mobile' for _,p in slots) != modules or {m for m,p in slots if p=='desktop'} != {m for m,p in slots if p=='mobile'}:
            checked['errors'].append(f'Premium A+ 必须是已确认 {modules} 模块 × Desktop/Mobile 两端，不能只交单端')
    checked['status'] = 'fail' if checked['errors'] else 'pass'
    if checked['errors']: checked.update(package_ready=False, final_accepted=False)
    return checked


def check_media(root, manifest, require_generated, minimum=1, require_review=True):
    errors = []; data = load(manifest); rows = manifest_rows(data)
    if len(rows) < minimum:
        errors.append(f'媒体清单只有 {len(rows)} 项，要求至少 {minimum} 项')
    generated = 0; reviewed = 0
    for row in rows:
        if not isinstance(row, dict): errors.append('媒体条目格式错误'); continue
        slot = row.get('asset_id') or row.get('id') or row.get('slot') or '未编号'
        status = row.get('status')
        if require_generated:
            if status not in {'generated', 'reviewed', 'accepted', 'approved'}:
                errors.append(str(slot) + ' 只有方案/ready，不能当作已生成媒体'); continue
            value = row.get('output_path') or row.get('path') or row.get('file') or row.get('local_path')
            try:
                if not value: raise ValueError('缺成品路径：' + str(slot))
                path = safe_file(root, value)
                if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov'}:
                    raise ValueError('媒体文件类型不符：' + str(slot))
                if path.suffix.lower() not in {'.mp4', '.mov'}:
                    from PIL import Image
                    with Image.open(path) as im: im.verify()
                elif b'ftyp' not in path.read_bytes()[:32]:
                    raise ValueError('不是可识别的视频容器：' + str(slot))
                if row.get('sha256') and row['sha256'].removeprefix('sha256:') != hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError('媒体已变化：' + str(slot))
                generated += 1
                is_reviewed = row.get('reviewed') is True or row.get('review_status') in {'approved', 'accepted', 'passed'} or status in {'reviewed', 'accepted', 'approved'}
                if require_review and not is_reviewed: errors.append(str(slot) + ' 缺实际成品审阅结果')
                if is_reviewed: reviewed += 1
            except (ValueError, OSError) as error: errors.append(str(error))
        elif not (row.get('prompt') or row.get('prompt_path') or row.get('prompt_id')):
            errors.append(str(slot) + ' 缺实际 Prompt 绑定')
    return result(errors, item_count=len(rows), generated=generated, reviewed=reviewed,
                  package_ready=bool(rows) and not errors, final_accepted=require_generated and not errors)


def check_snapshot(root, path, allow_gap):
    data = load(path); snap = data.get('snapshot', data); errors = []
    gap = snap.get('data_status') == 'data_gap' or snap.get('status') == 'data_gap'
    source = snap.get('source')
    if gap and allow_gap:
        if data.get('findings') or data.get('diagnosis') or snap.get('status') == 'normal' or not snap.get('gap_reason'):
            errors.append('缺数据时不得输出正常/异常诊断')
        return result(errors, data_status='data_gap', diagnosis_ready=False)
    if source not in {'lingxing_openapi', 'amazon_report', 'manual_snapshot'}:
        errors.append('巡检缺实际数据来源；不得将空数据当正常')
    if not snap.get('snapshot_date') or not snap.get('marketplace') or not snap.get('store_id'):
        errors.append('巡检缺时间、站点或店铺范围')
    refs = snap.get('evidence_refs') or data.get('evidence_refs') or snap.get('sources') or []
    if not refs: errors.append('巡检缺来源证据文件')
    for ref in refs:
        try:
            if not isinstance(ref, dict): raise ValueError('来源必须有路径和 SHA256')
            p = safe_file(root, ref['path'])
            if hashlib.sha256(p.read_bytes()).hexdigest() != str(ref.get('sha256', '')).removeprefix('sha256:'):
                errors.append('巡检来源 SHA256 不匹配')
        except (ValueError, OSError, KeyError) as error: errors.append(str(error))
    return result(errors, data_status='available' if not errors else 'unverified', diagnosis_ready=not errors)


def check_cost_model(root, md_path, model_path):
    """S1.7: the cost account must be the script's output for the current inputs, not a hand-written table."""
    errors = []
    model = load(model_path)
    if model.get('generator') != 's1_cost_model.py':
        return result(['成本账不是 s1_cost_model.py 算出的：先 --init 填 05_cost_inputs.yaml 再运行脚本'])
    if hashlib.sha256(Path(md_path).read_bytes()).hexdigest() != model.get('md_sha256'):
        errors.append('成本账 MD 与 05_cost_model.yaml 不一致：不要手改报告，改输入表后重跑脚本')
    inputs = Path(model_path).parent / '05_cost_inputs.yaml'
    if not inputs.is_file() or hashlib.sha256(inputs.read_bytes()).hexdigest() != model.get('inputs_sha256'):
        errors.append('05_cost_inputs.yaml 在计算后又被修改：重跑 s1_cost_model.py')
    if not model.get('scenarios'):
        errors.append('成本账缺售价档')
    return result(errors, cost_basis=model.get('cost_basis'))


def check_product_ct(root, *, home=None, require_image_scan=False):
    """S1.6: the chosen product's CT (what it is / is not) is product_ct.py output for the current CT file."""
    folder = Path(root) / 'S1_选品板块/1688-product-detail-scraper'
    status_path, ct_path, md_path = folder / '03_product_ct_status.json', folder / '03_product_ct.yaml', folder / '03_产品CT.md'
    if not status_path.is_file() or not ct_path.is_file():
        return result(['缺产品 CT：product_ct.py --init → 按 1688 页面/图片/厂家回复填 03_product_ct.yaml → 再运行脚本'])
    status = json.loads(status_path.read_text(encoding='utf-8'))
    if status.get('generator') != 'product_ct.py':
        return result(['产品 CT 状态不是 product_ct.py 生成的'])
    errors = []
    if hashlib.sha256(ct_path.read_bytes()).hexdigest() != status.get('ct_sha256'):
        errors.append('03_product_ct.yaml 在校验后又被修改：重跑 product_ct.py')
    if not md_path.is_file() or hashlib.sha256(md_path.read_bytes()).hexdigest() != status.get('md_sha256'):
        errors.append('03_产品CT.md 被手改：改 03_product_ct.yaml 后重跑 product_ct.py')
    if status.get('errors'):
        errors.append('产品 CT 还有「必须改」：' + '；'.join(status['errors']))
    if require_image_scan:
        try:
            ct = load(ct_path); scan = ct.get('image_scan') or {}
            if not home or not scan.get('images_root'): raise ValueError('缺全部主图/详情图真实清单和识别记录')
            producer = module(Path(home)/'skills/1688-product-detail-scraper/scripts/product_ct.py')
            observed = producer.verify_image_scan(root, scan['images_root'], scan)
            if not observed['complete']: errors.append('主图/详情图尚有未识别或失败项，不能声称全图CT完成')
            if observed['inventory_sha256'] != scan.get('inventory_sha256'): errors.append('图片清单在识别后变化，重做对应原图核验')
            image_hashes = {row['source']:row['sha256'] for row in observed['images'] if row.get('status') == 'scanned'}
            sourced_fields = [node for node in (ct.get('sources') or {}).values() if node.get('status') == 'image_extracted' and
                             node.get('source') in image_hashes and node.get('sha256') == image_hashes[node['source']]]
            if len(sourced_fields) < 7: errors.append('全图CT少于7项可追溯图片规格字段，缺项仍需明确处理')
        except (ValueError,OSError,KeyError,TypeError) as error:
            errors.append(str(error))
    return result(errors, material=status.get('material_cn'), material_status=status.get('material_status'))


def check_compliance_ruler(root, md_path, model_path, blacklist_path):
    """Validate honest research delivery separately from procurement/publication clearance."""
    try:
        model = load(model_path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return result(['合规模型缺失或不可读：' + str(error)], research_delivery_ready=False, procurement_cleared=False)
    if not isinstance(model, dict) or model.get('generator') != 'compliance_ruler.py':
        return result(['合规尺子不是 compliance_ruler.py 生成的：先 --init，逐项判属性、做检索，再运行脚本'])
    errors, warnings, gaps = [], [], []
    root, md_path, model_path = Path(root).resolve(), Path(md_path), Path(model_path)
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    try:
        modern = tuple(int(part) for part in str(model.get('schema_version', '1.0')).split('.')) >= (1, 1)
    except ValueError:
        modern = False
        errors.append('合规模型 schema_version 无效')
    if sha(md_path) != model.get('md_sha256') or not md_path.is_file():
        errors.append('合规尺子 MD 被手改：改 04A_ruler_inputs.yaml 后重跑脚本')
    inputs = model_path.parent / '04A_ruler_inputs.yaml'
    if not inputs.is_file() or sha(inputs) != model.get('inputs_sha256'):
        errors.append('04A_ruler_inputs.yaml 在生成后又被修改：重跑 compliance_ruler.py')
    unknown = model.get('unknown_attributes') or []
    if modern:
        receipt_path = model_path.parent / '04A_compliance_receipt.json'
        try:
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            receipt = {}
            errors.append('缺合规生成回执或回执不可读：重跑 compliance_ruler.py')
        if not isinstance(receipt, dict):
            receipt = {}
            errors.append('合规生成回执格式错误')
        if receipt:
            if receipt.get('generator') != 'compliance_ruler.py' or receipt.get('schema_version') != model.get('schema_version'):
                errors.append('合规生成回执与模型版本不一致')
            for path, key, label in ((model_path, 'model_sha256', '模型'), (md_path, 'md_sha256', 'MD'),
                                     (inputs, 'inputs_sha256', 'inputs')):
                if not path.is_file() or sha(path) != receipt.get(key):
                    errors.append(f'合规{label}与生成回执哈希不一致：重跑脚本')
        sources = model.get('source_files_sha256')
        if not isinstance(sources, dict):
            errors.append('合规模型缺来源哈希清单')
            sources = {}
        if receipt and receipt.get('source_files_sha256') != sources:
            errors.append('合规来源哈希清单与回执不一致')
        expected = ('S1_选品板块/amazon-market-research/01_product_profile.yaml',
                    'S1_选品板块/1688-product-detail-scraper/03_product_facts.yaml')
        if any((root / rel).is_file() and rel not in sources for rel in expected):
            errors.append('合规来源文件未纳入哈希清单：重跑脚本')
        for rel, recorded_hash in sources.items():
            path = (root / rel).resolve()
            if not path.is_relative_to(root) or not path.is_file() or sha(path) != recorded_hash:
                errors.append('合规来源文件已变化或越界：' + rel)
        attrs = model.get('attributes') or {}
        actual_unknown = {key for key, node in attrs.items() if isinstance(node, dict) and node.get('value') is None}
        pending = model.get('pending_confirmations') or []
        pending_ids = {item.get('attribute') for item in pending if isinstance(item, dict) and item.get('value') is None and item.get('label')}
        if set(unknown) != actual_unknown or pending_ids != set(unknown) or len(pending) != len(unknown):
            errors.append('待核属性、产品属性和待厂家确认清单不完整或不一致')
        if unknown:
            gaps.append('待厂家确认：' + '、'.join(unknown))
            if model.get('overall_light') == 'green':
                errors.append('存在待核属性，不能标为绿色已确认')
        if model.get('overall_light') not in {'red', 'orange', 'yellow', 'green', 'gray'}:
            errors.append('合规灯色无效')
    elif unknown:
        errors.append('产品属性未判完：' + '、'.join(unknown))
    for search in model.get('searches', []):
        if search.get('problem'):
            errors.append(f"检索 {search.get('id')}：{search['problem']}（查不了就写 user_todo 和原因）")
        if modern and search.get('status') == 'user_todo':
            if not str(search.get('reason') or '').strip():
                errors.append(f'检索 {search.get("id")} 缺待你查原因')
            card = search.get('self_check_card') or {}
            schema = card.get('screenshot_return_schema') or {}
            required_fields = {'search_id', 'searched_at', 'query', 'filters', 'result_count', 'record_ids',
                               'result_urls', 'screenshots', 'selected_sku', 'assessment', 'coverage_limits'}
            if (not card.get('links') or not card.get('queries') or not card.get('steps')
                    or not required_fields.issubset(schema) or schema.get('search_id') != search.get('id')
                    or not schema.get('screenshots')):
                errors.append(f'检索 {search.get("id")} 缺完整可操作自查卡')
            gaps.append('待你查：' + str(search.get('id')))
        elif modern and search.get('status') == 'searched':
            if not search.get('date') or not str(search.get('result') or '').strip():
                errors.append(f'检索 {search.get("id")} 缺实际日期或检索结果')
        elif modern:
            errors.append(f'检索 {search.get("id")} 未检索且未安排自查')
    if model.get('missing_searches'):
        errors.append('缺必做检索：' + '、'.join(model['missing_searches']))
    expected_ids = {'uspto_trademark', 'design_patent', 'utility_patent', 'image_reverse', 'schedule_a'}
    if modern and {s.get('id') for s in model.get('searches', [])} != expected_ids:
        errors.append('必做检索清单未覆盖五项')
    if modern and model.get('searches_complete') and any(s.get('status') != 'searched' for s in model.get('searches', [])):
        errors.append('待查项目不能计作检索已完成')
    try:
        listed = Path(blacklist_path).read_text(encoding='utf-8-sig').lower()
    except OSError:
        listed = ''
        errors.append('缺合规黑名单文件')
    missing = [t for t in model.get('blacklist_terms', []) if t.lower() not in listed]
    if missing:
        errors.append('合规尺子命中的商标陷阱/他牌名未进黑名单：' + '、'.join(missing))
    if gaps:
        warnings.append('研究报告可如实列示待核/待查；这些数据缺口不代表采购或上架获准。')
    return result(errors, overall_light=model.get('overall_light'), counts=model.get('counts'),
                  warnings=warnings, data_gaps=gaps, research_delivery_ready=not errors,
                  procurement_cleared=False, publication_cleared=False, searches_complete=model.get('searches_complete', False))


def check_s5_step(number, file):
    """S5 运营：快照是脚本产出、每条动作有依据、审批对得上方案指纹、落地有回执。"""
    errors = []
    if number == 1:
        snap = load(file('snapshot_json'))
        if snap.get('generator') != 's5_optimize.py':
            errors.append('广告快照不是 s5_optimize.py ingest 生成的')
    elif number == 2:
        plan = load(file('plan_json'))
        if plan.get('generator') != 's5_optimize.py':
            errors.append('诊断方案不是 s5_optimize.py diagnose 生成的')
        evidence = load(file('evidence_yaml')).get('actions') or {}
        empty = [a['id'] for a in plan.get('actions') or [] if not ((evidence.get(a['id']) or {}).get('advice'))]
        if empty:
            errors.append('这些动作还没写依据（advice 建议执行 / 暂缓）：' + '、'.join(empty))
    elif number == 3:
        report = Path(file('report_html'))
        status = load(report.parent / '03_report_status.json')
        if status.get('errors'):
            errors.append('报告还有「必须改」：' + '；'.join(status['errors']))
        decision = load(file('approval_json'))
        plan_path = report.parent / '02_诊断方案.json'
        if decision.get('plan_sha256') and plan_path.is_file() and decision['plan_sha256'] != hashlib.sha256(plan_path.read_bytes()).hexdigest():
            errors.append('审批后方案又变了：重新出报告、重新审批')
    elif number == 4:
        if not load(file('receipt_json')).get('uploaded'):
            errors.append('回执没写上传 / 写回结果')
    return result(errors)


def check_brand(page_path, status_path):
    """S3.10/11: the upload package is brand_preview.py output for the current plan, with nothing left under 必须改."""
    status = json.loads(Path(status_path).read_text(encoding='utf-8'))
    if status.get('generator') != 'brand_preview.py':
        return result(['品牌上传包不是 brand_preview.py 生成的：改方案 YAML 后运行脚本'])
    errors = []
    if hashlib.sha256(Path(page_path).read_bytes()).hexdigest() != status.get('page_sha256'):
        errors.append('从这里开始.html 被手改：改方案 YAML 后重跑 brand_preview.py')
    plan = Path(status_path).parent / ('10_brand_story.yaml' if status.get('kind') == 'story' else '11_brand_store.yaml')
    if not plan.is_file() or hashlib.sha256(plan.read_bytes()).hexdigest() != status.get('plan_sha256'):
        errors.append('方案 YAML 在生成上传包后又被修改：重跑 brand_preview.py')
    if status.get('errors'):
        errors.append('上传包还有「必须改」：' + '；'.join(status['errors']))
    return result(errors, counts=status.get('counts'), version=status.get('version'))


def check_step(home, root, stage, number, receipt, *, knowledge_verification=None):
    """Run all native checks in one disposable view of compact business files."""
    home, root = Path(home).resolve(), Path(root).resolve()
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(home / 'scripts'))
        spec = importlib.util.spec_from_file_location('starsky_gate_case_store', home / 'scripts/r01_case_store.py')
        helper = importlib.util.module_from_spec(spec); sys.modules[spec.name] = helper; spec.loader.exec_module(helper)
        store = helper.CompactCase(root)
        overrides = helper.decode_business_files(receipt.get('business_files', {}))
        if not overrides and not store.enabled():
            return _check_step(home, root, stage, number, receipt, knowledge_verification=knowledge_verification)
        with store.materialize(overrides=overrides) as view:
            artifacts = {}
            for role, value in receipt.get('artifacts', {}).items():
                item = dict(value) if isinstance(value, dict) else {'path': value}
                item['path'] = str(view.map_path(item['path']))
                artifacts[role] = item if isinstance(value, dict) else item['path']
            checked = _check_step(home, view.root, stage, number, {**receipt, 'artifacts': artifacts},
                                  knowledge_verification=knowledge_verification)
            # Errors and full audits must refer to the durable product, never a
            # temporary pathname whose lifetime ends at this return boundary.
            def logical(value):
                if isinstance(value, str):
                    return value.replace(str(view.root), str(view.logical_root)).replace(
                        Path(view.root).as_posix(), Path(view.logical_root).as_posix())
                if isinstance(value, list): return [logical(item) for item in value]
                if isinstance(value, dict): return {logical(key): logical(item) for key, item in value.items()}
                return value
            return logical(checked)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return result([str(error)], checks={})
    finally:
        sys.path[:] = previous_paths


def _check_step(home, root, stage, number, receipt, *, knowledge_verification=None):
    errors = []; details = {}; home = Path(home); root = Path(root)
    try:
        files = all_artifacts(root, stage, receipt)
        state = load(root / 'workflow_state.yaml')
        from s1_roadmap import resolve_roadmap
        stage_route = resolve_roadmap(home, root, state)['stages'][stage]
        stage_steps = stage_route['steps']
        delivery_step = int(stage_route.get('delivery_step', len(stage_steps)))
        step = stage_steps[number - 1]
        if receipt.get('outcome') != 'skipped':
            for role in step['control']['required_artifacts']:
                if role not in receipt.get('artifacts', {}): errors.append('缺本步产物：' + role)
        def add(name, checked):
            details[name] = checked
            errors.extend(checked.get('errors', []))
        def file(role):
            if role not in files: raise ValueError('缺校验依赖产物：' + role)
            return files[role]
        def native(relative, args): return run(home, root, relative, args)
        def native_audit(relative, args, filename):
            # Controller reruns are audit data in the receipt. The separately
            # approved prior validation remains a persistent, SHA-bound input.
            with tempfile.TemporaryDirectory(prefix='starsky-gate-') as temporary:
                output = Path(temporary) / filename
                checked = native(relative, [*args, '--output', output])
                try:
                    raw = output.read_bytes()
                    audit = yaml.safe_load(raw.decode('utf-8-sig'))
                    if not isinstance(audit, dict):
                        raise ValueError('校验审计必须是结构化对象')
                    checked.update(audit=audit, audit_sha256=hashlib.sha256(raw).hexdigest())
                except (OSError, ValueError, yaml.YAMLError) as error:
                    checked['errors'].append('缺少或无法解析真实校验审计：' + str(error))
                    checked['status'] = 'fail'
                return checked
        from verify_kb_evidence import verify_stage, record_references
        if knowledge_verification is not None:
            # Internal controller argument only. Never trust a receipt field as
            # a claimed pass; standalone/CLI calls still execute the verifier.
            add('knowledge',knowledge_verification)
        elif number == delivery_step:
            state = load(root / 'workflow_state.yaml') if (root / 'workflow_state.yaml').exists() else {}
            flow=state.get(stage.lower()+'_execution') or {}
            records = [{**row, 'receipt': load_record_receipt(root, row)} for row in flow.get('records', [])]
            seal=(flow.get('validation') or {}).get('knowledge_stage_seal')
            if seal is None:
                previous=next((row for row in reversed(records) if row.get('n')==number),{})
                seal=(previous.get('knowledge') or {}).get('response_hashes')
            records = [r for r in records if r.get('n') != number] + [{'n':number, 'receipt':receipt}]
            add('knowledge', verify_stage(root, home, stage, records,expected_response_hashes=seal))
        else:
            add('knowledge', record_references(root, home, stage, number, receipt))
        # verify_stage already includes every step's actual-result observations.
        m2 = stage == 'S1' and (state.get('s1_execution') or {}).get('roadmap_version') == 'r01.m2'
        if m2 and receipt.get('outcome') != 'blocked':
            from s1_research_gates import check_m2_step
            add('research_gate', check_m2_step(root, number, receipt, state.get('s1_execution') or {}))
            from s1_step_contract import check_step_contract
            add('early_report_contract', check_step_contract(root, number))
        if stage == 'S1' and number == (5 if m2 else 6) and receipt.get('outcome') != 'blocked':
            add('product_ct', check_product_ct(root, home=home, require_image_scan=m2))
        if stage == 'S1' and number == (6 if m2 else 7) and receipt.get('outcome') != 'blocked':
            add('cost_model', check_cost_model(root, file('cost_md'), file('cost_model')))
        if stage == 'S1' and number == 8 and receipt.get('outcome') != 'blocked':
            add('compliance_ruler', check_compliance_ruler(root, file('ruler_md'), file('ruler_yaml'), file('blacklist_yaml')))
        if stage == 'S2':
            inputs_module=module(home / 'scripts/s2_roadmap.py')
            add('current_inputs',inputs_module.check_inputs(root,number,files,state))
            gate_module = module(home / 'skills/_keyword_system_contract/scripts/s2_competitor_gate.py')
            if number >= 2:
                gate = gate_module.validate_gate(load(file('competitor_gate')), root); add('competitor_pool', gate)
            if number >= 3:
                export_module = module(home / 'skills/_keyword_system_contract/scripts/s2_export_intake_gate.py')
                manifest_path = file('export_manifest'); manifest = load(manifest_path)
                package = {'run_id': manifest.get('run_id'), 'source_mode': manifest.get('source'),
                           's2_export_intake': {'path': manifest_path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}}
                add('real_exports', export_module.validate_exports(package, manifest_path, gate.get('confirmed_asins', [])))
            if number >= 4:
                packages = [p for role, p in files.items() if role.startswith('keyword_package')]
                if not packages: errors.append('缺实际词包')
                for package_path in packages:
                    add(package_path.name, native('skills/_keyword_system_contract/scripts/keyword_package_contract.py', ['validate', '--input', package_path]))
                htmls = [p for role, p in files.items() if role.startswith('keyword_html')]
                if len(htmls) != len(packages): errors.append('词包来源线与词库 HTML 数量不一致')
                feeds = root / 'S2_文案板块/_投喂_关键词导出'
                active = [d for d in feeds.iterdir() if d.is_dir() and any(p.suffix.lower() in {'.csv', '.xlsx'} for p in d.rglob('*'))] if feeds.exists() else []
                if len(active) > len(packages): errors.append('有原始导出来源线未处理，不得只做一家就结束')
                if len(packages) > 1 and 'source_comparison_md' not in files: errors.append('多源词库缺差异对照报告')
                # An unreviewed queue leaves the usable pool small, and ST/copy then run out of eligible words.
                for package_path in packages:
                    queue, decided = package_path.parent / 'review_queue.json', package_path.parent / 'review_decisions.json'
                    if queue.is_file() and load(queue) and not decided.is_file():
                        errors.append('复核队列未处理：先对 review_queue.json 前列逐个裁决写入 review_decisions.json 并重跑清洗')
            visible = module(home / 'skills/_keyword_system_contract/scripts/s2_visible_report_gate.py')
            add('visible_reports', visible.check_step(root, number, files))
            if number == 7:
                add('listing_copy', native_audit('skills/amazon-listing-copywriter/scripts/validate_listing_copy.py', ['--input', file('listing_input_json')], 'listing_check.json'))
                # R08.2: copy done is S2 done. A user review is recorded when given; without one the delivery
                # carries a "review before listing" to-do instead of blocking S2.
                review = load(files['listing_review']) if 'listing_review' in files else {}
                reviewed = (review.get('source') == 'user' and review.get('status') == 'approved'
                            and review.get('sha256') == hashlib.sha256(file('listing_md').read_bytes()).hexdigest())
                details['listing_review'] = 'approved' if reviewed else 'not_reviewed'
        elif stage == 'S3':
            deferred = receipt.get('outcome') in {'deferred', 'skipped'}
            identity = step['control']['id']
            modern = stage_route.get('roadmap_version') == 'r03.s3'
            if modern and identity == 'visual_references':
                planner = module(home/'skills/_visual_suite_contract/scripts/visual_suite.py')
                inventory, inventory_path = planner._inventory(root)
                if file('asset_manifest').resolve() != inventory_path.resolve():
                    errors.append('素材清单未绑定本次全量盘点')
                if any(a.get('value')=='unreviewed' for a in inventory['assets']):
                    errors.append('素材盘点仍有未查看项；先查看图片/视频对照表并标注，不把扫描当成理解')
                details['asset_gaps'] = inventory.get('gaps') or []
            if modern and number >= 3:
                import product_manual
                manual = product_manual.read_confirmed(root)
                details['product_manual'] = {'complexity': manual['complexity'], 'current': True}
                if identity == 'product_manual':
                    for role, name in [('manual_md','产品使用说明书.md'),('manual_html','产品使用说明书.html')]:
                        if file(role).resolve() != (root/'00_产品资料'/name).resolve():
                            errors.append('说明书产物路径与当前确认记录不一致：'+role)
            if modern and number >= 5:
                planner = module(home/'skills/_visual_suite_contract/scripts/visual_suite.py')
                plan = planner.load_plan(root)
                details['visual_plan'] = {'current': True, 'slots': len(plan['slots']), 'aplus_modules': len(plan['aplus_slots'])}
                if identity == 'visual_master_plan':
                    for role, name in [('suite_yaml','visual_suite.yaml'),('plan_html','视觉总规划.html')]:
                        if file(role).resolve() != (root/'S3_视觉板块/amazon-listing-image-designer'/name).resolve():
                            errors.append('视觉规划产物路径与当前确认记录不一致：'+role)
            if number >= (6 if modern else 4):
                if modern:
                    packer = module(home/'skills/_visual_suite_contract/scripts/pack_gpt_web_feeds.py')
                    add('gpt_web_feeds', packer.verify_pack(root))
                else:
                    add('gpt_web_feeds', check_gpt_web_feeds(root))
            if modern and identity == 'video_package':
                video_packer = module(home/'skills/_visual_suite_contract/scripts/pack_gpt_web_feeds.py')
                details['video_package'] = video_packer.pack_video(root,file('video_package_yaml'),write=False)
            media = {'listing_image_production':('listing_media_manifest',7),
                     'aplus_production':('aplus_media_manifest',1), 'video_production':('video_media_manifest',1)}
            if modern and number >= 5:media['listing_image_production']=('listing_media_manifest',7*len(plan['variants']))
            if identity in media:
                key, minimum = media[identity]
                add(key, check_aplus(root, file(key), not deferred,expected_modules=len(plan['aplus_slots']) if modern else None,aplus_type=plan['aplus_path'] if modern else None) if identity == 'aplus_production' else check_media(root, file(key), require_generated=not deferred, minimum=minimum))
                if modern and identity!='video_production' and not deferred:
                    post = module(home/'scripts/visual_postprocess.py')
                    add('native_final_media',post.verify_media_manifest(root,file(key),'aplus' if identity=='aplus_production' else 'listing'))
                    if identity=='aplus_production' and plan.get('aplus_production_version')=='r03.m2':
                        upload=planner.write_aplus_upload_list(root)
                        add('aplus_upload_list',{'status':'pass' if upload['status']=='ready' else 'fail','errors':upload['errors']})
            if modern and identity=='video_production' and not deferred:
                native_video=module(home/'scripts/视频出片.py');add('native_video',native_video.r03_verify_manifest(root,file('video_media_manifest')))
            if identity in {'brand_story','brand_store'} and receipt.get('outcome') not in {'skipped', 'blocked', 'deferred'}:
                kind = 'story' if identity == 'brand_story' else 'store'
                add('brand_' + kind, check_brand(file(f'brand_{kind}_page'), file(f'brand_{kind}_status')))
                if modern:
                    brand=module(home/'skills/_brand_visual_contract/scripts/brand_preview.py')
                    add('brand_native',brand.verify_r03_package(root,kind))
            if identity == 's3_report_delivery':
                if modern:
                    report=module(home/'scripts/s3_stage_report.py')
                    checked=report.verify_report_file(file('report_html'))
                    if file('report_md').resolve() != (root/report.MD).resolve():
                        errors.append('阶段报告 MD 不在正式入口，请运行本版报告生成器')
                    add('native_stage_report',result(checked['errors']))
                prior = state.get('s3_execution', {}).get('records', [])
                modes = {r['step']: r['outcome'] for r in prior}
                sub = {}
                for task, (key, minimum) in media.items():
                    label = {'listing_image_production':'images','aplus_production':'premium_aplus','video_production':'video'}[task]
                    checked = check_aplus(root, file(key), modes.get(task) not in {'deferred','skipped'},expected_modules=len(plan['aplus_slots']) if modern else None,aplus_type=plan['aplus_path'] if modern else None) if task=='aplus_production' else check_media(root, file(key), modes.get(task) not in {'deferred', 'skipped'}, minimum)
                    if modern and task=='video_production' and modes.get(task) not in {'deferred','skipped'}:
                        native_video=module(home/'scripts/视频出片.py');add('native_video',native_video.r03_verify_manifest(root,file(key)))
                    add(label, checked); sub[label] = {k: checked[k] for k in ['package_ready', 'final_accepted', 'generated', 'reviewed']}
                    if modern and task!='video_production':
                        post=module(home/'scripts/visual_postprocess.py')
                        add(label+'_native',post.verify_media_manifest(root,file(key),'aplus' if task=='aplus_production' else 'listing'))
                        if task=='aplus_production' and plan.get('aplus_production_version')=='r03.m2':
                            upload=planner.write_aplus_upload_list(root)
                            add('aplus_upload_list',{'status':'pass' if upload['status']=='ready' else 'fail','errors':upload['errors']})
                    if modes.get(task)=='deferred':sub[label].update(outcome='deferred',available=False)
                choices=state.get('s3_execution',{}).get('optional_decisions') or {}
                for label in ('brand_story','brand_store'):
                    sub[label] = {'outcome': modes.get(label, choices.get(label,{}).get('outcome','not_run'))}
                details['sub_status'] = sub
        elif stage == 'S4':
            modern=stage_route.get('roadmap_version')=='r04.s4'
            if modern:
                if 'plan_yaml' in files:
                    import stage_deferred
                    errors.extend(stage_deferred.downstream_errors(home,state,load(file('plan_yaml'))))
                if number>=3:
                    strategy=load(file('strategy_brief'));review=load(file('strategy_review'))
                    add('strategy',native('skills/amazon-ad-strategy-architect/scripts/validate_strategy_brief.py',['--strategy',file('strategy_brief')]))
                    lib=module(home/'skills/amazon-ad-campaign-builder/scripts/ad_plan_lib.py');diagnostic={'blockers':[],'warnings':[]};lib.verify_strategy_review(strategy,review,diagnostic);errors.extend(diagnostic['blockers'])
                if number>=4:
                    plan=load(file('plan_yaml'));lib=module(home/'skills/amazon-ad-campaign-builder/scripts/ad_plan_lib.py')
                    errors.extend(lib.validate_plan(plan)['errors'])
                    if plan.get('flow_version')!='r04.s4':errors.append('本轮需要新版上架前方案，不能复用旧编号流程')
                    allowed=lambda reason: reason=='PRODUCTION_MISSING_SELLER_SKU' or (number==4 and reason.startswith('LAUNCH_BID_CONFLICT_REQUIRES_CHOICE:'))
                    errors.extend(reason for reason in plan.get('upload_eligibility',{}).get('reasons',[]) if not allowed(reason))
                if number>=5:
                    reviewed=load(file('architecture_review'))
                    if reviewed.get('status')!='approved_for_bulk' or reviewed.get('plan_hash')!=plan['plan_hash'] or reviewed.get('decision',{}).get('source')!='user':errors.append('当前方案尚未获得本人批准，或批准后已变化')
                    launch=module(home/'scripts/s4_launch.py');checked=launch.verify_report(root);add('s4_report',{'status':'pass' if checked['ok'] else 'fail','errors':checked['errors']})
                    add('html_delivery',check_html(home,root,stage,files))
                if number>=6:
                    launch=module(home/'scripts/s4_launch.py');proof=plan.get('sku_mapping_confirmation') or {}
                    launch.validate_mapping(plan,proof.get('rows') or [],proof.get('decision') or {})
                    args=['--plan',file('plan_yaml'),'--review-gate',file('architecture_review'),'--template',file('bulk_template'),'--template-ref',file('bulk_template_ref'),'--bulk',file('bulk_xlsx'),'--manifest',file('bulk_manifest'),'--bulk-release-review',file('bulk_release_review'),'--approved-validation',file('approved_validation')]
                    if 'compile_context' in files:args+=['--compile-context',file('compile_context')]
                    else:args+=['--compile-context',str(root/'S4_广告板块/_阶段总览/bulk_compile_context.yaml')]
                    checked=native_audit('skills/amazon-bulk-upload-troubleshooter/scripts/validate_bulk.py',args,'bulk_validation.yaml');add('bulk',checked)
                    if 'audit' in checked:
                        details['upload_status']=checked['audit'].get('uploadability')
                        if checked['audit'].get('uploadability',{}).get('status')!='reviewed_candidate_upload_prepared':errors.append('SKU或上传表的正式放行检查尚未通过')
            else:
                if 'plan_yaml' in files:
                    import stage_deferred
                    errors.extend(stage_deferred.downstream_errors(home, state, load(file('plan_yaml'))))
                if number >= 4:
                    strategy = load(file('strategy_brief')); review = load(file('strategy_review'))
                    add('strategy', native('skills/amazon-ad-strategy-architect/scripts/validate_strategy_brief.py', ['--strategy', file('strategy_brief')]))
                    lib = module(home / 'skills/amazon-ad-campaign-builder/scripts/ad_plan_lib.py')
                    diagnostic = {'blockers': [], 'warnings': []}
                    lib.verify_strategy_review(strategy, review, diagnostic)
                    errors.extend(diagnostic['blockers'])
                if number >= 5:
                    args = ['--plan', file('plan_yaml'), '--json']
                    if number >= 7: args += ['--architecture-review', file('architecture_review')]
                    add('ad_plan', native('skills/amazon-ad-campaign-builder/scripts/validate_ad_plan.py', args))
                if number >= 8:
                    args = ['--plan', file('plan_yaml'), '--review-gate', file('architecture_review'), '--template', file('bulk_template'),
                            '--template-ref', file('bulk_template_ref'), '--bulk', file('bulk_xlsx'), '--manifest', file('bulk_manifest'),
                            '--bulk-release-review', file('bulk_release_review'), '--approved-validation', file('approved_validation')]
                    if 'compile_context' in files: args += ['--compile-context', files['compile_context']]
                    checked = native_audit('skills/amazon-bulk-upload-troubleshooter/scripts/validate_bulk.py', args, 'bulk_validation.yaml')
                    add('bulk', checked)
                    if 'audit' in checked:
                        data = checked['audit']; readiness = data.get('uploadability', {}).get('status')
                        if readiness not in {'reviewed_candidate_upload_prepared', 'demo_only_not_uploadable'}:
                            errors.append('Bulk 技术通过仍缺独立放行审核：' + str(readiness))
                        details['upload_status'] = data.get('uploadability')
        elif stage == 'S5' and receipt.get('outcome') not in ('blocked', 'skipped', 'deferred'):
            add('s5', check_s5_step(number, file))
        if number == delivery_step and stage != 'S5':  # S5 的报告是第 3 步的优化前后对比；第 5 步日报可选
            add('html_delivery', check_html(home, root, stage, files))
            if 'report_md' not in files: errors.append('缺阶段综合 Markdown 报告')
        return result(errors, checks=details)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        return result(errors + [str(error)], checks=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--stage', choices=['S1', 'S2', 'S3', 'S4', 'S5'], required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        home = Path(__file__).resolve().parents[1]
        from s1_roadmap import resolve_roadmap
        steps = resolve_roadmap(home, args.root.resolve())['stages'][args.stage]['steps']
        checked = check_step(home, args.root.resolve(), args.stage, len(steps), load(args.receipt))
    except Exception as error:
        checked = result([str(error)])
    print(json.dumps(checked, ensure_ascii=False, indent=2))
    return 0 if checked['status'] == 'pass' else 1


if __name__ == '__main__': raise SystemExit(main())
