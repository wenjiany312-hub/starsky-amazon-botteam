"""Scoped stored-trend display only. No discovery, fetch, or backend actions.

UI projections alone are cached by filesystem change identity. Formal compliance
never consumes this cache: it retains its independent current operation boundary.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import time


def read_snapshot(series):
    # Unbound startup needs neither the asset parser nor its YAML dependency.
    from keyword_asset import read_snapshot as native
    return native(series)

_CACHE = OrderedDict()
_LIMIT = 8
MISSING = '季节词提醒未启用：尚未绑定本店铺商品的系列词库。'


def _rows(series, sku):
    path = Path(series).resolve() / '_词库资产/keyword_master.yaml'
    before = path.stat()
    signature = (before.st_mtime_ns, before.st_ctime_ns, before.st_size)
    key = str(path)
    cached = _CACHE.get(key)
    if cached and cached[0] == signature:
        _CACHE.move_to_end(key)
        return deepcopy(cached[1].get(sku, cached[1].get(None, [])))
    from yaml import YAMLError
    try:
        rows = read_snapshot(series).seasonal_projections()
    except YAMLError:
        _CACHE.pop(key, None)
        raise ValueError('词库 YAML 无法解析，请修复后重新打开') from None
    after = path.stat()
    if signature != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
        raise ValueError('词库在读取中变化，请重新打开')
    _CACHE[key] = (signature, rows)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _LIMIT:
        _CACHE.popitem(last=False)
    return deepcopy(rows.get(sku, rows.get(None, [])))


def _notices(path, consumer, reminders, today, missing=False):
    """Atomic small notice ledger, distinct from asset and monthly evaluation."""
    path = Path(path)
    if not path.parent.is_dir():
        return reminders
    lock = path.with_suffix('.lock')
    deadline = time.monotonic() + 2
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                return []
            time.sleep(.01)
    try:
        os.close(fd)
        try:
            ledger = json.loads(path.read_text(encoding='utf8'))
        except (OSError, ValueError):
            ledger = {}
        if not isinstance(ledger,dict) or not isinstance(ledger.get('consumers',{}),dict):
            ledger = {}
        old = json.dumps(ledger, sort_keys=True)
        seen = ledger.setdefault('consumers', {}).setdefault(consumer, {})
        # Month audit is informational. Never use it to skip evaluate_rows.
        ledger['last_audit_month'] = today[:7]
        notices = []
        for row in reminders:
            identity={key:row.get(key) for key in ('store_id','marketplace','sku','asin','series_root','term','rise_date','peak_end','action')}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if key not in seen:
                seen[key] = today
                notices.append(row)
        if missing:
            seen.setdefault('missing_binding', today)
        # Bound old notification bookkeeping; active display remains independent.
        if len(seen) > 2048:
            ledger['consumers'][consumer] = dict(sorted(seen.items(), key=lambda item:item[1])[-1024:])
        if json.dumps(ledger, sort_keys=True) != old:
            temp = path.with_suffix('.'+str(os.getpid())+'.tmp')
            temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf8')
            os.replace(temp, path)
        return notices
    finally:
        lock.unlink(missing_ok=True)


def collect(binding_path, *, consumer, store_id, marketplace, sku=None, asin=None, series_root=None, today=None):
    today = str(today or date.today())[:10]
    path = Path(binding_path)
    result = {'status':'unbound','reminders':[],'notices':[],'message':MISSING}
    try:
        document = json.loads(path.read_text(encoding='utf8'))
        if not isinstance(document,dict) or not isinstance(document.get('bindings',[]),list):
            raise ValueError('商品绑定须为 bindings 清单')
        bindings = document.get('bindings') or []
        candidates = [b for b in bindings if isinstance(b,dict) and b.get('enabled') is True
            and store_id and str(b.get('store_id')) == str(store_id)
            and str(b.get('marketplace','')).upper() == str(marketplace or '').upper()
            and (sku is None or b.get('sku') == sku) and (asin is None or b.get('asin') == asin)
            and (series_root is None or Path(b.get('series_root','')).resolve() == Path(series_root).resolve())]
        identities = [(b.get('store_id'),b.get('marketplace'),b.get('sku'),b.get('asin')) for b in candidates]
        if len(set(identities)) != len(identities):
            raise ValueError('商品绑定重复，需核对店铺与系列')
        if not candidates:
            return result
        from keyword_seasonal import evaluate_rows
        rows = []; gaps = []
        for binding in candidates:
            try:
                if not all(binding.get(k) for k in ('store_id','marketplace','sku','asin','series_root')):
                    raise ValueError('绑定缺少店铺 / 站点 / SKU / ASIN / 系列根')
                series = Path(binding['series_root']).expanduser()
                if not series.is_absolute() or not series.is_dir():
                    raise ValueError('绑定系列目录不可读')
                projected = _rows(series, binding['sku'])
                projected = [r for r in projected if r['marketplace'].upper() == str(marketplace).upper()
                             and (not r['skus'] or binding['sku'] in r['skus'])]
                for reminder in evaluate_rows(projected, today=today, lead_weeks=binding.get('lead_weeks',3)):
                    rows.append({**reminder, 'store_id':str(store_id), 'sku':binding['sku'], 'asin':binding['asin'],
                                 'series_root':str(series.resolve())})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                reason=str(exc) if isinstance(exc, ValueError) else '绑定词库或数据不可读'
                gaps.append(str(binding.get('sku') or '未标明 SKU')+'：'+reason)
        result.update(status='ready', reminders=rows, message='' if rows else '季节词：当前没有到期提醒。')
        if gaps:
            result.update(status='data_gap',data_gaps=gaps,message='季节词暂不可读：'+'；'.join(gaps))
        result['notices'] = _notices(path.with_name('seasonal_notices.json'), consumer, rows, today)
    except FileNotFoundError:
        result['message'] = MISSING if not path.exists() else '季节词暂不可读：绑定词库文件不存在。'
        result['status'] = 'unbound' if not path.exists() else 'data_gap'
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(status='data_gap', message='季节词暂不可读：'+str(exc))
    return result


def display_text(result):
    lines=[result['message']] if result.get('message') else []
    for row in result.get('reminders') or []:
        action={'prepare':'准备替换','active':'旺季使用前核对','remove':'峰值已过，撤下季节表达'}[row['action']]
        volume=str(row['search_volume'])+' / '+str(row['volume_unit']) if row.get('search_volume') is not None else '搜索量缺失'
        snippet='；'.join(str(v) for v in row.get('copy_fragments',{}).values()) if row.get('copy_allowed') else ''
        lines.append(f"{row.get('holiday') or '季节'} · {row['term']} · {volume} · {action}（起涨 {row['rise_date']} / 峰值结束 {row.get('peak_end') or '待补'}）；位置："+
            ' / '.join(row['suggested_positions'])+'；'+(snippet or '文案片段需按事实与合规核对')+
            '；季节广告组建议待审核，不自动修改。')
    return '\n'.join(lines)


def for_product(root, state, *, today=None):
    scope=(state or {}).get('seasonal_scope') or {}
    return collect(Path(root)/'seasonal_bindings.json', consumer='nav', store_id=scope.get('store_id'),
        marketplace=scope.get('marketplace'), sku=scope.get('sku'), asin=scope.get('asin'), series_root=root, today=today)
