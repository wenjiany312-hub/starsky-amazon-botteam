"""Ads API route CLI: authorization, SP collection, local review. No campaign writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

from ads_api_client import AdsApiError, AdsClient, authorize, default_config_path, load_config
from ads_api_reports import collect, encoded, read_json, sha, verify_artifacts, write_json


def _card(path):
    try:
        import yaml
        value = yaml.safe_load(Path(path).read_text(encoding='utf8'))
        if not isinstance(value, dict): raise ValueError()
        return value
    except Exception: raise AdsApiError('execution_card_invalid') from None


def analyze(input_path, card_path, output):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    payload = read_json(input_path)
    manifest = read_json(input_path.parent/'manifest.json')
    verify_artifacts(input_path.parent, manifest,
                     required=('entities','campaign_adgroups','keywords','targets','search_terms','review_input'))
    expected = manifest.get('files', {}).get('review_input', {})
    if (expected.get('path') != input_path.name or sha(input_path.read_bytes()) != expected.get('sha256')
            or manifest.get('state') not in ('complete','data_gap')):
        raise AdsApiError('analysis_evidence_invalid')
    context = payload.get('source_context') or {}
    card_doc = _card(card_path)
    card = card_doc.get('review_card', card_doc)
    if not isinstance(card, dict): raise AdsApiError('execution_card_invalid')
    for key in ('profile_id','region','currency','attribution_days','start_date','end_date'):
        if card.get(key) is not None and str(card[key]) != str(context.get(key)):
            raise AdsApiError('card_scope_mismatch')
    payload['card'] = card
    payload['pending_actions'] = card_doc.get('pending_actions', [])
    script_dir = Path(__file__).resolve().parents[2]/'_s5_ops_contract/scripts'
    sys.path.insert(0,str(script_dir))
    try:
        from review_loop import review
        result = review(payload)
    finally:
        sys.path.remove(str(script_dir))
    result['input_sha256'] = expected['sha256']
    result['execution_card_sha256'] = sha(Path(card_path).read_bytes())
    result['collection_state'] = manifest['state']
    result['next'] = '审核建议；确需执行时确认具体对象与数值，再生成 Bulk 候选。'
    output.mkdir(parents=True, exist_ok=True)
    # Each run of analysis is kept, so a prior review is never silently overwritten.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = output / ('review_'+stamp+'.json')
    write_json(target, result)
    page = target.with_suffix('.html')
    page.write_text(render_report(result), encoding='utf8')
    return {'state':'review_ready' if manifest['state']=='complete' else 'review_has_data_gaps',
            'json':str(target),'html':str(page),'branches':result['branches'],
            'execution_status':'not_submitted','approval_status':'pending approval'}


def render_report(result):
    esc = lambda value: html.escape(str(value if value is not None else '未知'), quote=True)
    ctx = result.get('source_context') or {}
    counts = result.get('branches') or {}
    rows = []
    for action in result.get('ad_actions') or []:
        evidence = ', '.join(str(e.get('file', '')) for e in action.get('evidence', []) if isinstance(e,dict))
        rows.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (
            action.get('layer'), action.get('object'), action.get('native_id'),
            action.get('spend'), action.get('sales'),
            (f"{action['acos']:.1%}" if action.get('acos') is not None else None),
            action.get('verdict'), json.dumps(action.get('proposed'),ensure_ascii=False),
            action.get('basis'), evidence))+'</tr>')
    issues = result.get('data_quality', {}).get('issues', [])
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SP 广告复盘建议</title><style>body{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f4f6fa;color:#192435;margin:0;padding:28px}main{max-width:1400px;margin:auto}h1{margin:0}p{max-width:1000px}.banner{background:#fff4dc;border:1px solid #e5c27c;border-radius:10px;padding:15px}.meta{color:#475569}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:13px}td,th{padding:10px;border:1px solid #dbe1ea;text-align:left;min-width:70px}th{background:#e8effa}code{overflow-wrap:anywhere}</style><main><h1>SP 广告复盘建议</h1>
<p class="banner">等待人工审核 · 尚未提交广告修改。仅覆盖所选账号与范围的 SP 数据；SB、SD 未覆盖。销售归因近期仍可能补记。</p>'''+(
        '<p class="meta">数据源：'+esc(ctx.get('source'))+' · '+esc(ctx.get('region'))+' / profile '+esc(ctx.get('profile_id'))+
        ' · '+esc(ctx.get('currency'))+' · '+esc(ctx.get('timezone'))+'<br>报表日期：'+esc(ctx.get('start_date'))+' 至 '+esc(ctx.get('end_date'))+
        '（含首尾） · 归因 '+esc(ctx.get('attribution_days'))+' 天 · 收集时间 '+esc(ctx.get('collected_at'))+'</p>'+
        '<p>建议调整 '+esc(counts.get('change',0))+' · 观察 '+esc(counts.get('watch',0))+' · 止损待审 '+esc(counts.get('stop_loss',0))+
        ' · 缺数据 '+esc(counts.get('needs_data',0))+'。各层级是同批广告的不同视图，花费不能跨层相加。</p>'+
        '<p>数据缺口：'+esc('; '.join(issues) if issues else '本批字段和采集完整性检查未发现缺口')+'</p>'+
        '<div class="scroll"><table><thead><tr>'+''.join('<th>'+label+'</th>' for label in ['层级','对象','原生 ID','花费','销售额','ACOS','判断','建议值','依据','来源文件'])+
        '</tr></thead><tbody>'+(''.join(rows) or '<tr><td colspan="10">本范围没有报告行；没有生成调整建议。</td></tr>')+'</tbody></table></div>'+
        '<p>输入校验：<code>'+esc(result.get('input_sha256'))+'</code><br>执行卡：<code>'+esc(result.get('execution_card_sha256'))+'</code></p></main></html>')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,default=None)
    sub = ap.add_subparsers(dest='command',required=True)
    auth = sub.add_parser('authorize', help='在本机浏览器完成 LWA 授权')
    auth.add_argument('--timeout-seconds',type=int,default=180)
    sub.add_parser('profiles', help='读取有权限的广告账号；不自动选首项')
    col = sub.add_parser('collect', help='拉取 SP 报表；重复同一命令续拉待完成任务')
    col.add_argument('--profile-id')
    col.add_argument('--start',required=True)
    col.add_argument('--end',required=True)
    col.add_argument('--attribution-days',type=int,choices=[7,14],required=True)
    scope = col.add_mutually_exclusive_group(required=True)
    scope.add_argument('--all-campaigns',action='store_true')
    scope.add_argument('--campaign-id',action='append')
    col.add_argument('--out',type=Path,required=True)
    col.add_argument('--wait-seconds',type=int,default=0)
    analysis = sub.add_parser('analyze',help='将已完成报表与执行卡送入 S5 建议流程')
    analysis.add_argument('--input',required=True,type=Path)
    analysis.add_argument('--card',required=True,type=Path)
    analysis.add_argument('--out',required=True,type=Path)
    args = ap.parse_args(argv)
    try:
        if args.command == 'analyze':
            result = analyze(args.input,args.card,args.out)
        else:
            config = load_config(args.config or default_config_path())
            if args.command == 'authorize':
                result = authorize(config,timeout_seconds=args.timeout_seconds)
            else:
                client = AdsClient(config)
                if args.command == 'profiles':
                    profiles = client.profiles()
                    result = {'state':'profiles_verified' if profiles else 'no_profiles','region':config['region'],
                              'profiles':[{k:p.get(k) for k in ('profileId','countryCode','currencyCode','timezone')}
                                          for p in profiles],
                              'next':'指定 profile_id 后拉报表；账号读取成功不等于报表数据已取得。'}
                else:
                    params = {'profile_id':args.profile_id or config.get('profile_id'), 'start_date':args.start,
                              'end_date':args.end,'attribution_days':args.attribution_days,
                              'campaign_ids':args.campaign_id,'all_campaigns':args.all_campaigns}
                    result = collect(client,params,args.out,args.wait_seconds)
        print(encoded(result).decode('utf8'),end='')
        return 2 if result.get('state') in ('pending','data_gap','failed','review_has_data_gaps','no_profiles') else 0
    except AdsApiError as error:
        print(json.dumps({'state':'blocked','error':error.code,'http_status':error.status},ensure_ascii=False))
        return 2
    except Exception:
        # Neither tracebacks nor third-party exception strings may reveal credentials.
        print(json.dumps({'state':'blocked','error':'local_processing_failed'},ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
