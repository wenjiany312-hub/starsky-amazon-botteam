#!/usr/bin/env python3
"""S5 第二部分写回：把用户已批准的广告改动通过领星 OpenAPI 同步到亚马逊广告后台。

  python s5_lingxing_writeback.py --run "<产品根或运营账本>" --sid 9964            # 预演：只读，列出要改什么、后台现在是多少
  python s5_lingxing_writeback.py --run "<…>" --sid 9964 --execute                 # 真写：只写 04_审批.json 里批准的

能写：改竞价 / 暂停（关键词、商品投放）、出单词加进已有精准组、否定关键词（精准 / 词组）、否定 ASIN、改日预算、改广告位加价。
不能写：新建活动 / 广告组（领星没有接口）→ 仍走 s5_optimize.py bulk 出上传表。
规则：只写审批过的；写前核对后台当前值，和报告「改前」不一致就跳过；写后回读 + 查领星操作日志，写 06_写回回执.json。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


opt = module(HERE.parent / 's5_optimize.py', 's5_optimize_wb')
src = module(HERE.parent / 's5_lingxing_source.py', 's5_lingxing_source_wb')

BASE = {'Keyword': ('/pb/openapi/newad/spKeywords', 'keyword_id'), 'Product Targeting': ('/pb/openapi/newad/spTargets', 'target_id')}
PUT = {'Keyword': ('/basicOpen/adReport/manage/putSpKeyword', 'keywords', 'keywordId'),
       'Product Targeting': ('/basicOpen/adReport/manage/putSpTarget', 'targetingClauses', 'targetId')}
LOG_TYPE = {'bid:Keyword': 'keywords', 'bid:Product Targeting': 'targets', 'add_keyword': 'keywords',
            'add_negative_keyword': 'negativeKeywords', 'add_negative_asin': 'negativeTargets', 'campaign': 'campaigns'}
STRATEGY_API = {'manual': 'MANUAL', 'legacyForSales': 'LEGACY_FOR_SALES', 'autoForSales': 'AUTO_FOR_SALES'}
PLACEMENT_API = {'placementTop': 'PLACEMENT_TOP', 'placementProductPage': 'PLACEMENT_PRODUCT_PAGE',
                 'placementRestOfSearch': 'PLACEMENT_REST_OF_SEARCH', 'placementAmazonBusiness': 'SITE_AMAZON_BUSINESS'}


def now():
    return datetime.now(timezone.utc).astimezone()


def group_of(a):
    """动作 → 写回通道；None = 这里写不了（出上传表）。"""
    kind, entity = a['kind'], a.get('entity')
    if kind in ('bid_down', 'bid_up', 'pause') and entity in BASE:
        return f'bid:{entity}'
    if kind == 'harvest_exact' and (a.get('destination') or {}).get('mode') == 'existing':
        return 'add_keyword'
    if kind in ('negative_exact', 'negative_phrase_ct'):
        return 'add_negative_keyword'
    if kind == 'negative_asin':
        return 'add_negative_asin'
    if kind in ('budget_up', 'budget_down', 'placement_up', 'placement_down'):
        return 'campaign'
    return None


def live_targets(lx, cfg, token, sid, entity, ids):
    path, key = BASE[entity]
    found = {}
    for state in ('enabled', 'paused'):
        for r in src.fetch_all(lx, cfg, token, path, {'sid': sid, 'state': state}, lambda r: str(r.get(key)) in ids):
            found[str(r[key])] = {'bid': opt.num(r.get('bid')), 'state': r.get('state')}
        if len(found) == len(ids):
            break
    return found


def live_campaigns(lx, cfg, token, sid, ids):
    found = {}
    for c in src.fetch_all(lx, cfg, token, '/pb/openapi/newad/spCampaigns', {'sid': sid}, lambda r: str(r.get('campaign_id')) in ids):
        try:
            bidding = json.loads(c.get('bidding') or '{}')
        except (TypeError, ValueError):
            bidding = {}
        found[str(c['campaign_id'])] = {'daily_budget': opt.num(c.get('daily_budget')), 'state': c.get('state'),
                                        'strategy': bidding.get('strategy'),
                                        'placements': {x.get('predicate'): opt.num(x.get('percentage')) for x in bidding.get('adjustments') or []}}
    return found


def plan_rows(lx, cfg, token, sid, actions):
    """按通道分组、核对后台现值，返回 (可写行, 跳过)。"""
    rows, skipped = [], []
    by_group = defaultdict(list)
    for a in actions:
        by_group[group_of(a)].append(a)
    for entity in BASE:
        group = by_group.get(f'bid:{entity}', [])
        if not group:
            continue
        ids = {str(a.get('keyword_id') or a.get('product_targeting_id')) for a in group}
        live = live_targets(lx, cfg, token, sid, entity, ids)
        for a in group:
            tid = str(a.get('keyword_id') or a.get('product_targeting_id'))
            now_val = live.get(tid)
            if not now_val:
                skipped.append({'id': a['id'], 'reason': '后台找不到（已归档或 ID 不对）'})
            elif abs((now_val['bid'] or 0) - (a['current'].get('bid') or 0)) > 0.005 or now_val['state'] != 'enabled':
                skipped.append({'id': a['id'], 'reason': f"后台当前值已变（报告改前 ${a['current'].get('bid')}，后台现在 ${now_val['bid']} / {now_val['state']}）"})
            else:
                rows.append({'id': a['id'], 'group': f'bid:{entity}', 'entity': entity, 'object': opt.display_name(a['object']),
                             'campaign': a['campaign_name'], 'target_id': tid, 'before': now_val,
                             'after': {'bid': a['proposed']['bid'], 'state': a['proposed'].get('state', 'enabled')}})
    for a in by_group.get('add_keyword', []):
        dest = a['destination']
        rows.append({'id': a['id'], 'group': 'add_keyword', 'object': a['object'], 'campaign': dest['campaign_name'],
                     'before': None, 'after': {'campaignId': dest['campaign_id'], 'adGroupId': dest['ad_group_id'], 'keyword': a['object'],
                                               'matchType': 'EXACT', 'state': 'ENABLED', 'bid': a['proposed']['bid']}})
    for a in by_group.get('add_negative_keyword', []):
        neg = {'campaignId': a['campaign_id'], 'keyword': a['object'],
               'matchType': 'negativePhrase' if a['kind'] == 'negative_phrase_ct' else 'negativeExact', 'state': 'ENABLED'}
        if a.get('ad_group_id'):
            neg['adGroupId'] = a['ad_group_id']
        rows.append({'id': a['id'], 'group': 'add_negative_keyword', 'object': a['object'], 'campaign': a['campaign_name'],
                     'before': None, 'after': neg})
    for a in by_group.get('add_negative_asin', []):
        asin = (re.search(r'b0[a-z0-9]{8}', str(a['object']), re.I) or [''])[0].upper()
        if not asin or not a.get('ad_group_id'):
            skipped.append({'id': a['id'], 'reason': '缺 ASIN 或广告组 ID'})
            continue
        rows.append({'id': a['id'], 'group': 'add_negative_asin', 'object': asin, 'campaign': a['campaign_name'], 'before': None,
                     'after': {'campaignId': a['campaign_id'], 'adGroupId': a['ad_group_id'], 'asin': asin, 'state': 'ENABLED'}})
    camp = by_group.get('campaign', [])
    if camp:
        live = live_campaigns(lx, cfg, token, sid, {a['campaign_id'] for a in camp})
        for cid, acts in _by_campaign(camp).items():
            now_val = live.get(cid)
            if not now_val:
                skipped += [{'id': a['id'], 'reason': '后台找不到这个活动'} for a in acts]
                continue
            after = {'daily_budget': now_val['daily_budget'], 'placements': dict(now_val['placements'])}
            ok = []
            for a in acts:
                if a['kind'].startswith('budget'):
                    if abs((now_val['daily_budget'] or 0) - a['current']['daily_budget']) > 0.005:
                        skipped.append({'id': a['id'], 'reason': f"日预算已变（报告 ${a['current']['daily_budget']}，后台 ${now_val['daily_budget']}）"})
                        continue
                    after['daily_budget'] = a['proposed']['daily_budget']
                else:
                    have = now_val['placements'].get(a['placement'], 0.0) or 0.0
                    if abs(have - a['current']['percentage']) > 0.5:
                        skipped.append({'id': a['id'], 'reason': f"广告位加价已变（报告 {a['current']['percentage']}%，后台 {have}%）"})
                        continue
                    after['placements'][a['placement']] = a['proposed']['percentage']
                ok.append(a['id'])
            if ok:
                rows.append({'id': ','.join(ok), 'ids': ok, 'group': 'campaign', 'object': acts[0]['campaign_name'],
                             'campaign': acts[0]['campaign_name'], 'target_id': cid, 'strategy': now_val['strategy'],
                             'budget_changed': any(i in ok for i in (a['id'] for a in acts if a['kind'].startswith('budget'))),
                             'placement_changed': any(i in ok for i in (a['id'] for a in acts if a['kind'].startswith('placement'))),
                             'before': {'daily_budget': now_val['daily_budget'], 'placements': now_val['placements']}, 'after': after})
    return rows, skipped


def _by_campaign(actions):
    out = defaultdict(list)
    for a in actions:
        out[a['campaign_id']].append(a)
    return out


def execute(lx, cfg, token, sid, rows, folder, stamp):
    """按通道批量提交；原始返回先落盘。返回 {行 id: (成功?, 说明)}。"""
    outcome = {}

    def call(tag, path, body):
        resp = lx.call(cfg, token, path, body)
        opt.dump_json(folder / f'06_写回原始返回_{tag}_{stamp}.json', {'request': body, 'response': resp})
        return resp

    for entity, (path, field, id_key) in PUT.items():
        batch = [r for r in rows if r['group'] == f'bid:{entity}']
        if batch:
            resp = call(entity.replace(' ', ''), path, {'sid': sid, field: [
                {id_key: int(r['target_id']), 'bid': r['after']['bid'], 'state': r['after']['state'], 'isBaseValue': 0} for r in batch]})
            api = {str(x.get(id_key) or x.get('targetId') or x.get('keywordId')): x for x in ((resp.get('data') or {}).get('apiResult') or [])}
            for r in batch:
                res = api.get(r['target_id']) or {}
                outcome[r['id']] = (res.get('code') == 'SUCCESS', res.get('description') or resp.get('msg') or resp.get('message'))
    for group, path, field in (('add_keyword', '/basicOpen/adReport/spTarget/addKeywords', 'keywords'),
                               ('add_negative_keyword', '/basicOpen/adReport/spTarget/addNegativeKeywords', 'negativeKeywords'),
                               ('add_negative_asin', '/basicOpen/adReport/spTarget/addNegativeTargets', 'asins')):
        batch = [r for r in rows if r['group'] == group]
        if not batch:
            continue
        resp = call(group, path, {'sid': sid, field: [r['after'] for r in batch]})
        data = resp.get('data') or {}
        good = {x.get('index'): x.get('target') for x in data.get('success') or []}
        bad = {x.get('index'): '；'.join(e.get('message', '') for e in x.get('errors') or []) for x in data.get('error') or []}
        for i, r in enumerate(batch):
            if i in good:
                r['new_id'] = (good[i] or {}).get('targetId')
            outcome[r['id']] = (i in good, bad.get(i) or (None if i in good else (resp.get('msg') or resp.get('message'))))
    batch = [r for r in rows if r['group'] == 'campaign']
    if batch:
        clauses = []
        for r in batch:
            clause = {'campaignId': int(r['target_id']), 'isBaseValue': 0}
            if r['budget_changed']:
                clause['budget'] = {'budgetType': 'daily', 'budget': r['after']['daily_budget']}
            if r['placement_changed']:
                # 三个广告位一起提交：没改的保持后台现值，避免被清零
                clause['dynamicBidding'] = {'strategy': STRATEGY_API.get(r['strategy'], 'LEGACY_FOR_SALES'),
                                            'placementBidding': [{'placement': PLACEMENT_API[k], 'percentage': v}
                                                                 for k, v in r['after']['placements'].items() if k in PLACEMENT_API]}
            clauses.append(clause)
        resp = call('campaign', '/basicOpen/adReport/manage/putSpCampaign', {'sid': sid, 'campaigns': clauses})
        api = {str(x.get('campaignId')): x for x in ((resp.get('data') or {}).get('apiResult') or [])}
        for r in batch:
            res = api.get(r['target_id']) or {}
            outcome[r['id']] = (res.get('code') == 'SUCCESS', res.get('description') or resp.get('msg') or resp.get('message'))
    return outcome


def verify(lx, cfg, token, sid, rows, started):
    for entity in BASE:
        batch = [r for r in rows if r['group'] == f'bid:{entity}']
        if batch:
            live = live_targets(lx, cfg, token, sid, entity, {r['target_id'] for r in batch})
            for r in batch:
                got = live.get(r['target_id']) or {}
                r['readback'] = got
                r['verified'] = got.get('bid') is not None and abs(got['bid'] - r['after']['bid']) <= 0.005 and got.get('state') == r['after']['state']
    batch = [r for r in rows if r['group'] == 'campaign']
    if batch:
        live = live_campaigns(lx, cfg, token, sid, {r['target_id'] for r in batch})
        for r in batch:
            got = live.get(r['target_id']) or {}
            r['readback'] = got
            r['verified'] = bool(got) and abs((got.get('daily_budget') or 0) - r['after']['daily_budget']) <= 0.005 and all(
                abs((got.get('placements') or {}).get(k, 0) - v) <= 0.5 for k, v in r['after']['placements'].items())
    for r in rows:
        if r['group'].startswith('add_'):
            r['verified'] = bool(r.get('new_id'))
    start = (started - timedelta(days=1)).date().isoformat()
    for group, op_type in LOG_TYPE.items():
        batch = [r for r in rows if r['group'] == group]
        if not batch:
            continue
        try:  # 日志接口：开始结束不能同一天；每页最多 15 条
            resp = lx.call(cfg, token, '/pb/openapi/newad/apiLogStandard',
                           {'sid': sid, 'log_source': 'all', 'sponsored_type': 'sp', 'operate_type': op_type,
                            'start_date': start, 'end_date': now().date().isoformat(), 'offset': 0, 'length': 15})
            log = resp.get('data') or []
        except Exception as exc:  # noqa: BLE001 — 核对失败不影响回执
            log = []
            print(json.dumps({'log_error': str(exc)[:200]}, ensure_ascii=False))
        for r in batch:
            keys = [k for k in (r.get('target_id'), r.get('new_id'), r.get('object')) if k]
            r['log_entries'] = [e for e in log if any(str(k) in json.dumps(e, ensure_ascii=False) for k in keys)][:3]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True)
    ap.add_argument('--sid', required=True, type=int)
    ap.add_argument('--execute', action='store_true', help='真写；不加只预演')
    args = ap.parse_args(argv)
    folder = opt.current_round(Path(args.run))
    plan = opt.load(folder / '02_诊断方案.json')
    decision = opt.load(folder / '04_审批.json')
    status = opt.load(folder / '03_report_status.json')
    if not decision:
        print(json.dumps({'status': 'blocked', 'error': '还没审批：用户同意后先 s5_optimize.py approve'}, ensure_ascii=False))
        return 2
    page = folder / '03_优化前后对比.html'
    if decision.get('report_page_sha256') != status.get('page_sha256') or status.get('page_sha256') != hashlib.sha256(page.read_bytes()).hexdigest():
        print(json.dumps({'status': 'blocked', 'error': '审批后报告又变了：重新给用户看报告并重新 approve'}, ensure_ascii=False))
        return 2
    if decision.get('plan_sha256') != hashlib.sha256((folder / '02_诊断方案.json').read_bytes()).hexdigest():
        print(json.dumps({'status': 'blocked', 'error': '审批后方案又变了或审批没记方案指纹：重新出报告、重新 approve 再写回'}, ensure_ascii=False))
        return 2
    chosen = [a for a in plan['actions'] if a['id'] in decision['approved_actions']]
    done = {i for r in (opt.load(folder / '06_写回回执.json').get('results') or [])
            if r.get('ok') or r.get('api_code') == 'SUCCESS' for i in (r.get('ids') or [r['id']])}
    chosen = [a for a in chosen if a['id'] not in done]  # 已写成功的不重复写
    unsupported = [a['id'] for a in chosen if group_of(a) is None]
    lx = src.client()
    cfg = lx.load_config()
    token = lx.get_token(cfg)
    rows, skipped = plan_rows(lx, cfg, token, args.sid, [a for a in chosen if group_of(a)])
    if not args.execute:
        print(json.dumps({'status': 'preview', 'write': rows, 'skipped': skipped, 'already_done': sorted(done),
                          'upload_file_needed': unsupported}, ensure_ascii=False, indent=1))
        return 0
    started = now()
    outcome = execute(lx, cfg, token, args.sid, rows, folder, f'{started:%H%M%S}')
    time.sleep(8)
    token = lx.get_token(cfg)
    for r in rows:
        r['ok'], r['api_error'] = outcome.get(r['id'], (False, '没有返回'))
    verify(lx, cfg, token, args.sid, [r for r in rows if r['ok']], started)
    previous = opt.load(folder / '06_写回回执.json')
    receipt = {'artifact_type': 's5_lingxing_writeback', 'generator': 's5_lingxing_writeback.py', 'at': started.isoformat(),
               'sid': args.sid, 'round': plan['round'], 'approval': str(folder / '04_审批.json'),
               'results': (previous.get('results') or []) + rows, 'skipped': skipped, 'upload_file_needed': unsupported}
    receipt['summary'] = {'written': sum(1 for r in rows if r['ok']), 'failed': sum(1 for r in rows if not r['ok']),
                          'verified': sum(1 for r in rows if r.get('verified')), 'with_log': sum(1 for r in rows if r.get('log_entries')),
                          'skipped': len(skipped), 'upload_file_needed': len(unsupported)}
    opt.dump_json(folder / '06_写回回执.json', receipt)
    print(json.dumps({'status': 'done', **receipt['summary'], 'receipt': str(folder / '06_写回回执.json'),
                      'results': [{k: r.get(k) for k in ('id', 'group', 'object', 'before', 'after', 'ok', 'api_error', 'readback', 'verified')}
                                  for r in rows], 'skipped': skipped, 'upload_file_needed': unsupported}, ensure_ascii=False, indent=1))
    return 0 if all(r['ok'] for r in rows) else 2


if __name__ == '__main__':
    sys.exit(main())
