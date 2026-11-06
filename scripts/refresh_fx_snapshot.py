#!/usr/bin/env python3
"""USD/CNY 汇率快照：生成、刷新、读取（契约 templates/fx_rate_snapshot.schema.yaml，政策 report_depth_and_demo_policy.md §3.4）。

  <python> <plugin>/scripts/refresh_fx_snapshot.py --run "<产品根>"                    保证快照存在且在 24 小时内
  <python> <plugin>/scripts/refresh_fx_snapshot.py --run "<产品根>" --force            无论新旧都重新取
  <python> <plugin>/scripts/refresh_fx_snapshot.py --run "<产品根>" --rate 6.78        接口取不到时，由用户亲自给出参考汇率
  <python> <plugin>/scripts/refresh_fx_snapshot.py --run "<产品根>" --settlement-rate 6.71
                                                                                  记录银行/支付实际结汇价（优先级最高）

退出码：0 = 快照可用（新取到，或仍在有效期）
        2 = 刷新失败，沿用了旧快照（已写入 refresh_failures 并提示过期小时数，不会静默沿用）
        1 = 没有任何可用快照（不写文件，不编造汇率）

汇率不是可以拍脑袋的演示参数：取不到就如实报告，不退回写死的约整数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

SNAPSHOT = 'fx_rate_snapshot.yaml'
SOURCE_NAME = 'ExchangeRate-API open endpoint'
SOURCE_URL = 'https://open.er-api.com/v6/latest/USD'
DEFAULT_HAIRCUT = 0.015
MAX_AGE_HOURS = 24
SANE_RANGE = (3.0, 12.0)  # 1 美元兑人民币；超出这个范围视为接口异常，不写入
FORMULA = 'effective_purchase_rate = reference_or_settlement_rate * (1 - rate_haircut_pct)'


class FxError(ValueError):
    """取数或校验失败；消息会原样写进 refresh_failures。"""


# ───────────────────────── 读取端（成本模型、报告、验收共用的约定） ─────────────────────────
def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def buffer_rate(snapshot):
    """含缓冲的有效采购汇率：schema 的嵌套位置优先，兼容旧版写在顶层的写法。"""
    snapshot = snapshot or {}
    return _number((snapshot.get('fx_buffer') or {}).get('effective_purchase_rate')) \
        or _number(snapshot.get('effective_purchase_rate'))


def settlement_rate(snapshot):
    return _number(((snapshot or {}).get('settlement_override') or {}).get('actual_settlement_rate'))


def effective_rate(snapshot):
    """成本测算用哪个汇率：实际结汇价 > 含缓冲有效汇率 > 旧版顶层字段。返回 (汇率, 依据) 或 (None, None)。"""
    actual = settlement_rate(snapshot)
    if actual:
        return actual, 'settlement_override.actual_settlement_rate'
    buffered = _number(((snapshot or {}).get('fx_buffer') or {}).get('effective_purchase_rate'))
    if buffered:
        return buffered, 'fx_buffer.effective_purchase_rate'
    legacy = _number((snapshot or {}).get('effective_purchase_rate'))
    if legacy:
        return legacy, 'effective_purchase_rate'
    return None, None


def parse_time(text):
    """解析快照里的时间；样例里有 7 位小数秒，统一截到 6 位。"""
    if not text:
        return None
    value = re.sub(r'(\.\d{6})\d+', r'\1', str(text).strip().replace('Z', '+00:00'))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_hours(snapshot, now=None):
    retrieved = parse_time((snapshot or {}).get('retrieved_at_utc'))
    if retrieved is None:
        return None
    return ((now or datetime.now(timezone.utc)) - retrieved).total_seconds() / 3600


def annotation(snapshot):
    """报告里每处换算必须带的口径标注（政策 §3.4 第 3 条）。"""
    retrieved = parse_time((snapshot or {}).get('retrieved_at_utc'))
    day = retrieved.date().isoformat() if retrieved else '日期未知'
    haircut = ((snapshot or {}).get('fx_buffer') or {}).get('rate_haircut_pct')
    note = f'，含 {haircut * 100:g}% 缓冲' if isinstance(haircut, (int, float)) else ''
    return f'USD/CNY effective_purchase_rate，快照 {day}{note}'


def load_snapshot(path):
    path = Path(path)
    if not path.is_file():
        return None
    document = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    return document if isinstance(document, dict) else None


# ───────────────────────── 取数与生成 ─────────────────────────
def fetch_open_endpoint(timeout=15):
    request = Request(SOURCE_URL, headers={'User-Agent': 'starsky-fx-snapshot/1.0', 'Accept': 'application/json'})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定的 https 地址
        return json.loads(response.read(1_000_000).decode('utf-8'))


def parse_response(raw):
    """校验接口返回；任何不对劲都当失败，宁可不写也不写错。"""
    if not isinstance(raw, dict) or raw.get('result') != 'success':
        raise FxError('接口返回不是 success')
    if raw.get('base_code') != 'USD':
        raise FxError('接口基准币不是 USD')
    rate = _number((raw.get('rates') or {}).get('CNY'))
    if rate is None:
        raise FxError('接口里没有可用的 CNY 汇率')
    if not SANE_RANGE[0] <= rate <= SANE_RANGE[1]:
        raise FxError(f'CNY 汇率 {rate} 超出合理范围 {SANE_RANGE}，疑似接口异常')
    return {'rate': rate, 'as_of': raw.get('time_last_update_utc'), 'next': raw.get('time_next_update_utc')}


def _stamp(now):
    return now.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def build_snapshot(rate, *, now, haircut=DEFAULT_HAIRCUT, source_name=SOURCE_NAME, source_url=SOURCE_URL,
                   as_of=None, next_update=None, quote_status='daily_reference', previous=None):
    if not 0 <= haircut < 0.5:
        raise FxError('缓冲比例应在 0 到 0.5 之间')
    kept = (previous or {}).get('settlement_override')
    if not isinstance(kept, dict):
        kept = {'actual_settlement_rate': None, 'status': 'not_received'}
    return {
        'schema_version': '1.0',
        'artifact_type': 'fx_rate_snapshot',
        'pair': 'USD/CNY',
        'quote_convention': 'CNY per 1 USD',
        'quote_status': quote_status,
        'reference_rate': rate,
        'source_name': source_name,
        'source_url': source_url,
        'source_as_of_utc': as_of,
        'next_source_update_utc': next_update,
        'retrieved_at_utc': _stamp(now),
        'live_quote_provider': 'not_configured_or_fallback',
        'settlement_override': kept,
        'fx_buffer': {'rate_haircut_pct': haircut, 'effective_purchase_rate': round(rate * (1 - haircut), 9), 'formula': FORMULA},
        'refresh_failures': [],
        'allowed_use': 'internal_simulation_only',
    }


def write_snapshot(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.fx-', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as stream:
            yaml.safe_dump(document, stream, allow_unicode=True, sort_keys=False, width=120)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def refresh(run, *, fetch=fetch_open_endpoint, now=None, haircut=DEFAULT_HAIRCUT, max_age_hours=MAX_AGE_HOURS,
            force=False, manual_rate=None, settlement=None):
    """保证 <run>/fx_rate_snapshot.yaml 可用。返回 {'status','action','path','message', ...}，不抛业务异常。

    status: 'ok'（新取或仍有效）| 'stale'（刷新失败，沿用旧快照）| 'unavailable'（没有可用快照）
    """
    now = now or datetime.now(timezone.utc)
    path = Path(run) / SNAPSHOT
    previous = load_snapshot(path)
    result = {'path': str(path)}

    if settlement is not None:
        actual = _number(settlement)
        if actual is None or not SANE_RANGE[0] <= actual <= SANE_RANGE[1]:
            return {**result, 'status': 'unavailable', 'action': 'rejected', 'message': f'实际结汇价 {settlement} 不在合理范围 {SANE_RANGE}'}
        if previous is None or not _number(previous.get('reference_rate')):
            return {**result, 'status': 'unavailable', 'action': 'rejected', 'message': '还没有快照，请先刷新出参考汇率，再记录实际结汇价'}
        previous['settlement_override'] = {'actual_settlement_rate': actual, 'status': 'received'}
        write_snapshot(path, previous)
        return {**result, 'status': 'ok', 'action': 'settlement_recorded', 'message': f'已记录实际结汇价 {actual}（成本测算优先使用）'}

    if manual_rate is not None:
        rate = _number(manual_rate)
        if rate is None or not SANE_RANGE[0] <= rate <= SANE_RANGE[1]:
            return {**result, 'status': 'unavailable', 'action': 'rejected', 'message': f'手填汇率 {manual_rate} 不在合理范围 {SANE_RANGE}'}
        document = build_snapshot(rate, now=now, haircut=haircut, source_name='用户手填参考汇率', source_url=None,
                                  as_of=_stamp(now), quote_status='user_supplied', previous=previous)
        write_snapshot(path, document)
        return {**result, 'status': 'ok', 'action': 'manual', 'message': f'已按你给的参考汇率 {rate} 生成快照（来源标注为用户手填）'}

    age = age_hours(previous, now) if previous else None
    if previous and not force and age is not None and age < max_age_hours and buffer_rate(previous):
        return {**result, 'status': 'ok', 'action': 'kept', 'age_hours': round(age, 2),
                'message': f'快照仍在有效期内（{age:.1f} 小时前取得），未重新取数'}

    try:
        parsed = parse_response(fetch())
    except Exception as error:  # 网络、解析、校验失败一律走同一条诚实路径
        failure = {'at_utc': _stamp(now), 'error': f'{type(error).__name__}: {error}'}
        if previous and buffer_rate(previous):
            failure['stale_hours'] = round(age, 1) if age is not None else None
            failures = [item for item in (previous.get('refresh_failures') or []) if isinstance(item, dict)][-4:] + [failure]
            previous['refresh_failures'] = failures
            write_snapshot(path, previous)
            hours = f'{age:.0f}' if age is not None else '未知'
            return {**result, 'status': 'stale', 'action': 'reused_stale', 'age_hours': age, 'failure': failure,
                    'message': f'刷新失败（{failure["error"]}）；仍在使用 {hours} 小时前的快照，报告里必须如实标注'}
        return {**result, 'status': 'unavailable', 'action': 'none', 'failure': failure,
                'message': f'刷新失败（{failure["error"]}），且没有可用的旧快照；不会编造汇率。'
                           f'可用 --rate <1美元兑人民币的参考汇率> 亲自提供，来源会标注为用户手填'}

    document = build_snapshot(parsed['rate'], now=now, haircut=haircut, as_of=parsed['as_of'], next_update=parsed['next'], previous=previous)
    write_snapshot(path, document)
    effective = document['fx_buffer']['effective_purchase_rate']
    return {**result, 'status': 'ok', 'action': 'fetched', 'reference_rate': parsed['rate'], 'effective_purchase_rate': effective,
            'message': f'已取得 USD/CNY 参考汇率 {parsed["rate"]}，含 {haircut * 100:g}% 缓冲的有效采购汇率 {effective}'}


EXIT_CODES = {'ok': 0, 'stale': 2, 'unavailable': 1}


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成或刷新 USD/CNY 汇率快照（fx_rate_snapshot.yaml）。')
    parser.add_argument('--run', type=Path, help='产品根目录；快照写在 <产品根>/fx_rate_snapshot.yaml')
    parser.add_argument('--output', type=Path, help='直接指定快照文件路径（不用 --run 时）')
    parser.add_argument('--force', action='store_true', help='即使快照仍在有效期也重新取')
    parser.add_argument('--rate', type=float, help='用户亲自给出的 USD/CNY 参考汇率（接口取不到时用）')
    parser.add_argument('--settlement-rate', type=float, help='记录银行/支付实际结汇价')
    parser.add_argument('--haircut-pct', type=float, default=DEFAULT_HAIRCUT, help='缓冲比例，默认 0.015 即 1.5%%')
    parser.add_argument('--max-age-hours', type=float, default=MAX_AGE_HOURS)
    parser.add_argument('--response-file', type=Path, help='离线回放：用文件里的接口 JSON 代替联网（测试与断网环境用）')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出结果')
    args = parser.parse_args(argv)
    if not args.run and not args.output:
        parser.error('需要 --run 或 --output')
    run = args.output.parent if args.output else args.run
    if args.output and args.output.name != SNAPSHOT:
        parser.error(f'快照文件名固定为 {SNAPSHOT}')
    fetch = (lambda: json.loads(args.response_file.read_text(encoding='utf-8'))) if args.response_file else fetch_open_endpoint
    result = refresh(run, fetch=fetch, haircut=args.haircut_pct, max_age_hours=args.max_age_hours,
                     force=args.force, manual_rate=args.rate, settlement=args.settlement_rate)
    if args.json:
        print(json.dumps({k: v for k, v in result.items()}, ensure_ascii=False, default=str))
    else:
        print(f'[{result["status"]}] {result["message"]}')
        print(f'快照文件：{result["path"]}')
    return EXIT_CODES.get(result['status'], 1)


if __name__ == '__main__':
    sys.exit(main())
