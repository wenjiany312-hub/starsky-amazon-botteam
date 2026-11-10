"""Verify step knowledge use; an explained outage passes without claiming use."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re

import yaml
import kb_plan

CARD_ID = re.compile(r'^(?:KC|PK)-\d{4}$')
SCRIPT_GENERATED_ROLES = frozenset({'ruler_md'})


def call_counter(calls):
    return Counter(json.dumps({k: row[k] for k in ('server', 'library', 'tool', 'args')},
                              sort_keys=True, ensure_ascii=False) for row in calls)


def comparable_calls(planned, calls):
    """Keep every health probe in evidence; exclude only excess valid probes."""
    expected, actual, additions = call_counter(planned), Counter(), []
    for row in calls:
        row = {**row, 'library': row.get('library') or kb_plan.LIBRARIES[row['server']][1]}
        key = next(iter(call_counter([row])))
        if (row['tool'] == 'kb_status' and row['args'] == {} and
                row['library'] == kb_plan.LIBRARIES[row['server']][1] and actual[key] >= expected[key]):
            additions.append(row)
        else:
            actual[key] += 1
    return actual, additions


def checked(errors, **fields):
    return {'status': 'fail' if errors else 'pass', 'errors': errors, **fields}


def read_object(path):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict): raise ValueError('知识库证据需要对象：' + str(path))
    return data


def response_ids(value):
    """Cards returned by real MCP JSON, including text-wrapped result objects."""
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get('id'), str) and CARD_ID.fullmatch(value['id']): found.add(value['id'])
        for key, item in value.items():
            if key in ('cards_cited', 'card_ids') and isinstance(item, list):
                found.update(x for x in item if isinstance(x, str) and CARD_ID.fullmatch(x))
            found.update(response_ids(item))
    elif isinstance(value, list):
        for item in value: found.update(response_ids(item))
    elif isinstance(value, str):
        try: found.update(response_ids(json.loads(value)))
        except (ValueError, TypeError): pass
    return found


def response_failed(value):
    """Inspect MCP result envelopes, including JSON-RPC and JSON text payloads."""
    if not isinstance(value, dict): return False
    if value.get('isError') or value.get('error'): return True
    if value.get('jsonrpc') and response_failed(value.get('result')): return True
    if response_failed(value.get('structuredContent')): return True
    for item in value.get('content', []):
        if isinstance(item, dict) and item.get('type')=='text':
            try:
                if response_failed(json.loads(item.get('text', ''))): return True
            except (ValueError, TypeError): pass
    return False


def card_exists(home, server, cid):
    folder = kb_plan.LIBRARIES[server][0]
    for path in (Path(home) / 'mcp' / folder / 'kb/cards').glob('*.md'):
        body = path.read_text(encoding='utf-8-sig')
        if body.startswith('---'):
            front = yaml.safe_load(body.split('---', 2)[1]) or {}
            if front.get('id') == cid and front.get('status') not in ('superseded', 'retracted'):
                return True
    return False


def report_errors(path, refs):
    body = Path(path).read_text(encoding='utf-8-sig')
    headings=list(re.finditer(r'^##[ \t]+知识库依据(?:[ \t]*[（(]第[ \t]*\d+[ \t]*步[）)])?[ \t]*\r?$',body,re.M))
    if not headings:return ['报告缺「知识库依据」小节：' + Path(path).name + '；请用登记写入器重新生成，不得手改已锁定报告']
    basis=re.split(r'^#{1,2}[ \t]+',body[headings[-1].end():],maxsplit=1,flags=re.M)[0]
    errors = []
    for server, cid in refs:
        name = kb_plan.LIBRARIES[server][1]
        # Same line/paragraph: a naked KC number cannot name the correct library.
        pattern = re.escape(name) + r'[^\n<>]{0,240}(?<![A-Z0-9-])' + re.escape(cid) + r'(?![A-Z0-9-])'
        if not re.search(pattern, basis): errors.append(f'{Path(path).name} 缺引用：{name} {cid}；请用登记写入器重新生成，不得手改已锁定报告')
    if refs and not re.search(r'(?:用于|用在|依据|支持|核对|判定|判断|检查|对照)', basis):
        errors.append('知识库依据未说明用在什么判断：' + Path(path).name)
    return errors


def reports(root, home, stage, number, receipt):
    roles = kb_plan.roadmap_for(root,home)['stages'][stage]['steps'][number-1]['control']['required_artifacts']
    result = []
    for role, item in (receipt or {}).get('artifacts', {}).items():
        if role not in roles or not role.endswith('_md') or role in SCRIPT_GENERATED_ROLES: continue
        value = item.get('path') if isinstance(item, dict) else item
        path = kb_plan.inside(root, value)
        if not path.is_file(): raise ValueError('缺本步知识库引用报告：' + str(path))
        result.append(path)
    if not result: raise ValueError('缺本步业务 Markdown 报告；知识库单独说明不能代替它')
    return result


class VerificationCache:
    """One stage closure owns this cache; never reuse across later revisions."""
    def __init__(self):
        self.raw = {}
        self.objects = {}
        self.hashes = {}
        self.card_indexes = {}

    def bytes(self, path):
        path = Path(path).resolve()
        if path not in self.raw:
            self.raw[path] = path.read_bytes()
        return self.raw[path]

    def response(self, path, selector=None):
        path = Path(path).resolve()
        if path not in self.objects:
            self.objects[path] = json.loads(self.bytes(path).decode('utf-8-sig'))
        value = self.objects[path]
        if selector:
            if not isinstance(selector, str) or not selector.startswith('/'):
                raise ValueError('response_selector 必须是 JSON Pointer')
            for token in selector[1:].split('/'):
                if re.search(r'~(?![01])', token):
                    raise ValueError('JSON Pointer 转义无效')
                token = token.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list):
                    if not re.fullmatch(r'0|[1-9][0-9]*', token):
                        raise ValueError('JSON Pointer 数组下标无效')
                    value = value[int(token)]
                else:
                    value = value[token]
        return value

    def sha256(self, path, selector=None):
        key = (Path(path).resolve(), selector)
        if key not in self.hashes:
            raw = (json.dumps(self.response(path, selector), ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf8') if selector else self.bytes(path))
            self.hashes[key] = hashlib.sha256(raw).hexdigest()
        return self.hashes[key]

    def card_exists(self, home, server, cid):
        key = (Path(home).resolve(), server)
        if key not in self.card_indexes:
            folder = kb_plan.LIBRARIES[server][0]
            ids = set()
            for path in (Path(home) / 'mcp' / folder / 'kb/cards').glob('*.md'):
                body = path.read_text(encoding='utf-8-sig')
                if body.startswith('---'):
                    front = yaml.safe_load(body.split('---', 2)[1]) or {}
                    if front.get('status') not in ('superseded', 'retracted'):
                        ids.add(front.get('id'))
            self.card_indexes[key] = ids
        return cid in self.card_indexes[key]


def _evidence(root, plan, receipt):
    inline = (receipt or {}).get('kb_evidence')
    return inline if isinstance(inline, dict) else read_object(kb_plan.inside(root, plan['evidence_path']))


def _full_card_ids(value):
    ids = Counter()
    if isinstance(value, dict):
        if isinstance(value.get('structuredContent'), dict):
            return _full_card_ids(value['structuredContent'])
        if value.get('jsonrpc') and isinstance(value.get('result'), dict):
            return _full_card_ids(value['result'])
        if value.get('id') and value.get('title') and value.get('statement'):
            ids[value['id']] += 1
        for item in value.values():
            ids.update(_full_card_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.update(_full_card_ids(item))
    elif isinstance(value, str):
        try:
            ids.update(_full_card_ids(json.loads(value)))
        except (ValueError, TypeError):
            pass
    return ids


def record_references(root, home, stage, number, receipt=None):
    """Record declarations without reading/hashing MCP response bodies."""
    errors = []
    refs = set()
    try:
        plan = kb_plan.plan_step(root, home, stage, number)
        if plan.get('reuse_step'):
            return checked([], knowledge_status='pending', verification_status='pending_stage',
                           reuse_step=plan['reuse_step'], refs=[], card_count=0)
        if not plan['calls']:
            return checked([], knowledge_status='none', verification_status='pending_stage', refs=[], card_count=0)
        evidence = _evidence(root, plan, receipt)
        if evidence.get('step') != plan['step']:
            raise ValueError('知识库证据步骤不符')
        if evidence.get('status') not in ('used', 'unavailable', 'skipped'):
            raise ValueError('知识库证据状态无效')
        if evidence.get('status') == 'skipped':
            if plan['required'] or not evidence.get('reason'):
                raise ValueError('必需知识库不能静默跳过')
        else:
            if not evidence.get('calls'):
                raise ValueError('缺真实知识库调用记录')
            for row in evidence['calls']:
                server = row['server']
                if server not in kb_plan.LIBRARIES:
                    raise ValueError('未知知识库服务')
                kb_plan.validate_args(kb_plan.schemas(home, server)[row['tool']], row['args'])
                if row.get('status') == 'error':
                    if not row.get('error') or row.get('card_ids'):
                        raise ValueError('失败调用缺原因或冒充引用')
                    continue
                if row.get('status') != 'ok':
                    raise ValueError('调用缺真实成功/失败状态')
                path = kb_plan.inside(root, row['response_path'])
                if not path.is_file():
                    raise ValueError('缺知识库原始响应')
                for cid in row.get('card_ids', []):
                    if not CARD_ID.fullmatch(str(cid)):
                        raise ValueError('卡号格式错误')
                    refs.add((server, cid))
        return checked(errors, knowledge_status=evidence['status'], verification_status='pending_stage',
                       refs=[{'server': s, 'library': kb_plan.LIBRARIES[s][1], 'card_id': c} for s, c in sorted(refs)],
                       card_count=len(refs), evidence_path=plan['evidence_path'])
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        return checked([str(error)], verification_status='pending_stage', refs=[], card_count=0)


def verify_stage(root, home, stage, records=None, *, require_complete=True, expected_response_hashes=None):
    """Fully verify a stage with one response/hash cache, without mutating evidence."""
    records = (kb_plan.load(Path(root) / 'workflow_state.yaml').get(stage.lower() + '_execution', {}).get('records', [])
               if records is None else records)
    latest = {row['n']: row for row in records}
    expected = kb_plan.roadmap_for(root,home)['stages'][stage]['steps']
    errors = []
    if require_complete and set(latest) != {row['n'] for row in expected}:
        errors.append('阶段步骤不完整，不能完成知识库核验')
    cache = VerificationCache()
    results = {}
    for number, record in sorted(latest.items()):
        try:
            if record.get('receipt_ref'):
                from workflow_checks import load_record_receipt
                receipt = load_record_receipt(root, record)
            else:
                receipt = record.get('receipt') or record
        except (OSError, ValueError, KeyError, TypeError) as error:
            failure = checked(['步骤回执引用无效：' + str(error)])
            results[str(number)] = failure
            errors.extend(f'{stage}.{number}: {message}' for message in failure['errors'])
            continue
        if isinstance(receipt, str):
            receipt = read_object(kb_plan.inside(root, receipt))
        result = verify(root, home, stage, number, receipt, _cache=cache,
                        _allow_pending=True, _records=list(latest.values()))
        if 1 <= number <= len(expected) and (expected[number - 1].get('kb') or {}).get('observations'):
            try:
                from kb_observations import verify as verify_observations
                observation = verify_observations(root, home, stage, number, receipt)
                result['observations'] = observation
                result['errors'].extend(observation.get('errors', []))
                if result['errors']:
                    result['status'] = 'fail'
            except ImportError as error:
                result['errors'].append('观察核验器不可用：' + str(error))
                result['status'] = 'fail'
        results[str(number)] = result
        errors.extend(f'{stage}.{number}: {error}' for error in result['errors'])
    # A later gate must compare against the saved stage seal, never bless a
    # changed pending response by merely computing a replacement baseline.
    for row in expected_response_hashes or []:
        try:
            key = (kb_plan.inside(root, row['path']), row.get('selector'))
            if key not in cache.hashes or cache.hashes[key] != row['sha256']:
                errors.append('知识库阶段封存响应已变化：' + row['path'])
        except (ValueError, KeyError, TypeError) as error:
            errors.append('知识库阶段封存记录无效：' + str(error))
    return checked(errors, verification_status='verified_stage' if not errors else 'failed_stage',
                   steps=results, response_hashes=[{'path': path.relative_to(Path(root).resolve()).as_posix(),
                                                   'selector': selector, 'sha256': digest}
                                                  for (path, selector), digest in cache.hashes.items()])


def verify(root, home, stage, number, receipt=None, *, _cache=None, _allow_pending=False, _records=None):
    cache = _cache or VerificationCache()
    errors = []; refs = set(); knowledge_status = 'pending'
    try:
        plan = kb_plan.plan_step(root, home, stage, number)
        if plan.get('reuse_step'):
            prior=plan['reuse_step']
            if prior>=number: raise ValueError('知识库复用必须来自本轮更早步骤')
            records=(_records if _records is not None else kb_plan.load(Path(root)/'workflow_state.yaml').get(stage.lower()+'_execution',{}).get('records',[]))
            source=next((r for r in reversed(records) if r.get('n')==prior),None)
            if source is None: raise ValueError('缺本轮知识库来源步骤回执')
            if source.get('receipt_ref'):
                from workflow_checks import load_record_receipt
                source_receipt=load_record_receipt(root,source)
            else:
                source_receipt=source.get('receipt')
            if isinstance(source_receipt,str): source_receipt=read_object(kb_plan.inside(root,source_receipt))
            if not isinstance(source_receipt,dict): source_receipt=source
            captured=source.get('artifacts',{})
            proof=captured.get('kb_evidence') or {}
            current=kb_plan.inside(root,kb_plan.evidence_path(stage,prior,root))
            inline_source = isinstance(source_receipt.get('kb_evidence'), dict)
            if not inline_source and (not proof.get('path') or kb_plan.inside(root,proof['path'])!=current):
                raise ValueError('知识库来源证据与当前轮次不一致')
            for role,item in captured.items():
                if not isinstance(item,dict) or not item.get('sha256'):
                    if _allow_pending and inline_source: continue
                    raise ValueError('来源回执缺摘要：'+role)
                if cache.sha256(kb_plan.inside(root,item['path']))!=item['sha256']:
                    raise ValueError('知识库来源步骤产物已变化：'+role)
            checked_source=verify(root,home,stage,prior,source_receipt,_cache=cache,_allow_pending=_allow_pending,_records=records)
            if checked_source['status']!='pass': raise ValueError('本轮来源知识库证据未通过：'+'；'.join(checked_source['errors']))
            refs={(r['server'],r['card_id']) for r in checked_source['refs']}
            for report in reports(root,home,stage,number,receipt):
                errors.extend(report_errors(report,refs))
                top='\n'.join(report.read_text(encoding='utf8').splitlines()[:8])
                if any(n not in top for n in checked_source.get('fallback_notices', [])):
                    errors.append('复用规则卡缺顶部无手册回退说明')
                if checked_source['knowledge_status']=='unavailable':
                    source_plan=kb_plan.plan_step(root,home,stage,prior)
                    reason=_evidence(root,source_plan,source_receipt).get('reason','')
                    top='\n'.join(report.read_text(encoding='utf8').splitlines()[:8])
                    if not all(x in top for x in ('本步未能调用知识库',reason,'检测星空配置')): errors.append('复用断库缺顶部提示')
            return checked(errors,knowledge_status=checked_source['knowledge_status'],card_count=len(refs),refs=checked_source['refs'],
                           label=checked_source['label']+f'（本轮第{prior}步）',reuse_step=prior,
                           fallback_notices=checked_source.get('fallback_notices', []),
                           evidence_path=kb_plan.evidence_path(stage,prior,root))
        if not plan['calls']:
            return checked([], knowledge_status='none', card_count=0, refs=[], label='– 本步无库')
        path = kb_plan.inside(root, plan['evidence_path'])
        if not path.is_file() and not isinstance((receipt or {}).get('kb_evidence'),dict): raise ValueError('缺本步 kb_evidence.yaml：' + plan['evidence_path'])
        evidence = _evidence(root, plan, receipt)
        pending = _allow_pending and evidence.get('verification_status') == 'pending_stage'
        if evidence.get('step') != plan['step']: raise ValueError('知识库证据步骤不符')
        knowledge_status = evidence.get('status')
        if knowledge_status not in ('used', 'unavailable', 'skipped'): raise ValueError('知识库证据状态无效')
        if knowledge_status == 'skipped':
            if plan['required'] or not evidence.get('reason'): raise ValueError('必需知识库不能静默跳过')
            return checked([], knowledge_status='skipped', card_count=0, refs=[], label='⚠ 已说明未引用')
        calls = evidence.get('calls')
        if not isinstance(calls, list) or not calls: raise ValueError('缺真实知识库调用记录')
        expected = call_counter(plan['calls'])
        actual, additional_calls = comparable_calls(plan['calls'], calls)
        failures = []
        for row in calls:
            server, tool = row['server'], row['tool']
            if server not in kb_plan.LIBRARIES:
                raise ValueError('未知知识库服务：' + str(server))
            row = {**row, 'library': row.get('library') or kb_plan.LIBRARIES[server][1]}
            schema = kb_plan.schemas(home, server)[tool]
            kb_plan.validate_args(schema, row['args'])
            key = json.dumps({k: row[k] for k in ('server','library','tool','args')}, sort_keys=True, ensure_ascii=False)
            if key not in expected and row not in additional_calls: errors.append('调用与本步计划不符：' + tool)
            ts = datetime.fromisoformat(str(row.get('ts', '')))
            if ts.tzinfo is None: errors.append('调用时间缺时区：' + tool)
            update = plan.get('context_update')
            if tool == 'kb_diagnose' and update and ts.tzinfo is not None:
                if ts < datetime.fromisoformat(update['updated_at']):
                    errors.append('诊断调用早于本次计划上下文更新；须按新计划重新实际调用')
            if row.get('status') == 'error':
                if not row.get('error'): errors.append('工具失败未记录原因')
                failures.append(server)
                if row.get('card_ids'): errors.append('失败调用不能声称引用卡片')
                continue
            if row.get('status') != 'ok': errors.append('调用缺真实成功/失败状态'); continue
            raw = kb_plan.inside(root, row['response_path'])
            observed = cache.sha256(raw, row.get('response_selector'))
            if observed != row.get('response_sha256') and not (pending and not row.get('response_sha256')):
                errors.append('知识库原始响应已变化：' + tool)
            response = cache.response(raw, row.get('response_selector'))
            if response_failed(response): errors.append('错误 MCP 响应不能标记成功')
            returned = response_ids(response)
            for cid in row.get('card_ids', []):
                if not CARD_ID.fullmatch(str(cid)) or not cache.card_exists(home, server, cid): errors.append('卡号不存在于对应库：' + server + ' ' + str(cid))
                if cid not in returned: errors.append('引用卡未出现在真实响应：' + server + ' ' + str(cid))
                if pending and _full_card_ids(response)[cid] != 1: errors.append('原始回包缺唯一卡片正文：' + server + ' ' + str(cid))
                refs.add((server, cid))
            if returned and tool not in ('kb_status', 'kb_list_gaps') and not row.get('card_ids') and not row.get('no_use_reason'):
                errors.append('取到卡但未引用或说明不适用原因：' + tool)
        report_paths = reports(root, home, stage, number, receipt)
        gap_rows = kb_plan.fallback_gaps(root, evidence, cache=cache, allow_pending=pending)
        notices = list(dict.fromkeys(g['notice'] for g in gap_rows))
        if gap_rows:
            ledger = path.with_name('technical_gaps.yaml')
            if evidence.get('technical_gaps') != gap_rows and (not ledger.is_file() or read_object(ledger).get('gaps') != gap_rows):
                errors.append('规则卡回退缺隔离技术缺口记录；请先运行报告生成器')
            for report in report_paths:
                top = '\n'.join(report.read_text(encoding='utf-8-sig').splitlines()[:8])
                if any(notice not in top for notice in notices): errors.append('缺报告顶部无手册回退说明：'+report.name)
        if knowledge_status == 'used':
            if actual != expected: errors.append('未完整执行本步知识库计划')
            if failures: errors.append('存在失败调用，不能标记 used')
            if not refs: errors.append('used 缺实际引用卡号')
            for report in report_paths: errors.extend(report_errors(report, refs))
        else:
            reason = evidence.get('reason')
            if not reason or not failures: errors.append('unavailable 缺实际失败调用及原因')
            # A failed library may omit later calls; independent libraries must still be attempted.
            for key, count in expected.items():
                if json.loads(key)['server'] not in failures and actual[key] != count:
                    errors.append('降级不能跳过未失败的知识库计划：' + json.loads(key)['library'])
            for report in report_paths:
                top = '\n'.join(report.read_text(encoding='utf-8-sig').splitlines()[:8])
                if not all(x in top for x in ('本步未能调用知识库', str(reason), '检测星空配置')):
                    errors.append('降级缺报告顶部可见提示：' + report.name)
            # Successful partial calls may still be cited while the failed library remains visible.
            if refs:
                for report in report_paths: errors.extend(report_errors(report, refs))
        return checked(errors, knowledge_status=knowledge_status, card_count=len(refs), fallback_notices=notices,
                       additional_calls=additional_calls,
                       refs=[{'server': server, 'library': kb_plan.LIBRARIES[server][1], 'card_id': cid} for server,cid in sorted(refs)],
                       label=(f'✓ 已引用 {len(refs)} 张'+(' · 无手册，按场景规则卡给依据' if notices else '')) if not errors and knowledge_status=='used' else '⚠ 知识库未接通，已提示继续' if not errors else '⚠ 本步该调用但未调用')
    except (OSError, ValueError, KeyError, IndexError, TypeError, yaml.YAMLError) as error:
        return checked(errors + [str(error)], knowledge_status=knowledge_status, card_count=0, refs=[], label='⚠ 本步该调用但未调用')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True); p.add_argument('--home', default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--stage', required=True); p.add_argument('--step', type=int, required=True); p.add_argument('--receipt', required=True)
    a = p.parse_args(); result = verify(a.root, a.home, a.stage, a.step, read_object(a.receipt))
    print(json.dumps(result, ensure_ascii=False, indent=2)); return int(result['status'] != 'pass')


if __name__ == '__main__': raise SystemExit(main())
