"""Local human review ledger. No remote writes or execution capability."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3


class ReviewError(ValueError):
    pass


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _stamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None: raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError): raise ReviewError('invalid_timestamp') from None


def _hash(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-fA-F]{64}',value) is not None


def _window(value):
    try:
        start,end=date.fromisoformat(value['start']),date.fromisoformat(value['end'])
        days=value['attribution_days']
        if start>end or isinstance(days,bool) or not isinstance(days,int) or days<0: raise ValueError()
        return start,end,days
    except (KeyError,TypeError,ValueError): raise ReviewError('invalid_window') from None


def _validate(action):
    required=('source_context','native_id','layer','field','current','proposed','reason','evidence_hashes','rule_version','expires_at','observe_days')
    if not isinstance(action,dict) or any(k not in action for k in required): raise ReviewError('action_incomplete')
    context=action['source_context']
    if not isinstance(context,dict) or any(not isinstance(context.get(k),str) or not context[k].strip() for k in ('store','marketplace','profile','currency')):
        raise ReviewError('scope_incomplete')
    _window(context.get('window'))
    for k in ('native_id','layer','field','reason','rule_version'):
        if not isinstance(action[k],str) or not action[k].strip(): raise ReviewError('action_incomplete')
    if action['current'] is None or action['proposed'] is None: raise ReviewError('value_missing')
    hashes=action['evidence_hashes']
    if not isinstance(hashes,list) or not hashes or not all(_hash(h) for h in hashes): raise ReviewError('evidence_missing')
    if isinstance(action['observe_days'],bool) or not isinstance(action['observe_days'],int) or action['observe_days']<0: raise ReviewError('invalid_observation')
    if _stamp(action['expires_at'])<=datetime.now(timezone.utc): raise ReviewError('action_expired')
    try: _json(action)
    except (ValueError,TypeError): raise ReviewError('action_invalid') from None


class ReviewLedger:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, action_hash TEXT UNIQUE NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, reviewer TEXT, reviewed_at TEXT, receipt TEXT)')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally: db.close()

    @staticmethod
    def _record(row):
        if row is None: raise ReviewError('action_not_found')
        record=dict(row)
        record['action']=json.loads(record.pop('content'))
        if hashlib.sha256(_json(record['action']).encode()).hexdigest()!=record['action_hash']: raise ReviewError('ledger_integrity_failed')
        record['receipt']=json.loads(record['receipt']) if record['receipt'] else None
        return record

    def propose(self,action):
        _validate(action)
        content=_json(action);digest=hashlib.sha256(content.encode()).hexdigest()
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO actions(action_id,action_hash,content,status,created_at) VALUES(?,?,?,?,?)',
                       (digest,digest,content,'pending_review',datetime.now(timezone.utc).isoformat()))
            return self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(digest,)).fetchone())

    def list_actions(self):
        with self._db() as db:
            return [self._record(r) for r in db.execute('SELECT * FROM actions ORDER BY created_at,action_id')]

    def decide(self,action_id,expected_hash,decision,reviewer='local_user'):
        if decision not in ('approve','reject') or not isinstance(reviewer,str) or not reviewer.strip(): raise ReviewError('invalid_decision')
        with self._db() as db:
            r=self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(action_id,)).fetchone())
            if r['action_hash']!=expected_hash: raise ReviewError('action_hash_mismatch')
            if r['status']!='pending_review': raise ReviewError('invalid_transition')
            if _stamp(r['action']['expires_at'])<=datetime.now(timezone.utc): raise ReviewError('action_expired')
            db.execute('UPDATE actions SET status=?, reviewer=?, reviewed_at=? WHERE action_id=?',
                       ('approved_not_executed' if decision=='approve' else 'rejected',reviewer,datetime.now(timezone.utc).isoformat(),action_id))
            return self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(action_id,)).fetchone())

    def record_result(self,action_id,expected_hash,execution_time,receipt_ref,sourcehash):
        stamp=_stamp(execution_time)
        if not isinstance(receipt_ref,str) or not receipt_ref.strip() or not _hash(sourcehash): raise ReviewError('receipt_required')
        with self._db() as db:
            r=self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(action_id,)).fetchone())
            if r['action_hash']!=expected_hash: raise ReviewError('action_hash_mismatch')
            if r['status']!='approved_not_executed': raise ReviewError('invalid_transition')
            if not _stamp(r['reviewed_at'])<=stamp<=datetime.now(timezone.utc) or stamp>_stamp(r['action']['expires_at']): raise ReviewError('invalid_execution_time')
            receipt={'execution_time':stamp.isoformat(),'receipt_ref':receipt_ref,'sourcehash':sourcehash,'verification':'user_reported_not_independently_verified'}
            db.execute('UPDATE actions SET status=?,receipt=? WHERE action_id=?',('executed_reported',_json(receipt),action_id))
            return self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(action_id,)).fetchone())

    def compare(self,action_id,before,after):
        with self._db() as db: r=self._record(db.execute('SELECT * FROM actions WHERE action_id=?',(action_id,)).fetchone())
        if r['status']!='executed_reported' or not r['receipt']: return {'state':'not_executed','deltas':None}
        try:
            action=r['action'];context=action['source_context']
            for snapshot in (before,after):
                if any(snapshot.get(k)!=action[k] for k in ('native_id','layer')): raise ReviewError('comparison_scope_mismatch')
                if any(snapshot['source_context'].get(k)!=context[k] for k in ('store','marketplace','profile','currency')): raise ReviewError('comparison_scope_mismatch')
                if not _hash(snapshot.get('evidence_hash')): raise ReviewError('comparison_evidence_missing')
            bs,be,ba=_window(before['source_context']['window']);as_,ae,aa=_window(after['source_context']['window'])
            if ba!=aa or ba!=context['window']['attribution_days'] or (be-bs)!=(ae-as_) or be>=as_: raise ReviewError('comparison_window_mismatch')
            executed=_stamp(r['receipt']['execution_time'])
            if be>=executed.date() or as_<=executed.date(): raise ReviewError('comparison_execution_boundary')
            # Context dates are marketplace-local. Use an extra UTC day when no timezone contract exists.
            mature=max(executed+timedelta(days=action['observe_days']),datetime.combine(ae+timedelta(days=aa+2),datetime.min.time(),timezone.utc))
            if datetime.now(timezone.utc)<mature: return {'state':'observing','deltas':None}
            if any(s.get('complete') is not True for s in (before,after)): return {'state':'data_gap','deltas':None}
            baseline_mature=datetime.combine(be+timedelta(days=ba+2),datetime.min.time(),timezone.utc)
            if _stamp(before.get('collected_at'))<baseline_mature or _stamp(after.get('collected_at'))<mature:
                return {'state':'awaiting_mature_snapshot','deltas':None}
            metrics={}
            for key in set(before['metrics']) & set(after['metrics']):
                left,right=before['metrics'][key],after['metrics'][key]
                valid=all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (left,right))
                metrics[key]=right-left if valid else None
            return {'state':'descriptive_comparison','deltas':metrics,'causal_claim':False,'note':'Observed difference only; does not attribute change to this action.', 'evidence_hashes':[before['evidence_hash'],after['evidence_hash']]}
        except (KeyError,TypeError): raise ReviewError('comparison_incomplete') from None
